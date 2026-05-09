import sys
from types import SimpleNamespace

from src.schemas.gold_digger import DigConfig, GarbageStockInfo, InvestmentTheme
from src.schemas.scanner import StockInfo
from src.services.gold_digger import _build_candidate, _fetch_us_garbage_stocks
from src.services.stock_universe import USStockUniverse


def test_nasdaq_screener_fetches_exchanges_separately_and_parses_rows(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, rows):
            self._rows = rows

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"rows": self._rows}}

    def fake_get(url, params, headers, timeout):
        calls.append(params["exchange"])
        rows = [
            {
                "symbol": f"{params['exchange']}A",
                "name": "Example Corp Common Stock",
                "lastsale": "$12.34",
                "volume": "1,234,567",
                "marketCap": "1234567890.00",
                "country": "United States",
                "sector": "Technology",
                "industry": "Software",
                "exchange": params["exchange"],
            },
            {
                "symbol": f"{params['exchange']}CN",
                "name": "Foreign Corp",
                "lastsale": "$8.00",
                "volume": "1000",
                "marketCap": "100000000.00",
                "country": "China",
                "sector": "Technology",
                "industry": "Software",
                "exchange": params["exchange"],
            },
        ]
        return FakeResponse(rows)

    monkeypatch.setattr("src.services.stock_universe.requests.get", fake_get)

    stocks = USStockUniverse()._fetch_from_nasdaq_screener()

    assert calls == ["NYSE", "NASDAQ"]
    assert [s.ticker for s in stocks] == ["NYSEA", "NASDAQA"]
    assert stocks[0].market_cap_m == 1234.56789
    assert stocks[0].avg_volume == 1234567


def test_nasdaq_screener_accepts_tableonly_response_shape(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "table": {
                        "rows": [
                            {
                                "symbol": "AAPL",
                                "name": "Apple Inc. Common Stock",
                                "lastsale": "$293.32",
                                "volume": "2688934",
                                "marketCap": "4308095261920.00",
                                "country": "United States",
                                "sector": "Technology",
                                "industry": "Computer Manufacturing",
                            }
                        ]
                    }
                }
            }

    monkeypatch.setattr("src.services.stock_universe.requests.get", lambda *args, **kwargs: FakeResponse())

    stocks = USStockUniverse()._fetch_from_nasdaq_screener()

    assert len(stocks) == 1
    assert stocks[0].ticker == "AAPL"
    assert stocks[0].market_cap_m == 4308095.26192


def test_gold_digger_batches_us_price_downloads(monkeypatch):
    calls = []

    class FakeIndexableValues:
        def __init__(self, values):
            self._values = values

        def __getitem__(self, index):
            return self._values[index]

    class FakeClose:
        def __init__(self):
            self._values = [10.0] * 10 + [8.0] * 10 + [6.0] * 10
            self.empty = False
            self.iloc = FakeIndexableValues(self._values)

        def dropna(self):
            return self

        def __len__(self):
            return len(self._values)

    class FakeTickerFrame:
        def __getitem__(self, key):
            assert key == "Close"
            return FakeClose()

    class FakeColumns:
        def __init__(self, tickers):
            self._tickers = tickers

        def get_level_values(self, level):
            return self._tickers

    class FakeRaw:
        def __init__(self, batch):
            self._batch = batch
            self.columns = FakeColumns(batch)

        def __getitem__(self, ticker):
            if ticker not in self._batch:
                raise KeyError(ticker)
            return FakeTickerFrame()

    def make_close_frame(batch):
        return FakeRaw(batch)

    def fake_download(tickers, **kwargs):
        batch = tickers.split()
        calls.append(batch)
        return make_close_frame(batch)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=fake_download))
    monkeypatch.setattr("src.services.gold_digger._US_PRICE_BATCH_SIZE", 2)

    universe = [
        StockInfo(
            ticker=f"T{i}",
            name=f"Ticker {i}",
            sector="Technology",
            industry="Software",
            market_cap_m=100.0,
            avg_volume=100000,
            price=6.0,
        )
        for i in range(5)
    ]

    results = _fetch_us_garbage_stocks(
        DigConfig(min_price_decline_6m_pct=20.0),
        universe,
    )

    assert [len(batch) for batch in calls] == [2, 2, 1]
    assert [r.ticker for r in results] == ["T0", "T1", "T2", "T3", "T4"]
    assert all(r.price_change_6m_pct == -40.0 for r in results)


def test_gold_digger_applies_china_policy_weight_to_cn_candidates():
    stock = GarbageStockInfo(
        ticker="300750",
        name="宁德时代",
        market="cn",
        sector="新能源",
        industry="电池",
        market_cap_m=0,
        current_price=100,
        price_change_6m_pct=-30,
        price_change_1m_pct=3,
        pe_ratio=None,
        sector_median_pe=None,
        pe_discount_pct=None,
        analyst_count=0,
        held_by_institutions_pct=None,
        short_ratio=None,
    )
    themes = [
        InvestmentTheme(
            name="新能源国家战略",
            description="政策支持新能源产业链",
            keywords=["电池", "新能源"],
            relevant_sectors=["新能源"],
            market_regions=["cn"],
            sentiment="bullish",
        )
    ]

    unweighted = _build_candidate(stock, themes, DigConfig(china_policy_weight=0))
    weighted = _build_candidate(stock, themes, DigConfig(china_policy_weight=0.5))

    assert weighted.top_theme_score == unweighted.top_theme_score
    assert weighted.composite_score > unweighted.composite_score
