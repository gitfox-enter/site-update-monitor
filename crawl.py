#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 多站点更新监控系统
功能：爬取47个站点 -> MD5比对检测更新 -> 数据持久化到 items.json
时间：每小时执行一次
时区：Asia/Shanghai（北京时间）
"""

# ============================================================
# 顶层导入（所有模块统一在此导入，禁止函数内散落导入）
# ============================================================
import os
import sys
import re
import time
import random
import hashlib
import html as html_mod
import json
import logging
import subprocess
import threading
import warnings
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Playwright: optional dependency for JS-rendered sites
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore

from common import (
    JsonFormatter, get_beijing_time, auto_categorize, CATEGORY_KEYWORDS,
    load_items_db, save_items_db, load_blacklist, is_blacklisted,
    build_source_name_index, get_source_name as _get_source_name_by_index,
    calculate_md5, upgrade_to_https, DomainRateLimiter, sanitize_href,
    sanitize_text, is_junk, ITEMS_DB_FILE, BLACKLIST_FILE,
    init_sqlite, sqlite_insert_items, sqlite_get_recent_items,
    sqlite_get_existing_urls, sqlite_export_json, sqlite_load_hash_records,
    sqlite_save_hash_records, SQLITE_DB_FILE, MAX_ITEMS_DB,
    ProxyPool, create_proxy_pool,
)

# 忽略 BeautifulSoup 的 XML 当 HTML 解析警告
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


# ============================================================
# 结构化 JSON 日志配置
# ============================================================

logger = logging.getLogger('crawl')
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)


# ============================================================
# 配置区域
# ============================================================

# 47个监控站点（新增：薅羊毛/我不找/反斗限免/佛系软件/多多软件/华军软件/异次元RSS）
MONITOR_SITES: List[str] = [
    "https://axutongxue.net/",
    "http://79tao.linejia.com/",
    "http://news.ixbk.net/",
    "http://www.0818tuan.com/",
    "https://907k.cn/",
    "https://b1.ymxianbao.cn/",
    "https://cjx8.com/",
    "https://m.hybase.com/",
    "https://news.ixbk.fun/",
    "https://www.007ymd.com/",
    "https://www.12345pro.com/",
    "https://www.423down.com/",
    "https://www.appinn.com/",
    "https://www.bacaoo.com/",
    "https://www.baicaio.com/",
    "https://www.daydayzhuan.com/",
    "https://www.h6room.com/",
    "https://www.huifabu.cn/",
    "https://www.huodong5.com/",
    "https://www.ithome.com/zt/xijiayi",
    "https://www.kxdao.net/forum-42-1.html",
    "https://www.lsapk.com/",
    "https://www.manmanbuy.com/",
    "https://www.thosefree.com/",
    "https://www.wycad.com/",
    "https://www.yangmaodang.club/",
    "https://www.yxssp.com/",
    "https://www.zhuanyes.com/xianbao/",
    "https://www.ziyuanting.com/",
    "https://xianbao.icu/",
    "https://xianbaomi.com/",
    "https://xzba.cc/",
    "https://yangmao.wang/",
    # === 果核剥壳 ===
    "https://www.ghxi.com/",
    # === 新增源站（来自 huifabu.cn 参考） ===
    "https://www.iqnew.com/",
    "https://www.51kanong.com/",
    "https://v1.xianbao.net/",
    "http://www.xiaodigu.com/",
    "https://www.douban.com/group/711811/",
    "https://www.haodanku.com/",
    # === 新增源站（用户补充） ===
    "https://www.ym2.cc/",
    "https://www.wobangzhao.com/",
    "https://free.apprcn.com/",
    "https://www.foxirj.com/",
    "https://www.ddooo.com/",
    "https://www.onlinedown.net/",
    "https://feed.iplaysoft.com/",
]

# URL -> 短名称映射（统一来源显示名称，避免使用页面标题导致名称过长/重复）
SOURCE_NAME_MAP: Dict[str, str] = {
    "https://axutongxue.net/": "爱Q生活",
    "http://79tao.linejia.com/": "79淘",
    "http://news.ixbk.net/": "线报酷",
    "http://www.0818tuan.com/": "0818团",
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
    "http://www.xiaodigu.com/": "小嘀咕",
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
}


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
FAILED_SITES_FILE = "failed_sites.json"  # 连续失败站点记录，自动建议移除
PAUSED_SITES_FILE = "paused_sites.json"  # 因连续失败被暂停的站点

# 自动移除/恢复配置
MAX_CONSECUTIVE_FAILURES = 3  # 连续失败 N 轮后自动暂停
RECOVERY_CHECK_INTERVAL = 6  # 每 N 轮尝试恢复一次暂停站点
MAX_ITEMS_DB = 2000  # items.json 最多保留条目数（与 common.py 保持一致）

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


# ============================================================
# 运行指标追踪
# ============================================================

class MetricsTracker:
    """
    追踪爬虫运行指标：总请求数、成功/失败计数、每站点平均响应时间。
    线程安全实现，支持并发抓取场景下的指标累加。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests: int = 0
        self.success_count: int = 0
        self.fail_count: int = 0
        self._site_times: Dict[str, List[float]] = {}  # domain -> [response_time, ...]

    def record_success(self, domain: str, response_time: float) -> None:
        """记录一次成功请求及其响应时间。"""
        with self._lock:
            self.total_requests += 1
            self.success_count += 1
            self._site_times.setdefault(domain, []).append(response_time)

    def record_failure(self, domain: str) -> None:
        """记录一次失败请求。"""
        with self._lock:
            self.total_requests += 1
            self.fail_count += 1

    def get_summary(self) -> Dict[str, Any]:
        """返回指标摘要字典。"""
        with self._lock:
            site_avg: Dict[str, float] = {}
            for domain, times in self._site_times.items():
                site_avg[domain] = round(sum(times) / len(times), 3) if times else 0.0
            return {
                'total_requests': self.total_requests,
                'success_count': self.success_count,
                'fail_count': self.fail_count,
                'avg_response_time_per_site': site_avg,
            }


# 全局指标实例
metrics = MetricsTracker()


# ============================================================
# 熔断器（Circuit Breaker）
# ============================================================

class CircuitBreaker:
    """
    简单的域名级熔断器：追踪每个域名的连续失败次数。
    当连续失败次数超过阈值时，熔断器打开（open），后续请求直接跳过该域名。
    成功请求会重置失败计数。

    状态说明：
    - closed: 正常状态，允许请求通过
    - open: 熔断状态，拒绝请求
    """

    def __init__(self, failure_threshold: int = MAX_CONSECUTIVE_FAILURES) -> None:
        self._lock = threading.Lock()
        self._failures: Dict[str, int] = {}  # domain -> consecutive failure count
        self._threshold = failure_threshold

    def is_open(self, domain: str) -> bool:
        """检查某域名的熔断器是否处于打开（拒绝请求）状态。"""
        with self._lock:
            return self._failures.get(domain, 0) >= self._threshold

    def record_success(self, domain: str) -> None:
        """记录成功，重置该域名的连续失败计数。"""
        with self._lock:
            self._failures[domain] = 0

    def record_failure(self, domain: str) -> None:
        """记录失败，递增该域名的连续失败计数。"""
        with self._lock:
            self._failures[domain] = self._failures.get(domain, 0) + 1

    def get_status(self) -> Dict[str, int]:
        """返回所有域名的失败计数快照。"""
        with self._lock:
            return dict(self._failures)


# 全局熔断器实例
circuit_breaker = CircuitBreaker()

# Global rate limiter
rate_limiter = DomainRateLimiter(min_gap=1.0)


# ============================================================
# Session 连接池管理（每域名一个 Session，复用 TCP 连接 + Cookie）
# ============================================================

_sessions: Dict[str, requests.Session] = {}
_sessions_lock = threading.Lock()


def get_session(domain: str) -> requests.Session:
    """
    获取指定域名的 requests.Session 实例。
    同一域名复用同一个 Session，实现连接池和 Cookie 持久化。
    """
    with _sessions_lock:
        if domain not in _sessions:
            session = requests.Session()
            # 预配置默认 headers，减少每次请求的开销
            session.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Cache-Control': 'max-age=0',
            })
            _sessions[domain] = session
        return _sessions[domain]


# ============================================================
# HTTP 条件请求缓存（ETag / Last-Modified）
# ============================================================

_conditional_cache: Dict[str, Dict[str, str]] = {}  # url -> {'etag': ..., 'last_modified': ...}
_conditional_cache_lock = threading.Lock()


def get_conditional_headers(url: str) -> Dict[str, str]:
    """
    获取指定 URL 的条件请求头（If-None-Match / If-Modified-Since）。
    如果之前请求过该 URL 且服务端返回了 ETag 或 Last-Modified，
    则在下次请求时携带条件头以避免重复下载未变更的内容。
    """
    with _conditional_cache_lock:
        cached = _conditional_cache.get(url, {})
    headers = {}
    if cached.get('etag'):
        headers['If-None-Match'] = cached['etag']
    if cached.get('last_modified'):
        headers['If-Modified-Since'] = cached['last_modified']
    return headers


def update_conditional_cache(url: str, response: requests.Response) -> None:
    """从响应中提取 ETag / Last-Modified 并缓存。"""
    etag = response.headers.get('ETag')
    last_modified = response.headers.get('Last-Modified')
    if etag or last_modified:
        with _conditional_cache_lock:
            _conditional_cache[url] = {
                'etag': etag or '',
                'last_modified': last_modified or '',
            }


# ============================================================
# robots.txt 合规检查
# ============================================================

_robots_cache: Dict[str, RobotFileParser] = {}  # base_url -> RobotFileParser
_robots_lock = threading.Lock()


def is_allowed_by_robots(url: str) -> bool:
    """
    检查指定 URL 是否被该站点的 robots.txt 允许爬取。
    结果按域名缓存，避免重复请求 robots.txt。
    如果 robots.txt 无法获取，默认允许（宽容策略）。
    """
    if not RESPECT_ROBOTS_TXT:
        return True

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{base_url}/robots.txt"

    with _robots_lock:
        if base_url in _robots_cache:
            rp = _robots_cache[base_url]
            return rp.can_fetch('*', url)

    # 首次访问该域名的 robots.txt
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        # 无法读取 robots.txt 时默认允许
        logger.info("robots.txt 读取失败，默认允许爬取", extra={'site': base_url, 'event': 'robots_txt_error'})
        return True

    with _robots_lock:
        _robots_cache[base_url] = rp

    allowed = rp.can_fetch('*', url)
    if not allowed:
        logger.info("robots.txt 禁止爬取该 URL", extra={'site': url, 'event': 'robots_txt_denied'})
    return allowed


# ============================================================
# 工具函数
# ============================================================

def get_current_round() -> int:
    """
    根据当前小时判断当日第几轮（固定映射，禁止计数器模式）
    - 00:00-03:59 -> 第1轮
    - 04:00-07:59 -> 第2轮
    - 08:00-11:59 -> 第3轮
    - 12:00-15:59 -> 第4轮
    - 16:00-19:59 -> 第5轮
    - 20:00-23:59 -> 第6轮
    """
    hour = get_beijing_time().hour
    if 0 <= hour < 4:
        return 1
    elif 4 <= hour < 8:
        return 2
    elif 8 <= hour < 12:
        return 3
    elif 12 <= hour < 16:
        return 4
    elif 16 <= hour < 20:
        return 5
    else:  # 20 <= hour < 24
        return 6


def get_random_profile() -> Dict[str, Any]:
    """随机返回一组一致的浏览器配置（UA + 指纹 + 语言匹配）"""
    return random.choice(BROWSER_PROFILES)


def get_random_ua() -> str:
    """随机返回一个User-Agent（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['user_agent']


def get_random_fingerprint() -> Dict[str, str]:
    """随机返回一组浏览器指纹头部（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['fingerprint']


def get_random_accept_language() -> str:
    """随机返回一个Accept-Language（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['accept_language']


def get_random_delay() -> float:
    """随机返回请求延迟时间（秒）"""
    return random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)


def get_referer(url: str) -> str:
    """
    根据目标 URL 生成 Referer 头（使用站点自身首页作为 Referer）。
    这增强了请求的真实性，降低被反爬机制拦截的概率。
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


# ============================================================
# 哈希记录管理
# ============================================================

