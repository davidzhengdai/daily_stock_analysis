from datetime import datetime

from src.services.realtime_quote_service import RealtimeQuoteService
from src.storage import DatabaseManager


def test_realtime_quote_service_returns_cached_quote_when_market_closed(monkeypatch):
    DatabaseManager.reset_instance()
    DatabaseManager(db_url="sqlite:///:memory:")
    service = RealtimeQuoteService()
    try:
        calls = {"count": 0}

        class FakeQuote:
            def to_dict(self):
                return {
                    "code": "600519",
                    "source": "fake",
                    "price": 123.45,
                    "fetched_at": datetime.utcnow().isoformat(),
                }

        class FakeFetcher:
            def get_realtime_quote(self, code, log_final_failure=False):
                calls["count"] += 1
                return FakeQuote()

        monkeypatch.setattr(
            "src.services.realtime_quote_service._get_shared_fetcher_manager",
            lambda: FakeFetcher(),
        )
        monkeypatch.setattr(
            RealtimeQuoteService,
            "_is_market_open_for_code",
            staticmethod(lambda code: True),
        )

        fresh = service.get_quote("600519")
        assert fresh is not None
        assert fresh["price"] == 123.45
        assert fresh["is_cached"] is False
        assert calls["count"] == 1

        monkeypatch.setattr(
            RealtimeQuoteService,
            "_is_market_open_for_code",
            staticmethod(lambda code: False),
        )
        cached = service.get_quote("600519")

        assert cached is not None
        assert cached["price"] == 123.45
        assert cached["is_cached"] is True
        assert cached["market_open"] is False
        assert calls["count"] == 1
    finally:
        DatabaseManager.reset_instance()
