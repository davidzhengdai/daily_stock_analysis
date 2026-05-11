# -*- coding: utf-8 -*-
"""
Model Benchmark Service — multi-model comparison for stock prediction accuracy.

=== Purpose ===
Compares different LLM models by running the same stock analysis through each
model, then backtesting their predictions against actual future price movements.

=== Workflow ===
  1. DISCOVER : List all available models from current config
  2. ANALYZE  : Run analysis for each (stock * model) combination
  3. EVALUATE : After eval_window_days, backtest each prediction
  4. REPORT   : Generate per-model metrics and leaderboard ranking
  5. DEBUG    : Live side-by-side comparison of model outputs (--debug)

=== Modes ===
  --sequential (default): Run models one at a time (rate-limit friendly)
  --parallel             : Run models concurrently via ThreadPoolExecutor
  --debug                : Show live side-by-side comparison during analysis

=== Scoring Formula ===
  composite_score = (
    direction_accuracy_norm  * 0.35 +   # Did the model call the right direction?
    win_rate_norm            * 0.30 +   # Win / (Win + Loss) ratio
    excess_return_norm       * 0.20 +   # Avg return vs baseline
    consistency_norm         * 0.10 +   # 1 - (stddev / mean) of returns
    conviction_bonus         * 0.05     # Bonus for correct decisive calls
  )

  cost_efficiency = accuracy_score / log10(1 + total_cost_usd)
    (Higher = more accurate per dollar spent)

=== Usage ===
  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA
  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA --parallel --debug
  python -m src.services.model_benchmark --evaluate --days 5
  python -m src.services.model_benchmark --report
  python -m src.services.model_benchmark --full --stocks AAPL,MSFT,TSLA  # all-in-one
"""

from __future__ import annotations

import json
import logging
import math
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model pricing (USD per 1M tokens, input / output)
# ---------------------------------------------------------------------------

_MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    # Anthropic
    "claude-sonnet-4-6":      (3.00,  15.00),
    "claude-sonnet-4-5":      (3.00,  15.00),
    "claude-opus-4-5":        (15.00, 75.00),
    "claude-haiku-4-5":       (0.80,  4.00),
    # OpenAI
    "gpt-5.5":                (2.50,  10.00),
    "gpt-4.1":                (2.00,  8.00),
    "gpt-4o-mini":            (0.15,  0.60),
    "o4-mini":                (1.10,  4.40),
    # Google
    "gemini-3.1-pro-preview": (1.25,  5.00),
    "gemini-3-flash-preview": (0.15,  0.60),
    # DeepSeek
    "deepseek-chat":          (0.27,  1.10),
    "deepseek-reasoner":      (0.55,  2.19),
    # Moonshot / Kimi
    "kimi-k2.6":              (0.60,  2.40),
    # Doubao (via Anspire)
    "Doubao-Seed-2.0-lite":   (0.50,  2.00),
    # Ollama (local, free)
    "qwen3.5":                (0.0,   0.0),
    "qwen3":                  (0.0,   0.0),
    "llama3.2":               (0.0,   0.0),
    "llama3.1":               (0.0,   0.0),
}


