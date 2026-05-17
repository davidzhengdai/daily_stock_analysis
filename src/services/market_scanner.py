# -*- coding: utf-8 -*-
"""
Market Scanner

Orchestrates the 5-tier funnel to scan all NYSE/NASDAQ stocks and produce
a ranked list of top investment opportunities for a medium-term horizon.

Tier 1  metadata filter           (in-memory, instant)
Tier 2  batch technical screen    (yfinance bulk, ~2-5 min)
Tier 3  fundamental screen        (yfinance .info, ~5-10 min)
Tier 4  sector diversity filter   (in-memory, instant)
Tier 5  LLM deep analysis         (pipeline.process_single_stock, ~20-40 min)
"""

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.config import Config, get_config
from src.schemas.scanner import (
    CandidateStock,
    FundScore,
    InvestmentThesis,
    ScanConfig,
    ScanMeta,
    ScanReport,
    StockRecommendation,
)
from src.services.screening_engine import ScreeningEngine, _configured_markets, _select_market_balanced
from src.services.stock_universe import CNStockUniverse, USStockUniverse

logger = logging.getLogger(__name__)

def _resolve_results_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).parent / "scanner_cache" / "results"

_RESULTS_DIR = _resolve_results_dir()
_TIER5_WORKERS = 3   # parallel pipeline workers for Tier 5


def _make_progress(cb: Optional[Callable[[int, str], None]], pct: int, msg: str) -> None:
    if cb:
        try:
            cb(pct, msg)
        except Exception:
            pass


