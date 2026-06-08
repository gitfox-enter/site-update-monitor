#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site-update-monitor shared module.

This module extracts and centralizes all code shared between ``crawl.py``
(the hourly full crawler) and ``fast_check.py`` (the high-frequency
incremental checker).  Importing from a single place eliminates duplication,
keeps constants in sync, and provides a stable public API that both scripts
(and the test suite) can rely on.

Contents:
  - Structured JSON log formatter
  - Beijing timezone helper
  - Auto-categorization by keywords
  - items.json persistence (load / save with atomic writes)
  - Blacklist loading and domain matching
  - Source-name O(1) lookup index
  - Input sanitization (href / text)
  - Junk-link detection
  - MD5 hashing helper
  - HTTPS auto-upgrade heuristic
  - Per-domain rate limiter
"""

# ============================================================
# Imports
# ============================================================

import os
import sys
import re
import json
import time
import sqlite3
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ============================================================
# Constants
# ============================================================

ITEMS_DB_FILE: str = "items.json"

BLACKLIST_FILE: str = "blacklist.json"

SQLITE_DB_FILE: str = "monitor.db"

MAX_ITEMS_DB: int = 1500

# ============================================================
# Public API
# ============================================================

__all__ = [
    # Constants
    "ITEMS_DB_FILE",
    "BLACKLIST_FILE",
    # Logging
    "JsonFormatter",
    # Time helpers
    "get_beijing_time",
    # Categorization
    "CATEGORY_KEYWORDS",
    "auto_categorize",
    # Data persistence
    "load_items_db",
    "save_items_db",
    # Blacklist
    "load_blacklist",
    "is_blacklisted",
    # Source-name index
    "build_source_name_index",
    "get_source_name",
    # Input sanitization
    "sanitize_href",
    "sanitize_text",
    # Junk detection
    "JUNK_PATTERNS",
    "is_junk",
    # Hashing
    "calculate_md5",
    # HTTPS upgrade
    "upgrade_to_https",
    # Rate limiter
    "DomainRateLimiter",
    # SQLite
    "SQLITE_DB_FILE",
    "MAX_ITEMS_DB",
    "init_sqlite",
    "sqlite_insert_items",
    "sqlite_get_recent_items",
    "sqlite_get_existing_urls",
    "sqlite_export_json",
    "sqlite_load_hash_records",
    "sqlite_save_hash_records",
    "sqlite_get_meta",
    "sqlite_set_meta",
]


# ============================================================
# Structured JSON log formatter
# ============================================================

class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON strings for structured log collection and analysis."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            'timestamp': datetime.fromtimestamp(
                record.created, tz=timezone(timedelta(hours=8))
            ).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry['exception'] = str(record.exc_info[1])
        # Attach optional extra fields (site, status_code, response_time, event)
        for key in ('site', 'status_code', 'response_time', 'event'):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, ensure_ascii=False)


# ============================================================
# Beijing timezone helper
# ============================================================

def get_beijing_time() -> datetime:
    """Return the current time in the Asia/Shanghai (UTC+8) timezone."""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(beijing_tz)


# ============================================================
# Auto-categorization by keywords
# ============================================================

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "京东": ["京东", "jd.com", "jd", "京豆", "京享"],
    "淘宝": ["淘宝", "天猫", "tmall", "taobao", "淘金币"],
    "拼多多": ["拼多多", "pdd", "拼多"],
    "外卖": ["外卖", "美团", "饿了么", "美团外卖"],
    "红包": ["红包", "虹包", "鸿包", "必中红包"],
    "优惠券": ["优惠券", "券", "满减", "消费券", "领券"],
}


def auto_categorize(text: str) -> Optional[str]:
    """Return the first matching category for *text*, or ``None`` if no keyword matches."""
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return None


# ============================================================
# Data persistence  (items.json)
# ============================================================

def load_items_db() -> Dict[str, Any]:
    """Load the items database from *ITEMS_DB_FILE*.

    Returns a fresh skeleton ``{"items": [], "updated_at": ""}`` when the
    file is missing, unreadable, or malformed.
    """
    if os.path.exists(ITEMS_DB_FILE):
        try:
            with open(ITEMS_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "items" in data:
                return data
        except Exception:
            pass
    return {"items": [], "updated_at": ""}


def save_items_db(db: Dict[str, Any]) -> bool:
    """Atomically persist *db* to *ITEMS_DB_FILE* (write to .tmp then os.replace).

    Returns ``True`` on success, ``False`` on failure.
    """
    tmp_file = ITEMS_DB_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_file, ITEMS_DB_FILE)
        return True
    except Exception:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


# ============================================================
# Blacklist
# ============================================================

def load_blacklist(path: str = BLACKLIST_FILE) -> List[str]:
    """Load blacklist domains from a JSON file.

    Expected JSON structure::

        {"blacklist": [{"domain": "example.com"}, ...]}

    Returns a list of lowercased domain strings, or an empty list when the
    file is missing or malformed.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            blacklist_data = json.load(f)
        return [entry["domain"].lower() for entry in blacklist_data.get("blacklist", [])]
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
        return []


