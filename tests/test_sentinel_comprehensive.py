# -*- coding: utf-8 -*-
"""Offline tests for ComprehensiveAnalyzer."""
import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.services.sentinel.comprehensive import ComprehensiveAnalyzer
from src.services.sentinel.config import SentinelConfig


def _make_config(**kwargs):
    cfg = SentinelConfig()
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _make_row(priority=4, title="Test news", source_name="新华社",
              category="breaking", llm_reasoning="Market impact",
              published_at="2026-01-01T00:00:00+00:00",
              fetched_at="2026-01-01T00:00:00+00:00"):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "priority": priority,
        "title": title,
        "source_name": source_name,
        "category": category,
        "llm_reasoning": llm_reasoning,
        "published_at": published_at,
        "fetched_at": fetched_at,
    }[k]
    return row


_VALID_RESPONSE = json.dumps({
    "themes": [{"theme": "央行降息预期", "confidence": 0.85, "sectors": ["银行", "地产"]}],
    "sector_opportunities": [{"sector": "银行", "signal": "bullish", "horizon": "short_term", "reason": "利好"}],
    "stock_leads": [{"code": "600519", "name": "贵州茅台", "reason": "流动性改善", "confidence": 0.8}],
    "risk_alerts": ["外汇波动风险"],
    "market_mood": "乐观",
})


class TestShouldRun:
    def test_runs_when_no_previous_analysis(self):
        cfg = _make_config(analysis_interval_hours=4)
        analyzer = ComprehensiveAnalyzer(cfg)
        store = MagicMock()
        store.get_last_cycle_analysis_at.return_value = None
        assert analyzer.should_run(store) is True

    def test_does_not_run_when_recent(self):
        cfg = _make_config(analysis_interval_hours=4)
        analyzer = ComprehensiveAnalyzer(cfg)
        store = MagicMock()
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.get_last_cycle_analysis_at.return_value = recent
        assert analyzer.should_run(store) is False

    def test_runs_when_interval_elapsed(self):
        cfg = _make_config(analysis_interval_hours=4)
        analyzer = ComprehensiveAnalyzer(cfg)
        store = MagicMock()
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        store.get_last_cycle_analysis_at.return_value = old
        assert analyzer.should_run(store) is True

    def test_handles_malformed_timestamp(self):
        cfg = _make_config(analysis_interval_hours=4)
        analyzer = ComprehensiveAnalyzer(cfg)
        store = MagicMock()
        store.get_last_cycle_analysis_at.return_value = "not-a-date"
        assert analyzer.should_run(store) is True


class TestBuildPrompt:
    def test_prompt_contains_priority(self):
        cfg = _make_config()
        analyzer = ComprehensiveAnalyzer(cfg)
        rows = [_make_row(priority=4)]
        prompt = analyzer._build_prompt(rows, 12)
        assert "[P4]" in prompt

    def test_prompt_contains_title(self):
        cfg = _make_config()
        analyzer = ComprehensiveAnalyzer(cfg)
        rows = [_make_row(title="Rate cut expected")]
        prompt = analyzer._build_prompt(rows, 12)
        assert "Rate cut expected" in prompt

    def test_prompt_contains_count(self):
        cfg = _make_config()
        analyzer = ComprehensiveAnalyzer(cfg)
        rows = [_make_row(), _make_row()]
        prompt = analyzer._build_prompt(rows, 12)
        assert "2" in prompt


class TestParseResponse:
    def setup_method(self):
        self.analyzer = ComprehensiveAnalyzer(_make_config())

    def test_valid_json_dict(self):
        result = self.analyzer._parse_response(_VALID_RESPONSE)
        assert result is not None
        assert "themes" in result
        assert "market_mood" in result

    def test_strips_markdown_fences(self):
        fenced = f"```json\n{_VALID_RESPONSE}\n```"
        result = self.analyzer._parse_response(fenced)
        assert result is not None
        assert result["market_mood"] == "乐观"

    def test_returns_none_on_junk(self):
        result = self.analyzer._parse_response("not json at all")
        assert result is None

    def test_returns_none_on_array(self):
        # Array is not a valid comprehensive response (should be dict)
        result = self.analyzer._parse_response("[1,2,3]")
        assert result is None


class TestAnalyze:
    def test_returns_none_when_not_due(self):
        cfg = _make_config(analysis_interval_hours=4)
        mock_analyzer = MagicMock()
        ca = ComprehensiveAnalyzer(cfg, analyzer=mock_analyzer)
        store = MagicMock()
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.get_last_cycle_analysis_at.return_value = recent
        result = ca.analyze(store)
        assert result is None
        assert mock_analyzer.generate_text.call_count == 0

    def test_returns_none_when_no_rows(self):
        cfg = _make_config(analysis_interval_hours=4)
        mock_analyzer = MagicMock()
        ca = ComprehensiveAnalyzer(cfg, analyzer=mock_analyzer)
        store = MagicMock()
        store.get_last_cycle_analysis_at.return_value = None
        store.get_recent_classified.return_value = []
        result = ca.analyze(store)
        assert result is None

    def test_returns_result_with_cycle_id(self):
        cfg = _make_config(analysis_interval_hours=4)
        mock_analyzer = MagicMock()
        mock_analyzer.generate_text.return_value = _VALID_RESPONSE
        ca = ComprehensiveAnalyzer(cfg, analyzer=mock_analyzer)

        store = MagicMock()
        store.get_last_cycle_analysis_at.return_value = None
        store.get_recent_classified.return_value = [_make_row()]
        store.record_cycle_analysis.return_value = 42

        result = ca.analyze(store)
        assert result is not None
        assert result["cycle_id"] == 42
        assert result["market_mood"] == "乐观"
        assert result["news_count"] == 1
        assert store.record_cycle_analysis.call_count == 1

    def test_returns_none_on_llm_failure(self):
        cfg = _make_config(analysis_interval_hours=4)
        mock_analyzer = MagicMock()
        mock_analyzer.generate_text.return_value = None
        ca = ComprehensiveAnalyzer(cfg, analyzer=mock_analyzer)

        store = MagicMock()
        store.get_last_cycle_analysis_at.return_value = None
        store.get_recent_classified.return_value = [_make_row()]

        result = ca.analyze(store)
        assert result is None
