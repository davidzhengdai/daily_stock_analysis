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

=== Scoring Formula ===
  composite_score = (
    direction_accuracy_norm  * 0.35 +   # Did the model call the right direction?
    win_rate_norm            * 0.30 +   # Win / (Win + Loss) ratio
    excess_return_norm       * 0.20 +   # Avg return vs baseline
    consistency_norm         * 0.10 +   # 1 - (stddev / mean) of returns
    conviction_bonus         * 0.05     # Bonus for correct decisive calls
  )

=== Usage ===
  python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA
  python -m src.services.model_benchmark --evaluate --days 5
  python -m src.services.model_benchmark --report
  python -m src.services.model_benchmark --full --stocks AAPL,MSFT,TSLA  # all-in-one
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Lightweight descriptor for a configured LLM model."""
    model_id: str           # e.g. "openai/gpt-5.5", "gemini/gemini-3.1-pro-preview"
    protocol: str           # "openai", "anthropic", "gemini", "deepseek"
    channel: str            # channel name from LLM_CHANNELS, or "legacy_env"
    base_url: str = ""
    display_name: str = ""

    @property
    def short_name(self) -> str:
        """Human-friendly label for reports."""
        if self.display_name:
            return self.display_name
        parts = self.model_id.split("/", 1)
        return parts[1] if len(parts) > 1 else parts[0]


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


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------

def discover_models(config=None) -> List[ModelInfo]:
    """Discover all available LLM models from the current configuration.

    Reads from the resolved ``llm_model_list`` (which aggregates LLM_CHANNELS
    and legacy env vars). Returns a deduplicated list of :class:`ModelInfo`.
    """
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

    # Read LLM_CHANNELS for per-channel metadata (protocol, base_url)
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

    # Build ModelInfo for each configured model from the authoritative model list
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
# Model override context manager
# ---------------------------------------------------------------------------

class _ModelOverride:
    """Temporarily override LITELLM_MODEL for a single analysis run.

    Context manager that sets the env var, reloads config, and restores on exit.
    """

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

    def __init__(self):
        from src.config import get_config
        self._config = get_config()

    # -- Discovery ----------------------------------------------------------

    def list_models(self) -> List[ModelInfo]:
        """Return all discovered models ready for benchmarking."""
        return discover_models(self._config)

    # -- Analysis phase -----------------------------------------------------

    def run_analysis(
        self,
        stock_codes: List[str],
        models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run analysis for each (stock * model) combination.

        Args:
            stock_codes: List of stock codes (e.g. ["AAPL", "NVDA"]).
            models: Specific model_ids to test. None = all discovered models.

        Returns:
            Dict with summary of runs: {model_id: {stock_code: analysis_id, ...}, ...}
        """
        if len(stock_codes) > self.MAX_STOCKS:
            raise ValueError(
                f"Maximum {self.MAX_STOCKS} stocks allowed per benchmark run "
                f"(got {len(stock_codes)}). "
                f"Each stock * model combination triggers a full LLM analysis pipeline."
            )

        available = self.list_models()
        if not available:
            raise RuntimeError(
                "No models discovered. Configure at least one LLM provider via "
                "LLM_CHANNELS or legacy API key env vars."
            )

        if models:
            # Filter to requested models
            model_ids_lower = {m.lower() for m in models}
            selected = [m for m in available if m.model_id.lower() in model_ids_lower]
            if not selected:
                raise ValueError(
                    f"No matching models found for: {models}. "
                    f"Available: {[m.model_id for m in available]}"
                )
        else:
            selected = available

        logger.info(
            "Starting benchmark analysis: %d stock(s) x %d model(s) = %d total runs",
            len(stock_codes), len(selected), len(stock_codes) * len(selected),
        )

        results: Dict[str, Any] = {
            "benchmark_run_id": _generate_run_id(),
            "timestamp": datetime.now().isoformat(),
            "stocks": stock_codes,
            "models_tested": [m.model_id for m in selected],
            "runs": {},
            "errors": [],
        }

        for model_info in selected:
            logger.info("--- Model: %s ---", model_info.model_id)
            model_runs: Dict[str, Any] = {}

            with _ModelOverride(model_info.model_id):
                for code in stock_codes:
                    try:
                        analysis_id = self._run_single_analysis(
                            stock_code=code,
                            model_id=model_info.model_id,
                        )
                        model_runs[code] = {"analysis_id": analysis_id, "status": "ok"}
                        logger.info("  [%s] -> analysis_id=%s", code, analysis_id)
                    except Exception as exc:
                        logger.error("  [%s] FAILED: %s", code, exc)
                        model_runs[code] = {"status": "error", "error": str(exc)}
                        results["errors"].append({
                            "model": model_info.model_id,
                            "stock": code,
                            "error": str(exc),
                        })

            results["runs"][model_info.model_id] = model_runs

        total_ok = sum(
            1 for mr in results["runs"].values()
            for sr in mr.values()
            if sr.get("status") == "ok"
        )
        logger.info(
            "Benchmark analysis complete: %d/%d successful",
            total_ok, len(stock_codes) * len(selected),
        )
        return results

    def _run_single_analysis(self, stock_code: str, model_id: str) -> int:
        """Run a single stock analysis and return the analysis_history ID."""
        from src.config import get_config
        from src.agent.factory import build_agent_executor
        from src.storage import DatabaseManager

        config = get_config()
        db = DatabaseManager.get_instance()

        # Build agent with fresh config (model already overridden via env)
        executor = build_agent_executor(config)

        task = f"分析 {stock_code}"
        result = executor.run(task, context={
            "stock_code": stock_code,
            "report_language": "en",  # US stocks → English reports
        })

        if not result.success:
            raise RuntimeError(f"Analysis failed: {result.error}")

        # Extract key fields from dashboard
        dashboard = result.dashboard or {}
        operation_advice = dashboard.get("operation_advice", "")
        sentiment_score = None
        try:
            sentiment_score = int(dashboard.get("sentiment_score", 0))
        except (TypeError, ValueError):
            pass

        trend_prediction = dashboard.get("trend_prediction", "")
        analysis_summary = dashboard.get("analysis_summary", "")

        # Extract sniper points
        battle_plan = (dashboard.get("dashboard") or {}).get("battle_plan") or {}
        sniper = battle_plan.get("sniper_points", {})
        ideal_buy = _parse_float(sniper.get("ideal_buy"))
        secondary_buy = _parse_float(sniper.get("secondary_buy"))
        stop_loss = _parse_float(sniper.get("stop_loss"))
        take_profit = _parse_float(sniper.get("take_profit"))

        # Build context_snapshot with model_id tag
        context_snapshot = {
            "model_id": model_id,
            "benchmark": True,
            "benchmark_run_id": getattr(self, "_current_run_id", ""),
            "decision_type": dashboard.get("decision_type", ""),
            "confidence_level": dashboard.get("confidence_level", ""),
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
            # Fallback for older save_analysis_history signatures
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

        return analysis_id

    # -- Evaluation phase ---------------------------------------------------

    def evaluate(
        self,
        eval_window_days: int = DEFAULT_EVAL_WINDOW_DAYS,
        models: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run backtesting for all benchmark-tagged analyses.

        Args:
            eval_window_days: Number of trading days to look forward.
            models: Filter to specific model_ids. None = all benchmarked models.

        Returns:
            Dict with evaluation summary.
        """
        from src.services.backtest_service import BacktestService

        svc = BacktestService()

        # First, run backtest on all pending benchmark analyses
        # The BacktestService filters by candidates that haven't been evaluated
        result = svc.run_backtest(
            code=None,  # all codes
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
        """Generate a comparison report for all benchmarked models.

        Queries backtest results grouped by model_id (from context_snapshot),
        computes per-model metrics, and ranks models by composite score.
        """
        from src.storage import DatabaseManager, AnalysisHistory, BacktestResult
        from sqlalchemy import and_, select

        db = DatabaseManager.get_instance()
        report = BenchmarkReport(
            generated_at=datetime.now().isoformat(),
            eval_window_days=eval_window_days,
        )

        with db.get_session() as session:
            # Join backtest_results -> analysis_history to get context_snapshot
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

        # Group results by model_id
        model_results: Dict[str, List[Dict[str, Any]]] = {}
        stock_codes: set[str] = set()

        for bt_row, snapshot_json, code in rows:
            model_id = self._extract_model_id(snapshot_json)
            if not model_id:
                continue  # skip non-benchmark analyses

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

        report.stocks_analyzed = sorted(stock_codes)

        # Compute per-model metrics
        for model_id, records in model_results.items():
            model_report = self._compute_model_metrics(model_id, records)
            report.models.append(model_report)

        # Rank by composite score
        ranked = sorted(report.models, key=lambda m: m.composite_score, reverse=True)
        for i, m in enumerate(ranked, start=1):
            m.rank = i

        report.models = ranked

        # Determine date range
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
    ) -> ModelBenchmarkResult:
        """Compute all metrics for a single model from its backtest records."""
        result = ModelBenchmarkResult(
            model_id=model_id,
            model_display=model_id,
            total_analyses=len(records),
            completed_evals=len(records),
        )

        completed = [r for r in records if r["outcome"] is not None]

        # Win/Loss/Neutral counts
        result.win_count = sum(1 for r in completed if r["outcome"] == "win")
        result.loss_count = sum(1 for r in completed if r["outcome"] == "loss")
        result.neutral_count = sum(1 for r in completed if r["outcome"] == "neutral")

        # Direction accuracy
        dir_records = [r for r in completed if r["direction_correct"] is not None]
        result.direction_total = len(dir_records)
        result.direction_correct_count = sum(1 for r in dir_records if r["direction_correct"] is True)
        if result.direction_total > 0:
            result.direction_accuracy_pct = round(
                result.direction_correct_count / result.direction_total * 100, 2
            )

        # Win rate (excluding neutrals)
        win_loss_total = result.win_count + result.loss_count
        if win_loss_total > 0:
            result.win_rate_pct = round(result.win_count / win_loss_total * 100, 2)

        # Average returns
        returns = [r["stock_return_pct"] for r in completed if r["stock_return_pct"] is not None]
        sim_returns = [r["simulated_return_pct"] for r in completed if r["simulated_return_pct"] is not None]

        if returns:
            result.avg_stock_return_pct = round(statistics.mean(returns), 2)
            if len(returns) >= 2:
                result.return_stddev = round(statistics.stdev(returns), 2)

        if sim_returns:
            result.avg_simulated_return_pct = round(statistics.mean(sim_returns), 2)

        # Per-stock details
        stock_groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in completed:
            stock_groups.setdefault(r["code"], []).append(r)

        for code, stock_records in stock_groups.items():
            stock_wins = sum(1 for r in stock_records if r["outcome"] == "win")
            stock_losses = sum(1 for r in stock_records if r["outcome"] == "loss")
            stock_returns = [r["stock_return_pct"] for r in stock_records if r["stock_return_pct"] is not None]
            result.stock_details[code] = {
                "total": len(stock_records),
                "wins": stock_wins,
                "losses": stock_losses,
                "avg_return_pct": round(statistics.mean(stock_returns), 2) if stock_returns else None,
            }

        # Composite score calculation
        result.consistency_score = self._calc_consistency(returns)
        result.composite_score = self._calc_composite_score(
            direction_accuracy=result.direction_accuracy_pct,
            win_rate=result.win_rate_pct,
            avg_return=result.avg_stock_return_pct,
            consistency=result.consistency_score,
            completed=completed,
            records=records,
        )
        result.composite_score = round(result.composite_score, 2)

        return result

    @staticmethod
    def _calc_consistency(returns: List[float]) -> Optional[float]:
        """Calculate consistency: 1 - CV (coefficient of variation)."""
        if not returns or len(returns) < 2:
            return None
        mean = statistics.mean(returns)
        if mean == 0:
            return 1.0 if all(r == 0 for r in returns) else 0.0
        try:
            cv = abs(statistics.stdev(returns) / mean)
        except statistics.StatisticsError:
            return None
        # Map CV to [0, 1]: CV=0 → perfect consistency (1.0), CV>2 → poor (0.0)
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
        """Compute composite score from normalized metrics.

        Weights:
            direction_accuracy: 0.35
            win_rate:           0.30
            excess_return:      0.20
            consistency:        0.10
            conviction_bonus:   0.05
        """
        if not completed:
            return 0.0

        # Normalize each metric to [0, 1]
        da_norm = (direction_accuracy or 0) / 100.0
        wr_norm = (win_rate or 0) / 100.0

        # Excess return: use sigmoid to map return % to [0, 1]
        # +5% return → ~0.73, +10% → ~0.88, -5% → ~0.27
        import math
        ret_val = avg_return or 0.0
        ret_norm = 1.0 / (1.0 + math.exp(-ret_val / 5.0))

        cons_norm = consistency or 0.5

        # Conviction bonus: reward models that made decisive calls correctly
        conviction = ModelBenchmarkService._calc_conviction(completed, records)

        score = (
            da_norm * 0.35 +
            wr_norm * 0.30 +
            ret_norm * 0.20 +
            cons_norm * 0.10 +
            conviction * 0.05
        )
        return score * 100  # scale to 0-100

    @staticmethod
    def _calc_conviction(
        completed: List[Dict[str, Any]],
        records: List[Dict[str, Any]],
    ) -> float:
        """Calculate conviction bonus — reward decisive & correct, penalize decisive & wrong."""
        total = len(records)
        if total == 0:
            return 0.5

        # A model is "decisive" if it gives buy/sell (not hold/cash)
        decisive = [r for r in records if r.get("position_recommendation") == "long"]
        if not decisive:
            return 0.5  # neutral if always holding cash

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

        # Ratio of correct decisive calls
        correct_ratio = decisive_correct / (decisive_correct + decisive_wrong)

        # Blend: how often decisive AND how often correct when decisive
        return round(decisive_ratio * 0.4 + correct_ratio * 0.6, 4)

    @staticmethod
    def _extract_model_id(snapshot_json: Optional[str]) -> Optional[str]:
        """Extract model_id from an analysis_history.context_snapshot JSON string."""
        if not snapshot_json:
            return None
        try:
            snapshot = json.loads(snapshot_json)
            if isinstance(snapshot, dict):
                # Only return model_id if this is a benchmark-tagged analysis
                if snapshot.get("benchmark"):
                    return snapshot.get("model_id")
        except (json.JSONDecodeError, TypeError):
            pass
        return None


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def format_benchmark_report(report: BenchmarkReport) -> str:
    """Format a BenchmarkReport as a human-readable text table."""
    if not report.models:
        return "No benchmark data available. Run --analyze first, wait for the evaluation window, then --evaluate."

    lines = []
    lines.append("=" * 90)
    lines.append("  MODEL BENCHMARK REPORT — US Stock Prediction Accuracy")
    lines.append("=" * 90)
    lines.append(f"  Generated    : {report.generated_at}")
    lines.append(f"  Eval Window   : {report.eval_window_days} trading days")
    lines.append(f"  Stocks        : {', '.join(report.stocks_analyzed)}")
    lines.append(f"  Date Range    : {report.date_range}")
    lines.append(f"  Models Tested : {len(report.models)}")
    lines.append("")

    # Leaderboard table
    header = (
        f"{'Rank':<5} {'Model':<35} {'Score':<8} {'DirAcc%':<9} "
        f"{'WinRate%':<9} {'AvgRet%':<9} {'W':<4} {'L':<4} {'N':<4} {'Total':<6}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    for m in report.leaderboard:
        rank_icon = {1: "1", 2: "2", 3: "3"}.get(m.rank, f"{m.rank}")
        line = (
            f"{rank_icon:<5} {m.model_display[:34]:<35} {m.composite_score:<8.1f} "
            f"{_fmt_pct(m.direction_accuracy_pct):<9} "
            f"{_fmt_pct(m.win_rate_pct):<9} "
            f"{_fmt_pct(m.avg_stock_return_pct):<9} "
            f"{m.win_count:<4} {m.loss_count:<4} {m.neutral_count:<4} "
            f"{m.completed_evals:<6}"
        )
        lines.append(line)

    lines.append("")
    lines.append("Scoring weights: Direction Accuracy 35% | Win Rate 30% | Excess Return 20% | Consistency 10% | Conviction 5%")
    lines.append("")

    # Per-model stock details
    for m in report.leaderboard:
        if not m.stock_details:
            continue
        lines.append(f"--- {m.model_display} — Per-Stock Breakdown ---")
        for code, detail in m.stock_details.items():
            wr = f"{detail['wins']}/{detail['wins']+detail['losses']}" if (detail['wins'] + detail['losses']) > 0 else "N/A"
            lines.append(
                f"  {code:<8}  Wins={detail['wins']}  Losses={detail['losses']}  "
                f"WR={wr}  AvgRet={_fmt_pct(detail['avg_return_pct'])}"
            )
        lines.append("")

    # Top model recommendation
    if report.leaderboard:
        best = report.leaderboard[0]
        lines.append("=" * 90)
        lines.append(f"  RECOMMENDATION: Use '{best.model_display}' for US stock analysis")
        lines.append(f"  Score: {best.composite_score:.1f} | Direction Accuracy: {_fmt_pct(best.direction_accuracy_pct)}")
        lines.append("=" * 90)

    return "\n".join(lines)


def format_benchmark_json(report: BenchmarkReport) -> str:
    """Format a BenchmarkReport as JSON."""
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
    """Safely parse a value to float, returning None on failure."""
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
    """Format a percentage value for display."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%" if value != 0 else "0.00%"


def _generate_run_id() -> str:
    """Generate a unique benchmark run ID."""
    import uuid
    return f"bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for model benchmarking.

    Usage:
        python -m src.services.model_benchmark --list-models
        python -m src.services.model_benchmark --analyze --stocks AAPL,NVDA,MSFT
        python -m src.services.model_benchmark --evaluate --days 5
        python -m src.services.model_benchmark --report
        python -m src.services.model_benchmark --full --stocks AAPL,TSLA --days 5
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Model Benchmark — compare LLM accuracy for stock prediction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Discover available models
  python -m src.services.model_benchmark --list-models

  # Run analysis for specific stocks through all models
  python -m src.services.model_benchmark --analyze --stocks AAPL,MSFT,NVDA

  # Wait N trading days, then backtest predictions
  python -m src.services.model_benchmark --evaluate --days 5

  # Generate comparison report
  python -m src.services.model_benchmark --report

  # Full workflow (analyze + immediate evaluate + report)
  python -m src.services.model_benchmark --full --stocks AAPL,TSLA
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

    args = parser.parse_args()

    # Initialize environment
    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))

    from src.config import setup_env
    setup_env()

    # Configure logging
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
            print(f"  {m.model_id:<45} protocol={m.protocol}{channel_info}")
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
              f"{len(model_filter) if model_filter else 'all'} model(s)...\n")

        result = svc.run_analysis(stock_codes, models=model_filter)

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
        report_path = project_root / "reports" / f"model_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.with_suffix(".json").write_text(format_benchmark_json(report), encoding="utf-8")
        report_path.with_suffix(".txt").write_text(format_benchmark_report(report), encoding="utf-8")
        print(f"\nReports saved to: {report_path}.{{txt,json}}")


if __name__ == "__main__":
    main()