def is_blacklisted(url: str, blacklist_domains: List[str]) -> bool:
    """Check whether *url* belongs to any domain in *blacklist_domains*.

    The comparison strips ``www.`` and ``m.`` prefixes and also matches
    sub-domains (e.g. ``sub.example.com`` matches ``example.com``).
    """
    parsed = urlparse(url)
    host = parsed.hostname or parsed.netloc
    host = host.lower().lstrip("www.").lstrip("m.")
    for domain in blacklist_domains:
        domain_clean = domain.lower().lstrip("www.").lstrip("m.")
        if host == domain_clean or host.endswith("." + domain_clean):
            return True
    return False


# ============================================================
# Source-name O(1) lookup
# ============================================================

def build_source_name_index(source_name_map: Dict[str, str]) -> Dict[str, str]:
    """Build a hostname -> name index for O(1) lookup.

    Both ``www.`` and non-``www.`` variants are indexed so that lookups
    succeed regardless of which form the URL uses.
    """
    index: Dict[str, str] = {}
    for base_url, name in source_name_map.items():
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        # Index both www and non-www versions
        index[host] = name
        if host.startswith("www."):
            index[host[4:]] = name
        else:
            index["www." + host] = name
    return index


def get_source_name(url: str, index: Dict[str, str]) -> Optional[str]:
    """Look up the display name for *url* via a pre-built hostname index.

    Returns ``None`` when no match is found.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    name = index.get(host)
    if name:
        return name
    # Fallback: try without www prefix
    if host.startswith("www."):
        return index.get(host[4:])
    return None


# ============================================================
# Input sanitization (precompiled regexes)
# ============================================================

# Match javascript: protocol (case-insensitive, ignoring leading whitespace)
_JS_HREF_RE = re.compile(r"^\s*javascript\s*:", re.IGNORECASE)
# Control characters and zero-width characters
_CONTROL_CHAR_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200c\u200d\ufeff]"
)


def sanitize_href(href: str) -> str:
    """Clean an href value: strip whitespace and reject ``javascript:`` URIs.

    Returns an empty string when the href is unsafe.
    """
    href = href.strip()
    if _JS_HREF_RE.match(href):
        return ""
    return href


def sanitize_text(text: str) -> str:
    """Clean visible text: remove control / zero-width characters and collapse whitespace."""
    text = _CONTROL_CHAR_RE.sub("", text)
    text = " ".join(text.split())
    return text


# ============================================================
# Junk detection
# ============================================================

JUNK_PATTERNS: List[str] = [
    "安卓软件", "办公软件", "安全软件", "查看详情", "直达链接", "阅读全文",
    "继续阅读", "更多", "首页", "登录", "注册", "搜索", "javascript:",
    "关于我们", "联系我们", "免责声明", "版权声明", "友情链接",
]


def is_junk(text: str) -> bool:
    """Return ``True`` if *text* looks like a non-content link (too short, pure digits, or a known junk pattern)."""
    if len(text) < 5:
        return True
    if text.isdigit():
        return True
    clean = text.replace(" ", "")
    for jp in JUNK_PATTERNS:
        if clean == jp:
            return True
    return False


# ============================================================
# MD5 hashing
# ============================================================

def calculate_md5(text: str) -> str:
    """Return the hex MD5 digest of *text* (UTF-8 encoded)."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ============================================================
# HTTPS auto-upgrade
# ============================================================

def upgrade_to_https(url: str) -> str:
    """Upgrade ``http://`` to ``https://`` (heuristic: most modern sites support HTTPS)."""
    if url.startswith("http://"):
        return "https://" + url[7:]
    return url