def load_hash_records() -> Dict[str, str]:
    """Load hash records from file. Supports both JSON and legacy url=hash format."""
    records: Dict[str, str] = {}
    if os.path.exists(HASH_RECORD_FILE):
        try:
            with open(HASH_RECORD_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            # Try JSON format first
            if content.startswith('{'):
                data = json.loads(content)
                if isinstance(data, dict):
                    records = data.get('records', data)
            else:
                # Legacy url=hash format
                for line in content.split('\n'):
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        url, md5_hash = line.split('=', 1)
                        records[url.strip()] = md5_hash.strip()
        except Exception as e:
            logger.warning("读取哈希文件失败: %s", e)
    return records


def save_hash_records(records: Dict[str, str]) -> bool:
    """Save hash records as JSON (atomic write)."""
    tmp_file = HASH_RECORD_FILE + '.tmp'
    try:
        data = {
            'schema_version': 2,
            'updated_at': get_beijing_time().isoformat(),
            'records': records,
        }
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, HASH_RECORD_FILE)
        logger.info("哈希文件已更新: %d 条记录", len(records))
        return True
    except Exception as e:
        logger.error("保存哈希文件失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


# ============================================================
# 已通知条目管理（去重）
# ============================================================

def load_notified_items() -> Dict[str, Any]:
    """
    加载已通知条目
    新格式：dict{'items': [{'url', 'text', 'source', 'time'}, ...]}
    旧格式：set of URLs（向后兼容）
    """
    if os.path.exists(NOTIFIED_ITEMS_FILE):
        try:
            with open(NOTIFIED_ITEMS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data  # 新格式直接返回
            elif isinstance(data, list):
                return {'items': [{'url': u} for u in data]}  # 旧格式转新格式
            elif isinstance(data, set):
                return {'items': [{'url': u} for u in data]}
        except Exception as e:
            logger.warning("读取已通知条目文件失败: %s", e)
    return {'items': []}


def save_notified_items(item_dict: Dict[str, Any]) -> bool:
    """保存已通知条目URL集合到文件（原子写入）"""
    tmp_file = NOTIFIED_ITEMS_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(item_dict, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, NOTIFIED_ITEMS_FILE)
        logger.info("已通知条目记录已更新: %s (%d 条)", NOTIFIED_ITEMS_FILE, len(item_dict.get('items', [])))
        return True
    except Exception as e:
        logger.error("保存已通知条目失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


def filter_new_items(items: List[Any], notified: Dict[str, Any]) -> Tuple[List[Any], Set[str]]:
    """
    从条目列表中过滤出未通知过的新条目
    返回：(新条目列表, 本轮新增的URL集合)
    """
    new_items: List[Any] = []
    new_urls: Set[str] = set()
    for item in items:
        item_url = item['url'] if isinstance(item, dict) else item
        if item_url not in notified:
            new_items.append(item)
            new_urls.add(item_url)
    return new_items, new_urls


# ============================================================
# 线报数据库（items.json）- 持久化累积所有历史线报
# ============================================================


def merge_items_into_db(new_item_list: List[Dict[str, str]], check_time: str) -> int:
    """
    将本轮新抓取的线报合并到全量数据库中（按 URL 去重）
    新条目插入到列表头部（最新的在前面）
    """
    db = load_items_db()
    existing_urls = set(item['url'] for item in db['items'])

    # 过滤出真正的新条目，并添加自动分类
    added = 0
    fresh_items: List[Dict[str, Any]] = []
    for item in new_item_list:
        url = item.get('url', '')
        if url and url not in existing_urls:
            # 添加自动分类
            if not item.get('category'):
                item['category'] = auto_categorize(item.get('text', ''))
            fresh_items.append(item)
            existing_urls.add(url)
            added += 1

    # 新条目插到头部
    if fresh_items:
        db['items'] = fresh_items + db['items']

    # 超出上限时裁剪（保留最新条目）
    if len(db['items']) > MAX_ITEMS_DB:
        removed = len(db['items']) - MAX_ITEMS_DB
        db['items'] = db['items'][:MAX_ITEMS_DB]
        logger.info("裁剪旧条目: 移除 %d 条，保留最新 %d 条", removed, MAX_ITEMS_DB)

    db['updated_at'] = check_time
    save_items_db(db)
    logger.info("新增 %d 条，总计 %d 条", added, len(db['items']))
    return added


# ============================================================
# 爬虫核心逻辑
# ============================================================

# ============================================================
# 站点专用解析器
# ============================================================

def parse_ypojie(soup: BeautifulSoup) -> str:
    """易破解 (WordPress DUX主题) - 精准提取最新文章标题和链接"""
    items: List[str] = []
    for h2 in soup.select('#content h2 a, #content .entry-title a, #main-content h2 a'):
        text = h2.get_text(strip=True)
        href = h2.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")
    if not items:
        for a in soup.select('.widget_recent a, .widgets-list a, .recent-posts a'):
            text = a.get_text(strip=True)
            href = a.get('href', '')
            if text and len(text) > 5 and not any(x in href for x in ['/page/', '/archives']):
                items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_discuz_threadlist(soup: BeautifulSoup) -> str:
    """Discuz论坛通用解析器 - 精准提取帖子列表"""
    items: List[str] = []
    for a in soup.select('.threadlist .t a, .tl .t a, #threadlist .t a, .threadlist tr td a.xst, .threadlist tr td a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 3 and '/thread-' in href:
            items.append(f"{text} ({href})")
    if not items:
        for tr in soup.select('.forum tbody tr, table tbody tr'):
            for a in tr.select('a'):
                text = a.get_text(strip=True)
                href = a.get('href', '')
                if text and len(text) > 3 and '/thread-' in href:
                    items.append(f"{text} ({href})")
                    break
    return '\n'.join(items[:30])


def parse_discuz_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Discuz论坛 - 结构化条目提取"""
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for a in soup.select('.threadlist .t a, .tl .t a, #threadlist .t a, .threadlist tr td a.xst, .threadlist tr td a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or text in seen or '/thread-' not in href:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            items.append({'text': text, 'url': href})
    if not items:
        for tr in soup.select('.forum tbody tr, table tbody tr'):
            for a in tr.select('a'):
                text = a.get_text(strip=True)
                href = a.get('href', '').strip()
                if text and len(text) > 3 and '/thread-' in href and text not in seen:
                    seen.add(text)
                    if href.startswith('/'):
                        href = urljoin(base_url, href)
                    if href.startswith('http'):
                        items.append({'text': text, 'url': href})
                    break
    return items[:30]


def parse_yxssp_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """异星软件空间 (yxssp.com) - extract article links.

    Primary: a[rel="bookmark"] which tagDiv themes use for post links.
    Fallback: any anchor with href matching yxssp.com/{digits}.html.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Primary: bookmark links (standard tagDiv theme pattern)
    for a in soup.select('a[rel="bookmark"]'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 4:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Secondary: entry-title links within td modules
    for a in soup.select('.entry-title a, .td-module-title a, td-module-title a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 4 or text in seen:
            continue
        if not re.search(r'yxssp\.com/\d+\.html', href):
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Fallback: broad URL pattern match
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 200:
                continue
            if not re.search(r'yxssp\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 3. 资源厅 / 晓晓资源网  (ziyuanting.com)
# ---------------------------------------------------------------------------
# HTML structure: OneNav WordPress theme (navigation/directory site).
#   Site entries:
#     <article class="posts-item sites-item ...">
#       <a href="https://www.ziyuanting.com/sites/{id}.html" class="sites-body">
#         <h3 class="item-title"><b>Site Name</b></h3>
#       </a>
#     </article>
#   Bulletin/announcement links:
#     <a href="https://www.ziyuanting.com/bulletin/{id}.html">title</a>
#   App/software entries:
#     <article class="posts-item app-item ...">
#       <a href="https://www.ziyuanting.com/app/{id}.html">
#
# The old parser only looked for /bulletin/, /article/, /post/ paths
# which misses the main content: /sites/ and /app/ entries.
# Fix: extract sites-item and app-item entries, plus bulletin links.
# ---------------------------------------------------------------------------




def parse_423down(soup: BeautifulSoup) -> str:
    """423Down - 精准提取软件文章，排除分类导航和侧边栏"""
    items: List[str] = []
    seen: Set[str] = set()
    # 主内容区的文章标题链接（格式：/数字.html 才是文章页）
    for a in soup.select('.post-list a, .content-list a, article h2 a, .entry-title a, #main a, .list-item a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if not text or len(text) < 5:
            continue
        # 只要文章页（/数字.html 格式）
        if not re.search(r'/\d+\.html', href):
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append(f"{text} ({href})")
    # 如果上面没找到，用更宽松的方式：取包含日期关键词的链接
    if not items:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 80:
                continue
            if not re.search(r'/\d+\.html', href):
                continue
            # 排除纯英文薄标题（通常是导航）
            chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
            if chinese_count < 2 and len(text) < 15:
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append(f"{text} ({href})")
    return '\n'.join(items[:30])




# ---------------------------------------------------------------------------
# 1. 423Down  (423down.com)
# ---------------------------------------------------------------------------
# HTML structure: WordPress D7 theme.
#   <ul class="excerpt">
#     <li>
#       <h2><a href="https://www.423down.com/16874.html" title="..." target="_blank">
#           XYplorer文件管理器v28.30.0700 绿色便携版</a></h2>
#       ...
#     </li>
#   </ul>
# The old parser used soup.find_all('a') + regex /\d+\.html which could
# fail when the HTTP response (via requests) differs from curl output
# or when Chinese text filters are too aggressive.
# Fix: target the specific .excerpt container and relax text filters.
# ---------------------------------------------------------------------------

def parse_423down_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """423Down - extract article entries from the WordPress excerpt list.

    Looks for <h2> links inside ul.excerpt > li, falling back to any
    anchor whose href matches /{digits}.html on the domain.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Primary: structured excerpt blocks (most reliable)
    for a in soup.select('ul.excerpt li h2 a, .excerpt h2 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Fallback: any link matching the article URL pattern
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 4 or len(text) > 120:
                continue
            if not re.search(r'423down\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            # Skip navigation-like short text
            skip_words = {'首页', '关于', '搜索', '登录', '安卓软件', '电脑软件',
                          '操作系统', '原创软件', '媒体播放', '网页浏览', '图形图像',
                          '聊天软件', '办公软件', '上传下载', '实用软件', '系统辅助',
                          '系统必备', '安全软件', '补丁相关', '硬件相关'}
            if text in skip_words:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


def parse_ziyuanting_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """晓晓资源网 (ziyuanting.com) - extract site entries and announcements.

    This is a navigation/directory site using the OneNav theme.
    Main content items use /sites/{id}.html and /app/{id}.html URLs.
    Announcements use /bulletin/{id}.html.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Primary: site directory entries (the main content of the page)
    for article in soup.select('article.sites-item, article.posts-item.sites-item'):
        a_tag = article.select_one('a.sites-body, a[href*="/sites/"]')
        if not a_tag:
            continue
        # Extract title from <b> inside .item-title, or from title attr
        title_el = article.select_one('.item-title b, .item-title')
        text = title_el.get_text(strip=True) if title_el else a_tag.get('title', '').strip()
        href = a_tag.get('href', '').strip()
        if not text or len(text) < 2:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Secondary: app/software download entries
    for article in soup.select('article.app-item, article.posts-item.app-item'):
        a_tag = article.select_one('a[href*="/app/"]')
        if not a_tag:
            continue
        title_el = article.select_one('.item-title a, .item-title')
        text = title_el.get_text(strip=True) if title_el else a_tag.get_text(strip=True)
        # Clean version suffix like " - 1.0.1"
        text = re.sub(r'\s*-\s*[\d.]+$', '', text).strip()
        href = a_tag.get('href', '').strip()
        if not text or len(text) < 2 or text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Tertiary: bulletin/announcement links
    for a in soup.select('a[href*="/bulletin/"]'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 4 or text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Fallback: broad pattern for any content links
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            if not re.search(r'ziyuanting\.com/(sites|app|bulletin)/\d+\.html', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:50]


# ---------------------------------------------------------------------------
# 4. 新赚吧 / 游戏下载吧  (xzba.cc)
# ---------------------------------------------------------------------------
# HTML structure: WordPress with Zibll theme.
#   Game entries appear in .posts-row containers:
#     <a href="https://xzba.cc/702.html">仁王3</a>
#   These are inside article elements within the home tab content.
# The old parser used selectors like '.post-title a, .item-title a,
# h2 a, h3 a, .game-title a, .list-item a' which may not match the
# Zibll theme's actual class structure.
# Fix: target the specific URL pattern xzba.cc/{digits}.html and use
# broader WordPress selectors.
# ---------------------------------------------------------------------------




def parse_ziyuanting(soup: BeautifulSoup) -> str:
    """晓晓资源网 - 只提取公告文本用于MD5比对"""
    items: List[str] = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if '/bulletin/' in href or '/article/' in href or '/post/' in href:
            if text and len(text) > 5:
                items.append(f"{text} ({href})")
    return '\n'.join(items[:20])


def parse_wycad_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """无忧软件网 - 提取软件/系统文章条目。

    站点为软件下载站，文章链接格式为 https://www.wycad.com/{id}.html。
    首页展示最新软件/系统文章，包含 Windows 系统、办公软件、媒体工具等。
    需排除分类导航 (/app/, /soft/, /windows/, /tag/, /happy/, /ziyuan/)、
    搜索链接 (?s=)、公告 (/bulletin/) 和纯分类描述文本。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    # URL path prefixes that indicate navigation/category links, not articles
    nav_patterns = [
        '/app/', '/soft/', '/windows/', '/happy/', '/ziyuan/',
        '/tag/', '/bulletin/', '?s=', '/page/',
    ]

    # Navigation / junk texts to skip
    skip_texts = {
        '首页', '手机软件', '电脑软件', '操作系统', '影音娱乐', '教程课程',
        '办公软件', '影音软件', '实用软件', '图形图像', '媒体工具',
        '教育教学', '上传下载', '社交聊天', '系统工具', '浏览器',
        '安卓游戏', '其他安卓', '图片图像', '视频工具', '安全防护',
        '即时通讯', '设计软件', 'Windows11', 'Windows10', 'Windows7',
        'WinPE', '原镜像', 'PC游戏', '电影分享', '音乐分享',
    }

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 6 or len(text) > 150:
            continue
        if text in seen or text in skip_texts:
            continue

        # Skip navigation/category links
        if any(pat in href for pat in nav_patterns):
            continue

        # Must be an article link: contains digits.html on wycad.com
        if not re.search(r'wycad\.com/\d+\.html', href):
            # Also accept relative links like /312.html
            if not re.search(r'^/\d+\.html$', href):
                continue

        # Extract article ID for dedup
        id_match = re.search(r'/(\d+)\.html', href)
        if id_match:
            article_id = id_match.group(1)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

        # Skip long description texts (category descriptions are usually > 50 chars
        # and don't have typical article title patterns)
        # Article titles usually contain version numbers, brackets, or specific software names
        if len(text) > 80 and not re.search(r'v\d|V\d|\d+\.\d+', text):
            continue

        # Ensure meaningful Chinese text
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2:
            continue

        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]




def parse_h6room_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """好料空间(H6线报) - 提取最新发布的软件/资源文章。

    站点使用 WordPress 风格 CMS，文章链接格式为 https://www.h6room.com/{id}.html。
    首页以卡片形式展示文章，包含缩略图链接和标题链接（指向同一文章）。
    分类包括：安卓应用、实用工具、拍照修图、影视影音、PC软件、TV盒子、技巧教程等。
    需排除导航分类链接、登录/注册、标签链接、作者链接、评论数链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    # Navigation / junk texts to skip
    skip_texts = {
        '首页', '安卓应用', '实用工具', '系统办公', '拍照修图',
        '影视影音', '办公学习', '小说阅读', '电影动漫', '社交聊天',
        '资源下载', '音乐铃声', '天气生活', '美化壁纸', '生活服务',
        'TV盒子', 'PC软件', '技巧教程', '会员专区',
        '更新', '浏览', '点赞', '评论',
        '登录', '注册', '找回密码', '普通会员', '黄金会员',
    }

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen or text in skip_texts:
            continue

        # Must be an article link: h6room.com/{digits}.html
        if not re.search(r'h6room\.com/\d+\.html', href):
            continue

        # Extract article ID for dedup (thumbnail + title link to same article)
        id_match = re.search(r'/(\d+)\.html', href)
        if id_match:
            article_id = id_match.group(1)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

        # Skip links that are just comment counts (#respond, #comments)
        if '#respond' in href or '#comments' in href:
            continue

        # Ensure meaningful Chinese text
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2:
            continue

        seen.add(text)
        items.append({'text': text, 'url': href})

    return items[:30]


def parse_xzba_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """游戏下载吧 (xzba.cc) - extract game entries from the WordPress post list.

    Primary: links inside post/article containers matching the URL pattern.
    Fallback: any anchor with href matching xzba.cc/{digits}.html.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Navigation / category words to skip
    skip_words = {'首页', '最新发布', '角色扮演', '动作', '模拟', '休闲',
                  '独立', '冒险', '策略', 'switch模拟', '找游戏', '登录',
                  '注册', '找回密码', '最新', '关于'}

    # Primary: post entry links within content containers
    for a in soup.select('.posts-row a, .home-tab-content a, .tab-content a, '
                         'article a, .post-entry a, .entry-title a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 120:
            continue
        if not re.search(r'xzba\.cc/\d+\.html', href):
            continue
        if text in seen or text in skip_words:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Fallback: broad URL pattern match across all links
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 2 or len(text) > 120:
                continue
            if not re.search(r'xzba\.cc/\d+\.html', href):
                continue
            if text in seen or text in skip_words:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 5. 我不找 / 我帮找网  (wobangzhao.com)
# ---------------------------------------------------------------------------
# HTML structure: Discuz! X3.5 forum.
#   Thread links use TWO formats:
#     1. SEO-friendly: thread-{tid}-{page}-{fid}.html
#        e.g. <a href="thread-6539-1-1.html"> or
#             <a href="https://www.wobangzhao.com/thread-6539-1-1.html">
#     2. Standard: forum.php?mod=viewthread&tid={id}
#   The homepage features:
#     - Slideshow with <span class="title">text</span> inside
#       <ul class="slideshow"><li><a href="thread-{id}-1-1.html">
#     - Hot/elite resource lists:
#       <div class="module cl xl xl1"><ul><li>
#         <a href="forum-{fid}-1.html">[category]</a>
#         <a href="thread-{tid}-1-1.html" title="...">title text</a>
#       </li></ul></div>
#     - Forum category sections with latest thread links:
#       <dd><a href="thread-{tid}-1-1.html" class="xi2">title</a></dd>
#
# The old parser used 'a[href*="thread-"]' which should match, but
# may fail due to relative URLs not being resolved, or the
# Discuz-specific <base href> tag affecting URL resolution.
# Fix: handle both absolute and relative thread- URLs, also target
# the specific Discuz portal block structure.
# ---------------------------------------------------------------------------




def parse_apprcn_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """反斗限免 - 提取限免软件列表"""
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for a in soup.select('article a, .post a, h2 a, h3 a, .entry-title a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or len(text) > 80:
            continue
        skip = ['阅读全文', '赞', '评论', '去评论', '下一页', '上一页', '返回顶部']
        if text in skip:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_daydayzhuan_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """天天赚 - 提取线报文章条目。

    站点使用自定义 CMS，文章链接格式为 /article/{数字}。
    首页分栏：实时线报、项目首码、手机赚钱、爆款秒杀、随笔等。
    文章标题通常描述具体的赚钱活动/红包攻略。
    需排除导航分类链接 (/yangmao, /longtime, /blockChain 等) 和 APP 下载链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    # Navigation / junk texts to skip
    skip_texts = {
        '首页', '实时线报', '项目首码', '手机赚钱', '爆款秒杀',
        '随笔', '去下载', '资讯', '网站地图',
    }

    # Strategy 1: Match /article/{id} links
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 5 or len(text) > 120:
            continue
        if text in seen or text in skip_texts:
            continue

        # Must be an article link
        if '/article/' not in href:
            continue

        # Extract article ID for dedup (same article may appear in featured + list)
        id_match = re.search(r'/article/(\d+)', href)
        if id_match:
            article_id = id_match.group(1)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

        # Ensure meaningful Chinese text
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2:
            continue

        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]


def parse_007ymd_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """007羊毛党 - 提取羊毛文章条目。

    站点使用自定义 CMS，文章链接格式为 https://www.007ymd.com/?id={数字}。
    首页分栏展示：长期羊毛、有奖活动、撸实物、影音会员、话费流量活动等。
    需排除导航分类链接 (?cate=)、纯 [查看详情] 文本、以及重复条目。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    # Navigation / junk texts to skip
    skip_texts = {
        '首页', '关于我们', '长期羊毛', '有奖活动', '撸实物',
        '影音会员', '话费流量活动', '[查看详情]', '趣闲赚',
        '长期 >', '活动 >', '实物 >',
    }

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 5 or len(text) > 120:
            continue
        if text in seen or text in skip_texts:
            continue

        # Must contain ?id= parameter (article links)
        if '?id=' not in href:
            continue

        # Extract article ID for dedup (same article may appear in multiple sections)
        id_match = re.search(r'[?&]id=(\d+)', href)
        if id_match:
            article_id = id_match.group(1)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)

        # Skip category navigation links (?cate=)
        if '?cate=' in href:
            continue

        # Ensure meaningful Chinese text
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2:
            continue

        # Clean up text: remove zero-width characters
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\u202e\u202c]', '', text).strip()
        if not text or len(text) < 5:
            continue

        seen.add(text)
        items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 3. www.daydayzhuan.com  (天天赚)
# ---------------------------------------------------------------------------




def parse_baicaio_items_v2(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """白菜哦 v2 - 提取文章列表"""
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # 匹配 /article/ 和 /item/ 模式
    for a in soup.select('a[href*="/article/"], a[href*="/item/"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_manmanbuy_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """慢慢买 - 提取优惠爆料/折扣商品条目。

    站点为比价导购平台，首页展示折扣爆料信息。
    主要文章/爆料链接格式为 https://cu.manmanbuy.com/discuxiao_{id}.aspx。
    每条爆料通常有两个链接（标题 + 价格），需通过 URL ID 去重只保留标题。
    需排除分类搜索链接 (zhekou/search, s.manmanbuy.com)、品牌订阅链接等导航。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 3 or len(text) > 150:
            continue
        if text in seen:
            continue

        # Must be a deal/article link on cu.manmanbuy.com
        if 'cu.manmanbuy.com/discuxiao_' not in href:
            continue

        # Extract deal ID for dedup
        id_match = re.search(r'discuxiao_(\d+)', href)
        if id_match:
            deal_id = id_match.group(1)
            if deal_id in seen_ids:
                continue
            seen_ids.add(deal_id)

        # Filter out pure price text (e.g. "20.4元/e15.46元") - keep the title line
        # Titles typically contain product names; price lines start with digits + 元
        if re.match(r'^[\d.]+\s*元', text) and len(text) < 40:
            continue

        # Ensure some meaningful content
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 1 and len(text) < 8:
            continue

        seen.add(text)
        items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 6. www.wycad.com  (网赚 / 无忧软件网)
# ---------------------------------------------------------------------------




def parse_axutongxue_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """阿虚同学的储物间 - 提取资源导航链接"""
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    # 提取所有外部链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not href.startswith('http'):
            continue
        if not text or len(text) < 3:
            continue
        # 过滤内部链接
        if 'axutongxue.net' in href:
            continue
        if text in seen:
            continue
        # 过滤导航词
        skip = ['获取公众号自动回复资源', '搜索储物间', '搜索公众号文章']
        if text in skip:
            continue
        seen.add(text)
        items.append({'text': text, 'url': href})
    return items[:30]


def parse_rss_feed(content_bytes: bytes, base_url: str) -> List[Dict[str, str]]:
    """RSS/Atom Feed 解析器 - 直接从XML提取文章条目"""
    from xml.etree import ElementTree as ET
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 预处理：修复常见的 XML 格式问题
    text = content_bytes.decode('utf-8', errors='ignore')
    # 去除 BOM
    if text.startswith('\ufeff'):
        text = text[1:]
    # 截断 </rss> 之后的内容（可能包含格式错误的注释等）
    rss_end = text.rfind('</rss>')
    if rss_end > 0:
        text = text[:rss_end + len('</rss>')]
    # 同样处理 </feed>（Atom）
    feed_end = text.rfind('</feed>')
    if feed_end > 0 and (rss_end < 0 or feed_end > rss_end):
        text = text[:feed_end + len('</feed>')]

    try:
        root = ET.fromstring(text.encode('utf-8'))
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        # RSS 2.0
        for item in root.findall('.//item'):
            title_el = item.find('title')
            link_el = item.find('link')
            title = title_el.text.strip() if title_el is not None and title_el.text else ''
            link = link_el.text.strip() if link_el is not None and link_el.text else base_url
            if title and title not in seen:
                seen.add(title)
                items.append({'text': title, 'url': link})
        # Atom
        if not items:
            for entry in root.findall('.//{http://www.w3.org/2005/Atom}entry'):
                title_el = entry.find('{http://www.w3.org/2005/Atom}title')
                link_el = entry.find('{http://www.w3.org/2005/Atom}link')
                title = title_el.text.strip() if title_el is not None and title_el.text else ''
                link = link_el.get('href', base_url) if link_el is not None else base_url
                if title and title not in seen:
                    seen.add(title)
                    items.append({'text': title, 'url': link})
    except ET.ParseError:
        # 最后兜底：用 BeautifulSoup 解析 RSS
        try:
            soup = BeautifulSoup(text, 'html.parser')
            for item in soup.find_all('item'):
                title_el = item.find('title')
                link_el = item.find('link')
                title = title_el.get_text(strip=True) if title_el else ''
                link = link_el.get_text(strip=True) if link_el else base_url
                if title and title not in seen:
                    seen.add(title)
                    items.append({'text': title, 'url': link})
        except Exception:
            pass
    return items[:30]


def parse_ghxi(soup: BeautifulSoup) -> str:
    """果核剥壳 (新版结构 .item-content h2 a) - 精准提取文章"""
    items: List[str] = []
    for a in soup.select('.item-content h2 a, .item-content h3 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")
    # 兼容旧版结构
    if not items:
        for a in soup.select('.post-item .entry-title a, .post-item h2 a'):
            text = a.get_text(strip=True)
            href = a.get('href', '')
            if text and len(text) > 5:
                items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_ghxi_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """果核剥壳 - 通过 WordPress REST API 获取文章（站点为 Vue SPA，HTML 无法直接解析）"""
    api_url = "https://www.ghxi.com/wp-json/wp/v2/posts?per_page=30"
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/json, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    items: List[Dict[str, str]] = []
    try:
        resp = requests.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            posts = resp.json()
            for post in posts:
                title = html_mod.unescape(post.get('title', {}).get('rendered', ''))
                link = post.get('link', '')
                if title and len(title) > 3 and link:
                    items.append({'text': title, 'url': link})
            logger.info("果核剥壳 WP API 获取到 %d 篇文章", len(items))
        else:
            logger.info("果核剥壳 WP API 返回 HTTP %d", resp.status_code)
    except Exception as e:
        logger.info("果核剥壳 WP API 请求失败: %s", e)
    return items


def parse_ym2cc_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """
    薅羊毛 (ym2.cc) - WordPress 站点，提取 /ymxb/ 路径的文章链接。
    选择器：a[href*="/ymxb/"]
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for a in soup.select('a[href*="/ymxb/"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        skip_words = ['首页', '关于', '联系', '留言', '搜索', '登录', '注册']
        if text in skip_words:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:30]


def parse_wobangzhao_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """我帮找网 (wobangzhao.com) - Discuz! X forum, extract thread/post links.

    Handles both SEO-friendly (thread-{id}-{page}-{tid}.html) and
    standard (forum.php?mod=viewthread) URL formats.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    skip_words = {'版块', '主题', '帖子', '更多', '下一页', '上一页',
                  '返回列表', '首页', 'BBS', '搜索', '登录', '注册',
                  '快捷导航', '联系我们', '找回密码', '立即注册',
                  '关于我们', '修复日志', '下载教程', '解压教程',
                  '加入天寻计划', '无法登陆？', '切换到宽版',
                  '设为首页', '收藏本站', '2026精选资源'}

    # Primary: thread links with title attributes (portal blocks, hot lists)
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get('title', '').strip() or a.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 150:
            continue

        # Match Discuz thread URLs (both formats)
        is_thread = False
        if re.search(r'thread-\d+-\d+-\d+\.html', href):
            is_thread = True
        elif 'mod=viewthread' in href:
            is_thread = True

        if not is_thread:
            continue

        # Filter out navigation/junk
        if any(w in text for w in skip_words):
            continue
        if text in seen:
            continue

        seen.add(text)
        # Resolve relative URLs (Discuz uses <base href> or relative paths)
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 6. 多多软件  (ddooo.com)
# ---------------------------------------------------------------------------
# HTML structure: Custom CMS (not WordPress).
#   Software entries use /softdown/{id}.htm links:
#     <a href="https://www.ddooo.com/softdown/210347.htm">
#       <p>雷电模拟器最新版</p></a>
#   The homepage has multiple sections:
#     - Top recommendation carousel: .app-list li > a
#     - Category sections with inline links:
#       <dd><a class="seahotid" href="/softdown/{id}.htm">name</a></dd>
#     - Sidebar "精品推荐" with softdown links
#     - "最新更新" and "最新下载" lists
#
# The old parser used 'a[href*="/softdown/"]' which is correct, but
# the skip_words filter included '下载' which may have been too broad,
# filtering out legitimate software names containing that word.
# Fix: relax the skip filter and also extract from the update list
# section which has the freshest content.
# ---------------------------------------------------------------------------




def parse_foxirj_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """
    佛系软件 (foxirj.com) - WordPress CoreNext 主题，提取文章列表。
    选择器：div.post-item h2 a, .entry-title a
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for a in soup.select('div.post-item h2 a, .entry-title a, article h2 a, .post-title a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 120:
            continue
        if text in seen:
            continue
        skip_words = ['首页', '关于', '联系', '留言', '搜索', '登录', '注册', '分类', '标签']
        if text in skip_words:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:30]


def parse_ddooo_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """多多软件站 (ddooo.com) - extract software entries.

    Looks for /softdown/{id}.htm links across the page, with improved
    filtering to avoid removing legitimate software names.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Only skip exact navigation words (not substrings of software names)
    skip_exact = {'首页', '最新更新', '软件分类', '论坛转贴', '收藏本站',
                  '电脑软件', '安卓下载', '苹果下载', '电脑游戏', 'MAC下载',
                  'TV市场', '专题合集', '排行榜', '手机版'}

    # Primary: all /softdown/ links with meaningful text
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 120:
            continue
        if '/softdown/' not in href:
            continue
        if text in skip_exact:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Secondary: latest update list items (often in a dedicated section)
    for li in soup.select('.update-list li, .new-list li, .CRCSList li'):
        a_tag = li.select_one('a[href*="/softdown/"]')
        if not a_tag:
            continue
        text = a_tag.get_text(strip=True)
        href = a_tag.get('href', '').strip()
        if not text or len(text) < 2 or text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]


def parse_onlinedown_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """华军软件园 (onlinedown.net) - extract software and article entries.

    Primary: /soft/{id}.htm links (software download pages).
    Secondary: /article/{id}.htm links (tutorials/guides).
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Skip exact navigation words
    skip_exact = {'首页', '电脑软件', '安卓软件', '苹果软件', '移动电脑版',
                  '系统软件', '软件专题', '教程攻略', '装机必备', '下载排行',
                  '最近更新', '更多', '搜索', '软件发布', 'AI产品榜'}

    # Primary: software download links (/soft/{id}.htm)
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 120:
            continue

        is_content = False
        if re.search(r'onlinedown\.net/soft/\d+\.htm', href):
            is_content = True
        elif re.search(r'/soft/\d+\.htm', href):
            is_content = True

        if not is_content:
            continue
        if text in skip_exact:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Secondary: article/tutorial links
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if not re.search(r'(onlinedown\.net)?/article/\d+\.htm', href):
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]




def extract_article_items(soup: BeautifulSoup, base_url: str = '') -> List[Dict[str, str]]:
    """
    从页面中提取独立文章条目列表（含链接）
    返回：[{'text': '标题', 'url': '链接'}, ...] 最多50条
    """
    # 移除干扰元素
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
        tag.decompose()

    body = soup.find('body')
    if not body:
        return []

    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1: 提取 <a> 标签的文本 + href
    for a_tag in body.find_all('a', href=True):
        text = a_tag.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
            continue
        text = ' '.join(text.split())
        if text in seen:
            continue
        # 过滤导航词/英文短词
        if text[0].isupper() and len(text) < 20:
            continue
        if len(re.findall(r'[^\w\u4e00-\u9fff\u3000-\u303f\s]', text)) > len(text) * 0.3:
            continue
        href = a_tag['href'].strip()
        # 转绝对链接
        if href.startswith('/') or not href.startswith('http'):
            href = urljoin(base_url, href)
        seen.add(text)
        items.append({'text': text, 'url': href})

    # 策略2: 如果 <a> 标签太少，用正文分句作为备选
    if len(items) < 2:
        text = body.get_text()
        for sep in ['\n', '｜', '丨', '│']:
            text = text.replace(sep, '|SPLIT|')
        lines = text.split('|SPLIT|')
        for line in lines:
            line = ' '.join(line.split()).strip()
            if not line or len(line) < 4 or len(line) > 150:
                continue
            if line in seen:
                continue
            if line.startswith('http') or line.startswith('www') or line.isdigit():
                continue
            if len(re.findall(r'[a-zA-Z0-9]', line)) > len(line) * 0.7 and len(line) < 30:
                continue
            seen.add(line)
            items.append({'text': line, 'url': base_url})

    return items[:50]




# ---------------------------------------------------------------------------
# 1. 12345pro.com  (12345线报)
# ---------------------------------------------------------------------------
def parse_12345pro_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """12345线报 - 提取文章条目。

    站点使用自定义 CMS，文章链接格式为 /article/{id}.html。
    主内容区使用 h2.zt-biaoti a，侧边栏使用 p.zt-biaoti a，
    幻灯片使用 .slide-title a。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - 主内容区文章列表 + 幻灯片标题 + 侧边栏推荐
    for selector in [
        'h2.zt-biaoti a',
        '.slide-title a',
        'p.zt-biaoti a',
        '.post-loop .item h2 a',
    ]:
        for a in soup.select(selector):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            if text in seen:
                continue
            # 仅保留文章链接 (/article/{id}.html)
            if not re.search(r'/article/\d+\.html', href):
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    # 策略2：通用兜底 - 匹配所有 /article/{id}.html 链接
    if len(items) < 3:
        for a in soup.select('a[href*="/article/"]'):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            if not re.search(r'/article/\d+\.html', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 2. appinn.com  (小众软件)
# ---------------------------------------------------------------------------


def parse_appinn_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """小众软件 - WordPress 站点，提取文章条目。

    文章使用 article.post-box 包裹，标题在 h2.title.post-title a 中。
    幻灯片标题在 h2.slide-title 中。
    文章链接格式为 appinn.com/{slug}/。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - 文章卡片标题 + 幻灯片标题
    for selector in [
        'article h2.title.post-title a',
        'article.post-box h2 a',
        'h2.slide-title',
    ]:
        for a in soup.select(selector):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            # h2.slide-title 本身不是 <a>，取其文本
            if not href:
                continue
            if text in seen:
                continue
            # 过滤分类链接 (category/xxx/)
            if '/category/' in href:
                continue
            # 仅保留 appinn.com 域名下的文章链接
            if 'appinn.com' not in href and not href.startswith('/'):
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    # 策略2：通用兜底 - 匹配 appinn.com 下的 slug 链接
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 120:
                continue
            if text in seen:
                continue
            # 匹配 appinn.com/some-slug/ 格式
            if not re.search(r'appinn\.com/[\w-]+/$', href):
                continue
            if '/category/' in href or '/tag/' in href or '/page/' in href:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 3. ithome.com/zt/xijiayi  (IT之家 - 喜加一专题)
# ---------------------------------------------------------------------------


def parse_ithome_xijiayi_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """IT之家喜加一专题 - 提取新闻条目。

    专题页面使用 ol.newslist 列表，每条新闻为 li，
    标题在 div.newsbody 内的 a > h2 中。
    链接格式为 ithome.com/0/{id}/{id}.htm。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - 新闻列表中的 h2 标题链接
    for li in soup.select('ol.newslist li'):
        newsbody = li.select_one('.newsbody')
        if not newsbody:
            continue
        # 标题在 .newsbody > a > h2 或 .newsbody > a[href]
        title_link = newsbody.select_one('a[href*="ithome.com"] h2')
        if title_link:
            a_tag = title_link.find_parent('a')
        else:
            a_tag = newsbody.select_one('a[href*="ithome.com/0/"]')
        if not a_tag:
            continue
        href = a_tag.get('href', '').strip()
        text = a_tag.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略2：通用兜底 - 匹配 ithome.com/0/{xxx}/{xxx}.htm 链接
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 120:
                continue
            if not re.search(r'ithome\.com/0/\d+/\d+\.htm', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 4. lsapk.com  (LSapk / 蓝鲨应用库)
# ---------------------------------------------------------------------------


def parse_lsapk_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """蓝鲨应用库 - WordPress CorePress 主题，提取文章条目。

    文章列表使用 li.post-item，标题在 .post-item-main h2 a 中。
    链接格式为 lsapk.com/{id}.html。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - 文章列表卡片标题
    for a in soup.select('li.post-item .post-item-main h2 a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 120:
            continue
        if text in seen:
            continue
        # 仅保留 lsapk.com 域名下的文章链接
        if 'lsapk.com' not in href and not href.startswith('/'):
            continue
        # 排除分类、标签等导航链接
        if '/category/' in href or '/tag/' in href or '/page/' in href:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略2：通用兜底 - 匹配 lsapk.com/{digits}.html 格式
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            if not re.search(r'lsapk\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 5. thosefree.com  (免费族 / 那些免费的砖)
# ---------------------------------------------------------------------------


def parse_thosefree_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """那些免费的砖 - 自定义 WordPress 主题，提取文章条目。

    文章列表使用 .post-item，标题在 a.post-item-title h3 中。
    文章链接格式为 thosefree.com/{slug}（无尾部斜杠）。
    幻灯片使用 .pic-cover-item。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - 文章卡片标题
    for a in soup.select('a.post-item-title'):
        href = a.get('href', '').strip()
        # 标题在 h3 子元素中
        h3 = a.find('h3')
        text = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 150:
            continue
        if text in seen:
            continue
        # 排除标签、分类等导航链接
        if '/tag/' in href or '/page/' in href or '/web/' in href or '/design/' in href:
            continue
        if '/apps/' in href and '/' == href.rstrip('/').split('thosefree.com')[-1]:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略2：幻灯片封面链接
    for a in soup.select('a.pic-cover-item'):
        href = a.get('href', '').strip()
        h3 = a.select_one('.pic-cover-item-title')
        text = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 150:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略3：侧边栏推荐文章
    for a in soup.select('a.sider-post-item-title'):
        href = a.get('href', '').strip()
        h3 = a.find('h3')
        text = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 150:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 6. douban.com/group/711811  (豆瓣小组 - 薅羊毛深度爱好者)
# ---------------------------------------------------------------------------


def parse_douban_group_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """豆瓣小组 - 提取讨论帖子条目。

    帖子列表使用 table.olt，每行 tr 中 td.title a 包含帖子标题。
    链接格式为 douban.com/group/topic/{id}/。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 策略1：精确选择器 - table.olt 中的帖子标题
    for a in soup.select('table.olt td.title a'):
        href = a.get('href', '').strip()
        # 优先使用 title 属性（完整标题），其次使用文本内容
        text = a.get('title', '').strip() or a.get_text(strip=True)
        if not text or len(text) < 3 or len(text) > 150:
            continue
        if not re.search(r'/group/topic/\d+', href):
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        # 清理 URL 中的查询参数
        href = re.sub(r'\?_spm_id=[^&]*', '', href)
        items.append({'text': text, 'url': href})

    # 策略2：通用兜底 - 匹配所有 group/topic/{id} 链接
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get('title', '').strip() or a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 150:
                continue
            if not re.search(r'douban\.com/group/topic/\d+', href):
                continue
            if text in seen:
                continue
            skip_words = ['回复', '删除', '举报', '推荐', '加入小组', '登录/注册']
            if any(w in text for w in skip_words):
                continue
            seen.add(text)
            href = re.sub(r'\?_spm_id=[^&]*', '', href)
            items.append({'text': text, 'url': href})

    return items[:50]


# ---------------------------------------------------------------------------
# 7. haodanku.com  (好单库)
# ---------------------------------------------------------------------------


def parse_haodanku_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """好单库 - Vue.js SPA 站点，从服务端渲染的 HTML 中提取可用链接。

    好单库为淘客选品平台，主体内容通过 Vue + API 动态渲染，
    BeautifulSoup 无法直接解析商品卡片数据。
    本解析器提取服务端渲染的导航页面链接和公告链接，
    以及 Vue 模板中嵌入的静态页面链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 定义关键内容页面映射（服务端渲染的重要导航/功能页面）
    key_pages = {
        '实时榜单': '/item/index',
        '好单预告': '/herald/index',
        '好单线报': '/activity/tip_off',
        '品牌专场': '/branditem/index',
        '品牌实时榜': '/Branditem/brandlist',
        '品牌库': '/Branditem/brandlibrary',
        '素材广场': '/material/index',
        '单页专区': '/Openindex/source_market',
        '全部商品': '/item/all_index',
        '好单库CMS': '/cms/intro',
        '抖货视频': '/dyitem',
        '精编文案': '/index/elaborately',
        '早安问候语': '/salutation',
    }

    # 过滤纯功能性/辅助性文字（非内容页面）
    skip_words = {
        '首页', '登录', '注册', '搜索', '好单库首页', '好单库APP',
        '客户服务', '联系客服', '建议反馈', 'API文档', '活动中心',
        '我的应用', '招商入驻', '开放平台', '商家合作',
        'CMS中心', '帮助中心', '退出登录',
    }

    # 策略1：提取服务端渲染的导航链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 80:
            continue
        # 清除 Vue 模板碎片（如 {{oTag.index}}）
        if '{{' in text:
            text = re.sub(r'\{\{[^}]*\}\}', '', text).strip()
            if not text or len(text) < 2:
                continue
        if text in seen:
            continue
        if text in skip_words:
            continue

        # 匹配 haodanku.com 域名下的有效页面链接
        is_valid = False
        if 'haodanku.com' in href:
            # 排除纯 JS 绑定（Vue 模板中的 href）
            if '{{' in href or "+'" in href or '+item' in href:
                continue
            # 排除静态资源
            if any(ext in href for ext in ['.css', '.js', '.png', '.jpg', '.gif', '.ico']):
                continue
            # 排除 API 子域名链接
            if 'api.' in href or 'cmspro.' in href:
                continue
            is_valid = True
        elif href.startswith('/') and not href.startswith('//'):
            # 相对路径，排除静态资源和 Vue 绑定
            if any(ext in href for ext in ['.css', '.js', '.png', '.jpg', '.gif']):
                continue
            if '{{' in href or "+'" in href:
                continue
            is_valid = True

        if not is_valid:
            continue

        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略2：提取公告详情链接 (notice_detail)
    for a in soup.select('a[href*="notice_detail"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True) or '好单库公告'
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # 策略3：提取 Vue 模板中嵌入的外部工具链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2:
            continue
        if text in seen:
            continue
        # 匹配外部链接（如多兔插件等）
        if href.startswith('http') and 'haodanku.com' not in href:
            # 排除 CDN、广告、统计等
            skip_domains = [
                'cdnjs.', 'alicdn.', 'googlesyndication.',
                'google-analytics.', 'efengqing.', 'bc.haodanku.',
            ]
            if any(d in href for d in skip_domains):
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]




# ---------------------------------------------------------------------------
# 1. hybase.com - 好赚网 (Custom CMS, GB2312)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Featured carousel: .top-entry a.top-entry-img-wrapper[title] -> /{cat}/{id}.html
#   - Article cards:      .li-type-card-title a[href]
#   - Buzz/quick links:   .home-buzz .title a[href] -> /free/{id}.html
#   - Recommended:        .axdswiper .swiper-slide a[href] -> /softkb/{cat}/{id}.html
# URL patterns: /shouji/android/{id}.html, /pc/windows/{id}.html,
#               /xitong/windows/{id}.html, /free/{id}.html,
#               /softkb/{cat}/{id}.html
# ---------------------------------------------------------------------------

def parse_hybase_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """好赚网 (hybase.com) - 提取软件/资源文章条目。

    站点为自定义 CMS，文章链接格式为 /{分类}/{id}.html。
    提取轮播推荐、文章卡片、快讯等多个区域的内容链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Targeted selectors for article title links
    selectors = [
        '.top-entry a.top-entry-img-wrapper',   # carousel featured items (use title attr)
        '.li-type-card-title a',                 # main article card titles
        '.home-buzz .title a',                   # buzz/quick news links
        '.axdswiper .swiper-slide a',            # recommended sidebar items (use title attr)
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get('href', '').strip()
            # Prefer 'title' attribute (full text), fall back to inner text
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not text or len(text) < 4 or len(text) > 120:
                continue
            if text in seen:
                continue
            # Must be an article link (contains .html and a numeric segment)
            if not re.search(r'/\d+\.html', href):
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    # Strategy 2: Fallback - scan all <a> tags matching article URL pattern
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 120:
                continue
            if not re.search(r'/\d+\.html', href):
                continue
            # Filter out navigation keywords
            skip_words = ['首页', '导航', '站点地图', '联系', '关于', '搜索',
                          '精选软件', '精选博客', '最新软件', '最新博客']
            if text in skip_words:
                continue
            if text in seen:
                continue
            chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
            if chinese_count < 2 and len(text) < 15:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 2. huodong5.com - 活动5 (WordPress, deal aggregator)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Main article list: .feature-post li.item .title a[href]
#     e.g. <a href="https://www.huodong5.com/223591.html">title</a>
#   - Slider featured:   h3.slide-title a
#   - Sidebar news:      .slider-ad li a
#   - Scrolling marquee: #xinxiaoxi a
# URL pattern: huodong5.com/{numeric_id}.html
# ---------------------------------------------------------------------------


def parse_huodong5_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """活动5 (huodong5.com) - 提取有奖活动文章条目。

    WordPress 站点，文章链接格式为 /{id}.html。
    提取"最新更新活动"列表、轮播推荐、侧栏推荐等区域。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Primary article list (.feature-post .title a)
    for a in soup.select('.feature-post .title a, h3.slide-title a, .slider-ad li a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5 or len(text) > 120:
            continue
        if text in seen:
            continue
        # Must be an article URL (domain + numeric id + .html)
        if not re.search(r'huodong5\.com/\d+\.html', href):
            continue
        # Filter ad/recommendation prefixes
        text = re.sub(r'^【推荐】', '', text).strip()
        if len(text) < 5:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Broader fallback - any link matching article URL pattern
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 120:
                continue
            if not re.search(r'huodong5\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            text = re.sub(r'^【推荐】', '', text).strip()
            if len(text) < 5:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 3. yangmaodang.club - 羊毛党 (WordPress, twentyfourteen theme)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Article entries: a[rel="bookmark"] -> /articles/{slug}/, /news/{slug}/
#   - Also blog-style: /things/{slug}/, /{slug}/ (standalone pages)
#   - Category links:  a[rel="category tag"] (these are category labels, skip)
#   - Navigation:      /category/*, /tag/*, /contact/, /about/, /history/
# URL patterns: /articles/{slug}/, /news/{slug}/, /things/{slug}/
# ---------------------------------------------------------------------------


def parse_yangmaodang_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """羊毛党 (yangmaodang.club) - 提取羊毛文章条目。

    WordPress twentyfourteen 主题站点，文章链接格式为 /articles/{slug}/。
    通过 rel="bookmark" 属性精准定位文章链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Use rel="bookmark" to find article links directly
    for a in soup.select('a[rel="bookmark"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        # Ensure it's an article-style URL, not a category/tag page
        if not re.search(r'yangmaodang\.club/(articles|news|things)/', href):
            # Also accept standalone article pages (e.g. /dache/, /one-click-urls/)
            if not re.search(r'yangmaodang\.club/[a-z0-9\-]+/$', href):
                continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Fallback - scan article links by URL pattern
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 4 or len(text) > 120:
                continue
            if text in seen:
                continue
            if not re.search(r'yangmaodang\.club/(articles|news|things)/\S+', href):
                continue
            # Skip category/tag pages
            if '/category/' in href or '/tag/' in href:
                continue
            skip_words = ['首页', '最新羊毛', '伙伴事物', '银行羊毛', '阿里羊毛',
                          '腾讯羊毛', '其他羊毛', '长期羊毛', '关于本站', '联系']
            if text in skip_words:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 4. xianbaomi.com - 线报迷 (Z-Blog)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Main list: ul.erx-list > li.item
#     Each item has: div.a > a.main[href] with title text
#     Pinned items have class "istop" with <em>[置顶]</em> prefix
#     e.g. <a href="https://xianbaomi.com/xb/242891.html" class="main">
#   - Some links are external (redirect URLs like kzurl19.cn)
# URL pattern: xianbaomi.com/xb/{id}.html (internal articles)
# ---------------------------------------------------------------------------


def parse_xianbaomi_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """线报迷 (xianbaomi.com) - 提取线报帖子条目。

    Z-Blog 站点，帖子链接格式为 /xb/{id}.html。
    列表结构为 ul.erx-list > li.item，标题在 a.main 中。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Primary list structure
    for li in soup.select('ul.erx-list li.item'):
        a = li.select_one('a.main')
        if not a:
            continue
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2 or len(text) > 120:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Broader fallback for all article links
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 2 or len(text) > 120:
                continue
            if text in seen:
                continue
            # Accept internal article links or external redirect links
            if 'xianbaomi.com/xb/' not in href and not href.startswith('http'):
                continue
            # Filter navigation
            skip_words = ['首页', '活动线报', '24小时热门线报', '1周热门线报',
                          '最新内容', '网站地图', '查券', '一周热门', '神车群']
            if text in skip_words:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:50]


# ---------------------------------------------------------------------------
# 5. yangmao.wang - 羊毛王 (Z-Blog)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Latest updates list: ul.newest.list-a#gengxin > li > a[href]
#     e.g. <a href="https://yangmao.wang/yangmao/41.html" title="...">text</a>
#   - Category lists:     ul.list-a > li > a[href] (under each section)
#   - Sidebar hot:        #sidehot li a, #hotreviewarticles li a
# URL pattern: yangmao.wang/{category}/{id}.html
#   categories: yangmao, zhuanqian, dzyh, gonglue
# ---------------------------------------------------------------------------


def parse_yangmao_wang_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """羊毛王 (yangmao.wang) - 提取羊毛活动文章条目。

    Z-Blog 站点，文章链接格式为 yangmao.wang/{分类}/{id}.html。
    主要提取最新更新列表和各分类板块的文章链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Extract from list-a (main article lists)
    for a in soup.select('ul.list-a li a'):
        href = a.get('href', '').strip()
        # Prefer title attribute for full text
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        # Must match article URL pattern
        if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Broader fallback
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not text or len(text) < 4 or len(text) > 120:
                continue
            if text in seen:
                continue
            if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
                continue
            skip_words = ['首页', '羊毛活动', '赚钱软件', '打折优惠', '赚钱攻略',
                          '更多', '关于本站', '网站地图']
            if text in skip_words:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 6. iqnew.com - 爱Q社区 (Custom CMS, GB2312)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Main update list (two-column layout):
#     .news-comm-wrap ul li a[target="_blank"][href]
#     e.g. <a href="/activity/214255.html">title</a>
#   - Banner carousel:   #myFocus .pic li a[href]
#   - Sections: /activity/{id}.html, /news/{id}.html, /mall/{id}.html
# URL patterns: /activity/{id}.html, /news/{id}.html, /mall/{id}.html
# ---------------------------------------------------------------------------


def parse_iqnew_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """爱Q社区 (iqnew.com) - 提取活动/资讯文章条目。

    帝国 CMS 站点，文章链接格式为 /{分类}/{id}.html。
    提取"最新更新"列表、轮播推荐等区域的内容链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Main update list (.news-comm-wrap)
    for a in soup.select('.news-comm-wrap ul li a, .iq_layer1_new a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        # Strip any <font> tag artifacts from text
        text = re.sub(r'<[^>]+>', '', text).strip()
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        # Must be an article link
        if not re.search(r'/(activity|news|mall)/\d+\.html', href):
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Banner/carousel links
    for a in soup.select('#myFocus .pic li a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen:
            continue
        if not re.search(r'/(activity|news|mall)/\d+\.html', href):
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 3: Fallback - scan all links
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 4 or len(text) > 120:
                continue
            if text in seen:
                continue
            if not re.search(r'/(activity|news|mall)/\d+\.html', href):
                continue
            skip_words = ['首页', 'QQ活动', '最新活动', '手机活动', '电脑软件',
                          '购物商城', '投稿', '注册', '登录', 'QQ群', '关注我们']
            if text in skip_words:
                continue
            chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
            if chinese_count < 2 and len(text) < 15:
                continue
            seen.add(text)
            if href.startswith('/'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 7. 51kanong.com - 51卡农 (Discuz X3.4, portal + forum)
# ---------------------------------------------------------------------------
# HTML structure:
#   - Portal article list: #article_list > li.article_item
#     .article_title h2 a[href] -> xyk-{tid}-1.htm
#   - Slide show:         .slideshow li a[href] -> xyk-{tid}-1.htm
#   - Today's headlines:  .comlimi_tops h2 a[href] -> xyk-{tid}-1.htm
#   - Featured articles:  .comlimi_hots h4 a[href] -> a-{id}-1.htm
#   - Thread links:       xyk-{tid}-{page}.htm, thread-{fid}-{page}-{tid}.html
# URL patterns: xyk-{tid}-{page}.htm, a-{id}-{page}.htm
#   also: forum.php?mod=viewthread&tid={tid}
# ---------------------------------------------------------------------------


def parse_51kanong_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """51卡农 (51kanong.com) - 提取论坛帖子/文章条目。

    Discuz X3.4 论坛门户页面，帖子链接格式为 xyk-{tid}-{page}.htm。
    提取最新文章列表、今日头条、精选导读、轮播推荐等区域。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Portal article list (#article_list)
    for a in soup.select('#article_list .article_title h2 a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not text or len(text) < 3 or len(text) > 120:
            continue
        if text in seen:
            continue
        seen.add(text)
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 2: Today's headlines (.comlimi_tops h2 a)
    for a in soup.select('.comlimi_tops h2 a, .comlimi_hots h4 a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not text or len(text) < 3 or len(text) > 120:
            continue
        if text in seen:
            continue
        seen.add(text)
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 3: Slideshow links
    for a in soup.select('.slideshow li a.slide_pic, .slideshow h3 a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not text or len(text) < 3 or len(text) > 120:
            continue
        if text in seen:
            continue
        seen.add(text)
        if not href.startswith('http'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    # Strategy 4: Fallback - scan all thread/article links
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 3 or len(text) > 120:
                continue
            if text in seen:
                continue
            # Match thread patterns: xyk-{tid}-1.htm, a-{id}-1.htm, thread-*
            if not re.search(r'(xyk-\d+-\d+\.htm|a-\d+-\d+\.htm|thread-\d+)', href):
                continue
            # Filter navigation
            skip_words = ['首页', '信用卡交流', '贷款交流', '热帖推荐', '租机交流',
                          '信用卡产品', '更多', '投稿', '注册', '登录', '找回密码']
            if text in skip_words:
                continue
            seen.add(text)
            if not href.startswith('http'):
                href = urljoin(base_url, href)
            items.append({'text': text, 'url': href})

    return items[:30]




# ---------------------------------------------------------------------------
# 1. b1.ymxianbao.cn  (羊毛线报)
# ---------------------------------------------------------------------------
def parse_ymxianbao_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """羊毛线报 - 提取线报文章条目。

    站点为 WordPress 博客，文章链接格式为 https://b1.ymxianbao.cn/{id}.html。
    首页以文章标题列表展示，链接直接指向文章页。
    排除联系站长、500G流量卡、任务平台合集等非文章导航链接。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Non-article navigation texts to skip
    skip_texts = {
        '羊毛阁', '首页', '联系站长', '联系我们', '500G流量卡', '任务平台合集',
        '1', '2', '3', '4', '5', '...679',
    }

    # Strategy 1: Match article links with numeric .html pattern on ymxianbao domain
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not text or len(text) < 4 or len(text) > 120:
            continue
        if text in seen or text in skip_texts:
            continue

        # Must be an article link: contains digits followed by .html
        if not re.search(r'\d+\.html', href):
            continue

        # Must be on the ymxianbao domain (absolute or relative)
        if 'ymxianbao' in href or href.startswith('/') or href.startswith('http') is False:
            pass  # acceptable
        else:
            continue

        # Skip pagination links like /page/2
        if '/page/' in href:
            continue

        # Ensure text has Chinese content (filter pure numbers / short nav)
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2:
            continue

        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})

    return items[:30]


# ---------------------------------------------------------------------------
# 2. www.007ymd.com  (007羊毛党)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 79tao.linejia.com  (79淘/邻家惠)
# ---------------------------------------------------------------------------

def parse_linejia_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """79淘/邻家惠 (linejia.com) - 提取活动线报条目

    HTML 结构: <ul class="list-wz"><li><a href="/huodong/xxx.html">标题</a>
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 主要结构: ul.list-wz li a
    for a in soup.select('ul.list-wz li a, .list-wz a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or text in seen:
            continue
        if '/huodong/' not in href and not re.search(r'/\d+\.html', href):
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            items.append({'text': text, 'url': href})

    # 回退: 任何包含 /huodong/ 的链接
    if not items:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 4 or text in seen:
                continue
            if '/huodong/' in href:
                seen.add(text)
                if href.startswith('/'):
                    href = urljoin(base_url, href)
                items.append({'text': text, 'url': href})

    return items[:30]


# ============================================================
# 解析器注册表（Parser Registry）
# 将域名模式映射到 (items_parser, text_parser) 元组，
# 替代 fetch_page_content 中冗长的 if/elif 链。
# ============================================================

# items_parser: (soup, base_url) -> List[Dict[str, str]]
# text_parser:  (soup) -> str  或  None（此时 text 由 items 拼接得到）
PARSER_REGISTRY: Dict[str, Tuple[Any, Optional[Any]]] = {
    '423down.com':       (parse_423down_items,      None),
    'ziyuanting.com':    (parse_ziyuanting_items,    None),
    'wycad.com':         (parse_wycad_items,         None),
    'baicaio.com':       (parse_baicaio_items_v2,    None),
    'h6room.com':        (parse_h6room_items,        None),
    'xzba.cc':           (parse_xzba_items,          None),
    'free.apprcn.com':   (parse_apprcn_items,        None),
    'kxdao.net':         (parse_discuz_items,         None),
    'yxssp.com':         (parse_yxssp_items,          None),
    'daydayzhuan.com':   (parse_daydayzhuan_items,   None),
    '007ymd.com':        (parse_007ymd_items,         None),
    'axutongxue.net':    (parse_axutongxue_items,    None),
    'manmanbuy.com':     (parse_manmanbuy_items,      None),
    'ym2.cc':            (parse_ym2cc_items,          None),
    'wobangzhao.com':    (parse_wobangzhao_items,     None),
    'foxirj.com':        (parse_foxirj_items,         None),
    'ddooo.com':         (parse_ddooo_items,          None),
    'onlinedown.net':    (parse_onlinedown_items,     None),
    # === New parsers ===
    '12345pro.com':      (parse_12345pro_items,       None),
    'appinn.com':        (parse_appinn_items,         None),
    'ithome.com':        (parse_ithome_xijiayi_items, None),
    'lsapk.com':         (parse_lsapk_items,          None),
    'thosefree.com':     (parse_thosefree_items,      None),
    'douban.com':        (parse_douban_group_items,   None),
    'haodanku.com':      (parse_haodanku_items,       None),
    'hybase.com':        (parse_hybase_items,         None),
    'huodong5.com':      (parse_huodong5_items,       None),
    'yangmaodang.club':  (parse_yangmaodang_items,    None),
    'xianbaomi.com':     (parse_xianbaomi_items,      None),
    'yangmao.wang':      (parse_yangmao_wang_items,   None),
    'iqnew.com':         (parse_iqnew_items,          None),
    '51kanong.com':      (parse_51kanong_items,       None),
    'ymxianbao.cn':     (parse_ymxianbao_items,      None),
    'linejia.com':      (parse_linejia_items,        None),
}


def _match_parser(url: str) -> Optional[Tuple[Any, Optional[Any]]]:
    """
    根据 URL 匹配 PARSER_REGISTRY 中的解析器。
    返回 (items_parser, text_parser) 或 None（使用通用解析）。
    """
    for domain_pattern, parsers in PARSER_REGISTRY.items():
        if domain_pattern in url:
            return parsers
    return None


def fetch_page_content(url: str) -> Tuple[bool, Any]:
    """
    爬取页面完整正文。
    返回：(成功标志, 内容/错误信息)
    内容包含：(text, title, summary, response_time)

    增强特性：
    - 指数退避重试（最多 3 次）
    - 每域名 Session 连接池复用
    - Referer 头部增强反爬抗性
    - HTTP 条件请求（ETag / If-Modified-Since）减少带宽
    - 熔断器自动跳过连续失败域名
    - robots.txt 合规检查
    """
    # URL scheme validation: only allow http/https
    if not url.startswith(('http://', 'https://')):
        return False, f"Invalid URL scheme: {url[:50]}"

    parsed = urlparse(url)
    domain = parsed.hostname or parsed.netloc

    # 熔断器检查：如果该域名连续失败过多，直接跳过
    if circuit_breaker.is_open(domain):
        logger.info("熔断器打开，跳过该域名", extra={'site': domain, 'event': 'circuit_breaker_open'})
        return False, "熔断器已打开（连续失败过多）"

    # robots.txt 合规检查
    if not is_allowed_by_robots(url):
        return False, "robots.txt 禁止爬取"

    session = get_session(domain)

    def make_request(ua: str, fingerprint: Optional[Dict[str, str]] = None,
                     accept_lang: Optional[str] = None) -> requests.Response:
        """构建并发送 HTTP 请求，包含所有反检测头部和条件请求头。"""
        headers: Dict[str, str] = {
            'User-Agent': ua,
            'Accept-Language': accept_lang or 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': get_referer(url),
        }
        # 添加浏览器指纹头部（Chrome/Edge 特有）
        if fingerprint:
            headers.update(fingerprint)
        # 添加 HTTP 条件请求头（If-None-Match / If-Modified-Since）
        headers.update(get_conditional_headers(url))

        return session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    try:
        logger.info("爬取 %s", url, extra={'site': url, 'event': 'crawl_start'})

        # 指数退避重试循环
        response: Optional[requests.Response] = None
        elapsed: float = 0.0

        for attempt in range(MAX_RETRIES):
            profile = get_random_profile()
            ua = profile['user_agent']
            fingerprint = profile['fingerprint']
            accept_lang = profile['accept_language']

            start_time = time.time()
            response = make_request(ua, fingerprint, accept_lang)
            elapsed = time.time() - start_time

            # 成功或非 403/5xx：直接使用
            if response.status_code == 200:
                break

            # 304 Not Modified：页面未变更，使用缓存
            if response.status_code == 304:
                logger.info("HTTP 304 Not Modified", extra={'site': url, 'event': 'not_modified'})
                # 304 时仍视为成功（页面未变更），返回特殊标记
                circuit_breaker.record_success(domain)
                return False, "304 页面未变更"

            # 403 或 5xx：指数退避重试
            if response.status_code in (403, 500, 502, 503, 504):
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.info("重试请求 HTTP %d -> 第 %d/%d 次，延迟 %.1fs",
                                response.status_code, attempt + 2, MAX_RETRIES, delay,
                                extra={'site': url, 'event': 'retry', 'status_code': response.status_code})
                    time.sleep(delay)
                    continue

            # 其他状态码：不重试，直接跳出
            break

        if response is None:
            return False, "请求未发出"

        # 记录条件请求缓存（ETag / Last-Modified）
        update_conditional_cache(url, response)

        # 检查最终 HTTP 状态码
        if response.status_code != 200:
            circuit_breaker.record_failure(domain)
            metrics.record_failure(domain)
            logger.info("HTTP 请求失败", extra={
                'site': url, 'event': 'http_error',
                'status_code': response.status_code,
            })
            return False, f"HTTP {response.status_code}"

        # 请求成功，重置熔断器
        circuit_breaker.record_success(domain)
        metrics.record_success(domain, elapsed)

        # SSRF protection: validate final URL is not internal (after redirects)
        final_url = response.url
        parsed_final = urlparse(final_url)
        hostname = parsed_final.hostname or ''
        if hostname.startswith(('127.', '10.', '172.16.', '192.168.', '169.254.', '0.', '::1', 'localhost')):
            return False, f"SSRF blocked: redirect to internal address {hostname}"

        # Response size limit (10MB)
        MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB
        content_length = response.headers.get('Content-Length')
        if content_length and int(content_length) > MAX_RESPONSE_SIZE:
            return False, f"Response too large: {content_length} bytes"
        if len(response.content) > MAX_RESPONSE_SIZE:
            return False, f"Response body too large: {len(response.content)} bytes"

        # 让 BeautifulSoup 直接用字节流自动检测编码（避免 requests 默认 ISO-8859-1 导致中文乱码）
        soup = BeautifulSoup(response.content, 'html.parser')

        # 如果 BS 没检测到编码，尝试 apparent_encoding（基于 chardet）
        if not soup.original_encoding:
            encoding = response.apparent_encoding or 'utf-8'
            if encoding.lower() in ['gb2312', 'gbk', 'gb18030']:
                encoding = 'gbk'
            content = response.content.decode(encoding, errors='ignore')
            soup = BeautifulSoup(content, 'html.parser')

        # 获取页面标题
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else url

        # === 站点专用解析器（通过注册表查找） ===
        parser_pair = _match_parser(url)

        # 特殊处理：RSS/Atom Feed
        if 'feed.iplaysoft.com' in url or url.endswith('.xml'):
            # RSS/Atom Feed：直接解析XML
            article_items = parse_rss_feed(response.content, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'ghxi.com' in url:
            # 果核剥壳特殊处理：优先 WP API，失败回退通用解析
            article_items = parse_ghxi_items(soup, url)
            if article_items:
                text = '\n'.join(item['text'] for item in article_items)
            else:
                # API 失败时回退到通用解析（SPA 可能拿不到内容）
                article_items = extract_article_items(soup, url)
                body = soup.find('body')
                text = body.get_text(separator=' ', strip=True) if body else ''
                text = ' '.join(text.split())
        elif parser_pair is not None:
            # 注册表命中：调用 items_parser 和可选的 text_parser
            items_parser, text_parser = parser_pair
            article_items = items_parser(soup, url)
            if text_parser is not None:
                text = text_parser(soup)
            else:
                text = '\n'.join(item['text'] for item in article_items)
        else:
            # 通用解析：移除干扰元素后取body文本，同时用通用条目提取器
            article_items = extract_article_items(soup, url)
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            body = soup.find('body')
            if body:
                text = body.get_text(separator=' ', strip=True)
            else:
                text = soup.get_text(separator=' ', strip=True)
            text = ' '.join(text.split())

        if not text:
            return False, "页面正文为空"

        # 生成摘要（前300个字符）
        summary = text[:300] + '...' if len(text) > 300 else text

        logger.info("爬取成功", extra={
            'site': url, 'event': 'crawl_success',
            'response_time': round(elapsed, 3),
        })

        # 返回包含标题、摘要和文章条目的字典
        return True, {
            'text': text,
            'title': title,
            'summary': summary,
            'items': article_items,
            'response_time': round(elapsed, 3)
        }

    except requests.Timeout:
        circuit_breaker.record_failure(domain)
        metrics.record_failure(domain)
        logger.info("请求超时", extra={'site': url, 'event': 'timeout'})
        return False, "请求超时"
    except requests.ConnectionError:
        circuit_breaker.record_failure(domain)
        metrics.record_failure(domain)
        logger.info("连接失败", extra={'site': url, 'event': 'connection_error'})
        return False, "连接失败"
    except requests.RequestException as e:
        circuit_breaker.record_failure(domain)
        metrics.record_failure(domain)
        logger.info("请求异常", extra={'site': url, 'event': 'request_exception'})
        return False, f"请求异常: {str(e)[:50]}"
    except Exception as e:
        circuit_breaker.record_failure(domain)
        metrics.record_failure(domain)
        logger.info("未知错误", extra={'site': url, 'event': 'unknown_error'})
        return False, f"未知错误: {str(e)[:50]}"


# Legacy sync version - use fetch_page_content_async instead
# (kept for backward compatibility and tests)


# ============================================================
# Playwright: JS 渲染抓取
# ============================================================

# 全局 Playwright 浏览器实例（延迟初始化，复用跨站点）
_pw_browser = None
_pw_playwright = None


async def _ensure_playwright_browser():
    """延迟初始化 Playwright Chromium 浏览器实例（全局复用）。"""
    global _pw_browser, _pw_playwright
    if _pw_browser is not None:
        return _pw_browser
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("playwright 未安装，请运行: pip install playwright && playwright install chromium")
    _pw_playwright = await async_playwright().start()
    _pw_browser = await _pw_playwright.chromium.launch(
        headless=True,
        args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    )
    logger.info("Playwright Chromium 浏览器已启动", extra={'event': 'playwright_init'})
    return _pw_browser


async def close_playwright():
    """关闭 Playwright 浏览器（在 main 函数结束时调用）。"""
    global _pw_browser, _pw_playwright
    if _pw_browser:
        await _pw_browser.close()
        _pw_browser = None
    if _pw_playwright:
        await _pw_playwright.stop()
        _pw_playwright = None


async def fetch_with_playwright(url: str, timeout_ms: int = 20000) -> Tuple[bool, str]:
    """使用 Playwright 抓取 JS 渲染页面。

    Returns:
        (success: bool, html_content: str)
    """
    try:
        browser = await _ensure_playwright_browser()
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
            # 等待内容加载（给动态内容一点时间）
            await page.wait_for_timeout(3000)
            html = await page.content()
            logger.info("Playwright 抓取成功: %s (%d bytes)", url, len(html),
                        extra={'site': url, 'event': 'playwright_success'})
            return True, html
        except Exception as e:
            logger.info("Playwright 抓取失败: %s - %s", url, str(e)[:100],
                        extra={'site': url, 'event': 'playwright_error'})
            return False, str(e)
        finally:
            await page.close()
            await context.close()
    except Exception as e:
        logger.info("Playwright 初始化失败: %s", str(e)[:100],
                    extra={'site': url, 'event': 'playwright_init_error'})
        return False, str(e)


def _needs_playwright(url: str) -> bool:
    """判断 URL 是否需要 Playwright JS 渲染。"""
    if not PLAYWRIGHT_AVAILABLE:
        return False
    parsed = urlparse(url)
    domain = parsed.hostname or ''
    for js_domain in JS_RENDER_SITES:
        if domain == js_domain or domain.endswith('.' + js_domain):
            return True
    return False


async def fetch_page_content_async(
    url: str,
    session: aiohttp.ClientSession,
    old_records: Dict[str, str],
) -> Tuple[bool, Any]:
    """Async version of fetch_page_content using aiohttp.

    Returns the same tuple format: (success: bool, content: dict/str).
    """
    # URL scheme validation: only allow http/https
    if not url.startswith(('http://', 'https://')):
        return False, f"Invalid URL scheme: {url[:50]}"

    parsed = urlparse(url)
    domain = parsed.hostname or parsed.netloc

    # Circuit breaker check
    if circuit_breaker.is_open(domain):
        logger.info("熔断器打开，跳过该域名", extra={'site': domain, 'event': 'circuit_breaker_open'})
        return False, "熔断器已打开（连续失败过多）"

    # robots.txt check
    if not is_allowed_by_robots(url):
        return False, "robots.txt 禁止爬取"

    # Per-domain rate limiting
    rate_limiter.wait(domain)

    # === Playwright 路径：JS 渲染站点 ===
    if _needs_playwright(url):
        logger.info("Playwright 模式抓取: %s", url, extra={'site': url, 'event': 'playwright_start'})
        start_time = time.time()
        pw_ok, pw_result = await fetch_with_playwright(url)
        elapsed = time.time() - start_time
        if not pw_ok:
            circuit_breaker.record_failure(domain)
            metrics.record_failure(domain)
            return False, f"Playwright 抓取失败: {pw_result[:80]}"

        # 解析 Playwright 返回的 HTML
        content_bytes = pw_result.encode('utf-8')
        soup = BeautifulSoup(pw_result, 'html.parser')
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else url

        parser_pair = _match_parser(url)
        if parser_pair is not None:
            items_parser, text_parser = parser_pair
            article_items = items_parser(soup, url)
            if text_parser is not None:
                text = text_parser(soup)
            else:
                text = '\n'.join(item['text'] for item in article_items)
        else:
            article_items = extract_article_items(soup, url)
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            body = soup.find('body')
            text = body.get_text(separator=' ', strip=True) if body else soup.get_text(separator=' ', strip=True)
            text = ' '.join(text.split())

        if not text:
            return False, "Playwright: 页面正文为空"

        summary = text[:300] + '...' if len(text) > 300 else text
        logger.info("Playwright 爬取成功", extra={
            'site': url, 'event': 'playwright_crawl_success',
            'response_time': round(elapsed, 3),
        })
        return True, {
            'text': text,
            'title': title,
            'summary': summary,
            'items': article_items,
            'response_time': round(elapsed, 3),
        }

    # === 普通 aiohttp 路径 ===
    profile = get_random_profile()
    headers: Dict[str, str] = {
        'User-Agent': profile['user_agent'],
        'Accept-Language': profile['accept_language'],
        'Referer': get_referer(url),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
    }
    headers.update(profile.get('fingerprint', {}))
    headers.update(get_conditional_headers(url))

    logger.info("爬取 %s", url, extra={'site': url, 'event': 'crawl_start'})

    response = None
    elapsed = 0.0
    active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None

    for attempt in range(MAX_RETRIES):
        try:
            start_time = time.time()
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=True,
                proxy=active_proxy,
            ) as resp:
                elapsed = time.time() - start_time

                # SSRF protection: check final URL after redirects
                final_host = urlparse(str(resp.url)).hostname or ''
                if final_host.startswith(('127.', '10.', '172.16.', '192.168.', '169.254.', '0.', '::1', 'localhost')):
                    return False, f"SSRF blocked: {final_host}"

                if resp.status == 200:
                    # Read response body while context manager is open
                    content_bytes = await resp.read()
                    response_headers = {k: v for k, v in resp.headers.items()}
                    response_encoding = resp.get_encoding() or 'utf-8'
                    response_status = resp.status
                    response = SimpleNamespace(
                        status=response_status,
                        headers=response_headers,
                        content=content_bytes,
                        encoding=response_encoding,
                    )
                    if active_proxy and _proxy_pool:
                        _proxy_pool.report_success(active_proxy)
                    break
                elif resp.status == 304:
                    circuit_breaker.record_success(domain)
                    if active_proxy and _proxy_pool:
                        _proxy_pool.report_success(active_proxy)
                    return False, "304 页面未变更"
                elif resp.status in (403, 500, 502, 503, 504):
                    if active_proxy and _proxy_pool:
                        _proxy_pool.report_failure(active_proxy)
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                        logger.info("重试请求 HTTP %d -> 第 %d/%d 次，延迟 %.1fs",
                                    resp.status, attempt + 2, MAX_RETRIES, delay,
                                    extra={'site': url, 'event': 'retry', 'status_code': resp.status})
                        await asyncio.sleep(delay)
                        # Rotate profile and proxy for retry
                        profile = get_random_profile()
                        headers['User-Agent'] = profile['user_agent']
                        headers['Accept-Language'] = profile['accept_language']
                        headers.update(profile.get('fingerprint', {}))
                        active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
                        continue
                else:
                    # Other status codes: don't retry
                    response_status = resp.status
                    content_bytes = await resp.read()
                    response_headers = {k: v for k, v in resp.headers.items()}
                    response = SimpleNamespace(
                        status=response_status,
                        headers=response_headers,
                        content=content_bytes,
                        encoding=resp.get_encoding() or 'utf-8',
                    )
                    break

        except asyncio.TimeoutError:
            circuit_breaker.record_failure(domain)
            metrics.record_failure(domain)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                await asyncio.sleep(delay)
                active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
                continue
            logger.info("请求超时", extra={'site': url, 'event': 'timeout'})
            return False, "请求超时"
        except aiohttp.ClientError as e:
            circuit_breaker.record_failure(domain)
            metrics.record_failure(domain)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            logger.info("连接失败", extra={'site': url, 'event': 'connection_error'})
            return False, f"连接失败: {str(e)[:50]}"
        except Exception as e:
            circuit_breaker.record_failure(domain)
            metrics.record_failure(domain)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            return False, f"请求异常: {str(e)[:50]}"

    if response is None:
        return False, "请求未发出"

    # Response size limit (10MB)
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024
    content_length = response.headers.get('Content-Length')
    if content_length and int(content_length) > MAX_RESPONSE_SIZE:
        return False, f"Response too large: {content_length} bytes"
    if len(response.content) > MAX_RESPONSE_SIZE:
        return False, f"Response body too large: {len(response.content)} bytes"

    # Update conditional cache using SimpleNamespace wrapper
    update_conditional_cache(url, SimpleNamespace(headers=response.headers))

    if response.status != 200:
        circuit_breaker.record_failure(domain)
        metrics.record_failure(domain)
        logger.info("HTTP 请求失败", extra={
            'site': url, 'event': 'http_error',
            'status_code': response.status,
        })
        return False, f"HTTP {response.status}"

    circuit_breaker.record_success(domain)
    metrics.record_success(domain, elapsed)

    # Parse HTML with BeautifulSoup
    soup = BeautifulSoup(response.content, 'html.parser')
    if not soup.original_encoding:
        encoding = response.encoding or 'utf-8'
        if encoding.lower() in ['gb2312', 'gbk', 'gb18030']:
            encoding = 'gbk'
        content = response.content.decode(encoding, errors='ignore')
        soup = BeautifulSoup(content, 'html.parser')

    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else url

    # === Site-specific parser dispatch (same logic as sync version) ===
    parser_pair = _match_parser(url)

    # Special handling: RSS/Atom Feed
    if 'feed.iplaysoft.com' in url or url.endswith('.xml'):
        article_items = parse_rss_feed(response.content, url)
        text = '\n'.join(item['text'] for item in article_items)
    elif 'ghxi.com' in url:
        # ghxi special: prefer WP API, fallback to generic
        article_items = parse_ghxi_items(soup, url)
        if article_items:
            text = '\n'.join(item['text'] for item in article_items)
        else:
            article_items = extract_article_items(soup, url)
            body = soup.find('body')
            text = body.get_text(separator=' ', strip=True) if body else ''
            text = ' '.join(text.split())
    elif parser_pair is not None:
        items_parser, text_parser = parser_pair
        article_items = items_parser(soup, url)
        if text_parser is not None:
            text = text_parser(soup)
        else:
            text = '\n'.join(item['text'] for item in article_items)
    else:
        # Generic extraction
        article_items = extract_article_items(soup, url)
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        body = soup.find('body')
        if body:
            text = body.get_text(separator=' ', strip=True)
        else:
            text = soup.get_text(separator=' ', strip=True)
        text = ' '.join(text.split())

    if not text:
        return False, "页面正文为空"

    summary = text[:300] + '...' if len(text) > 300 else text

    logger.info("爬取成功", extra={
        'site': url, 'event': 'crawl_success',
        'response_time': round(elapsed, 3),
    })

    return True, {
        'text': text,
        'title': title,
        'summary': summary,
        'items': article_items,
        'response_time': round(elapsed, 3),
    }


def check_site_update(url: str, old_records: Dict[str, str]) -> Tuple[Optional[bool], Optional[str], str, Optional[Dict[str, Any]]]:
    """
    检查单个站点是否有更新
    返回：(是否更新, 新哈希值, 错误信息, 页面信息)
    """
    success, result = fetch_page_content(url)

    if not success:
        return None, None, result, None  # 爬取失败

    # result现在是一个字典
    text = result['text']
    page_info = {
        'url': url,
        'title': result['title'],
        'summary': result['summary'],
        'items': result['items']
    }

    # Hash the article items list (titles+urls) instead of full body text.
    # This prevents false positives from timestamps, ads, or dynamic widgets.
    article_items = result.get('items', [])
    if article_items:
        items_text = json.dumps([{'t': item['text'], 'u': item['url']} for item in article_items],
                                ensure_ascii=False, sort_keys=True)
        new_hash = calculate_md5(items_text)
    else:
        new_hash = calculate_md5(text)
    old_hash = old_records.get(url)

    if old_hash is None:
        # 首次监控，记录哈希但不视为更新
        return False, new_hash, "首次监控", page_info
    elif old_hash != new_hash:
        # 检测到更新
        return True, new_hash, "内容已更新", page_info
    else:
        # 无更新
        return False, new_hash, "无更新", page_info


async def check_site_update_async(
    url: str,
    old_records: Dict[str, str],
    session: aiohttp.ClientSession,
) -> Tuple[Optional[bool], Optional[str], str, Optional[Dict[str, Any]]]:
    """Async version of check_site_update."""
    success, result = await fetch_page_content_async(url, session, old_records)

    if not success:
        return None, None, result, None

    text = result['text']
    page_info = {
        'url': url,
        'title': result['title'],
        'summary': result['summary'],
        'items': result['items'],
    }

    article_items = result.get('items', [])
    if article_items:
        items_text = json.dumps([{'t': item['text'], 'u': item['url']} for item in article_items],
                                ensure_ascii=False, sort_keys=True)
        new_hash = calculate_md5(items_text)
    else:
        new_hash = calculate_md5(text)
    old_hash = old_records.get(url)

    if old_hash is None:
        return False, new_hash, "首次监控", page_info
    elif old_hash != new_hash:
        return True, new_hash, "内容已更新", page_info
    else:
        return False, new_hash, "无更新", page_info


def git_commit_if_changed() -> bool:
    """
    检查是否有变更，仅在有变更时执行commit & push
    变更条件：哈希文件修改

    注意：此函数在GitHub Actions环境中会跳过git操作，
    """
    # 检查是否在GitHub Actions环境中
    if os.getenv('GITHUB_ACTIONS') == 'true':
        logger.info("在GitHub Actions环境中，跳过脚本内git操作", extra={'event': 'git'})
        logger.info("变更将由workflow的提交步骤处理", extra={'event': 'git'})
        return False

    try:
        # 检查工作区状态
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=10
        )

        changes = result.stdout.strip()
        if not changes:
            logger.info("无变更，跳过提交", extra={'event': 'git'})
            return False

        # 有变更，执行提交
        now = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
        commit_msg = f"站点更新检测 - {now}"

        # Git add 特定文件（避免 git add -A 在 Windows 上索引 nul 等设备名文件）
        TRACKED_FILES = [
            'items.json', 'hash_record.txt', 'monitor.db',
            'notified_items.json', 'run_log.jsonl', '.gitignore',
        ]
        for f in TRACKED_FILES:
            fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
            if os.path.exists(fpath):
                subprocess.run(['git', 'add', f], check=True, timeout=30)

        # Git commit
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True, timeout=30)

        # Git pull --rebase 再 push（避免远程有更新的推送冲突）
        try:
            subprocess.run(['git', 'pull', '--rebase'], check=True, timeout=60)
        except subprocess.CalledProcessError:
            logger.warning("pull --rebase 失败，尝试直接 push", extra={'event': 'git'})
        subprocess.run(['git', 'push'], check=True, timeout=60)

        logger.info("提交成功: %s", commit_msg, extra={'event': 'git'})
        return True

    except subprocess.CalledProcessError as e:
        logger.error("提交失败: %s", e, extra={'event': 'git'})
        return False
    except Exception as e:
        logger.error("Git异常: %s", e, extra={'event': 'git'})
        return False


# ============================================================
# 运行日志管理
# ============================================================

def load_run_log() -> List[Dict[str, Any]]:
    """加载历史运行日志"""
    log: List[Dict[str, Any]] = []
    if os.path.exists(RUN_LOG_FILE):
        try:
            with open(RUN_LOG_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        log.append(json.loads(line))
        except Exception:
            pass
    return log


def append_run_log(entry: Dict[str, Any]) -> None:
    """追加一条运行日志（原子写入，保留最近 30 条）"""
    tmp_file = RUN_LOG_FILE + '.tmp'
    try:
        # Load existing entries to support rotation
        log = load_run_log()
        log.append(entry)
        # Keep only last 30 entries
        if len(log) > 30:
            log = log[-30:]
        with open(tmp_file, 'w', encoding='utf-8') as f:
            for e in log:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
        os.replace(tmp_file, RUN_LOG_FILE)
    except Exception as e:
        logger.warning("运行日志写入失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def analyze_and_fix(run_result: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    运行后自分析 + 自动修复
    run_result: {'success': N, 'error': N, 'updated': N, 'total': N, 'errors': [...], 'updated_sites': [...]}
    """
    logger.info("运行后分析", extra={'event': 'self_check'})
    issues_found: List[Dict[str, str]] = []

    # 1. 检查失败站点
    if run_result['errors']:
        for err in run_result['errors']:
            url = err['url']
            msg = err['message']

            # 403 封锁 -> 建议增加延迟
            if '403' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': 'HTTP 403 被封锁',
                    'action': '已记录，建议该站点增加请求延迟或更换 User-Agent'
                })

            # 404 页面不存在 -> 建议移除
            elif '404' in msg:
                issues_found.append({
                    'level': 'error',
                    'site': url,
                    'issue': 'HTTP 404 页面不存在',
                    'action': '建议从 MONITOR_SITES 中移除该站点'
                })

            # 页面正文为空 -> JS 渲染问题
            elif '页面正文为空' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '页面正文为空（可能是JS渲染）',
                    'action': '该站点可能需要 Playwright 才能抓取，暂时保留观察'
                })

            # 超时
            elif '超时' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '请求超时',
                    'action': '网络问题，下轮重试'
                })

            # 连接失败
            elif '连接失败' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '连接失败',
                    'action': '站点可能已下线，连续3轮失败后将建议移除'
                })

    # 2. 检查失败率
    error_rate = run_result['error'] / run_result['total'] if run_result['total'] > 0 else 0
    if error_rate > 0.1:
        issues_found.append({
            'level': 'error',
            'site': '全局',
            'issue': f'失败率 {error_rate:.0%} 超过 10%',
            'action': '检查网络环境或 GitHub Actions 运行时'
        })

    # 3. 更新率异常检测（>80% 站点更新可能是哈希过于敏感）
    if run_result['total'] > 0:
        update_rate = run_result['updated'] / run_result['total']
        if update_rate > 0.8:
            issues_found.append({
                'level': 'info',
                'site': '全局',
                'issue': f'更新率 {update_rate:.0%} 异常高，可能是首次运行或哈希过于敏感',
                'action': '观察下轮结果，如持续高更新率可考虑过滤动态内容'
            })

    # 4. 检查历史趋势：连续3轮失败的站点（仅取交集，排除偶发失败）
    run_log = load_run_log()
    if len(run_log) >= 3:
        # 排除 robots.txt 拒绝（不算真正失败）
        def _real_errors(errors: list) -> Set[str]:
            return {
                e['url'] for e in errors
                if 'robots.txt' not in e.get('message', '')
            }
        error_sets = [_real_errors(entry.get('errors', [])) for entry in run_log[-3:]]
        # 取交集：只有在最近 3 轮都失败的站点才报警
        consecutive_failures = error_sets[0] & error_sets[1] & error_sets[2]

        for url in consecutive_failures:
            issues_found.append({
                'level': 'error',
                'site': url,
                'issue': '连续3轮巡检均失败',
                'action': '建议从 MONITOR_SITES 中移除'
            })

    # 打印分析报告
    if issues_found:
        logger.info("自检发现 %d 个问题", len(issues_found), extra={'event': 'self_check'})
        for i, issue in enumerate(issues_found, 1):
            logger.info("  %d. [%s] 问题: %s | 建议: %s",
                        i, issue['site'], issue['issue'], issue['action'],
                        extra={'event': 'self_check'})
    else:
        logger.info("本轮运行健康，无异常问题", extra={'event': 'self_check'})

    return issues_found


# ============================================================
# 暂停站点管理（自动移除/恢复连续失败站点）
# ============================================================

def load_paused_sites() -> Dict[str, Any]:
    """加载被暂停的站点 {url: {'paused_at': '...', 'reason': '...', 'fail_count': N}}"""
    if os.path.exists(PAUSED_SITES_FILE):
        try:
            with open(PAUSED_SITES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_paused_sites(paused: Dict[str, Any]) -> None:
    """保存暂停站点（原子写入）"""
    tmp_file = PAUSED_SITES_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(paused, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, PAUSED_SITES_FILE)
    except Exception as e:
        logger.warning("暂停站点保存失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def main() -> None:
    """Legacy sync main - kept for backward compatibility. Use main() (async) instead."""
    logger.info("GitHub Actions 多站点更新监控系统 v2.0 (legacy sync mode)")
    # Delegate to async version
    asyncio.run(main_async())


async def check_one_async(
    url: str,
    idx: int,
    session: aiohttp.ClientSession,
    old_records: Dict[str, str],
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """Check one site asynchronously."""
    async with semaphore:
        # Random delay before request (anti-ban)
        await asyncio.sleep(get_random_delay())

        # Graceful shutdown check
        if _shutdown_requested:
            return {
                'url': url, 'title': url, 'summary': '', 'items': [],
                'status': 'skipped', 'message': '优雅退出',
                'is_updated': None, 'new_hash': None, 'page_info': None,
            }

        # 死站黑名单检查
        dead_reason = is_dead_site(url)
        if dead_reason:
            logger.info("跳过死站: %s (%s)", url, dead_reason,
                        extra={'site': url, 'event': 'dead_site_skip'})
            return {
                'url': url, 'title': url, 'summary': '', 'items': [],
                'status': 'dead', 'message': f'死站: {dead_reason}',
                'is_updated': None, 'new_hash': None, 'page_info': None,
            }

        is_updated, new_hash, message, page_info = await check_site_update_async(
            url, old_records, session
        )

        if is_updated is None:
            is_robots_denied = 'robots.txt' in (message or '')
            return {
                'url': url, 'title': url, 'summary': '', 'items': [],
                'status': 'robots_denied' if is_robots_denied else 'error',
                'message': message, 'is_updated': None, 'new_hash': None, 'page_info': None,
            }
        return {
            'url': url,
            'title': page_info.get('title', url) if page_info else url,
            'summary': page_info.get('summary', '') if page_info else '',
            'items': page_info.get('items', []) if page_info else [],
            'status': 'updated' if is_updated else ('first' if url not in old_records else 'no_update'),
            'message': message,
            'is_updated': is_updated,
            'new_hash': new_hash,
            'page_info': page_info,
        }


async def main_async() -> None:
    """主监控流程 (async version using aiohttp + SQLite)."""
    global _proxy_pool
    logger.info("GitHub Actions 多站点更新监控系统 v3.0 (async)")

    # 获取当前时间和轮次
    now = get_beijing_time()
    round_num = get_current_round()
    check_time = now.strftime('%Y-%m-%d %H:%M:%S')

    logger.info("北京时间: %s", check_time)
    logger.info("当日第 %d 轮巡检", round_num)

    # 初始化代理池（从环境变量 / 配置文件加载，无可用代理则直连）
    _proxy_pool = create_proxy_pool()
    if _proxy_pool.total_count > 0:
        logger.info("代理池已加载: %d 个代理 (%d 活跃)",
                    _proxy_pool.total_count, _proxy_pool.active_count)
    else:
        logger.info("代理池为空，使用直连模式 (设置 PROXY_LIST 环境变量启用代理)")

    # 加载黑名单
    blacklist_domains: List[str] = load_blacklist()

    # 过滤黑名单站点
    filtered_by_blacklist = [url for url in MONITOR_SITES if is_blacklisted(url, blacklist_domains)]
    monitor_sites = [url for url in MONITOR_SITES if not is_blacklisted(url, blacklist_domains)]
    if filtered_by_blacklist:
        logger.info("黑名单过滤 %d 个站点: %s", len(filtered_by_blacklist), ', '.join(filtered_by_blacklist))

    # 加载暂停站点
    paused = load_paused_sites()
    paused_urls = set(paused.keys())

    # 实际监控列表
    active_sites = [upgrade_to_https(url) for url in monitor_sites if url not in paused_urls]
    logger.info("监控站点数: %d (活跃) + %d (黑名单) + %d (暂停)",
                len(active_sites), len(blacklist_domains), len(paused_urls))
    if paused_urls:
        logger.info("暂停站点: %s", ', '.join(paused_urls))

    # 加载已通知过的条目URL（去重用）
    notified = load_notified_items()
    logger.info("已加载历史条目: %d 条", len(notified.get('items', [])))

    # Run log rotation: keep only the last 30 entries
    run_log = load_run_log()
    if len(run_log) > 30:
        run_log = run_log[-30:]
        tmp_file = RUN_LOG_FILE + '.tmp'
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                for entry in run_log:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
            os.replace(tmp_file, RUN_LOG_FILE)
        except Exception:
            pass

    # Shuffle site order to avoid deterministic crawl patterns
    random.shuffle(active_sites)

    # Initialize SQLite and load hash records
    db_conn = init_sqlite()
    old_records = sqlite_load_hash_records(db_conn)
    logger.info("已加载哈希记录 (SQLite): %d 条", len(old_records))

    # Create aiohttp session with connection pooling (ssl=False: 全局跳过SSL验证，不惜代价抓内容)
    connector = aiohttp.TCPConnector(
        limit=10,
        limit_per_host=2,
        ttl_dns_cache=300,
        ssl=False,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        # Concurrent crawling with semaphore
        semaphore = asyncio.Semaphore(6)
        tasks = [
            check_one_async(url, idx, session, old_records, semaphore)
            for idx, url in enumerate(active_sites, 1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    all_site_results: List[Dict[str, Any]] = []
    new_records = old_records.copy()
    success_count = 0
    error_count = 0
    updated_count = 0
    robots_denied_count = 0

    for idx, result in enumerate(results, 1):
        url = active_sites[idx - 1]

        # Handle exceptions from gather
        if isinstance(result, Exception):
            error_count += 1
            all_site_results.append({
                'url': url, 'title': url, 'summary': '',
                'status': 'error', 'message': str(result)[:80],
            })
            continue

        if _shutdown_requested:
            logger.info("优雅退出：跳过剩余站点")
            break

        if result['status'] == 'robots_denied':
            logger.info("[%d/%d] robots.txt 拒绝: %s", idx, len(active_sites), url,
                        extra={'site': url, 'event': 'crawl_result'})
            robots_denied_count += 1
        elif result['status'] == 'dead':
            logger.info("[%d/%d] 死站: %s - %s", idx, len(active_sites), url, result['message'],
                        extra={'site': url, 'event': 'dead_site'})
        elif result['is_updated'] is None:
            logger.info("[%d/%d] 失败: %s", idx, len(active_sites), result['message'],
                        extra={'site': url, 'event': 'crawl_result'})
            error_count += 1
        else:
            if result['is_updated']:
                logger.info("[%d/%d] 更新: %s", idx, len(active_sites), result['message'],
                            extra={'site': url, 'event': 'crawl_result'})
                updated_count += 1
            else:
                logger.info("[%d/%d] 正常: %s", idx, len(active_sites), result['message'],
                            extra={'site': url, 'event': 'crawl_result'})
            success_count += 1
            new_records[result['url']] = result['new_hash']
        all_site_results.append({
            'url': result['url'],
            'title': result['title'],
            'summary': result['summary'],
            'items': result['items'],
            'status': result['status'],
            'message': result['message'],
        })

    # 添加暂停站点到结果列表
    for url in paused_urls:
        all_site_results.append({
            'url': url,
            'title': url,
            'summary': '',
            'status': 'paused',
            'message': paused[url].get('reason', '已暂停'),
        })

    total_count = len(all_site_results)

    # 统计死站数
    dead_count = sum(1 for r in all_site_results if r.get('status') == 'dead')

    logger.info("成功: %d | 失败: %d | 死站: %d | robots.txt跳过: %d | 暂停: %d",
                success_count, error_count, dead_count, robots_denied_count, len(paused_urls))
    logger.info("更新站点: %d 个", updated_count)

    # 输出运行指标摘要
    metrics_summary = metrics.get_summary()
    logger.info("总请求: %d | 成功: %d | 失败: %d",
                metrics_summary['total_requests'], metrics_summary['success_count'], metrics_summary['fail_count'])

    # 输出熔断器状态
    cb_status = circuit_breaker.get_status()
    open_circuits = {d: c for d, c in cb_status.items() if c >= MAX_CONSECUTIVE_FAILURES}
    if open_circuits:
        logger.warning("已熔断域名: %s", ', '.join(open_circuits.keys()))

    # Save hash records to SQLite
    sqlite_save_hash_records(db_conn, new_records)

    # Also save to JSON file for backward compat
    save_hash_records(new_records)

    # 构建完整条目字典（包含更新和首次爬取的站点）
    new_item_list: List[Dict[str, str]] = []
    for r in all_site_results:
        if r['status'] in ('updated', 'first'):
            for item in r.get('items', []):
                item_url = item['url'] if isinstance(item, dict) else item
                item_text = item['text'] if isinstance(item, dict) else str(item)
                if item_url and not item_url.startswith('javascript:'):
                    src_name = get_source_name(r.get('url', '')) or r.get('title', r['url'])
                    new_item_list.append({
                        'url': item_url,
                        'text': item_text,
                        'source': src_name,
                        'time': check_time,
                    })
    save_notified_items({
        'items': new_item_list,
        'updated_at': check_time,
    })

    # Insert new items to SQLite
    if new_item_list:
        added = sqlite_insert_items(db_conn, new_item_list, check_time)
        logger.info("SQLite 新增 %d 条线报", added)

    # Export items.json for frontend SPA
    sqlite_export_json(db_conn)

    # 计算本轮新增URL数
    existing_urls_set = set(item['url'] for item in (notified.get('items', []) if isinstance(notified, dict) else []))
    new_urls = set(item['url'] for item in new_item_list if item['url'] not in existing_urls_set)
    logger.info("本轮新通知条目: %d 条", len(new_urls))

    # Git提交
    git_commit_if_changed()

    # ===== 运行后自分析 =====
    errors_detail = [{'url': r['url'], 'message': r.get('message', '')} for r in all_site_results if r['status'] == 'error']
    updated_sites = [r['url'] for r in all_site_results if r['status'] == 'updated']

    # 记录本轮运行日志
    run_entry = {
        'round': round_num,
        'check_time': check_time,
        'total': total_count,
        'active': len(active_sites),
        'success': success_count,
        'error': error_count,
        'robots_denied': robots_denied_count,
        'updated': updated_count,
        'paused': len(paused_urls),
        'new_items': len(new_urls),
        'errors': errors_detail,
        'updated_sites': updated_sites,
        'metrics': metrics_summary,
    }
    append_run_log(run_entry)

    # 自分析 + 建议
    issues = analyze_and_fix({
        'success': success_count,
        'error': error_count,
        'updated': updated_count,
        'total': total_count,
        'errors': errors_detail,
        'updated_sites': updated_sites,
    })

    # Close Playwright browser
    await close_playwright()

    # Close SQLite connection
    db_conn.close()

    logger.info("本轮巡检结束")


# ============================================================
# Graceful shutdown
# ============================================================
import signal

_shutdown_requested = False

def _handle_signal(signum, frame):
    global _shutdown_requested
    if _shutdown_requested:
        logger.warning("强制退出（数据可能未完全保存）")
        sys.exit(1)
    _shutdown_requested = True
    logger.info("收到停止信号，将在当前任务完成后优雅退出")

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("用户手动停止")
        sys.exit(0)
    except Exception as e:
        logger.error("致命错误: %s", e)
        import traceback
        traceback.print_exc()
        sys.exit(1)
