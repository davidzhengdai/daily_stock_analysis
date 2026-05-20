# -*- coding: utf-8 -*-
"""US stock symbol suggestion service."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SymbolSuggestion:
    symbol: str
    name: str
    exchange: str = ""
    quote_type: str = "EQUITY"
    source: str = "builtin"


_ALIASES: Dict[str, List[SymbolSuggestion]] = {
    "GOOGLE": [
        SymbolSuggestion("GOOG", "Alphabet Inc. Class C", "NASDAQ"),
        SymbolSuggestion("GOOGL", "Alphabet Inc. Class A", "NASDAQ"),
    ],
    "ALPHABET": [
        SymbolSuggestion("GOOG", "Alphabet Inc. Class C", "NASDAQ"),
        SymbolSuggestion("GOOGL", "Alphabet Inc. Class A", "NASDAQ"),
    ],
    "APPLE": [SymbolSuggestion("AAPL", "Apple Inc.", "NASDAQ")],
    "MICROSOFT": [SymbolSuggestion("MSFT", "Microsoft Corporation", "NASDAQ")],
    "AMAZON": [SymbolSuggestion("AMZN", "Amazon.com, Inc.", "NASDAQ")],
    "NVIDIA": [SymbolSuggestion("NVDA", "NVIDIA Corporation", "NASDAQ")],
    "TESLA": [SymbolSuggestion("TSLA", "Tesla, Inc.", "NASDAQ")],
    "META": [SymbolSuggestion("META", "Meta Platforms, Inc.", "NASDAQ")],
    "FACEBOOK": [SymbolSuggestion("META", "Meta Platforms, Inc.", "NASDAQ")],
    "NETFLIX": [SymbolSuggestion("NFLX", "Netflix, Inc.", "NASDAQ")],
    "COCA COLA": [SymbolSuggestion("KO", "The Coca-Cola Company", "NYSE")],
    "COCA-COLA": [SymbolSuggestion("KO", "The Coca-Cola Company", "NYSE")],
    "VISA": [SymbolSuggestion("V", "Visa Inc.", "NYSE")],
    "MASTERCARD": [SymbolSuggestion("MA", "Mastercard Incorporated", "NYSE")],
    "BERKSHIRE": [SymbolSuggestion("BRK.B", "Berkshire Hathaway Inc. Class B", "NYSE")],
    "JPMORGAN": [SymbolSuggestion("JPM", "JPMorgan Chase & Co.", "NYSE")],
    "JOHNSON": [SymbolSuggestion("JNJ", "Johnson & Johnson", "NYSE")],
    "WALMART": [SymbolSuggestion("WMT", "Walmart Inc.", "NYSE")],
    "DISNEY": [SymbolSuggestion("DIS", "The Walt Disney Company", "NYSE")],
}

_US_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "NASDAQ", "NYSE", "AMEX", "NYSEARCA"}
_QUOTE_TYPES = {"EQUITY", "ETF"}


class UsSymbolSearchService:
    """Search US ticker symbols by ticker, company name, or common aliases."""

    def search(self, query: str, limit: int = 8) -> List[SymbolSuggestion]:
        normalized = self._normalize_query(query)
        if not normalized:
            return []

        suggestions: List[SymbolSuggestion] = []
        suggestions.extend(self._builtin_matches(normalized))
        suggestions.extend(self._yahoo_matches(query, limit=limit))
        return self._dedupe(suggestions)[:limit]

    def _builtin_matches(self, normalized: str) -> List[SymbolSuggestion]:
        matches: List[SymbolSuggestion] = []
        for alias, suggestions in _ALIASES.items():
            if normalized == alias or normalized in alias or alias in normalized:
                matches.extend(suggestions)

        symbol_matches = [
            item
            for suggestions in _ALIASES.values()
            for item in suggestions
            if item.symbol == normalized
        ]
        return symbol_matches + matches

    def _yahoo_matches(self, query: str, limit: int) -> List[SymbolSuggestion]:
        params = urlencode({"q": query, "quotesCount": limit, "newsCount": 0})
        url = f"https://query1.finance.yahoo.com/v1/finance/search?{params}"
        req = Request(url, headers={"User-Agent": "daily-stock-analysis/1.0"})
        try:
            with urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("Yahoo symbol search failed for %s: %s", query, exc)
            return []

        matches: List[SymbolSuggestion] = []
        for item in payload.get("quotes", []):
            quote_type = str(item.get("quoteType") or "").upper()
            exchange = str(item.get("exchange") or item.get("exchDisp") or "").upper()
            symbol = str(item.get("symbol") or "").upper()
            if not symbol or quote_type not in _QUOTE_TYPES:
                continue
            if exchange and exchange not in _US_EXCHANGES:
                continue
            name = str(item.get("shortname") or item.get("longname") or symbol)
            matches.append(SymbolSuggestion(symbol, name, exchange, quote_type, "yahoo"))
        return matches

    def _dedupe(self, suggestions: List[SymbolSuggestion]) -> List[SymbolSuggestion]:
        seen = set()
        result: List[SymbolSuggestion] = []
        for item in suggestions:
            if item.symbol in seen:
                continue
            seen.add(item.symbol)
            result.append(item)
        return result

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join((query or "").strip().upper().replace(".", " ").split())