def estimate_cost(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a model invocation based on known pricing."""
    # Extract model name without protocol prefix
    model_name = model_id.split("/", 1)[-1] if "/" in model_id else model_id
    model_lower = model_name.lower()

    # Exact match
    if model_lower in _MODEL_PRICING:
        input_price, output_price = _MODEL_PRICING[model_lower]
    else:
        # Fuzzy match: check if any known model is a substring
        matched = False
        for known, prices in _MODEL_PRICING.items():
            if known in model_lower:
                input_price, output_price = prices
                matched = True
                break
        if not matched:
            return 0.0  # unknown model, assume free

    cost = (prompt_tokens / 1_000_000) * input_price + (completion_tokens / 1_000_000) * output_price
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Lightweight descriptor for a configured LLM model."""
    model_id: str
    protocol: str
    channel: str
    base_url: str = ""
    display_name: str = ""

    @property
    def short_name(self) -> str:
        if self.display_name:
            return self.display_name
        parts = self.model_id.split("/", 1)
        return parts[1] if len(parts) > 1 else parts[0]


@dataclass
class BenchmarkRunMeta:
    """Per-run performance metadata captured during analysis."""
    model_id: str
    stock_code: str
    latency_ms: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    decision_type: str = ""
    confidence: str = ""
    operation_advice: str = ""
    analysis_summary: str = ""
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    success: bool = True
    error: str = ""


@dataclass
class ModelBenchmarkResult:
    """Per-model aggregated metrics after backtesting."""
    model_id: str
    model_display: str
    total_analyses: int = 0
    completed_evals: int = 0
    insufficient_evals: int = 0
    win_count: int = 0
    loss_count: int = 0
    neutral_count: int = 0
    direction_correct_count: int = 0
    direction_total: int = 0
    direction_accuracy_pct: Optional[float] = None
    win_rate_pct: Optional[float] = None
    avg_stock_return_pct: Optional[float] = None
    avg_simulated_return_pct: Optional[float] = None
    return_stddev: Optional[float] = None
    consistency_score: Optional[float] = None
    composite_score: float = 0.0
    # Performance metrics (from BenchmarkRunMeta)
    avg_latency_ms: Optional[float] = None
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    cost_efficiency: Optional[float] = None
    rank: int = 0
    stock_details: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class BenchmarkReport:
    """Top-level benchmark report."""
    generated_at: str = ""
    eval_window_days: int = 5
    stocks_analyzed: List[str] = field(default_factory=list)
    date_range: str = ""
    models: List[ModelBenchmarkResult] = field(default_factory=list)

    @property
    def leaderboard(self) -> List[ModelBenchmarkResult]:
        return sorted(self.models, key=lambda m: m.composite_score, reverse=True)

    @property
    def performance_leaderboard(self) -> List[ModelBenchmarkResult]:
        return sorted(self.models, key=lambda m: m.avg_latency_ms or 0)


# ---------------------------------------------------------------------------
# Model discovery (unchanged)
# ---------------------------------------------------------------------------

def discover_models(config=None) -> List[ModelInfo]:
    if config is None:
        from src.config import get_config
        config = get_config()

    from src.config import get_configured_llm_models

    llm_model_list = getattr(config, "llm_model_list", []) or []
    if not llm_model_list:
        return []

    configured_ids = get_configured_llm_models(llm_model_list)
    if not configured_ids:
        return []

    llm_channels_raw = os.getenv("LLM_CHANNELS", "").strip()
    channel_meta: Dict[str, Dict[str, str]] = {}
    if llm_channels_raw:
        for ch_name in llm_channels_raw.split(","):
            ch_name = ch_name.strip()
            if not ch_name:
                continue
            ch_upper = ch_name.upper()
            enabled = _parse_env_bool(os.getenv(f"LLM_{ch_upper}_ENABLED"), default=True)
            if not enabled:
                continue
            protocol_raw = os.getenv(f"LLM_{ch_upper}_PROTOCOL", "").strip()
            if not protocol_raw:
                protocol_raw = _infer_protocol(ch_name)
            protocol = _canonicalize_protocol(protocol_raw)
            base_url = os.getenv(f"LLM_{ch_upper}_BASE_URL", "").strip()
            models_raw = os.getenv(f"LLM_{ch_upper}_MODELS", "").strip()
            for model_name in models_raw.split(","):
                model_name = model_name.strip()
                if not model_name:
                    continue
                full_id = f"{protocol}/{model_name}" if "/" not in model_name else model_name
                channel_meta[full_id] = {
                    "protocol": protocol,
                    "channel": ch_name.lower(),
                    "base_url": base_url,
                }

    models: List[ModelInfo] = []
    for model_id in configured_ids:
        meta = channel_meta.get(model_id, {})
        protocol = meta.get("protocol") or _infer_protocol(
            model_id.split("/")[0] if "/" in model_id else model_id
        )
        channel = meta.get("channel", "legacy")
        base_url = meta.get("base_url", "")
        models.append(ModelInfo(
            model_id=model_id,
            protocol=protocol,
            channel=channel,
            base_url=base_url,
            display_name=model_id,
        ))

    logger.info("Discovered %d unique model(s): %s", len(models),
                 [m.model_id for m in models])
    return models


def _parse_env_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _infer_protocol(channel_name: str) -> str:
    mapping = {
        "openai": "openai", "anspire": "openai", "aihubmix": "openai",
        "anthropic": "anthropic", "claude": "anthropic",
        "gemini": "gemini", "google": "gemini",
        "deepseek": "deepseek",
        "ollama": "ollama",
        "vertex_ai": "vertex_ai",
    }
    return mapping.get(channel_name.lower(), "openai")


def _canonicalize_protocol(raw: str) -> str:
    proto = raw.lower().strip()
    valid = {"openai", "anthropic", "gemini", "deepseek", "ollama", "vertex_ai"}
    return proto if proto in valid else "openai"


# ---------------------------------------------------------------------------
# Model override context manager (unchanged)
# ---------------------------------------------------------------------------

class _ModelOverride:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._original = os.environ.get("LITELLM_MODEL", "")
        self._original_agent = os.environ.get("AGENT_LITELLM_MODEL", "")

    def __enter__(self):
        os.environ["LITELLM_MODEL"] = self.model_id
        os.environ["AGENT_LITELLM_MODEL"] = self.model_id
        from src.config import setup_env
        setup_env(override=True)
        return self

    def __exit__(self, *args):
        if self._original:
            os.environ["LITELLM_MODEL"] = self._original
        else:
            os.environ.pop("LITELLM_MODEL", None)
        if self._original_agent:
            os.environ["AGENT_LITELLM_MODEL"] = self._original_agent
        else:
            os.environ.pop("AGENT_LITELLM_MODEL", None)
        from src.config import setup_env
        setup_env(override=True)


# ---------------------------------------------------------------------------
# Benchmark Service
# ---------------------------------------------------------------------------

class ModelBenchmarkService:
    """Orchestrates multi-model benchmarking for stock analysis."""

    DEFAULT_EVAL_WINDOW_DAYS = 5
    MAX_STOCKS = 20
    DEFAULT_MAX_PARALLEL = 3  # conservative default to avoid rate limits

    def __init__(self):
        from src.config import get_config
        self._config = get_config()
        self._run_meta: List[BenchmarkRunMeta] = []
        self._meta_lock = threading.Lock()

    # -- Discovery ----------------------------------------------------------

    def list_models(self) -> List[ModelInfo]:
        return discover_models(self._config)

    # -- Analysis phase -----------------------------------------------------

    def run_analysis(
        self,
        stock_codes: List[str],
        models: Optional[List[str]] = None,
        *,
        parallel: bool = False,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        debug: bool = False,
    ) -> Tuple[Dict[str, Any], List[BenchmarkRunMeta]]:
        """Run analysis for each (stock * model) combination.

        Args:
            stock_codes: List of stock codes (e.g. ["AAPL", "NVDA"]).
            models: Specific model_ids to test. None = all discovered models.
            parallel: If True, run models concurrently via ThreadPoolExecutor.
            max_parallel: Max concurrent model invocations (default 3).
            debug: If True, capture full performance metadata for side-by-side comparison.

        Returns:
            Tuple of (results dict, list of BenchmarkRunMeta for debug/performance).
        """
        if len(stock_codes) > self.MAX_STOCKS:
            raise ValueError(
                f"Maximum {self.MAX_STOCKS} stocks allowed per benchmark run "
                f"(got {len(stock_codes)})."
            )

        available = self.list_models()
        if not available:
            raise RuntimeError(
                "No models discovered. Configure at least one LLM provider via "
                "LLM_CHANNELS or legacy API key env vars."
            )

        if models:
            model_ids_lower = {m.lower() for m in models}
            selected = [m for m in available if m.model_id.lower() in model_ids_lower]
            if not selected:
                raise ValueError(
                    f"No matching models found for: {models}. "
                    f"Available: {[m.model_id for m in available]}"
                )
        else:
            selected = available

        total_runs = len(stock_codes) * len(selected)
        logger.info(
            "Starting benchmark analysis: %d stock(s) x %d model(s) = %d runs "
            "(mode=%s, debug=%s)",
            len(stock_codes), len(selected), total_runs,
            "parallel" if parallel else "sequential",
            "on" if debug else "off",
        )

        results: Dict[str, Any] = {
            "benchmark_run_id": _generate_run_id(),
            "timestamp": datetime.now().isoformat(),
            "stocks": stock_codes,
            "models_tested": [m.model_id for m in selected],
            "runs": {},
            "errors": [],
        }
        self._run_meta = []

        # Build flat work list: (model_info, stock_code)
        work_items: List[Tuple[ModelInfo, str]] = []
        for model_info in selected:
            for code in stock_codes:
                work_items.append((model_info, code))

        if parallel and len(selected) > 1:
            self._run_parallel(work_items, results, max_parallel, debug)
        else:
            self._run_sequential(work_items, results, debug)

        total_ok = sum(
            1 for mr in results["runs"].values()
            for sr in mr.values()
            if sr.get("status") == "ok"
        )
        logger.info(
            "Benchmark analysis complete: %d/%d successful", total_ok, total_runs
        )
        return results, self._run_meta

    def _run_sequential(
        self,
        work_items: List[Tuple[ModelInfo, str]],
        results: Dict[str, Any],
        debug: bool,
    ) -> None:
        """Run work items sequentially, one model at a time."""
        for model_info, code in work_items:
            if model_info.model_id not in results["runs"]:
                results["runs"][model_info.model_id] = {}

            try:
                meta = self._run_single_analysis(
                    stock_code=code,
                    model_id=model_info.model_id,
                    capture_meta=debug,
                )
                results["runs"][model_info.model_id][code] = {
                    "analysis_id": meta.analysis_id if hasattr(meta, 'analysis_id') else -1,
                    "status": "ok",
                }
                if debug:
                    self._run_meta.append(meta)
                logger.info("  [%s][%s] -> ok", model_info.model_id, code)
            except Exception as exc:
                logger.error("  [%s][%s] FAILED: %s", model_info.model_id, code, exc)
                results["runs"][model_info.model_id][code] = {
                    "status": "error", "error": str(exc),
                }
                results["errors"].append({
                    "model": model_info.model_id, "stock": code, "error": str(exc),
                })
                if debug:
                    self._run_meta.append(BenchmarkRunMeta(
                        model_id=model_info.model_id,
                        stock_code=code,
                        success=False,
                        error=str(exc),
                    ))

    def _run_parallel(
        self,
        work_items: List[Tuple[ModelInfo, str]],
        results: Dict[str, Any],
        max_parallel: int,
        debug: bool,
    ) -> None:
        """Run work items concurrently using ThreadPoolExecutor.

        Each work item runs in its own thread with a temporary model override.
        The _ModelOverride context manager mutates process-level env vars, so
        we serialize model switches with a lock — only one model's analysis
        runs at a time, but multiple stocks within the same model can be
        concurrent.  This is a practical compromise: full per-model isolation
        would require subprocesses.
        """
        # Group by model so stocks under the same model can run in parallel
        model_groups: Dict[str, List[str]] = {}
        for model_info, code in work_items:
            model_groups.setdefault(model_info.model_id, []).append(code)

        # Initialize results structure
        for model_id, codes in model_groups.items():
            results["runs"].setdefault(model_id, {})
            for code in codes:
                results["runs"][model_id].setdefault(code, {})

        # Process model by model (sequentially across models, parallel within)
        for model_id, codes in model_groups.items():
            logger.info("--- Model: %s (%d stocks, parallel) ---", model_id, len(codes))

            def _run_one_stock(code: str) -> Optional[BenchmarkRunMeta]:
                try:
                    return self._run_single_analysis(
                        stock_code=code,
                        model_id=model_id,
                        capture_meta=debug,
                    )
                except Exception as exc:
                    logger.error("  [%s][%s] FAILED: %s", model_id, code, exc)
                    results["errors"].append({
                        "model": model_id, "stock": code, "error": str(exc),
                    })
                    if debug:
                        return BenchmarkRunMeta(
                            model_id=model_id, stock_code=code,
                            success=False, error=str(exc),
                        )
                    return None

            with _ModelOverride(model_id):
                with ThreadPoolExecutor(max_workers=min(max_parallel, len(codes))) as executor:
                    future_map = {
                        executor.submit(_run_one_stock, code): code
                        for code in codes
                    }
                    for future in as_completed(future_map):
                        code = future_map[future]
                        try:
                            meta = future.result()
                            if meta and meta.success:
                                results["runs"][model_id][code] = {
                                    "status": "ok",
                                    "latency_ms": meta.latency_ms,
                                }
                                if debug:
                                    self._run_meta.append(meta)
                                logger.info("  [%s][%s] -> ok (%.0fms)", model_id, code, meta.latency_ms)
                            elif meta:
                                results["runs"][model_id][code] = {
                                    "status": "error", "error": meta.error,
                                }
                        except Exception as exc:
                            results["runs"][model_id][code] = {
                                "status": "error", "error": str(exc),
                            }

    def _run_single_analysis(
        self,
        stock_code: str,
        model_id: str,
        capture_meta: bool = False,
    ) -> BenchmarkRunMeta:
        """Run a single stock analysis. Returns BenchmarkRunMeta with performance data."""
        from src.config import get_config
        from src.agent.factory import build_agent_executor
        from src.storage import DatabaseManager

        config = get_config()
        db = DatabaseManager.get_instance()

        t0 = time.perf_counter()
        executor = build_agent_executor(config)

        task = f"分析 {stock_code}"
        result = executor.run(task, context={
            "stock_code": stock_code,
            "report_language": "en",
        })

        latency_ms = (time.perf_counter() - t0) * 1000

        if not result.success:
            raise RuntimeError(f"Analysis failed: {result.error}")

        # Extract token usage from result
        total_tokens = getattr(result, "total_tokens", 0) or 0
        # Try to get prompt/completion breakdown from stats
        stats = getattr(result, "stats", None)
        prompt_tokens = 0
        completion_tokens = 0
        if stats:
            prompt_tokens = getattr(stats, "total_prompt_tokens", 0) or 0
            completion_tokens = getattr(stats, "total_completion_tokens", 0) or 0
        if not prompt_tokens and not completion_tokens and total_tokens:
            # Rough split: 60% prompt, 40% completion for long-context analysis
            prompt_tokens = int(total_tokens * 0.6)
            completion_tokens = total_tokens - prompt_tokens

        est_cost = estimate_cost(model_id, prompt_tokens, completion_tokens)

        dashboard = result.dashboard or {}
        operation_advice = dashboard.get("operation_advice", "")
        sentiment_score = None
        try:
            sentiment_score = int(dashboard.get("sentiment_score", 0))
        except (TypeError, ValueError):
            pass

        trend_prediction = dashboard.get("trend_prediction", "")
        analysis_summary = dashboard.get("analysis_summary", "")
        decision_type = dashboard.get("decision_type", "")
        confidence = dashboard.get("confidence_level", "")

        battle_plan = (dashboard.get("dashboard") or {}).get("battle_plan") or {}
        sniper = battle_plan.get("sniper_points", {})
        ideal_buy = _parse_float(sniper.get("ideal_buy"))
        secondary_buy = _parse_float(sniper.get("secondary_buy"))
        stop_loss = _parse_float(sniper.get("stop_loss"))
        take_profit = _parse_float(sniper.get("take_profit"))

        context_snapshot = {
            "model_id": model_id,
            "benchmark": True,
            "benchmark_meta": {
                "latency_ms": round(latency_ms, 1),
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": est_cost,
            },
            "decision_type": decision_type,
            "confidence_level": confidence,
            "key_points": dashboard.get("key_points", [])[:5],
        }

        try:
            analysis_id = db.save_analysis_history(
                code=stock_code,
                name=dashboard.get("stock_name", stock_code),
                report_type="benchmark",
                sentiment_score=sentiment_score,
                operation_advice=operation_advice,
                trend_prediction=trend_prediction,
                analysis_summary=analysis_summary[:500] if analysis_summary else "",
                raw_result=json.dumps(dashboard, ensure_ascii=False),
                context_snapshot=context_snapshot,
                ideal_buy=ideal_buy,
                secondary_buy=secondary_buy,
                stop_loss=stop_loss,
                take_profit=take_profit,
                save_snapshot=True,
            )
        except TypeError:
            analysis_id = db.save_analysis_history(
                code=stock_code,
                name=dashboard.get("stock_name", stock_code),
                report_type="benchmark",
                sentiment_score=sentiment_score,
                operation_advice=operation_advice,
                trend_prediction=trend_prediction,
                analysis_summary=analysis_summary[:500] if analysis_summary else "",
                raw_result=json.dumps(dashboard, ensure_ascii=False),
                context_snapshot=context_snapshot,
                ideal_buy=ideal_buy,
                secondary_buy=secondary_buy,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        return BenchmarkRunMeta(
            model_id=model_id,
            stock_code=stock_code,
            latency_ms=round(latency_ms, 1),
            total_tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=est_cost,
            decision_type=decision_type,
            confidence=confidence,
            operation_advice=operation_advice,
            analysis_summary=analysis_summary[:200] if analysis_summary else "",
            stop_loss=stop_loss,
            take_profit=take_profit,
            success=True,
        )

    # -- Debug: side-by-side comparison -------------------------------------

    def format_debug_comparison(
        self,
        stock_codes: List[str],
        run_meta: List[BenchmarkRunMeta],
    ) -> str:
        """Build a live side-by-side comparison table from run metadata."""
        if not run_meta:
            return "No debug data available."

        lines = []
        lines.append("")
        lines.append("=" * 110)
        lines.append("  LIVE DEBUG — Multi-Model Side-by-Side Comparison")
        lines.append("=" * 110)

        for code in stock_codes:
            code_meta = [m for m in run_meta if m.stock_code == code]
            if not code_meta:
                continue

            lines.append(f"\n  Stock: {code}")
            lines.append(f"  {'Model':<38} {'Signal':<8} {'Conf':<6} {'Latency':<10} {'Tokens':<8} {'Cost':<10} {'StopLoss':<10} {'TakeProfit':<10}")
            lines.append(f"  {'-'*36} {'-'*6} {'-'*4} {'-'*8} {'-'*6} {'-'*8} {'-'*8} {'-'*8}")

            # Sort by latency for performance comparison
            sorted_meta = sorted(code_meta, key=lambda m: m.latency_ms)

            for meta in sorted_meta:
                display = meta.model_id.split("/")[-1] if "/" in meta.model_id else meta.model_id
                signal = meta.decision_type[:7] if meta.decision_type else "N/A"
                conf = meta.confidence[:5] if meta.confidence else "N/A"
                lat = f"{meta.latency_ms:.0f}ms" if meta.success else "FAILED"
                tok = _fmt_count(meta.total_tokens)
                cost = f"${meta.estimated_cost_usd:.4f}" if meta.estimated_cost_usd > 0 else "free"
                sl = f"{meta.stop_loss:.2f}" if meta.stop_loss else "N/A"
                tp = f"{meta.take_profit:.2f}" if meta.take_profit else "N/A"

                lines.append(
                    f"  {display:<38} {signal:<8} {conf:<6} {lat:<10} {tok:<8} {cost:<10} {sl:<10} {tp:<10}"
                )

            # Show divergence analysis
            decisions = [m.decision_type for m in code_meta if m.success]
            unique_decisions = list(dict.fromkeys(decisions))
            if len(unique_decisions) > 1:
                lines.append(f"  >>> DIVERGENCE: models disagree on {code} — decisions: {unique_decisions}")
            else:
                lines.append(f"  >>> CONSENSUS: all models agree on {code} — {unique_decisions[0] if unique_decisions else 'N/A'}")

            # Summary line for this stock
            success_meta = [m for m in code_meta if m.success]
            if success_meta:
                fastest = min(success_meta, key=lambda m: m.latency_ms)
                cheapest = min(success_meta, key=lambda m: m.estimated_cost_usd if m.estimated_cost_usd > 0 else float('inf'))
                lines.append(f"  >>> Fastest: {fastest.model_id.split('/')[-1]} ({fastest.latency_ms:.0f}ms) | "
                             f"Cheapest: {cheapest.model_id.split('/')[-1]} (${cheapest.estimated_cost_usd:.4f})")

            lines.append("")

        # Cross-stock performance summary
        lines.append("  --- Performance Summary (All Stocks) ---")
        lines.append(f"  {'Model':<38} {'AvgLat':<10} {'TotalTok':<10} {'TotalCost':<12}")
        lines.append(f"  {'-'*36} {'-'*8} {'-'*8} {'-'*10}")

        model_perf: Dict[str, Dict[str, Any]] = {}
        for meta in run_meta:
            if not meta.success:
                continue
            entry = model_perf.setdefault(meta.model_id, {
                "latencies": [], "total_tokens": 0, "total_cost": 0.0,
            })
            entry["latencies"].append(meta.latency_ms)
            entry["total_tokens"] += meta.total_tokens
            entry["total_cost"] += meta.estimated_cost_usd

        for model_id, perf in sorted(model_perf.items(), key=lambda x: statistics.mean(x[1]["latencies"])):
            display = model_id.split("/")[-1] if "/" in model_id else model_id
            avg_lat = f"{statistics.mean(perf['latencies']):.0f}ms"
            lines.append(
                f"  {display:<38} {avg_lat:<10} {_fmt_count(perf['total_tokens']):<10} ${perf['total_cost']:.4f}"
            )

        lines.append("=" * 110)
        return "\n".join(lines)

    # -- Evaluation phase ---------------------------------------------------

    def evaluate(
        self,
        eval_window_days: int = DEFAULT_EVAL_WINDOW_DAYS,
        models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from src.services.backtest_service import BacktestService

        svc = BacktestService()
        result = svc.run_backtest(
            code=None,
            force=False,
            eval_window_days=eval_window_days,
            limit=2000,
        )

        logger.info(
            "Backtest evaluation: processed=%d saved=%d completed=%d insufficient=%d errors=%d",
            result.get("processed", 0),
            result.get("saved", 0),
            result.get("completed", 0),
            result.get("insufficient", 0),
            result.get("errors", 0),
        )
        return result

    # -- Reporting phase ----------------------------------------------------

    def generate_report(
        self,
        eval_window_days: int = DEFAULT_EVAL_WINDOW_DAYS,
        models: Optional[List[str]] = None,
    ) -> BenchmarkReport:
        from src.storage import DatabaseManager, AnalysisHistory, BacktestResult
        from sqlalchemy import and_, select

        db = DatabaseManager.get_instance()
        report = BenchmarkReport(
            generated_at=datetime.now().isoformat(),
            eval_window_days=eval_window_days,
        )

        with db.get_session() as session:
            rows = session.execute(
                select(
                    BacktestResult,
                    AnalysisHistory.context_snapshot,
                    AnalysisHistory.code,
                )
                .join(AnalysisHistory,
                      BacktestResult.analysis_history_id == AnalysisHistory.id)
                .where(
                    and_(
                        BacktestResult.eval_window_days == eval_window_days,
                        BacktestResult.eval_status == "completed",
                    )
                )
                .order_by(BacktestResult.analysis_date.desc())
            ).all()

        if not rows:
            logger.warning("No completed backtest results found for window=%d days", eval_window_days)
            return report

        model_results: Dict[str, List[Dict[str, Any]]] = {}
        model_perf_meta: Dict[str, List[Dict[str, Any]]] = {}
        stock_codes: set[str] = set()

        for bt_row, snapshot_json, code in rows:
            model_id, bench_meta = self._extract_model_info(snapshot_json)
            if not model_id:
                continue
            if models and model_id not in models:
                continue

            stock_codes.add(code)

            record = {
                "code": code,
                "analysis_date": bt_row.analysis_date.isoformat() if bt_row.analysis_date else None,
                "operation_advice": bt_row.operation_advice,
                "position_recommendation": bt_row.position_recommendation,
                "direction_expected": bt_row.direction_expected,
                "direction_correct": bt_row.direction_correct,
                "outcome": bt_row.outcome,
                "start_price": bt_row.start_price,
                "end_close": bt_row.end_close,
                "stock_return_pct": bt_row.stock_return_pct,
                "simulated_return_pct": bt_row.simulated_return_pct,
                "hit_stop_loss": bt_row.hit_stop_loss,
                "hit_take_profit": bt_row.hit_take_profit,
            }
            model_results.setdefault(model_id, []).append(record)
            if bench_meta:
                model_perf_meta.setdefault(model_id, []).append(bench_meta)

        report.stocks_analyzed = sorted(stock_codes)

        for model_id, records in model_results.items():
            perf_records = model_perf_meta.get(model_id, [])
            model_report = self._compute_model_metrics(model_id, records, perf_records)
            report.models.append(model_report)

        ranked = sorted(report.models, key=lambda m: m.composite_score, reverse=True)
        for i, m in enumerate(ranked, start=1):
            m.rank = i
        report.models = ranked

        all_dates = []
        for records in model_results.values():
            for r in records:
                if r["analysis_date"]:
                    all_dates.append(r["analysis_date"])
        if all_dates:
            all_dates.sort()
            report.date_range = f"{all_dates[0]} ~ {all_dates[-1]}"

        logger.info(
            "Benchmark report: %d models, %d stocks, %d total evaluations",
            len(report.models), len(report.stocks_analyzed),
            sum(m.total_analyses for m in report.models),
        )
        return report

    def _compute_model_metrics(
        self,
        model_id: str,
        records: List[Dict[str, Any]],
        perf_records: List[Dict[str, Any]],
    ) -> ModelBenchmarkResult:
        result = ModelBenchmarkResult(
            model_id=model_id,
            model_display=model_id,
            total_analyses=len(records),
            completed_evals=len(records),
        )

        completed = [r for r in records if r["outcome"] is not None]

        result.win_count = sum(1 for r in completed if r["outcome"] == "win")
        result.loss_count = sum(1 for r in completed if r["outcome"] == "loss")
        result.neutral_count = sum(1 for r in completed if r["outcome"] == "neutral")

        dir_records = [r for r in completed if r["direction_correct"] is not None]
        result.direction_total = len(dir_records)
        result.direction_correct_count = sum(1 for r in dir_records if r["direction_correct"] is True)
        if result.direction_total > 0:
            result.direction_accuracy_pct = round(
                result.direction_correct_count / result.direction_total * 100, 2
            )

        win_loss_total = result.win_count + result.loss_count
        if win_loss_total > 0:
            result.win_rate_pct = round(result.win_count / win_loss_total * 100, 2)

        returns = [r["stock_return_pct"] for r in completed if r["stock_return_pct"] is not None]
        sim_returns = [r["simulated_return_pct"] for r in completed if r["simulated_return_pct"] is not None]

        if returns:
            result.avg_stock_return_pct = round(statistics.mean(returns), 2)
            if len(returns) >= 2:
                result.return_stddev = round(statistics.stdev(returns), 2)
        if sim_returns:
            result.avg_simulated_return_pct = round(statistics.mean(sim_returns), 2)

        # Performance metrics from benchmark_meta
        if perf_records:
            latencies = [p.get("latency_ms", 0) for p in perf_records if p.get("latency_ms")]
            if latencies:
                result.avg_latency_ms = round(statistics.mean(latencies), 1)
            result.total_tokens_used = sum(p.get("total_tokens", 0) for p in perf_records)
            result.total_cost_usd = round(sum(p.get("estimated_cost_usd", 0) for p in perf_records), 6)
            # Cost efficiency: accuracy per log-dollar (higher = better value)
            if result.total_cost_usd > 0 and result.composite_score > 0:
                result.cost_efficiency = round(
                    result.composite_score / math.log10(1 + result.total_cost_usd * 100), 2
                )

        # Stock details
        stock_groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in completed:
            stock_groups.setdefault(r["code"], []).append(r)
        for code, stock_records in stock_groups.items():
            stock_wins = sum(1 for r in stock_records if r["outcome"] == "win")
            stock_losses = sum(1 for r in stock_records if r["outcome"] == "loss")
            stock_rets = [r["stock_return_pct"] for r in stock_records if r["stock_return_pct"] is not None]
            result.stock_details[code] = {
                "total": len(stock_records),
                "wins": stock_wins, "losses": stock_losses,
                "avg_return_pct": round(statistics.mean(stock_rets), 2) if stock_rets else None,
            }

        result.consistency_score = self._calc_consistency(returns)
        result.composite_score = round(self._calc_composite_score(
            direction_accuracy=result.direction_accuracy_pct,
            win_rate=result.win_rate_pct,
            avg_return=result.avg_stock_return_pct,
            consistency=result.consistency_score,
            completed=completed,
            records=records,
        ), 2)

        return result

    @staticmethod
    def _calc_consistency(returns: List[float]) -> Optional[float]:
        if not returns or len(returns) < 2:
            return None
        mean = statistics.mean(returns)
        if mean == 0:
            return 1.0 if all(r == 0 for r in returns) else 0.0
        try:
            cv = abs(statistics.stdev(returns) / mean)
        except statistics.StatisticsError:
            return None
        return round(max(0.0, min(1.0, 1.0 - cv / 2.0)), 4)

    @staticmethod
    def _calc_composite_score(
        direction_accuracy: Optional[float],
        win_rate: Optional[float],
        avg_return: Optional[float],
        consistency: Optional[float],
        completed: List[Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> float:
        if not completed:
            return 0.0
        da_norm = (direction_accuracy or 0) / 100.0
        wr_norm = (win_rate or 0) / 100.0
        ret_val = avg_return or 0.0
        ret_norm = 1.0 / (1.0 + math.exp(-ret_val / 5.0))
        cons_norm = consistency or 0.5
        conviction = ModelBenchmarkService._calc_conviction(completed, records)
        score = (
            da_norm * 0.35 + wr_norm * 0.30 + ret_norm * 0.20 +
            cons_norm * 0.10 + conviction * 0.05
        )
        return score * 100

    @staticmethod
    def _calc_conviction(completed, records) -> float:
        total = len(records)
        if total == 0:
            return 0.5
        decisive = [r for r in records if r.get("position_recommendation") == "long"]
        if not decisive:
            return 0.5
        decisive_ratio = len(decisive) / total
        decisive_correct = sum(
            1 for r in decisive
            if r.get("direction_correct") is True and r.get("outcome") == "win"
        )
        decisive_wrong = sum(
            1 for r in decisive
            if r.get("direction_correct") is False and r.get("outcome") == "loss"
        )
        if decisive_correct + decisive_wrong == 0:
            return 0.5
        correct_ratio = decisive_correct / (decisive_correct + decisive_wrong)
        return round(decisive_ratio * 0.4 + correct_ratio * 0.6, 4)

    @staticmethod
    def _extract_model_info(snapshot_json: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not snapshot_json:
            return None, None
        try:
            snapshot = json.loads(snapshot_json)
            if isinstance(snapshot, dict) and snapshot.get("benchmark"):
                return snapshot.get("model_id"), snapshot.get("benchmark_meta")
        except (json.JSONDecodeError, TypeError):
            pass
        return None, None


# ---------------------------------------------------------------------------
# BenchmarkTagger — passive / auto-tag every analysis for background scoring
# ---------------------------------------------------------------------------

class BenchmarkTagger:
    """
    被动评分标注器：在正常使用 main.py 运行分析时，自动为每一条分析结果标注
    model_id + 性能元数据（延迟 / Token / 成本估算），使得后续回测评估后可以
    自动产出跨模型对比报告，无需手动运行 benchmark CLI。

    === 使用方式（由 pipeline 自动调用，用户无需操作） ===

      from src.services.model_benchmark import BenchmarkTagger

      # 在每次分析执行前后：
      t0 = time.perf_counter()
      result = run_analysis(...)
      latency_ms = (time.perf_counter() - t0) * 1000

      # 为 context_snapshot 注入 benchmark 标签：
      enriched = BenchmarkTagger.enrich_context_snapshot(
          existing_snapshot=original_context_snapshot,
          model_id=config.litellm_model,
          latency_ms=latency_ms,
          agent_result=agent_result,   # Agent 路径传入，非 Agent 路径为 None
      )

      db.save_analysis_history(..., context_snapshot=enriched, ...)

    === 存储格式（context_snapshot 内） ===

      {
          ...原有字段...,
          "model_id": "anthropic/claude-sonnet-4-6",
          "benchmark": true,
          "benchmark_meta": {
              "latency_ms": 3421.5,
              "total_tokens": 8500,
              "prompt_tokens": 5100,
              "completion_tokens": 3400,
              "estimated_cost_usd": 0.0663
          }
      }

    只有含有 "benchmark": true 标记的分析记录，才会被 generate_report()
    JOIN 查询纳入跨模型对比。
    """

    @staticmethod
    def get_current_model_id(config) -> str:
        """
        获取当前正在使用的模型 ID。

        优先级：
        1. Agent 模式下的 AGENT_LITELLM_MODEL（显式配置）
        2. Agent 模式下的 LITELLM_MODEL（fallback 继承）
        3. 非 Agent 模式下的 LITELLM_MODEL
        4. 环境变量 LITELLM_MODEL 兜底
        """
        # Check agent-specific model first
        if getattr(config, 'agent_mode', False) or getattr(config, 'agent_skills', None):
            agent_model = getattr(config, 'agent_litellm_model', '') or ''
            if agent_model:
                return agent_model
        # Fall back to primary litellm model
        primary = getattr(config, 'litellm_model', '') or ''
        if primary:
            return primary
        # Last resort: env var
        return os.environ.get('LITELLM_MODEL', 'unknown')

    @staticmethod
    def enrich_context_snapshot(
        existing_snapshot: Optional[Dict[str, Any]],
        model_id: str,
        latency_ms: float,
        agent_result: Any = None,
    ) -> Dict[str, Any]:
        """
        为 context_snapshot 注入 benchmark 标注字段。

        Args:
            existing_snapshot: 原始 context_snapshot dict（可能为 None）
            model_id: 当前使用的模型 ID（如 "anthropic/claude-sonnet-4-6"）
            latency_ms: 分析耗时（毫秒）
            agent_result: AgentResult 对象（Agent 路径传入，用于提取 Token 用量）

        Returns:
            注入 benchmark 字段后的新 dict（不修改原对象）
        """
        snapshot = dict(existing_snapshot) if existing_snapshot else {}

        # 如果已有 benchmark 标记（例如 benchmark CLI 手动调用），保留不覆盖
        # 但兼容日常路径未打标的情况
        if snapshot.get("benchmark") and snapshot.get("model_id"):
            # Already tagged, update perf meta only if missing
            if not snapshot.get("benchmark_meta"):
                snapshot["benchmark_meta"] = BenchmarkTagger._build_benchmark_meta(
                    latency_ms, agent_result
                )
            return snapshot

        # Extract token usage from AgentResult (agent path)
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if agent_result is not None:
            total_tokens = getattr(agent_result, 'total_tokens', 0) or 0
            stats = getattr(agent_result, 'stats', None)
            if stats:
                prompt_tokens = getattr(stats, 'total_prompt_tokens', 0) or 0
                completion_tokens = getattr(stats, 'total_completion_tokens', 0) or 0
            # 如果没有细分数据但有总量，估算 6:4 拆分
            if not prompt_tokens and not completion_tokens and total_tokens:
                prompt_tokens = int(total_tokens * 0.6)
                completion_tokens = total_tokens - prompt_tokens

        cost = estimate_cost(model_id, prompt_tokens, completion_tokens)

        snapshot["model_id"] = model_id
        snapshot["benchmark"] = True
        snapshot["benchmark_meta"] = {
            "latency_ms": round(latency_ms, 1),
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": cost,
        }
        return snapshot

    @staticmethod
    def _build_benchmark_meta(
        latency_ms: float,
        agent_result: Any = None,
    ) -> Dict[str, Any]:
        """构建 benchmark_meta 子字典（内部复用）。"""
        total_tokens = 0
        prompt_tokens = 0
        completion_tokens = 0
        if agent_result is not None:
            total_tokens = getattr(agent_result, 'total_tokens', 0) or 0
            stats = getattr(agent_result, 'stats', None)
            if stats:
                prompt_tokens = getattr(stats, 'total_prompt_tokens', 0) or 0
                completion_tokens = getattr(stats, 'total_completion_tokens', 0) or 0
            if not prompt_tokens and not completion_tokens and total_tokens:
                prompt_tokens = int(total_tokens * 0.6)
                completion_tokens = total_tokens - prompt_tokens
        return {
            "latency_ms": round(latency_ms, 1),
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": 0.0,  # model_id unknown at this point
        }


# ---------------------------------------------------------------------------
# Report formatting (updated with performance section)
# ---------------------------------------------------------------------------

def format_benchmark_report(report: BenchmarkReport) -> str:
    if not report.models:
        return "No benchmark data available. Run --analyze first, wait for the evaluation window, then --evaluate."

    lines = []
    lines.append("=" * 110)
    lines.append("  MODEL BENCHMARK REPORT — US Stock Prediction Accuracy")
    lines.append("=" * 110)
    lines.append(f"  Generated    : {report.generated_at}")
    lines.append(f"  Eval Window   : {report.eval_window_days} trading days")
    lines.append(f"  Stocks        : {', '.join(report.stocks_analyzed)}")
    lines.append(f"  Date Range    : {report.date_range}")
    lines.append(f"  Models Tested : {len(report.models)}")
    lines.append("")

    # -- Accuracy Leaderboard --
    lines.append("  >>> ACCURACY LEADERBOARD (Composite Score)")
    header = (
        f"  {'Rank':<5} {'Model':<38} {'Score':<8} {'DirAcc%':<9} "
        f"{'WinRate%':<9} {'AvgRet%':<9} {'W':<4} {'L':<4} {'N':<4} {'Total':<6}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for m in report.leaderboard:
        rank_icon = {1: "1", 2: "2", 3: "3"}.get(m.rank, f"{m.rank}")
        line = (
            f"  {rank_icon:<5} {m.model_display[:37]:<38} {m.composite_score:<8.1f} "
            f"{_fmt_pct(m.direction_accuracy_pct):<9} "
            f"{_fmt_pct(m.win_rate_pct):<9} "
            f"{_fmt_pct(m.avg_stock_return_pct):<9} "
            f"{m.win_count:<4} {m.loss_count:<4} {m.neutral_count:<4} "
            f"{m.completed_evals:<6}"
        )
        lines.append(line)

    lines.append("")
    lines.append("  Scoring: Direction Accuracy 35% | Win Rate 30% | Excess Return 20% | Consistency 10% | Conviction 5%")

    # -- Performance Leaderboard --
    models_with_perf = [m for m in report.models if m.avg_latency_ms is not None]
    if models_with_perf:
        lines.append("")
        lines.append("  >>> PERFORMANCE LEADERBOARD (Latency & Cost)")
        perf_header = (
            f"  {'Model':<38} {'AvgLat':<10} {'Tokens':<10} {'Cost':<12} {'CostEff':<10}"
        )
        lines.append(perf_header)
        lines.append("  " + "-" * (len(perf_header) - 2))

        sorted_perf = sorted(models_with_perf, key=lambda m: m.avg_latency_ms or 0)
        for m in sorted_perf:
            lat = f"{m.avg_latency_ms:.0f}ms" if m.avg_latency_ms else "N/A"
            tok = _fmt_count(m.total_tokens_used)
            cost = f"${m.total_cost_usd:.4f}" if m.total_cost_usd > 0 else "free"
            ce = f"{m.cost_efficiency:.1f}" if m.cost_efficiency else "N/A"
            lines.append(
                f"  {m.model_display[:37]:<38} {lat:<10} {tok:<10} {cost:<12} {ce:<10}"
            )

        # Best value recommendation
        best_value = max(
            (m for m in models_with_perf if m.cost_efficiency),
            key=lambda m: m.cost_efficiency or 0,
            default=None,
        )
        if best_value and best_value != report.leaderboard[0]:
            lines.append(f"\n  Value pick: '{best_value.model_display}' has best cost-efficiency ({best_value.cost_efficiency})")
            lines.append(f"  (Top accuracy: '{report.leaderboard[0].model_display}' with score {report.leaderboard[0].composite_score:.1f})")

    lines.append("")

    # Per-model stock details
    for m in report.leaderboard:
        if not m.stock_details:
            continue
        lines.append(f"--- {m.model_display} — Per-Stock Breakdown ---")
        for code, detail in m.stock_details.items():
            denom = detail['wins'] + detail['losses']
            wr = f"{detail['wins']}/{denom}" if denom > 0 else "N/A"
            lines.append(
                f"  {code:<8}  Wins={detail['wins']}  Losses={detail['losses']}  "
                f"WR={wr}  AvgRet={_fmt_pct(detail['avg_return_pct'])}"
            )
        lines.append("")

    # Recommendation
    if report.leaderboard:
        best = report.leaderboard[0]
        lines.append("=" * 110)
        lines.append(f"  BEST MODEL: '{best.model_display}' — Score: {best.composite_score:.1f}")
        lines.append(f"  Direction Accuracy: {_fmt_pct(best.direction_accuracy_pct)} | "
                     f"Win Rate: {_fmt_pct(best.win_rate_pct)} | "
                     f"Avg Return: {_fmt_pct(best.avg_stock_return_pct)}")
        if best.avg_latency_ms:
            lines.append(f"  Avg Latency: {best.avg_latency_ms:.0f}ms | "
                         f"Total Cost: ${best.total_cost_usd:.4f}")
        lines.append("=" * 110)

    return "\n".join(lines)


def format_benchmark_json(report: BenchmarkReport) -> str:
    output = {
        "generated_at": report.generated_at,
        "eval_window_days": report.eval_window_days,
        "stocks_analyzed": report.stocks_analyzed,
        "date_range": report.date_range,
        "scoring_weights": {
            "direction_accuracy": 0.35,
            "win_rate": 0.30,
            "excess_return": 0.20,
            "consistency": 0.10,
            "conviction_bonus": 0.05,
        },
        "leaderboard": [
            {
                "rank": m.rank,
                "model_id": m.model_id,
                "model_display": m.model_display,
                "composite_score": m.composite_score,
                "direction_accuracy_pct": m.direction_accuracy_pct,
                "win_rate_pct": m.win_rate_pct,
                "avg_stock_return_pct": m.avg_stock_return_pct,
                "avg_simulated_return_pct": m.avg_simulated_return_pct,
                "return_stddev": m.return_stddev,
                "consistency_score": m.consistency_score,
                "avg_latency_ms": m.avg_latency_ms,
                "total_tokens_used": m.total_tokens_used,
                "total_cost_usd": m.total_cost_usd,
                "cost_efficiency": m.cost_efficiency,
                "win_count": m.win_count,
                "loss_count": m.loss_count,
                "neutral_count": m.neutral_count,
                "total_evaluations": m.completed_evals,
                "stock_details": m.stock_details,
            }
            for m in report.leaderboard
        ],
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        if cleaned.upper() in {"N/A", "-", "--", ""}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%" if value != 0 else "0.00%"


def _fmt_count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value/1_000:.1f}K"
    return str(value)


def _generate_run_id() -> str:
    import uuid
    return f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Model Benchmark — compare LLM accuracy for stock prediction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover available models
  python -m src.services.model_benchmark --list-models

  # Run analysis (sequential, no debug output)
  python -m src.services.model_benchmark --analyze --stocks AAPL,MSFT,NVDA

  # Run analysis in parallel with live debug comparison
  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA --parallel --debug

  # Limit parallel workers
  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA --parallel --max-parallel 2

  # Wait N trading days, then backtest
  python -m src.services.model_benchmark --evaluate --days 5

  # Generate comparison report
  python -m src.services.model_benchmark --report

  # Full workflow
  python -m src.services.model_benchmark --full --stocks AAPL,TSLA --parallel
        """,
    )

    parser.add_argument("--list-models", action="store_true",
                        help="List all discovered models and exit")
    parser.add_argument("--analyze", action="store_true",
                        help="Run analysis for specified stocks through all models")
    parser.add_argument("--evaluate", action="store_true",
                        help="Backtest stored predictions against actual prices")
    parser.add_argument("--report", action="store_true",
                        help="Generate comparison report from stored results")
    parser.add_argument("--full", action="store_true",
                        help="Run analyze + evaluate + report in sequence")
    parser.add_argument("--stocks", type=str, default="",
                        help="Comma-separated stock codes (e.g. AAPL,NVDA,MSFT)")
    parser.add_argument("--models", type=str, default="",
                        help="Comma-separated model_ids to test (default: all)")
    parser.add_argument("--days", type=int, default=5,
                        help="Evaluation window in trading days (default: 5)")
    parser.add_argument("--json", action="store_true",
                        help="Output report as JSON")
    # New flags
    parser.add_argument("--parallel", "-p", action="store_true",
                        help="Run model analyses concurrently (faster, may hit rate limits)")
    parser.add_argument("--max-parallel", type=int, default=ModelBenchmarkService.DEFAULT_MAX_PARALLEL,
                        help=f"Max concurrent model invocations (default: {ModelBenchmarkService.DEFAULT_MAX_PARALLEL})")
    parser.add_argument("--debug", "-d", action="store_true",
                        help="Capture performance metadata and show live side-by-side comparison")

    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))

    from src.config import setup_env
    setup_env()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    svc = ModelBenchmarkService()

    # --list-models
    if args.list_models:
        models = svc.list_models()
        if not models:
            print("No models discovered. Configure LLM_CHANNELS or API keys in .env")
            return
        print(f"\nDiscovered {len(models)} model(s):\n")
        for m in models:
            channel_info = f" (channel={m.channel})" if m.channel else ""
            print(f"  {m.model_id:<50} protocol={m.protocol}{channel_info}")
        print("\nTo benchmark these, run:")
        print("  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA")
        return

    # --analyze
    if args.analyze or args.full:
        if not args.stocks:
            print("ERROR: --stocks is required for --analyze. Example: --stocks AAPL,MSFT,NVDA")
            sys.exit(1)

        stock_codes = [s.strip().upper() for s in args.stocks.split(",") if s.strip()]
        model_filter = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None

        print(f"\nStarting analysis for {len(stock_codes)} stock(s) across "
              f"{len(model_filter) if model_filter else 'all'} model(s)")
        if args.parallel:
            print(f"Mode: PARALLEL (max {args.max_parallel} workers)")
            print("NOTE: Parallel mode runs models concurrently but is rate-limit sensitive.")
        if args.debug:
            print("Mode: DEBUG (performance metadata + live comparison)")
        print()

        result, run_meta = svc.run_analysis(
            stock_codes,
            models=model_filter,
            parallel=args.parallel,
            max_parallel=args.max_parallel,
            debug=args.debug or args.parallel,
        )

        ok_count = sum(
            1 for mr in result["runs"].values()
            for sr in mr.values() if sr.get("status") == "ok"
        )
        total = len(stock_codes) * len(result["models_tested"])
        print(f"\nAnalysis complete: {ok_count}/{total} successful")
        if result["errors"]:
            print(f"Errors: {len(result['errors'])}")
            for err in result["errors"][:5]:
                print(f"  [{err['model']}] {err['stock']}: {err['error']}")

        # Show debug comparison if requested
        if args.debug and run_meta:
            print(svc.format_debug_comparison(stock_codes, run_meta))

        if not args.full:
            print(f"\nBenchmark run ID: {result['benchmark_run_id']}")
            print("Wait for the evaluation window, then run:")
            print(f"  python -m src.services.model_benchmark --evaluate --days {args.days}")
            print(f"  python -m src.services.model_benchmark --report")

    # --evaluate
    if args.evaluate or args.full:
        print(f"\nRunning backtest evaluation (window={args.days} days)...\n")
        eval_result = svc.evaluate(eval_window_days=args.days)
        print(f"Evaluation: processed={eval_result.get('processed', 0)}, "
              f"completed={eval_result.get('completed', 0)}, "
              f"insufficient={eval_result.get('insufficient', 0)}, "
              f"errors={eval_result.get('errors', 0)}")

    # --report
    if args.report or args.full:
        print("\nGenerating benchmark report...\n")
        report = svc.generate_report(eval_window_days=args.days)

        if args.json:
            print(format_benchmark_json(report))
        else:
            print(format_benchmark_report(report))

        # Save report to file
        report_dir = project_root / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        (report_dir / f"model_benchmark_{ts}.json").write_text(
            format_benchmark_json(report), encoding="utf-8")
        (report_dir / f"model_benchmark_{ts}.txt").write_text(
            format_benchmark_report(report), encoding="utf-8")
        print(f"\nReports saved to: reports/model_benchmark_{ts}.{{txt,json}}")


if __name__ == "__main__":
    main()
