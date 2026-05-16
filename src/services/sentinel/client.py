# -*- coding: utf-8 -*-
"""
SentinelCacheClient — read-only facade over NewsStore for use by SearchService.

Converts NewsStore rows into SearchResponse / SearchResult objects so that
sentinel-cached news can be injected as a search dimension without any online
API call.
"""
import json
import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)


class SentinelCacheClient:
    """Read-only client for the Sentinel news cache.

    Lazy-initialises the underlying NewsStore so that importing this module
    in environments where SENTINEL_ENABLED=false does not open a DB connection.
    """

    def __init__(self, config=None, store=None) -> None:
        self._config = config
        self._store = store

    # ── lazy init ─────────────────────────────────────────────────────────────

    def _get_config(self):
        if self._config is None:
            from .config import SentinelConfig
            self._config = SentinelConfig.from_env()
        return self._config

    def _get_store(self):
        if self._store is None:
            from .store import NewsStore
            cfg = self._get_config()
            self._store = NewsStore(db_path=cfg.db_path)
        return self._store

    # ── public API ────────────────────────────────────────────────────────────

    def is_available(self, min_items: int = 10) -> bool:
        """Return True when sentinel is enabled, cache has data, and data is fresh.

        Freshness is checked against the most recent successful spider run recorded
        in spider_runs. If the sentinel Docker has stopped scraping, this will
        return False after max_cache_age_hours, causing SearchService to fall back
        to online search providers.
        """
        try:
            cfg = self._get_config()
            if not cfg.enabled:
                return False
            store = self._get_store()
            if store.count() < min_items:
                return False
            # Freshness check: bail out if no successful spider run within the window
            if cfg.max_cache_age_hours > 0:
                from datetime import datetime, timezone, timedelta
                latest = store.get_latest_spider_run_time()
                if latest is None:
                    return False  # no successful run ever recorded
                age = datetime.now(timezone.utc) - latest
                if age > timedelta(hours=cfg.max_cache_age_hours):
                    logger.info(
                        "[Sentinel] cache is stale (last run %.1fh ago, threshold %dh) — falling back",
                        age.total_seconds() / 3600,
                        cfg.max_cache_age_hours,
                    )
                    return False
            return True
        except Exception as exc:
            logger.debug("SentinelCacheClient.is_available: %s", exc)
            return False

    def search_for_stock(
        self,
        code: str,
        name: str,
        max_results: int = 10,
    ):
        """Search the cache for news relevant to *code* / *name*.

        Returns a ``SearchResponse`` (imported from ``src.search_service``).
        Falls back to recent high-priority items filtered by affected_stocks
        when FTS yields nothing.
        """
        from src.search_service import SearchResult, SearchResponse

        t0 = time.monotonic()
        try:
            store = self._get_store()

            # Primary: FTS search
            query = f"{name} {code}"
            rows = store.search_fts(query, limit=max_results)

            # Fallback: recent P3+ items mentioning the stock code
            if not rows:
                candidates = store.get_recent(hours=72, priority_min=3, limit=200)
                rows = [
                    r for r in candidates
                    if code in (r["affected_stocks"] or "")
                ][:max_results]

            results: List[SearchResult] = []
            for row in rows:
                title = row["title"] or ""
                content = row["content"] or ""
                snippet = content[:300] if content else title
                results.append(
                    SearchResult(
                        title=title,
                        snippet=snippet,
                        url=row["url"] or "",
                        source=row["source_name"] or "sentinel",
                        published_date=row["published_at"] or row["fetched_at"],
                    )
                )

            elapsed = time.monotonic() - t0
            return SearchResponse(
                query=query,
                results=results,
                provider="sentinel_cache",
                success=True,
                search_time=elapsed,
            )
        except Exception as exc:
            logger.warning("SentinelCacheClient.search_for_stock failed: %s", exc)
            from src.search_service import SearchResult, SearchResponse
            return SearchResponse(
                query=f"{name} {code}",
                results=[],
                provider="sentinel_cache",
                success=False,
                error_message=str(exc),
                search_time=time.monotonic() - t0,
            )

    def register_stock(self, code: str, name: str = "") -> bool:
        """Register a stock for future targeted news fetching (non-blocking, best-effort).

        Called on sentinel cache miss so the next cycle will pre-fetch news for
        this stock. Safe to call even when sentinel is disabled — returns False silently.
        """
        try:
            cfg = self._get_config()
            if not cfg.enabled:
                return False
            store = self._get_store()
            return store.append_watched_stock(code.strip(), name.strip())
        except Exception as exc:
            logger.debug("SentinelCacheClient.register_stock failed: %s", exc)
            return False

    def get_recent_news(
        self,
        hours: int = 24,
        priority_min: int = 3,
        limit: int = 50,
    ) -> List[dict]:
        """Return classified high-priority news items as plain dicts.

        Keys: id, url_hash, title, content, source_name, url, priority,
              sentiment, category, market_scope, affected_sectors,
              affected_stocks, impact_horizon, llm_reasoning, is_actionable,
              published_at, fetched_at.
        """
        try:
            store = self._get_store()
            rows = store.get_recent_classified(hours=hours, priority_min=priority_min, limit=limit)
            return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.warning("SentinelCacheClient.get_recent_news failed: %s", exc)
            return []

    def get_latest_analysis(self) -> Optional[dict]:
        """Return the most recent cycle analysis as a dict, or None."""
        try:
            store = self._get_store()
            row = store.get_latest_cycle_analysis()
            if row is None:
                return None
            d = dict(row)
            for field in ("themes", "sector_opps", "stock_leads", "risk_alerts", "triggered_stocks"):
                val = d.get(field)
                if val and isinstance(val, str):
                    try:
                        d[field] = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        d[field] = []
                elif val is None:
                    d[field] = []
            return d
        except Exception as exc:
            logger.warning("SentinelCacheClient.get_latest_analysis failed: %s", exc)
            return None

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        for field in ("affected_sectors", "affected_stocks"):
            val = d.get(field)
            if val and isinstance(val, str):
                try:
                    d[field] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    d[field] = []
            elif val is None:
                d[field] = []
        d["is_actionable"] = bool(d.get("is_actionable", 0))
        return d
