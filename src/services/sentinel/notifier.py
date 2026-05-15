# -*- coding: utf-8 -*-
"""SentinelNotifier — dispatches alerts and triggers stock analysis.

Two responsibilities:
1. Sends a notification digest after ComprehensiveAnalyzer produces a result
   (market mood + themes + sector ops + high-confidence stock leads).
2. Fires immediate P5 / breaking alerts for newly classified high-priority items.
3. Enqueues per-stock deep-analysis via TaskService for high-confidence stock leads.

Both NotificationService and TaskService are lazy-imported so that the
sentinel package stays decoupled from the full LLM + notification stack when
running in dry-run or test mode.  Injectable `notify_fn` / `trigger_fn`
parameters allow offline unit-testing without any real services.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from .config import SentinelConfig
from .store import NewsStore

logger = logging.getLogger(__name__)

_MOOD_EMOJI = {
    "极度悲观": "🔴",
    "悲观": "🟠",
    "中性": "⚪",
    "乐观": "🟢",
    "极度乐观": "💚",
}


class SentinelNotifier:
    def __init__(
        self,
        config: SentinelConfig,
        notify_fn: Optional[Callable[..., bool]] = None,
        trigger_fn: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._config = config
        self._notify_fn = notify_fn       # (content, **kwargs) -> bool
        self._trigger_fn = trigger_fn     # (stock_code: str) -> Any
        # In-session dedup: avoid re-notifying the same url_hash twice
        self._notified_hashes: set = set()

    # ── public API ────────────────────────────────────────────────────────────

    def dispatch_cycle_analysis(self, result: dict, store: NewsStore) -> List[str]:
        """Send analysis digest and trigger stock analysis for confident leads.

        Args:
            result: Output dict from ComprehensiveAnalyzer.analyze().
            store:  NewsStore for updating triggered_stocks in cycle_analyses.

        Returns:
            List of stock codes that were enqueued for analysis.
        """
        triggered: List[str] = []

        stock_leads = result.get("stock_leads") or []
        confident_leads = [
            s for s in stock_leads
            if isinstance(s, dict) and float(s.get("confidence", 0)) >= self._config.trigger_confidence
        ]

        # Send digest notification (always, even if no stock leads)
        content = self._format_digest(result, confident_leads)
        self._send(content, route_type="alert", severity="info",
                   dedup_key=f"sentinel:cycle:{result.get('cycle_at', '')}")

        # Enqueue stock analysis for each confident lead
        for lead in confident_leads:
            code = lead.get("code", "").strip()
            if not code:
                continue
            try:
                self._trigger_stock(code)
                triggered.append(code)
                logger.info("SentinelNotifier: triggered analysis for %s", code)
            except Exception:
                logger.exception("SentinelNotifier: failed to trigger analysis for %s", code)

        # Update DB record with triggered stocks
        cycle_id = result.get("cycle_id")
        if cycle_id and triggered:
            store.update_cycle_triggered_stocks(
                cycle_id, json.dumps(triggered, ensure_ascii=False)
            )

        return triggered

    def dispatch_breaking_alerts(self, new_url_hashes: List[str], store: NewsStore) -> int:
        """Send immediate alerts for newly classified P4+ actionable items.

        Args:
            new_url_hashes: url_hash values inserted this cycle (for dedup).
            store:          NewsStore to look up classification fields.

        Returns:
            Number of alerts sent.
        """
        if not new_url_hashes:
            return 0

        sent = 0
        for uh in new_url_hashes:
            if uh in self._notified_hashes:
                continue
            row = store.get_item_by_url_hash(uh)
            if row is None:
                continue
            priority = row["priority"]
            if priority is None or priority < 4:
                continue
            if not row["is_actionable"]:
                continue

            content = self._format_breaking_alert(row)
            ok = self._send(
                content,
                route_type="alert",
                severity="critical" if priority == 5 else "warning",
                dedup_key=f"sentinel:breaking:{uh}",
            )
            if ok:
                self._notified_hashes.add(uh)
                sent += 1

        return sent

    # ── formatting ────────────────────────────────────────────────────────────

    def _format_digest(self, result: dict, confident_leads: list) -> str:
        mood = result.get("market_mood", "中性")
        mood_icon = _MOOD_EMOJI.get(mood, "⚪")
        cycle_at = result.get("cycle_at", "")[:16].replace("T", " ")
        news_count = result.get("news_count", 0)

        lines = [
            f"## {mood_icon} Sentinel 市场情报 [{cycle_at} UTC]",
            f"分析基础：最近 {news_count} 条 P3+ 资讯",
            f"**市场情绪：{mood}**",
            "",
        ]

        themes = result.get("themes") or []
        if themes:
            lines.append("### 宏观主题")
            for t in themes[:3]:
                if isinstance(t, dict):
                    conf = int(float(t.get("confidence", 0)) * 100)
                    sectors = "、".join(t.get("sectors", []))
                    lines.append(f"- **{t.get('theme', '')}**（置信度 {conf}%）{f'  [{sectors}]' if sectors else ''}")
            lines.append("")

        sector_opps = result.get("sector_opportunities") or []
        if sector_opps:
            lines.append("### 板块机会")
            signal_map = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
            for s in sector_opps[:5]:
                if isinstance(s, dict):
                    sig = signal_map.get(s.get("signal", ""), s.get("signal", ""))
                    lines.append(f"- **{s.get('sector', '')}** {sig} · {s.get('reason', '')}")
            lines.append("")

        risk_alerts = result.get("risk_alerts") or []
        if risk_alerts:
            lines.append("### 风险提示")
            for r in risk_alerts[:3]:
                lines.append(f"- ⚠️ {r}")
            lines.append("")

        if confident_leads:
            lines.append("### 股票线索（已触发深度分析）")
            for lead in confident_leads:
                if isinstance(lead, dict):
                    code = lead.get("code", "")
                    name = lead.get("name", "")
                    conf = int(float(lead.get("confidence", 0)) * 100)
                    lines.append(f"- **{code}** {name}（置信度 {conf}%）：{lead.get('reason', '')}")

        return "\n".join(lines)

    def _format_breaking_alert(self, row: Any) -> str:
        priority = row["priority"]
        source = row["source_name"] or ""
        title = row["title"] or ""
        pub = (row["published_at"] or row["fetched_at"] or "")[:16].replace("T", " ")
        reasoning = row["llm_reasoning"] or ""
        category = row["category"] or ""
        mood_map = {"positive": "🟢", "negative": "🔴", "neutral": "⚪", "mixed": "🟡"}
        sentiment_icon = mood_map.get(row["sentiment"] or "", "⚪")

        label = "🚨 **P5 重大新闻**" if priority == 5 else "⚡ **P4 高影响新闻**"
        lines = [
            f"{label} · {category}",
            f"**{title}**",
            f"{sentiment_icon} 来源：{source}  时间：{pub} UTC",
        ]
        if reasoning:
            lines.append(f"> {reasoning}")

        affected = row["affected_stocks"] or ""
        if affected:
            try:
                stocks = json.loads(affected)
                if stocks:
                    lines.append(f"涉及个股：{', '.join(str(s) for s in stocks[:5])}")
            except (json.JSONDecodeError, TypeError):
                pass

        return "\n".join(lines)

    # ── dispatch helpers ──────────────────────────────────────────────────────

    def _send(self, content: str, **kwargs) -> bool:
        fn = self._notify_fn
        if fn is None:
            fn = self._lazy_notify_fn()
        if fn is None:
            return False
        try:
            return bool(fn(content, **kwargs))
        except Exception:
            logger.exception("SentinelNotifier: notification dispatch failed")
            return False

    def _trigger_stock(self, code: str) -> None:
        fn = self._trigger_fn
        if fn is None:
            fn = self._lazy_trigger_fn()
        if fn is not None:
            fn(code)

    def _lazy_notify_fn(self) -> Optional[Callable]:
        try:
            from src.notification import NotificationService
            svc = NotificationService()
            self._notify_fn = svc.send
            return self._notify_fn
        except Exception:
            logger.warning("SentinelNotifier: NotificationService unavailable — alerts suppressed")
            self._notify_fn = lambda *a, **kw: False
            return self._notify_fn

    def _lazy_trigger_fn(self) -> Optional[Callable]:
        try:
            from src.services.task_service import TaskService
            ts = TaskService.get_instance()
            self._trigger_fn = lambda code: ts.submit_analysis(code, query_source="sentinel")
            return self._trigger_fn
        except Exception:
            logger.warning("SentinelNotifier: TaskService unavailable — stock triggers suppressed")
            self._trigger_fn = lambda code: None
            return self._trigger_fn
