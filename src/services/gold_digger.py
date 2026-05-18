# -*- coding: utf-8 -*-
"""
沙里淘金 (Gold Digger) Service

Scans garbage stocks (small-cap, beaten-down, low coverage, PE-cheap) across
US NYSE/NASDAQ and A-shares to find hidden gems positioned for macro/sector themes.

Pipeline:
  1. Gather A-share universe (Tushare/Baostock fallback)
  2. Gather US small-cap universe (from stock_universe cache)
  3. Apply garbage filters per market
  4. Score value, momentum-reversal, theme relevance, institutional signals
  5. LLM deep analysis on top candidates (max_tier5_per_market per market)
  6. Rank and produce GoldPick list
"""

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.config import get_config
from src.schemas.gold_digger import (
    DigConfig,
    DigMeta,
    DigReport,
    GarbageStockInfo,
    GoldCandidate,
    GoldPick,
    InvestmentTheme,
    ThemeMatch,
)
from src.services.screening_engine import _market_counts, _select_market_balanced

logger = logging.getLogger(__name__)

_TIER5_WORKERS = 2   # parallel LLM workers (conservative to avoid rate limits)
_US_PRICE_BATCH_SIZE = 100


def _resolve_results_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).parent / "scanner_cache" / "gold_results"


_RESULTS_DIR = _resolve_results_dir()


# ---------------------------------------------------------------------------
# A-share universe helpers
# ---------------------------------------------------------------------------

def _get_cn_stock_list() -> List[Dict[str, str]]:
    """Return basic A-share list: [{code, name, industry, market}]."""
    try:
        from data_provider.tushare_fetcher import TushareFetcher
        fetcher = TushareFetcher()
        df = fetcher.get_stock_list()
        if df is not None and not df.empty:
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "industry": str(row.get("industry", "")),
                    "market": str(row.get("market", "")),
                })
            return rows
    except Exception as exc:
        logger.warning("TushareFetcher failed for CN list: %s", exc)

    try:
        from data_provider.baostock_fetcher import BaostockFetcher
        fetcher = BaostockFetcher()
        df = fetcher.get_stock_list()
        if df is not None and not df.empty:
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "industry": "",
                    "market": "",
                })
            return rows
    except Exception as exc:
        logger.warning("BaostockFetcher failed for CN list: %s", exc)

    return []


# ---------------------------------------------------------------------------
# Data enrichment helpers
# ---------------------------------------------------------------------------

