# -*- coding: utf-8 -*-
"""SentinelService — top-level orchestrator for the News Sentinel.

Phase 1: single-threaded sequential spider runner with dedup + store.
Phase 2+: scheduler, LLM classifier, comprehensive analyzer.

CLI usage:
    python -m src.services.sentinel.service            # one full cycle
    python -m src.services.sentinel.service --dry-run  # fetch only, no write
"""
import logging
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from .config import SentinelConfig
from .dedup import Deduplicator, url_hash
from .models import CycleSummary, RawArticle
from .store import NewsStore
from .spiders.base import SpiderBase
from .spiders.google_news import GoogleNewsENSpider, GoogleNewsCNSpider
from .spiders.eastmoney import EastMoneySpider
from .spiders.cls import CLSRSSHubSpider
from .spiders.yahoo_finance import YahooFinanceRSSSpider
from .spiders.rsshub import RSSHubSpider

logger = logging.getLogger(__name__)


def _build_default_spiders(config: SentinelConfig) -> List[SpiderBase]:
    all_spiders: List[SpiderBase] = [
        GoogleNewsENSpider(),
        GoogleNewsCNSpider(),
        EastMoneySpider(),
        CLSRSSHubSpider(),
        YahooFinanceRSSSpider(),
    ]
    # Inject RSSHub base URL into all RSSHubSpider instances
    for spider in all_spiders:
        if isinstance(spider, RSSHubSpider):
            spider.configure(config)

    return [s for s in all_spiders if s.is_enabled(config)]


class SentinelService:
    def __init__(
        self,
        config: Optional[SentinelConfig] = None,
        store: Optional[NewsStore] = None,
        spiders: Optional[List[SpiderBase]] = None,
    ) -> None:
        self._config = config or SentinelConfig.from_env()
        self._store = store or NewsStore(self._config.db_path)
        self._spiders = spiders if spiders is not None else _build_default_spiders(self._config)

    # ── public API ────────────────────────────────────────────────────────────

    def run_cycle(self, dry_run: bool = False) -> CycleSummary:
        """Run one full fetch cycle across all enabled spiders."""
        summary = CycleSummary(started_at=datetime.now(timezone.utc))
        deduper = Deduplicator(self._store)

        for spider in self._spiders:
            spider_start = datetime.now(timezone.utc).isoformat()
            articles: List[RawArticle] = []
            fetched = new_count = deduped = 0
            status = "ok"
            error_msg = ""

            try:
                articles = spider.fetch()
                fetched = len(articles)

                if not dry_run:
                    for article in articles:
                        if not article.url:
                            continue
                        if deduper.is_new(article):
                            if self._store.upsert(article):
                                new_count += 1
                        else:
                            deduped += 1
                else:
                    new_count = fetched  # dry-run: treat all as new

            except Exception as exc:
                status = "error"
                error_msg = str(exc)
                logger.exception("[%s] unexpected error during cycle", spider.name)

            spider_end = datetime.now(timezone.utc).isoformat()

            if not dry_run:
                self._store.log_spider_run(
                    spider_name=spider.name,
                    started_at=spider_start,
                    finished_at=spider_end,
                    items_fetched=fetched,
                    items_new=new_count,
                    items_deduped=deduped,
                    status=status,
                    error_msg=error_msg,
                )

            summary.spider_results[spider.name] = {
                "fetched": fetched,
                "new": new_count,
                "deduped": deduped,
                "status": status,
                "error": error_msg,
                "healthy": spider.is_healthy(),
            }
            summary.total_fetched += fetched
            summary.total_new += new_count
            summary.total_deduped += deduped
            if error_msg:
                summary.errors.append(f"{spider.name}: {error_msg}")

            # Polite delay between spiders
            if not dry_run and self._config.request_delay_seconds > 0:
                time.sleep(self._config.request_delay_seconds)

        summary.finished_at = datetime.now(timezone.utc)
        return summary

    def status(self) -> dict:
        return {
            "enabled_spiders": [s.name for s in self._spiders],
            "total_items": self._store.count(),
            "items_by_spider": self._store.count_by_spider(),
        }


# ── CLI entry point ───────────────────────────────────────────────────────────

def _print_summary(summary: CycleSummary, dry_run: bool) -> None:
    mode = "[DRY-RUN] " if dry_run else ""
    elapsed = (summary.finished_at - summary.started_at).total_seconds()
    print(f"\n{mode}=== Sentinel Cycle Summary ===")
    print(f"Duration : {elapsed:.1f}s")
    print(f"Fetched  : {summary.total_fetched}")
    print(f"New      : {summary.total_new}")
    print(f"Deduped  : {summary.total_deduped}")
    print()
    print(f"{'Spider':<30} {'Fetched':>8} {'New':>6} {'Dedup':>6} {'Status':<10} {'Healthy'}")
    print("-" * 75)
    for name, r in summary.spider_results.items():
        healthy = "✓" if r["healthy"] else "✗ DEGRADED"
        err = f"  ({r['error']})" if r["error"] else ""
        print(f"{name:<30} {r['fetched']:>8} {r['new']:>6} {r['deduped']:>6} {r['status']:<10} {healthy}{err}")
    if summary.errors:
        print(f"\nErrors: {len(summary.errors)}")
        for e in summary.errors:
            print(f"  - {e}")
    print()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args
    loop_mode = "--loop" in args

    config = SentinelConfig.from_env()
    service = SentinelService(config=config)

    print(f"Sentinel starting — {len(service._spiders)} spiders enabled")
    if dry_run:
        print("Mode: DRY-RUN (no writes to DB)")
    elif loop_mode:
        print(f"Mode: LOOP (every {config.cycle_interval_minutes} min)  DB: {config.db_path}")
    else:
        print(f"DB  : {config.db_path}")

    if loop_mode:
        _run_loop(service, config)
    else:
        summary = service.run_cycle(dry_run=dry_run)
        _print_summary(summary, dry_run)
        if not dry_run:
            st = service.status()
            print(f"DB total items: {st['total_items']}")


def _run_loop(service: SentinelService, config: SentinelConfig) -> None:
    """Run cycles indefinitely, sleeping cycle_interval_minutes between runs.
    Designed for Docker / daemon use; handles KeyboardInterrupt / SIGTERM cleanly.
    """
    import signal

    _stop = [False]

    def _on_signal(signum, frame):
        logger.info("Sentinel received signal %d — finishing current cycle then stopping", signum)
        _stop[0] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    cycle_num = 0
    while not _stop[0]:
        cycle_num += 1
        logger.info("=== Sentinel cycle #%d starting ===", cycle_num)
        try:
            summary = service.run_cycle(dry_run=False)
            _print_summary(summary, dry_run=False)
            st = service.status()
            logger.info("DB total items: %d", st["total_items"])
        except Exception:
            logger.exception("Unexpected error in sentinel cycle #%d", cycle_num)

        if _stop[0]:
            break

        sleep_secs = config.cycle_interval_minutes * 60
        logger.info("Next cycle in %d min — sleeping", config.cycle_interval_minutes)
        # Sleep in 5-second chunks so SIGTERM is handled promptly
        for _ in range(sleep_secs // 5):
            if _stop[0]:
                break
            time.sleep(5)

    logger.info("Sentinel loop stopped after %d cycles", cycle_num)


if __name__ == "__main__":
    main()
