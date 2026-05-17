import sys
from types import SimpleNamespace

import pandas as pd

from src.schemas.gold_digger import DigConfig, GarbageStockInfo, GoldCandidate, InvestmentTheme, ThemeMatch
from src.schemas.scanner import CandidateStock, FundScore, ScanConfig, StockInfo, TechScore
from src.services.ai_preselector import (
    ai_preselect_gold_candidates,
    ai_preselect_scanner_candidates,
)
from src.services.screening_engine import ScreeningEngine, _select_market_balanced
from src.services.gold_digger import _build_candidate, _fetch_cn_garbage_stocks, _fetch_us_garbage_stocks
from src.services.stock_universe import CNStockUniverse, USStockUniverse


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


def test_cn_universe_filters_indices_before_limit(monkeypatch):
    rows = [
        {"code": "000001", "name": "上证综合指数", "industry": "", "market": ""},
        {"code": "000002", "name": "上证A股指数", "industry": "", "market": ""},
        {"code": "000004", "name": "国华网安", "industry": "软件", "market": "SZ"},
        {"code": "600519", "name": "贵州茅台", "industry": "白酒", "market": "SH"},
    ]
    monkeypatch.setattr(CNStockUniverse, "_fetch_rows", lambda self: rows)

    stocks = CNStockUniverse().get_all(limit=2)

    assert [stock.ticker for stock in stocks] == ["000004", "600519"]


def test_scanner_cn_tier2_uses_data_fetcher_fallback(monkeypatch):
    calls = []

    class FakeManager:
        def get_daily_data(self, code, days=30):
            calls.append((code, days))
            return _make_price_history(), "FakeFetcher"

    monkeypatch.setattr(
        "src.services.cn_daily_data.build_cn_screening_data_manager",
        lambda: FakeManager(),
    )

    engine = ScreeningEngine()
    engine._analyzer = SimpleNamespace(
        analyze=lambda df, ticker: SimpleNamespace(
            signal_score=77,
            trend_status=SimpleNamespace(value="多头排列"),
            buy_signal=SimpleNamespace(value="买入"),
            rsi_12=55.0,
            macd_status=SimpleNamespace(value="金叉"),
            volume_status=SimpleNamespace(value="放量"),
        )
    )
    stocks = [StockInfo(ticker="600519", name="贵州茅台", market="cn")]

    result = engine._tier2_cn_technical_screen(stocks, ScanConfig(markets=["cn"]))

    assert calls == [("600519", 140)]
    assert len(result) == 1
    assert result[0].stock.ticker == "600519"


def test_gold_digger_cn_garbage_uses_data_fetcher_fallback(monkeypatch):
    class FakeManager:
        def get_daily_data(self, code, days=30):
            return _make_price_history(), "FakeFetcher"

    monkeypatch.setattr(
        "src.services.cn_daily_data.build_cn_screening_data_manager",
        lambda: FakeManager(),
    )

    results = _fetch_cn_garbage_stocks(
        DigConfig(min_price_decline_6m_pct=20.0),
        [{"code": "600519", "name": "贵州茅台", "industry": "白酒"}],
    )

    assert [stock.ticker for stock in results] == ["600519"]
    assert results[0].price_change_6m_pct < -20


def test_scanner_market_balanced_selection_reserves_a_share_slots():
    items = (
        [{"ticker": f"US{i}", "market": "us"} for i in range(8)]
        + [{"ticker": f"CN{i}", "market": "cn"} for i in range(4)]
    )

    selected = _select_market_balanced(
        items,
        6,
        ["us", "cn"],
        lambda item: item["market"],
    )

    assert len(selected) == 6
    assert sum(1 for item in selected if item["market"] == "cn") == 3
    assert sum(1 for item in selected if item["market"] == "us") == 3


def test_scanner_market_balanced_selection_fills_unused_market_quota():
    items = (
        [{"ticker": f"US{i}", "market": "us"} for i in range(8)]
        + [{"ticker": "CN0", "market": "cn"}]
    )

    selected = _select_market_balanced(
        items,
        6,
        ["us", "cn"],
        lambda item: item["market"],
    )

    assert len(selected) == 6
    assert sum(1 for item in selected if item["market"] == "cn") == 1
    assert sum(1 for item in selected if item["market"] == "us") == 5


def test_scanner_tier4_preserves_a_share_candidates_after_sector_diversity():
    engine = ScreeningEngine()
    candidates = []

    for idx in range(12):
        candidates.append(_make_fund_score(f"US{idx}", "us", f"US Sector {idx}", 100 - idx))
    for idx in range(12):
        candidates.append(_make_fund_score(f"CN{idx}", "cn", "A股", 70 - idx))

    result = engine.tier4_sector_filter(
        candidates,
        ScanConfig(markets=["us", "cn"], max_tier5_stocks=10),
    )

    assert len(result) == 10
    assert sum(1 for item in result if item.fund.tech.stock.market == "cn") == 5
    assert sum(1 for item in result if item.fund.tech.stock.market == "us") == 5


def test_ai_preselector_reorders_scanner_candidates_and_fills_missing_tickers():
    class FakeAnalyzer:
        def generate_text(self, *args, **kwargs):
            return '[{"ticker":"CN1","score":95},{"ticker":"US0","score":90}]'

    candidates = [
        CandidateStock(_make_fund_score("US0", "us", "Technology", 90), sector_rank=1),
        CandidateStock(_make_fund_score("US1", "us", "Industrials", 88), sector_rank=1),
        CandidateStock(_make_fund_score("CN1", "cn", "新能源", 80), sector_rank=1),
        CandidateStock(_make_fund_score("CN2", "cn", "消费", 78), sector_rank=1),
    ]

    selected = ai_preselect_scanner_candidates(candidates, 3, FakeAnalyzer())

    assert [item.ticker for item in selected] == ["CN1", "US0", "US1"]


