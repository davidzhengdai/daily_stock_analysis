# -*- coding: utf-8 -*-
"""
Moomoo OpenAPI realtime quote fetcher.

Moomoo quote APIs are served through a local OpenD gateway. This fetcher is
opt-in through REALTIME_SOURCE_PRIORITY=moomoo and reads the gateway endpoint
from MOOMOO_OPEND_HOST / MOOMOO_OPEND_PORT.
"""

import logging
import os
import socket
import threading
from typing import Optional

import pandas as pd

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS, is_bse_code
from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_float, safe_int
from .us_index_mapping import is_us_index_code, is_us_stock_code

logger = logging.getLogger(__name__)


def _is_hk_code(stock_code: str) -> bool:
    normalized = (stock_code or "").strip().upper()
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.endswith(".HK"):
        base = normalized[:-3]
        return base.isdigit() and 1 <= len(base) <= 5
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


def _is_us_code(stock_code: str) -> bool:
    normalized = (stock_code or "").strip().upper()
    return is_us_index_code(normalized) or is_us_stock_code(normalized)


def _to_moomoo_symbol(stock_code: str) -> Optional[str]:
    """Convert internal stock code to Moomoo Python SDK symbol format."""
    code = (stock_code or "").strip()
    upper = code.upper()

    if "." in upper:
        market, symbol = upper.split(".", 1)
        if market in {"US", "HK", "SH", "SZ"} and symbol:
            if market == "HK" and symbol.isdigit():
                return f"HK.{symbol.zfill(5)}"
            return f"{market}.{symbol}"
        if symbol == "US" and market:
            return f"US.{market}"
        if symbol == "HK" and market.isdigit():
            return f"HK.{market.zfill(5)}"
        if symbol in {"SH", "SS"} and market.isdigit():
            return f"SH.{market}"
        if symbol == "SZ" and market.isdigit():
            return f"SZ.{market}"

    if _is_us_code(upper):
        return f"US.{upper}"

    if _is_hk_code(upper):
        digits = upper[2:] if upper.startswith("HK") else upper
        return f"HK.{digits.zfill(5)}"

    if upper.isdigit() and len(upper) == 6:
        if is_bse_code(upper):
            logger.debug("[Moomoo] 暂不支持北交所代码格式: %s", stock_code)
            return None
        if upper.startswith(("5", "6", "9")):
            return f"SH.{upper}"
        return f"SZ.{upper}"

    return None


def _moomoo_host() -> str:
    return (os.getenv("MOOMOO_OPEND_HOST") or "127.0.0.1").strip() or "127.0.0.1"


def _moomoo_port() -> int:
    raw = (os.getenv("MOOMOO_OPEND_PORT") or "11111").strip()
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid MOOMOO_OPEND_PORT=%r, fallback to 11111", raw)
        return 11111


def _moomoo_connect_timeout() -> float:
    raw = (os.getenv("MOOMOO_OPEND_CONNECT_TIMEOUT") or "1.0").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("Invalid MOOMOO_OPEND_CONNECT_TIMEOUT=%r, fallback to 1.0", raw)
        return 1.0


