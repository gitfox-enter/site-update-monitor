#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Slim entry point — all logic moved to crawler/ package.

Re-exports all public symbols from the crawler package so that
``import crawl; crawl.get_beijing_time()`` works (backward compatibility
with tests and legacy scripts).
"""

import sys

# Re-export everything from the crawler package and its submodules
from crawler import *  # noqa: F401,F403
from crawler.config import *  # noqa: F401,F403
from crawler.network import *  # noqa: F401,F403
from crawler.storage import *  # noqa: F401,F403
from crawler.parsers import *  # noqa: F401,F403
from crawler.parsers import _match_parser  # private name not exported by *

# Import the main function from the crawler engine
from crawler.engine import main

# Lazy-load engine functions and anything not yet defined
_engine_names = {
    'main', 'check_site_update', 'git_commit_if_changed',
    'load_run_log', 'append_run_log', 'analyze_and_fix',
    'load_paused_sites', 'save_paused_sites',
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
