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
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from src.repositories.simtrade_repo import SimTradeRepo

logger = logging.getLogger(__name__)

# 信号有效期（小时）
_SIGNAL_TTL_HOURS = 4
_PRICE_MISMATCH_LIMIT_PCT = 3.0
_US_MIN_SIGNAL_CONFIDENCE = 0.75
_US_LOW_PRICE_MIN_SIGNAL_CONFIDENCE = 0.82
_US_LOW_PRICE_THRESHOLD = 5.0
_US_MAX_BUY_CASH_PCT = 8.0
_US_ADD_MAX_BUY_CASH_PCT = 3.0
_US_LOW_PRICE_MAX_BUY_CASH_PCT = 2.0
_US_MAX_RECENT_AUTO_BUYS = 2
_US_RECENT_BUY_LOOKBACK_HOURS = 48
_US_ADD_LOSS_BLOCK_PCT = -0.5
_TECHNICAL_PRIMARY_WEIGHTS = {'technical': 0.70, 'sentinel': 0.15, 'risk': 0.15}
_SENTINEL_PRIMARY_WEIGHTS = {'technical': 0.35, 'sentinel': 0.50, 'risk': 0.15}


def _parse_positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("%s 配置无效，使用默认值 %d", name, default)
        return default


_STOP_LOSS_COOLDOWN_MINUTES = _parse_positive_int_env('SIMTRADE_STOP_LOSS_COOLDOWN_MINUTES', 240)

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
Decision weight profile: technical={technical_weight}, sentinel={sentinel_weight}, risk={risk_weight}
Auto-trade mode: {mode}
  - conservative: only trade at confidence>0.75, size ≤10%
  - balanced: confidence>0.65, size ≤20%
  - aggressive: confidence>0.55, size ≤30%

US auto-trade guardrails:
  - US buys require higher conviction than CN buys.
  - Do not average down a losing US position.
  - Keep US single-order size small; never use oversized position_size_pct.
  - Treat low-priced/speculative US stocks as high risk.

Respond with JSON ONLY (no markdown, no explanation):
{{
  "signal": "buy|sell|hold|skip",
  "confidence": 0.0-1.0,
  "technical_score": 0.0-1.0,
  "sentiment_score": 0.0-1.0,
  "risk_score": 0.0-1.0,
  "risk_flags": ["short_machine_readable_flags"],
  "position_size_pct": 0.0-100.0,
  "suggested_price": null or float,
  "stop_loss": null or float,
  "take_profit": null or float,
  "reasoning": "one concise sentence max 120 chars"
}}

Rules:
- signal=sell when: stop-loss approached, take-profit hit, or trend reversal confirmed
- signal=skip when: data insufficient, already at max position, or low conviction
- Add risk_flags for issues such as recent_stop_loss, stale_price, price_mismatch, max_position, concentration
- position_size_pct: % of available cash to deploy (not % of portfolio)
- stop_loss/take_profit: absolute price levels (not percentages)
"""

_SELL_REVIEW_PROMPT = """\
You are the execution risk reviewer for an AI paper-trading system.
Review whether a proposed SELL should be executed now.

Stock: {code} ({name}), Market: {market}
Current Price: {price} {currency}
Position: {qty} shares, avg cost {avg_cost}, unrealized P&L {pnl_pct}%
Position weight: {pos_weight_pct}% of portfolio, max position policy: {max_pos}%

Trend context:
  MA5={ma5}, MA10={ma10}, MA20={ma20}
  5-day price change={change_5d}%
  Technical score={technical_score}

News context:
  {sentiment_summary}

Original SELL proposal:
  confidence={confidence}
  risk_flags={risk_flags}
  reasoning={reasoning}

Decide if execution should proceed. Consider trend, news catalysts, position risk,
stop-loss/take-profit, and whether this is a full exit or only a trim.

Respond with JSON ONLY:
{{
  "approve": true|false,
  "action": "sell|trim|hold",
  "quantity_pct": 0.0-100.0,
  "confidence": 0.0-1.0,
  "reasoning": "one concise sentence max 160 chars"
}}

