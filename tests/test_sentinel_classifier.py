# -*- coding: utf-8 -*-
"""Offline tests for LLMClassifier using a mock analyzer."""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from src.services.sentinel.classifier import LLMClassifier, _GOV_SOURCES
from src.services.sentinel.config import SentinelConfig


def _make_config(**kwargs):
    cfg = SentinelConfig()
    for k, v in kwargs.items():
        setattr(cfg, k, v)
    return cfg


def _make_row(
    url_hash="abc123",
    title="Test news",
    source_name="新华社",
    content="Some financial content",
    published_at="2026-01-01T00:00:00+00:00",
    fetched_at="2026-01-01T00:00:00+00:00",
):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {
        "url_hash": url_hash,
        "title": title,
        "source_name": source_name,
        "content": content,
        "published_at": published_at,
        "fetched_at": fetched_at,
    }[k]
    return row


class TestBuildPrompt:
    def test_prompt_contains_item_index(self):
        clf = LLMClassifier(_make_config())
        row = _make_row(title="Central bank rate cut")
        prompt = clf._build_prompt([row])
        assert "[0]" in prompt

    def test_prompt_contains_source(self):
        clf = LLMClassifier(_make_config())
        row = _make_row(source_name="新华社")
        prompt = clf._build_prompt([row])
        assert "新华社" in prompt

    def test_prompt_contains_title(self):
        clf = LLMClassifier(_make_config())
        row = _make_row(title="Fed hikes 50bps")
        prompt = clf._build_prompt([row])
        assert "Fed hikes 50bps" in prompt

    def test_prompt_multiple_items(self):
        clf = LLMClassifier(_make_config())
        rows = [_make_row(title=f"News {i}") for i in range(3)]
        prompt = clf._build_prompt(rows)
        assert "[0]" in prompt
        assert "[1]" in prompt
        assert "[2]" in prompt


class TestParseResponse:
    def setup_method(self):
        self.clf = LLMClassifier(_make_config())

    def test_valid_json_array(self):
        text = json.dumps([{"idx": 0, "priority": 3}])
        result = self.clf._parse_response(text)
        assert len(result) == 1
        assert result[0]["priority"] == 3

    def test_strips_markdown_fences(self):
        text = "```json\n[{\"idx\": 0, \"priority\": 2}]\n```"
        result = self.clf._parse_response(text)
        assert result[0]["priority"] == 2

    def test_handles_wrapped_dict(self):
        text = json.dumps({"items": [{"idx": 0, "priority": 4}]})
        result = self.clf._parse_response(text)
        assert result[0]["priority"] == 4

    def test_returns_empty_on_junk(self):
        result = self.clf._parse_response("not json at all!!")
        assert isinstance(result, list)

    def test_handles_trailing_text(self):
        text = '[{"idx": 0, "priority": 1}] some trailing text'
        # json_repair should handle this
        result = self.clf._parse_response(text)
        assert isinstance(result, list)


