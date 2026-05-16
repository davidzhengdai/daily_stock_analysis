# -*- coding: utf-8 -*-
"""LLM-based news classification pipeline for the News Sentinel.

Batch-processes unclassified news items (priority IS NULL) using the existing
GeminiAnalyzer.generate_text() interface. Injectable analyzer allows offline
unit-testing without importing the full LLM stack.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from .config import SentinelConfig
from .models import CycleSummary
from .store import NewsStore
from .ttl import compute_expires_at

logger = logging.getLogger(__name__)

# Government / authoritative sources that receive a priority boost (+1 capped at 5)
_GOV_SOURCES = frozenset({
    "中国政府网", "新华社", "人民日报", "央视新闻", "证监会", "发改委",
    "商务部", "财政部", "国家统计局", "央行", "银保监会", "工信部",
    "国务院", "人大", "政协", "Xinhua", "NDRC", "PBOC", "CSRC",
})

# News more recent than this (hours) gets a freshness boost (+1 capped at 5)
_FRESHNESS_HOURS = 2

_CLASSIFICATION_PROMPT_TEMPLATE = """\
You are a financial news analyst specialising in Chinese and global capital markets \
(A-shares, HK, US equities).

Classify the following {n} news items and return a JSON array. Each element must have \
these exact keys:
  idx            - integer, the item index from the list (0-based)
  category       - one of: "breaking" | "industry" | "political" | "finance" | "macro" | "regulatory" | "other"
  priority       - integer 1-5 where 5=极高影响(permanent record), 4=高影响, 3=中等, 2=低, 1=极低/noise
  sentiment      - one of: "positive" | "negative" | "neutral" | "mixed"
  market_scope   - one of: "CN" | "HK" | "US" | "global" | "sector"
  affected_sectors  - JSON array of sector names (strings), e.g. ["银行", "地产"] — empty array if none
  affected_stocks   - JSON array of stock codes or names, e.g. ["600519", "贵州茅台"] — empty array if none
  impact_horizon    - one of: "immediate" | "short_term" | "medium_term" | "long_term"
  llm_reasoning     - one concise sentence (≤80 chars) explaining the rating
  is_actionable     - boolean, true if this news should trigger a stock re-analysis

Priority calibration guide:
  5 — Central bank / government policy shift, systemic risk event, major index crash/rally
  4 — Significant sector-level policy, earnings surprise (>20%), major M&A, index component change
  3 — Quarterly earnings in-line, mid-cap corporate event, macro data release (CPI, PMI)
  2 — Routine corporate disclosures, minor analyst upgrades, commodity price moves
  1 — Repetitive news, soft comments, social media sentiment, very low relevance

News items to classify:
{items_block}