def _fetch_us_garbage_stocks(
    cfg: DigConfig,
    universe_stocks,
) -> List[GarbageStockInfo]:
    """Apply garbage filters to US small-cap stocks and enrich with yfinance."""
    import yfinance as yf

    # Pre-filter from universe metadata
    candidates = [
        s for s in universe_stocks
        if cfg.us_min_market_cap_m <= s.market_cap_m <= cfg.us_max_market_cap_m
    ]
    logger.info("US pre-filter: %d small/micro-cap candidates", len(candidates))

    if not candidates:
        return []

    ticker_map = {s.ticker: s for s in candidates}
    results: List[GarbageStockInfo] = []

    def _get_ticker_close(raw, ticker: str, batch_size: int):
        try:
            if batch_size == 1:
                closes = raw["Close"]
            else:
                closes = raw[ticker]["Close"] if ticker in raw.columns.get_level_values(0) else None
            if closes is None or closes.empty:
                return None
            closes = closes.dropna()
            if len(closes) < 10:
                return None
            return closes
        except Exception:
            return None

    tickers = [s.ticker for s in candidates]
    batches = [
        tickers[i:i + _US_PRICE_BATCH_SIZE]
        for i in range(0, len(tickers), _US_PRICE_BATCH_SIZE)
    ]
    for batch_idx, batch in enumerate(batches, start=1):
        try:
            raw = yf.download(
                tickers=" ".join(batch),
                period="6mo",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            logger.warning(
                "yfinance batch download failed for US garbage batch %d/%d: %s",
                batch_idx,
                len(batches),
                exc,
            )
            continue

        for ticker in batch:
            stock = ticker_map[ticker]
            try:
                closes = _get_ticker_close(raw, ticker, len(batch))
                if closes is None:
                    continue

                price_now = float(closes.iloc[-1])
                price_6m_ago = float(closes.iloc[0])
                price_1m_ago = float(closes.iloc[max(0, len(closes) - 21)])

                pct_6m = (price_now - price_6m_ago) / price_6m_ago * 100
                pct_1m = (price_now - price_1m_ago) / price_1m_ago * 100

                if pct_6m > -cfg.min_price_decline_6m_pct:
                    continue

                results.append(GarbageStockInfo(
                    ticker=ticker,
                    name=stock.name,
                    market="us",
                    sector=stock.sector,
                    industry=stock.industry,
                    market_cap_m=stock.market_cap_m,
                    current_price=price_now,
                    price_change_6m_pct=pct_6m,
                    price_change_1m_pct=pct_1m,
                    pe_ratio=None,
                    sector_median_pe=None,
                    pe_discount_pct=None,
                    analyst_count=0,
                    held_by_institutions_pct=None,
                    short_ratio=None,
                ))
            except Exception:
                continue

    logger.info("US garbage (price filter): %d stocks", len(results))
    return results


def _enrich_us_fundamental(stocks: List[GarbageStockInfo]) -> List[GarbageStockInfo]:
    """Enrich US stocks with PE, analyst count, and institutional data from yfinance.info."""
    import yfinance as yf

    sector_pe_samples: Dict[str, List[float]] = {}

    def _fetch_info(gs: GarbageStockInfo) -> GarbageStockInfo:
        try:
            info = yf.Ticker(gs.ticker).info or {}
            pe = info.get("trailingPE") or info.get("forwardPE")
            gs.pe_ratio = float(pe) if pe else None
            gs.analyst_count = int(info.get("numberOfAnalystOpinions") or 0)
            gs.held_by_institutions_pct = (
                float(info.get("heldPercentInstitutions") or 0) * 100
            )
            gs.short_ratio = info.get("shortRatio")
        except Exception:
            pass
        return gs

    with ThreadPoolExecutor(max_workers=8) as pool:
        enriched = list(pool.map(_fetch_info, stocks))

    # Compute sector median PE
    for gs in enriched:
        if gs.pe_ratio and 0 < gs.pe_ratio < 200:
            sector_pe_samples.setdefault(gs.sector, []).append(gs.pe_ratio)

    sector_medians: Dict[str, float] = {}
    for sector, pes in sector_pe_samples.items():
        if pes:
            sorted_pes = sorted(pes)
            mid = len(sorted_pes) // 2
            sector_medians[sector] = sorted_pes[mid]

    for gs in enriched:
        median = sector_medians.get(gs.sector)
        if median and gs.pe_ratio and 0 < gs.pe_ratio < 200:
            gs.sector_median_pe = median
            gs.pe_discount_pct = (gs.pe_ratio - median) / median * 100

    return enriched


def _fetch_cn_garbage_stocks(
    cfg: DigConfig,
    stock_list: List[Dict[str, str]],
) -> List[GarbageStockInfo]:
    """Fetch A-share garbage stocks with price history via configured data-source fallback."""
    try:
        from src.services.cn_daily_data import build_cn_screening_data_manager
        data_manager = build_cn_screening_data_manager()
    except Exception as exc:
        logger.warning("DataFetcherManager unavailable; skipping CN garbage stock scan: %s", exc)
        return []

    results: List[GarbageStockInfo] = []
    source_counts: Dict[str, int] = {}
    failures = 0

    def _get_cn_stock(row: Dict[str, str]) -> Optional[Tuple[GarbageStockInfo, str]]:
        code = row["code"]
        try:
            df, source = data_manager.get_daily_data(code, days=200)
            if df is None or df.empty or len(df) < 10:
                return None

            close_col = next(
                (c for c in df.columns if "收盘" in c or "close" in c.lower()), None
            )
            if close_col is None:
                return None

            closes = df[close_col].dropna().astype(float)
            if len(closes) < 10:
                return None

            price_now = float(closes.iloc[-1])
            price_6m_ago = float(closes.iloc[0])
            price_1m_ago = float(closes.iloc[max(0, len(closes) - 21)])

            pct_6m = (price_now - price_6m_ago) / price_6m_ago * 100
            pct_1m = (price_now - price_1m_ago) / price_1m_ago * 100

            if pct_6m > -cfg.min_price_decline_6m_pct:
                return None

            stock = GarbageStockInfo(
                ticker=code,
                name=row.get("name", code),
                market="cn",
                sector="",
                industry=row.get("industry", ""),
                market_cap_m=0.0,
                current_price=price_now,
                price_change_6m_pct=pct_6m,
                price_change_1m_pct=pct_1m,
                pe_ratio=None,
                sector_median_pe=None,
                pe_discount_pct=None,
                analyst_count=0,
                held_by_institutions_pct=None,
                short_ratio=None,
            )
            return stock, source
        except Exception:
            return None

    # Sample up to 500 CN stocks to keep runtime manageable
    sample = stock_list[:500]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(_get_cn_stock, sample):
            if result is None:
                failures += 1
                continue
            stock, source = result
            results.append(stock)
            source_counts[source] = source_counts.get(source, 0) + 1

    logger.info(
        "CN garbage (price filter): %d stocks, source_counts=%s, failures=%d",
        len(results),
        source_counts,
        failures,
    )
    return results


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_value(gs: GarbageStockInfo, cfg: DigConfig) -> float:
    """0-100: PE undervaluation vs sector peers."""
    score = 50.0
    if gs.pe_discount_pct is not None:
        discount = -gs.pe_discount_pct  # positive = cheaper than peers
        if discount >= 50:
            score = 95
        elif discount >= 30:
            score = 80
        elif discount >= cfg.min_pe_discount_pct:
            score = 60 + discount
        elif discount > 0:
            score = 50 + discount * 0.5
        else:
            score = max(0, 50 + discount)
    return min(100, max(0, score))


def _score_momentum_reversal(gs: GarbageStockInfo) -> float:
    """0-100: beaten-down magnitude + 1-month stabilization signal."""
    base = min(100, abs(gs.price_change_6m_pct))  # deeper decline = more room to revert
    reversal_bonus = 0.0
    if gs.price_change_1m_pct is not None:
        if gs.price_change_1m_pct > 5:
            reversal_bonus = 20   # recovering
        elif gs.price_change_1m_pct > 0:
            reversal_bonus = 10   # stabilizing
        elif gs.price_change_1m_pct > -5:
            reversal_bonus = 5    # still soft but slowing
    return min(100, base * 0.6 + reversal_bonus)


def _score_institutional(gs: GarbageStockInfo) -> float:
    """0-100: low institutional ownership (more upside) + analyst neglect bonus."""
    score = 50.0
    if gs.held_by_institutions_pct is not None:
        if gs.held_by_institutions_pct < 5:
            score += 30  # very overlooked
        elif gs.held_by_institutions_pct < 20:
            score += 15
    if gs.analyst_count == 0:
        score += 20
    elif gs.analyst_count <= 2:
        score += 10
    return min(100, score)


def _score_theme_matches(
    gs: GarbageStockInfo,
    themes: List[InvestmentTheme],
) -> List[ThemeMatch]:
    """Score relevance of stock to each investment theme via keyword matching."""
    matches: List[ThemeMatch] = []
    industry_lower = (gs.industry + " " + gs.sector).lower()

    for theme in themes:
        # Check sector match
        sector_hit = gs.sector in theme.relevant_sectors
        # Check keyword match
        kw_hits = [kw for kw in theme.keywords if kw.lower() in industry_lower]
        # Check market match
        market_hit = gs.market in theme.market_regions or "global" in theme.market_regions

        if not market_hit:
            continue

        if sector_hit and kw_hits:
            score = 80 + min(20, len(kw_hits) * 5)
            reason = f"Sector match ({gs.sector}) + keywords: {', '.join(kw_hits[:3])}"
        elif sector_hit:
            score = 55
            reason = f"Sector match ({gs.sector})"
        elif kw_hits:
            score = 40 + min(20, len(kw_hits) * 8)
            reason = f"Keyword match: {', '.join(kw_hits[:3])}"
        else:
            continue

        if theme.sentiment == "bearish":
            score = max(0, score - 30)

        if score >= 30:
            matches.append(ThemeMatch(
                theme_name=theme.name,
                relevance_score=round(score, 1),
                match_reason=reason,
            ))

    return sorted(matches, key=lambda m: m.relevance_score, reverse=True)


def _build_candidate(
    gs: GarbageStockInfo,
    themes: List[InvestmentTheme],
    cfg: DigConfig,
) -> GoldCandidate:
    value_score = _score_value(gs, cfg)
    momentum_score = _score_momentum_reversal(gs)
    institutional_score = _score_institutional(gs)
    theme_matches = _score_theme_matches(gs, themes)
    top_theme_score = theme_matches[0].relevance_score if theme_matches else 0.0

    composite = (
        value_score * 0.30
        + momentum_score * 0.25
        + top_theme_score * 0.30
        + institutional_score * 0.15
    )
    if gs.market == "cn" and cfg.china_policy_weight > 0:
        policy_weight = max(0.0, min(1.0, cfg.china_policy_weight))
        composite = composite * (1 - policy_weight) + top_theme_score * policy_weight

    return GoldCandidate(
        stock=gs,
        value_score=round(value_score, 1),
        momentum_reversal_score=round(momentum_score, 1),
        theme_matches=theme_matches,
        top_theme_score=round(top_theme_score, 1),
        institutional_score=round(institutional_score, 1),
        composite_score=round(composite, 1),
    )


def _apply_cn_theme_fallback(candidate: GoldCandidate, cfg: DigConfig) -> None:
    """
    Keep valid A-share garbage candidates eligible when detected themes omit
    cn/global market regions. Theme detection is news-driven and can skew US-only,
    but GoldDigger should still inspect A-share value/reversal candidates when
    the user requested the CN market.
    """
    if candidate.market != "cn" or candidate.top_theme_score >= 30:
        return

    fallback_score = 50.0
    if cfg.china_policy_weight > 0:
        fallback_score += min(20.0, cfg.china_policy_weight * 40.0)

    candidate.theme_matches = [ThemeMatch(
        theme_name="A-share policy/value rebound",
        relevance_score=round(fallback_score, 1),
        match_reason="CN market requested; fallback keeps beaten-down A-share candidates eligible when detected themes are not CN-tagged.",
    )]
    candidate.top_theme_score = round(fallback_score, 1)
    policy_weight = max(0.0, min(1.0, cfg.china_policy_weight))
    composite = (
        candidate.value_score * 0.30
        + candidate.momentum_reversal_score * 0.25
        + candidate.top_theme_score * 0.30
        + candidate.institutional_score * 0.15
    )
    if policy_weight > 0:
        composite = composite * (1 - policy_weight) + candidate.top_theme_score * policy_weight
    candidate.composite_score = round(composite, 1)


# ---------------------------------------------------------------------------
# LLM analysis helpers
# ---------------------------------------------------------------------------

_GOLD_ANALYSIS_PROMPT = """You are a contrarian value investor looking for hidden gems in beaten-down stocks.

Analyze this stock as a potential 沙里淘金 (hidden gem in garbage) opportunity:

Ticker: {ticker}
Company: {name}
Market: {market}
Sector: {sector} / {industry}
Current Price: {price}
6-Month Price Change: {change_6m:.1f}%
1-Month Price Change: {change_1m:.1f}%
Market Cap: ${market_cap:.0f}M
PE Ratio: {pe}
PE vs Sector: {pe_discount}
Analyst Coverage: {analysts} analysts
Institutional Ownership: {institution_pct}
Matched Investment Themes: {themes}

Your task: Determine if this is GOLD hidden in garbage.

Return ONLY valid JSON (no markdown):
{{
  "llm_confidence": <0-100 integer, confidence this is a genuine hidden gem>,
  "why_garbage": "<1-2 sentences: why the market overlooks or avoids this stock>",
  "why_gold": "<2-3 sentences: the specific hidden value or upcoming catalyst>",
  "analysis_summary": "<3-4 sentences comprehensive investment thesis>",
  "key_catalysts": "<2-3 specific upcoming catalysts that could unlock value>",
  "key_risks": "<2-3 main risks that could keep it depressed>",
  "entry_strategy": "<specific buy zone, stop-loss level, and 6-month price target>"
}}"""


def _parse_llm_gold_response(raw: str, default_name: str, model: str = "") -> Dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        model_tag = f" [model={model}]" if model else ""
        logger.warning(
            "Gold Digger JSON parse failed%s — raw (first 200 chars): %.200s",
            model_tag, raw.strip()
        )
        return {
            "llm_confidence": 40,
            "why_garbage": "Market uncertainty and limited coverage.",
            "why_gold": f"{default_name} shows potential value relative to beaten-down price.",
            "analysis_summary": "Insufficient data for full analysis.",
            "key_catalysts": "Sector recovery, earnings improvement.",
            "key_risks": "Continued price decline, liquidity risk.",
            "entry_strategy": "Small position, trailing stop 15%.",
            "_parse_fallback": True,
        }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class GoldDigger:
    """Orchestrates the 沙里淘金 scan: garbage filter → scoring → LLM analysis."""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._lock = threading.Lock()
        self._progress: Dict[str, Dict[str, Any]] = {}  # run_id -> {progress, message, status}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_dig(self, dig_config: DigConfig) -> str:
        """Launch a dig run in a background thread; return run_id."""
        run_id = uuid.uuid4().hex[:12]
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._progress[run_id] = {"progress": 0, "message": "Starting…", "status": "running"}
        thread = threading.Thread(
            target=self._run,
            args=(run_id, dig_config),
            daemon=True,
            name=f"gold-dig-{run_id}",
        )
        thread.start()
        return run_id

    def get_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._progress.get(run_id, {}))

    def get_result(self, run_id: str) -> Optional[DigReport]:
        path = _RESULTS_DIR / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._dict_to_report(data)
        except Exception as exc:
            logger.warning("Failed to load gold result %s: %s", run_id, exc)
            return None

    def get_latest_result(self) -> Optional[DigReport]:
        files = sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("status") == "completed":
                    return self._dict_to_report(data)
            except Exception:
                continue
        return None

    def list_runs(self) -> List[DigMeta]:
        metas: List[DigMeta] = []
        for f in sorted(_RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                picks = data.get("gold_picks", [])
                top = picks[0] if picks else {}
                metas.append(DigMeta(
                    run_id=data["run_id"],
                    timestamp=data["timestamp"],
                    top_ticker=top.get("ticker", "—"),
                    top_name=top.get("name", "—"),
                    theme_count=len(data.get("detected_themes", [])),
                    gold_picks=len(picks),
                    duration_s=data.get("duration_s", 0),
                    status=data.get("status", "unknown"),
                ))
            except Exception:
                continue
        return metas

    # ------------------------------------------------------------------
    # Background execution
    # ------------------------------------------------------------------

    def _update(self, run_id: str, progress: int, message: str, status: str = "running") -> None:
        with self._lock:
            self._progress[run_id] = {"progress": progress, "message": message, "status": status}

    def _run(self, run_id: str, cfg: DigConfig) -> None:
        start = time.time()
        report: Optional[DigReport] = None
        try:
            timestamp = _date.today().isoformat()
            report = DigReport(
                run_id=run_id,
                timestamp=timestamp,
                config=cfg.to_dict(),
                detected_themes=[],
                us_universe_size=0,
                cn_universe_size=0,
                garbage_filtered=0,
                theme_matched=0,
                deep_analyzed=0,
                gold_picks=[],
                duration_s=0.0,
                status="running",
            )
            self._do_run(run_id, cfg, report, start)
        except Exception as exc:
            logger.exception("Gold dig run %s failed: %s", run_id, exc)
            if report is None:
                report = DigReport(
                    run_id=run_id,
                    timestamp=_date.today().isoformat(),
                    config=cfg.to_dict(),
                    detected_themes=[],
                    us_universe_size=0,
                    cn_universe_size=0,
                    garbage_filtered=0,
                    theme_matched=0,
                    deep_analyzed=0,
                    gold_picks=[],
                    duration_s=0.0,
                    status="error",
                )
            report.status = "error"
            report.error = str(exc)
            report.duration_s = time.time() - start
            self._save_result(report)
            self._update(run_id, 100, f"Failed: {exc}", "error")

    def _do_run(self, run_id: str, cfg: DigConfig, report: DigReport, start: float) -> None:
        # Step 1 — detect themes (with live news via SearchService)
        self._update(run_id, 3, "Fetching macro/finance/political news…")
        from src.analyzer import GeminiAnalyzer
        from src.search_service import SearchService
        from src.services.theme_detector import ThemeDetector
        analyzer = GeminiAnalyzer()
        try:
            search_service = SearchService(
                bocha_keys=self.config.bocha_api_keys,
                tavily_keys=self.config.tavily_api_keys,
                anspire_keys=getattr(self.config, "anspire_api_keys", []),
                brave_keys=self.config.brave_api_keys,
                serpapi_keys=self.config.serpapi_keys,
                minimax_keys=getattr(self.config, "minimax_api_keys", []),
                searxng_base_urls=self.config.searxng_base_urls,
                searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                news_max_age_days=7,
                news_strategy_profile="medium",
            )
        except Exception as exc:
            logger.warning("SearchService init failed for theme detection: %s", exc)
            search_service = None
        self._update(run_id, 5, "Synthesizing investment themes from news…")
        detector = ThemeDetector(analyzer, search_service=search_service)
        themes = detector.detect_themes(count=cfg.theme_count, date_str=_date.today().isoformat())
        report.detected_themes = themes
        logger.info("Detected %d themes", len(themes))

        all_garbage: List[GarbageStockInfo] = []

        # Step 2 — US universe
        if "us" in cfg.markets:
            self._update(run_id, 10, "Loading US small-cap universe…")
            from src.services.stock_universe import USStockUniverse
            universe = USStockUniverse()
            us_stocks = universe.get_all()
            report.us_universe_size = len(us_stocks)
            logger.info("US universe: %d stocks", len(us_stocks))

            self._update(run_id, 20, "Filtering US garbage stocks (6-month price history)…")
            us_garbage = _fetch_us_garbage_stocks(cfg, us_stocks)

            self._update(run_id, 35, f"Enriching {len(us_garbage)} US candidates with fundamentals…")
            us_garbage = _enrich_us_fundamental(us_garbage)
            # Apply PE discount filter
            us_garbage = [
                g for g in us_garbage
                if g.pe_discount_pct is None or g.pe_discount_pct <= -cfg.min_pe_discount_pct
            ]
            logger.info("US garbage after PE filter: %d", len(us_garbage))
            all_garbage.extend(us_garbage)

        # Step 3 — CN universe
        if "cn" in cfg.markets:
            self._update(run_id, 40, "Loading A-share universe…")
            cn_list = _get_cn_stock_list()
            report.cn_universe_size = len(cn_list)
            logger.info("CN universe: %d stocks", len(cn_list))

            self._update(run_id, 45, "Filtering A-share garbage stocks…")
            cn_garbage = _fetch_cn_garbage_stocks(cfg, cn_list)
            all_garbage.extend(cn_garbage)

        report.garbage_filtered = len(all_garbage)
        logger.info("Total garbage candidates: %d", len(all_garbage))
        self._update(run_id, 55, f"Scoring {len(all_garbage)} candidates against {len(themes)} themes…")

        # Step 4 — Score all candidates
        candidates = [_build_candidate(gs, themes, cfg) for gs in all_garbage]
        if "cn" in cfg.markets:
            cn_candidates = [c for c in candidates if c.market == "cn"]
            cn_themed = [c for c in cn_candidates if c.top_theme_score >= 30]
            cn_needed = max(
                0,
                min(cfg.max_tier5_per_market, len(cn_candidates)) - len(cn_themed),
            )
            if cn_needed > 0:
                cn_fallback_pool = sorted(
                    [c for c in cn_candidates if c.top_theme_score < 30],
                    key=lambda c: (
                        c.value_score * 0.35
                        + c.momentum_reversal_score * 0.35
                        + c.institutional_score * 0.30
                    ),
                    reverse=True,
                )
                for candidate in cn_fallback_pool[:cn_needed]:
                    _apply_cn_theme_fallback(candidate, cfg)
                if cn_fallback_pool:
                    logger.info(
                        "CN theme fallback kept %d A-share candidates for GoldDigger deep analysis",
                        min(cn_needed, len(cn_fallback_pool)),
                    )

        # Keep only candidates with at least one theme match
        themed = [c for c in candidates if c.top_theme_score >= 30]
        report.theme_matched = len(themed)
        logger.info(
            "Theme-matched candidates: %d, market_counts=%s",
            len(themed),
            _market_counts(themed, lambda c: c.market),
        )

        # Sort and take top per market for AI preselection, then deep LLM analysis
        themed.sort(key=lambda c: c.composite_score, reverse=True)
        tier5_us = self._ai_preselect_market_candidates(
            [c for c in themed if c.market == "us"],
            cfg.max_tier5_per_market,
            analyzer,
        )
        tier5_cn = self._ai_preselect_market_candidates(
            [c for c in themed if c.market == "cn"],
            cfg.max_tier5_per_market,
            analyzer,
        )
        tier5 = tier5_us + tier5_cn
        report.deep_analyzed = len(tier5)
        logger.info("Tier 5 candidates for LLM: %d", len(tier5))

        self._update(run_id, 60, f"AI deep analysis of {len(tier5)} candidates…")

        # Step 5 — LLM analysis
        llm_results = self._llm_analyze(tier5, analyzer, run_id, len(tier5))

        # Step 6 — Rank and pick top_n
        llm_results.sort(key=lambda x: x[1].get("llm_confidence", 0) * 0.6 + x[0].composite_score * 0.4, reverse=True)
        selected_results = _select_market_balanced(
            llm_results,
            cfg.top_n,
            cfg.markets,
            lambda item: item[0].market,
        )
        logger.info(
            "GoldDigger Top Picks market-balanced selection: %d → %d, selected_market_counts=%s",
            len(llm_results),
            len(selected_results),
            _market_counts(selected_results, lambda item: item[0].market),
        )
        picks: List[GoldPick] = []
        for rank, (candidate, llm_data) in enumerate(selected_results, start=1):
            gs = candidate.stock
            picks.append(GoldPick(
                rank=rank,
                ticker=gs.ticker,
                name=gs.name,
                market="US" if gs.market == "us" else "A-share",
                sector=gs.sector,
                industry=gs.industry,
                current_price=gs.current_price,
                price_change_6m_pct=gs.price_change_6m_pct,
                pe_ratio=gs.pe_ratio,
                pe_discount_pct=gs.pe_discount_pct,
                composite_score=candidate.composite_score,
                llm_confidence=int(llm_data.get("llm_confidence", 50)),
                matched_themes=[m.theme_name for m in candidate.theme_matches[:3]],
                why_garbage=llm_data.get("why_garbage", ""),
                why_gold=llm_data.get("why_gold", ""),
                analysis_summary=llm_data.get("analysis_summary", ""),
                key_catalysts=llm_data.get("key_catalysts", ""),
                key_risks=llm_data.get("key_risks", ""),
                entry_strategy=llm_data.get("entry_strategy", ""),
            ))

        report.gold_picks = picks
        report.duration_s = time.time() - start
        report.status = "completed"
        self._save_result(report)
        self._update(run_id, 100, f"Completed — {len(picks)} gold picks found", "completed")
        logger.info("Gold dig %s done in %.1fs, %d picks", run_id, report.duration_s, len(picks))

        # Send notification
        try:
            self._send_notification(report)
        except Exception as exc:
            logger.warning("Notification failed: %s", exc)

    def _ai_preselect_market_candidates(
        self,
        candidates: List[GoldCandidate],
        target_count: int,
        analyzer,
    ) -> List[GoldCandidate]:
        target = min(target_count, len(candidates))
        if target <= 0:
            return []
        if len(candidates) <= target:
            return candidates

        pool_size = min(len(candidates), max(target, target * 2))
        pool = candidates[:pool_size]
        if not getattr(self.config, "gold_digger_ai_preselect_enabled", True):
            return pool[:target]

        try:
            from src.services.ai_preselector import ai_preselect_gold_candidates

            selected = ai_preselect_gold_candidates(
                pool,
                target,
                analyzer,
                model=getattr(self.config, "gold_digger_model", "") or None,
            )
            market = pool[0].market if pool else "unknown"
            logger.info(
                "GoldDigger AI preselection [%s]: %d → %d",
                market,
                len(pool),
                len(selected),
            )
            return selected
        except Exception as exc:
            logger.warning("GoldDigger AI preselection failed; using rule-ranked candidates: %s", exc)
            return pool[:target]

    def _llm_analyze(
        self,
        candidates: List[GoldCandidate],
        analyzer,
        run_id: str,
        total: int,
    ) -> List[tuple]:
        results = []
        done = 0

        # Resolve per-task model override once (captured by closure)
        _gold_model = getattr(self.config, 'gold_digger_model', '') or None

        def _analyze_one(candidate: GoldCandidate) -> tuple:
            gs = candidate.stock
            pe_str = f"{gs.pe_ratio:.1f}" if gs.pe_ratio else "N/A"
            pe_disc_str = (
                f"{gs.pe_discount_pct:.1f}% vs sector median"
                if gs.pe_discount_pct is not None
                else "N/A"
            )
            inst_str = (
                f"{gs.held_by_institutions_pct:.1f}%"
                if gs.held_by_institutions_pct is not None
                else "unknown"
            )
            themes_str = "; ".join(
                f"{m.theme_name} ({m.relevance_score:.0f}%)"
                for m in candidate.theme_matches[:3]
            ) or "None detected"

            prompt = _GOLD_ANALYSIS_PROMPT.format(
                ticker=gs.ticker,
                name=gs.name,
                market="US (NYSE/NASDAQ)" if gs.market == "us" else "A-share (沪深)",
                sector=gs.sector or "Unknown",
                industry=gs.industry or "Unknown",
                price=f"{gs.current_price:.2f}",
                change_6m=gs.price_change_6m_pct,
                change_1m=gs.price_change_1m_pct,
                market_cap=gs.market_cap_m,
                pe=pe_str,
                pe_discount=pe_disc_str,
                analysts=gs.analyst_count,
                institution_pct=inst_str,
                themes=themes_str,
            )
            try:
                raw = analyzer.generate_text(prompt, max_tokens=1000, temperature=0.5, model=_gold_model)
                if raw:
                    return candidate, _parse_llm_gold_response(raw, gs.name, model=_gold_model or "default")
            except Exception as exc:
                logger.warning("LLM analysis failed for %s [model=%s]: %s", gs.ticker, _gold_model or "default", exc)
            return candidate, _parse_llm_gold_response("", gs.name, model=_gold_model or "default")

        with ThreadPoolExecutor(max_workers=_TIER5_WORKERS) as pool:
            futures = {pool.submit(_analyze_one, c): c for c in candidates}
            for future in as_completed(futures):
                done += 1
                pct = 60 + int((done / total) * 35)
                self._update(run_id, pct, f"AI analysis {done}/{total}…")
                try:
                    results.append(future.result())
                except Exception:
                    pass

        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_result(self, report: DigReport) -> None:
        path = _RESULTS_DIR / f"{report.run_id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Failed to save gold result: %s", exc)

    def _send_notification(self, report: DigReport) -> None:
        if not report.gold_picks:
            return
        lines = [
            f"沙里淘金扫描完成 — {report.timestamp}",
            f"发现 {len(report.gold_picks)} 只潜力股 | 检测到 {len(report.detected_themes)} 个主题",
            "",
        ]
        for pick in report.gold_picks[:5]:
            market_label = "US" if pick.market == "US" else "A股"
            lines.append(
                f"#{pick.rank} {pick.ticker} ({market_label}) — 评分{pick.composite_score:.0f} "
                f"置信度{pick.llm_confidence}%"
            )
            lines.append(f"  {pick.why_gold[:80]}…")
        content = "\n".join(lines)

        try:
            from src.notification import get_notification_service
            ns = get_notification_service()
            ns.send(content=content, email_send_to_all=True)
        except Exception as exc:
            logger.warning("Could not send notification: %s", exc)

    @staticmethod
    def _dict_to_report(data: Dict) -> DigReport:
        themes = [InvestmentTheme(**t) for t in data.get("detected_themes", [])]
        picks_raw = data.get("gold_picks", [])
        picks = []
        for p in picks_raw:
            picks.append(GoldPick(
                rank=p.get("rank", 0),
                ticker=p.get("ticker", ""),
                name=p.get("name", ""),
                market=p.get("market", ""),
                sector=p.get("sector", ""),
                industry=p.get("industry", ""),
                current_price=float(p.get("current_price", 0)),
                price_change_6m_pct=float(p.get("price_change_6m_pct", 0)),
                pe_ratio=p.get("pe_ratio"),
                pe_discount_pct=p.get("pe_discount_pct"),
                composite_score=float(p.get("composite_score", 0)),
                llm_confidence=int(p.get("llm_confidence", 0)),
                matched_themes=p.get("matched_themes", []),
                why_garbage=p.get("why_garbage", ""),
                why_gold=p.get("why_gold", ""),
                analysis_summary=p.get("analysis_summary", ""),
                key_catalysts=p.get("key_catalysts", ""),
                key_risks=p.get("key_risks", ""),
                entry_strategy=p.get("entry_strategy", ""),
            ))
        funnel = data.get("funnel", {})
        return DigReport(
            run_id=data["run_id"],
            timestamp=data["timestamp"],
            config=data.get("config", {}),
            detected_themes=themes,
            us_universe_size=funnel.get("us_universe", 0),
            cn_universe_size=funnel.get("cn_universe", 0),
            garbage_filtered=funnel.get("garbage_filtered", 0),
            theme_matched=funnel.get("theme_matched", 0),
            deep_analyzed=funnel.get("deep_analyzed", 0),
            gold_picks=picks,
            duration_s=float(data.get("duration_s", 0)),
            status=data.get("status", "unknown"),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_gold_digger_instance: Optional[GoldDigger] = None
_gold_digger_lock = threading.Lock()


def get_gold_digger() -> GoldDigger:
    global _gold_digger_instance
    if _gold_digger_instance is None:
        with _gold_digger_lock:
            if _gold_digger_instance is None:
                _gold_digger_instance = GoldDigger()
    return _gold_digger_instance
