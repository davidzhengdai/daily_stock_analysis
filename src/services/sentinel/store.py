# -*- coding: utf-8 -*-
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional

from .dedup import SimHasher, simhash_to_db, simhash_from_db, url_hash
from .models import NewsItem, RawArticle

logger = logging.getLogger(__name__)

_hasher = SimHasher()

_DDL = """
CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash        TEXT NOT NULL UNIQUE,
    simhash         INTEGER,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT,
    source_name     TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    spider_name     TEXT NOT NULL,
    language        TEXT DEFAULT 'zh',
    category        TEXT,
    priority        INTEGER,
    sentiment       TEXT,
    market_scope    TEXT,
    affected_sectors TEXT,
    affected_stocks  TEXT,
    impact_horizon   TEXT,
    llm_reasoning    TEXT,
    is_actionable    INTEGER DEFAULT 0,
    published_at     TEXT,
    fetched_at       TEXT NOT NULL,
    expires_at       TEXT,
    is_expired       INTEGER DEFAULT 0,
    is_archived      INTEGER DEFAULT 0,
    CONSTRAINT priority_range CHECK (priority IS NULL OR priority BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS idx_news_url_hash  ON news_items(url_hash);
CREATE INDEX IF NOT EXISTS idx_news_spider    ON news_items(spider_name, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_fetched   ON news_items(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_priority  ON news_items(priority DESC, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_simhash   ON news_items(simhash);

CREATE TABLE IF NOT EXISTS spider_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    spider_name   TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    items_fetched INTEGER DEFAULT 0,
    items_new     INTEGER DEFAULT 0,
    items_deduped INTEGER DEFAULT 0,
    error_msg     TEXT,
    status        TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS cycle_analyses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_at         TEXT NOT NULL,
    news_count       INTEGER,
    themes           TEXT,
    sector_opps      TEXT,
    stock_leads      TEXT,
    risk_alerts      TEXT,
    market_mood      TEXT,
    triggered_stocks TEXT,
    model_used       TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS news_fts USING fts5(
    title, content, source_name,
    content='news_items',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS news_items_ai AFTER INSERT ON news_items BEGIN
    INSERT INTO news_fts(rowid, title, content, source_name)
    VALUES (new.id, new.title, new.content, new.source_name);
END;

CREATE TRIGGER IF NOT EXISTS news_items_ad AFTER DELETE ON news_items BEGIN
    INSERT INTO news_fts(news_fts, rowid, title, content, source_name)
    VALUES ('delete', old.id, old.title, old.content, old.source_name);
END;

CREATE TRIGGER IF NOT EXISTS news_items_au AFTER UPDATE ON news_items BEGIN
    INSERT INTO news_fts(news_fts, rowid, title, content, source_name)
    VALUES ('delete', old.id, old.title, old.content, old.source_name);
    INSERT INTO news_fts(rowid, title, content, source_name)
    VALUES (new.id, new.title, new.content, new.source_name);
END;
"""


