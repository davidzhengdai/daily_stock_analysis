# -*- coding: utf-8 -*-
"""ComprehensiveAnalyzer — post-cycle macro synthesis using LLM.

Runs once every `analysis_interval_hours` (default 4 h). Aggregates P3+
classified news from the recent window, asks the LLM to identify macro
themes, sector opportunities, specific stock leads, and risk alerts,
then persists the result in cycle_analyses.

Returns a result dict that SentinelNotifier uses to dispatch alerts and
trigger per-stock analysis.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from .config import SentinelConfig
from .store import NewsStore

logger = logging.getLogger(__name__)

_ANALYSIS_PROMPT = """\
以下是过去 {window} 小时内的重要市场资讯（共 {count} 条，已按优先级排序）：

{news_digest}

请完成以下分析，并以 JSON 格式输出（不要使用 Markdown 代码块）：

{{
  "themes": [
    {{"theme": "...", "confidence": 0.0-1.0, "sectors": ["..."]}}
  ],
  "sector_opportunities": [
    {{"sector": "...", "signal": "bullish|bearish|neutral", "horizon": "immediate|short_term|medium_term", "reason": "..."}}
  ],
  "stock_leads": [
    {{"code": "股票代码", "name": "公司名", "reason": "...", "confidence": 0.0-1.0}}
  ],
  "risk_alerts": ["..."],
  "market_mood": "极度悲观|悲观|中性|乐观|极度乐观"
}}

分析要求：
1. 宏观主题归纳（最多 3 条）：当前市场最主要的催化剂或风险，含置信度与受影响板块
2. 板块机会信号：哪些板块可能在未来 1-5 个交易日出现异动，说明方向与时间窗口
3. 具体股票线索（如有）：值得立即深度分析的股票代码与理由，含置信度（≥0.7 才填入）
4. 风险提示：需警惕的重大系统性风险（最多 3 条）
5. 综合判断：整体市场情绪

只返回 JSON，不要解释，不要 Markdown 代码块。
"""


class ComprehensiveAnalyzer:
    def __init__(
        self,
        config: SentinelConfig,
        analyzer: Any = None,
    ) -> None:
        self._config = config
        self._analyzer = analyzer

    def _get_analyzer(self) -> Any:
        if self._analyzer is None:
            from src.analyzer import GeminiAnalyzer
            self._analyzer = GeminiAnalyzer()
        return self._analyzer

    # ── public API ────────────────────────────────────────────────────────────

    def should_run(self, store: NewsStore) -> bool:
        """True if analysis_interval_hours has elapsed since the last run."""
        last_at = store.get_last_cycle_analysis_at()
        if last_at is None:
            return True
        try:
            last = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
            elapsed_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            return elapsed_h >= self._config.analysis_interval_hours
        except (ValueError, TypeError):
            return True

    def analyze(self, store: NewsStore) -> Optional[dict]:
        """Run comprehensive analysis if due. Returns result dict or None."""
        if not self.should_run(store):
            logger.debug("ComprehensiveAnalyzer: not due yet, skipping")
            return None

        window_hours = max(self._config.analysis_interval_hours * 2, 12)
        rows = store.get_recent_classified(
            hours=int(window_hours),
            priority_min=3,
            limit=100,
        )
        if not rows:
            logger.info("ComprehensiveAnalyzer: no P3+ items in window — skipping")
            return None

        logger.info("ComprehensiveAnalyzer: analyzing %d items from past %dh", len(rows), window_hours)

        prompt = self._build_prompt(rows, window_hours)
        raw = self._get_analyzer().generate_text(prompt, max_tokens=2048, temperature=0.3)
        if not raw:
            logger.warning("ComprehensiveAnalyzer: empty LLM response")
            return None

        result = self._parse_response(raw)
        if not result:
            logger.warning("ComprehensiveAnalyzer: failed to parse LLM response")
            return None

        # Persist to DB
        cycle_at = datetime.now(timezone.utc).isoformat()
        cycle_id = store.record_cycle_analysis({
            "cycle_at": cycle_at,
            "news_count": len(rows),
            "themes": json.dumps(result.get("themes", []), ensure_ascii=False),
            "sector_opps": json.dumps(result.get("sector_opportunities", []), ensure_ascii=False),
            "stock_leads": json.dumps(result.get("stock_leads", []), ensure_ascii=False),
            "risk_alerts": json.dumps(result.get("risk_alerts", []), ensure_ascii=False),
            "market_mood": result.get("market_mood", ""),
            "triggered_stocks": "",
            "model_used": "",
        })

        result["cycle_at"] = cycle_at
        result["news_count"] = len(rows)
        result["cycle_id"] = cycle_id
        return result

    # ── internal ──────────────────────────────────────────────────────────────

    def _build_prompt(self, rows: list, window_hours: float) -> str:
        lines = []
        for row in rows:
            pri = row["priority"] or "?"
            source = row["source_name"] or ""
            title = row["title"] or ""
            pub = (row["published_at"] or row["fetched_at"] or "")[:16]
            category = row["category"] or ""
            reasoning = row["llm_reasoning"] or ""
            lines.append(f"[P{pri}][{category}][{source}][{pub}] {title}")
            if reasoning:
                lines.append(f"  → {reasoning}")
        news_digest = "\n".join(lines)
        return _ANALYSIS_PROMPT.format(
            window=int(window_hours),
            count=len(rows),
            news_digest=news_digest,
        )

    def _parse_response(self, text: str) -> Optional[dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        try:
            from json_repair import repair_json
            data = repair_json(text, return_objects=True)
            if isinstance(data, dict):
                return data
        except Exception:
            logger.warning("ComprehensiveAnalyzer: json_repair failed")

        return None
