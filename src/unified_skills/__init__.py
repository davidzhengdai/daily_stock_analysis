"""Unified Skills Registry: Integrates existing strategies with Vibe-Trading skills.

Provides a unified interface for:
- Existing execution-type strategies (strategies/*.yaml)
- Vibe-Trading learning-type skills (skills_library/*/SKILL.md)
"""

from .registry import (
    UnifiedSkill,
    UnifiedSkillRegistry,
    SkillType,
    get_unified_registry,
)

__all__ = [
    "UnifiedSkill",
    "UnifiedSkillRegistry",
    "SkillType",
    "get_unified_registry",
]
