# -*- coding: utf-8 -*-
"""Crawler package — modular refactor of crawl.py.

Re-exports commonly used symbols for backward compatibility.
"""

# --- Config ---
from crawler.config import (
    MONITOR_SITES,
    SOURCE_NAME_MAP,
    HASH_RECORD_FILE,
    NOTIFIED_ITEMS_FILE,
    RUN_LOG_FILE,
    FAILED_SITES_FILE,
    ADAPTIVE_TIERS_FILE,
    MAX_ITEMS_DB,
    TIER_PROMOTE_SUCCESS_STREAK,
    TIER_DEMOTE_FAIL_STREAK,
    TIER_PROMOTE_ON_UPDATE,
    MAX_RETRIES,
    RETRY_BASE_DELAY,
    REQUEST_TIMEOUT,
    REQUEST_DELAY_MIN,
    REQUEST_DELAY_MAX,
    JS_RENDER_SITES,
    RESPECT_ROBOTS_TXT,
    BROWSER_PROFILES,
    DEAD_SITES,
    is_dead_site,
    get_source_name,
    get_site_tier,
    init_adaptive_tiers,
    update_adaptive_tier,
    save_adaptive_tiers,
    get_all_adaptive_tiers,
)

# --- Network ---
from crawler.network import (
    MetricsTracker,
    metrics,
)

# --- Storage ---
from crawler.storage import (
    load_hash_records,
    save_hash_records,
    load_notified_items,
    save_notified_items,
    filter_new_items,
    merge_items_into_db,
    get_current_round,
)

# --- Parsers ---
from crawler.parsers import (
    PARSER_REGISTRY,
    parse_423down_items,
    parse_discuz_items,
    parse_rss_feed,
    extract_article_items,
    parse_baicaio_items_v2,
)

# --- Common re-exports ---
from common import (
    ITEMS_DB_FILE,
    ITEMS_LATEST_FILE,
    CRAWL_STATUS_FILE,
    BLACKLIST_FILE,
    build_source_name_index,
    get_beijing_time,
    calculate_md5,
    auto_categorize,
    is_blacklisted,
    is_junk,
    ProxyPool,
    create_proxy_pool,
    DomainRateLimiter,
    sanitize_text,
    sanitize_href,
    upgrade_to_https,
)

# --- Engine (lazy) ---
def __getattr__(name):
    _engine_names = {
        'main', 'check_site_update', 'git_commit_if_changed',
        'load_run_log', 'append_run_log', 'analyze_and_fix',
        'export_crawl_status',
        '_handle_signal', '_needs_playwright',
    }
    if name in _engine_names:
        from crawler import engine as _engine
        val = getattr(_engine, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'crawler' has no attribute {name!r}")
