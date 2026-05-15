# -*- coding: utf-8 -*-
import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import NewsStore

_TRACKING_RE = re.compile(r"[?&](utm_\w+|ref|from|source|medium|campaign)=[^&]*", re.IGNORECASE)


def normalize_url(url: str) -> str:
    url = url.strip().lower()
    url = _TRACKING_RE.sub("", url)
    url = url.rstrip("/").rstrip("?").rstrip("&")
    return url


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


class SimHasher:
    """64-bit SimHash for near-duplicate detection (Hamming distance ≤ 3 = near-dup)."""

    BITS = 64
    _TOKEN_RE = re.compile(r"\w+")

    def compute(self, text: str) -> int:
        tokens = self._TOKEN_RE.findall(text.lower())[:200]
        v = [0] * self.BITS
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            for i in range(self.BITS):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1
        result = 0
        for i in range(self.BITS):
            if v[i] > 0:
                result |= 1 << i
        return result

    @staticmethod
    def hamming_distance(h1: int, h2: int) -> int:
        x = h1 ^ h2
        count = 0
        while x:
            count += x & 1
            x >>= 1
        return count


def simhash_to_db(n: int) -> int:
    """Convert unsigned 64-bit SimHash to signed 64-bit int for SQLite storage."""
    if n >= (1 << 63):
        n -= (1 << 64)
    return n


def simhash_from_db(n: int) -> int:
    """Reverse signed→unsigned for Hamming distance comparison."""
    if n < 0:
        n += (1 << 64)
    return n


_NEAR_DUP_THRESHOLD = 3
_hasher = SimHasher()


class Deduplicator:
    """Checks RawArticle novelty: URL hash (exact) + SimHash (near-duplicate)."""

    def __init__(self, store: "NewsStore") -> None:
        self._store = store

    def is_new(self, article) -> bool:
        from .dedup import url_hash as _url_hash

        h = _url_hash(article.url)
        if self._store.exists_by_url_hash(h):
            return False

        text = f"{article.title} {article.content[:200]}"
        sh = simhash_to_db(_hasher.compute(text))
        if self._store.near_duplicate_exists(sh, _NEAR_DUP_THRESHOLD):
            return False

        return True