Guidance:
- approve=false/action=hold if the sell thesis is weak or contradicted by trend/news.
- action=trim if the issue is position size or concentration but the thesis remains positive.
- action=sell only when there is a clear exit reason such as stop-loss, take-profit,
  confirmed reversal, or materially negative news.
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
        trade_context: str = "watchlist",
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
        display_name = f"{code}({name})" if name else code
        logger.info("[SignalService] 开始评估 %s 市场=%s", display_name, market)

        acct = self.repo.get_or_create_account()
        account_id = acct['id']
        mode = acct.get('auto_trade_mode', 'conservative')

        # ---- 获取市场数据 ----
        stock_data = self._get_stock_data(code)
        if not stock_data:
            logger.warning("[SignalService] 无法获取 %s 市场数据，信号跳过", display_name)
            return self.repo.create_signal(
                account_id=account_id,
                code=code, name=name, market=market,
                signal='skip', confidence=0.0,
                reasoning='市场数据不可用',
                status='rejected',
            )

        daily_price = stock_data.get('close', 0.0) or 0.0
        current_price = daily_price
        data_date = stock_data.get('date')
        risk_flags: List[str] = []
        realtime_price = self._get_realtime_price(code)
        if realtime_price and realtime_price > 0:
            current_price = realtime_price
            if daily_price > 0:
                mismatch_pct = abs(realtime_price / daily_price - 1) * 100
                if mismatch_pct > _PRICE_MISMATCH_LIMIT_PCT:
                    risk_flags.append('price_mismatch')
        else:
            risk_flags.append('realtime_price_unavailable')
        if data_date and data_date != date.today():
            risk_flags.append('stale_daily_bar')
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
        if pos_weight_pct >= max_pos * 0.8:
            risk_flags.append('concentration')
        if pos_weight_pct >= max_pos:
            risk_flags.append('max_position')
        recent_loss_order = self._recent_loss_sell_order(account_id, code)
        if recent_loss_order:
            risk_flags.append('recent_stop_loss')
        recent_auto_buy_count = (
            self._recent_auto_buy_count(account_id, code, market)
            if market == 'US'
            else 0
        )

        # ---- 新闻情绪 ----
        sentiment_summary, news_bias = self._get_sentiment(code)
        context_weights = self._context_weight_profile(news_bias)

        # ---- 构建 Prompt ----
        prompt = _SIGNAL_PROMPT.format(
            code=code, name=name or code, market=market, price=current_price, currency=currency,
            ma5=round(ma5, 3), ma10=round(ma10, 3), ma20=round(ma20, 3),
            volume_ratio=round(volume_ratio, 2), change_5d=round(change_5d, 2),
            technical_score=technical_score,
            qty=qty, avg_cost=round(avg_cost, 3), pnl_pct=round(pnl_pct, 2),
            equity=round(total_equity, 2), cash=round(available_cash, 2),
            technical_weight=context_weights['technical'],
            sentinel_weight=context_weights['sentinel'],
            risk_weight=context_weights['risk'],
            max_pos=max_pos, sentiment_summary=sentiment_summary, mode=mode,
        )

        # 淘金列表短线上下文：引导 LLM 使用更紧的止损和催化剂导向判断
        if trade_context == "discovery_short_term":
            prompt += (
                "\n\nTrade context: SHORT-TERM discovery pick (5-day horizon). "
                "This stock was identified by the heat radar or discovery scanner as having "
                "an immediate catalyst. Prioritize momentum and catalyst proximity over "
                "long-term fundamentals. Suggest stop_loss 3-5% below entry price, "
                "take_profit 10-15% above entry price."
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
        # 淘金列表短线：允许较低置信度阈值（外部催化剂已确认）
        if trade_context == "discovery_short_term":
            min_conf = min(min_conf, float(os.getenv('DISCOVERY_MIN_SIGNAL_CONFIDENCE', '0.55')))
        llm_risk_flags = self._parse_risk_flags(parsed.get('risk_flags'))
        risk_flags = sorted(set(risk_flags + llm_risk_flags))
        if market == 'US':
            min_conf = max(min_conf, _US_MIN_SIGNAL_CONFIDENCE)
            if 0 < current_price < _US_LOW_PRICE_THRESHOLD:
                min_conf = max(min_conf, _US_LOW_PRICE_MIN_SIGNAL_CONFIDENCE)
                risk_flags = sorted(set(risk_flags + ['low_price_us_stock']))

        if signal in ('buy', 'sell') and 'realtime_price_unavailable' in risk_flags:
            signal = 'skip'
            parsed['reasoning'] = '实时成交价不可用，跳过自动交易'

        if signal in ('buy', 'sell') and 'price_mismatch' in risk_flags:
            signal = 'skip'
            parsed['reasoning'] = '实时价与日线价偏差过大，跳过自动交易'

        if signal == 'buy' and recent_loss_order:
            signal = 'skip'
            filled_at = recent_loss_order.get('filled_at') or ''
            parsed['reasoning'] = (
                f"最近 {_STOP_LOSS_COOLDOWN_MINUTES} 分钟内已亏损/止损卖出"
                f"（{filled_at[:16]}），冷却期内禁止重新买入"
            )

        if signal == 'buy' and market == 'US' and qty > 0 and pnl_pct <= _US_ADD_LOSS_BLOCK_PCT:
            signal = 'skip'
            parsed['reasoning'] = (
                f"美股持仓浮亏 {pnl_pct:.1f}%，禁止自动加仓摊平"
            )

        if signal == 'buy' and market == 'US' and recent_auto_buy_count >= _US_MAX_RECENT_AUTO_BUYS:
            signal = 'skip'
            parsed['reasoning'] = (
                f"最近 {_US_RECENT_BUY_LOOKBACK_HOURS} 小时已自动买入 {recent_auto_buy_count} 次，"
                "跳过重复追单"
            )

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
        sell_review: Dict[str, Any] = {}
        pos_size_pct = float(parsed.get('position_size_pct', 10.0) or 10.0)
        if market == 'US':
            pos_size_pct = self._clamp_us_position_size_pct(
                pos_size_pct,
                current_price=current_price,
                has_position=qty > 0,
                risk_flags=risk_flags,
            )
        if signal == 'buy' and current_price > 0 and available_cash > 0:
            max_spend = available_cash * pos_size_pct / 100
            lot = 100 if market == 'CN' else 1
            raw_qty = int(max_spend / current_price)
            suggested_qty = max(lot, (raw_qty // lot) * lot)
        elif signal == 'sell' and qty > 0:
            sell_review = self._review_sell_with_ai(
                code=code,
                name=name,
                market=market,
                currency=currency,
                current_price=current_price,
                qty=qty,
                avg_cost=avg_cost,
                pnl_pct=pnl_pct,
                pos_weight_pct=pos_weight_pct,
                max_pos=max_pos,
                ma5=ma5,
                ma10=ma10,
                ma20=ma20,
                change_5d=change_5d,
                technical_score=technical_score,
                sentiment_summary=sentiment_summary,
                confidence=confidence,
                risk_flags=risk_flags,
                reasoning=str(parsed.get('reasoning', '')),
            )
            if not sell_review.get('approve') or sell_review.get('action') == 'hold':
                signal = 'skip'
                parsed['reasoning'] = f"AI 卖出复核未通过: {sell_review.get('reasoning', '未给出理由')}"
            else:
                action = sell_review.get('action')
                if action == 'trim':
                    quantity_pct = float(sell_review.get('quantity_pct') or 0.0)
                    lot = 100 if market == 'CN' else 1
                    raw_qty = int(qty * max(0.0, min(quantity_pct, 100.0)) / 100)
                    suggested_qty = max(lot, (raw_qty // lot) * lot)
                    suggested_qty = min(qty, suggested_qty)
                else:
                    suggested_qty = qty
                parsed['reasoning'] = f"{parsed.get('reasoning', '')}；AI 卖出复核: {sell_review.get('reasoning', '')}"

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
                'risk_flags': risk_flags,
                'news_bias': news_bias,
                'sentiment_summary': sentiment_summary,
                'context_weights': context_weights,
                'sell_review': sell_review,
                'realtime_price': round(realtime_price, 4) if realtime_price else None,
                'daily_price': round(daily_price, 4) if daily_price else None,
                'daily_date': data_date.isoformat() if data_date else None,
                'daily_data_source': stock_data.get('data_source'),
                'daily_data_updated_at': stock_data.get('data_updated_at'),
                'recent_loss_sell_order_id': recent_loss_order.get('id') if recent_loss_order else None,
                'recent_auto_buy_count': recent_auto_buy_count,
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
        """获取最近一个交易日数据，缓存缺失或过期时先同步刷新。"""
        stock_data = self._read_stock_data_from_cache(code)
        if stock_data and not self._is_daily_data_stale(stock_data.get('date')):
            return stock_data

        if stock_data:
            logger.warning(
                "[SignalService] %s 日线数据过期，最新日期=%s，尝试刷新后再生成信号",
                code,
                stock_data.get('date'),
            )
        else:
            logger.info("[SignalService] %s 本地日线数据缺失，尝试刷新后再生成信号", code)

        if self._refresh_stock_data(code):
            stock_data = self._read_stock_data_from_cache(code)

        if not stock_data:
            return None

        if self._is_daily_data_stale(stock_data.get('date')):
            logger.warning(
                "[SignalService] %s 日线数据刷新后仍过期，最新日期=%s，跳过自动交易信号",
                code,
                stock_data.get('date'),
            )
            return None

        return stock_data

    @staticmethod
    def _is_daily_data_stale(data_date: Any) -> bool:
        return bool(data_date and (date.today() - data_date).days > 3)

    def _refresh_stock_data(self, code: str) -> bool:
        """Fetch recent daily bars and persist them for technical analysis."""
        try:
            from data_provider.base import DataFetcherManager
            from src.storage import DatabaseManager

            days = _parse_positive_int_env('SIMTRADE_DAILY_REFRESH_DAYS', 120)
            df, source = DataFetcherManager().get_daily_data(code, days=days)
            if df is None or df.empty:
                logger.warning("[SignalService] %s 日线刷新未返回数据", code)
                return False
            DatabaseManager.get_instance().save_daily_data(
                df,
                code,
                data_source=f"simtrade:{source}",
            )
            latest_date = None
            if 'date' in df.columns:
                try:
                    latest_date = df['date'].max()
                except Exception:
                    latest_date = None
            logger.info(
                "[SignalService] %s 日线刷新完成，source=%s, rows=%d, latest=%s",
                code,
                source,
                len(df),
                latest_date,
            )
            return True
        except Exception as exc:
            logger.warning("[SignalService] %s 日线刷新失败: %s", code, exc)
            return False

    def _read_stock_data_from_cache(self, code: str) -> Optional[Dict[str, Any]]:
        """Read recent daily bars from local cache."""
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
                    'date': latest.date,
                    'close': latest.close,
                    'ma5': latest.ma5,
                    'ma10': latest.ma10,
                    'ma20': latest.ma20,
                    'volume_ratio': latest.volume_ratio,
                    'change_5d': round(change_5d, 2),
                    'data_source': latest.data_source,
                    'data_updated_at': latest.updated_at.isoformat() if latest.updated_at else None,
                }
        except Exception as exc:
            logger.debug("[SignalService] 数据获取失败 %s: %s", code, exc)
            return None

    def _get_realtime_price(self, code: str) -> Optional[float]:
        """Fetch realtime price for execution-grade signal review."""
        try:
            from src.services.simtrade.order_service import _get_shared_fetcher_manager
            logger.info("[SignalService] 查询实时价格 %s", code)
            quote = _get_shared_fetcher_manager().get_realtime_quote(code, log_final_failure=False)
            price = getattr(quote, 'price', None) if quote is not None else None
            if price is not None and float(price) > 0:
                logger.info("[SignalService] %s 实时价格 %.4f", code, float(price))
            return float(price) if price is not None and float(price) > 0 else None
        except Exception as exc:
            logger.debug("[SignalService] 实时价格获取失败 %s: %s", code, exc)
            return None

    def _review_sell_with_ai(
        self,
        *,
        code: str,
        name: str,
        market: str,
        currency: str,
        current_price: float,
        qty: int,
        avg_cost: float,
        pnl_pct: float,
        pos_weight_pct: float,
        max_pos: float,
        ma5: float,
        ma10: float,
        ma20: float,
        change_5d: float,
        technical_score: float,
        sentiment_summary: str,
        confidence: float,
        risk_flags: List[str],
        reasoning: str,
    ) -> Dict[str, Any]:
        """Ask the LLM for execution confirmation before any auto sell."""
        prompt = _SELL_REVIEW_PROMPT.format(
            code=code,
            name=name or code,
            market=market,
            price=round(current_price, 4),
            currency=currency,
            qty=qty,
            avg_cost=round(avg_cost, 4),
            pnl_pct=round(pnl_pct, 2),
            pos_weight_pct=round(pos_weight_pct, 2),
            max_pos=round(max_pos, 2),
            ma5=round(ma5, 4),
            ma10=round(ma10, 4),
            ma20=round(ma20, 4),
            change_5d=round(change_5d, 2),
            technical_score=round(technical_score, 3),
            sentiment_summary=sentiment_summary,
            confidence=round(confidence, 3),
            risk_flags=", ".join(risk_flags) or "none",
            reasoning=reasoning or "none",
        )
        try:
            raw = self._get_analyzer().generate_text(prompt, max_tokens=512, temperature=0.1)
            parsed = self._parse_llm_response(raw)
            action = str(parsed.get('action') or 'hold').lower()
            if action not in ('sell', 'trim', 'hold'):
                action = 'hold'
            return {
                'approve': bool(parsed.get('approve')) and action in ('sell', 'trim'),
                'action': action,
                'quantity_pct': float(parsed.get('quantity_pct') or 0.0),
                'confidence': float(parsed.get('confidence') or 0.0),
                'reasoning': str(parsed.get('reasoning') or '')[:300],
            }
        except Exception as exc:
            logger.warning("[SignalService] AI 卖出复核失败 %s: %s", code, exc)
            return {
                'approve': False,
                'action': 'hold',
                'quantity_pct': 0.0,
                'confidence': 0.0,
                'reasoning': f'AI 卖出复核不可用: {exc}',
            }

    def _recent_loss_sell_order(self, account_id: int, code: str) -> Optional[Dict[str, Any]]:
        cutoff = datetime.now() - timedelta(minutes=_STOP_LOSS_COOLDOWN_MINUTES)
        return self.repo.get_recent_loss_sell_order(account_id, code, cutoff)

    def _recent_auto_buy_count(self, account_id: int, code: str, market: str) -> int:
        cutoff = datetime.now() - timedelta(hours=_US_RECENT_BUY_LOOKBACK_HOURS)
        return self.repo.count_recent_filled_auto_buys(account_id, code, market, cutoff)

    @staticmethod
    def _clamp_us_position_size_pct(
        position_size_pct: float,
        *,
        current_price: float,
        has_position: bool,
        risk_flags: List[str],
    ) -> float:
        cap = _US_ADD_MAX_BUY_CASH_PCT if has_position else _US_MAX_BUY_CASH_PCT
        if 0 < current_price < _US_LOW_PRICE_THRESHOLD or 'low_price_us_stock' in risk_flags:
            cap = min(cap, _US_LOW_PRICE_MAX_BUY_CASH_PCT)
        return max(0.0, min(position_size_pct, cap))

    @staticmethod
    def _parse_risk_flags(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [str(v) for v in value if v]
        return []

    def _get_sentiment(self, code: str) -> tuple[str, str]:
        """从 Sentinel 获取新闻情绪摘要（可选，失败静默降级）。"""
        try:
            from src.services.sentinel.store import NewsStore
            store = NewsStore()
            items = store.get_news_for_stock(code, limit=5)
            if not items:
                return "无可用新闻", "neutral"
            pos = sum(1 for i in items if getattr(i, 'sentiment', '') == 'positive')
            neg = sum(1 for i in items if getattr(i, 'sentiment', '') == 'negative')
            if pos > neg:
                bias = "positive"
            elif neg > pos:
                bias = "negative"
            else:
                bias = "neutral"
            return f"{len(items)} 条近期新闻，正面 {pos} 条，负面 {neg} 条", bias
        except Exception:
            return "情报中心不可用", "unknown"

    @staticmethod
    def _context_weight_profile(news_bias: str) -> Dict[str, float]:
        """Return the decision weights used to balance technicals and Sentinel news."""
        if news_bias in ('positive', 'negative'):
            return dict(_SENTINEL_PRIMARY_WEIGHTS)
        return dict(_TECHNICAL_PRIMARY_WEIGHTS)

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
