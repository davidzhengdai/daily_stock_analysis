"""Goal Alert Service: Check and alert on goals needing attention.

Provides automated goal monitoring and alerting capabilities.
Simplified adapter for Vibe-Trading GoalStore.

Adapted from Vibe-Trading (https://github.com/HKUDS/Vibe-Trading)
Original: Custom integration layer
Licensed under MIT License - Copyright (c) 2025 HKUDS
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from src.goal import get_goal_manager, GoalRecord, GoalStatus

logger = logging.getLogger(__name__)


class GoalAlertService:
    """Service for monitoring goals and generating alerts."""
    
    def __init__(self, goal_manager=None):
        self.goal_manager = goal_manager or get_goal_manager()
    
    def get_due_goals(
        self,
        hours_threshold: int = 24
    ) -> List[Dict[str, Any]]:
        """Get goals that need attention (due for check).
        
        Args:
            hours_threshold: Hours since last check
            
        Returns:
            List of due goals with alert metadata
        """
        active_goals = self.goal_manager.list_by_status(GoalStatus.ACTIVE)
        now = datetime.now()
        due = []
        
        for goal in active_goals:
            # Check last update time
            is_due = False
            hours_since_update = None
            
            if goal.updated_at:
                try:
                    updated = datetime.fromisoformat(goal.updated_at.replace("Z", "+00:00"))
                    hours_since_update = (now - updated).total_seconds() / 3600
                    if hours_since_update >= hours_threshold:
                        is_due = True
                except:
                    pass
            
            if is_due:
                # Simplified priority based on staleness
                priority = "low"
                if hours_since_update and hours_since_update > 72:
                    priority = "high"
                elif hours_since_update and hours_since_update > 48:
                    priority = "medium"
                
                due.append({
                    "goal": goal,
                    "goal_id": goal.goal_id,
                    "title": goal.objective,
                    "summary": goal.ui_summary,
                    "hours_overdue": hours_since_update,
                    "priority": priority,
                })
        
        # Sort by priority and overdue time
        priority_order = {"high": 0, "medium": 1, "low": 2}
        due.sort(key=lambda x: (priority_order.get(x["priority"], 3), -(x["hours_overdue"] or 0)))
        
        return due
    
    def generate_alert_report(
        self,
        hours_threshold: int = 24,
        include_all_active: bool = True
    ) -> str:
        """Generate formatted alert report.
        
        Args:
            hours_threshold: Hours since last check
            include_all_active: Include all active goals summary
            
        Returns:
            Formatted markdown report
        """
        due_goals = self.get_due_goals(hours_threshold)
        
        lines = [
            "# 🎯 Goal Alert Report",
            f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        ]
        
        if due_goals:
            lines.append(f"\n## ⚠️ Goals Needing Attention ({len(due_goals)})")
            
            for item in due_goals:
                priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item["priority"], "⚪")
                lines.append(f"\n### {priority_emoji} {item['title']}")
                lines.append(f"- **Goal ID**: `{item['goal_id']}`")
                if item["hours_overdue"]:
                    lines.append(f"- **Overdue**: {item['hours_overdue']:.1f} hours")
                if item.get("summary"):
                    lines.append(f"- **Summary**: {item['summary'][:100]}...")
                
                lines.append(f"\n**Action**: Review and update goal progress")
        else:
            lines.append("\n## ✅ All Goals Up-to-Date")
            lines.append(f"No goals overdue for check (threshold: {hours_threshold} hours)")
        
        if include_all_active:
            active_goals = self.goal_manager.list_by_status(GoalStatus.ACTIVE)
            lines.append(f"\n## 📊 All Active Goals ({len(active_goals)})")
            
            for goal in active_goals[:20]:  # Limit to 20
                emoji = "🟢"
                lines.append(f"- {emoji} `{goal.goal_id}`: {goal.objective[:50]}")
        
        return "\n".join(lines)
    
    def check_and_notify(
        self,
        hours_threshold: int = 24,
        min_priority: str = "medium"
    ) -> Dict[str, Any]:
        """Check goals and return notification data.
        
        Args:
            hours_threshold: Hours since last check
            min_priority: Minimum priority to include (low/medium/high)
            
        Returns:
            Notification data structure
        """
        due_goals = self.get_due_goals(hours_threshold)
        
        priority_filter = {"low": 0, "medium": 1, "high": 2}
        min_level = priority_filter.get(min_priority, 0)
        
        filtered = [
            g for g in due_goals
            if priority_filter.get(g["priority"], 0) >= min_level
        ]
        
        return {
            "has_alerts": len(filtered) > 0,
            "alert_count": len(filtered),
            "due_goals": filtered,
            "report": self.generate_alert_report(hours_threshold) if filtered else None,
            "timestamp": datetime.now().isoformat()
        }


# Singleton instance
_alert_service_instance: Optional[GoalAlertService] = None


def get_goal_alert_service() -> GoalAlertService:
    """Get the singleton alert service instance."""
    global _alert_service_instance
    if _alert_service_instance is None:
        _alert_service_instance = GoalAlertService()
    return _alert_service_instance
