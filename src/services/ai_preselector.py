# -*- coding: utf-8 -*-
"""
Lightweight AI preselection before expensive deep stock analysis.

The helpers in this module ask the configured LLM to rank an already-screened
candidate pool. They never invent stocks and always fall back to deterministic
rule ordering when the LLM is unavailable or returns invalid JSON.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
_PRESELECT_TIMEOUT_SECONDS = 120


def ai_preselect_scanner_candidates(
    candidates: List[T],
    target_count: int,
    analyzer: Any,
    model: Optional[str] = None,
    market_balancer: Optional[Callable[[List[T], int], List[T]]] = None,
) -> List[T]:
    """Use AI to choose Scanner Tier-5 candidates from a broader Tier-4 pool."""
    if target_count <= 0:
        return []
    if len(candidates) <= target_count:
        return candidates

    prompt = _scanner_prompt(candidates, target_count)
    selected = _preselect(
        candidates=candidates,
        target_count=target_count,
        analyzer=analyzer,
        prompt=prompt,
        model=model,
        ticker_getter=lambda item: item.ticker,
        log_label="Scanner AI preselection",
    )
    if market_balancer:
        selected_ids = {id(candidate) for candidate in selected}
        balanced_pool = selected + [
            candidate for candidate in candidates
            if id(candidate) not in selected_ids
        ]
        selected = market_balancer(balanced_pool, target_count)
    return selected


def ai_preselect_gold_candidates(
    candidates: List[T],
    target_count: int,
    analyzer: Any,
    model: Optional[str] = None,
) -> List[T]:
    """Use AI to choose GoldDigger deep-analysis candidates from one market pool."""
    if target_count <= 0:
        return []
    if len(candidates) <= target_count:
        return candidates

    return _preselect(
        candidates=candidates,
        target_count=target_count,
        analyzer=analyzer,
        prompt=_gold_prompt(candidates, target_count),
        model=model,
        ticker_getter=lambda item: item.ticker,
        log_label="GoldDigger AI preselection",
    )


def _preselect(
    candidates: List[T],
    target_count: int,
    analyzer: Any,
    prompt: str,
    model: Optional[str],
    ticker_getter: Callable[[T], str],
    log_label: str,
) -> List[T]:
    fallback = candidates[:target_count]
    raw = _generate_text_with_timeout(analyzer, prompt, model, log_label)
    if raw is None:
        return fallback

    by_ticker: Dict[str, T] = {
        _normalize_ticker(ticker_getter(candidate)): candidate
        for candidate in candidates
    }
    ranked_tickers = _parse_ranked_tickers(raw or "")
    if not ranked_tickers:
        ranked_tickers = _extract_known_tickers(raw or "", list(by_ticker.keys()))
    if not ranked_tickers:
        logger.warning(
            "%s returned no usable tickers; falling back to rule ranking. Raw preview: %s",
            log_label,
            (raw or "")[:300].replace("\n", " "),
        )
        return fallback

    selected: List[T] = []
    selected_keys = set()
    for ticker in ranked_tickers:
        key = _normalize_ticker(ticker)
        candidate = by_ticker.get(key)
        if candidate is None or key in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(key)
        if len(selected) >= target_count:
            break

    if len(selected) < target_count:
        for candidate in candidates:
            key = _normalize_ticker(ticker_getter(candidate))
            if key in selected_keys:
                continue
            selected.append(candidate)
            selected_keys.add(key)
            if len(selected) >= target_count:
                break

    logger.info("%s selected %d/%d candidates", log_label, len(selected), len(candidates))
    return selected


def _generate_text_with_timeout(
    analyzer: Any,
    prompt: str,
    model: Optional[str],
    log_label: str,
) -> Optional[str]:
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        analyzer.generate_text,
        prompt,
        max_tokens=2500,
        temperature=0.2,
        model=model,
    )
    try:
        return future.result(timeout=_PRESELECT_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        logger.warning(
            "%s timed out after %ss; falling back to rule ranking",
            log_label,
            _PRESELECT_TIMEOUT_SECONDS,
        )
        return None
    except Exception as exc:
        logger.warning("%s failed, falling back to rule ranking: %s", log_label, exc)
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _parse_ranked_tickers(raw: str) -> List[str]:
    text = _strip_json_fence(raw).strip()
    if not text:
        return []

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", text)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

    items: Sequence[Any]
    if isinstance(payload, dict):
        items = payload.get("selected") or payload.get("candidates") or payload.get("ranked") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return []

    tickers: List[str] = []
    for item in items:
        if isinstance(item, str):
            ticker = item
        elif isinstance(item, dict):
            ticker = item.get("ticker") or item.get("symbol") or item.get("code") or ""
        else:
            ticker = ""
        ticker = str(ticker).strip()
        if ticker:
            tickers.append(ticker)
    return tickers


def _extract_known_tickers(raw: str, allowed_tickers: List[str]) -> List[str]:
    """Recover ranked tickers from non-JSON model output using the input whitelist."""
    text = str(raw or "").upper()
    found: List[str] = []
    seen = set()
    # Prefer longer symbols first so 600519 is matched before shorter overlaps.
    for ticker in sorted(allowed_tickers, key=len, reverse=True):
        if not ticker or ticker in seen:
            continue
        pattern = r"(?<![A-Z0-9.])" + re.escape(ticker) + r"(?![A-Z0-9.])"
        match = re.search(pattern, text)
        if match:
            found.append((match.start(), ticker))
            seen.add(ticker)
    found.sort(key=lambda item: item[0])
    return [ticker for _, ticker in found]


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _scanner_prompt(candidates: List[Any], target_count: int) -> str:
    rows = []
    for idx, candidate in enumerate(candidates, start=1):
        fund = candidate.fund
        tech = fund.tech
        stock = tech.stock
        rows.append(
            " | ".join([
                f"rank={idx}",
                f"ticker={candidate.ticker}",
                f"name={stock.name}",
                f"market={stock.market}",
                f"sector={stock.sector}",
                f"industry={stock.industry}",
                f"composite={candidate.composite_score:.1f}",
                f"technical={tech.signal_score}",
                f"trend={tech.trend_status}",
                f"signal={tech.buy_signal}",
                f"fundamental={fund.fundamental_score:.1f}",
                f"pe={_fmt_optional(fund.pe_ratio)}",
                f"growth={_fmt_percent(fund.revenue_growth)}",
            ])
        )

    return (
        "You are selecting stocks for the expensive final deep-analysis stage of a "
        "cross-market Scanner. The input candidates already passed metadata, "
        "technical, fundamental, policy, sector, and market-diversity filters.\n"
        f"Select exactly {target_count} tickers for final analysis. Prefer candidates "
        "with durable catalysts, quality fundamentals, clean technical setup, and "
        "cross-market coverage when multiple markets are present. Do not invent "
        "tickers. Return only JSON as an array of objects with ticker, score, reason.\n\n"
        "Candidates:\n" + "\n".join(rows)
    )


def _gold_prompt(candidates: List[Any], target_count: int) -> str:
    rows = []
    for idx, candidate in enumerate(candidates, start=1):
        stock = candidate.stock
        themes = "; ".join(
            f"{match.theme_name}:{match.relevance_score:.0f}"
            for match in candidate.theme_matches[:3]
        ) or "none"
        rows.append(
            " | ".join([
                f"rank={idx}",
                f"ticker={candidate.ticker}",
                f"name={stock.name}",
                f"market={stock.market}",
                f"sector={stock.sector}",
                f"industry={stock.industry}",
                f"composite={candidate.composite_score:.1f}",
                f"value={candidate.value_score:.1f}",
                f"reversal={candidate.momentum_reversal_score:.1f}",
                f"theme={candidate.top_theme_score:.1f}",
                f"institutional={candidate.institutional_score:.1f}",
                f"6m_change={stock.price_change_6m_pct:.1f}%",
                f"1m_change={stock.price_change_1m_pct:.1f}%",
                f"pe_discount={_fmt_percent_number(stock.pe_discount_pct)}",
                f"themes={themes}",
            ])
        )

    return (
        "You are selecting candidates for the expensive final deep-analysis stage of "
        "GoldDigger. The input stocks are beaten-down, undervalued, low-coverage "
        "candidates with macro/theme matches.\n"
        f"Select exactly {target_count} tickers that deserve full analysis. Prefer "
        "asymmetric upside, credible catalysts, theme fit, and signs of stabilization. "
        "Avoid value traps when the data suggests weak recovery odds. Do not invent "
        "tickers. Return only JSON as an array of objects with ticker, score, reason.\n\n"
        "Candidates:\n" + "\n".join(rows)
    )


def _fmt_optional(value: Optional[float]) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def _fmt_percent(value: Optional[float]) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _fmt_percent_number(value: Optional[float]) -> str:
    return f"{value:.1f}%" if value is not None else "n/a"