def _moomoo_extended_time_enabled() -> bool:
    raw = (os.getenv("MOOMOO_EXTENDED_TIME") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class MoomooFetcher(BaseFetcher):
    """Moomoo OpenAPI realtime quote source."""

    name = "MoomooFetcher"
    priority = 5

    def __init__(self) -> None:
        self.host = _moomoo_host()
        self.port = _moomoo_port()
        self._ctx = None
        self._ctx_lock = threading.RLock()
        self._available: Optional[bool] = None
        self._endpoint_available: Optional[bool] = None

    def _is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import moomoo  # noqa: F401
            self._available = True
        except Exception as exc:
            logger.debug("[Moomoo] SDK 未安装或不可用: %s", exc)
            self._available = False
        return self._available

    def _opend_endpoint_reachable(self) -> bool:
        if self._endpoint_available is not None:
            return self._endpoint_available

        timeout = _moomoo_connect_timeout()
        if timeout <= 0:
            self._endpoint_available = True
            return True

        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                self._endpoint_available = True
                return True
        except OSError as exc:
            logger.warning(
                "[Moomoo] OpenD endpoint unreachable (%s:%s, timeout=%ss): %s. "
                "请确认 OpenD 已启动、已登录，并允许 Docker 容器访问该地址。",
                self.host,
                self.port,
                timeout,
                exc,
            )
            return False

    def _get_ctx(self):
        if not self._is_available():
            return None

        with self._ctx_lock:
            if self._ctx is not None:
                return self._ctx
            if not self._opend_endpoint_reachable():
                return None
            try:
                from moomoo import OpenQuoteContext

                self._ctx = OpenQuoteContext(host=self.host, port=self.port)
                return self._ctx
            except Exception as exc:
                logger.info(
                    "[Moomoo] 连接 OpenD 失败 (%s:%s): %s",
                    self.host,
                    self.port,
                    exc,
                )
                self._ctx = None
                return None

    def close(self) -> None:
        with self._ctx_lock:
            if self._ctx is not None:
                try:
                    self._ctx.close()
                except Exception:
                    pass
                self._ctx = None

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        symbol = _to_moomoo_symbol(stock_code)
        if symbol is None:
            logger.debug("[Moomoo] 无法转换代码: %s", stock_code)
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None

        snapshot_quote = self._get_snapshot_quote(stock_code, symbol)
        if snapshot_quote is not None:
            return snapshot_quote

        try:
            from moomoo import RET_OK, SubType

            ret_sub, message = ctx.subscribe(
                [symbol],
                [SubType.QUOTE],
                subscribe_push=False,
                extended_time=_moomoo_extended_time_enabled(),
            )
            if ret_sub != RET_OK:
                logger.info("[Moomoo] 订阅 %s 失败: %s", symbol, message)
                return None

            ret, data = ctx.get_stock_quote([symbol])
            if ret != RET_OK:
                logger.info("[Moomoo] get_stock_quote(%s) 失败: %s", symbol, data)
                return None
            if data is None or getattr(data, "empty", True):
                return None

            row = data.iloc[0]
            return self._row_to_quote(stock_code, row)
        except Exception as exc:
            logger.info("[Moomoo] 获取 %s 实时行情失败: %s", symbol, exc)
            return None

    def _get_snapshot_quote(self, stock_code: str, symbol: str) -> Optional[UnifiedRealtimeQuote]:
        ctx = self._get_ctx()
        if ctx is None:
            return None

        try:
            from moomoo import RET_OK

            ret, data = ctx.get_market_snapshot([symbol])
            if ret != RET_OK:
                logger.info("[Moomoo] get_market_snapshot(%s) 失败: %s", symbol, data)
                return None
            if data is None or getattr(data, "empty", True):
                return None
            quote = self._row_to_quote(stock_code, data.iloc[0])
            if quote is not None:
                logger.info(
                    "[Moomoo] %s 快照获取成功: 价格=%s, PE=%s, PB=%s",
                    symbol,
                    quote.price,
                    quote.pe_ratio,
                    quote.pb_ratio,
                )
            return quote
        except Exception as exc:
            logger.info("[Moomoo] get_market_snapshot(%s) 异常，回退实时订阅行情: %s", symbol, exc)
            return None

    def _row_to_quote(self, stock_code: str, row) -> Optional[UnifiedRealtimeQuote]:
        price = safe_float(row.get("last_price"))
        if price is None or price <= 0:
            return None

        prev_close = safe_float(row.get("prev_close_price"))
        change_amount = safe_float(row.get("change_val"))
        change_pct = safe_float(row.get("change_rate"))
        if change_amount is None and prev_close and prev_close > 0:
            change_amount = round(price - prev_close, 4)
        if change_pct is None and prev_close and prev_close > 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        return UnifiedRealtimeQuote(
            code=stock_code,
            name=str(row.get("name") or ""),
            source=RealtimeSource.MOOMOO,
            price=price,
            change_pct=change_pct,
            change_amount=change_amount,
            volume=safe_int(row.get("volume")),
            amount=safe_float(row.get("turnover")),
            volume_ratio=safe_float(row.get("volume_ratio")),
            turnover_rate=safe_float(row.get("turnover_rate")),
            amplitude=safe_float(row.get("amplitude")),
            open_price=safe_float(row.get("open_price")),
            high=safe_float(row.get("high_price")),
            low=safe_float(row.get("low_price")),
            pre_close=prev_close,
            pe_ratio=safe_float(row.get("pe_ttm_ratio") or row.get("pe_ratio")),
            pb_ratio=safe_float(row.get("pb_ratio")),
            total_mv=safe_float(row.get("total_market_val")),
            circ_mv=safe_float(row.get("circular_market_val") or row.get("outstanding_market_val")),
            high_52w=safe_float(row.get("highest52weeks_price")),
            low_52w=safe_float(row.get("lowest52weeks_price")),
        )

    def get_financial_snapshot(self, stock_code: str) -> Optional[dict]:
        """Return Moomoo market snapshot fields useful for fundamentals/debugging."""
        symbol = _to_moomoo_symbol(stock_code)
        if symbol is None:
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None

        try:
            from moomoo import RET_OK

            ret, data = ctx.get_market_snapshot([symbol])
            if ret != RET_OK or data is None or getattr(data, "empty", True):
                return None
            row = data.iloc[0]
            fields = [
                "code", "name", "update_time", "last_price",
                "listing_date", "lot_size", "issued_shares",
                "outstanding_shares", "total_market_val", "circular_market_val",
                "net_asset", "net_profit", "earning_per_share",
                "net_asset_per_share", "pe_ratio", "pe_ttm_ratio",
                "pb_ratio", "dividend_ttm", "dividend_ratio_ttm",
                "highest52weeks_price", "lowest52weeks_price",
                "pre_price", "pre_change_rate", "after_price",
                "after_change_rate", "overnight_price", "overnight_change_rate",
            ]
            result = {
                field: row.get(field)
                for field in fields
                if field in row.index and pd.notna(row.get(field))
            }
            result["source"] = RealtimeSource.MOOMOO.value
            return result
        except Exception as exc:
            logger.info("[Moomoo] financial snapshot failed for %s: %s", symbol, exc)
            return None

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        symbol = _to_moomoo_symbol(stock_code)
        if symbol is None:
            raise DataFetchError(f"MoomooFetcher 不支持该代码格式: {stock_code}")

        ctx = self._get_ctx()
        if ctx is None:
            raise DataFetchError("Moomoo OpenD 不可用或 SDK 未安装")

        try:
            from moomoo import AuType, KLType, RET_OK

            ret, data, page_req_key = ctx.request_history_kline(
                symbol,
                start=start_date,
                end=end_date,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
                max_count=1000,
            )
            if ret != RET_OK:
                raise DataFetchError(str(data))

            frames = []
            if data is not None and not data.empty:
                frames.append(data)

            while page_req_key is not None:
                ret, data, page_req_key = ctx.request_history_kline(
                    symbol,
                    start=start_date,
                    end=end_date,
                    ktype=KLType.K_DAY,
                    autype=AuType.QFQ,
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret != RET_OK:
                    raise DataFetchError(str(data))
                if data is not None and not data.empty:
                    frames.append(data)

            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        except DataFetchError:
            raise
        except Exception as exc:
            raise DataFetchError(f"Moomoo request_history_kline({symbol}) 失败: {exc}") from exc

    def _normalize_data(self, raw_data: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if raw_data is None or raw_data.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        df = raw_data.copy()
        rename_map = {
            "time_key": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "turnover": "amount",
            "change_rate": "pct_chg",
        }
        df = df.rename(columns=rename_map)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
        if "amount" not in df.columns:
            df["amount"] = None
        if "pct_chg" not in df.columns:
            df["pct_chg"] = None
        for col in STANDARD_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[STANDARD_COLUMNS]
