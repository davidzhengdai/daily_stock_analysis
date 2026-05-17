# -*- coding: utf-8 -*-
"""A-share daily data manager for batch discovery jobs."""

import logging
from typing import List

from data_provider.base import BaseFetcher, DataFetcherManager

logger = logging.getLogger(__name__)


def build_cn_screening_data_manager() -> DataFetcherManager:
    """
    Build a CN daily-data manager tuned for scanner-style batch jobs.

    The global manager prefers the freshest Eastmoney/efinance path, but in
    batch discovery a slow failing endpoint can stall every A-share candidate.
    Baostock is T+1, but stable and enough for candidate preselection.
    """
    fetchers: List[BaseFetcher] = []
    for priority, (module_name, class_name) in enumerate(
        [
            ("data_provider.baostock_fetcher", "BaostockFetcher"),
            ("data_provider.akshare_fetcher", "AkshareFetcher"),
            ("data_provider.tushare_fetcher", "TushareFetcher"),
            ("data_provider.pytdx_fetcher", "PytdxFetcher"),
            ("data_provider.efinance_fetcher", "EfinanceFetcher"),
            ("data_provider.yfinance_fetcher", "YfinanceFetcher"),
        ]
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            fetcher = getattr(module, class_name)()
            fetcher.priority = priority
            fetchers.append(fetcher)
        except Exception as exc:
            logger.debug("CN screening fetcher unavailable %s.%s: %s", module_name, class_name, exc)

    if not fetchers:
        return DataFetcherManager()
    return DataFetcherManager(fetchers=fetchers)
