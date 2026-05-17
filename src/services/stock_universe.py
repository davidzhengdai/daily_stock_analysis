# -*- coding: utf-8 -*-
"""
US Stock Universe Manager

Fetches and caches all NYSE/NASDAQ listed stocks organised by GICS sector.

Primary source:  NASDAQ screener API (no API key required)
Fallback:        S&P 500 + NASDAQ-100 + S&P 400 + S&P 600 via Wikipedia
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from src.schemas.scanner import StockInfo

logger = logging.getLogger(__name__)

# Use DATABASE_PATH to derive a sibling cache dir that survives Docker volume mounts.
def _resolve_cache_dir() -> Path:
    db_path = os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).parent / "scanner_cache"

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nasdaq.com/",
}

_WIKIPEDIA_INDEX_URLS: List[str] = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://en.wikipedia.org/wiki/NASDAQ-100",
    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
]

_CACHE_DIR = _resolve_cache_dir()
_UNIVERSE_CACHE_FILE = _CACHE_DIR / "stock_universe.json"

GICS_SECTORS = [
    "Technology",
    "Healthcare",
    "Financials",
    "Energy",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Materials",
    "Communication Services",
    "Utilities",
    "Real Estate",
]

_A_SHARE_ETF_PREFIXES = ("15", "16", "18", "51", "52", "56", "58")

_SECTOR_ALIASES: Dict[str, str] = {
    "health care": "Healthcare",
    "financial services": "Financials",
    "basic materials": "Materials",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "communication services": "Communication Services",
    "real estate": "Real Estate",
    "industrials": "Industrials",
    "utilities": "Utilities",
    "technology": "Technology",
    "energy": "Energy",
}


def _normalize_sector(raw: str) -> str:
    if not raw:
        return "Other"
    key = raw.strip().lower()
    return _SECTOR_ALIASES.get(key, raw.strip())


def _parse_market_cap(raw: str) -> float:
    """Convert NASDAQ-style market cap string ('$1.2B', '345M') to USD millions."""
    if not raw or raw in ("-", "N/A", ""):
        return 0.0
    s = raw.replace("$", "").replace(",", "").strip()
    try:
        if s.endswith("T"):
            return float(s[:-1]) * 1_000_000
        if s.endswith("B"):
            return float(s[:-1]) * 1_000
        if s.endswith("M"):
            return float(s[:-1])
        return float(s) / 1_000_000
    except ValueError:
        return 0.0


def _parse_volume(raw) -> float:
    try:
        return float(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_price(raw: str) -> float:
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _is_valid_a_share_universe_row(code: str, name: str) -> bool:
    """Keep common A-share stocks and skip indices/funds before applying limits."""
    code = code.strip()
    name = (name or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        return False
    if code.startswith(_A_SHARE_ETF_PREFIXES):
        return False
    if any(token in name for token in ("指数", "基金", "ETF", "etf")):
        return False
    return code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689"))


class USStockUniverse:
    """Manages the full US stock universe with local caching."""

    def __init__(self, cache_max_age_hours: int = 24):
        self._cache_max_age_hours = cache_max_age_hours
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_all(self) -> List[StockInfo]:
        """Return cached universe, refreshing if stale."""
        cached = self._load_cache()
        if cached is not None:
            logger.info("Loaded %d stocks from universe cache", len(cached))
            return cached
        return self.refresh()

    def refresh(self) -> List[StockInfo]:
        """Force-fetch the universe from network and update cache."""
        stocks = self._fetch_universe()
        if stocks:
            self._save_cache(stocks)
            logger.info("Universe refreshed: %d stocks", len(stocks))
        return stocks

    def get_by_sector(self, sector: str) -> List[StockInfo]:
        return [s for s in self.get_all() if s.sector == sector]

    def get_sectors(self) -> List[str]:
        return GICS_SECTORS

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self) -> Optional[List[StockInfo]]:
        if not _UNIVERSE_CACHE_FILE.exists():
            return None
        age_hours = (time.time() - _UNIVERSE_CACHE_FILE.stat().st_mtime) / 3600
        if age_hours > self._cache_max_age_hours:
            logger.info("Universe cache expired (%.1fh old)", age_hours)
            return None
        try:
            with open(_UNIVERSE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [StockInfo(**row) for row in data]
        except Exception as exc:
            logger.warning("Failed to load universe cache: %s", exc)
            return None

    def _save_cache(self, stocks: List[StockInfo]) -> None:
        try:
            with open(_UNIVERSE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump([s.__dict__ for s in stocks], f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Failed to save universe cache: %s", exc)

    # ------------------------------------------------------------------
    # Fetch strategies
    # ------------------------------------------------------------------

    def _fetch_universe(self) -> List[StockInfo]:
        stocks = self._fetch_from_nasdaq_screener()
        if len(stocks) > 100:
            return stocks
        logger.warning("NASDAQ screener returned %d stocks, falling back to Wikipedia", len(stocks))
        return self._fetch_from_wikipedia()

    def _fetch_from_nasdaq_screener(self) -> List[StockInfo]:
        """Fetch all stocks from NASDAQ's public screener API."""
        all_rows = []
        try:
            for exchange in ("NYSE", "NASDAQ"):
                resp = requests.get(
                    NASDAQ_SCREENER_URL,
                    params={"download": "true", "exchange": exchange},
                    headers=NASDAQ_HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                payload = resp.json()
                data = payload.get("data") or {}
                rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
                all_rows.extend(rows)
            if not all_rows:
                logger.warning("NASDAQ screener returned empty rows")
                return []
            return self._parse_nasdaq_rows(all_rows)
        except Exception as exc:
            logger.warning("NASDAQ screener fetch failed: %s", exc)
            return []

    def _parse_nasdaq_rows(self, rows: list) -> List[StockInfo]:
        stocks: List[StockInfo] = []
        seen = set()
        for row in rows:
            try:
                ticker = (row.get("symbol") or "").strip()
                if not ticker or "/" in ticker or "^" in ticker:
                    continue
                if ticker in seen:
                    continue
                seen.add(ticker)
                country = (row.get("country") or "").strip().lower()
                if country and country != "united states":
                    continue
                market_cap_m = _parse_market_cap(row.get("marketCap", ""))
                avg_vol = _parse_volume(row.get("volume", 0))
                price = _parse_price(row.get("lastsale", "0"))
                sector = _normalize_sector(row.get("sector", ""))
                industry = (row.get("industry") or "").strip()
                name = (row.get("name") or ticker).strip()
                exchange = (row.get("exchange") or "").strip()
                stocks.append(StockInfo(
                    ticker=ticker,
                    name=name,
                    sector=sector,
                    industry=industry,
                    market_cap_m=market_cap_m,
                    avg_volume=avg_vol,
                    price=price,
                    exchange=exchange,
                    market="us",
                ))
            except Exception:
                continue
        return stocks

    def _fetch_from_wikipedia(self) -> List[StockInfo]:
        """Fallback: scrape S&P 500 / NASDAQ-100 / S&P 400 / S&P 600 from Wikipedia."""
        try:
            import pandas as pd
        except ImportError:
            logger.warning("pandas not available for Wikipedia fallback")
            return []

        seen: Dict[str, StockInfo] = {}
        for url in _WIKIPEDIA_INDEX_URLS:
            try:
                tables = pd.read_html(url)
                for table in tables:
                    cols = [str(c).lower() for c in table.columns]
                    # Look for a column that looks like a ticker
                    ticker_col = next(
                        (c for c in table.columns if str(c).lower() in
                         ("symbol", "ticker", "ticker symbol", "stock")),
                        None,
                    )
                    sector_col = next(
                        (c for c in table.columns if "sector" in str(c).lower()), None
                    )
                    name_col = next(
                        (c for c in table.columns if str(c).lower() in
                         ("security", "company", "name", "company name")),
                        None,
                    )
                    if ticker_col is None:
                        continue
                    for _, row in table.iterrows():
                        ticker = str(row[ticker_col]).strip()
                        if not ticker or ticker == "nan" or " " in ticker:
                            continue
                        sector = _normalize_sector(str(row[sector_col]).strip()) if sector_col else "Other"
                        name = str(row[name_col]).strip() if name_col else ticker
                        if ticker not in seen:
                            seen[ticker] = StockInfo(
                                ticker=ticker,
                                name=name,
                                sector=sector,
                                industry="",
                                market_cap_m=0.0,
                                avg_volume=0.0,
                                price=0.0,
                                exchange="",
                                market="us",
                            )
            except Exception as exc:
                logger.warning("Wikipedia fetch failed for %s: %s", url, exc)
        return list(seen.values())


class CNStockUniverse:
    """Fetches a basic A-share universe from configured local data providers."""

    def get_all(self, limit: int = 800) -> List[StockInfo]:
        rows = self._fetch_rows()
        stocks: List[StockInfo] = []
        for row in rows:
            code = str(row.get("code", "")).strip()
            name = str(row.get("name") or code).strip()
            if not _is_valid_a_share_universe_row(code, name):
                continue
            stocks.append(StockInfo(
                ticker=code,
                name=name,
                sector=str(row.get("industry") or "A股").strip() or "A股",
                industry=str(row.get("industry") or "").strip(),
                market_cap_m=0.0,
                avg_volume=0.0,
                price=0.0,
                exchange=str(row.get("market") or "").strip(),
                market="cn",
            ))
            if len(stocks) >= limit:
                break
        return stocks

    def _fetch_rows(self) -> List[Dict[str, str]]:
        try:
            from data_provider.tushare_fetcher import TushareFetcher
            fetcher = TushareFetcher()
            df = fetcher.get_stock_list()
            if df is not None and not df.empty:
                return [
                    {
                        "code": str(row.get("code", "")),
                        "name": str(row.get("name", "")),
                        "industry": str(row.get("industry", "")),
                        "market": str(row.get("market", "")),
                    }
                    for _, row in df.iterrows()
                ]
        except Exception as exc:
            logger.warning("TushareFetcher failed for CN universe: %s", exc)

        try:
            from data_provider.baostock_fetcher import BaostockFetcher
            fetcher = BaostockFetcher()
            df = fetcher.get_stock_list()
            if df is not None and not df.empty:
                return [
                    {
                        "code": str(row.get("code", "")),
                        "name": str(row.get("name", "")),
                        "industry": "",
                        "market": "",
                    }
                    for _, row in df.iterrows()
                ]
        except Exception as exc:
            logger.warning("BaostockFetcher failed for CN universe: %s", exc)

        return []
