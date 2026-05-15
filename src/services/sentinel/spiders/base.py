# -*- coding: utf-8 -*-
import logging
from abc import ABC, abstractmethod
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import SentinelConfig
    from ..models import RawArticle

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_EMPTY = 3


class SpiderBase(ABC):
    name: str = ""
    category: str = "finance"      # finance | policy | industry | breaking | macro
    interval_minutes: int = 30
    max_items_per_run: int = 50
    timeout_seconds: int = 10
    language: str = "zh"

    def __init__(self) -> None:
        self._consecutive_empty: int = 0
        self._consecutive_errors: int = 0

    @abstractmethod
    def fetch(self) -> "List[RawArticle]":
        """Fetch raw articles from the source. Must handle all network errors internally."""
        ...

    def is_enabled(self, config: "SentinelConfig") -> bool:
        if config.enabled_spiders.strip().lower() == "all":
            return True
        names = [s.strip() for s in config.enabled_spiders.split(",")]
        return self.name in names

    def is_healthy(self) -> bool:
        return self._consecutive_empty < _MAX_CONSECUTIVE_EMPTY

    def _record_result(self, count: int) -> None:
        if count == 0:
            self._consecutive_empty += 1
            if self._consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                logger.warning(
                    "[%s] consecutive empty runs=%d — marking degraded",
                    self.name, self._consecutive_empty,
                )
        else:
            self._consecutive_empty = 0
            self._consecutive_errors = 0

    def _record_error(self) -> None:
        self._consecutive_errors += 1
        self._consecutive_empty += 1

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
