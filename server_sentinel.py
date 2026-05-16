# -*- coding: utf-8 -*-
"""Standalone News Sentinel process with built-in health/metrics HTTP server.

Runs the sentinel fetch-classify-analyse loop in the foreground while
serving a minimal HTTP API on a configurable port (default 9100).

Usage:
    python server_sentinel.py [--port 9100] [--dry-run]

Environment variables (same as main service):
    SENTINEL_ENABLED           — must be "true" to activate (default false)
    SENTINEL_DB_PATH           — path to SQLite DB
    SENTINEL_SERVER_PORT       — HTTP server port (default 9100)
    All other SENTINEL_* vars  — see docs/design-news-sentinel.html

Endpoints served:
    GET /healthz               — {"status": "ok", "uptime_seconds": N}
    GET /metrics               — full metrics summary JSON
    GET /status                — sentinel config + DB stats

This module is intentionally dependency-free beyond the project's own
stack (stdlib http.server + threading). For production deployment use the
Docker Compose sentinel service definition instead.
"""
import json
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sentinel.server")

_sentinel_service: Optional[object] = None   # SentinelService instance
_sentinel_metrics: Optional[object] = None   # SentinelMetrics instance
_sentinel_config: Optional[object] = None    # SentinelConfig instance
_stop_event = threading.Event()


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log
        logger.debug("HTTP %s", fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        if path == "/healthz":
            self._respond({"status": "ok", "uptime_seconds": _uptime()})
        elif path == "/metrics":
            self._respond(_get_metrics())
        elif path == "/status":
            self._respond(_get_status())
        elif path == "/news":
            self._respond(_get_news(query))
        elif path == "/news/search":
            self._respond(_search_news(query))
        elif path == "/stock-news/stream":
            self._stream_stock_news(query)
        elif path == "/analyses":
            self._respond(_get_analyses(query))
        elif path == "/watched-stocks":
            self._respond({"items": _get_watched_stocks()})
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        if path == "/watched-stocks":
            self._respond(_set_watched_stocks(self._read_json_body(), query))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path == "/fetch-now":
            self._respond(_fetch_now(self._read_json_body()))
        else:
            self.send_response(404)
            self.end_headers()

    def _read_json_body(self) -> object:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _respond(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_stock_news(self, query: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(event: dict) -> bool:
            try:
                self.wfile.write(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        code = str(query.get("code", [""])[0] or "").strip()
        name = str(query.get("name", [""])[0] or "").strip()
        context = str(query.get("context", [""])[0] or "").strip()
        limit = _int_param(query, "limit", 10, 1, 50)
        if not code:
            send({"event": "error", "error": "code is required"})
            return

        if not send({"event": "heartbeat", "stage": "cache_check", "code": code}):
            return

        cached = _search_stock_context_rows(code, name, context, limit)
        if cached:
            send({"event": "result", "cached": True, "items": [_row_to_news(r) for r in cached]})
            return

        if _sentinel_service is None:
            send({"event": "error", "error": "sentinel service not initialised"})
            return

        done = threading.Event()
        result: dict = {}

        def run_fetch() -> None:
            try:
                result["fetch"] = _sentinel_service.fetch_for_stock(code, name)
                result["rows"] = _search_stock_context_rows(code, name, context, limit)
            except Exception as exc:
                logger.warning("stock-news stream fetch failed for %s: %s", code, exc)
                result["error"] = str(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_fetch, daemon=True, name=f"sentinel-stream-fetch-{code}")
        worker.start()
        started = time.monotonic()
        if not send({"event": "heartbeat", "stage": "search_started", "code": code}):
            return
        while not done.wait(timeout=1.0):
            if not send({
                "event": "heartbeat",
                "stage": "searching",
                "code": code,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }):
                return

        if result.get("error"):
            send({"event": "error", "error": result["error"]})
            return
        fetch = result.get("fetch") if isinstance(result.get("fetch"), dict) else {}
        rows = result.get("rows") or []
        send({
            "event": "result",
            "cached": False,
            "fetch": fetch,
            "items": [_row_to_news(r) for r in rows],
        })


_start_time = time.time()


def _uptime() -> int:
    return int(time.time() - _start_time)


def _get_metrics() -> dict:
    if _sentinel_metrics is None:
        return {"error": "metrics not initialised"}
    db_path = _sentinel_config.db_path if _sentinel_config else None
    return _sentinel_metrics.summary(db_path=db_path)


def _get_status() -> dict:
    result: dict = {}
    if _sentinel_config:
        result["enabled"] = _sentinel_config.enabled
        result["db_path"] = _sentinel_config.db_path
        result["cycle_interval_minutes"] = _sentinel_config.cycle_interval_minutes
    if _sentinel_service:
        try:
            result.update(_sentinel_service.status())
        except Exception as exc:
            result["status_error"] = str(exc)
    try:
        store = _store()
        result.setdefault("total_items", store.count())
        result["unclassified_count"] = store.count_unclassified()
        result["last_analysis_at"] = store.get_last_cycle_analysis_at()
        latest_run = store.get_latest_spider_run_time()
        result["latest_spider_run_at"] = latest_run.isoformat() if latest_run else None
        result.setdefault("watched_stocks_count", len(store.get_watched_stocks()))
    except Exception as exc:
        result["db_status_error"] = str(exc)
    return result


def _store():
    if _sentinel_service is not None:
        return _sentinel_service._store
    from src.services.sentinel.config import SentinelConfig
    from src.services.sentinel.store import NewsStore

    cfg = _sentinel_config or SentinelConfig.from_env()
    return NewsStore(db_path=cfg.db_path)


def _int_param(query: dict, name: str, default: int, min_value: int, max_value: int) -> int:
    raw = query.get(name, [default])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(min_value, min(max_value, value))


def _parse_json_field(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _row_to_news(row) -> dict:
    d = dict(row)
    return {
        "id": d.get("id"),
        "title": d.get("title") or "",
        "content": (d.get("content") or "")[:500],
        "source_name": d.get("source_name") or "",
        "url": d.get("url") or "",
        "priority": d.get("priority"),
        "sentiment": d.get("sentiment"),
        "category": d.get("category"),
        "market_scope": d.get("market_scope"),
        "affected_sectors": _parse_json_field(d.get("affected_sectors")),
        "affected_stocks": _parse_json_field(d.get("affected_stocks")),
        "impact_horizon": d.get("impact_horizon"),
        "llm_reasoning": d.get("llm_reasoning"),
        "is_actionable": bool(d.get("is_actionable", 0)),
        "published_at": d.get("published_at"),
        "fetched_at": d.get("fetched_at") or "",
    }


def _row_to_analysis(row) -> dict:
    d = dict(row)
    return {
        "id": d.get("id"),
        "cycle_at": d.get("cycle_at") or "",
        "news_count": d.get("news_count"),
        "themes": _parse_json_field(d.get("themes")),
        "sector_opps": _parse_json_field(d.get("sector_opps")),
        "stock_leads": _parse_json_field(d.get("stock_leads")),
        "risk_alerts": _parse_json_field(d.get("risk_alerts")),
        "market_mood": d.get("market_mood"),
        "triggered_stocks": _parse_json_field(d.get("triggered_stocks")),
        "created_at": d.get("created_at"),
    }


def _get_news(query: dict) -> dict:
    store = _store()
    hours = _int_param(query, "hours", 24, 1, 720)
    priority_min = _int_param(query, "priority_min", 3, 1, 5)
    limit = _int_param(query, "limit", 50, 1, 200)
    rows = store.get_recent_classified(hours=hours, priority_min=priority_min, limit=limit)
    return {"items": [_row_to_news(r) for r in rows]}


def _search_news(query: dict) -> dict:
    store = _store()
    q = str(query.get("q", [""])[0] or "").strip()
    limit = _int_param(query, "limit", 20, 1, 100)
    rows = store.search_fts(q, limit=limit) if q else []
    return {"items": [_row_to_news(r) for r in rows]}


def _search_stock_context_rows(code: str, name: str, context: str = "", limit: int = 10) -> list:
    store = _store()
    terms = " ".join(
        part for part in [
            name.strip(),
            code.strip(),
            context.strip(),
            "industry sector policy regulation political geopolitical macro",
        ]
        if part
    )
    rows = store.search_fts(terms, limit=limit) if terms else []
    if rows:
        return rows

    candidates = store.get_recent(hours=72, priority_min=3, limit=200)
    code_upper = code.upper()
    name_lower = name.lower()
    context_terms = [t.lower() for t in context.split() if len(t) >= 2]
    matched = []
    for row in candidates:
        d = dict(row)
        stock_blob = json.dumps(_parse_json_field(d.get("affected_stocks")), ensure_ascii=False).upper()
        text = f"{d.get('title') or ''} {d.get('content') or ''} {d.get('affected_sectors') or ''}".lower()
        if code_upper and code_upper in stock_blob:
            matched.append(row)
        elif name_lower and name_lower in text:
            matched.append(row)
        elif context_terms and any(term in text for term in context_terms):
            matched.append(row)
        if len(matched) >= limit:
            break
    return matched


def _get_analyses(query: dict) -> dict:
    store = _store()
    limit = _int_param(query, "limit", 10, 1, 50)
    return {"items": [_row_to_analysis(r) for r in store.get_cycle_analyses(limit=limit)]}


def _get_watched_stocks() -> list:
    return _store().get_watched_stocks()


def _set_watched_stocks(body: object, query: dict) -> dict:
    store = _store()
    stocks = body if isinstance(body, list) else []
    data = [
        {"code": str(s.get("code", "")).strip(), "name": str(s.get("name", "")).strip()}
        for s in stocks
        if isinstance(s, dict) and str(s.get("code", "")).strip()
    ]
    merge = str(query.get("merge", ["false"])[0]).strip().lower() in ("1", "true", "yes", "on")
    if merge:
        count = sum(1 for s in data if store.append_watched_stock(s["code"], s["name"]))
    else:
        count = store.upsert_watched_stocks(data)
    return {"updated": count, "merge": merge}


def _fetch_now(body: object) -> dict:
    data = body if isinstance(body, dict) else {}
    code = str(data.get("code", "")).strip()
    name = str(data.get("name", "")).strip()
    if not code:
        return {"code": "", "fetched": 0, "new": 0, "classified": 0, "error": "code is required"}
    if _sentinel_service is None:
        return {"code": code, "fetched": 0, "new": 0, "classified": 0, "error": "sentinel service not initialised"}
    try:
        return _sentinel_service.fetch_for_stock(code, name)
    except Exception as exc:
        logger.warning("fetch-now failed for %s: %s", code, exc)
        return {"code": code, "fetched": 0, "new": 0, "classified": 0, "error": str(exc)}


# ── sentinel loop ─────────────────────────────────────────────────────────────

def _run_sentinel_loop(service, config, metrics) -> None:
    from src.services.sentinel.service import _print_summary

    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        logger.info("=== Sentinel cycle #%d ===", cycle)
        t0 = time.time()
        try:
            summary = service.run_cycle(dry_run=False)
            _print_summary(summary, dry_run=False)
            metrics.record_cycle(error=False)
            # Record per-spider stats
            for name, r in summary.spider_results.items():
                metrics.record_spider_fetch(name, r["fetched"], error=(r["status"] == "error"))
            # Record classification latency (rough: total elapsed / classified)
            if summary.classified_count > 0:
                metrics.record_classification(summary.classified_count, time.time() - t0)
        except Exception:
            logger.exception("Sentinel cycle #%d error", cycle)
            metrics.record_cycle(error=True)

        sleep_secs = config.cycle_interval_minutes * 60
        logger.info("Next cycle in %d min", config.cycle_interval_minutes)
        for _ in range(sleep_secs // 5):
            if _stop_event.is_set():
                break
            time.sleep(5)

    logger.info("Sentinel loop exited after %d cycles", cycle)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    global _sentinel_service, _sentinel_metrics, _sentinel_config

    import os
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    port = int(os.getenv("SENTINEL_SERVER_PORT", "9100"))
    for arg in args:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])

    from src.services.sentinel.config import SentinelConfig
    from src.services.sentinel.service import SentinelService
    from src.services.sentinel.metrics import SentinelMetrics

    config = SentinelConfig.from_env()
    _sentinel_config = config

    if not config.enabled and not dry_run:
        logger.warning(
            "SENTINEL_ENABLED is not set to true. Set it in .env or pass --dry-run. Exiting."
        )
        sys.exit(1)

    metrics = SentinelMetrics()
    _sentinel_metrics = metrics

    service = SentinelService(config=config)
    _sentinel_service = service

    logger.info(
        "Sentinel server starting — %d spiders, port %d%s",
        len(service._spiders),
        port,
        " (DRY-RUN)" if dry_run else "",
    )

    # Signal handling
    def _on_signal(signum, frame):
        logger.info("Received signal %d — shutting down", signum)
        _stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    if dry_run:
        logger.info("Dry-run mode: running one cycle then exiting")
        from src.services.sentinel.service import _print_summary
        summary = service.run_cycle(dry_run=True)
        _print_summary(summary, dry_run=True)
        return

    # Start HTTP health server in background thread
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="sentinel-http")
    http_thread.start()
    logger.info("Health server listening on :%d", port)

    # Run sentinel loop in main thread
    try:
        _run_sentinel_loop(service, config, metrics)
    finally:
        httpd.shutdown()
        logger.info("Sentinel server stopped")


if __name__ == "__main__":
    main()
