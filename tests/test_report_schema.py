# -*- coding: utf-8 -*-
"""
===================================
Report Engine - Schema parsing and fallback tests
===================================

Tests for AnalysisReportSchema validation and analyzer fallback behavior.
"""

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# Mock litellm before importing analyzer (optional runtime dep)
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.schemas.report_schema import AnalysisReportSchema
from src.analyzer import GeminiAnalyzer, AnalysisResult


class TestAnalysisReportSchema(unittest.TestCase):
    """Schema parsing tests."""

    def test_valid_dashboard_parses(self) -> None:
        """Valid LLM-like JSON parses successfully."""
        data = {
            "stock_name": "贵州茅台",
            "sentiment_score": 75,
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "decision_type": "hold",
            "confidence_level": "中",
            "dashboard": {
                "core_conclusion": {"one_sentence": "持有观望"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "110元"}},
            },
            "analysis_summary": "基本面稳健",
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertEqual(schema.stock_name, "贵州茅台")
        self.assertEqual(schema.sentiment_score, 75)
        self.assertIsNotNone(schema.dashboard)

    def test_schema_allows_optional_fields_missing(self) -> None:
        """Schema accepts minimal valid structure."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 50,
            "trend_prediction": "震荡",
            "operation_advice": "观望",
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertIsNone(schema.dashboard)
        self.assertIsNone(schema.analysis_summary)

    def test_schema_allows_numeric_strings(self) -> None:
        """Schema accepts string values for numeric fields (LLM may return N/A)."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 60,
            "trend_prediction": "看多",
            "operation_advice": "买入",
            "dashboard": {
                "data_perspective": {
                    "price_position": {
                        "current_price": "N/A",
                        "bias_ma5": "2.5",
                    }
                }
            },
        }
        schema = AnalysisReportSchema.model_validate(data)
        self.assertIsNotNone(schema.dashboard)
        pp = schema.dashboard and schema.dashboard.data_perspective and schema.dashboard.data_perspective.price_position
        self.assertIsNotNone(pp)
        if pp:
            self.assertEqual(pp.current_price, "N/A")
            self.assertEqual(pp.bias_ma5, "2.5")

    def test_schema_fails_on_invalid_sentiment_score(self) -> None:
        """Schema validation fails when sentiment_score out of range."""
        data = {
            "stock_name": "测试",
            "sentiment_score": 150,  # out of 0-100
            "trend_prediction": "看多",
            "operation_advice": "买入",
        }
        with self.assertRaises(Exception):
            AnalysisReportSchema.model_validate(data)


