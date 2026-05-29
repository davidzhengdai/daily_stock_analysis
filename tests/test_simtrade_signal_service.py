from datetime import date, timedelta

import pandas as pd

from src.services.simtrade.signal_service import SignalService
from src.storage import DatabaseManager


def test_parse_llm_response_handles_empty_response():
    assert SignalService._parse_llm_response(None) == {}
    assert SignalService._parse_llm_response("") == {}
    assert SignalService._parse_llm_response("   ") == {}


def test_parse_llm_response_extracts_fenced_json():
    raw = """```json
{"signal":"skip","confidence":0.1}
```"""

    parsed = SignalService._parse_llm_response(raw)

    assert parsed["signal"] == "skip"
    assert parsed["confidence"] == 0.1


def test_parse_risk_flags_accepts_string_and_list():
    assert SignalService._parse_risk_flags(None) == []
    assert SignalService._parse_risk_flags("recent_stop_loss") == ["recent_stop_loss"]
    assert SignalService._parse_risk_flags(["price_mismatch", "", "max_position"]) == [
        "price_mismatch",
        "max_position",
    ]


def test_clamp_us_position_size_limits_oversized_new_buys():
    assert SignalService._clamp_us_position_size_pct(
        100.0,
        current_price=120.0,
        has_position=False,
        risk_flags=[],
    ) == 8.0


def test_clamp_us_position_size_limits_adds_and_low_price_buys():
    assert SignalService._clamp_us_position_size_pct(
        20.0,
        current_price=120.0,
        has_position=True,
        risk_flags=[],
    ) == 3.0
    assert SignalService._clamp_us_position_size_pct(
        20.0,
        current_price=2.5,
        has_position=False,
        risk_flags=[],
    ) == 2.0
    assert SignalService._clamp_us_position_size_pct(
        20.0,
        current_price=120.0,
        has_position=False,
        risk_flags=["low_price_us_stock"],
    ) == 2.0


def test_get_stock_data_refreshes_stale_daily_cache(monkeypatch):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        code = "V"
        stale_day = date.today() - timedelta(days=7)
        fresh_day = date.today()
        db.save_daily_data(
            pd.DataFrame([
                {
                    "date": stale_day,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000.0,
                    "amount": 100000.0,
                    "pct_chg": 0.0,
                    "ma5": 100.0,
                    "ma10": 100.0,
                    "ma20": 100.0,
                    "volume_ratio": 1.0,
                }
            ]),
            code,
            "test-stale",
        )

        class FakeFetcherManager:
            def get_daily_data(self, stock_code, days=120):
                assert stock_code == code
                assert days == 120
                return pd.DataFrame([
                    {
                        "date": stale_day,
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.0,
                        "volume": 1000.0,
                        "amount": 100000.0,
                        "pct_chg": 0.0,
                        "ma5": 100.0,
                        "ma10": 100.0,
                        "ma20": 100.0,
                        "volume_ratio": 1.0,
                    },
                    {
                        "date": fresh_day,
                        "open": 105.0,
                        "high": 108.0,
                        "low": 104.0,
                        "close": 107.0,
                        "volume": 1200.0,
                        "amount": 128400.0,
                        "pct_chg": 7.0,
                        "ma5": 104.0,
                        "ma10": 102.0,
                        "ma20": 101.0,
                        "volume_ratio": 1.2,
                    },
                ]), "FakeFetcher"

        import data_provider.base as base_module

        monkeypatch.setattr(base_module, "DataFetcherManager", FakeFetcherManager)

        data = SignalService()._get_stock_data(code)

        assert data is not None
        assert data["date"] == fresh_day
        assert data["close"] == 107.0
        assert data["data_source"] == "simtrade:FakeFetcher"
    finally:
        DatabaseManager.reset_instance()
