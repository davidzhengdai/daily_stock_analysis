"""Unified Skills Registry: Merges existing strategies with Vibe-Trading skills.

Supports:
- Execution skills: strategies/*.yaml (existing)
- Learning skills: skills_library/*/SKILL.md (Vibe-Trading)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import yaml

from src.skills.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class SkillType(Enum):
    """Type of skill."""
    EXECUTION = "execution"  # Existing strategies/*.yaml
    LEARNING = "learning"    # Vibe-Trading skills_library


@dataclass
class UnifiedSkill:
    """Unified skill representation."""
    
    name: str
    display_name: str
    description: str
    skill_type: SkillType
    category: str = "other"
    
    # For execution skills (existing)
    instructions: str = ""
    required_tools: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    core_rules: List[int] = field(default_factory=list)
    default_priority: int = 50
    market_regimes: List[str] = field(default_factory=list)
    
    # For learning skills (Vibe-Trading)
    body: str = ""  # Markdown content
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Source info
    source_path: Path = Path()
    source_format: str = ""  # "yaml" or "markdown"


class UnifiedSkillRegistry:
    """Registry that loads and manages both skill types."""
    
    def __init__(
        self,
        strategies_dir: Optional[Path] = None,
        skills_library_dir: Optional[Path] = None,
    ):
        self.strategies_dir = strategies_dir or Path(
            __file__).resolve().parents[2] / "strategies"
        self.skills_library_dir = skills_library_dir or Path(
            __file__).resolve().parents[2] / "src" / "skills_library"
        
        self._skills: Dict[str, UnifiedSkill] = {}
        self._aliases: Dict[str, str] = {}  # alias -> skill_name
        self._load_all()
    
    def _load_all(self) -> None:
        """Load all skills from both sources."""
        # Load execution skills first (existing strategies)
        self._load_execution_skills()
        
        # Load learning skills (Vibe-Trading)
        self._load_learning_skills()
        
        logger.info(
            f"Loaded {len(self._skills)} unified skills: "
            f"{sum(1 for s in self._skills.values() if s.skill_type == SkillType.EXECUTION)} execution, "
            f"{sum(1 for s in self._skills.values() if s.skill_type == SkillType.LEARNING)} learning"
        )
    
    def _load_execution_skills(self) -> None:
        """Load existing strategies/*.yaml."""
        if not self.strategies_dir.exists():
            logger.warning(f"Strategies directory not found: {self.strategies_dir}")
            return
        
        for yaml_file in self.strategies_dir.rglob("*.yaml"):
            try:
                skill = self._parse_execution_skill(yaml_file)
                if skill:
                    self._skills[skill.name] = skill
                    # Register aliases
                    for alias in skill.aliases:
                        self._aliases[alias] = skill.name
                    logger.debug(f"Loaded execution skill: {skill.name}")
            except Exception as e:
                logger.warning(f"Failed to load execution skill {yaml_file}: {e}")
    
    def _parse_execution_skill(self, path: Path) -> Optional[UnifiedSkill]:
        """Parse a strategies/*.yaml file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data or not data.get("name"):
                return None
            
            return UnifiedSkill(
                name=data["name"],
                display_name=data.get("display_name", data["name"]),
                description=data.get("description", ""),
                skill_type=SkillType.EXECUTION,
                category=data.get("category", "other"),
                instructions=data.get("instructions", ""),
                required_tools=data.get("required_tools", []),
                aliases=data.get("aliases", []),
                core_rules=data.get("core_rules", []),
                default_priority=data.get("default_priority", 50),
                market_regimes=data.get("market_regimes", []),
                source_path=path,
                source_format="yaml",
            )
        except Exception as e:
            logger.error(f"Error parsing {path}: {e}")
            return None
    
    def _load_learning_skills(self) -> None:
        """Load Vibe-Trading skills_library/*/SKILL.md."""
        if not self.skills_library_dir.exists():
            logger.warning(f"Skills library directory not found: {self.skills_library_dir}")
            return
        
        for skill_dir in self.skills_library_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            
            try:
                skill = self._parse_learning_skill(skill_md)
                if skill:
                    # Handle name collision: add _learn suffix if name already exists
                    original_name = skill.name
                    if skill.name in self._skills:
                        skill.name = f"{skill.name}_learn"
                        logger.debug(f"Renamed learning skill '{original_name}' to '{skill.name}' to avoid collision")
                    self._skills[skill.name] = skill
                    logger.debug(f"Loaded learning skill: {skill.name}")
            except Exception as e:
                logger.warning(f"Failed to load learning skill {skill_md}: {e}")
    
    def _parse_learning_skill(self, path: Path) -> Optional[UnifiedSkill]:
        """Parse a skills_library/*/SKILL.md file."""
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = parse_frontmatter(text)
            
            name = meta.get("name", path.parent.name)
            if not name:
                return None
            
            return UnifiedSkill(
                name=name,
                display_name=meta.get("name", name),
                description=meta.get("description", ""),
                skill_type=SkillType.LEARNING,
                category=meta.get("category", "other"),
                body=body,
                metadata=meta,
                source_path=path,
                source_format="markdown",
            )
        except Exception as e:
            logger.error(f"Error parsing {path}: {e}")
            return None
    
    def get(self, name: str) -> Optional[UnifiedSkill]:
        """Get a skill by name or alias."""
        # Direct name lookup
        if name in self._skills:
            return self._skills[name]
        
        # Alias lookup
        if name in self._aliases:
            return self._skills[self._aliases[name]]
        
        return None
    
    def list_all(self, skill_type: Optional[SkillType] = None) -> List[UnifiedSkill]:
        """List all skills, optionally filtered by type."""
        skills = list(self._skills.values())
        if skill_type:
            skills = [s for s in skills if s.skill_type == skill_type]
        return skills
    
    def get_by_category(self, category: str) -> List[UnifiedSkill]:
        """Get skills by category."""
        return [s for s in self._skills.values() if s.category == category]
    
    def search(self, query: str) -> List[UnifiedSkill]:
        """Search skills by name, display_name, or description."""
        query_lower = query.lower()
        results = []
        
        for skill in self._skills.values():
            # Check name and display_name
            if query_lower in skill.name.lower():
                results.append(skill)
                continue
            if query_lower in skill.display_name.lower():
                results.append(skill)
                continue
            
            # Check description
            if query_lower in skill.description.lower():
                results.append(skill)
                continue
            
            # For learning skills, also search in body
            if skill.skill_type == SkillType.LEARNING and query_lower in skill.body.lower():
                results.append(skill)
                continue
            
            # Check aliases for execution skills
            if skill.skill_type == SkillType.EXECUTION:
                for alias in skill.aliases:
                    if query_lower in alias.lower():
                        results.append(skill)
                        break
        
        return results
    
    def get_for_market_regime(self, regime: str) -> List[UnifiedSkill]:
        """Get execution skills suitable for a market regime."""
        return [
            s for s in self._skills.values()
            if s.skill_type == SkillType.EXECUTION and regime in s.market_regimes
        ]
    
    def format_for_agent(self, skill: UnifiedSkill) -> str:
        """Format a skill for injection into agent prompt."""
        if skill.skill_type == SkillType.EXECUTION:
            # Format execution skill
            lines = [
                f"## {skill.display_name} ({skill.name})",
                f"Type: Execution | Category: {skill.category} | Priority: {skill.default_priority}",
                f"Required Tools: {', '.join(skill.required_tools)}",
                "",
                skill.instructions,
            ]
            return "\n".join(lines)
        
        else:
            # Format learning skill (Vibe-Trading style)
            lines = [
                f'<skill name="{skill.name}">',
                skill.body,
                '</skill>',
            ]
            return "\n".join(lines)
    
    def get_descriptions(self) -> str:
        """Get all skill descriptions grouped by category."""
        categories: Dict[str, List[UnifiedSkill]] = {}
        
        for skill in self._skills.values():
            cat = skill.category
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(skill)
        
        lines = ["# Available Skills\n"]
        
        # Execution skills first
        if SkillType.EXECUTION in [s.skill_type for s in self._skills.values()]:
            lines.append("## Execution Skills (for Agent)\n")
            for cat, skills in sorted(categories.items()):
                exec_skills = [s for s in skills if s.skill_type == SkillType.EXECUTION]
                if exec_skills:
                    lines.append(f"### {cat}")
                    for skill in sorted(exec_skills, key=lambda s: s.default_priority, reverse=True):
                        lines.append(f"  - {skill.name}: {skill.description} (priority: {skill.default_priority})")
                    lines.append("")
        
        # Learning skills
        if SkillType.LEARNING in [s.skill_type for s in self._skills.values()]:
            lines.append("## Learning Skills (for Reference)\n")
            for cat, skills in sorted(categories.items()):
                learn_skills = [s for s in skills if s.skill_type == SkillType.LEARNING]
                if learn_skills:
                    lines.append(f"### {cat}")
                    for skill in sorted(learn_skills, key=lambda s: s.name):
                        lines.append(f"  - {skill.name}: {skill.description}")
                    lines.append("")
        
        return "\n".join(lines)


# Singleton instance
_registry_instance: Optional[UnifiedSkillRegistry] = None


def get_unified_registry() -> UnifiedSkillRegistry:
    """Get the singleton unified registry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = UnifiedSkillRegistry()
    return _registry_instance
