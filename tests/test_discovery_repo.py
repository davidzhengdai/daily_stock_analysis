# -*- coding: utf-8 -*-
"""Tests for DiscoveryList repository behavior."""

from datetime import datetime, timedelta

from src.repositories.discovery_repo import DiscoveryRepo
from src.storage import DatabaseManager, DiscoveryItem


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
