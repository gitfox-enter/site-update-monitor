#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slim entry point — all logic moved to crawler/ package.

Re-exports all public symbols from the crawler package so that
``import crawl; crawl.get_beijing_time()`` works (backward compatibility
with tests and legacy scripts).
"""

import sys

# Re-export everything from the crawler package and its submodules
from common import *  # noqa: F401,F403
from crawler.config import (
    MONITOR_SITES, SOURCE_NAME_MAP, SITE_INTERVALS,
    get_source_name, get_site_tier, is_dead_site, JS_RENDER_SITES,
    REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BASE_DELAY, BROWSER_PROFILES,
    SOURCE_NAME_MAP as _CFG_SOURCE_NAME_MAP,
)
from crawler.network import (
    MetricsTracker, metrics, rate_limiter,
    is_allowed_by_robots, get_session, get_conditional_headers,
    update_conditional_cache,
)
from crawler.storage import (
    load_items_db, save_items_db, load_blacklist, is_blacklisted,
    get_current_round, load_notified_items, save_notified_items,
    filter_new_items, merge_items_into_db, load_hash_records,
    save_hash_records, export_items_latest_json,
    get_random_delay, get_random_profile, get_referer,
)
from crawler.parsers import (
    _match_parser, extract_article_items, parse_rss_feed,
    parse_ghxi_items, fetch_page_content,
)
from crawler.parsers import _match_parser  # private name not exported by *

# Import the main function from the crawler engine
from crawler.engine import main

# Lazy-load engine functions and anything not yet defined
_engine_names = {
    'main', 'check_site_update', 'git_commit_if_changed',
    'load_run_log', 'append_run_log', 'analyze_and_fix',
    'export_crawl_status',
    '_handle_signal', '_needs_playwright',
    'PLAYWRIGHT_AVAILABLE',
    'fetch_page_content_async', 'fetch_with_playwright',
    'close_playwright', 'check_one_async', 'main_async',
    'check_site_update_async',
}


def __getattr__(name):
    if name in _engine_names:
        from crawler import engine as _engine
        val = getattr(_engine, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module 'crawl' has no attribute {name!r}")


if __name__ == '__main__':
    main()
