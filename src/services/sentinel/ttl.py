# -*- coding: utf-8 -*-
"""TTL retention policy for news items.

Priority → retention mapping (from design doc):
  P5 (极高影响): permanent — never expires
  P4 (高影响)  : 90 days   — archived after expiry
  P3 (中等)    : 30 days   — soft-deleted
  P2 (低)      : 7 days    — hard-deleted
  P1 (极低)    : 24 hours  — hard-deleted
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .store import NewsStore

logger = logging.getLogger(__name__)

TTL_BY_PRIORITY: dict = {
    1: timedelta(hours=24),
    2: timedelta(days=7),
    3: timedelta(days=30),
    4: timedelta(days=90),
    5: None,   # permanent
}

_DEFAULT_TTL = timedelta(days=7)   # for unclassified items


def compute_expires_at(
    priority: Optional[int],
    base_time: datetime,
) -> Optional[str]:
    """Return ISO8601 UTC expiry string, or None for P5 / unknown priority.

    Args:
        priority:  LLM-assigned priority (1-5) or None for unclassified.
        base_time: Reference timestamp (use published_at if available, else fetched_at).
                   Should be timezone-aware; naive datetimes are assumed UTC.
    """
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)

    if priority == 5:
        return None

    ttl = TTL_BY_PRIORITY.get(priority, _DEFAULT_TTL)
    if ttl is None:
        return None

    return (base_time + ttl).isoformat()


class TTLPurger:
    """Runs the expiry cleanup against NewsStore."""

    def __init__(self, store: "NewsStore") -> None:
        self._store = store

    def run(self) -> dict:
        """Execute the TTL purge and return {"deleted": N, "archived": N}."""
        result = self._store.purge_expired()
        logger.info(
            "TTLPurger: deleted=%d archived=%d",
            result.get("deleted", 0),
            result.get("archived", 0),
        )
        return result
