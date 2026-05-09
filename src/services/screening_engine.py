# -*- coding: utf-8 -*-
"""
Multi-tier US stock screening engine.

Tier 1 — metadata filter          (in-memory, no network)
Tier 2 — batch technical screen    (yfinance bulk download + StockTrendAnalyzer)
Tier 3 — fundamental screen        (yfinance .info per stock, thread pool)
Tier 4 — sector diversity filter   (ensures multi-sector representation)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from src.schemas.scanner import (
    CandidateStock,
    FundScore,
    ScanConfig,
    StockInfo,
    TechScore,
)
from src.stock_analyzer import StockTrendAnalyzer

logger = logging.getLogger(__name__)

_BATCH_SIZE = 200          # tickers per yfinance bulk download call
_FUNDAMENTAL_WORKERS = 10
_FUNDAMENTAL_TIMEOUT_S = 5.0


def _make_progress(cb: Optional[Callable[[int, str], None]], pct: int, msg: str) -> None:
    if cb:
        try:
            cb(pct, msg)
        except Exception:
            pass


class ScreeningEngine:
    """Implements the four pre-LLM screening tiers."""

    def __init__(self) -> None:
        self._analyzer = StockTrendAnalyzer()

    # ------------------------------------------------------------------
    # Tier 1: metadata filter
    # ------------------------------------------------------------------

    def tier1_filter(self, stocks: List[StockInfo], config: ScanConfig) -> List[StockInfo]:
        """In-memory filter using metadata already present in StockInfo."""
        passed = []
        for s in stocks:
            # Skip ETFs / funds / warrants (typically have long names or special chars)
            if any(kw in s.name.lower() for kw in (" etf", " fund", " trust", " warrant", " notes")):
                continue
            if s.market_cap_m > 0 and s.market_cap_m < config.min_market_cap_m:
                continue
            if s.avg_volume > 0 and s.avg_volume < config.min_avg_volume:
                continue
            if s.price > 0 and (s.price < config.min_price or s.price > config.max_price):
                continue
            passed.append(s)
        logger.info("Tier 1: %d → %d stocks", len(stocks), len(passed))
        return passed

    # ------------------------------------------------------------------
    # Tier 2: batch technical screen
    # ------------------------------------------------------------------

    def tier2_technical_screen(
        self,
        stocks: List[StockInfo],
        config: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> List[TechScore]:
        us_stocks = [s for s in stocks if s.market != "cn"]
        cn_stocks = [s for s in stocks if s.market == "cn"]
        results: List[TechScore] = []

        if us_stocks:
            results.extend(self._tier2_us_technical_screen(us_stocks, config, progress_cb))
        if cn_stocks:
            results.extend(self._tier2_cn_technical_screen(cn_stocks, config, progress_cb))

        results.sort(key=lambda x: x.signal_score, reverse=True)
        top = results[: config.max_tier2_candidates]
        logger.info("Tier 2: %d → %d candidates (top by signal_score)", len(results), len(top))
        return top

    def _tier2_us_technical_screen(
        self,
        stocks: List[StockInfo],
        config: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> List[TechScore]:
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed; cannot run Tier 2 screen")
            return []

        tickers = [s.ticker for s in stocks]
        ticker_map: Dict[str, StockInfo] = {s.ticker: s for s in stocks}
        results: List[TechScore] = []
        batches = [tickers[i:i + _BATCH_SIZE] for i in range(0, len(tickers), _BATCH_SIZE)]

        for batch_idx, batch in enumerate(batches):
            pct = 10 + int((batch_idx / len(batches)) * 30)
            _make_progress(progress_cb, pct, f"US technical screen batch {batch_idx + 1}/{len(batches)}")
            try:
                raw = yf.download(
                    tickers=" ".join(batch),
                    period="3mo",
                    interval="1d",
                    group_by="ticker",
                    threads=True,
                    progress=False,
                    auto_adjust=True,
                )
            except Exception as exc:
                logger.warning("yfinance batch download failed (batch %d): %s", batch_idx, exc)
                continue

            for ticker in batch:
                try:
                    df = self._extract_ticker_df(raw, ticker, len(batch))
                    if df is None or len(df) < 20:
                        continue
                    trend = self._analyzer.analyze(df, ticker)
                    stock_info = ticker_map[ticker]
                    results.append(TechScore(
                        stock=stock_info,
                        signal_score=trend.signal_score,
                        trend_status=trend.trend_status.value,
                        buy_signal=trend.buy_signal.value,
                        rsi_12=trend.rsi_12,
                        macd_status=trend.macd_status.value,
                        volume_status=trend.volume_status.value,
                    ))
                except Exception as exc:
                    logger.debug("Technical analysis failed for %s: %s", ticker, exc)

        return results

    def _tier2_cn_technical_screen(
        self,
        stocks: List[StockInfo],
        config: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> List[TechScore]:
        try:
            import akshare as ak
        except ImportError:
            logger.error("akshare not installed; cannot run China Tier 2 screen")
            return []

        results: List[TechScore] = []
        start_date = (date.today() - timedelta(days=140)).strftime("%Y%m%d")
        end_date = date.today().strftime("%Y%m%d")
        total = len(stocks)

        for idx, stock in enumerate(stocks, start=1):
            if idx == 1 or idx % 25 == 0:
                pct = 10 + int((idx / max(total, 1)) * 30)
                _make_progress(progress_cb, pct, f"China technical screen {idx}/{total}")
            try:
                raw = ak.stock_zh_a_hist(
                    symbol=stock.ticker,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq",
                )
                df = self._normalise_cn_history(raw)
                if df is None or len(df) < 20:
                    continue
                trend = self._analyzer.analyze(df, stock.ticker)
                last = df.iloc[-1]
                stock.price = float(last.get("close") or stock.price or 0)
                stock.avg_volume = float(df["volume"].tail(20).mean() or 0)
                results.append(TechScore(
                    stock=stock,
                    signal_score=trend.signal_score,
                    trend_status=trend.trend_status.value,
                    buy_signal=trend.buy_signal.value,
                    rsi_12=trend.rsi_12,
                    macd_status=trend.macd_status.value,
                    volume_status=trend.volume_status.value,
                ))
            except Exception as exc:
                logger.debug("China technical analysis failed for %s: %s", stock.ticker, exc)

        return results

    @staticmethod
    def _normalise_cn_history(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
        if raw is None or raw.empty:
            return None
        column_map = {}
        for col in raw.columns:
            name = str(col)
            lower = name.lower()
            if name == "日期" or lower in ("date", "datetime"):
                column_map[col] = "date"
            elif name == "开盘" or lower == "open":
                column_map[col] = "open"
            elif name == "最高" or lower == "high":
                column_map[col] = "high"
            elif name == "最低" or lower == "low":
                column_map[col] = "low"
            elif name == "收盘" or lower == "close":
                column_map[col] = "close"
            elif name == "成交量" or lower == "volume":
                column_map[col] = "volume"
        df = raw.rename(columns=column_map)
        required = {"date", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            return None
        df = df[list(required)].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close", "volume"])
        df = df[df["close"] > 0]
        return df

    @staticmethod
    def _extract_ticker_df(raw: pd.DataFrame, ticker: str, batch_size: int) -> Optional[pd.DataFrame]:
        """Extract and normalise a single ticker's OHLCV from a bulk yfinance download."""
        try:
            if batch_size == 1:
                df = raw.copy()
            else:
                # MultiIndex DataFrame
                if ticker not in raw.columns.get_level_values(0):
                    return None
                df = raw[ticker].copy()

            df = df.reset_index()
            # Normalise column names
            df.columns = [str(c).lower() for c in df.columns]
            rename_map = {}
            for col in df.columns:
                lc = col.lower()
                if lc in ("date", "datetime"):
                    rename_map[col] = "date"
                elif lc == "open":
                    rename_map[col] = "open"
                elif lc == "high":
                    rename_map[col] = "high"
                elif lc == "low":
                    rename_map[col] = "low"
                elif lc in ("close", "adj close"):
                    rename_map[col] = "close"
                elif lc == "volume":
                    rename_map[col] = "volume"
            df = df.rename(columns=rename_map)

            required = {"date", "open", "high", "low", "close", "volume"}
            if not required.issubset(df.columns):
                return None

            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            df = df.dropna(subset=["close", "volume"])
            df = df[df["close"] > 0]
            return df[list(required)]
        except Exception as exc:
            logger.debug("DataFrame extraction failed for %s: %s", ticker, exc)
            return None

    # ------------------------------------------------------------------
    # Tier 3: fundamental screen
    # ------------------------------------------------------------------

    def tier3_fundamental_screen(
        self,
        candidates: List[TechScore],
        config: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> List[FundScore]:
        _make_progress(progress_cb, 40, f"Fundamental screen: fetching {len(candidates)} stocks…")
        yf = None
        if any(tc.stock.market != "cn" for tc in candidates):
            try:
                import yfinance as yf_module
                yf = yf_module
            except ImportError:
                logger.error("yfinance not installed; US Tier 3 fundamentals will use neutral scores")

        fund_scores: List[FundScore] = []
        sector_groups: Dict[str, List[float]] = {}

        def _fetch_one(tc: TechScore) -> Tuple[TechScore, Dict]:
            if tc.stock.market == "cn" or yf is None:
                return tc, {}
            try:
                info = yf.Ticker(tc.ticker).info
                return tc, info
            except Exception:
                return tc, {}

        with ThreadPoolExecutor(max_workers=_FUNDAMENTAL_WORKERS) as pool:
            futures = {pool.submit(_fetch_one, tc): tc for tc in candidates}
            done = 0
            for future in as_completed(futures, timeout=len(candidates) * _FUNDAMENTAL_TIMEOUT_S):
                done += 1
                pct = 40 + int((done / len(candidates)) * 25)
                _make_progress(progress_cb, pct, f"Fundamentals {done}/{len(candidates)}")
                try:
                    tc, info = future.result()
                    fscore = self._build_fund_score(tc, info)
                    fund_scores.append(fscore)
                    sector_groups.setdefault(tc.stock.sector, []).append(fscore.fundamental_score)
                except Exception as exc:
                    logger.debug("Fundamental future failed: %s", exc)

        # Compute sector-relative score
        sector_avg: Dict[str, float] = {
            sec: (sum(v) / len(v) if v else 50.0)
            for sec, v in sector_groups.items()
        }
        for fs in fund_scores:
            sec_avg = sector_avg.get(fs.sector, 50.0)
            sector_rel = 50.0 + (fs.fundamental_score - sec_avg)
            sector_rel = max(0.0, min(100.0, sector_rel))
            composite = (
                0.50 * fs.tech.signal_score
                + 0.30 * fs.fundamental_score
                + 0.20 * sector_rel
            )
            fs.composite_score = round(composite, 2)

        fund_scores.sort(key=lambda x: x.composite_score, reverse=True)
        top = fund_scores[: config.max_tier3_candidates]
        logger.info("Tier 3: %d → %d candidates (top by composite_score)", len(fund_scores), len(top))
        return top

    @staticmethod
    def _build_fund_score(tc: TechScore, info: Dict) -> FundScore:
        pe = info.get("trailingPE") or info.get("forwardPE")
        fwd_pe = info.get("forwardPE")
        roe = info.get("returnOnEquity")            # decimal
        rev_growth = info.get("revenueGrowth")      # decimal
        margin = info.get("profitMargins")           # decimal
        d2e = info.get("debtToEquity")              # typically raw ratio

        score = 50.0  # start neutral
        # Valuation: low PE is better (relative)
        if pe and 0 < pe < 15:
            score += 10
        elif pe and pe < 25:
            score += 5
        elif pe and pe > 50:
            score -= 10

        # Growth
        if rev_growth and rev_growth > 0.20:
            score += 15
        elif rev_growth and rev_growth > 0.10:
            score += 8
        elif rev_growth and rev_growth < 0:
            score -= 10

        # Profitability
        if margin and margin > 0.20:
            score += 10
        elif margin and margin > 0.10:
            score += 5
        elif margin and margin < 0:
            score -= 10

        # ROE
        if roe and roe > 0.20:
            score += 10
        elif roe and roe > 0.10:
            score += 5

        # Leverage
        if d2e is not None:
            if d2e < 50:
                score += 5
            elif d2e > 200:
                score -= 5

        score = max(0.0, min(100.0, score))

        return FundScore(
            tech=tc,
            pe_ratio=pe,
            forward_pe=fwd_pe,
            roe=roe,
            revenue_growth=rev_growth,
            profit_margin=margin,
            debt_to_equity=d2e,
            fundamental_score=round(score, 2),
            composite_score=0.0,  # filled in after sector normalisation
        )

    # ------------------------------------------------------------------
    # Tier 4: sector diversity filter
    # ------------------------------------------------------------------

    def tier4_sector_filter(
        self,
        candidates: List[FundScore],
        config: ScanConfig,
        progress_cb: Optional[Callable[[int, str], None]] = None,
    ) -> List[CandidateStock]:
        _make_progress(progress_cb, 65, "Applying sector diversity filter…")
        max_candidates = config.max_tier5_stocks

        # Group by sector, sorted by composite_score
        sector_buckets: Dict[str, List[FundScore]] = {}
        for fs in candidates:
            sector_buckets.setdefault(fs.sector, []).append(fs)

        selected: List[FundScore] = []
        selected_tickers = set()

        # Round-robin: pick the best unseen stock from each sector in turn
        buckets_sorted = sorted(sector_buckets.values(), key=lambda b: -b[0].composite_score)
        pointers = [0] * len(buckets_sorted)
        rounds = 0
        while len(selected) < max_candidates and rounds < 100:
            rounds += 1
            added_this_round = False
            for i, bucket in enumerate(buckets_sorted):
                if len(selected) >= max_candidates:
                    break
                while pointers[i] < len(bucket):
                    fs = bucket[pointers[i]]
                    pointers[i] += 1
                    if fs.ticker not in selected_tickers:
                        selected.append(fs)
                        selected_tickers.add(fs.ticker)
                        added_this_round = True
                        break
            if not added_this_round:
                break

        # Assign sector ranks
        sector_rank_counter: Dict[str, int] = {}
        result: List[CandidateStock] = []
        for fs in selected:
            sector_rank_counter[fs.sector] = sector_rank_counter.get(fs.sector, 0) + 1
            result.append(CandidateStock(fund=fs, sector_rank=sector_rank_counter[fs.sector]))

        logger.info("Tier 4: %d → %d diverse candidates", len(candidates), len(result))
        return result