def test_ai_preselector_falls_back_to_rule_order_on_invalid_json():
    class FakeAnalyzer:
        def generate_text(self, *args, **kwargs):
            return "not json"

    candidates = [
        CandidateStock(_make_fund_score("US0", "us", "Technology", 90), sector_rank=1),
        CandidateStock(_make_fund_score("US1", "us", "Industrials", 88), sector_rank=1),
        CandidateStock(_make_fund_score("CN1", "cn", "新能源", 80), sector_rank=1),
    ]

    selected = ai_preselect_scanner_candidates(candidates, 2, FakeAnalyzer())

    assert [item.ticker for item in selected] == ["US0", "US1"]


def test_ai_preselector_recovers_tickers_from_non_json_text():
    class FakeAnalyzer:
        def generate_text(self, *args, **kwargs):
            return "I would rank these as: 1. CN1 for policy fit, 2. US1 for momentum."

    candidates = [
        CandidateStock(_make_fund_score("US0", "us", "Technology", 90), sector_rank=1),
        CandidateStock(_make_fund_score("CN1", "cn", "新能源", 80), sector_rank=1),
        CandidateStock(_make_fund_score("US1", "us", "Industrials", 88), sector_rank=1),
    ]

    selected = ai_preselect_scanner_candidates(candidates, 2, FakeAnalyzer())

    assert [item.ticker for item in selected] == ["CN1", "US1"]


def test_ai_preselector_keeps_scanner_market_balance_after_ai_ranking():
    class FakeAnalyzer:
        def generate_text(self, *args, **kwargs):
            return '[{"ticker":"US0"},{"ticker":"US1"},{"ticker":"US2"}]'

    candidates = [
        CandidateStock(_make_fund_score("US0", "us", "Technology", 90), sector_rank=1),
        CandidateStock(_make_fund_score("US1", "us", "Industrials", 88), sector_rank=1),
        CandidateStock(_make_fund_score("US2", "us", "Healthcare", 86), sector_rank=1),
        CandidateStock(_make_fund_score("CN1", "cn", "新能源", 80), sector_rank=1),
        CandidateStock(_make_fund_score("CN2", "cn", "消费", 78), sector_rank=1),
    ]

    selected = ai_preselect_scanner_candidates(
        candidates,
        4,
        FakeAnalyzer(),
        market_balancer=lambda items, limit: _select_market_balanced(
            items,
            limit,
            ["us", "cn"],
            lambda item: item.fund.tech.stock.market,
        ),
    )

    assert len(selected) == 4
    assert sum(1 for item in selected if item.fund.tech.stock.market == "cn") == 2


def test_ai_preselector_selects_gold_candidates_from_ranked_json():
    class FakeAnalyzer:
        def generate_text(self, *args, **kwargs):
            return '{"selected":[{"ticker":"G2","score":91},{"ticker":"G0","score":88}]}'

    candidates = [
        _make_gold_candidate("G0", 90),
        _make_gold_candidate("G1", 88),
        _make_gold_candidate("G2", 70),
    ]

    selected = ai_preselect_gold_candidates(candidates, 2, FakeAnalyzer())

    assert [item.ticker for item in selected] == ["G2", "G0"]


def _make_fund_score(ticker: str, market: str, sector: str, score: float) -> FundScore:
    stock = StockInfo(
        ticker=ticker,
        name=ticker,
        sector=sector,
        industry=sector,
        market_cap_m=1000.0,
        avg_volume=1000000,
        price=10.0,
        market=market,
    )
    tech = TechScore(
        stock=stock,
        signal_score=int(score),
        trend_status="多头排列",
        buy_signal="买入",
        rsi_12=50.0,
        macd_status="金叉",
        volume_status="放量",
    )
    return FundScore(
        tech=tech,
        pe_ratio=None,
        forward_pe=None,
        roe=None,
        revenue_growth=None,
        profit_margin=None,
        debt_to_equity=None,
        fundamental_score=50.0,
        composite_score=score,
    )


def _make_gold_candidate(ticker: str, score: float) -> GoldCandidate:
    stock = GarbageStockInfo(
        ticker=ticker,
        name=ticker,
        market="us",
        sector="Technology",
        industry="Software",
        market_cap_m=100.0,
        current_price=5.0,
        price_change_6m_pct=-40.0,
        price_change_1m_pct=5.0,
        pe_ratio=8.0,
        sector_median_pe=16.0,
        pe_discount_pct=-50.0,
        analyst_count=0,
        held_by_institutions_pct=10.0,
        short_ratio=None,
    )
    return GoldCandidate(
        stock=stock,
        value_score=score,
        momentum_reversal_score=score,
        theme_matches=[ThemeMatch("AI recovery", 80, "theme fit")],
        top_theme_score=80,
        institutional_score=50,
        composite_score=score,
    )


def _make_price_history():
    rows = 30
    closes = [10.0 - (4.0 * idx / (rows - 1)) for idx in range(rows)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="D"),
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [1000000 + idx for idx in range(rows)],
        }
    )
