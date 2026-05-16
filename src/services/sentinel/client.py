# -*- coding: utf-8 -*-
"""
SentinelCacheClient — read-only facade over NewsStore for use by SearchService.

Converts NewsStore rows into SearchResponse / SearchResult objects so that
sentinel-cached news can be injected as a search dimension without any online
API call.
"""
import json
import socket
import logging
import time
from typing import List, Optional
from urllib import error as urlerror
from urllib import parse, request

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

    def _http_json(self, path: str, query: Optional[dict] = None, method: str = "GET", body=None):
        cfg = self._get_config()
        base_url = getattr(cfg, "server_url", "")
        if not base_url:
            return None
        url = f"{base_url}{path}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, urlerror.URLError, json.JSONDecodeError) as exc:
            logger.debug("Sentinel HTTP request failed for %s: %s", path, exc)
            return None

    def _server_url(self) -> str:
        cfg = self._get_config()
        return getattr(cfg, "server_url", "")

    @staticmethod
    def _payload_items(payload) -> Optional[list]:
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return payload["items"]
        if isinstance(payload, list):
            return payload
        return None

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
            payload = self._http_json("/status")
            if isinstance(payload, dict):
                return bool(payload.get("enabled", False))
            store = self._get_store()
            count = store.count()
            if count < min_items:
                logger.info("[Sentinel] 缓存条目不足（%d < %d），标记为不可用", count, min_items)
                return False
            # Freshness check: bail out if no successful spider run within the window
            if cfg.max_cache_age_hours > 0:
                from datetime import datetime, timezone, timedelta
                latest = store.get_latest_spider_run_time()
                if latest is None:
                    logger.info("[Sentinel] 尚无成功抓取记录，标记为不可用")
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
            payload = self._http_json("/news/search", query={"q": f"{name} {code}", "limit": max_results})
            rows = self._payload_items(payload)
            if rows is None:
                store = self._get_store()

                # Primary: FTS search
                query = f"{name} {code}"
                rows = [dict(r) for r in store.search_fts(query, limit=max_results)]

                # Fallback: recent P3+ items mentioning the stock code
                if not rows:
                    candidates = store.get_recent(hours=72, priority_min=3, limit=200)
                    rows = [
                        dict(r) for r in candidates
                        if code in (r["affected_stocks"] or "")
                    ][:max_results]
            else:
                query = f"{name} {code}"
                rows = [r for r in rows if isinstance(r, dict)]

            # Fallback: recent P3+ items mentioning the stock code.
            if not rows:
                payload = self._http_json("/news", query={"hours": 72, "priority_min": 3, "limit": 200})
                candidates = self._payload_items(payload) or []
                rows = [
                    r for r in candidates
                    if isinstance(r, dict) and code in json.dumps(r.get("affected_stocks") or [], ensure_ascii=False)
                ][:max_results]

            results: List[SearchResult] = []
            for row in rows:
                title = row.get("title") or ""
                content = row.get("content") or ""
                snippet = content[:300] if content else title
                results.append(
                    SearchResult(
                        title=title,
                        snippet=snippet,
                        url=row.get("url") or "",
                        source=row.get("source_name") or "sentinel",
                        published_date=row.get("published_at") or row.get("fetched_at"),
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

    def search_for_stock_live(
        self,
        code: str,
        name: str,
        max_results: int = 10,
        context: str = "",
        heartbeat_timeout: float = 3.0,
    ):
        """Ask the Sentinel HTTP service for stock-context news with heartbeat.

        Returns ``None`` when the server is unavailable or stops sending events
        for ``heartbeat_timeout`` seconds, allowing SearchService to fall back to
        online providers.
        """
        from src.search_service import SearchResult, SearchResponse

        base_url = self._server_url()
        if not base_url:
            return None

        query = f"{name} {code}"
        url = f"{base_url}/stock-news/stream?{parse.urlencode({'code': code, 'name': name, 'context': context, 'limit': max_results})}"
        req = request.Request(url, headers={"Accept": "application/x-ndjson"})
        t0 = time.monotonic()
        try:
            with request.urlopen(req, timeout=heartbeat_timeout) as resp:
                while True:
                    try:
                        line = resp.readline()
                    except socket.timeout:
                        logger.info(
                            "[Sentinel] heartbeat timeout after %.1fs for %s(%s)",
                            heartbeat_timeout,
                            name,
                            code,
                        )
                        return None
                    if not line:
                        return None
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    event_type = event.get("event")
                    if event_type == "heartbeat":
                        logger.info(
                            "[Sentinel] heartbeat stage=%s %s(%s)",
                            event.get("stage", "unknown"),
                            name,
                            code,
                        )
                        continue
                    if event_type == "error":
                        logger.info("[Sentinel] stream error for %s(%s): %s", name, code, event.get("error"))
                        return None
                    if event_type == "result":
                        rows = [r for r in event.get("items", []) if isinstance(r, dict)]
                        results: List[SearchResult] = []
                        for row in rows:
                            title = row.get("title") or ""
                            content = row.get("content") or ""
                            results.append(
                                SearchResult(
                                    title=title,
                                    snippet=content[:300] if content else title,
                                    url=row.get("url") or "",
                                    source=row.get("source_name") or "sentinel",
                                    published_date=row.get("published_at") or row.get("fetched_at"),
                                )
                            )
                        return SearchResponse(
                            query=query,
                            results=results,
                            provider="sentinel_cache",
                            success=True,
                            search_time=time.monotonic() - t0,
                        )
        except (OSError, urlerror.URLError, TimeoutError) as exc:
            logger.info("[Sentinel] live request failed for %s(%s): %s", name, code, exc)
            return None

    def register_stock(self, code: str, name: str = "") -> bool:
        """Register a stock for future targeted news fetching (non-blocking, best-effort).

        Called on sentinel cache miss so the next cycle will pre-fetch news for
        this stock. Safe to call even when sentinel is disabled — returns False silently.
        """
        try:
            cfg = self._get_config()
            if not cfg.enabled:
                return False
            payload = self._http_json(
                "/watched-stocks",
                query={"merge": "true"},
                method="PUT",
                body=[{"code": code.strip(), "name": name.strip()}],
            )
            if isinstance(payload, dict):
                return bool(payload.get("updated", 0))
            store = self._get_store()
            return store.append_watched_stock(code.strip(), name.strip())
        except Exception as exc:
            logger.debug("SentinelCacheClient.register_stock failed: %s", exc)
            return False

    def fetch_for_stock_async(self, code: str, name: str = "") -> Optional["threading.Event"]:
        """Fetch, store, and classify news for *code* in a daemon thread.

        Returns a ``threading.Event`` that is set when the fetch completes (or
        fails), so the caller can wait for fresh results before deciding to fall
        back to online search. Returns ``None`` when sentinel is disabled.
        """
        import threading

        try:
            cfg = self._get_config()
            if not cfg.enabled:
                return None
        except Exception:
            return None

        done = threading.Event()

        def _bg() -> None:
            try:
                payload = self._http_json("/fetch-now", method="POST", body={"code": code.strip(), "name": name.strip()})
                if isinstance(payload, dict):
                    logger.info(
                        "[Sentinel] 即时抓取完成 %s(%s): fetched=%d new=%d classified=%d",
                        name, code, payload.get("fetched", 0), payload.get("new", 0), payload.get("classified", 0),
                    )
                    return
                from .service import SentinelService
                svc = SentinelService()
                result = svc.fetch_for_stock(code.strip(), name.strip())
                logger.info(
                    "[Sentinel] 即时抓取完成 %s(%s): fetched=%d new=%d classified=%d",
                    name, code, result["fetched"], result["new"], result["classified"],
                )
            except Exception as exc:
                logger.debug("[Sentinel] 即时抓取异常 %s(%s): %s", name, code, exc)
            finally:
                done.set()

        t = threading.Thread(target=_bg, daemon=True, name=f"sentinel-fetch-{code}")
        t.start()
        logger.info("[Sentinel] 已触发即时抓取：%s(%s)，等待结果中…", name, code)
        return done

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
            payload = self._http_json("/news", query={"hours": hours, "priority_min": priority_min, "limit": limit})
            rows = self._payload_items(payload)
            if rows is not None:
                return [r for r in rows if isinstance(r, dict)]
            store = self._get_store()
            rows = store.get_recent_classified(hours=hours, priority_min=priority_min, limit=limit)
            return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.warning("SentinelCacheClient.get_recent_news failed: %s", exc)
            return []

    def get_latest_analysis(self) -> Optional[dict]:
        """Return the most recent cycle analysis as a dict, or None."""
        try:
            payload = self._http_json("/analyses", query={"limit": 1})
            rows = self._payload_items(payload)
            if rows:
                return rows[0] if isinstance(rows[0], dict) else None
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
