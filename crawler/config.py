# -*- coding: utf-8 -*-
"""Crawler configuration: sites, paths, retry config, browser profiles, dead sites."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
from common import build_source_name_index, ProxyPool

# ============================================================
# 配置区域
# ============================================================

# 47个监控站点（新增：薅羊毛/我不找/反斗限免/佛系软件/多多软件/华军软件/异次元RSS）
# 47个监控站点 — loaded from sites.yaml for no-code management
def _load_sites_from_yaml() -> List[str]:
    """Load site URLs from sites.yaml (falls back to hardcoded list if YAML missing)."""
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sites.yaml")
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return [s["url"] for s in cfg.get("sites", [])]
    except Exception:
        return [  # fallback
            "https://axutongxue.net/", "http://news.ixbk.net/",
            "https://907k.cn/", "https://b1.ymxianbao.cn/",
            "https://cjx8.com/", "https://m.hybase.com/", "https://news.ixbk.fun/",
            "https://www.007ymd.com/", "https://www.12345pro.com/", "https://www.423down.com/",
            "https://www.appinn.com/", "https://www.bacaoo.com/", "https://www.baicaio.com/",
            "https://www.daydayzhuan.com/", "https://www.h6room.com/", "https://www.huifabu.cn/",
            "https://www.huodong5.com/", "https://www.ithome.com/zt/xijiayi",
            "https://www.kxdao.net/forum-42-1.html", "https://www.lsapk.com/",
            "https://www.manmanbuy.com/", "https://www.thosefree.com/", "https://www.wycad.com/",
            "https://www.yangmaodang.club/", "https://www.yxssp.com/",
            "https://www.zhuanyes.com/xianbao/", "https://www.ziyuanting.com/",
            "https://xianbao.icu/", "https://xianbaomi.com/", "https://xzba.cc/",
            "https://yangmao.wang/", "https://www.ghxi.com/", "https://www.iqnew.com/",
            "https://www.51kanong.com/", "https://v1.xianbao.net/",
            "https://www.douban.com/group/711811/", "https://www.haodanku.com/",
            "https://www.ym2.cc/", "https://www.wobangzhao.com/", "https://free.apprcn.com/",
            "https://www.foxirj.com/", "https://www.ddooo.com/", "https://www.onlinedown.net/",
            "https://feed.iplaysoft.com/",
            "https://10000yun.com/",
        ]

MONITOR_SITES: List[str] = _load_sites_from_yaml()


def _load_site_tiers() -> Dict[str, str]:
    """Load site tier from sites.yaml. Returns {url: tier} dict. Default tier is 'high'."""
    yaml_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sites.yaml")
    tiers: Dict[str, str] = {}
    try:
        import yaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        for s in cfg.get("sites", []):
            tiers[s["url"]] = s.get("tier", "high")
    except Exception:
        pass  # all default to 'high' via get_site_tier()
    return tiers


SITE_TIERS: Dict[str, str] = _load_site_tiers()

# 自适应 tier 记录（运行时由 engine 更新，优先于 sites.yaml 中的静态 tier）
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
        import logging
        logging.getLogger('crawl').warning("自适应 tier 保存失败: %s", e)


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
    Returns 'high', 'medium', or 'low'. Default: 'high'."""
    adaptive = get_adaptive_tier(url)
    if adaptive:
        return adaptive
    return SITE_TIERS.get(url, 'high')


