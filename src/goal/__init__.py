"""Finance research goal runtime primitives.

Adapted from Vibe-Trading (https://github.com/HKUDS/Vibe-Trading)
Original: agent/src/goal/
Licensed under MIT License - Copyright (c) 2025 HKUDS
"""

import logging

from src.goal.models import (
    AuditRow,
    EvidenceInput,
    EvidenceRecord,
    GoalClaim,
    GoalCriterion,
    GoalRecord,
    GoalStatus,
    RiskTier,
    StaleGoalError,
)
from src.goal.policy import normalize_required_text, reject_live_execution_objective
from src.goal.store import GoalStore

__all__ = [
    "AuditRow",
    "EvidenceInput",
    "EvidenceRecord",
    "GoalClaim",
    "GoalCriterion",
    "GoalRecord",
    "GoalStatus",
    "GoalStore",
    "RiskTier",
    "StaleGoalError",
    "normalize_required_text",
    "reject_live_execution_objective",
    # Compatibility wrappers
    "GoalManager",
    "get_goal_manager",
]


# Compatibility wrapper for simplified CLI usage
class GoalManager:
    """Simplified wrapper around GoalStore for CLI compatibility."""
    
    def __init__(self) -> None:
        self._store = GoalStore()
    
    def create(self, symbol: str, title: str, criteria: list | None = None):
        """Create a simplified goal (compatibility method)."""
        # Use a default session for single-user mode
        session_id = "default"
        protocol = "Simplified stock analysis goal"
        
        # Convert criteria to protocol criteria
        protocol_criteria = []
        if criteria:
            for c in criteria:
                protocol_criteria.append({
                    "text": c.get("desc", c.get("description", "")),
                    "required": True,
                })
        
        return self._store.create(
            session_id=session_id,
            objective=title,
            ui_summary=f"Research goal for {symbol}: {title}",
            protocol=protocol,
            criteria=protocol_criteria,
        )
    
    def list_by_status(self, status: GoalStatus | None = None):
        """List goals by status (compatibility method)."""
        # For simplicity, return all goals and filter
        # In full implementation, use proper session filtering
        session_id = "default"
        goals = self._store.list_by_session(session_id)
        
        if status:
            goals = [g for g in goals if g.status == status]
        return goals
    
    def get_active_for_symbol(self, symbol: str):
        """Get active goals for a symbol (compatibility method)."""
        # Search in objective for symbol
        all_active = self.list_by_status(GoalStatus.ACTIVE)
        return [g for g in all_active if symbol.upper() in g.objective.upper()]
    
    def complete(self, goal_id: str, summary: str) -> bool:
        """Complete a goal (compatibility method)."""
        try:
            self._store.complete(
                goal_id=goal_id,
                result="satisfied",
                recap=summary,
            )
            return True
        except Exception:
            return False
    
    def cancel(self, goal_id: str, reason: str) -> bool:
        """Cancel a goal (compatibility method)."""
        try:
            self._store.update(goal_id, status=GoalStatus.CANCELLED)
            return True
        except Exception:
            return False
    
    def get_due_for_check(self):
        """Get goals due for check (compatibility method)."""
        # Return active goals that need attention
        return self.list_by_status(GoalStatus.ACTIVE)
    
    def add_evidence(self, goal_id: str, data: dict, source: str, summary: str) -> bool:
        """Add evidence to a goal (compatibility method).
        
        In simplified mode, this just logs the evidence. Full implementation
        would store in the goal evidence table.
        """
        try:
            logger = logging.getLogger(__name__)
            logger.info(f"Evidence for goal {goal_id} from {source}: {summary}")
            return True
        except Exception:
            return False
    
    def update_analysis(self, goal_id: str, analysis: str) -> bool:
        """Update goal analysis text (compatibility method).
        
        In simplified mode, this updates the goal objective with latest analysis.
        """
        try:
            goal = self._store.get(goal_id)
            if goal:
                # Update ui_summary with latest analysis
                new_summary = f"{goal.ui_summary}\n\nLatest: {analysis[:100]}..."
                self._store.update(goal_id, ui_summary=new_summary)
            return True
        except Exception:
            return False


# Singleton instance
_manager_instance: GoalManager | None = None


def get_goal_manager() -> GoalManager:
    """Get the singleton GoalManager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = GoalManager()
    return _manager_instance
