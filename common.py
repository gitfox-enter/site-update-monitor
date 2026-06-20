#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSSForge shared module.

This module extracts and centralizes all code shared between ``crawl.py``
(the hourly full crawler) and ``fast_check.py`` (the high-frequency
incremental checker) for the **RSSForge** project.  Importing from a single place eliminates duplication,
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
  - Async-safe proxy pool with health tracking
"""

import os
import sys
import re
import json
import time
import random
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger('common')

# ============================================================
# Constants
# ============================================================

ITEMS_DB_FILE: str = "items.json"
ITEMS_LATEST_FILE: str = "items_latest.json"
CRAWL_STATUS_FILE: str = "crawl_status.json"

BLACKLIST_FILE: str = "blacklist.json"

MAX_ITEMS_DB: int = 0  # 0 = 无上限，仅按7天时间窗口保留


# ============================================================
# Filename slugification — ASCII-safe filenames (fix #9)
# ============================================================

def slugify(name: str) -> str:
    """将站点名称转为 ASCII 安全的文件名。

    中文字符转为拼音，其他非 ASCII 字符移除。
    例如: '线报酷' -> 'xian-bao-ku', '423Down' -> '423Down'
    """
    if not name:
        return 'unknown'

    # 尝试用 pypinyin 转换中文
    try:
        from pypinyin import lazy_pinyin, Style
        parts = lazy_pinyin(name, style=Style.NORMAL)
        slug = '-'.join(parts)
    except ImportError:
        # pypinyin 不可用时，直接移除非 ASCII
        slug = name

    # 保留字母、数字、连字符
    slug = re.sub(r'[^a-zA-Z0-9\-]', '-', slug)
    # 合并连续连字符
    slug = re.sub(r'-{2,}', '-', slug)
    # 移除首尾连字符
    slug = slug.strip('-')

    return slug or 'unknown'

# ============================================================
# Public API
# ============================================================

__all__ = [
    # Constants
    "ITEMS_DB_FILE",
    "ITEMS_LATEST_FILE",
    "CRAWL_STATUS_FILE",
    "BLACKLIST_FILE",
    "MAX_ITEMS_DB",
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
    # Proxy pool
    "ProxyPool",
    "create_proxy_pool",
    # Favicon
    "fetch_site_favicon",
    # Content extraction
    "fetch_article_summary",
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
        # 写后验证：确保文件可读且为有效 JSON
        try:
            with open(ITEMS_DB_FILE, 'r', encoding='utf-8') as f:
                json.load(f)
        except Exception as ve:
            logger.error('JSON 验证失败: %s', ve)
            return False
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
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    elif host.startswith("m."):
        host = host[2:]
    for domain in blacklist_domains:
        domain_clean = domain.lower()
        if domain_clean.startswith("www."):
            domain_clean = domain_clean[4:]
        elif domain_clean.startswith("m."):
            domain_clean = domain_clean[2:]
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
                sleep_time = self._min_gap - elapsed
                # 在锁内预留时间，防止并发竞态 (fix #20)
                self._last_request[domain] = now + sleep_time
            else:
                sleep_time = 0.0
                self._last_request[domain] = now
        # Sleep outside the lock so other domains are not blocked
        if sleep_time > 0:
            time.sleep(sleep_time)

    async def async_wait(self, domain: str) -> None:
        """Async-compatible rate limiter: uses asyncio.sleep to avoid blocking the event loop."""
        import asyncio as _asyncio
        sleep_time = 0.0
        with self._lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            elapsed = now - last
            if elapsed < self._min_gap:
                sleep_time = self._min_gap - elapsed
        if sleep_time > 0:
            await _asyncio.sleep(sleep_time)
        with self._lock:
            self._last_request[domain] = time.time()


# ============================================================
# Proxy pool
# ============================================================

_ALLOWED_PROXY_SCHEMES = {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}


class ProxyPool:
    """Async-safe proxy pool with health tracking, auto-blacklisting, and round-robin rotation.

    Supports:
      - Loading proxies from environment variable ``PROXY_LIST`` (comma-separated)
      - Loading proxies from a JSON file (``proxy_pool.json``)
      - Programmatic add/remove
      - Round-robin or random selection
      - Per-proxy failure tracking with auto-blacklisting after *max_failures*
      - Cooldown period before re-enabling a blacklisted proxy
      - Graceful degradation: returns ``None`` when no healthy proxies available

    Thread safety is provided via :class:`threading.Lock` so the pool can
    safely be shared across threads **and** used from synchronous code that
    sits alongside an asyncio event loop.
    """

    PROXY_LIST_ENV: str = "PROXY_LIST"
    PROXY_FILE: str = "proxy_pool.json"
    DEFAULT_MAX_FAILURES: int = 5
    DEFAULT_COOLDOWN: float = 300.0

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def __init__(
        self,
        proxies: Optional[List[str]] = None,
        max_failures: int = DEFAULT_MAX_FAILURES,
        cooldown: float = DEFAULT_COOLDOWN,
        strategy: str = "round_robin",
    ) -> None:
        if strategy not in ("round_robin", "random"):
            raise ValueError(
                f"Invalid strategy {strategy!r}; expected 'round_robin' or 'random'"
            )

        self._lock = threading.RLock()
        self._proxies: Dict[str, Dict[str, Any]] = {}
        self._rr_index: int = 0
        self._max_failures: int = max_failures
        self._cooldown: float = cooldown
        self._strategy: str = strategy

        if proxies:
            for url in proxies:
                self._add_proxy_unlocked(url)

    # ------------------------------------------------------------------
    # Internal helpers (caller must hold ``_lock``)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_proxy_url(proxy_url: str) -> bool:
        """Return ``True`` when *proxy_url* has an acceptable URL scheme."""
        try:
            parsed = urlparse(proxy_url)
            # 1. Check scheme
            if parsed.scheme.lower() not in _ALLOWED_PROXY_SCHEMES:
                return False
            # 2. Require netloc
            if not parsed.netloc:
                return False
            # 3. Require hostname
            if not parsed.hostname:
                return False
            # 4. Validate port range (1-65535)
            if parsed.port is not None and not (1 <= parsed.port <= 65535):
                return False
            return True
        except Exception:
            return False

    def _add_proxy_unlocked(self, proxy_url: str) -> None:
        """Add *proxy_url* to the pool (lock must already be held)."""
        proxy_url = proxy_url.strip()
        if not proxy_url or not self._validate_proxy_url(proxy_url):
            return
        if proxy_url not in self._proxies:
            self._proxies[proxy_url] = {
                "failures": 0,
                "blacklisted_at": 0.0,
                "success_count": 0,
                "total_count": 0,
            }

    def _is_active_unlocked(self, info: Dict[str, Any]) -> bool:
        """Return ``True`` when a proxy entry is currently usable.

        A blacklisted proxy is automatically re-enabled once its cooldown
        period has elapsed.  This method mutates *info* in-place when
        re-enabling (resets failure counter and blacklist timestamp).
        """
        if info["blacklisted_at"] == 0.0:
            return True
        # Check whether the cooldown has elapsed
        if time.time() - info["blacklisted_at"] >= self._cooldown:
            info["blacklisted_at"] = 0.0
            info["failures"] = 0
            return True
        return False

    # ------------------------------------------------------------------
    # Loading helpers
    # ------------------------------------------------------------------

    def load_from_env(self) -> int:
        """Load proxies from the ``PROXY_LIST`` environment variable.

        The variable should contain a comma-separated list of proxy URLs.
        Returns the number of proxies successfully loaded.
        """
        raw = os.environ.get(self.PROXY_LIST_ENV, "")
        if not raw.strip():
            return 0
        count = 0
        with self._lock:
            for url in raw.split(","):
                url = url.strip()
                if url and self._validate_proxy_url(url) and url not in self._proxies:
                    self._add_proxy_unlocked(url)
                    count += 1
        return count

    def load_from_file(self, path: Optional[str] = None) -> int:
        """Load proxies from a JSON file.

        Expected format::

            {"proxies": ["http://user:pass@host:port", ...]}

        Returns the number of proxies successfully loaded.  Returns ``0``
        when the file is missing or malformed.
        """
        filepath = path or self.PROXY_FILE
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return 0
        if not isinstance(data, dict):
            return 0
        proxy_list = data.get("proxies", [])
        if not isinstance(proxy_list, list):
            return 0
        count = 0
        with self._lock:
            for url in proxy_list:
                if isinstance(url, str):
                    url = url.strip()
                    if url and self._validate_proxy_url(url) and url not in self._proxies:
                        self._add_proxy_unlocked(url)
                        count += 1
        return count

    # ------------------------------------------------------------------
    # Programmatic add / remove
    # ------------------------------------------------------------------

    def add_proxy(self, proxy_url: str) -> None:
        """Add a proxy to the pool.

        Silently ignores duplicates and invalid URLs.
        """
        with self._lock:
            self._add_proxy_unlocked(proxy_url)

    def remove_proxy(self, proxy_url: str) -> None:
        """Remove a proxy from the pool.

        Raises :class:`KeyError` when *proxy_url* is not in the pool.
        """
        with self._lock:
            if proxy_url not in self._proxies:
                raise KeyError(f"Proxy not found in pool: {proxy_url!r}")
            del self._proxies[proxy_url]
            # Guard the round-robin index against shrinking pool sizes
            if self._proxies and self._rr_index >= len(self._proxies):
                self._rr_index = 0

    # ------------------------------------------------------------------
    # Proxy selection
    # ------------------------------------------------------------------

    def get_proxy(self) -> Optional[str]:
        """Return the next healthy proxy URL, or ``None`` if the pool is empty.

        Blacklisted proxies whose cooldown has elapsed are automatically
        re-enabled and become eligible for selection again.  When every
        proxy in the pool is currently blacklisted, ``None`` is returned
        so the caller can gracefully degrade to a direct connection.
        """
        with self._lock:
            if not self._proxies:
                return None

            # Re-enable any proxies whose cooldown has elapsed
            for info in self._proxies.values():
                if info["blacklisted_at"] != 0.0:
                    self._is_active_unlocked(info)

            # Build the candidate list (active, non-blacklisted proxies)
            active: List[str] = [
                url for url, info in self._proxies.items()
                if info["blacklisted_at"] == 0.0
            ]
            if not active:
                return None

            if self._strategy == "random":
                return random.choice(active)

            # Round-robin selection
            idx = self._rr_index % len(active)
            self._rr_index = idx + 1
            return active[idx]

    # ------------------------------------------------------------------
    # Health reporting
    # ------------------------------------------------------------------

    def report_success(self, proxy_url: str) -> None:
        """Report a successful request through *proxy_url*.

        Resets the consecutive failure counter to ``0`` and increments the
        success and total request counters.
        """
        with self._lock:
            info = self._proxies.get(proxy_url)
            if info is not None:
                info["failures"] = 0
                info["success_count"] += 1
                info["total_count"] += 1

    def report_failure(self, proxy_url: str) -> None:
        """Report a failed request through *proxy_url*.

        Increments the consecutive failure counter.  When the counter
        reaches *max_failures* the proxy is blacklisted and will not be
        returned by :meth:`get_proxy` until its cooldown elapses.
        """
        with self._lock:
            info = self._proxies.get(proxy_url)
            if info is not None:
                info["failures"] += 1
                info["total_count"] += 1
                if info["failures"] >= self._max_failures:
                    info["blacklisted_at"] = time.time()

    # ------------------------------------------------------------------
    # Properties and statistics
    # ------------------------------------------------------------------

    @property
    def active_count(self) -> int:
        """Number of currently active (non-blacklisted) proxies.

        Proxies whose cooldown has elapsed are counted as active.
        """
        with self._lock:
            count = 0
            for info in self._proxies.values():
                if self._is_active_unlocked(info):
                    count += 1
            return count

    @property
    def total_count(self) -> int:
        """Total number of proxies in the pool (including blacklisted ones)."""
        with self._lock:
            return len(self._proxies)

    def get_stats(self) -> Dict[str, Any]:
        """Return a snapshot of pool statistics.

        The returned dict contains:

        - ``total``: total proxies in the pool
        - ``active``: currently usable proxies
        - ``blacklisted``: currently blacklisted proxies
        - ``strategy``: the selection strategy in use
        - ``max_failures``: failure threshold for blacklisting
        - ``cooldown``: cooldown period in seconds
        - ``proxies``: per-proxy breakdown (failures, success_count,
          total_count, blacklisted)
        """
        with self._lock:
            active = 0
            blacklisted = 0
            proxy_details: Dict[str, Dict[str, Any]] = {}
            for url, info in self._proxies.items():
                is_active = self._is_active_unlocked(info)
                if is_active:
                    active += 1
                else:
                    blacklisted += 1
                proxy_details[url] = {
                    "failures": info["failures"],
                    "success_count": info["success_count"],
                    "total_count": info["total_count"],
                    "blacklisted": not is_active,
                }
            return {
                "total": len(self._proxies),
                "active": active,
                "blacklisted": blacklisted,
                "strategy": self._strategy,
                "max_failures": self._max_failures,
                "cooldown": self._cooldown,
                "proxies": proxy_details,
            }

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"ProxyPool(total={self.total_count}, active={self.active_count}, "
            f"strategy={self._strategy!r})"
        )

    def __len__(self) -> int:
        return self.total_count

    def __contains__(self, proxy_url: str) -> bool:
        with self._lock:
            return proxy_url in self._proxies


def create_proxy_pool(extra_proxies: Optional[List[str]] = None) -> ProxyPool:
    """Factory: create a :class:`ProxyPool`, loading from env/file if available.

    Loading order:

    1. Proxies from the ``PROXY_LIST`` environment variable.
    2. Proxies from ``proxy_pool.json`` (if the file exists).
    3. Any *extra_proxies* passed programmatically.

    The pool is returned regardless of how many (or few) proxies were
    loaded -- it may even be empty, in which case :meth:`ProxyPool.get_proxy`
    will gracefully return ``None``.
    """
    pool = ProxyPool()
    pool.load_from_env()
    pool.load_from_file()
    if extra_proxies:
        for url in extra_proxies:
            pool.add_proxy(url)
    return pool


# ============================================================
# Favicon fetching — 直接从网站获取 favicon，替代 Google 服务
# ============================================================

import urllib.request as _urllib_request
import urllib.error as _urllib_error

_ICONS_DIR = "public/icons"
_favicon_cache: Dict[str, str] = {}

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _download_to_file(url: str, filepath: str, timeout: int = 8) -> bool:
    """Download *url* to *filepath*. Returns True on success."""
    try:
        req = _urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) < 50:  # too small, probably an error page
                return False
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(data)
            return True
    except Exception:
        return False


def _try_fetch_favicon_from_html(site_url: str, filepath: str) -> bool:
    """Fetch the site homepage, parse <link rel="icon">, and download the favicon."""
    try:
        req = _urllib_request.Request(site_url, headers={"User-Agent": _USER_AGENT})
        with _urllib_request.urlopen(req, timeout=10) as resp:
            html = resp.read(32768).decode("utf-8", errors="ignore")

        # Parse link tags for icon references
        for m in re.finditer(
            r'<link[^>]+rel=["\'](?:shortcut icon|icon|apple-touch-icon)["\'][^>]*>',
            html, re.IGNORECASE,
        ):
            tag = m.group(0)
            href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
            if href_m:
                icon_href = href_m.group(1).strip()
                if not icon_href or icon_href.startswith("data:"):
                    continue
                # Resolve relative URLs
                parsed_site = urlparse(site_url)
                if icon_href.startswith("//"):
                    icon_url = parsed_site.scheme + ":" + icon_href
                elif icon_href.startswith("/"):
                    icon_url = f"{parsed_site.scheme}://{parsed_site.hostname}{icon_href}"
                elif icon_href.startswith("http"):
                    icon_url = icon_href
                else:
                    icon_url = f"{parsed_site.scheme}://{parsed_site.hostname}/{icon_href}"
                if _download_to_file(icon_url, filepath):
                    return True

        # Fallback: try /favicon.ico
        parsed = urlparse(site_url)
        ico_url = f"{parsed.scheme}://{parsed.hostname}/favicon.ico"
        return _download_to_file(ico_url, filepath)
    except Exception:
        return False


def fetch_site_favicon(site_url: str, site_name: str) -> str:
    """获取网站真实 favicon 并缓存到 public/icons/。

    尝试顺序：
      1. 本地缓存（已存在则直接返回）
      2. 从网站 HTML 解析 <link rel="icon">
      3. 网站 /favicon.ico
      4. DuckDuckGo 图标服务（备选）
      5. Icon.horse 服务（备选）
      6. 生成占位 SVG（兜底）

    Returns:
        favicon 的 URL（用于 feeds_meta.json 和 Atom feed 的 <icon>）
    """
    if site_name in _favicon_cache:
        return _favicon_cache[site_name]

    os.makedirs(_ICONS_DIR, exist_ok=True)
    safe_name = slugify(site_name)
    filepath = os.path.join(_ICONS_DIR, f"{safe_name}.png")
    icon_url = SITE_URL_BASE + f"icons/{safe_name}.png"

    # 1) 本地已缓存
    if os.path.exists(filepath) and os.path.getsize(filepath) > 50:
        _favicon_cache[site_name] = icon_url
        return icon_url

    # 2) 从网站 HTML 解析 + /favicon.ico
    if _try_fetch_favicon_from_html(site_url, filepath):
        _favicon_cache[site_name] = icon_url
        return icon_url

    # 3) DuckDuckGo 图标服务（不需要 API key，国内可访问）
    domain = urlparse(site_url).hostname or ""
    if _download_to_file(
        f"https://icons.duckduckgo.com/ip3/{domain}.ico", filepath
    ):
        _favicon_cache[site_name] = icon_url
        return icon_url

    # 4) Icon.horse
    if _download_to_file(
        f"https://icon.horse/icon/{domain}", filepath
    ):
        _favicon_cache[site_name] = icon_url
        return icon_url

    # 5) 兜底：生成带首字母的 SVG 占位图
    import html as _html
    raw_letter = site_name[0] if site_name else "?"
    letter = _html.escape(raw_letter)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        f'<rect width="64" height="64" rx="12" fill="#e91e8e"/>'
        f'<text x="32" y="44" font-size="32" fill="white" text-anchor="middle" '
        f'font-family="sans-serif">{letter}</text></svg>'
    )
    svg_path = filepath.replace(".png", ".svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg)
    fallback_url = SITE_URL_BASE + f"icons/{safe_name}.svg"
    _favicon_cache[site_name] = fallback_url
    return fallback_url


# 基础 URL，供 favicon 和 summary 使用
SITE_URL_BASE = os.environ.get("SITE_URL_BASE", "https://gitfox-enter.github.io/RSSForge/")


# ============================================================
# Article summary extraction — 提取文章摘要用于 RSS 正文
# ============================================================

def fetch_article_summary(url: str, timeout: int = 8) -> Dict[str, str]:
    """抓取文章页面并提取摘要文本和图片。

    Returns:
        dict with keys: 'summary' (text), 'image' (first image URL or '')
    """
    result = {"summary": "", "image": ""}
    try:
        req = _urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(65536)
            html = raw.decode("utf-8", errors="ignore")

        # 尝试 <meta name="description">
        desc_m = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if desc_m:
            result["summary"] = desc_m.group(1).strip()[:500]

        # 尝试 <meta property="og:description">
        if not result["summary"]:
            og_m = re.search(
                r'<meta\s+(?:property|name)=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
                html, re.IGNORECASE,
            )
            if og_m:
                result["summary"] = og_m.group(1).strip()[:500]

        # 如果没有 meta description，提取第一段正文
        if not result["summary"]:
            # 移除 script/style 标签
            clean = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
            paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', clean, re.DOTALL | re.IGNORECASE)
            for p in paragraphs:
                text = re.sub(r'<[^>]+>', '', p).strip()
                if len(text) > 30:
                    result["summary"] = text[:500]
                    break

        # 提取第一张图片
        img_m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if img_m:
            img_url = img_m.group(1)
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                parsed = urlparse(url)
                img_url = f"{parsed.scheme}://{parsed.hostname}{img_url}"
            result["image"] = img_url

    except Exception:
        pass
    return result
