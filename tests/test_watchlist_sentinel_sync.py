# -*- coding: utf-8 -*-

from src.services.sentinel.store import NewsStore
from src.services.watchlist_service import WatchlistService


class FakeWatchlistRepo:
    def __init__(self, items=None):
        self.items = list(items or [])
        self.removed = False

    def list_all(self):
        return list(self.items)

    def upsert(self, code: str, name: str = "", notes: str = ""):
        item = {
            "code": code,
            "name": name,
            "notes": notes,
            "added_at": None,
            "last_analyzed_at": None,
        }
        self.items = [existing for existing in self.items if existing["code"] != code]
        self.items.append(item)
        return item

    def remove(self, code: str):
        original_count = len(self.items)
        self.items = [item for item in self.items if item["code"] != code]
        self.removed = len(self.items) != original_count
        return self.removed

    def is_watched(self, code: str):
        return any(item["code"] == code for item in self.items)


def test_add_and_remove_trigger_sentinel_sync(monkeypatch):
    repo = FakeWatchlistRepo()
    service = WatchlistService(repo=repo)
    sync_calls = []

    def fake_sync():
        sync_calls.append([item["code"] for item in repo.list_all()])
        return len(sync_calls[-1])

    monkeypatch.setattr(service, "sync_to_sentinel", fake_sync)

    service.add("AAPL", "Apple")
    service.remove("AAPL")
    service.remove("MSFT")

    assert sync_calls == [["AAPL"], []]


def test_sync_to_sentinel_replaces_stale_watched_stocks(monkeypatch, tmp_path):
    sentinel_db = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(sentinel_db))

    store = NewsStore(str(sentinel_db))
    store.upsert_watched_stocks([
        {"code": "META", "name": "Meta Platforms"},
        {"code": "LUM", "name": "股票LUM"},
    ])

    repo = FakeWatchlistRepo([
        {"code": "AAPL", "name": "Apple"},
        {"code": "MSFT", "name": "Microsoft"},
    ])
    synced = WatchlistService(repo=repo).sync_to_sentinel()

    assert synced == 2
    assert store.get_watched_stocks() == [
        {"code": "AAPL", "name": "Apple"},
        {"code": "MSFT", "name": "Microsoft"},
    ]


def test_sync_to_sentinel_clears_watched_stocks_when_watchlist_is_empty(monkeypatch, tmp_path):
    sentinel_db = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(sentinel_db))

    store = NewsStore(str(sentinel_db))
    store.upsert_watched_stocks([{"code": "AAPL", "name": "Apple"}])

    synced = WatchlistService(repo=FakeWatchlistRepo()).sync_to_sentinel()

    assert synced == 0
    assert store.get_watched_stocks() == []
