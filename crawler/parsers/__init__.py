# -*- coding: utf-8 -*-
"""Crawler parsers package — modular refactor of the monolithic parsers.py.

Structure:
  _utils.py         — Shared utilities (_has_chinese, _add_item, etc.)
  deal_sites.py     — 线报/羊毛/比价站解析器 (24 parsers)
  software_sites.py — 软件资源站解析器 (8 parsers)
  forum_sites.py    — 社区论坛解析器 (2 parsers)
  rss_parsers.py    — RSS/Atom feed + special parsers (6 parsers)
  core.py           — PARSER_REGISTRY, _match_parser, fetch_page_content

Backward compatibility:
  All public symbols are re-exported from this package,
  so ``from crawler.parsers import parse_423down_items`` still works.
"""

# --- Utils ---
from crawler.parsers._utils import (
    _has_chinese,
    _is_valid_text,
    _add_item,
    _make_skip_set,
    COMMON_SKIP_WORDS,
)

# --- Deal/Coupon site parsers ---
from crawler.parsers.deal_sites import (
    parse_423down_items,
    parse_ziyuanting_items,
    parse_wycad_items,
    parse_h6room_items,
    parse_xzba_items,
    parse_apprcn_items,
    parse_daydayzhuan_items,
    parse_007ymd_items,
    parse_baicaio_items_v2,
    parse_manmanbuy_items,
    parse_12345pro_items,
    parse_ym2cc_items,
    parse_wobangzhao_items,
    parse_haodanku_items,
    parse_hybase_items,
    parse_huodong5_items,
    parse_yangmaodang_items,
    parse_xianbaomi_items,
    parse_yangmao_wang_items,
    parse_iqnew_items,
    parse_51kanong_items,
    parse_ymxianbao_items,
    parse_linejia_items,
    parse_10000yun_items,
)

# --- Software site parsers ---
from crawler.parsers.software_sites import (
    parse_yxssp_items,
    parse_foxirj_items,
    parse_ddooo_items,
    parse_onlinedown_items,
    parse_appinn_items,
    parse_lsapk_items,
    parse_thosefree_items,
    parse_ithome_xijiayi_items,
)

# --- Forum parsers ---
from crawler.parsers.forum_sites import (
    parse_discuz_items,
    parse_douban_group_items,
)

# --- RSS/Special parsers ---
from crawler.parsers.rss_parsers import (
    parse_rss_feed,
    _ghxi_fetch_sync,
    fetch_ghxi_items_async,
    fetch_rss_feed_async,
    parse_ghxi_items,
    extract_article_items,
)

# --- Core: Registry + Matching + Fetching ---
from crawler.parsers.core import (
    PARSER_REGISTRY,
    _match_parser,
    fetch_page_content,
)
