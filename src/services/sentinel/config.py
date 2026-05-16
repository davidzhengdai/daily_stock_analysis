# -*- coding: utf-8 -*-
import os
from dataclasses import dataclass, field


@dataclass
class SentinelConfig:
    enabled: bool = False
    db_path: str = "data/sentinel.db"
    rsshub_base_url: str = "http://localhost:1200"
    rsshub_timeout: int = 15
    cycle_interval_minutes: int = 30
    analysis_interval_hours: int = 4
    llm_batch_size: int = 20
    llm_max_per_cycle: int = 200
    trigger_confidence: float = 0.70
    cycle_timeout_minutes: int = 10
    max_spider_concurrency: int = 5
    request_delay_seconds: float = 2.0
    webhook_secret: str = ""
    redis_url: str = ""
    enabled_spiders: str = "all"
    trading_hours_boost: bool = True
    watched_stocks_boost: bool = True

    @classmethod
    def from_env(cls) -> "SentinelConfig":
        def _bool(key: str, default: bool) -> bool:
            v = os.getenv(key, "").strip().lower()
            if not v:
                return default
            return v in ("1", "true", "yes", "on")

        def _int(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, str(default)))
            except (ValueError, TypeError):
                return default

        def _float(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, str(default)))
            except (ValueError, TypeError):
                return default

        return cls(
            enabled=_bool("SENTINEL_ENABLED", False),
            db_path=os.getenv("SENTINEL_DB_PATH", "data/sentinel.db"),
            rsshub_base_url=os.getenv("SENTINEL_RSSHUB_BASE_URL", "http://localhost:1200").rstrip("/"),
            rsshub_timeout=_int("SENTINEL_RSSHUB_TIMEOUT", 15),
            cycle_interval_minutes=_int("SENTINEL_CYCLE_INTERVAL_MINUTES", 30),
            analysis_interval_hours=_int("SENTINEL_ANALYSIS_INTERVAL_HOURS", 4),
            llm_batch_size=_int("SENTINEL_LLM_BATCH_SIZE", 20),
            llm_max_per_cycle=_int("SENTINEL_LLM_MAX_PER_CYCLE", 200),
            trigger_confidence=_float("SENTINEL_TRIGGER_CONFIDENCE", 0.70),
            cycle_timeout_minutes=_int("SENTINEL_CYCLE_TIMEOUT_MINUTES", 10),
            max_spider_concurrency=_int("SENTINEL_MAX_SPIDER_CONCURRENCY", 5),
            request_delay_seconds=_float("SENTINEL_REQUEST_DELAY_SECONDS", 2.0),
            webhook_secret=os.getenv("SENTINEL_WEBHOOK_SECRET", ""),
            redis_url=os.getenv("SENTINEL_REDIS_URL", ""),
            enabled_spiders=os.getenv("SENTINEL_ENABLED_SPIDERS", "all"),
            trading_hours_boost=_bool("SENTINEL_TRADING_HOURS_BOOST", True),
            watched_stocks_boost=_bool("SENTINEL_WATCHED_STOCKS_BOOST", True),
        )