class TestAnalyzerSchemaFallback(unittest.TestCase):
    """Analyzer fallback when schema validation fails."""

    def test_parse_response_continues_when_schema_fails(self) -> None:
        """When schema validation fails, analyzer continues with raw dict."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台",
            "sentiment_score": 150,  # invalid for schema
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "analysis_summary": "测试摘要",
        })
        result = analyzer._parse_response(response, "600519", "贵州茅台")
        self.assertIsInstance(result, AnalysisResult)
        self.assertEqual(result.code, "600519")
        self.assertEqual(result.sentiment_score, 150)  # from raw dict
        self.assertTrue(result.success)

    def test_parse_response_valid_json_succeeds(self) -> None:
        """Valid JSON produces correct AnalysisResult."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台",
            "sentiment_score": 72,
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "decision_type": "hold",
            "confidence_level": "高",
            "analysis_summary": "技术面向好",
        })
        result = analyzer._parse_response(response, "600519", "股票600519")
        self.assertIsInstance(result, AnalysisResult)
        self.assertEqual(result.name, "贵州茅台")
        self.assertEqual(result.sentiment_score, 72)
        self.assertEqual(result.analysis_summary, "技术面向好")

    def test_parse_response_accepts_chinese_score_aliases(self) -> None:
        """Local models may return Chinese field names and scores like '69/100'."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "股票名称": "特斯拉（TSLA）",
            "趋势分析预判": {
                "趋势状态": "多头排列",
                "系统评分": "48/100",
            },
            "操作建议": "持有",
            "置信度": "中",
            "analysis_summary": "技术面偏强但估值较高",
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "TSLA", "股票TSLA")

        self.assertEqual(result.name, "特斯拉（TSLA）")
        self.assertEqual(result.sentiment_score, 48)
        self.assertEqual(result.trend_prediction, "多头排列")
        self.assertEqual(result.operation_advice, "持有")

    def test_parse_response_accepts_system_score_alias(self) -> None:
        """Some local models return system_score instead of sentiment_score."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "特斯拉（TSLA）",
            "system_score": 48,
            "operation_advice": "持有",
            "dashboard": {
                "core_conclusion": {"one_sentence": "持有，等待回调"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": 406.94}},
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "TSLA", "Tesla")

        self.assertEqual(result.sentiment_score, 48)

    def test_parse_response_normalizes_sniper_point_aliases(self) -> None:
        """Local models may return buy_price/stop_loss_price/target_price aliases."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "维宏股份（300508.SZ）",
            "system_score": 35,
            "operation_advice": "持有",
            "decision_type": "hold",
            "analysis_summary": "等待回调",
            "dashboard": {
                "core_conclusion": {"one_sentence": "乖离率过高，等待回调"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {
                    "sniper_points": {
                        "buy_price": "36.07 元（回踩 MA20 支撑）",
                        "stop_loss_price": "34.50 元（跌破 MA10 且放量确认）",
                        "target_price": "42.00 元（前期高点阻力区）",
                    }
                },
            },
            "specific_sniper_points": {
                "buy_price": "36.07 元（回踩 MA20 支撑）",
                "stop_loss_price": "34.50 元（跌破 MA10 且放量确认）",
                "target_price": "42.00 元（前期高点阻力区）",
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "300508.SZ", "维宏股份")

        sniper = result.dashboard["battle_plan"]["sniper_points"]
        self.assertEqual(sniper["ideal_buy"], "36.07 元（回踩 MA20 支撑）")
        self.assertEqual(sniper["stop_loss"], "34.50 元（跌破 MA10 且放量确认）")
        self.assertEqual(sniper["take_profit"], "42.00 元（前期高点阻力区）")

    def test_parse_response_uses_nested_sniper_aliases_when_canonical_block_is_placeholder(self) -> None:
        """Local models may place useful sniper aliases under a non-standard parent."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "维宏股份（300508.SZ）",
            "operation_advice": "持有",
            "decision_type": "hold",
            "analysis_summary": "等待回踩",
            "dashboard": {
                "core_conclusion": {"one_sentence": "等待回踩确认"},
                "intelligence": {
                    "risk_alerts": [],
                    "battle_plan": {
                        "sniper_points": {
                            "buy_zone": "34.80 - 35.20（回踩 MA5 支撑区）",
                            "stop_loss": "37.20（跌破今日低点）",
                            "target_price": "41.50（前高压力区）",
                        }
                    },
                },
                "battle_plan": {"sniper_points": {"stop_loss": "待补充"}},
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "300508.SZ", "维宏股份")

        sniper = result.dashboard["battle_plan"]["sniper_points"]
        self.assertEqual(sniper["ideal_buy"], "34.80 - 35.20（回踩 MA5 支撑区）")
        self.assertEqual(sniper["stop_loss"], "37.20（跌破今日低点）")
        self.assertEqual(sniper["take_profit"], "41.50（前高压力区）")

    def test_parse_response_accepts_repaired_json_list_root(self) -> None:
        """json_repair may recover repeated local-model JSON blocks as a list."""
        analyzer = GeminiAnalyzer()
        payload = [
            {"stock_name": "旧输出", "operation_advice": "持有"},
            {
                "stock_name": "维宏股份（300508.SZ）",
                "operation_advice": "持有",
                "decision_type": "hold",
                "analysis_summary": "等待回调",
                "dashboard": {
                    "core_conclusion": {"one_sentence": "等待回调确认"},
                    "intelligence": {"risk_alerts": []},
                    "battle_plan": {
                        "sniper_points": {
                            "buy_price": "37.50 元",
                            "stop_loss_price": "36.00 元",
                            "target_price": "41.00 元",
                        }
                    },
                },
            },
        ]

        result = analyzer._parse_response(json.dumps(payload, ensure_ascii=False), "300508.SZ", "维宏股份")

        self.assertTrue(result.success)
        sniper = result.dashboard["battle_plan"]["sniper_points"]
        self.assertEqual(sniper["ideal_buy"], "37.50 元")
        self.assertEqual(sniper["stop_loss"], "36.00 元")
        self.assertEqual(sniper["take_profit"], "41.00 元")

    def test_parse_response_accepts_nested_chinese_dashboard_score(self) -> None:
        """Local Chinese models may put the score under nested dashboard/technical sections."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "股票名称": "特斯拉（TSLA）",
            "技术面数据": {
                "趋势分析预判": {
                    "趋势状态": "多头排列",
                    "系统信号": "持有",
                    "系统评分": 48,
                },
            },
            "决策仪表盘": {
                "core_conclusion": {"one_sentence": "持有，等待回调"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": 386.76}},
                "系统信号": "持有",
                "系统评分": 48,
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "TSLA", "Tesla")

        self.assertEqual(result.sentiment_score, 48)
        self.assertEqual(result.trend_prediction, "多头排列")
        self.assertEqual(result.operation_advice, "持有")
        self.assertIsNotNone(result.dashboard)

    def test_parse_response_accepts_system_score_object(self) -> None:
        """Some local models return 系统评分 as an object containing the actual score."""
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "股票名称": "美光科技（MU）",
            "核心结论": "乖离率超买严重，建议持有并设置止损",
            "系统评分": {
                "趋势强度": 90,
                "系统评分": 52,
                "信号": "持有",
            },
            "dashboard": {
                "core_conclusion": {"one_sentence": "乖离率超买严重，建议持有并设置止损"},
                "intelligence": {"risk_alerts": []},
                "battle_plan": {"sniper_points": {"stop_loss": "495.00元"}},
            },
        }, ensure_ascii=False)

        result = analyzer._parse_response(response, "MU", "Micron Technology")

        self.assertEqual(result.sentiment_score, 52)
        self.assertEqual(result.operation_advice, "持有")
    def test_parse_response_keeps_unknown_dashboard_fields(self) -> None:
        analyzer = GeminiAnalyzer()
        response = json.dumps({
            "stock_name": "贵州茅台",
            "sentiment_score": 72,
            "trend_prediction": "看多",
            "operation_advice": "持有",
            "decision_type": "hold",
            "analysis_summary": "技术面向好",
            "dashboard": {
                "core_conclusion": {
                    "one_sentence": "先观察",
                    "signal_type": "🟡持有观望",
                },
                "decision_stability": {
                    "applied": True,
                    "reason": "回测验证",
                },
            },
        })
        result = analyzer._parse_response(response, "600519", "股票600519")
        self.assertEqual(result.dashboard["decision_stability"]["applied"], True)
        self.assertEqual(result.dashboard["decision_stability"]["reason"], "回测验证")

    def test_parse_text_response_honors_injected_runtime_report_language(self) -> None:
        """Fallback text parsing should use the analyzer's injected config, not the global singleton."""
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer(config=SimpleNamespace(report_language="en"))

        result = analyzer._parse_text_response("bullish buy setup", "AAPL", "Apple")

        self.assertEqual(result.report_language, "en")
        self.assertEqual(result.trend_prediction, "Bullish")
        self.assertEqual(result.operation_advice, "Buy")
        self.assertEqual(result.confidence_level, "Low")