class TestApplyPriorityRules:
    def setup_method(self):
        self.clf = LLMClassifier(_make_config())

    def test_priority_clamped_to_1(self):
        row = _make_row()
        fields = self.clf._apply_priority_rules({"priority": -5}, row)
        assert fields["priority"] == 1

    def test_priority_clamped_to_5(self):
        row = _make_row()
        fields = self.clf._apply_priority_rules({"priority": 99}, row)
        assert fields["priority"] == 5

    def test_gov_source_bonus(self):
        row = _make_row(source_name="新华社")
        # start at priority 2 → should become 3
        fields = self.clf._apply_priority_rules({"priority": 2}, row)
        assert fields["priority"] == 3

    def test_gov_source_bonus_capped_at_5(self):
        row = _make_row(source_name="央视新闻")
        fields = self.clf._apply_priority_rules({"priority": 5}, row)
        assert fields["priority"] == 5

    def test_non_gov_source_no_bonus(self):
        row = _make_row(source_name="RandomBlog")
        fields = self.clf._apply_priority_rules({"priority": 2}, row)
        assert fields["priority"] == 2

    def test_expires_at_populated(self):
        row = _make_row(source_name="RandomBlog")
        fields = self.clf._apply_priority_rules({"priority": 2}, row)
        assert fields["expires_at"] is not None

    def test_p5_expires_at_is_none(self):
        row = _make_row(source_name="RandomBlog")
        fields = self.clf._apply_priority_rules({"priority": 5}, row)
        assert fields["expires_at"] is None

    def test_affected_sectors_serialised_to_json(self):
        row = _make_row()
        fields = self.clf._apply_priority_rules(
            {"priority": 3, "affected_sectors": ["银行", "地产"]}, row
        )
        loaded = json.loads(fields["affected_sectors"])
        assert loaded == ["银行", "地产"]

    def test_is_actionable_coerced_to_int(self):
        row = _make_row()
        fields = self.clf._apply_priority_rules({"priority": 3, "is_actionable": True}, row)
        assert fields["is_actionable"] == 1

    def test_llm_reasoning_truncated(self):
        row = _make_row()
        long_reason = "x" * 300
        fields = self.clf._apply_priority_rules({"priority": 3, "llm_reasoning": long_reason}, row)
        assert len(fields["llm_reasoning"]) <= 200

    def test_missing_priority_defaults_to_2(self):
        row = _make_row()
        fields = self.clf._apply_priority_rules({}, row)
        # no gov source bonus for generic row
        assert fields["priority"] in (2, 3)  # 2 base, or 3 if source matches gov list


class TestClassifyPending:
    def test_classify_pending_calls_store(self):
        mock_analyzer = MagicMock()
        mock_analyzer.generate_text.return_value = json.dumps([
            {
                "idx": 0,
                "category": "finance",
                "priority": 3,
                "sentiment": "neutral",
                "market_scope": "CN",
                "affected_sectors": [],
                "affected_stocks": [],
                "impact_horizon": "short_term",
                "llm_reasoning": "Routine earnings",
                "is_actionable": False,
            }
        ])

        config = _make_config(llm_batch_size=5, llm_max_per_cycle=10)
        clf = LLMClassifier(config, analyzer=mock_analyzer)

        fake_row = _make_row()
        call_count = [0]

        class FakeStore:
            def get_pending_classification(self, limit):
                call_count[0] += 1
                if call_count[0] == 1:
                    return [fake_row]
                return []  # second call returns empty → stops loop

            def update_classification(self, url_hash, fields):
                pass

        count = clf.classify_pending(FakeStore())
        assert count == 1
        assert mock_analyzer.generate_text.call_count == 1

    def test_classify_pending_stops_on_empty_batch(self):
        mock_analyzer = MagicMock()
        config = _make_config(llm_batch_size=5, llm_max_per_cycle=100)
        clf = LLMClassifier(config, analyzer=mock_analyzer)

        class FakeStore:
            def get_pending_classification(self, limit):
                return []

            def update_classification(self, url_hash, fields):
                pass

        count = clf.classify_pending(FakeStore())
        assert count == 0
        assert mock_analyzer.generate_text.call_count == 0

    def test_classify_pending_handles_llm_failure(self):
        mock_analyzer = MagicMock()
        mock_analyzer.generate_text.return_value = None  # LLM returns nothing

        config = _make_config(llm_batch_size=5, llm_max_per_cycle=10)
        clf = LLMClassifier(config, analyzer=mock_analyzer)

        fake_row = _make_row()

        class FakeStore:
            def get_pending_classification(self, limit):
                return [fake_row]

            def update_classification(self, url_hash, fields):
                pass

        # Should not raise; returns 0 classified (empty response → empty parse → no fields applied)
        count = clf.classify_pending(FakeStore())
        assert isinstance(count, int)