# ============================================================
# Per-domain rate limiter
# ============================================================

class DomainRateLimiter:
    """Thread-safe per-domain rate limiter enforcing a minimum gap between requests."""

    def __init__(self, min_gap: float = 2.0) -> None:
        self._lock = threading.Lock()
        self._last_request: Dict[str, float] = {}
        self._min_gap = min_gap

    def wait(self, domain: str) -> None:
        """Block until at least *min_gap* seconds have elapsed since the last request to *domain*."""
        with self._lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            elapsed = now - last
            if elapsed < self._min_gap:
                time.sleep(self._min_gap - elapsed)
            self._last_request[domain] = time.time()


# ============================================================
# SQLite Data Layer
# ============================================================

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    url TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    source TEXT DEFAULT '',
    category TEXT,
    time TEXT DEFAULT '',
    inserted_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_time ON items(time DESC);
CREATE INDEX IF NOT EXISTS idx_items_inserted ON items(inserted_at DESC);

CREATE TABLE IF NOT EXISTS hash_records (
    url TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def init_sqlite(db_path: str = SQLITE_DB_FILE) -> sqlite3.Connection:
    """Initialize SQLite database with schema. Returns the connection."""
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging for concurrency
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SQLITE_SCHEMA)
    conn.commit()
    return conn


def sqlite_insert_items(conn: sqlite3.Connection, items: List[Dict[str, str]],
                        check_time: str = "") -> int:
    """Insert items into SQLite (INSERT OR IGNORE for dedup). Returns count of new inserts."""
    if not items:
        return 0
    added = 0
    for item in items:
        url = item.get("url", "")
        if not url:
            continue
        text = item.get("text", "")
        source = item.get("source", "")
        category = item.get("category") or auto_categorize(text)
        t = item.get("time", check_time)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO items (url, text, source, category, time) VALUES (?, ?, ?, ?, ?)",
                (url, text, source, category, t),
            )
            if conn.total_changes:
                added += 1
        except sqlite3.Error:
            pass
    conn.commit()
    # Enforce MAX_ITEMS_DB: keep only the most recent entries
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if count > MAX_ITEMS_DB:
        excess = count - MAX_ITEMS_DB
        conn.execute(
            "DELETE FROM items WHERE url IN (SELECT url FROM items ORDER BY inserted_at ASC LIMIT ?)",
            (excess,),
        )
        conn.commit()
    return added


def sqlite_get_recent_items(conn: sqlite3.Connection, limit: int = MAX_ITEMS_DB) -> List[Dict[str, str]]:
    """Get the most recent items from SQLite."""
    cursor = conn.execute(
        "SELECT url, text, source, category, time FROM items ORDER BY inserted_at DESC LIMIT ?",
        (limit,),
    )
    items = []
    for row in cursor:
        items.append({
            "url": row[0],
            "text": row[1],
            "source": row[2],
            "category": row[3],
            "time": row[4],
        })
    return items


def sqlite_get_existing_urls(conn: sqlite3.Connection) -> Set[str]:
    """Get all existing item URLs from SQLite."""
    cursor = conn.execute("SELECT url FROM items")
    return {row[0] for row in cursor}


def sqlite_export_json(conn: sqlite3.Connection, json_path: str = ITEMS_DB_FILE) -> bool:
    """Export SQLite items to items.json for frontend SPA consumption (atomic write)."""
    items = sqlite_get_recent_items(conn)
    updated_at = get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")
    db = {"items": items, "updated_at": updated_at}
    tmp_file = json_path + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_file, json_path)
        return True
    except Exception:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


def sqlite_load_hash_records(conn: sqlite3.Connection) -> Dict[str, str]:
    """Load all hash records from SQLite."""
    cursor = conn.execute("SELECT url, hash FROM hash_records")
    return {row[0]: row[1] for row in cursor}


def sqlite_save_hash_records(conn: sqlite3.Connection, records: Dict[str, str]) -> None:
    """Save hash records to SQLite (upsert)."""
    updated_at = get_beijing_time().isoformat()
    for url, hash_val in records.items():
        conn.execute(
            "INSERT OR REPLACE INTO hash_records (url, hash, updated_at) VALUES (?, ?, ?)",
            (url, hash_val, updated_at),
        )
    conn.commit()


def sqlite_get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    """Get a metadata value from SQLite."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def sqlite_set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Set a metadata value in SQLite."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()
