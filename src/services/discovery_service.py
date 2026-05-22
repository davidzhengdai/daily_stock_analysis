# -*- coding: utf-8 -*-
"""
淘金列表服务

聚合 Scanner / GoldDigger / NewsHeatRadar 的扫描结果，
写入 discovery_list 并提供 AutoTradeService 消费接口。
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional

from src.repositories.discovery_repo import DiscoveryRepo

logger = logging.getLogger(__name__)


class DiscoveryService:
    """淘金列表业务服务层。"""

    def __init__(self, repo: Optional[DiscoveryRepo] = None):
        self.repo = repo or DiscoveryRepo()

    # ------------------------------------------------------------------
    # 写入接口 — 各扫描源完成后调用
    # ------------------------------------------------------------------

    def add_from_scanner(self, scan_id: str) -> int:
        """从 Scanner 结果写入 discovery_list，返回实际写入条数。"""
        try:
            from src.services.market_scanner import get_market_scanner
            result = get_market_scanner().get_result(scan_id) or {}
        except Exception as exc:
            logger.warning("[Discovery] 读取 Scanner 结果失败 (%s): %s", scan_id, exc)
            return 0

        ttl = int(os.getenv("DISCOVERY_SCANNER_TTL_DAYS", "14"))
        count = 0
        for pick in result.get("top_picks", []):
            try:
                themes = pick.get("selection_factors", []) or []
                added = self.repo.add(
                    ticker=pick["ticker"],
                    name=pick.get("name", ""),
                    market=pick.get("market", "US"),
                    sector=pick.get("sector", ""),
                    source="scanner",
                    source_run_id=scan_id,
                    score=float(pick.get("composite_score", 0)),
                    confidence=int(pick.get("llm_confidence", 50)),
                    thesis=pick.get("analysis_summary", ""),
                    themes_json=json.dumps(themes[:5], ensure_ascii=False),
                    trade_horizon="medium",
                    ttl_days=ttl,
                )
                if added is not None:
                    count += 1
            except Exception as exc:
                logger.debug("[Discovery] scanner pick 写入失败 (%s): %s", pick.get("ticker"), exc)
        logger.info("[Discovery] Scanner +%d 条目 from %s (TTL=%dd)", count, scan_id, ttl)
        return count

    def add_from_gold_digger(self, run_id: str) -> int:
        """从 GoldDigger 结果写入 discovery_list，返回实际写入条数。"""
        try:
            from src.services.gold_digger import get_gold_digger
            report = get_gold_digger().get_result(run_id)
        except Exception as exc:
            logger.warning("[Discovery] 读取 GoldDigger 结果失败 (%s): %s", run_id, exc)
            return 0

        if not report:
            return 0

        ttl = int(os.getenv("DISCOVERY_GOLD_DIGGER_TTL_DAYS", "21"))
        count = 0
        picks = report.gold_picks if hasattr(report, "gold_picks") else (report.get("gold_picks", []) if isinstance(report, dict) else [])
        for pick in picks:
            try:
                if isinstance(pick, dict):
                    ticker = pick.get("ticker", "")
                    name = pick.get("name", "")
                    market = pick.get("market", "US")
                    sector = pick.get("sector", "")
                    score = float(pick.get("composite_score", 0))
                    confidence = int(pick.get("llm_confidence", 50))
                    thesis = pick.get("analysis_summary", "")
                    themes = pick.get("matched_themes", []) or []
                else:
                    ticker = pick.ticker
                    name = pick.name
                    market = pick.market
                    sector = pick.sector
                    score = float(pick.composite_score)
                    confidence = int(pick.llm_confidence)
                    thesis = pick.analysis_summary
                    themes = list(pick.matched_themes) if pick.matched_themes else []

                added = self.repo.add(
                    ticker=ticker,
                    name=name,
                    market=market,
                    sector=sector,
                    source="gold_digger",
                    source_run_id=run_id,
                    score=score,
                    confidence=confidence,
                    thesis=thesis,
                    themes_json=json.dumps(themes[:5], ensure_ascii=False),
                    trade_horizon="medium",
                    ttl_days=ttl,
                )
                if added is not None:
                    count += 1
            except Exception as exc:
                logger.debug("[Discovery] gold_digger pick 写入失败: %s", exc)
        logger.info("[Discovery] GoldDigger +%d 条目 from %s (TTL=%dd)", count, run_id, ttl)
        return count

    def add_from_heat_radar(self, run_id: str) -> int:
        """从 NewsHeatRadar 结果写入 discovery_list，返回实际写入条数。"""
        try:
            from src.services.news_heat_radar import get_news_heat_radar
            report = get_news_heat_radar().get_result(run_id)
        except Exception as exc:
            logger.warning("[Discovery] 读取 HeatRadar 结果失败 (%s): %s", run_id, exc)
            return 0

        if not report:
            return 0

        ttl = int(os.getenv("DISCOVERY_HEAT_RADAR_TTL_DAYS", "5"))
        count = 0
        picks = report.hot_picks if hasattr(report, "hot_picks") else (report.get("hot_picks", []) if isinstance(report, dict) else [])
        for pick in picks:
            try:
                if isinstance(pick, dict):
                    ticker = pick.get("ticker", "")
                    name = pick.get("name", "")
                    market = pick.get("market", "US")
                    sector = pick.get("sector", "")
                    score = float(pick.get("heat_score", 0))
                    confidence = int(pick.get("llm_confidence", 50))
                    thesis = pick.get("catalyst_thesis", "")
                    themes = [pick.get("matched_sector", "")] if pick.get("matched_sector") else []
                else:
                    ticker = pick.ticker
                    name = pick.name
                    market = pick.market
                    sector = pick.sector
                    score = float(pick.heat_score)
                    confidence = int(pick.llm_confidence)
                    thesis = pick.catalyst_thesis
                    themes = [pick.matched_sector] if pick.matched_sector else []

                added = self.repo.add(
                    ticker=ticker,
                    name=name,
                    market=market,
                    sector=sector,
                    source="heat_radar",
                    source_run_id=run_id,
                    score=score,
                    confidence=confidence,
                    thesis=thesis,
                    themes_json=json.dumps(themes, ensure_ascii=False),
                    trade_horizon="short",
                    ttl_days=ttl,
                )
                if added is not None:
                    count += 1
            except Exception as exc:
                logger.debug("[Discovery] heat_radar pick 写入失败: %s", exc)
        logger.info("[Discovery] HeatRadar +%d 条目 from %s (TTL=%dd)", count, run_id, ttl)
        return count

    # ------------------------------------------------------------------
    # 读取接口
    # ------------------------------------------------------------------

    def list_active(self) -> List[dict]:
        """返回所有活跃条目（先清理过期）。"""
        expired = self.repo.expire_stale()
        if expired:
            logger.debug("[Discovery] 清理 %d 条过期记录", expired)
        return self.repo.list_active()

    def get_auto_trade_stocks(self) -> List[dict]:
        """供 AutoTradeService 使用：返回活跃去重列表，含 trade_context 标记。"""
        self.repo.expire_stale()
        return self.repo.get_auto_trade_stocks()

    def expire_stale(self) -> int:
        return self.repo.expire_stale()

    def reject(self, item_id: int) -> bool:
        return self.repo.reject(item_id)

    def stats(self) -> Dict[str, Any]:
        return self.repo.stats()

    def list_history(
        self,
        *,
        ticker: Optional[str] = None,
        item_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[dict]:
        return self.repo.list_history(ticker=ticker, item_id=item_id, limit=limit)
