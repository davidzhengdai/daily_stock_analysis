"""Persistent Memory: file-based cross-session memory, zero external dependencies."""

from .persistent import (
    PersistentMemory,
    MemoryEntry,
    MEMORY_BASE,
    MAX_INDEX_LINES,
    MAX_ENTRY_CHARS,
    MAX_RESULTS,
)

__all__ = [
    "PersistentMemory",
    "MemoryEntry",
    "MEMORY_BASE",
    "MAX_INDEX_LINES",
    "MAX_ENTRY_CHARS",
    "MAX_RESULTS",
]
