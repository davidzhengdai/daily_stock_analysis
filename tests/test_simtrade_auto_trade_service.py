from src.services.simtrade.auto_trade_service import _filter_watchlist_by_open_markets


def test_filter_watchlist_by_open_markets_keeps_cn_when_only_cn_open():
    watchlist = [
        {"code": "600060"},
        {"code": "NVDA"},
        {"code": "600238"},
        {"code": "AAPL"},
    ]

    filtered, skipped, markets = _filter_watchlist_by_open_markets(
        watchlist,
        cn_open=True,
        us_open=False,
    )

    assert [item["code"] for item in filtered] == ["600060", "600238"]
    assert skipped == 2
    assert markets == {"CN"}


def test_filter_watchlist_by_open_markets_keeps_us_when_only_us_open():
    watchlist = [
        {"code": "600060"},
        {"code": "NVDA"},
        {"code": "AAPL"},
    ]

    filtered, skipped, markets = _filter_watchlist_by_open_markets(
        watchlist,
        cn_open=False,
        us_open=True,
    )

    assert [item["code"] for item in filtered] == ["NVDA", "AAPL"]
    assert skipped == 1
    assert markets == {"US"}
