"""PersistentMemory: file-based cross-session memory, zero external dependencies.

Storage layout:
    ~/.daily-stock-analysis/memory/
    +-- INDEX.md          # Index (< 200 lines)
    +-- watchlist.md      # Watchlist entries
    +-- preferences.md    # User preferences
    +-- alert_rules.md    # Custom alert rules
    +-- notes.md          # Analysis notes

Adapted from Vibe-Trading (https://github.com/HKUDS/Vibe-Trading)
Original: agent/src/memory/persistent.py
Licensed under MIT License - Copyright (c) 2025 HKUDS
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import json

logger = logging.getLogger(__name__)

# Default storage location
MEMORY_BASE = Path.home() / ".daily-stock-analysis" / "memory"

# Limits
MAX_INDEX_LINES = 200
MAX_ENTRY_CHARS = 8000
MAX_RESULTS = 10
METADATA_WEIGHT = 2.0
MEMORY_TYPES = ("watchlist", "preference", "alert_rule", "note", "sector_pref")

# Tokenization for CJK and other scripts
_NON_LATIN_SCRIPT_RANGES = (
    "一-鿿"   # CJK Unified Ideographs (U+4E00-U+9FFF)
    "㐀-䶿"   # CJK Extension A (U+3400-U+4DBF)
    "฀-๿"   # Thai (U+0E00-U+0E7F)
    "ؠ-ي"   # Arabic letters (U+0620-U+064A)
    "א-ת"   # Hebrew letters (U+05D0-U+05EA)
    "Ѐ-ӿ"   # Cyrillic (U+0400-U+04FF)
)

_TOKEN_RE = re.compile(rf"[a-zA-Z0-9]{{3,}}|[{_NON_LATIN_SCRIPT_RANGES}]")
_SLUG_DISALLOWED_RE = re.compile(rf"[^a-z0-9_\-{_NON_LATIN_SCRIPT_RANGES}]")


@dataclass(frozen=True)
class MemoryEntry:
    """A single memory entry on disk."""
    
    path: Path
    title: str
    description: str
    memory_type: str
    body: str
    tags: List[str]
    modified_at: float


class PersistentMemory:
    """File-based persistent memory with search capabilities."""
    
    def __init__(self, base_path: Optional[Path] = None):
        self.base = base_path or MEMORY_BASE
        self.base.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base / "INDEX.md"
        
    def _slugify(self, text: str) -> str:
        """Create filesystem-safe slug from title."""
        text = text.lower().strip()
        text = _SLUG_DISALLOWED_RE.sub("-", text)
        text = re.sub(r"-+", "-", text).strip("-")
        if not text:
            text = "entry"
        # Add hash suffix for uniqueness
        hash_suffix = hashlib.md5(text.encode()).hexdigest()[:6]
        return f"{text[:50]}-{hash_suffix}.md"
    
    def _tokenize(self, text: str) -> set[str]:
        """Split text into searchable tokens."""
        text = text.lower().replace("_", " ")
        tokens = set()
        for match in _TOKEN_RE.finditer(text):
            token = match.group()
            if len(token) >= 3:
                tokens.add(token)
        return tokens
    
    def _score_match(self, query_tokens: set[str], entry: MemoryEntry) -> float:
        """Calculate relevance score for entry against query."""
        title_tokens = self._tokenize(entry.title)
        desc_tokens = self._tokenize(entry.description)
        body_tokens = self._tokenize(entry.body)
        tag_tokens = set(t.lower() for t in entry.tags)
        
        score = 0.0
        for token in query_tokens:
            if token in title_tokens:
                score += 3.0 * METADATA_WEIGHT
            if token in desc_tokens:
                score += 2.0 * METADATA_WEIGHT
            if token in tag_tokens:
                score += 2.5 * METADATA_WEIGHT
            if token in body_tokens:
                score += 1.0
                
        # Boost for recent entries
        age_days = (datetime.now().timestamp() - entry.modified_at) / 86400
        recency_boost = max(0, 1 - age_days / 30)  # 30-day decay
        score *= (1 + recency_boost)
        
        return score
    
    def _parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        """Parse YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return {}, content
            
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content
            
        fm_text = parts[1].strip()
        body = parts[2].strip()
        
        metadata = {}
        for line in fm_text.split("\n"):
            line = line.strip()
            if ":" in line:
                key, value = line.split(":", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # Handle list values
                if value.startswith("[") and value.endswith("]"):
                    value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
                metadata[key] = value
                
        return metadata, body
    
    def _write_frontmatter(
        self,
        title: str,
        memory_type: str,
        tags: List[str],
        description: str = ""
    ) -> str:
        """Generate YAML frontmatter."""
        now = datetime.now().isoformat()
        tags_str = json.dumps(tags, ensure_ascii=False)
        
        return f"""---
title: "{title}"
type: {memory_type}
created_at: {now}
modified_at: {now}
tags: {tags_str}
description: "{description}"
---

"""
    
    def add(
        self,
        title: str,
        body: str,
        memory_type: str = "note",
        tags: Optional[List[str]] = None,
        description: str = ""
    ) -> str:
        """Add a new memory entry.
        
        Args:
            title: Entry title
            body: Markdown body content
            memory_type: One of MEMORY_TYPES
            tags: Optional list of tags
            description: One-line description
            
        Returns:
            Path to created file
        """
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Invalid type {memory_type}. Use: {MEMORY_TYPES}")
            
        tags = tags or []
        
        # Truncate if too long
        if len(body) > MAX_ENTRY_CHARS:
            body = body[:MAX_ENTRY_CHARS] + "\n\n... (truncated)"
            
        slug = self._slugify(title)
        file_path = self.base / slug
        
        frontmatter = self._write_frontmatter(title, memory_type, tags, description)
        content = frontmatter + body
        
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"Memory added: {title} -> {file_path}")
        
        self._update_index()
        return str(file_path)
    
    def get(self, title: str) -> Optional[MemoryEntry]:
        """Retrieve a specific memory entry by title."""
        slug = self._slugify(title)
        file_path = self.base / slug
        
        if not file_path.exists():
            # Try searching by exact title match
            for entry in self.list_all():
                if entry.title.lower() == title.lower():
                    return entry
            return None
            
        return self._read_entry(file_path)
    
    def _read_entry(self, file_path: Path) -> Optional[MemoryEntry]:
        """Read a memory entry from file."""
        try:
            content = file_path.read_text(encoding="utf-8")
            metadata, body = self._parse_frontmatter(content)
            
            return MemoryEntry(
                path=file_path,
                title=metadata.get("title", file_path.stem),
                description=metadata.get("description", ""),
                memory_type=metadata.get("type", "note"),
                body=body,
                tags=metadata.get("tags", []) if isinstance(metadata.get("tags"), list) else [],
                modified_at=self._parse_timestamp(metadata.get("modified_at", "0"))
            )
        except Exception as e:
            logger.warning(f"Failed to read {file_path}: {e}")
            return None
    
    def _parse_timestamp(self, ts_str: str) -> float:
        """Parse ISO timestamp string."""
        try:
            if ts_str == "0":
                return 0
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except:
            return 0
    
    def list_all(self, memory_type: Optional[str] = None) -> List[MemoryEntry]:
        """List all memory entries, optionally filtered by type."""
        entries = []
        for file_path in self.base.glob("*.md"):
            if file_path.name == "INDEX.md":
                continue
            entry = self._read_entry(file_path)
            if entry:
                if memory_type is None or entry.memory_type == memory_type:
                    entries.append(entry)
        return sorted(entries, key=lambda e: e.modified_at, reverse=True)
    
    def search(self, query: str, memory_type: Optional[str] = None, limit: int = MAX_RESULTS) -> List[Tuple[MemoryEntry, float]]:
        """Search memory entries by query string.
        
        Args:
            query: Search query
            memory_type: Optional type filter
            limit: Max results
            
        Returns:
            List of (entry, score) tuples, sorted by relevance
        """
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
            
        entries = self.list_all(memory_type)
        scored = [(entry, self._score_match(query_tokens, entry)) for entry in entries]
        scored = [(e, s) for e, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        
        return scored[:limit]
    
    def update(self, title: str, body: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Update an existing memory entry.
        
        Args:
            title: Entry title to update
            body: New body content (optional)
            tags: New tags (optional)
            
        Returns:
            True if updated successfully
        """
        entry = self.get(title)
        if not entry:
            return False
            
        # Read existing content
        content = entry.path.read_text(encoding="utf-8")
        metadata, old_body = self._parse_frontmatter(content)
        
        # Update fields
        if body is not None:
            new_body = body
        else:
            new_body = old_body
            
        if tags is not None:
            metadata["tags"] = tags
            
        metadata["modified_at"] = datetime.now().isoformat()
        
        # Rebuild frontmatter
        tags_str = json.dumps(metadata.get("tags", []), ensure_ascii=False)
        frontmatter = f"""---
title: "{metadata.get('title', title)}"
type: {metadata.get('type', 'note')}
created_at: {metadata.get('created_at', datetime.now().isoformat())}
modified_at: {metadata['modified_at']}
tags: {tags_str}
description: "{metadata.get('description', '')}"
---

"""
        
        entry.path.write_text(frontmatter + new_body, encoding="utf-8")
        logger.info(f"Memory updated: {title}")
        self._update_index()
        return True
    
    def delete(self, title: str) -> bool:
        """Delete a memory entry."""
        entry = self.get(title)
        if not entry:
            return False
            
        entry.path.unlink()
        logger.info(f"Memory deleted: {title}")
        self._update_index()
        return True
    
    def _update_index(self):
        """Regenerate the INDEX.md file."""
        entries = self.list_all()
        
        lines = ["# Memory Index\n", f"Generated: {datetime.now().isoformat()}\n", "---\n\n"]
        
        for entry in entries[:MAX_INDEX_LINES]:
            tags = ", ".join(entry.tags) if entry.tags else "no tags"
            lines.append(f"- **{entry.title}** ({entry.memory_type})\n")
            lines.append(f"  Tags: {tags} | Modified: {datetime.fromtimestamp(entry.modified_at).strftime('%Y-%m-%d')}\n")
            if entry.description:
                lines.append(f"  > {entry.description[:80]}...\n")
            lines.append(f"  File: `{entry.path.name}`\n\n")
            
        self.index_path.write_text("".join(lines), encoding="utf-8")


# Singleton instance
_memory_instance: Optional[PersistentMemory] = None


def get_memory() -> PersistentMemory:
    """Get the singleton memory instance."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = PersistentMemory()
    return _memory_instance
