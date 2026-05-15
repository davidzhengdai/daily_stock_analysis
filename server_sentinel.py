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
from http.server import BaseHTTPRequestHandler, HTTPServer
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
        path = self.path.split("?")[0].rstrip("/")
        if path == "/healthz":
            self._respond({"status": "ok", "uptime_seconds": _uptime()})
        elif path == "/metrics":
            self._respond(_get_metrics())
        elif path == "/status":
            self._respond(_get_status())
        else:
            self.send_response(404)
            self.end_headers()

    def _respond(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
    return result


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
    httpd = HTTPServer(("0.0.0.0", port), _Handler)
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
