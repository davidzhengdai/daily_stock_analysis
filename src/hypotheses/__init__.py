"""Hypothesis Registry: Track research hypotheses and their validation.

Adapted from Vibe-Trading (https://github.com/HKUDS/Vibe-Trading)
Original: agent/src/hypotheses/registry.py
Licensed under MIT License - Copyright (c) 2025 HKUDS
"""

from .registry import (
    Hypothesis,
    HypothesisRegistry,
    HYPOTHESIS_STATUSES,
    get_registry,
)

__all__ = [
    "Hypothesis",
    "HypothesisRegistry",
    "HYPOTHESIS_STATUSES",
    "get_registry",
]
