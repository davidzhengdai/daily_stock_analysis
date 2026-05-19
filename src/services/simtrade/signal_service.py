# -*- coding: utf-8 -*-
"""
===================================
AI 交易信号生成服务
===================================

职责：
1. 对单只股票生成 AI 交易信号（buy/sell/hold/skip）
2. 集成技术指标预评分 + LLM 深度分析
3. 将信号写入 simulated_ai_signals 表
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.repositories.simtrade_repo import SimTradeRepo

logger = logging.getLogger(__name__)

# 信号有效期（小时）
_SIGNAL_TTL_HOURS = 4

_SIGNAL_PROMPT = """\
You are an AI trading advisor for a paper-trading simulation system.
Analyze the following stock data and generate a precise trading signal.

Stock: {code} ({name}), Market: {market}
Current Price: {price} {currency}

Technical Data (recent):
  MA5={ma5}, MA10={ma10}, MA20={ma20}
  Volume Ratio={volume_ratio}
  5-day price change: {change_5d}%
  Pre-computed technical score (rule-based): {technical_score:.2f} (0=bearish, 1=bullish)

Current Position: {qty} shares at avg cost {avg_cost} (P&L: {pnl_pct}%)
Portfolio: total equity ≈ {equity} CNY, available {currency} cash = {cash}
Max position per stock: {max_pos}% of portfolio
News sentiment: {sentiment_summary}
Auto-trade mode: {mode}
  - conservative: only trade at confidence>0.75, size ≤10%
  - balanced: confidence>0.65, size ≤20%
  - aggressive: confidence>0.55, size ≤30%

Respond with JSON ONLY (no markdown, no explanation):
{{
  "signal": "buy|sell|hold|skip",
  "confidence": 0.0-1.0,
  "technical_score": 0.0-1.0,
  "sentiment_score": 0.0-1.0,
  "risk_score": 0.0-1.0,
  "position_size_pct": 0.0-100.0,
  "suggested_price": null or float,
  "stop_loss": null or float,
  "take_profit": null or float,
  "reasoning": "one concise sentence max 120 chars"
}}

