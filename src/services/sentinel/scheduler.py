# -*- coding: utf-8 -*-
"""SentinelScheduler — wraps the `schedule` library to run periodic sentinel tasks.

Usage (in-process, not the main daemon loop):
    scheduler = SentinelScheduler(service, config)
    scheduler.start()   # starts background thread
    ...
    scheduler.stop()    # signals thread to exit; joins with timeout

The main daemon loop in service.py uses a simpler manual sleep approach and does NOT
use this class directly. SentinelScheduler is provided for embedding the sentinel
into larger applications that already manage their own event loop.
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_TICK_INTERVAL = 5  # seconds between schedule.run_pending() checks


class SentinelScheduler:
    """Background thread that periodically fires sentinel cycles using the `schedule` library."""

    def __init__(self, service: "SentinelService", config: "SentinelConfig") -> None:  # type: ignore[name-defined]
        self._service = service
        self._config = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._scheduler = self._build_scheduler()

    def _build_scheduler(self):
        try:
            import schedule
        except ImportError:
            logger.warning("SentinelScheduler: 'schedule' package not installed — scheduling disabled")
            return None

        s = schedule.Scheduler()
        s.every(self._config.cycle_interval_minutes).minutes.do(self._run_cycle_safe)
        logger.info(
            "SentinelScheduler: fetch cycle every %d minutes",
            self._config.cycle_interval_minutes,
        )
        return s

    def _run_cycle_safe(self) -> None:
        try:
            from .service import _print_summary
            summary = self._service.run_cycle(dry_run=False)
            _print_summary(summary, dry_run=False)
        except Exception:
            logger.exception("SentinelScheduler: unhandled error in cycle")

    def _loop(self) -> None:
        logger.info("SentinelScheduler thread started")
        while not self._stop_event.is_set():
            if self._scheduler is not None:
                self._scheduler.run_pending()
            self._stop_event.wait(timeout=_TICK_INTERVAL)
        logger.info("SentinelScheduler thread stopped")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sentinel-scheduler")
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
