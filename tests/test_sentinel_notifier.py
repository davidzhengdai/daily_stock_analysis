# -*- coding: utf-8 -*-
"""Offline tests for SentinelNotifier."""
import json
import pytest
from unittest.mock import MagicMock, call

from src.services.sentinel.notifier import SentinelNotifier, _MOOD_EMOJI
from src.services.sentinel.config import SentinelConfig


def _make_config(**kwargs):
    cfg = SentinelConfig()
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _make_cycle_result(
    market_mood="乐观",
    stock_leads=None,
    cycle_id=1,
    cycle_at="2026-01-01T00:00:00+00:00",
    news_count=10,
):
    return {
        "themes": [{"theme": "降息", "confidence": 0.8, "sectors": ["银行"]}],
        "sector_opportunities": [{"sector": "银行", "signal": "bullish", "horizon": "short_term", "reason": "利好"}],
        "stock_leads": stock_leads or [],
        "risk_alerts": ["外汇风险"],
        "market_mood": market_mood,
        "cycle_id": cycle_id,
        "cycle_at": cycle_at,
        "news_count": news_count,
    }


def _make_news_row(priority=5, title="Major policy change", source_name="新华社",
                   sentiment="positive", category="regulatory", is_actionable=1,
                   url_hash="abc123", llm_reasoning="重大政策", affected_stocks='["600519"]',
                   published_at="2026-01-01T00:00:00+00:00",
                   fetched_at="2026-01-01T00:00:00+00:00"):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "priority": priority,
        "title": title,
        "source_name": source_name,
        "sentiment": sentiment,
        "category": category,
        "is_actionable": is_actionable,
        "url_hash": url_hash,
        "llm_reasoning": llm_reasoning,
        "affected_stocks": affected_stocks,
        "published_at": published_at,
        "fetched_at": fetched_at,
    }[k]
    return row


class TestFormatDigest:
    def test_contains_mood(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True)
        result = _make_cycle_result(market_mood="乐观")
        content = n._format_digest(result, [])
        assert "乐观" in content

    def test_contains_mood_emoji(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True)
        result = _make_cycle_result(market_mood="乐观")
        content = n._format_digest(result, [])
        assert _MOOD_EMOJI["乐观"] in content

    def test_contains_themes(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True)
        result = _make_cycle_result()
        content = n._format_digest(result, [])
        assert "降息" in content

    def test_contains_stock_leads_when_present(self):
        cfg = _make_config()
        leads = [{"code": "600519", "name": "茅台", "reason": "涨价", "confidence": 0.85}]
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True)
        result = _make_cycle_result(stock_leads=leads)
        content = n._format_digest(result, leads)
        assert "600519" in content

    def test_no_leads_section_when_empty(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True)
        result = _make_cycle_result()
        content = n._format_digest(result, [])
        assert "股票线索" not in content


class TestFormatBreakingAlert:
    def test_p5_label(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg)
        row = _make_news_row(priority=5)
        content = n._format_breaking_alert(row)
        assert "P5" in content

    def test_p4_label(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg)
        row = _make_news_row(priority=4)
        content = n._format_breaking_alert(row)
        assert "P4" in content

    def test_contains_title(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg)
        row = _make_news_row(title="央行降息50bp")
        content = n._format_breaking_alert(row)
        assert "央行降息50bp" in content

    def test_contains_affected_stocks(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg)
        row = _make_news_row(affected_stocks='["600519", "000858"]')
        content = n._format_breaking_alert(row)
        assert "600519" in content


class TestDispatchCycleAnalysis:
    def test_sends_notification(self):
        sent_calls = []
        cfg = _make_config(trigger_confidence=0.7)

        def fake_notify(content, **kwargs):
            sent_calls.append(content)
            return True

        n = SentinelNotifier(cfg, notify_fn=fake_notify)
        store = MagicMock()
        result = _make_cycle_result()
        triggered = n.dispatch_cycle_analysis(result, store)

        assert len(sent_calls) == 1
        assert triggered == []

    def test_triggers_stock_for_confident_lead(self):
        triggered_stocks = []
        cfg = _make_config(trigger_confidence=0.7)
        leads = [{"code": "600519", "name": "茅台", "reason": "涨价", "confidence": 0.85}]

        def fake_trigger(code):
            triggered_stocks.append(code)

        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True, trigger_fn=fake_trigger)
        store = MagicMock()
        result = _make_cycle_result(stock_leads=leads)
        triggered = n.dispatch_cycle_analysis(result, store)

        assert "600519" in triggered
        assert triggered_stocks == ["600519"]

    def test_does_not_trigger_low_confidence_lead(self):
        triggered_stocks = []
        cfg = _make_config(trigger_confidence=0.7)
        leads = [{"code": "000001", "name": "平安银行", "reason": "弱", "confidence": 0.5}]

        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True,
                             trigger_fn=lambda c: triggered_stocks.append(c))
        store = MagicMock()
        result = _make_cycle_result(stock_leads=leads)
        triggered = n.dispatch_cycle_analysis(result, store)

        assert triggered == []
        assert triggered_stocks == []

    def test_updates_db_with_triggered_stocks(self):
        cfg = _make_config(trigger_confidence=0.7)
        leads = [{"code": "600519", "name": "茅台", "reason": "涨价", "confidence": 0.9}]

        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: True, trigger_fn=lambda c: None)
        store = MagicMock()
        result = _make_cycle_result(stock_leads=leads, cycle_id=7)
        n.dispatch_cycle_analysis(result, store)

        store.update_cycle_triggered_stocks.assert_called_once()
        args = store.update_cycle_triggered_stocks.call_args[0]
        assert args[0] == 7
        assert "600519" in args[1]


class TestDispatchBreakingAlerts:
    def test_sends_alert_for_p5_actionable(self):
        sent_calls = []
        cfg = _make_config()

        def fake_notify(content, **kwargs):
            sent_calls.append(content)
            return True

        n = SentinelNotifier(cfg, notify_fn=fake_notify)
        store = MagicMock()
        store.get_item_by_url_hash.return_value = _make_news_row(priority=5, is_actionable=1)
        count = n.dispatch_breaking_alerts(["abc123"], store)
        assert count == 1

    def test_skips_low_priority(self):
        sent_calls = []
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: sent_calls.append(1) or True)
        store = MagicMock()
        store.get_item_by_url_hash.return_value = _make_news_row(priority=2, is_actionable=1)
        count = n.dispatch_breaking_alerts(["abc123"], store)
        assert count == 0

    def test_skips_non_actionable(self):
        sent_calls = []
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: sent_calls.append(1) or True)
        store = MagicMock()
        store.get_item_by_url_hash.return_value = _make_news_row(priority=5, is_actionable=0)
        count = n.dispatch_breaking_alerts(["abc123"], store)
        assert count == 0

    def test_dedup_within_session(self):
        sent_calls = []
        cfg = _make_config()
        n = SentinelNotifier(cfg, notify_fn=lambda *a, **kw: sent_calls.append(1) or True)
        store = MagicMock()
        store.get_item_by_url_hash.return_value = _make_news_row(priority=5, is_actionable=1)

        # First call sends
        n.dispatch_breaking_alerts(["abc123"], store)
        # Second call with same hash is deduplicated
        n.dispatch_breaking_alerts(["abc123"], store)
        assert len(sent_calls) == 1

    def test_returns_zero_for_empty_list(self):
        cfg = _make_config()
        n = SentinelNotifier(cfg)
        store = MagicMock()
        assert n.dispatch_breaking_alerts([], store) == 0
