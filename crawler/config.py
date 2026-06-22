# -*- coding: utf-8 -*-
"""Crawler configuration: sites, paths, retry config, browser profiles, dead sites.

All site-level configuration (URLs, names, tiers, fast_check, js_render,
rss_feed, dead_sites) is loaded from sites.yaml — the single source of truth.
Hardcoded fallbacks exist only for when YAML is unavailable.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
from common import build_source_name_index, ProxyPool

logger = logging.getLogger('crawl')

# ============================================================
# YAML 配置加载（单一真相源）
# ============================================================

_YAML_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sites.yaml")
_YAML_DATA: Dict[str, Any] = {}


def _load_yaml_config() -> Dict[str, Any]:
    """Load and cache sites.yaml configuration."""
    global _YAML_DATA
    if _YAML_DATA:
        return _YAML_DATA
    try:
        import yaml
        with open(_YAML_PATH, "r", encoding="utf-8") as f:
            _YAML_DATA = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("sites.yaml 加载失败，使用硬编码默认值: %s", e)
        _YAML_DATA = {}
    return _YAML_DATA


def _get_sites_list() -> List[Dict[str, Any]]:
    """Get sites list from YAML config."""
    return _load_yaml_config().get("sites", [])


# ============================================================
# 站点列表（从 sites.yaml 加载）
# ============================================================

def _load_sites_from_yaml() -> List[str]:
    """Load site URLs from sites.yaml (falls back to hardcoded list if YAML missing)."""
    sites = _get_sites_list()
    if sites:
        return [s["url"] for s in sites]
    # Fallback
    return [
        "https://axutongxue.net/", "http://news.ixbk.net/",
        "https://b1.ymxianbao.cn/", "https://cjx8.com/",
        "https://m.hybase.com/", "https://news.ixbk.fun/",
        "https://www.007ymd.com/", "https://www.12345pro.com/",
        "https://www.423down.com/", "https://www.appinn.com/",
        "https://www.bacaoo.com/", "https://www.baicaio.com/",
        "https://www.daydayzhuan.com/", "https://www.h6room.com/",
        "https://www.huifabu.cn/", "https://www.huodong5.com/",
        "https://www.ithome.com/zt/xijiayi", "https://www.kxdao.net/forum-42-1.html",
        "https://www.lsapk.com/", "https://www.manmanbuy.com/",
        "https://www.thosefree.com/", "https://www.wycad.com/",
        "https://www.yangmaodang.club/", "https://www.yxssp.com/",
        "https://www.zhuanyes.com/xianbao/", "https://www.ziyuanting.com/",
        "https://xianbao.icu/", "https://xianbaomi.com/", "https://xzba.cc/",
        "https://yangmao.wang/", "https://www.ghxi.com/", "https://www.iqnew.com/",
        "https://www.51kanong.com/", "https://v1.xianbao.net/",
        "https://www.douban.com/group/711811/",
        "https://www.wobangzhao.com/", "https://free.apprcn.com/",
        "https://www.foxirj.com/", "https://www.ddooo.com/",
        "https://www.onlinedown.net/", "https://feed.iplaysoft.com/",
        "https://10000yun.com/",
    ]


MONITOR_SITES: List[str] = _load_sites_from_yaml()


# ============================================================
# 站点 Tier（从 sites.yaml 加载）
# ============================================================

def _load_site_tiers() -> Dict[str, str]:
    """Load site tier from sites.yaml. Returns {url: tier} dict. Default tier is 'high'."""
    sites = _get_sites_list()
    if not sites:
        return {}
    return {s["url"]: s.get("tier", "high") for s in sites}


SITE_TIERS: Dict[str, str] = _load_site_tiers()


# ============================================================
# 站点抓取间隔（从 sites.yaml interval 字段加载）
# ============================================================

def _load_site_intervals() -> Dict[str, int]:
    """Load per-site crawl interval from sites.yaml. Returns {url: interval_minutes}."""
    sites = _get_sites_list()
    if not sites:
        return {}
    return {s["url"]: s.get("interval", 30) for s in sites}


SITE_INTERVALS: Dict[str, int] = _load_site_intervals()


# ============================================================
# 站点名称（从 sites.yaml 加载，替代 SOURCE_NAME_MAP）
# ============================================================

def _load_source_names() -> Dict[str, str]:
    """Load source names from sites.yaml. Returns {url: name} dict."""
    sites = _get_sites_list()
    names: Dict[str, str] = {}
    if sites:
        for s in sites:
            if "name" in s:
                names[s["url"]] = s["name"]
    # Fallback to legacy SOURCE_NAME_MAP if YAML is empty
    if not names:
        names = _LEGACY_SOURCE_NAME_MAP
    return names


# Legacy source name map (fallback only)
_LEGACY_SOURCE_NAME_MAP: Dict[str, str] = {
    "https://axutongxue.net/": "爱Q生活",
    "http://news.ixbk.net/": "线报酷",
    "https://news.ixbk.fun/": "线报酷",
    "https://xianbao.icu/": "线报ICU",
    "https://www.zhuanyes.com/xianbao/": "专业线报",
    "https://xianbaomi.com/": "线报迷",
    "https://v1.xianbao.net/": "线报网",
    "https://xzba.cc/": "新赚吧",
    "https://www.h6room.com/": "H6线报",
    "https://yangmao.wang/": "羊毛王",
    "https://www.yangmaodang.club/": "羊毛党",
    "https://b1.ymxianbao.cn/": "羊毛线报",
    "https://cjx8.com/": "超级线报",
    "https://www.huifabu.cn/": "汇发部",
    "https://www.iqnew.com/": "爱Q社区",
    "https://www.007ymd.com/": "007羊毛党",
    "https://www.12345pro.com/": "12345线报",
    "https://m.hybase.com/": "好赚网",
    "https://www.huodong5.com/": "活动5",
    "https://www.daydayzhuan.com/": "天天赚",
    "https://www.ziyuanting.com/": "资源厅",
    "https://www.wycad.com/": "网赚",
    "https://www.wobangzhao.com/": "我不找",
    "https://www.manmanbuy.com/": "慢慢买",
    "https://www.baicaio.com/": "白菜哦",
    "https://www.bacaoo.com/": "拔草哦",
    "https://www.yxssp.com/": "优惠线报",
    "https://www.ghxi.com/": "果核剥壳",
    "https://www.423down.com/": "423Down",
    "https://www.appinn.com/": "小众软件",
    "https://www.lsapk.com/": "LSapk",
    "https://www.thosefree.com/": "免费族",
    "https://www.foxirj.com/": "佛系软件",
    "https://www.ddooo.com/": "多多软件",
    "https://www.onlinedown.net/": "华军软件",
    "https://free.apprcn.com/": "反斗限免",
    "https://feed.iplaysoft.com/": "异次元RSS",
    "https://www.douban.com/group/711811/": "豆瓣小组",
    "https://www.kxdao.net/forum-42-1.html": "开心赚",
    "https://www.51kanong.com/": "51卡农",
    "https://www.ithome.com/zt/xijiayi": "IT之家",
    "https://10000yun.com/": "万云积分",
}

SOURCE_NAME_MAP: Dict[str, str] = _load_source_names()

# Build O(1) source name index at module load time
_SOURCE_NAME_INDEX: Dict[str, str] = build_source_name_index(SOURCE_NAME_MAP)


def get_source_name(url: str) -> Optional[str]:
    """根据 URL 获取统一短名称 (O(1) lookup)"""
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    name = _SOURCE_NAME_INDEX.get(host)
    if name:
        return name
    if host.startswith('www.'):
        return _SOURCE_NAME_INDEX.get(host[4:])
    return None


# ============================================================
# 站点最大分页数（从 sites.yaml max_pages 字段加载）
# ============================================================

def _load_site_max_pages() -> Dict[str, int]:
    """Load per-site max pages to crawl for historical backfill. Returns {url: max_pages}."""
    sites = _get_sites_list()
    if not sites:
        return {}
    return {s["url"]: s.get("max_pages", 1) for s in sites}


SITE_MAX_PAGES: Dict[str, int] = _load_site_max_pages()


# ============================================================
# 多分类支持（从 sites.yaml categories 字段加载）
# ============================================================

def _load_site_categories() -> Dict[str, List[Dict[str, str]]]:
    """Load per-site category definitions. Returns {url: [{path, name}]}."""
    sites = _get_sites_list()
    if not sites:
        return {}
    result: Dict[str, List[Dict[str, str]]] = {}
    for s in sites:
        cats = s.get("categories")
        if cats:
            result[s["url"]] = cats
    return result


SITE_CATEGORIES: Dict[str, List[Dict[str, str]]] = _load_site_categories()


def get_site_categories(url: str) -> List[Dict[str, str]]:
    """Get categories for a given site URL. Returns list of {path, name}."""
    return SITE_CATEGORIES.get(url, [])


# ============================================================
# Parser 策略（从 sites.yaml parser 字段加载）
# ============================================================

def _load_site_parsers() -> Dict[str, str]:
    """Load per-site parser strategy from sites.yaml. Returns {url: parser_name}."""
    sites = _get_sites_list()
    if not sites:
        return {}
    result: Dict[str, str] = {}
    for s in sites:
        parser_name = s.get("parser")
        if parser_name:
            result[s["url"]] = parser_name
    return result


SITE_PARSERS: Dict[str, str] = _load_site_parsers()


def get_parser_strategy(url: str) -> Optional[str]:
    """Get the parser strategy for a given site URL from sites.yaml.
    
    Returns the parser name string (e.g. 'ghxi', 'rss', '423down.com'),
    or None if no parser is specified.
    
    The engine can use this to select a different parsing strategy
    instead of relying solely on PARSER_REGISTRY domain matching.
    """
    return SITE_PARSERS.get(url)


def get_category_feed_key(category_url: str) -> str:
    """Generate a unique feed key for a category URL.
    
    E.g. 'https://www.423down.com/apk' -> '423Down-安卓软件'
    Returns empty string if not a category URL.
    """
    from urllib.parse import urlparse
    parsed = urlparse(category_url)
    host = (parsed.hostname or '').lower()
    path = parsed.path.rstrip('/')
    
    # Find which parent site this category belongs to
    parent_url = None
    parent_name = None
    for parent, cats in SITE_CATEGORIES.items():
        parent_parsed = urlparse(parent)
        if parent_parsed.hostname == host:
            for cat in cats:
                cat_path = '/' + cat['path'].lstrip('/')
                if path == cat_path or path == cat_path.rstrip('/'):
                    parent_url = parent
                    parent_name = get_source_name(parent) or parent
                    return f"{parent_name}-{cat['name']}"
    return ""


# ============================================================
# 快速检查站点（从 sites.yaml fast_check 字段加载）
# ============================================================

def _load_fast_check_sites() -> List[Dict[str, str]]:
    """Load fast check sites from sites.yaml (sites with fast_check: true)."""
    sites = _get_sites_list()
    fast_sites = []
    for s in sites:
        if s.get("fast_check"):
            name = s.get("name", urlparse(s["url"]).hostname or s["url"])
            fast_sites.append({"url": s["url"], "name": name})
    if not fast_sites:
        # Fallback to legacy hardcoded list
        fast_sites = [
            {"url": "https://www.zhuanyes.com/xianbao/", "name": "专业线报"},
            {"url": "https://news.ixbk.net/", "name": "线报酷"},
            {"url": "https://news.ixbk.fun/", "name": "线报酷"},
            {"url": "https://www.huifabu.cn/", "name": "汇发部"},
            {"url": "https://cjx8.com/", "name": "超级线报"},
            {"url": "https://xianbao.icu/", "name": "线报ICU"},
            {"url": "https://www.baicaio.com/", "name": "白菜哦"},
            {"url": "https://www.iqnew.com/", "name": "爱Q社区"},
            {"url": "https://www.51kanong.com/", "name": "51卡农"},
            {"url": "https://v1.xianbao.net/", "name": "线报网"},
        ]
    return fast_sites


FAST_SITES: List[Dict[str, str]] = _load_fast_check_sites()


# ============================================================
# 死站黑名单（从 sites.yaml dead_sites 加载）
# ============================================================

def _load_dead_sites() -> Dict[str, Dict[str, str]]:
    """Load dead sites from sites.yaml."""
    data = _load_yaml_config()
    dead = data.get("dead_sites", {})
    if dead:
        return dead
    # Fallback to legacy hardcoded list
    return {
        "https://907k.cn/": {
            "reason": "DNS/连接失败",
            "confirmed_at": "2026-06-10",
            "test_result": "HTTP 000 - 无法建立连接",
        },
        "http://www.xiaodigu.com/": {
            "reason": "服务器 502 错误",
            "confirmed_at": "2026-06-10",
            "test_result": "HTTP 502 Bad Gateway",
        },
        "https://www.ym2.cc/": {
            "reason": "DNS/连接失败",
            "confirmed_at": "2026-06-10",
            "test_result": "HTTP 000 - 域名无法解析",
        },
        "https://79tao.linejia.com/": {
            "reason": "连接失败(Connection refused)",
            "confirmed_at": "2026-06-12",
            "test_result": "Connection refused",
        },
        "https://www.0818tuan.com/": {
            "reason": "连接失败(Connection refused)",
            "confirmed_at": "2026-06-12",
            "test_result": "Connection refused",
        },
        "http://79tao.linejia.com/": {
            "reason": "连接失败(Connection refused)",
            "confirmed_at": "2026-06-12",
            "test_result": "Connection refused",
        },
        "http://www.0818tuan.com/": {
            "reason": "连接失败(Connection refused)",
            "confirmed_at": "2026-06-12",
            "test_result": "Connection refused",
        },
    }


DEAD_SITES: Dict[str, Dict[str, str]] = _load_dead_sites()


def is_dead_site(url: str) -> Optional[str]:
    """检查 URL 是否在死站黑名单中，返回原因或 None。"""
    if url in DEAD_SITES:
        return DEAD_SITES[url].get('reason', '未知原因')
    return None


# ============================================================
# JS 渲染站点（从 sites.yaml js_render 字段加载）
# ============================================================

def _load_js_render_sites() -> Set[str]:
    """Load JS-rendered site domains from sites.yaml.
    
    Strips www. prefix so subdomain matching works correctly.
    """
    sites = _get_sites_list()
    domains: Set[str] = set()
    for s in sites:
        if s.get("js_render"):
            parsed = urlparse(s["url"])
            domain = parsed.hostname or ''
            # Strip www. prefix for subdomain matching
            if domain.startswith('www.'):
                domain = domain[4:]
            if domain:
                domains.add(domain)
    if not domains:
        domains = {'kxdao.net', '51kanong.com'}
    return domains


JS_RENDER_SITES: Set[str] = _load_js_render_sites()


# ============================================================
# RSS 优先站点（从 sites.yaml rss_feed 字段加载）
# ============================================================

def _load_rss_first_sites() -> Dict[str, str]:
    """Load RSS-first site mapping from sites.yaml."""
    sites = _get_sites_list()
    rss_map: Dict[str, str] = {}
    for s in sites:
        rss_url = s.get("rss_feed")
        if rss_url:
            parsed = urlparse(s["url"])
            domain = parsed.hostname or ''
            if domain:
                rss_map[domain] = rss_url
    if not rss_map:
        # Fallback
        rss_map = {
            'foxirj.com': 'https://www.foxirj.com/feed/',
            'appinn.com': 'https://www.appinn.com/feed/',
            'thosefree.com': 'https://www.thosefree.com/feed/',
        }
    return rss_map


RSS_FIRST_SITES: Dict[str, str] = _load_rss_first_sites()


# ============================================================
# 自适应 Tier 系统
# ============================================================

_ADAPTIVE_TIERS: Dict[str, dict] = {}


def load_adaptive_tiers() -> Dict[str, dict]:
    """从 adaptive_tiers.json 加载自适应 tier 记录。"""
    if os.path.exists(ADAPTIVE_TIERS_FILE):
        try:
            with open(ADAPTIVE_TIERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_adaptive_tiers(tiers: Dict[str, dict]) -> None:
    """保存自适应 tier 记录（原子写入）。"""
    tmp_file = ADAPTIVE_TIERS_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(tiers, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, ADAPTIVE_TIERS_FILE)
    except Exception as e:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        logger.warning("自适应 tier 保存失败: %s", e)


def init_adaptive_tiers() -> None:
    """初始化自适应 tier，从文件加载。应在爬取开始前调用。"""
    global _ADAPTIVE_TIERS
    _ADAPTIVE_TIERS = load_adaptive_tiers()


def get_adaptive_tier(url: str) -> Optional[str]:
    """获取站点的自适应 tier，未找到返回 None。"""
    return _ADAPTIVE_TIERS.get(url, {}).get('tier')


def get_all_adaptive_tiers() -> Dict[str, dict]:
    """获取所有自适应 tier 记录（用于保存）。"""
    return _ADAPTIVE_TIERS


def get_site_tier(url: str) -> str:
    """Get the crawl tier for a site. 自适应 tier 优先于 sites.yaml 静态配置。
    Returns 'high', 'medium', 'low', or 'dead'. Default: 'high'."""
    adaptive = get_adaptive_tier(url)
    if adaptive:
        return adaptive
    return SITE_TIERS.get(url, 'high')


def update_adaptive_tier(url: str, status: str, has_new_items: bool = False) -> Optional[str]:
    """根据爬取结果更新站点的自适应 tier。

    Args:
        url: 站点 URL
        status: 'ok', 'fail', 或 'dead'
        has_new_items: 是否有新内容产出

    Returns:
        更新后的 tier，或 None（无变化时）
    """
    _TIER_ORDER = {'high': 2, 'medium': 1, 'low': 0, 'dead': -1}
    _TIER_NAMES = {2: 'high', 1: 'medium', 0: 'low', -1: 'dead'}
    current_tier = get_site_tier(url)
    entry = _ADAPTIVE_TIERS.get(url, {
        'tier': current_tier,
        'success_streak': 0,
        'fail_streak': 0,
    })

    changed = False
    new_tier = current_tier

    if status == 'ok':
        entry['success_streak'] = entry.get('success_streak', 0) + 1
        entry['fail_streak'] = 0

        # 死站恢复：如果当前是 dead tier 且成功，恢复到 low
        if current_tier == 'dead':
            new_tier = 'low'
            changed = True

        # 有新内容产出 → 立刻升一级
        elif has_new_items and TIER_PROMOTE_ON_UPDATE:
            level = _TIER_ORDER.get(current_tier, 2)
            if level < 2:
                new_tier = _TIER_NAMES[level + 1]
                changed = True

        # 连续成功 → 升一级
        elif entry['success_streak'] >= TIER_PROMOTE_SUCCESS_STREAK:
            level = _TIER_ORDER.get(current_tier, 2)
            if level < 2:
                new_tier = _TIER_NAMES[level + 1]
                changed = True

    elif status == 'fail':
        entry['fail_streak'] = entry.get('fail_streak', 0) + 1
        entry['success_streak'] = 0

        # 连续失败 → 降一级
        if entry['fail_streak'] >= TIER_DEMOTE_FAIL_STREAK:
            level = _TIER_ORDER.get(current_tier, 2)
            if level > 0:
                new_tier = _TIER_NAMES[level - 1]
                changed = True
            elif level == 0:
                # low tier 连续失败 → 标记为 dead
                new_tier = 'dead'
                changed = True

    elif status == 'dead':
        # 外部确认的死站标记
        if current_tier != 'dead':
            new_tier = 'dead'
            changed = True

    # tier 变更后重置连续计数，避免连续跳级太快
    if changed and new_tier != current_tier:
        entry['tier'] = new_tier
        entry['success_streak'] = 0
        entry['fail_streak'] = 0
        entry['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    _ADAPTIVE_TIERS[url] = entry

    if changed:
        reason = '有新内容' if has_new_items else ('连续成功' if status == 'ok' else ('确认死站' if status == 'dead' else '连续失败'))
        logger.info("自适应 tier 变更: %s %s → %s (%s)", url, current_tier, new_tier, reason)

    return new_tier if changed else None


# ============================================================
# 文件存储配置
# ============================================================

HASH_RECORD_FILE = "hash_record.txt"
NOTIFIED_ITEMS_FILE = "notified_items.json"
RUN_LOG_FILE = "run_log.jsonl"
MAX_ITEMS_DB = 0  # 0 = 无上限，仅按7天时间窗口保留

# ============================================================
# 自适应 Tier 策略配置
# ============================================================

ADAPTIVE_TIERS_FILE = "adaptive_tiers.json"
TIER_PROMOTE_SUCCESS_STREAK = 2  # 连续成功 N 次后升一级
TIER_DEMOTE_FAIL_STREAK = 2  # 连续失败 N 次后降一级
TIER_PROMOTE_ON_UPDATE = True  # 有新内容产出时立即升一级
DEAD_THRESHOLD_FAIL_STREAK = 5  # low tier 连续失败 N 次后标记为 dead

# ============================================================
# 爬虫配置
# ============================================================

REQUEST_TIMEOUT = 15  # 单个站点超时时间（秒）
REQUEST_DELAY_MIN = 0.5  # 请求间隔最小值（秒）
REQUEST_DELAY_MAX = 1.5  # 请求间隔最大值（秒）

# 重试配置（指数退避）
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0

# robots.txt 合规配置
RESPECT_ROBOTS_TXT: bool = True  # fix #18: 默认遵守 robots.txt

# 代理池
_proxy_pool: Optional[ProxyPool] = None

# ============================================================
# 统一浏览器配置文件池
# ============================================================

BROWSER_PROFILES: List[Dict[str, Any]] = [
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'fingerprint': {
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        },
        'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'fingerprint': {
            'sec-ch-ua': '"Not/A_Brand";v="8", "Chromium";v="125", "Google Chrome";v="125"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        },
        'accept_language': 'zh-CN,zh;q=0.9',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'fingerprint': {
            'sec-ch-ua': '"Not A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
        },
        'accept_language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'fingerprint': {},
        'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'fingerprint': {},
        'accept_language': 'zh-TW,zh-CN;q=0.9,zh;q=0.8,en;q=0.7',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        'fingerprint': {},
        'accept_language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    },
    {
        'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'fingerprint': {
            'sec-ch-ua': '"Chromium";v="120", "Not A Brand";v="24", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Linux"',
        },
        'accept_language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/120.0.0.0 Safari/537.36',
        'fingerprint': {
            'sec-ch-ua': '"Not A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        },
        'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
]
