# -*- coding: utf-8 -*-
"""Crawler network layer: session pool, metrics, conditional requests, robots.txt."""

import logging
import random
import threading
import time
from urllib.robotparser import RobotFileParser
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from crawler.config import RESPECT_ROBOTS_TXT

import requests

from common import DomainRateLimiter

logger = logging.getLogger('crawl')

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

