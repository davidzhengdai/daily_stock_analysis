# -*- coding: utf-8 -*-
"""Shared realtime quote helpers for UI list enrichment."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_shared_fetcher_manager = None


def _get_shared_fetcher_manager():
    global _shared_fetcher_manager
    if _shared_fetcher_manager is None:
        from data_provider.base import DataFetcherManager
        _shared_fetcher_manager = DataFetcherManager()
    return _shared_fetcher_manager


class RealtimeQuoteService:
    """Fetch realtime quotes for list views with per-item fault isolation."""

    def get_quote(
        self,
        code: str,
        *,
        include_closed_markets: bool = False,
    ) -> Optional[Dict[str, Any]]:
        code_norm = (code or "").strip().upper()
        if not code_norm:
            return None
        market_open = self._is_market_open_for_code(code_norm)
        if not include_closed_markets and not market_open:
            logger.debug("[RealtimeQuote] %s 所属市场闭市，跳过实时行情刷新", code_norm)
            return self._get_cached_quote(code_norm, market_open=False)
        try:
            quote = _get_shared_fetcher_manager().get_realtime_quote(
                code_norm,
                log_final_failure=False,
            )
            if quote is None:
                return self._get_cached_quote(code_norm, market_open=market_open)
            data = quote.to_dict() if hasattr(quote, "to_dict") else {}
            if not isinstance(data, dict):
                return self._get_cached_quote(code_norm, market_open=market_open)
            data["fetched_at"] = datetime.utcnow().isoformat()
            data["is_cached"] = False
            data["market_open"] = market_open
            self._save_cached_quote(code_norm, data)
            return data
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 获取失败: %s", code_norm, exc)
            return self._get_cached_quote(code_norm, market_open=market_open)

    def get_quotes(
        self,
        codes: Iterable[str],
        *,
        include_closed_markets: bool = False,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        quotes: Dict[str, Optional[Dict[str, Any]]] = {}
        for code in codes:
            code_norm = (code or "").strip().upper()
            if not code_norm or code_norm in quotes:
                continue
            quotes[code_norm] = self.get_quote(
                code_norm,
                include_closed_markets=include_closed_markets,
            )
        return quotes

    @staticmethod
    def _is_market_open_for_code(code: str) -> bool:
        try:
            from src.services.simtrade.auto_trade_service import _is_cn_market_open, _is_us_market_open
            from src.services.simtrade.signal_service import SignalService

            market = SignalService._infer_market(code)
            if market == "CN":
                return _is_cn_market_open()
            if market == "US":
                return _is_us_market_open()
            return True
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 市场状态判断失败，允许刷新: %s", code, exc)
            return True

    def _get_cached_quote(self, code: str, *, market_open: bool) -> Optional[Dict[str, Any]]:
        try:
            from src.storage import DatabaseManager, RealtimeQuoteCache

            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                row = session.get(RealtimeQuoteCache, code)
                if row is None:
                    return self._get_latest_daily_quote(code, market_open=market_open)
                data = row.to_dict()
                data["is_cached"] = True
                data["market_open"] = market_open
                data["cache_updated_at"] = row.updated_at.isoformat() if row.updated_at else None
                return data
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 缓存读取失败: %s", code, exc)
            return None

    def _get_latest_daily_quote(self, code: str, *, market_open: bool) -> Optional[Dict[str, Any]]:
        try:
            from sqlalchemy import desc, select
            from src.storage import DatabaseManager, StockDaily

            db = DatabaseManager.get_instance()
            row = None
            with db.get_session() as session:
                row = session.execute(
                    select(StockDaily)
                    .where(StockDaily.code == code)
                    .order_by(desc(StockDaily.date))
                    .limit(1)
                ).scalar_one_or_none()
            if row is None and not market_open:
                self._load_recent_daily_cache(code)
                with db.get_session() as session:
                    row = session.execute(
                        select(StockDaily)
                        .where(StockDaily.code == code)
                        .order_by(desc(StockDaily.date))
                        .limit(1)
                    ).scalar_one_or_none()
            if row is None or row.close is None:
                return None
            fetched_at = row.updated_at.isoformat() if row.updated_at else (
                row.date.isoformat() if row.date else None
            )
            return {
                "code": code,
                "source": f"daily:{row.data_source or 'cache'}",
                "price": row.close,
                "change_pct": row.pct_chg,
                "volume": row.volume,
                "amount": row.amount,
                "fetched_at": fetched_at,
                "is_cached": True,
                "market_open": market_open,
                "daily_date": row.date.isoformat() if row.date else None,
            }
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 日线缓存读取失败: %s", code, exc)
            return None

    def _load_recent_daily_cache(self, code: str) -> None:
        try:
            from data_provider.base import DataFetcherManager
            from src.storage import DatabaseManager

            df, source = DataFetcherManager().get_daily_data(code, days=30)
            if df is None or df.empty:
                return
            DatabaseManager.get_instance().save_daily_data(
                df,
                code,
                data_source=f"quote-fallback:{source}",
            )
            logger.info("[RealtimeQuote] %s 闭市报价日线兜底缓存完成: source=%s rows=%d", code, source, len(df))
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 闭市报价日线兜底失败: %s", code, exc)

    def _save_cached_quote(self, code: str, data: Dict[str, Any]) -> None:
        try:
            import json
            from datetime import datetime
            from src.storage import DatabaseManager, RealtimeQuoteCache

            fetched_at_raw = data.get("fetched_at")
            fetched_at = datetime.utcnow()
            if isinstance(fetched_at_raw, str) and fetched_at_raw:
                try:
                    fetched_at = datetime.fromisoformat(fetched_at_raw)
                except ValueError:
                    fetched_at = datetime.utcnow()
            payload = dict(data)
            db = DatabaseManager.get_instance()
            with db.session_scope() as session:
                row = session.get(RealtimeQuoteCache, code)
                if row is None:
                    row = RealtimeQuoteCache(code=code, quote_json="{}", fetched_at=fetched_at)
                    session.add(row)
                row.quote_json = json.dumps(payload, ensure_ascii=False)
                row.fetched_at = fetched_at
                row.source = str(data.get("source") or "")[:50] or None
                row.updated_at = datetime.utcnow()
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 缓存写入失败: %s", code, exc)
