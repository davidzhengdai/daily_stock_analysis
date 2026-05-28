"""Watchlist + Memory Integration: Link watchlist stocks with analysis notes.

Provides bidirectional linking between database watchlist and file-based memory system.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from src.memory import get_memory, PersistentMemory
from src.services.watchlist_service import WatchlistService
from src.repositories.watchlist_repo import WatchlistRepo

logger = logging.getLogger(__name__)


class WatchlistMemoryIntegration:
    """Integrates watchlist database with persistent memory system."""
    
    def __init__(
        self,
        memory: Optional[PersistentMemory] = None,
        watchlist_service: Optional[WatchlistService] = None
    ):
        self.memory = memory or get_memory()
        self.watchlist = watchlist_service or WatchlistService()
    
    def add_note_to_stock(
        self,
        code: str,
        note_title: str,
        note_body: str,
        tags: Optional[List[str]] = None
    ) -> str:
        """Add an analysis note to a specific stock.
        
        Args:
            code: Stock code (e.g., "AAPL", "600519")
            note_title: Note title
            note_body: Note content (Markdown supported)
            tags: Optional tags, will auto-add stock code tag
            
        Returns:
            Path to created memory file
        """
        # Ensure stock is in watchlist
        if not self.watchlist.is_watched(code):
            # Add to watchlist if not exists
            self.watchlist.add(code, name="", notes=f"Auto-added from memory note: {note_title}")
            logger.info(f"Added {code} to watchlist from memory note")
        
        # Prepare tags with stock code
        all_tags = tags or []
        code_tag = code.upper().replace(".", "_")
        if code_tag not in all_tags:
            all_tags.append(code_tag)
        
        # Create memory entry
        description = f"Analysis note for {code}: {note_title[:50]}"
        path = self.memory.add(
            title=f"[{code}] {note_title}",
            body=note_body,
            memory_type="note",
            tags=all_tags,
            description=description
        )
        
        logger.info(f"Added note to {code}: {path}")
        return path
    
    def get_stock_notes(
        self,
        code: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get all analysis notes for a specific stock.
        
        Args:
            code: Stock code
            limit: Max results
            
        Returns:
            List of note entries with metadata
        """
        code_tag = code.upper().replace(".", "_")
        results = self.memory.search(code_tag, memory_type="note", limit=limit)
        
        notes = []
        for entry, score in results:
            notes.append({
                "title": entry.title,
                "body": entry.body,
                "tags": entry.tags,
                "modified_at": entry.modified_at,
                "path": str(entry.path),
                "relevance": score
            })
        
        return notes
    
    def get_watchlist_with_notes(
        self,
        include_notes_summary: bool = True
    ) -> List[Dict[str, Any]]:
        """Get watchlist with linked notes summary.
        
        Args:
            include_notes_summary: Whether to include notes summary
            
        Returns:
            Watchlist items with notes metadata
        """
        items = self.watchlist.list_all()
        
        if not include_notes_summary:
            return items
        
        # Enrich with notes count
        enriched = []
        for item in items:
            code = item.get("code", "")
            notes = self.get_stock_notes(code, limit=5)
            
            item["notes_count"] = len(notes)
            item["latest_note"] = notes[0] if notes else None
            item["notes_preview"] = [
                {"title": n["title"], "date": datetime.fromtimestamp(n["modified_at"]).strftime("%Y-%m-%d")}
                for n in notes[:3]
            ]
            enriched.append(item)
        
        return enriched
    
    def search_notes_across_watchlist(
        self,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search notes across all watchlist stocks.
        
        Args:
            query: Search query
            limit: Max results
            
        Returns:
            Matching notes with stock code
        """
        # Get watchlist codes
        watchlist_items = self.watchlist.list_all()
        watchlist_codes = {item.get("code", "").upper() for item in watchlist_items}
        
        # Search in memory
        results = self.memory.search(query, memory_type="note", limit=limit * 2)
        
        # Filter to watchlist stocks only
        filtered = []
        for entry, score in results:
            # Extract stock code from title or tags
            stock_code = None
            title_upper = entry.title.upper()
            
            # Check tags for stock code
            for tag in entry.tags:
                if tag.upper() in watchlist_codes:
                    stock_code = tag.upper()
                    break
            
            # Check title format [CODE] Title
            if not stock_code and title_upper.startswith("["):
                try:
                    code_in_title = title_upper[1:title_upper.find("]")]
                    if code_in_title in watchlist_codes:
                        stock_code = code_in_title
                except:
                    pass
            
            if stock_code:
                filtered.append({
                    "stock_code": stock_code,
                    "title": entry.title,
                    "body": entry.body[:200] + "..." if len(entry.body) > 200 else entry.body,
                    "modified_at": entry.modified_at,
                    "relevance": score
                })
                
                if len(filtered) >= limit:
                    break
        
        return filtered
    
    def sync_watchlist_to_memory(
        self,
        create_default_notes: bool = True
    ) -> Dict[str, int]:
        """Sync watchlist to memory system.
        
        Creates a summary memory entry for each watchlist stock if not exists.
        
        Args:
            create_default_notes: Create default tracking notes
            
        Returns:
            Sync statistics
        """
        items = self.watchlist.list_all()
        stats = {"total": len(items), "created": 0, "existing": 0}
        
        if not create_default_notes:
            return stats
        
        for item in items:
            code = item.get("code", "")
            name = item.get("name", "")
            
            # Check if tracking note exists
            code_tag = code.upper().replace(".", "_")
            existing = self.memory.search(code_tag, memory_type="watchlist", limit=1)
            
            if existing:
                stats["existing"] += 1
                continue
            
            # Create tracking note
            note_body = f"""## {name} ({code}) - Watchlist Tracking

**Added to watchlist**: {datetime.now().strftime("%Y-%m-%d")}

### Notes
{item.get("notes", "No initial notes")}

### Analysis History
- Initial entry created from watchlist sync

### Key Metrics to Track
- Price trends
- Volume patterns  
- Support/resistance levels
- News sentiment
"""
            
            self.memory.add(
                title=f"[{code}] Watchlist Tracking",
                body=note_body,
                memory_type="watchlist",
                tags=[code_tag, "watchlist", "tracking"],
                description=f"Tracking notes for {name} ({code})"
            )
            stats["created"] += 1
        
        logger.info(f"Watchlist sync complete: {stats}")
        return stats


# Singleton instance
_integration_instance: Optional[WatchlistMemoryIntegration] = None


def get_watchlist_memory_integration() -> WatchlistMemoryIntegration:
    """Get the singleton integration instance."""
    global _integration_instance
    if _integration_instance is None:
        _integration_instance = WatchlistMemoryIntegration()
    return _integration_instance
