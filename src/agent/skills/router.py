# -*- coding: utf-8 -*-
"""
SkillRouter — rule-based skill selection.

Selects which trading skills to apply based on:
1. User-explicit request (highest priority)
2. Market regime detection from technical data in ``AgentContext``
3. Centralised default fallback
"""

from __future__ import annotations

import logging
from typing import List, Optional

from src.agent.protocols import AgentContext
from src.agent.skills.defaults import (
    get_default_router_skill_ids,
    get_regime_skill_ids,
)

logger = logging.getLogger(__name__)


class SkillRouter:
    """Select applicable skills for a given analysis context.

    Auto-detects market region from stock code and filters skills accordingly.
    CN stocks (numeric codes) -> region="cn" skills.
    US stocks (letter tickers) -> region="us" skills.
    HK stocks (5-digit codes) -> region="cn" skills (share CN strategies).
    """

    # ── Region detection ────────────────────────────────────────

    @staticmethod
    def _detect_region(stock_code: str) -> str:
        """Detect market region from stock code pattern."""
        code = (stock_code or "").strip().upper()
        if not code:
            return "cn"
        # US stocks: ticker symbols (letters, possibly with dots like BRK.B)
        if code.replace(".", "").isalpha():
            return "us"
        # CN stocks: 6-digit numeric; HK: 5-digit numeric
        return "cn"

    @staticmethod
    def _filter_by_region(skills: list, region: str) -> list:
        """Filter skills to those matching the target region or 'all'."""
        if not region:
            return skills
        return [
            s for s in skills
            if getattr(s, "region", "cn") in (region, "all")
        ]

    # ── Skill selection ─────────────────────────────────────────

    def select_skills(
        self,
        ctx: AgentContext,
        max_count: int = 3,
    ) -> List[str]:
        requested_skills = ctx.meta.get("skills_requested") or ctx.meta.get("strategies_requested", [])
        if requested_skills:
            logger.info("[SkillRouter] user-requested skills: %s", requested_skills)
            return requested_skills[:max_count]

        region = self._detect_region(ctx.stock_code)
        logger.info("[SkillRouter] detected region=%s from stock_code=%s", region, ctx.stock_code)

        routing_mode = self._get_routing_mode()
        if routing_mode == "manual":
            selected = self._get_manual_skills(max_count=max_count, region=region)
            logger.info("[SkillRouter] manual mode (region=%s) — using skills: %s", region, selected)
            return selected

        all_skills = self._get_available_skills()
        available_skills = self._filter_by_region(all_skills, region)
        skill_catalog = available_skills or None
        available_ids = {skill.name for skill in available_skills}

        # Detect market regime and prefix with region for US strategies
        base_regime = self._detect_regime(ctx)
        regime = f"{region}_{base_regime}" if base_regime and region == "us" else base_regime

        if regime:
            selected = get_regime_skill_ids(
                regime,
                skill_catalog,
                max_count=max_count,
                available_skill_ids=available_ids or None,
            )
            if selected:
                logger.info("[SkillRouter] region=%s regime=%s -> skills: %s", region, regime, selected)
                return selected

        default_skills = get_default_router_skill_ids(
            skill_catalog,
            max_count=max_count,
            available_skill_ids=available_ids or None,
        )
        logger.info("[SkillRouter] region=%s using default skills: %s", region, default_skills)
        return default_skills

    def select_strategies(
        self,
        ctx: AgentContext,
        max_count: int = 3,
    ) -> List[str]:
        """Compatibility wrapper for legacy strategy-based callers."""
        return self.select_skills(ctx, max_count=max_count)

    def _detect_regime(self, ctx: AgentContext) -> Optional[str]:
        for op in ctx.opinions:
            if op.agent_name != "technical":
                continue
            raw = op.raw_data or {}

            ma_alignment = str(raw.get("ma_alignment", "")).lower()
            try:
                trend_score = float(raw.get("trend_score", 50))
            except (TypeError, ValueError):
                trend_score = 50.0
            volume_status = str(raw.get("volume_status", "")).lower()

            if ma_alignment == "bullish" and trend_score >= 70:
                return "trending_up"
            if ma_alignment == "bearish" and trend_score <= 30:
                return "trending_down"
            if ma_alignment == "neutral" or 35 <= trend_score <= 65:
                return "sideways"
            if volume_status == "heavy" and 30 < trend_score < 70:
                return "volatile"

        if ctx.meta.get("sector_hot"):
            return "sector_hot"
        return None

    @staticmethod
    def _get_routing_mode() -> str:
        try:
            from src.config import get_config

            config = get_config()
            return getattr(config, "agent_skill_routing", "auto")
        except Exception:
            logger.warning("Failed to get routing mode, falling back to auto", exc_info=True)
            return "auto"

    @staticmethod
    def _get_available_ids() -> set:
        return {skill.name for skill in SkillRouter._get_available_skills()}

    @staticmethod
    def _get_available_skills() -> list:
        try:
            from src.agent.factory import _SKILL_MANAGER_PROTOTYPE

            if _SKILL_MANAGER_PROTOTYPE is not None:
                return list(_SKILL_MANAGER_PROTOTYPE.list_skills())

            from src.agent.factory import get_skill_manager

            sm = get_skill_manager()
            return list(sm.list_skills())
        except Exception:
            logger.warning("Failed to get available skills", exc_info=True)
            return []

    @classmethod
    def _get_manual_skills(cls, max_count: int, region: str = "cn") -> List[str]:
        configured: List[str] = []
        try:
            from src.config import get_config

            config = get_config()
            configured = [
                skill_id
                for skill_id in getattr(config, "agent_skills", []) or []
                if isinstance(skill_id, str) and skill_id
            ]
        except Exception:
            logger.warning("Failed to get manual skills config", exc_info=True)
            configured = []

        all_skills = cls._get_available_skills()
        available_skills = cls._filter_by_region(all_skills, region)
        skill_catalog = available_skills or None
        available = {skill.name for skill in available_skills}
        selected = [skill_id for skill_id in configured if skill_id in available][:max_count]
        if selected:
            return selected

        return get_default_router_skill_ids(
            skill_catalog,
            max_count=max_count,
            available_skill_ids=available or None,
        )


StrategyRouter = SkillRouter
_DEFAULT_STRATEGIES = tuple(get_default_router_skill_ids())
_DEFAULT_SKILLS = _DEFAULT_STRATEGIES
