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
        if not include_closed_markets and not self._is_market_open_for_code(code_norm):
            logger.debug("[RealtimeQuote] %s 所属市场闭市，跳过实时行情刷新", code_norm)
            return None
        try:
            quote = _get_shared_fetcher_manager().get_realtime_quote(
                code_norm,
                log_final_failure=False,
            )
            if quote is None:
                return None
            data = quote.to_dict() if hasattr(quote, "to_dict") else {}
            if not isinstance(data, dict):
                return None
            data["fetched_at"] = datetime.utcnow().isoformat()
            return data
        except Exception as exc:
            logger.debug("[RealtimeQuote] %s 获取失败: %s", code_norm, exc)
            return None

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