def _candidate_market_counts(candidates: List[CandidateStock]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for candidate in candidates:
        market = str(candidate.fund.tech.stock.market or "unknown").lower()
        counts[market] = counts.get(market, 0) + 1
    return counts


def _analysis_market_counts(items: List[tuple]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for candidate, _result in items:
        market = str(candidate.fund.tech.stock.market or "unknown").lower()
        counts[market] = counts.get(market, 0) + 1
    return counts


def _build_investment_thesis(result: Any, candidate: CandidateStock) -> InvestmentThesis:
    """Extract InvestmentThesis from a pipeline AnalysisResult."""
    financial = (
        getattr(result, "fundamental_analysis", "")
        or getattr(result, "company_highlights", "")
        or "Financial analysis not available."
    )
    industry_news = (
        getattr(result, "news_summary", "")
        or getattr(result, "hot_topics", "")
        or "Industry news not available."
    )
    global_status = (
        getattr(result, "sector_position", "")
        or getattr(result, "market_sentiment", "")
        or "Global industry status not available."
    )
    risks = getattr(result, "risk_warning", "")

    # Try to pull entry strategy from dashboard
    entry_parts = []
    dashboard = getattr(result, "dashboard", None) or {}
    battle_plan = dashboard.get("battle_plan") or {}
    sniper = battle_plan.get("sniper_points") or {}
    if sniper.get("ideal_buy"):
        entry_parts.append(f"Buy zone: {sniper['ideal_buy']}")
    if sniper.get("secondary_buy"):
        entry_parts.append(f"Secondary: {sniper['secondary_buy']}")
    if sniper.get("stop_loss"):
        entry_parts.append(f"Stop loss: {sniper['stop_loss']}")
    if sniper.get("take_profit"):
        entry_parts.append(f"Target: {sniper['take_profit']}")
    entry_strategy = " | ".join(entry_parts) if entry_parts else getattr(result, "buy_reason", "")

    return InvestmentThesis(
        financial_summary=financial,
        industry_news=industry_news,
        global_industry_status=global_status,
        entry_strategy=entry_strategy,
        key_risks=risks,
    )


def _build_recommendation(rank: int, candidate: CandidateStock, result: Any) -> StockRecommendation:
    thesis = _build_investment_thesis(result, candidate)
    why_selected, selection_factors = _build_selection_explanation(candidate, result)
    return StockRecommendation(
        rank=rank,
        ticker=candidate.ticker,
        name=getattr(result, "name", candidate.ticker),
        market=candidate.fund.tech.stock.market,
        sector=candidate.sector,
        industry=candidate.fund.tech.stock.industry,
        current_price=getattr(result, "current_price", candidate.fund.tech.stock.price) or 0.0,
        composite_score=candidate.composite_score,
        llm_confidence=getattr(result, "sentiment_score", 50) or 50,
        buy_signal=candidate.fund.tech.buy_signal,
        why_selected=why_selected,
        selection_factors=selection_factors,
        news_evidence=getattr(result, "news_evidence", []) or [],
        thesis=thesis,
        llm_decision=getattr(result, "decision_type", "hold") or "hold",
        analysis_summary=getattr(result, "analysis_summary", "") or "",
    )


def _build_selection_explanation(candidate: CandidateStock, result: Any) -> tuple:
    """Build a customer-facing explanation for why a candidate was selected."""
    stock = candidate.fund.tech.stock
    tech = candidate.fund.tech
    fund = candidate.fund
    market_label = "A股" if stock.market == "cn" else "美股"

    factors = [
        f"{market_label}候选中综合评分 {candidate.composite_score:.1f}/100，进入行业分散后的第 {candidate.sector_rank} 梯队。",
        f"技术面评分 {tech.signal_score}/100，趋势状态为「{tech.trend_status}」，买入信号为「{tech.buy_signal}」。",
        f"基本面评分 {fund.fundamental_score:.1f}/100，行业为「{stock.sector or stock.industry or '未分类'}」。",
    ]
    if fund.pe_ratio:
        factors.append(f"估值参考 PE {fund.pe_ratio:.1f}。")
    if fund.revenue_growth is not None:
        factors.append(f"收入增长约 {fund.revenue_growth * 100:.1f}%。")
    if stock.market == "cn":
        factors.append("A股候选额外参考中国政策、国家热点和产业主题相关性。")

    summary = getattr(result, "analysis_summary", "") or getattr(result, "buy_reason", "")
    if summary:
        why_selected = f"{factors[0]} AI 进一步确认：{summary}"
    else:
        why_selected = " ".join(factors[:3])
    return why_selected, factors


def _format_scan_report(report: ScanReport) -> str:
    """Render a human-readable Markdown scan report."""
    lines = [
        f"# Cross-Market Scan Report — {report.timestamp[:10]}",
        "",
        "## Scan Summary",
        f"- Universe: **{report.universe_size:,}** stocks scanned",
        f"- After metadata filter: {report.tier1_survivors:,}",
        f"- After technical screen: {report.tier2_survivors}",
        f"- After fundamental screen: {report.tier3_survivors}",
        f"- Deep analysed by AI: {report.tier5_analyzed}",
        f"- Scan duration: {report.duration_s / 60:.0f} min",
        "",
        f"## Top {len(report.top_picks)} Investment Opportunities (Medium-term 1–6 months)",
        "",
    ]
    for pick in report.top_picks:
        evidence_lines = [
            f"- [{item.get('dimension', 'News')}] {item.get('title', '')}"
            + (f" ({item.get('source')})" if item.get("source") else "")
            + (f" — {item.get('url')}" if item.get("url") else "")
            for item in pick.news_evidence[:5]
        ] or ["_No article evidence_"]
        lines += [
            f"### #{pick.rank} {pick.ticker} — {pick.name} ({pick.sector})",
            f"**Score**: {pick.composite_score:.0f}/100 | "
            f"**AI Confidence**: {pick.llm_confidence}/100 | "
            f"**Signal**: {pick.buy_signal}",
            "",
            "#### Why Selected",
            pick.why_selected or "_No explanation_",
            "",
            "#### Selection Factors",
            *[f"- {factor}" for factor in pick.selection_factors],
            "",
            "#### News Evidence",
            *evidence_lines,
            "",
            "#### Financial Health",
            pick.thesis.financial_summary or "_No data_",
            "",
            "#### Industry News",
            pick.thesis.industry_news or "_No data_",
            "",
            "#### Global Industry Status",
            pick.thesis.global_industry_status or "_No data_",
            "",
            "#### Entry Strategy",
            pick.thesis.entry_strategy or "_See full analysis_",
            "",
            "#### Key Risks",
            pick.thesis.key_risks or "_No data_",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


class MarketScanner:
    """Orchestrates the full 5-tier market scan."""

    def __init__(
        self,
        config: Optional[Config] = None,
        universe_cache_hours: Optional[int] = None,
    ):
        self.config = config or get_config()
        cache_hours = universe_cache_hours or getattr(
            self.config, "scanner_universe_cache_hours", 24
        )
        self._universe = USStockUniverse(cache_max_age_hours=cache_hours)
        self._cn_universe = CNStockUniverse()
        self._engine = ScreeningEngine()
        self._results_lock = threading.Lock()
        self._scans: Dict[str, Dict[str, Any]] = {}  # scan_id → progress/result dict
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start_scan(
        self,
        scan_config: Optional[ScanConfig] = None,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> str:
        """Start a scan in a background thread and return the scan_id."""
        scan_id = uuid.uuid4().hex[:12]
        cfg = scan_config or self._default_scan_config()
        with self._results_lock:
            self._scans[scan_id] = {
                "status": "running",
                "progress": 0,
                "message": "Scan started",
                "result": None,
                "error": None,
                "started_at": datetime.now().isoformat(),
            }

        def _run():
            try:
                report = self._run_scan(scan_id, cfg, progress_cb)
                with self._results_lock:
                    self._scans[scan_id]["status"] = "completed"
                    self._scans[scan_id]["progress"] = 100
                    self._scans[scan_id]["result"] = report.to_dict()
                    self._scans[scan_id]["completed_at"] = datetime.now().isoformat()
                self._save_result(report)
                self._send_notifications(report)
            except Exception as exc:
                logger.exception("Market scan %s failed: %s", scan_id, exc)
                with self._results_lock:
                    self._scans[scan_id]["status"] = "failed"
                    self._scans[scan_id]["error"] = str(exc)

        thread = threading.Thread(target=_run, name=f"scanner-{scan_id}", daemon=True)
        thread.start()
        return scan_id

    def get_status(self, scan_id: str) -> Optional[Dict[str, Any]]:
        with self._results_lock:
            return self._scans.get(scan_id)

    def get_result(self, scan_id: str) -> Optional[Dict[str, Any]]:
        with self._results_lock:
            entry = self._scans.get(scan_id)
        if entry and entry.get("result"):
            return entry["result"]
        # Try persisted result
        path = _RESULTS_DIR / f"{scan_id}.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def get_latest_result(self) -> Optional[Dict[str, Any]]:
        """Return the most recently completed scan result."""
        files = sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                continue
        return None

    def list_scans(self) -> List[ScanMeta]:
        metas: List[ScanMeta] = []
        for f in sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                picks = data.get("top_picks", [])
                top = picks[0] if picks else {}
                metas.append(ScanMeta(
                    scan_id=data.get("scan_id", f.stem),
                    timestamp=data.get("timestamp", ""),
                    top_ticker=top.get("ticker", ""),
                    top_score=top.get("composite_score", 0.0),
                    universe_size=data.get("funnel", {}).get("universe", 0),
                    top_n=len(picks),
                    duration_s=data.get("duration_s", 0.0),
                    status=data.get("status", "completed"),
                ))
            except Exception:
                continue
        return metas

    # ------------------------------------------------------------------
    # Internal scan execution
    # ------------------------------------------------------------------

    def _run_scan(
        self,
        scan_id: str,
        cfg: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]],
    ) -> ScanReport:
        started = time.monotonic()

        def _cb(pct: int, msg: str) -> None:
            with self._results_lock:
                if scan_id in self._scans:
                    self._scans[scan_id]["progress"] = pct
                    self._scans[scan_id]["message"] = msg
            _make_progress(progress_cb, pct, msg)

        # ── Tier 1 ──────────────────────────────────────────────────────
        _cb(2, "Fetching stock universe…")
        all_stocks = self._load_market_universe(cfg)
        universe_size = len(all_stocks)
        _cb(8, f"Universe: {universe_size:,} stocks. Applying metadata filter…")
        tier1 = self._engine.tier1_filter(all_stocks, cfg)
        _cb(10, f"Tier 1 → {len(tier1):,} stocks. Running technical screen…")

        # ── Tier 2 ──────────────────────────────────────────────────────
        tier2 = self._engine.tier2_technical_screen(tier1, cfg, progress_cb=_cb)
        _cb(40, f"Tier 2 → {len(tier2)} candidates. Running fundamental screen…")

        # ── Tier 3 ──────────────────────────────────────────────────────
        tier3 = self._engine.tier3_fundamental_screen(tier2, cfg, progress_cb=_cb)
        tier3 = self._apply_china_policy_weight(tier3, cfg, _cb)
        _cb(65, f"Tier 3 → {len(tier3)} candidates. Applying sector filter…")

        # ── Tier 4 ──────────────────────────────────────────────────────
        tier4_pool = self._build_tier4_preselection_pool(tier3, cfg, _cb)
        tier4 = self._ai_preselect_tier5_candidates(tier4_pool, cfg, _cb)
        _cb(70, f"Tier 4 → {len(tier4)} AI-preselected candidates. Running LLM analysis…")

        # ── Tier 5 ──────────────────────────────────────────────────────
        top_picks = self._tier5_llm_analysis(tier4, cfg, _cb)
        _cb(98, f"Analysis complete. Building report…")

        elapsed = time.monotonic() - started
        report = ScanReport(
            scan_id=scan_id,
            timestamp=datetime.now().isoformat(),
            config=cfg.to_dict(),
            universe_size=universe_size,
            tier1_survivors=len(tier1),
            tier2_survivors=len(tier2),
            tier3_survivors=len(tier3),
            tier4_survivors=len(tier4),
            tier5_analyzed=len(tier4),
            top_picks=top_picks,
            duration_s=elapsed,
        )
        return report

    def _load_market_universe(self, cfg: ScanConfig):
        markets = {m.lower() for m in (cfg.markets or ["us"])}
        stocks = []
        if "us" in markets:
            stocks.extend(self._universe.get_all())
        if "cn" in markets:
            cn_stocks = self._cn_universe.get_all(limit=cfg.max_cn_stocks)
            logger.info("China scanner universe loaded: %d stocks", len(cn_stocks))
            stocks.extend(cn_stocks)
        return stocks

    def _apply_china_policy_weight(
        self,
        candidates: List[FundScore],
        cfg: ScanConfig,
        progress_cb: Callable[[int, str], None],
    ) -> List[FundScore]:
        if not candidates or "cn" not in {m.lower() for m in (cfg.markets or [])}:
            return candidates
        if cfg.china_policy_weight <= 0:
            return candidates

        cn_candidates = [c for c in candidates if c.tech.stock.market == "cn"]
        if not cn_candidates:
            return candidates

        progress_cb(62, "Applying China policy and hot-topic weighting…")
        themes = self._detect_china_themes(cfg)
        if not themes:
            return candidates

        weight = max(0.0, min(1.0, cfg.china_policy_weight))
        for fs in cn_candidates:
            theme_score = self._china_theme_score(fs, themes)
            if theme_score <= 0:
                continue
            boosted = fs.composite_score * (1 - weight) + theme_score * weight
            fs.composite_score = round(max(fs.composite_score, boosted), 2)

        candidates.sort(key=lambda x: x.composite_score, reverse=True)
        logger.info("Applied China policy weighting with %d theme(s)", len(themes))
        return candidates

    def _detect_china_themes(self, cfg: ScanConfig):
        try:
            from src.analyzer import GeminiAnalyzer
            from src.services.theme_detector import ThemeDetector
            detector = ThemeDetector(GeminiAnalyzer(), search_service=None)
            themes = detector.detect_themes(count=6, date_str=datetime.now().date().isoformat())
        except Exception as exc:
            logger.warning("China theme detection failed, using fallback themes: %s", exc)
            try:
                from src.services.theme_detector import ThemeDetector
                themes = ThemeDetector._fallback_themes()
            except Exception:
                themes = []

        return [
            t for t in themes
            if "cn" in getattr(t, "market_regions", []) or "global" in getattr(t, "market_regions", [])
        ]

    @staticmethod
    def _china_theme_score(candidate: FundScore, themes) -> float:
        stock = candidate.tech.stock
        text = f"{stock.name} {stock.sector} {stock.industry}".lower()
        best = 0.0
        for theme in themes:
            sectors = set(getattr(theme, "relevant_sectors", []) or [])
            keywords = [str(k).lower() for k in (getattr(theme, "keywords", []) or [])]
            sector_hit = bool(stock.sector and stock.sector in sectors)
            keyword_hits = sum(1 for kw in keywords if kw and kw in text)
            if sector_hit and keyword_hits:
                score = 80 + min(20, keyword_hits * 5)
            elif sector_hit:
                score = 60
            elif keyword_hits:
                score = 45 + min(35, keyword_hits * 8)
            else:
                score = 0
            if getattr(theme, "sentiment", "neutral") == "bearish":
                score *= 0.6
            best = max(best, score)
        return best

    def _build_tier4_preselection_pool(
        self,
        tier3: List[FundScore],
        cfg: ScanConfig,
        progress_cb: Callable[[int, str], None],
    ) -> List[CandidateStock]:
        """Build a broader sector-diverse pool before AI chooses Tier-5 candidates."""
        if getattr(self.config, "scanner_ai_preselect_enabled", True):
            pool_size = min(
                len(tier3),
                max(cfg.max_tier5_stocks, cfg.max_tier5_stocks * 2),
            )
        else:
            pool_size = cfg.max_tier5_stocks
        pool_cfg = replace(cfg, max_tier5_stocks=pool_size)
        return self._engine.tier4_sector_filter(tier3, pool_cfg, progress_cb=progress_cb)

    def _ai_preselect_tier5_candidates(
        self,
        candidates: List[CandidateStock],
        cfg: ScanConfig,
        progress_cb: Callable[[int, str], None],
    ) -> List[CandidateStock]:
        target = min(cfg.max_tier5_stocks, len(candidates))
        if target <= 0 or len(candidates) <= target:
            return candidates
        if not getattr(self.config, "scanner_ai_preselect_enabled", True):
            return candidates[:target]

        progress_cb(68, f"AI preselecting {target} candidates from {len(candidates)} screened stocks…")
        try:
            from src.analyzer import GeminiAnalyzer
            from src.services.ai_preselector import ai_preselect_scanner_candidates

            analyzer = GeminiAnalyzer()
            markets = _configured_markets(cfg)
            selected = ai_preselect_scanner_candidates(
                candidates,
                target,
                analyzer,
                model=getattr(self.config, "scanner_model", "") or None,
                market_balancer=lambda items, limit: _select_market_balanced(
                    items,
                    limit,
                    markets,
                    lambda item: item.fund.tech.stock.market,
                ),
            )
            logger.info(
                "Scanner AI preselection: %d → %d, input_market_counts=%s, selected_market_counts=%s",
                len(candidates),
                len(selected),
                _candidate_market_counts(candidates),
                _candidate_market_counts(selected),
            )
            return selected
        except Exception as exc:
            logger.warning("Scanner AI preselection failed; using rule-ranked candidates: %s", exc)
            return _select_market_balanced(
                candidates,
                target,
                _configured_markets(cfg),
                lambda item: item.fund.tech.stock.market,
            )

    def _tier5_llm_analysis(
        self,
        candidates: List[CandidateStock],
        cfg: ScanConfig,
        progress_cb: Callable[[int, str], None],
    ) -> List[StockRecommendation]:
        """Run full LLM analysis for each candidate and return top-N ranked picks."""
        from src.core.pipeline import StockAnalysisPipeline
        from src.enums import ReportType

        horizon_ctx = (
            "Analyse this stock for a MEDIUM-TERM investment horizon of 1–6 months. "
            "Focus on: (1) historical financial performance trend over 3 years, "
            "(2) recent industry news and catalysts, "
            "(3) global industry macro status. "
            "Provide a clear entry strategy with buy zone, stop loss, and price targets."
        )
        if cfg.extra_context:
            horizon_ctx += " " + cfg.extra_context

        pipeline = StockAnalysisPipeline(
            config=self.config,
            max_workers=_TIER5_WORKERS,
        )

        raw_results: List[tuple] = []  # (candidate, analysis_result)
        total = len(candidates)

        def _analyze_one(candidate: CandidateStock) -> tuple:
            try:
                result = pipeline.process_single_stock(
                    code=candidate.ticker,
                    skip_analysis=False,
                    single_stock_notify=False,
                    report_type=ReportType.FULL,
                    analysis_query_id=uuid.uuid4().hex,
                    model_override=getattr(self.config, 'scanner_model', '') or None,
                )
                return candidate, result
            except Exception as exc:
                logger.warning("Tier 5 analysis failed for %s: %s", candidate.ticker, exc)
                return candidate, None

        with ThreadPoolExecutor(max_workers=_TIER5_WORKERS) as pool:
            futures = {pool.submit(_analyze_one, c): c for c in candidates}
            done = 0
            for future in as_completed(futures):
                done += 1
                pct = 70 + int((done / total) * 28)
                try:
                    candidate, result = future.result()
                    raw_results.append((candidate, result))
                except Exception:
                    pass
                progress_cb(pct, f"AI analysis {done}/{total}…")

        # Rank: primary = LLM sentiment_score, secondary = composite_score
        def _rank_key(item):
            candidate, result = item
            llm_score = (getattr(result, "sentiment_score", 0) or 0) if result else 0
            return (llm_score * 0.6 + candidate.composite_score * 0.4)

        raw_results = [r for r in raw_results if r[1] is not None and r[1].success]
        raw_results.sort(key=_rank_key, reverse=True)
        logger.info(
            "Tier 5 successful analyses: %d/%d, market_counts=%s",
            len(raw_results),
            total,
            _analysis_market_counts(raw_results),
        )

        top_n = min(cfg.top_n, len(raw_results))
        selected_results = _select_market_balanced(
            raw_results,
            top_n,
            _configured_markets(cfg),
            lambda item: item[0].fund.tech.stock.market,
        )
        logger.info(
            "Top Picks market-balanced selection: %d → %d, selected_market_counts=%s",
            len(raw_results),
            len(selected_results),
            _analysis_market_counts(selected_results),
        )
        picks = []
        for rank, (candidate, result) in enumerate(selected_results, start=1):
            picks.append(_build_recommendation(rank, candidate, result))
        return picks

    # ------------------------------------------------------------------
    # Persistence and notification
    # ------------------------------------------------------------------

    def _save_result(self, report: ScanReport) -> None:
        path = _RESULTS_DIR / f"{report.scan_id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("Scan result saved to %s", path)
        except Exception as exc:
            logger.warning("Failed to save scan result: %s", exc)

        # Also save Markdown report
        try:
            from src.notification import get_notification_service
            md_content = _format_scan_report(report)
            ns = get_notification_service()
            file_path = ns.save_report_to_file(md_content, f"scan_report_{report.timestamp[:10]}.md")
            logger.info("Markdown scan report saved to %s", file_path)
        except Exception as exc:
            logger.warning("Failed to save Markdown scan report: %s", exc)

    def _send_notifications(self, report: ScanReport) -> None:
        if not report.top_picks:
            return
        try:
            from src.notification import get_notification_service
            md_content = _format_scan_report(report)
            ns = get_notification_service()
            ns.send(md_content)
            logger.info("Scan report notification sent")
        except Exception as exc:
            logger.warning("Failed to send scan notification: %s", exc)

    def _default_scan_config(self) -> ScanConfig:
        return ScanConfig(
            top_n=getattr(self.config, "scanner_top_n", 10),
            markets=getattr(self.config, "scanner_markets", ["us", "cn"]),
            min_market_cap_m=getattr(self.config, "scanner_min_market_cap_m", 500.0),
            min_avg_volume=getattr(self.config, "scanner_min_avg_volume", 500_000),
            max_tier5_stocks=getattr(self.config, "scanner_max_tier5_stocks", 30),
            max_cn_stocks=getattr(self.config, "scanner_max_cn_stocks", 800),
            china_policy_weight=getattr(self.config, "scanner_china_policy_weight", 0.25),
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scanner_instance: Optional[MarketScanner] = None
_scanner_lock = threading.Lock()


def get_market_scanner() -> MarketScanner:
    global _scanner_instance
    with _scanner_lock:
        if _scanner_instance is None:
            _scanner_instance = MarketScanner()
    return _scanner_instance
