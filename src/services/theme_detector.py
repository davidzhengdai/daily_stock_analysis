# -*- coding: utf-8 -*-
"""
Investment theme detector for 沙里淘金.

1. Fetches live macro/finance/political news via SearchService (Bocha/Tavily/SerpAPI/Brave/SearXNG)
2. Passes the news digest to the LLM to extract actionable investment themes
3. Caches results for `cache_ttl_hours` to avoid repeated API calls within a single day
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from src.config import get_config
from src.schemas.gold_digger import InvestmentTheme

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# News search queries — cover macro, geopolitics, sector, and policy angles
# ---------------------------------------------------------------------------
_MACRO_QUERIES = [
    # Global macro / central banks / rates
    "global markets macro outlook Fed interest rates inflation 2025",
    # Geopolitics and policy risk
    "geopolitical risk trade tariffs sanctions policy impact markets",
    # China macro / domestic policy stimulus
    "China economic policy stimulus sectors A-share market 2025",
    # Sector rotation / earnings trends
    "sector rotation earnings growth technology healthcare energy industrials",
    # Commodities / energy / materials
    "oil gas lithium copper gold commodity prices outlook",
    # AI / tech infrastructure
    "AI artificial intelligence data center semiconductor chip demand",
    # Emerging themes / political events
    "election regulatory policy ESG green energy infrastructure spending",
]

_THEME_PROMPT_TEMPLATE = """You are a macro investment analyst. You have just read the following live news digest from today ({date}):

--- NEWS DIGEST ---
{news_digest}
--- END OF NEWS ---

Based ONLY on the above news (not your training data alone), identify the top {count} investable macro or sector themes that are actionable RIGHT NOW for the next 1–6 months.

For each theme, provide:
1. A concise name (3–6 words)
2. A 2–3 sentence description of why this theme is actionable NOW based on the news above
3. 5–8 relevant keywords (companies, technologies, or sector terms)
4. Relevant GICS sectors (from: Technology, Healthcare, Financials, Energy, Consumer Discretionary, Consumer Staples, Industrials, Materials, Communication Services, Utilities, Real Estate)
5. Market regions: list from ["us", "cn", "global"]
6. Sentiment: "bullish", "bearish", or "neutral"

Focus on themes where SMALL-CAP or OVERLOOKED stocks could benefit disproportionately — hidden beneficiaries.

