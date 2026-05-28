"""Skill System: Modular analysis capabilities with progressive disclosure.

Adapted from Vibe-Trading (https://github.com/HKUDS/Vibe-Trading)
Original: agent/src/agent/skills.py
Licensed under MIT License - Copyright (c) 2025 HKUDS
"""

from .loader import (
    Skill,
    SkillsLoader,
    get_skills_loader,
    USER_SKILLS_DIR,
)
from .frontmatter import parse_frontmatter

__all__ = [
    "Skill",
    "SkillsLoader",
    "get_skills_loader",
    "USER_SKILLS_DIR",
    "parse_frontmatter",
]