Return ONLY the JSON array — no markdown fences, no explanation.
"""


class LLMClassifier:
    def __init__(
        self,
        config: SentinelConfig,
        analyzer: Any = None,
    ) -> None:
        self._config = config
        self._analyzer = analyzer  # None → lazy-loaded on first use

    def _get_analyzer(self) -> Any:
        if self._analyzer is None:
            from src.analyzer import GeminiAnalyzer
            override_model = self._config.llm_model
            if override_model:
                import dataclasses
                from src.config import get_config
                custom_config = dataclasses.replace(get_config(), litellm_model=override_model)
                self._analyzer = GeminiAnalyzer(config=custom_config)
            else:
                self._analyzer = GeminiAnalyzer()
        return self._analyzer

    # ── public API ────────────────────────────────────────────────────────────

    def classify_pending(self, store: NewsStore) -> int:
        """Classify up to llm_max_per_cycle unclassified items. Returns count classified."""
        total_classified = 0
        watched_codes: set = set()
        if self._config.watched_stocks_boost:
            try:
                watched_codes = {s["code"] for s in store.get_watched_stocks()}
            except Exception:
                pass
        remaining = self._config.llm_max_per_cycle

        while remaining > 0:
            batch_size = min(self._config.llm_batch_size, remaining)
            rows = store.get_pending_classification(limit=batch_size)
            if not rows:
                break

            try:
                results = self._classify_batch(rows)
            except Exception:
                logger.exception("LLMClassifier batch failed — skipping batch")
                break

            for item_dict, row in zip(results, rows):
                try:
                    fields = self._apply_priority_rules(item_dict, row, watched_codes=watched_codes)
                    store.update_classification(row["url_hash"], fields)
                    total_classified += 1
                except Exception:
                    logger.exception("Failed to apply classification for %s", row["url_hash"][:12])

            remaining -= len(rows)
            if len(rows) < batch_size:
                break

        return total_classified

    # ── internal ──────────────────────────────────────────────────────────────

    def _classify_batch(self, rows: list) -> List[dict]:
        """Call LLM for a batch of rows; return list of classification dicts aligned to rows."""
        prompt = self._build_prompt(rows)
        analyzer = self._get_analyzer()
        raw = analyzer.generate_text(prompt, max_tokens=4096, temperature=0.2)

        if not raw:
            logger.warning("LLMClassifier: empty response from LLM")
            return [{} for _ in rows]

        parsed = self._parse_response(raw)
        # Re-align by idx; fall back to positional if idx is missing
        result: List[dict] = [{} for _ in rows]
        for item in parsed:
            if not isinstance(item, dict):
                continue
            idx = item.get("idx")
            if isinstance(idx, int) and 0 <= idx < len(rows):
                result[idx] = item
            # items without valid idx are discarded (rare LLM hallucination)
        return result

    def _build_prompt(self, rows: list) -> str:
        lines = []
        for i, row in enumerate(rows):
            title = row["title"] or ""
            source = row["source_name"] or ""
            content_snip = (row["content"] or "")[:300].replace("\n", " ")
            pub = row["published_at"] or row["fetched_at"] or ""
            lines.append(
                f"[{i}] [{source}] [{pub[:16]}] {title}\n    {content_snip}"
            )
        items_block = "\n\n".join(lines)
        return _CLASSIFICATION_PROMPT_TEMPLATE.format(n=len(rows), items_block=items_block)

    def _parse_response(self, text: str) -> List[dict]:
        text = text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            text = text.strip()

        # Try direct parse first
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "items" in data:
                return data["items"]
            return []
        except json.JSONDecodeError:
            pass

        # Fall back to json_repair
        try:
            from json_repair import repair_json
            data = repair_json(text, return_objects=True)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        return v
        except Exception:
            logger.warning("LLMClassifier: json_repair also failed")

        return []

    def _apply_priority_rules(self, item: dict, row: Any, watched_codes: Optional[set] = None) -> dict:
        """Validate and adjust classification fields; compute expires_at."""
        category = item.get("category", "other")
        priority_raw = item.get("priority")
        sentiment = item.get("sentiment", "neutral")
        market_scope = item.get("market_scope", "CN")
        affected_sectors = item.get("affected_sectors", [])
        affected_stocks = item.get("affected_stocks", [])
        impact_horizon = item.get("impact_horizon", "short_term")
        llm_reasoning = item.get("llm_reasoning", "")
        is_actionable = bool(item.get("is_actionable", False))

        # Coerce priority to int in [1, 5]
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            priority = 2  # default for unclassifiable
        priority = max(1, min(5, priority))

        # Government/authoritative source bonus
        source = row["source_name"] or ""
        if source in _GOV_SOURCES and priority < 5:
            priority = min(5, priority + 1)

        # Freshness bonus — very recent breaking news gets a nudge
        if priority < 5:
            fetched_str = row["fetched_at"] or ""
            try:
                fetched_dt = datetime.fromisoformat(fetched_str.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - fetched_dt).total_seconds() / 3600
                if age_hours <= _FRESHNESS_HOURS and category == "breaking":
                    priority = min(5, priority + 1)
            except (ValueError, TypeError):
                pass

        # Watched stocks boost
        if watched_codes and priority < 5:
            try:
                article_stocks = set(affected_stocks) if isinstance(affected_stocks, list) else set()
                if article_stocks & watched_codes:
                    priority = min(5, priority + 1)
            except Exception:
                pass

        # Compute expires_at from published_at (or fetched_at as fallback)
        pub_str = row["published_at"] or row["fetched_at"] or ""
        base_time: Optional[datetime] = None
        if pub_str:
            try:
                base_time = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        if base_time is None:
            base_time = datetime.now(timezone.utc)

        expires_at = compute_expires_at(priority, base_time)

        return {
            "category": category,
            "priority": priority,
            "sentiment": sentiment,
            "market_scope": market_scope,
            "affected_sectors": json.dumps(affected_sectors, ensure_ascii=False) if isinstance(affected_sectors, list) else affected_sectors,
            "affected_stocks": json.dumps(affected_stocks, ensure_ascii=False) if isinstance(affected_stocks, list) else affected_stocks,
            "impact_horizon": impact_horizon,
            "llm_reasoning": llm_reasoning[:200] if llm_reasoning else "",
            "is_actionable": 1 if is_actionable else 0,
            "expires_at": expires_at,
        }
