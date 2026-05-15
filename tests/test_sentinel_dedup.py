# -*- coding: utf-8 -*-
"""Tests for sentinel deduplication helpers (offline, no network)."""
import pytest
from src.services.sentinel.dedup import normalize_url, url_hash, SimHasher


class TestNormalizeUrl:
    def test_strips_whitespace(self):
        assert normalize_url("  https://example.com  ") == "https://example.com"

    def test_lowercases(self):
        assert normalize_url("HTTPS://Example.COM/Path") == "https://example.com/path"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com"

    def test_removes_utm_params(self):
        raw = "https://example.com/article?utm_source=rss&utm_medium=feed"
        assert "utm_" not in normalize_url(raw)

    def test_preserves_meaningful_params(self):
        raw = "https://example.com/search?q=gold&page=2"
        result = normalize_url(raw)
        assert "q=gold" in result or "page=2" in result  # non-tracking params kept


class TestUrlHash:
    def test_same_url_same_hash(self):
        assert url_hash("https://example.com/a") == url_hash("https://example.com/a")

    def test_different_url_different_hash(self):
        assert url_hash("https://example.com/a") != url_hash("https://example.com/b")

    def test_returns_hex_string(self):
        h = url_hash("https://example.com")
        assert len(h) == 64  # SHA-256 hex = 64 chars
        int(h, 16)  # must be valid hex

    def test_trailing_slash_equivalence(self):
        assert url_hash("https://example.com") == url_hash("https://example.com/")


class TestSimHasher:
    def setup_method(self):
        self.h = SimHasher()

    def test_identical_texts_same_hash(self):
        t = "央行宣布降息 25 个基点，市场情绪向好"
        assert self.h.compute(t) == self.h.compute(t)

    def test_very_different_texts_large_distance(self):
        h1 = self.h.compute("央行降息货币政策宽松")
        h2 = self.h.compute("earthquake hurricane weather disaster flood")
        dist = SimHasher.hamming_distance(h1, h2)
        # Different language / topic — expect significant divergence
        assert dist > 5

    def test_near_duplicate_small_distance(self):
        text = "国务院发布新能源汽车补贴政策，新能源板块大涨"
        t2 = text + " 。"   # trivially different
        h1 = self.h.compute(text)
        h2 = self.h.compute(t2)
        assert SimHasher.hamming_distance(h1, h2) <= 3

    def test_hamming_distance_zero_same(self):
        h = self.h.compute("hello world")
        assert SimHasher.hamming_distance(h, h) == 0

    def test_hamming_distance_symmetric(self):
        h1 = self.h.compute("foo bar baz")
        h2 = self.h.compute("qux quux corge")
        assert SimHasher.hamming_distance(h1, h2) == SimHasher.hamming_distance(h2, h1)

    def test_returns_int(self):
        assert isinstance(self.h.compute("test"), int)

    def test_empty_text(self):
        # Should not raise
        h = self.h.compute("")
        assert isinstance(h, int)