class NewsStore:
    def __init__(self, db_path: str = "data/sentinel.db") -> None:
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    # ── connection ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_db(self) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.executescript(_DDL)
                try:
                    con.executescript(_FTS_DDL)
                except sqlite3.OperationalError as exc:
                    logger.warning("FTS5 setup skipped: %s", exc)
                con.commit()
            finally:
                con.close()

    # ── write ─────────────────────────────────────────────────────────────────

    def upsert(self, article: RawArticle) -> bool:
        """Insert article; return True if newly inserted, False if duplicate URL."""
        uh = url_hash(article.url)
        text = f"{article.title} {article.content[:200]}"
        sh = simhash_to_db(_hasher.compute(text))
        fetched = article.fetched_at.isoformat() if article.fetched_at else datetime.now(timezone.utc).isoformat()
        pub = article.published_at.isoformat() if article.published_at else None
        source_url = article.source_url or ""

        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO news_items
                        (url_hash, simhash, url, title, content,
                         source_name, source_url, spider_name, language,
                         published_at, fetched_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (uh, sh, article.url, article.title, article.content,
                     article.source_name, source_url, article.spider_name, article.language,
                     pub, fetched),
                )
                con.commit()
                return cur.rowcount > 0
            except sqlite3.Error as exc:
                logger.error("NewsStore.upsert failed: %s", exc)
                return False
            finally:
                con.close()

    def log_spider_run(
        self,
        spider_name: str,
        started_at: str,
        finished_at: str,
        items_fetched: int,
        items_new: int,
        items_deduped: int,
        status: str,
        error_msg: str = "",
    ) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    """
                    INSERT INTO spider_runs
                        (spider_name, started_at, finished_at,
                         items_fetched, items_new, items_deduped, error_msg, status)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (spider_name, started_at, finished_at,
                     items_fetched, items_new, items_deduped, error_msg or None, status),
                )
                con.commit()
            finally:
                con.close()

    # ── read ──────────────────────────────────────────────────────────────────

    def get_item_by_url_hash(self, uh: str) -> Optional[sqlite3.Row]:
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    "SELECT * FROM news_items WHERE url_hash=? LIMIT 1", (uh,)
                ).fetchone()
            finally:
                con.close()

    def exists_by_url_hash(self, uh: str) -> bool:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT 1 FROM news_items WHERE url_hash=? LIMIT 1", (uh,)
                ).fetchone()
                return row is not None
            finally:
                con.close()

    def near_duplicate_exists(self, simhash: int, threshold: int = 3) -> bool:
        """Return True if any stored item has Hamming distance ≤ threshold to simhash."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT simhash FROM news_items WHERE simhash IS NOT NULL LIMIT 5000"
                ).fetchall()
                query_unsigned = simhash_from_db(simhash) if simhash < 0 else simhash
                for row in rows:
                    stored_unsigned = simhash_from_db(row[0])
                    if SimHasher.hamming_distance(query_unsigned, stored_unsigned) <= threshold:
                        return True
                return False
            finally:
                con.close()

    def get_recent(
        self,
        hours: int = 24,
        priority_min: int = 1,
        limit: int = 200,
    ) -> List[sqlite3.Row]:
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    """
                    SELECT * FROM news_items
                    WHERE fetched_at >= datetime('now', ?)
                      AND (priority IS NULL OR priority >= ?)
                      AND is_expired = 0
                    ORDER BY fetched_at DESC
                    LIMIT ?
                    """,
                    (f"-{hours} hours", priority_min, limit),
                ).fetchall()
            finally:
                con.close()

    def search_fts(self, query: str, limit: int = 20) -> List[sqlite3.Row]:
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    """
                    SELECT n.* FROM news_items n
                    JOIN news_fts f ON f.rowid = n.id
                    WHERE news_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
            finally:
                con.close()

    def count(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                return con.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
            finally:
                con.close()

    def count_by_spider(self) -> dict:
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    "SELECT spider_name, COUNT(*) as cnt FROM news_items GROUP BY spider_name"
                ).fetchall()
                return {row[0]: row[1] for row in rows}
            finally:
                con.close()

    # ── Phase 2: classification & lifecycle ──────────────────────────────────

    def get_pending_classification(self, limit: int = 20) -> List[sqlite3.Row]:
        """Return unclassified items (priority IS NULL) oldest-first.

        Note: `analyzed_at` column is not present in the Phase 1 schema;
        `priority IS NULL` is used as the proxy for "not yet classified."
        """
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    """
                    SELECT * FROM news_items
                    WHERE priority IS NULL AND is_expired = 0
                    ORDER BY fetched_at ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            finally:
                con.close()

    def update_classification(self, url_hash: str, fields: dict) -> None:
        """Update LLM classification fields for a news item.

        Accepted keys: category, priority, sentiment, market_scope,
        affected_sectors, affected_stocks, impact_horizon, llm_reasoning,
        is_actionable, expires_at.  Unknown keys are silently dropped.
        """
        _ALLOWED = frozenset({
            "category", "priority", "sentiment", "market_scope",
            "affected_sectors", "affected_stocks", "impact_horizon",
            "llm_reasoning", "is_actionable", "expires_at",
        })
        safe = {k: v for k, v in fields.items() if k in _ALLOWED}
        if not safe:
            return
        cols = ", ".join(f"{k}=?" for k in safe)
        vals = list(safe.values()) + [url_hash]
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    f"UPDATE news_items SET {cols} WHERE url_hash=?", vals
                )
                con.commit()
            except sqlite3.Error as exc:
                logger.error("update_classification failed for %s: %s", url_hash[:12], exc)
            finally:
                con.close()

    def record_cycle_analysis(self, data: dict) -> int:
        """Insert a cycle analysis record; return the new row id."""
        fields = (
            "cycle_at", "news_count", "themes", "sector_opps",
            "stock_leads", "risk_alerts", "market_mood",
            "triggered_stocks", "model_used",
        )
        vals = tuple(data.get(f) for f in fields)
        with self._lock:
            con = self._connect()
            try:
                cur = con.execute(
                    f"INSERT INTO cycle_analyses ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                    vals,
                )
                con.commit()
                return cur.lastrowid or 0
            except sqlite3.Error as exc:
                logger.error("record_cycle_analysis failed: %s", exc)
                return 0
            finally:
                con.close()

    def purge_expired(self) -> dict:
        """Archive P4 and hard-delete P1/P2/P3 rows past their expires_at.

        P5 rows are never touched (permanent).
        Returns {"deleted": N, "archived": N}.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            con = self._connect()
            try:
                # Hard-delete P1, P2, P3 past TTL
                cur_del = con.execute(
                    """
                    DELETE FROM news_items
                    WHERE priority IN (1, 2, 3)
                      AND expires_at IS NOT NULL
                      AND expires_at < ?
                    """,
                    (now_iso,),
                )
                deleted = cur_del.rowcount

                # Soft-archive P4 past TTL
                cur_arc = con.execute(
                    """
                    UPDATE news_items
                    SET is_archived = 1, is_expired = 1
                    WHERE priority = 4
                      AND expires_at IS NOT NULL
                      AND expires_at < ?
                      AND is_archived = 0
                    """,
                    (now_iso,),
                )
                archived = cur_arc.rowcount

                # Purge unclassified items older than 7 days (no priority assigned)
                cur_unc = con.execute(
                    """
                    DELETE FROM news_items
                    WHERE priority IS NULL
                      AND fetched_at < datetime('now', '-7 days')
                    """,
                )
                deleted += cur_unc.rowcount

                con.commit()
                logger.info("TTL purge: deleted=%d archived=%d", deleted, archived)
                return {"deleted": deleted, "archived": archived}
            except sqlite3.Error as exc:
                logger.error("purge_expired failed: %s", exc)
                return {"deleted": 0, "archived": 0}
            finally:
                con.close()

    def count_unclassified(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    "SELECT COUNT(*) FROM news_items WHERE priority IS NULL AND is_expired = 0"
                ).fetchone()[0]
            finally:
                con.close()

    def get_recent_classified(
        self,
        hours: int = 12,
        priority_min: int = 3,
        limit: int = 100,
    ) -> List[sqlite3.Row]:
        """Return P{priority_min}+ classified items fetched within the last N hours."""
        with self._lock:
            con = self._connect()
            try:
                return con.execute(
                    """
                    SELECT * FROM news_items
                    WHERE fetched_at >= datetime('now', ?)
                      AND priority IS NOT NULL
                      AND priority >= ?
                      AND is_expired = 0
                    ORDER BY priority DESC, fetched_at DESC
                    LIMIT ?
                    """,
                    (f"-{hours} hours", priority_min, limit),
                ).fetchall()
            finally:
                con.close()

    def get_last_cycle_analysis_at(self) -> Optional[str]:
        """Return the most recent cycle_at timestamp from cycle_analyses, or None."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT cycle_at FROM cycle_analyses ORDER BY cycle_at DESC LIMIT 1"
                ).fetchone()
                return row[0] if row else None
            finally:
                con.close()

    def update_cycle_triggered_stocks(self, cycle_id: int, triggered_json: str) -> None:
        with self._lock:
            con = self._connect()
            try:
                con.execute(
                    "UPDATE cycle_analyses SET triggered_stocks=? WHERE id=?",
                    (triggered_json, cycle_id),
                )
                con.commit()
            except sqlite3.Error as exc:
                logger.error("update_cycle_triggered_stocks failed: %s", exc)
            finally:
                con.close()