Rules:
- signal=sell when: stop-loss approached, take-profit hit, or trend reversal confirmed
- signal=skip when: data insufficient, already at max position, or low conviction
- position_size_pct: % of available cash to deploy (not % of portfolio)
- stop_loss/take_profit: absolute price levels (not percentages)
"""


class SignalService:
    """AI 交易信号生成服务。"""

    def __init__(
        self,
        repo: Optional[SimTradeRepo] = None,
        analyzer: Any = None,
    ):
        self.repo = repo or SimTradeRepo()
        self._analyzer = analyzer

    def _get_analyzer(self) -> Any:
        if self._analyzer is None:
            from src.analyzer import GeminiAnalyzer
            model = os.getenv('SIMTRADE_LLM_MODEL', '').strip() or None
            if model:
                import dataclasses
                from src.config import get_config
                custom_cfg = dataclasses.replace(get_config(), litellm_model=model)
                self._analyzer = GeminiAnalyzer(config=custom_cfg)
            else:
                self._analyzer = GeminiAnalyzer()
        return self._analyzer

    def generate_signal(
        self,
        code: str,
        market: str,
        name: str = "",
    ) -> Dict[str, Any]:
        """
        为指定股票生成 AI 信号，写入数据库并返回。

        - code: 股票代码
        - market: CN 或 US
        - name: 股票名称（可选）
        """
        code = code.upper()
        market = market.upper()
        currency = 'CNY' if market == 'CN' else 'USD'
        fx_rate = float(os.getenv('SIMTRADE_USD_CNY_RATE', '7.25'))

        acct = self.repo.get_or_create_account()
        account_id = acct['id']
        mode = acct.get('auto_trade_mode', 'conservative')

        # ---- 获取市场数据 ----
        stock_data = self._get_stock_data(code)
        if not stock_data:
            logger.warning("[SignalService] 无法获取 %s 市场数据，信号跳过", code)
            return self.repo.create_signal(
                account_id=account_id,
                code=code, name=name, market=market,
                signal='skip', confidence=0.0,
                reasoning='市场数据不可用',
                status='rejected',
            )

        current_price = stock_data.get('close', 0.0) or 0.0
        ma5 = stock_data.get('ma5') or current_price
        ma10 = stock_data.get('ma10') or current_price
        ma20 = stock_data.get('ma20') or current_price
        volume_ratio = stock_data.get('volume_ratio') or 1.0
        change_5d = stock_data.get('change_5d', 0.0) or 0.0

        # ---- 技术预评分 ----
        technical_score = self._compute_technical_score(
            current_price, ma5, ma10, ma20, volume_ratio, change_5d
        )

        # ---- 当前持仓 ----
        pos = self.repo.get_position(account_id, code)
        qty = pos['qty'] if pos else 0
        avg_cost = pos['avg_cost'] if pos else 0.0
        pnl_pct = pos['unrealized_pnl_pct'] if pos else 0.0

        # ---- 账户状态 ----
        positions = self.repo.list_positions(account_id)
        market_value_cny = sum(
            (p['last_price'] * p['qty']) if p['currency'] == 'CNY'
            else (p['last_price'] * p['qty'] * fx_rate)
            for p in positions
        )
        cash_cny = acct['cash_cny']
        cash_usd = acct['cash_usd']
        total_equity = cash_cny + cash_usd * fx_rate + market_value_cny
        available_cash = cash_cny if currency == 'CNY' else cash_usd

        # ---- 当前仓位占比检查 ----
        pos_value = (current_price * qty) * (1 if currency == 'CNY' else fx_rate)
        pos_weight_pct = (pos_value / total_equity * 100) if total_equity > 0 else 0.0
        max_pos = acct.get('max_position_pct', 20.0)

        # ---- 新闻情绪 ----
        sentiment_summary = self._get_sentiment(code)

        # ---- 构建 Prompt ----
        prompt = _SIGNAL_PROMPT.format(
            code=code, name=name or code, market=market, price=current_price, currency=currency,
            ma5=round(ma5, 3), ma10=round(ma10, 3), ma20=round(ma20, 3),
            volume_ratio=round(volume_ratio, 2), change_5d=round(change_5d, 2),
            technical_score=technical_score,
            qty=qty, avg_cost=round(avg_cost, 3), pnl_pct=round(pnl_pct, 2),
            equity=round(total_equity, 2), cash=round(available_cash, 2),
            max_pos=max_pos, sentiment_summary=sentiment_summary, mode=mode,
        )

        # ---- LLM 调用 ----
        parsed: Dict[str, Any] = {}
        try:
            analyzer = self._get_analyzer()
            raw = analyzer.generate_text(prompt, max_tokens=1024, temperature=0.2)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("LLM returned empty response")
            parsed = self._parse_llm_response(raw)
        except Exception as exc:
            logger.warning("[SignalService] LLM 调用失败 %s: %s", code, exc)
            parsed = {
                'signal': 'skip', 'confidence': 0.0,
                'technical_score': technical_score, 'sentiment_score': 0.5,
                'risk_score': 0.5, 'position_size_pct': 0.0,
                'suggested_price': None, 'stop_loss': None, 'take_profit': None,
                'reasoning': f'LLM 不可用: {exc}',
            }

        # ---- 风险管理覆写 ----
        signal = parsed.get('signal', 'skip')
        confidence = float(parsed.get('confidence', 0.0))
        min_conf = acct.get('min_signal_confidence', 0.65)

        # 仓位已满 → 禁止买入
        if signal == 'buy' and pos_weight_pct >= max_pos:
            signal = 'skip'
            parsed['reasoning'] = f"仓位已达 {pos_weight_pct:.1f}% ≥ 上限 {max_pos}%，跳过买入"

        # 无持仓 → 无法卖出
        if signal == 'sell' and qty == 0:
            signal = 'skip'
            parsed['reasoning'] = '无持仓，跳过卖出信号'

        # 置信度不足 → 降级
        if signal in ('buy', 'sell') and confidence < min_conf:
            signal = 'skip'
            parsed['reasoning'] = f"置信度 {confidence:.2f} < 阈值 {min_conf}，跳过"

        # ---- 计算建议数量 ----
        suggested_qty = None
        pos_size_pct = float(parsed.get('position_size_pct', 10.0) or 10.0)
        if signal == 'buy' and current_price > 0 and available_cash > 0:
            max_spend = available_cash * pos_size_pct / 100
            lot = 100 if market == 'CN' else 1
            raw_qty = int(max_spend / current_price)
            suggested_qty = max(lot, (raw_qty // lot) * lot)
        elif signal == 'sell' and qty > 0:
            suggested_qty = qty

        # ---- 写入 DB ----
        signal_record = self.repo.create_signal(
            account_id=account_id,
            code=code, name=name, market=market,
            signal=signal,
            confidence=round(confidence, 3),
            price_at_signal=current_price,
            technical_score=round(float(parsed.get('technical_score', technical_score)), 3),
            sentiment_score=round(float(parsed.get('sentiment_score', 0.5) or 0.5), 3),
            risk_score=round(float(parsed.get('risk_score', 0.5) or 0.5), 3),
            position_size_pct=round(pos_size_pct, 1),
            suggested_qty=suggested_qty,
            suggested_price=parsed.get('suggested_price'),
            stop_loss=parsed.get('stop_loss'),
            take_profit=parsed.get('take_profit'),
            reasoning=str(parsed.get('reasoning', ''))[:300],
            signal_factors=json.dumps({
                'technical_pre_score': round(technical_score, 3),
                'ma5_ma10_ma20': [round(ma5, 3), round(ma10, 3), round(ma20, 3)],
                'volume_ratio': round(volume_ratio, 2),
                'change_5d': round(change_5d, 2),
                'pos_weight_pct': round(pos_weight_pct, 2),
                'available_cash': round(available_cash, 2),
                'total_equity': round(total_equity, 2),
            }, ensure_ascii=False),
            status='pending',
        )
        return signal_record

    def generate_signals_for_watchlist(self) -> List[Dict[str, Any]]:
        """为自选股全量生成信号，返回所有信号列表。"""
        from src.services.watchlist_service import WatchlistService
        watchlist = WatchlistService().list_all()
        signals = []
        for item in watchlist:
            code = item['code']
            name = item.get('name', '')
            market = self._infer_market(code)
            try:
                sig = self.generate_signal(code, market, name)
                signals.append(sig)
            except Exception as exc:
                logger.warning("[SignalService] %s 信号生成失败: %s", code, exc)
        return signals

    def list_signals(self, limit: int = 30) -> List[Dict[str, Any]]:
        acct = self.repo.get_or_create_account()
        self.repo.expire_old_signals(acct['id'])
        return self.repo.list_signals(acct['id'], limit=limit)

    # -------------------------------------------------------
    # 内部工具
    # -------------------------------------------------------

    def _compute_technical_score(
        self,
        price: float,
        ma5: float,
        ma10: float,
        ma20: float,
        volume_ratio: float,
        change_5d: float,
    ) -> float:
        score = 0.5
        # MA 多头排列
        if ma5 > ma10 > ma20:
            score += 0.20
        elif ma5 < ma10 < ma20:
            score -= 0.20
        # 价格位于 MA20 上方
        if price > ma20:
            score += 0.10
        elif price < ma20:
            score -= 0.10
        # 量比放大
        if volume_ratio >= 2.0:
            score += 0.15
        elif volume_ratio >= 1.5:
            score += 0.08
        elif volume_ratio < 0.7:
            score -= 0.08
        # 近 5 日涨跌
        if change_5d > 5:
            score += 0.05
        elif change_5d < -5:
            score -= 0.05
        return round(max(0.0, min(1.0, score)), 3)

    def _get_stock_data(self, code: str) -> Optional[Dict[str, Any]]:
        """获取最近一个交易日数据。"""
        try:
            from src.storage import DatabaseManager, StockDaily
            from sqlalchemy import select, desc
            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                rows = session.execute(
                    select(StockDaily)
                    .where(StockDaily.code == code)
                    .order_by(desc(StockDaily.date))
                    .limit(6)
                ).scalars().all()
                if not rows:
                    return None
                latest = rows[0]
                change_5d = 0.0
                if len(rows) >= 5 and rows[4].close and latest.close:
                    change_5d = (latest.close / rows[4].close - 1) * 100
                return {
                    'close': latest.close,
                    'ma5': latest.ma5,
                    'ma10': latest.ma10,
                    'ma20': latest.ma20,
                    'volume_ratio': latest.volume_ratio,
                    'change_5d': round(change_5d, 2),
                }
        except Exception as exc:
            logger.debug("[SignalService] 数据获取失败 %s: %s", code, exc)
            return None

    def _get_sentiment(self, code: str) -> str:
        """从 Sentinel 获取新闻情绪摘要（可选，失败静默降级）。"""
        try:
            from src.services.sentinel.store import NewsStore
            store = NewsStore()
            items = store.get_news_for_stock(code, limit=5)
            if not items:
                return "无可用新闻"
            pos = sum(1 for i in items if getattr(i, 'sentiment', '') == 'positive')
            neg = sum(1 for i in items if getattr(i, 'sentiment', '') == 'negative')
            return f"{len(items)} 条近期新闻，正面 {pos} 条，负面 {neg} 条"
        except Exception:
            return "情报中心不可用"

    @staticmethod
    def _infer_market(code: str) -> str:
        """根据股票代码推断市场：纯数字为 A 股（CN），其余为美股（US）。"""
        return 'CN' if re.match(r'^\d+$', code) else 'US'

    @staticmethod
    def _parse_llm_response(raw: Optional[str]) -> Dict[str, Any]:
        """从 LLM 输出中提取 JSON。"""
        if not isinstance(raw, str) or not raw.strip():
            return {}

        # 去除 markdown 代码块
        text = re.sub(r'```[a-z]*\n?', '', raw).strip()
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 提取第一个 {...} 块
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {}