Return ONLY a valid JSON array — no markdown fences, no explanation:
[
  {{
    "name": "Theme Name",
    "description": "Why this theme matters now based on the news...",
    "keywords": ["keyword1", "keyword2", "keyword3"],
    "relevant_sectors": ["Technology", "Industrials"],
    "market_regions": ["us", "global"],
    "sentiment": "bullish"
  }}
]"""


def _resolve_cache_path() -> Path:
    db_path = os.environ.get("DATABASE_PATH", "./data/stock_analysis.db")
    return Path(db_path).parent / "scanner_cache" / "investment_themes.json"


class ThemeDetector:
    """Detects current macro investment themes from live news, then synthesizes via LLM."""

    def __init__(self, analyzer, search_service=None, cache_ttl_hours: int = 6):
        self._analyzer = analyzer          # GeminiAnalyzer
        self._search = search_service      # SearchService or None
        self._cache_ttl_hours = cache_ttl_hours
        self._cache_path = _resolve_cache_path()
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)

    def detect_themes(self, count: int = 8, date_str: str = "") -> List[InvestmentTheme]:
        """Return current investment themes, using cache when fresh."""
        cached = self._load_cache(count)
        if cached is not None:
            logger.info("Loaded %d investment themes from cache", len(cached))
            return cached

        themes = self._fetch_from_news_and_llm(count, date_str)
        if themes:
            self._save_cache(themes)
        return themes

    # ------------------------------------------------------------------
    # News fetch + LLM synthesis
    # ------------------------------------------------------------------

    def _fetch_from_news_and_llm(self, count: int, date_str: str) -> List[InvestmentTheme]:
        from datetime import date as _date
        if not date_str:
            date_str = _date.today().isoformat()

        news_digest = self._build_news_digest()

        if news_digest:
            logger.info("Synthesizing %d themes from live news (%d chars)…", count, len(news_digest))
            prompt = _THEME_PROMPT_TEMPLATE.format(
                date=date_str,
                count=count,
                news_digest=news_digest[:8000],  # stay within token budget
            )
        else:
            # No search available — fall back to pure LLM knowledge
            logger.warning("No live news available; asking LLM for themes from training knowledge")
            prompt = _THEME_PROMPT_TEMPLATE.format(
                date=date_str,
                count=count,
                news_digest=(
                    "(No live news feed available. Use your most current knowledge of "
                    f"global financial markets as of {date_str}.)"
                ),
            )

        try:
            _theme_model = getattr(get_config(), 'theme_detector_model', '') or None
            raw = self._analyzer.generate_text(prompt, max_tokens=3000, temperature=0.5, model=_theme_model)
            if not raw:
                logger.warning("LLM returned empty response for theme detection [model=%s]", _theme_model or "default")
                return self._fallback_themes()
            return self._parse_themes(raw, model=_theme_model or "default")
        except Exception as exc:
            logger.warning("Theme detection LLM call failed [model=%s]: %s", _theme_model or "default", exc)
            return self._fallback_themes()

    def _build_news_digest(self) -> str:
        """Search across all macro query dimensions and concatenate results."""
        if self._search is None or not getattr(self._search, "is_available", False):
            logger.info("SearchService not available; skipping live news fetch for themes")
            return ""

        snippets: List[str] = []

        def _search_one(query: str) -> str:
            try:
                response = self._search.search(query, max_results=5, days=7)
                if response and response.results:
                    return response.to_context(max_results=5)
            except Exception as exc:
                logger.debug("News search failed for '%s': %s", query, exc)
            return ""

        # Run all queries in parallel (up to 4 threads) to keep latency low
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_search_one, q): q for q in _MACRO_QUERIES}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    snippets.append(result)

        if not snippets:
            return ""

        digest = "\n\n".join(snippets)
        logger.info(
            "News digest built: %d search queries returned content, total %d chars",
            len(snippets), len(digest),
        )
        return digest

    # ------------------------------------------------------------------
    # Theme parsing
    # ------------------------------------------------------------------

    def _parse_themes(self, raw: str, model: str = "") -> List[InvestmentTheme]:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            model_tag = f" [model={model}]" if model else ""
            logger.warning("Failed to parse theme JSON%s: %s — raw: %.200s", model_tag, exc, text)
            return self._fallback_themes()

        themes: List[InvestmentTheme] = []
        for item in data:
            try:
                themes.append(InvestmentTheme(
                    name=str(item.get("name", "Unknown Theme")),
                    description=str(item.get("description", "")),
                    keywords=[str(k) for k in item.get("keywords", [])],
                    relevant_sectors=[str(s) for s in item.get("relevant_sectors", [])],
                    market_regions=[str(r) for r in item.get("market_regions", ["us"])],
                    sentiment=str(item.get("sentiment", "neutral")),
                ))
            except Exception:
                continue

        if not themes:
            logger.warning("Parsed 0 themes from LLM output; using fallback")
            return self._fallback_themes()

        logger.info("Detected %d investment themes from live news", len(themes))
        return themes

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _load_cache(self, expected_count: int) -> Optional[List[InvestmentTheme]]:
        if not self._cache_path.exists():
            return None
        age_hours = (time.time() - self._cache_path.stat().st_mtime) / 3600
        if age_hours > self._cache_ttl_hours:
            logger.info("Theme cache expired (%.1fh old)", age_hours)
            return None
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            themes = [InvestmentTheme(**item) for item in data]
            if len(themes) < min(expected_count, 3):
                return None
            return themes
        except Exception as exc:
            logger.warning("Failed to load theme cache: %s", exc)
            return None

    def _save_cache(self, themes: List[InvestmentTheme]) -> None:
        try:
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in themes], f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Failed to save theme cache: %s", exc)

    # ------------------------------------------------------------------
    # Static fallback (when both news fetch and LLM fail)
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_themes() -> List[InvestmentTheme]:
        return [
            InvestmentTheme(
                name="AI Infrastructure Buildout",
                description="Data centers, power, and networking are being built at record pace to support AI workloads. Small-cap suppliers of cooling, power equipment, and optical components are key hidden beneficiaries.",
                keywords=["AI", "data center", "GPU", "cooling", "power", "HPC", "networking"],
                relevant_sectors=["Technology", "Industrials", "Utilities"],
                market_regions=["us", "global"],
                sentiment="bullish",
            ),
            InvestmentTheme(
                name="Domestic Consumption Recovery",
                description="Chinese domestic consumption is rebounding with policy stimulus focused on retail and services. A-share small-caps in consumer goods and catering are direct beneficiaries.",
                keywords=["consumption", "retail", "stimulus", "catering", "consumer goods", "domestic"],
                relevant_sectors=["Consumer Discretionary", "Consumer Staples"],
                market_regions=["cn"],
                sentiment="bullish",
            ),
            InvestmentTheme(
                name="Energy Transition Materials",
                description="The push for renewables and EVs is creating sustained demand for lithium, copper, rare earths, and specialty chemicals. Small-cap miners and processors trade well below replacement cost.",
                keywords=["lithium", "copper", "rare earth", "EV", "solar", "battery", "mining"],
                relevant_sectors=["Materials", "Energy", "Industrials"],
                market_regions=["us", "cn", "global"],
                sentiment="bullish",
            ),
            InvestmentTheme(
                name="Defense and Security Spending",
                description="Geopolitical tensions are accelerating defense budgets globally. Small-cap defense suppliers, cybersecurity, and drone companies have long order backlogs at discounted valuations.",
                keywords=["defense", "cybersecurity", "drone", "munitions", "radar", "satellite"],
                relevant_sectors=["Industrials", "Technology"],
                market_regions=["us", "global"],
                sentiment="bullish",
            ),
            InvestmentTheme(
                name="Healthcare AI and Diagnostics",
                description="AI-driven diagnostics and drug discovery are compressing development timelines. Small biotech and medtech firms with AI pipelines are undervalued relative to potential.",
                keywords=["AI diagnostics", "biotech", "drug discovery", "genomics", "medtech", "IVD"],
                relevant_sectors=["Healthcare"],
                market_regions=["us", "cn"],
                sentiment="bullish",
            ),
        ]
