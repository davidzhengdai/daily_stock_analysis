---
name: technical-analysis
description: Technical analysis patterns including support/resistance, moving averages, RSI, MACD, and volume analysis
category: analysis
---

# Technical Analysis Skill

## Overview

Provides technical analysis capabilities for stock price patterns and indicators.

## Capabilities

### Moving Averages
- MA5, MA10, MA20, MA60 calculation and trend detection
- Golden cross / Death cross identification
- Price deviation from MA (乖离率)

### Support and Resistance
- Historical high/low based levels
- Volume-weighted support/resistance
- Dynamic level adjustment

### Momentum Indicators
- RSI (Relative Strength Index)
- MACD (Moving Average Convergence Divergence)
- Volume ratio analysis

### Pattern Recognition
- Breakout detection
- Consolidation patterns
- Trend continuation patterns

## Usage

```python
from src.skills import get_skills_loader

loader = get_skills_loader()
content = loader.get_content("technical-analysis")
```

## Output Format

Analysis results follow standard dashboard schema with:
- `trend_analysis`: Trend direction and strength
- `price_position`: Price relative to MAs
- `volume_analysis`: Volume patterns and signals
- `sniper_points`: Entry/exit recommendations
