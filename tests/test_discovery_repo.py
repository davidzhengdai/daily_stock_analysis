# -*- coding: utf-8 -*-
"""Tests for DiscoveryList repository behavior."""

from datetime import datetime, timedelta

from src.repositories.discovery_repo import DiscoveryRepo
from src.storage import DatabaseManager, DiscoveryItem
from api.v1.endpoints.discovery import _dedupe_discovery_items


def _add(repo: DiscoveryRepo, ticker: str = "NVDA", run_id: str = "run-1"):
    return repo.add(
        ticker=ticker,
        name="NVIDIA",
        market="us",
        sector="Semiconductors",
        source="heat_radar",
        source_run_id=run_id,
        score=88.0,
        confidence=75,
        thesis="AI catalyst",
        themes_json='["AI"]',
        trade_horizon="short",
        ttl_days=5,
    )


def test_discovery_add_is_idempotent_and_counts_only_new_rows():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = DiscoveryRepo(db)

        first = _add(repo)
        duplicate = _add(repo)

        assert first is not None
        assert duplicate is None
        assert len(repo.list_active()) == 1
    finally:
        DatabaseManager.reset_instance()


def test_discovery_expire_stale_hides_expired_items():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = DiscoveryRepo(db)
        _add(repo)
        with db.get_session() as session:
            item = session.query(DiscoveryItem).filter_by(ticker="NVDA").one()
            item.expires_at = datetime.utcnow() - timedelta(days=1)
            session.commit()

        assert repo.expire_stale() == 1
        assert repo.list_active() == []
    finally:
        DatabaseManager.reset_instance()


def test_discovery_api_dedupe_keeps_highest_score_per_ticker_market():
    items = [
        {
            "id": 1,
            "ticker": "NVDA",
            "market": "US",
            "source": "scanner",
            "score": 71.0,
            "added_at": "2026-05-26T10:00:00",
        },
        {
            "id": 2,
            "ticker": "nvda",
            "market": "US",
            "source": "heat_radar",
            "score": 88.0,
            "added_at": "2026-05-26T11:00:00",
        },
        {
            "id": 3,
            "ticker": "AAPL",
            "market": "US",
            "source": "gold_digger",
            "score": 80.0,
            "added_at": "2026-05-26T09:00:00",
        },
    ]

    deduped = _dedupe_discovery_items(items)

    assert [item["id"] for item in deduped] == [2, 3]