def update_adaptive_tier(url: str, status: str, has_new_items: bool = False) -> Optional[str]:
    """根据爬取结果更新站点的自适应 tier。

    Args:
        url: 站点 URL
        status: 'ok' 或 'fail'
        has_new_items: 是否有新内容产出

    Returns:
        更新后的 tier，或 None（无变化时）
    """
    import json as _json  # avoid shadowing
    _TIER_ORDER = {'high': 2, 'medium': 1, 'low': 0}
    _TIER_NAMES = {2: 'high', 1: 'medium', 0: 'low'}
    current_tier = get_site_tier(url)  # 已包含自适应优先逻辑
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

        # 有新内容产出 → 立刻升一级
        if has_new_items and TIER_PROMOTE_ON_UPDATE:
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

        # 连续失败 → 降一级（不会暂停，最多降到 low）
        if entry['fail_streak'] >= TIER_DEMOTE_FAIL_STREAK:
            level = _TIER_ORDER.get(current_tier, 2)
            if level > 0:
                new_tier = _TIER_NAMES[level - 1]
                changed = True

    # tier 变更后重置连续计数，避免连续跳级太快
    if changed and new_tier != current_tier:
        entry['tier'] = new_tier
        entry['success_streak'] = 0
        entry['fail_streak'] = 0
        entry['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    _ADAPTIVE_TIERS[url] = entry

    if changed:
        reason = '有新内容' if has_new_items else ('连续成功' if status == 'ok' else '连续失败')
        logger_adapt = logging.getLogger('crawl')
        logger_adapt.info("自适应 tier 变更: %s %s → %s (%s)", url, current_tier, new_tier, reason)

    return new_tier if changed else None


# URL -> 短名称映射（统一来源显示名称，避免使用页面标题导致名称过长/重复）
SOURCE_NAME_MAP: Dict[str, str] = {
    "https://axutongxue.net/": "爱Q生活",
    "http://news.ixbk.net/": "线报酷",
    "https://907k.cn/": "907线报",
    "https://b1.ymxianbao.cn/": "羊毛线报",
    "https://cjx8.com/": "超级线报",
    "https://m.hybase.com/": "好赚网",
    "https://news.ixbk.fun/": "线报酷",
    "https://www.007ymd.com/": "007羊毛党",
    "https://www.12345pro.com/": "12345线报",
    "https://www.423down.com/": "423Down",
    "https://www.appinn.com/": "小众软件",
    "https://www.bacaoo.com/": "拔草哦",
    "https://www.baicaio.com/": "白菜哦",
    "https://www.daydayzhuan.com/": "天天赚",
    "https://www.h6room.com/": "H6线报",
    "https://www.huifabu.cn/": "汇发部",
    "https://www.huodong5.com/": "活动5",
    "https://www.ithome.com/zt/xijiayi": "IT之家",
    "https://www.kxdao.net/forum-42-1.html": "开心赚",
    "https://www.lsapk.com/": "LSapk",
    "https://www.manmanbuy.com/": "慢慢买",
    "https://www.thosefree.com/": "免费族",
    "https://www.wycad.com/": "网赚",
    "https://www.yangmaodang.club/": "羊毛党",
    "https://www.yxssp.com/": "优惠线报",
    "https://www.zhuanyes.com/xianbao/": "专业线报",
    "https://www.ziyuanting.com/": "资源厅",
    "https://xianbao.icu/": "线报ICU",
    "https://xianbaomi.com/": "线报迷",
    "https://xzba.cc/": "新赚吧",
    "https://yangmao.wang/": "羊毛王",
    "https://www.iqnew.com/": "爱Q社区",
    "https://www.51kanong.com/": "51卡农",
    "https://v1.xianbao.net/": "线报网",
    "https://www.douban.com/group/711811/": "豆瓣小组",
    "https://www.haodanku.com/": "好单库",
    "https://www.ghxi.com/": "果核剥壳",
    "https://www.ym2.cc/": "薅羊毛",
    "https://www.wobangzhao.com/": "我不找",
    "https://free.apprcn.com/": "反斗限免",
    "https://www.foxirj.com/": "佛系软件",
    "https://www.ddooo.com/": "多多软件",
    "https://www.onlinedown.net/": "华军软件",
    "https://feed.iplaysoft.com/": "异次元RSS",
    "https://10000yun.com/": "万云积分",
}
# 已移除的死站映射 (2026-06-12):
# "http://79tao.linejia.com/": "79淘"
# "http://www.0818tuan.com/": "0818团"
# "http://www.xiaodigu.com/": "小嘀咕"


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


# 文件存储配置
HASH_RECORD_FILE = "hash_record.txt"
NOTIFIED_ITEMS_FILE = "notified_items.json"  # 记录已通知过的条目URL，避免重复推送
RUN_LOG_FILE = "run_log.jsonl"  # 每轮运行日志（JSONL格式），用于追踪历史与自检
MAX_ITEMS_DB = 0  # 0 = 无上限，仅按7天时间窗口保留（与 common.py 保持一致）

# 自适应 Tier 策略配置
ADAPTIVE_TIERS_FILE = "adaptive_tiers.json"  # 自适应 tier 记录
TIER_PROMOTE_SUCCESS_STREAK = 2  # 连续成功 N 次后升一级
TIER_DEMOTE_FAIL_STREAK = 2  # 连续失败 N 次后降一级（最多降到 low）
TIER_PROMOTE_ON_UPDATE = True  # 有新内容产出时立即升一级

# 爬虫配置
REQUEST_TIMEOUT = 15  # 单个站点超时时间（秒）
REQUEST_DELAY_MIN = 0.5  # 请求间隔最小值（秒）
REQUEST_DELAY_MAX = 1.5  # 请求间隔最大值（秒）

# 重试配置（指数退避）
MAX_RETRIES = 3  # 最大重试次数
RETRY_BASE_DELAY = 1.0  # 重试基础延迟（秒），实际延迟 = base * 2^attempt

# 需要 Playwright JS 渲染的站点（域名匹配）
# 这些站点通过 aiohttp 获取的 HTML 内容不完整（依赖 JS 加载数据）
JS_RENDER_SITES: Set[str] = {
    'kxdao.net',          # Discuz 论坛，帖子列表需要 JS 渲染
    '51kanong.com',       # 反爬虫 JS 重定向页面（"页面重载开启"）
}

# RSS 优先站点：这些 WordPress 站点的 HTML 主页可能被反爬封禁（403）或加载慢，
# 但 RSS feed（/feed/）端点通常无限制且更轻量。
# 格式：{域名: RSS feed URL}
RSS_FIRST_SITES: Dict[str, str] = {
    'foxirj.com':     'https://www.foxirj.com/feed/',
    'appinn.com':     'https://www.appinn.com/feed/',
    'thosefree.com':  'https://www.thosefree.com/feed/',
}

# robots.txt 合规配置
RESPECT_ROBOTS_TXT: bool = False  # 是否遵守 robots.txt（线报站 robots.txt 通常过严，个人监控工具建议关闭）

# 代理池（初始化后全局可用，None 表示直连模式）
_proxy_pool: Optional[ProxyPool] = None

# ============================================================
# 死站黑名单（经多轮测试确认无法访问的站点）
# 格式: {URL: {'reason': '原因', 'confirmed_at': '确认日期', 'test_result': '测试结果'}}
# ============================================================
DEAD_SITES: Dict[str, Dict[str, str]] = {
    "https://907k.cn/": {
        "reason": "DNS/连接失败",
        "confirmed_at": "2026-06-10",
        "test_result": "HTTP 000 - 无法建立连接，DNS 解析失败或服务器已下线",
    },
    "http://www.xiaodigu.com/": {
        "reason": "服务器 502 错误",
        "confirmed_at": "2026-06-10",
        "test_result": "HTTP 502 Bad Gateway - 上游服务器不可用",
    },
    "https://www.ym2.cc/": {
        "reason": "DNS/连接失败",
        "confirmed_at": "2026-06-10",
        "test_result": "HTTP 000 - 无法建立连接，域名无法解析或服务器已下线",
    },
    "https://79tao.linejia.com/": {
        "reason": "连接失败(Connection refused)",
        "confirmed_at": "2026-06-12",
        "test_result": "Connection refused - 服务器拒绝连接或已下线",
    },
    "https://www.0818tuan.com/": {
        "reason": "连接失败(Connection refused)",
        "confirmed_at": "2026-06-12",
        "test_result": "Connection refused - 服务器拒绝连接或已下线",
    },
    # 以下为 http 协议版本（engine 会通过 upgrade_to_https 自动升级为 https）
    "http://79tao.linejia.com/": {
        "reason": "连接失败(Connection refused)",
        "confirmed_at": "2026-06-12",
        "test_result": "Connection refused - 服务器拒绝连接或已下线",
    },
    "http://www.0818tuan.com/": {
        "reason": "连接失败(Connection refused)",
        "confirmed_at": "2026-06-12",
        "test_result": "Connection refused - 服务器拒绝连接或已下线",
    },
}


def is_dead_site(url: str) -> Optional[str]:
    """检查 URL 是否在死站黑名单中，返回原因或 None。"""
    if url in DEAD_SITES:
        return DEAD_SITES[url].get('reason', '未知原因')
    return None

# 统一浏览器配置文件池（UA + 指纹 + 语言 一一对应，防止 Firefox UA 搭配 Chrome sec-ch-ua 头）
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
        'fingerprint': {},  # Firefox does not send sec-ch-ua headers
        'accept_language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
    {
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'fingerprint': {},  # Firefox does not send sec-ch-ua headers
        'accept_language': 'zh-TW,zh-CN;q=0.9,zh;q=0.8,en;q=0.7',
    },
    {
        'user_agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
        'fingerprint': {},  # Safari does not send sec-ch-ua headers
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


