# -*- coding: utf-8 -*-

import os
import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.moomoo_fetcher import MoomooFetcher, _to_moomoo_symbol
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


class TestMoomooSymbolConversion(unittest.TestCase):
    def test_us_symbol(self):
        self.assertEqual(_to_moomoo_symbol("AAPL"), "US.AAPL")
        self.assertEqual(_to_moomoo_symbol("US.NVDA"), "US.NVDA")
        self.assertEqual(_to_moomoo_symbol("NVDA.US"), "US.NVDA")

    def test_hk_symbol(self):
        self.assertEqual(_to_moomoo_symbol("HK00700"), "HK.00700")
        self.assertEqual(_to_moomoo_symbol("00700"), "HK.00700")
        self.assertEqual(_to_moomoo_symbol("700.HK"), "HK.00700")

    def test_a_share_symbol(self):
        self.assertEqual(_to_moomoo_symbol("600519"), "SH.600519")
        self.assertEqual(_to_moomoo_symbol("000001"), "SZ.000001")
        self.assertEqual(_to_moomoo_symbol("300750.SZ"), "SZ.300750")

    def test_bse_not_supported(self):
        self.assertIsNone(_to_moomoo_symbol("920748"))


class TestMoomooFetcherMocked(unittest.TestCase):
    @contextmanager
    def _install_mock_moomoo(self, ctx):
        module = types.ModuleType("moomoo")
        module.RET_OK = 0
        module.SubType = types.SimpleNamespace(QUOTE="QUOTE")
        module.KLType = types.SimpleNamespace(K_DAY="K_DAY")
        module.AuType = types.SimpleNamespace(QFQ="QFQ")
        module.OpenQuoteContext = MagicMock(return_value=ctx)
        with patch.dict("sys.modules", {"moomoo": module}), patch(
            "data_provider.moomoo_fetcher.socket.create_connection"
        ) as mock_connect:
            mock_connect.return_value.__enter__.return_value = None
            yield module

    def test_get_realtime_quote_prefers_market_snapshot(self):
        ctx = MagicMock()
        ctx.get_market_snapshot.return_value = (
            0,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "APPLE",
                        "last_price": 188.38,
                        "open_price": 193.89,
                        "high_price": 199.88,
                        "low_price": 187.34,
                        "prev_close_price": 203.19,
                        "volume": 125910913,
                        "turnover": 24244730000.0,
                        "turnover_rate": 0.838,
                        "amplitude": 6.172,
                        "pe_ttm_ratio": 28.5,
                        "pb_ratio": 42.0,
                        "total_market_val": 2800000000000.0,
                        "circular_market_val": 2700000000000.0,
                        "highest52weeks_price": 260.0,
                        "lowest52weeks_price": 160.0,
                    }
                ]
            ),
        )

        with self._install_mock_moomoo(ctx):
            fetcher = MoomooFetcher()
            quote = fetcher.get_realtime_quote("AAPL")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.source, RealtimeSource.MOOMOO)
        self.assertEqual(quote.code, "AAPL")
        self.assertEqual(quote.name, "APPLE")
        self.assertEqual(quote.price, 188.38)
        self.assertEqual(quote.volume, 125910913)
        self.assertEqual(quote.turnover_rate, 0.838)
        self.assertEqual(quote.pe_ratio, 28.5)
        self.assertEqual(quote.pb_ratio, 42.0)
        self.assertEqual(quote.total_mv, 2800000000000.0)
        self.assertEqual(quote.circ_mv, 2700000000000.0)
        self.assertEqual(quote.high_52w, 260.0)
        self.assertAlmostEqual(quote.change_pct, -7.29, places=2)
        ctx.get_market_snapshot.assert_called_once_with(["US.AAPL"])
        ctx.subscribe.assert_not_called()
        ctx.get_stock_quote.assert_not_called()

    def test_get_realtime_quote_falls_back_to_subscription_quote(self):
        ctx = MagicMock()
        ctx.get_market_snapshot.return_value = (-1, "snapshot unavailable")
        ctx.subscribe.return_value = (0, "")
        ctx.get_stock_quote.return_value = (
            0,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "APPLE",
                        "last_price": 188.38,
                        "open_price": 193.89,
                        "high_price": 199.88,
                        "low_price": 187.34,
                        "prev_close_price": 203.19,
                        "volume": 125910913,
                        "turnover": 24244730000.0,
                    }
                ]
            ),
        )

        with self._install_mock_moomoo(ctx):
            fetcher = MoomooFetcher()
            quote = fetcher.get_realtime_quote("AAPL")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.source, RealtimeSource.MOOMOO)
        ctx.subscribe.assert_called_once()
        ctx.get_stock_quote.assert_called_once_with(["US.AAPL"])

    def test_subscribe_failure_returns_none(self):
        ctx = MagicMock()
        ctx.get_market_snapshot.return_value = (-1, "snapshot unavailable")
        ctx.subscribe.return_value = (-1, "no quota")

        with self._install_mock_moomoo(ctx):
            fetcher = MoomooFetcher()
            quote = fetcher.get_realtime_quote("AAPL")

        self.assertIsNone(quote)
        ctx.get_stock_quote.assert_not_called()

    def test_get_financial_snapshot(self):
        ctx = MagicMock()
        ctx.get_market_snapshot.return_value = (
            0,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "name": "APPLE",
                        "last_price": 188.38,
                        "issued_shares": 15000000000,
                        "outstanding_shares": 14500000000,
                        "total_market_val": 2800000000000.0,
                        "net_profit": 97000000000.0,
                        "earning_per_share": 6.43,
                        "pe_ttm_ratio": 28.5,
                        "pb_ratio": 42.0,
                        "dividend_ratio_ttm": 0.51,
                    }
                ]
            ),
        )

        with self._install_mock_moomoo(ctx):
            fetcher = MoomooFetcher()
            data = fetcher.get_financial_snapshot("AAPL")

        self.assertIsNotNone(data)
        self.assertEqual(data["source"], "moomoo")
        self.assertEqual(data["issued_shares"], 15000000000)
        self.assertEqual(data["pe_ttm_ratio"], 28.5)

    def test_get_daily_data_uses_history_kline(self):
        ctx = MagicMock()
        ctx.request_history_kline.return_value = (
            0,
            pd.DataFrame(
                [
                    {
                        "code": "US.AAPL",
                        "time_key": "2026-05-07 00:00:00",
                        "open": 180.0,
                        "high": 190.0,
                        "low": 179.0,
                        "close": 188.0,
                        "volume": 1000000,
                        "turnover": 188000000.0,
                        "change_rate": 2.5,
                    }
                ]
            ),
            None,
        )

        with self._install_mock_moomoo(ctx):
            fetcher = MoomooFetcher()
            df = fetcher.get_daily_data("AAPL", start_date="2026-05-01", end_date="2026-05-08")

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["close"], 188.0)
        self.assertEqual(df.iloc[0]["amount"], 188000000.0)
        self.assertEqual(df.iloc[0]["pct_chg"], 2.5)
        self.assertIn("ma5", df.columns)
        ctx.request_history_kline.assert_called_once()

    def test_unreachable_opend_returns_none_without_openquote_retries(self):
        module = types.ModuleType("moomoo")
        module.OpenQuoteContext = MagicMock()

        with patch.dict("sys.modules", {"moomoo": module}), patch(
            "data_provider.moomoo_fetcher.socket.create_connection",
            side_effect=ConnectionRefusedError("connection refused"),
        ):
            fetcher = MoomooFetcher()
            quote = fetcher.get_realtime_quote("AAPL")

        self.assertIsNone(quote)
        module.OpenQuoteContext.assert_not_called()


class TestMoomooManagerIntegration(unittest.TestCase):
    def test_cn_priority_uses_moomoo_source(self):
        from data_provider.base import DataFetcherManager

        moomoo_quote = UnifiedRealtimeQuote(
            code="600519",
            name="贵州茅台",
            source=RealtimeSource.MOOMOO,
            price=1668.0,
            turnover_rate=0.5,
            pe_ratio=28.0,
        )
        mock_moomoo = MagicMock()
        mock_moomoo.name = "MoomooFetcher"
        mock_moomoo.get_realtime_quote.return_value = moomoo_quote

        manager = DataFetcherManager(fetchers=[mock_moomoo])
        cfg = types.SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority="moomoo,tencent",
        )

        with patch("src.config.get_config", return_value=cfg):
            result = manager.get_realtime_quote("600519")

        self.assertEqual(result, moomoo_quote)
        mock_moomoo.get_realtime_quote.assert_called_once_with("600519")


if __name__ == "__main__":
    unittest.main()
