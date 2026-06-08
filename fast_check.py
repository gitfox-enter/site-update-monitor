#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速增量检查器 - 高频抓取 top 站点，追加新线报到 SQLite (monitor.db)
设计目标：30-60 秒内完成，适合 GitHub Actions 每 5 分钟运行一次

增强功能:
  - aiohttp 异步并发抓取 (Semaphore 限流)
  - 指数退避重试 (3 次: 1s, 2s, 4s)
  - SQLite 持久化 (替代 items.json 读写)
  - 结构化日志 (Python logging)
  - 完整类型标注
  - robots.txt 合规检查 (按域名缓存)
  - Referer 头 (使用站点首页)
  - 指标追踪 (请求数、成功/失败、平均响应时间)
  - 输入清洗 (javascript: 过滤、文本净化)
"""

# ============================================================
# 1. 所有导入集中在顶部
# ============================================================

import asyncio
import aiohttp
import os
import re
import sys
import time
import json
import random
import logging
import subprocess
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from common import (
    JsonFormatter,
    get_beijing_time,
    auto_categorize,
    sanitize_href,
    sanitize_text,
    is_junk,
    init_sqlite,
    sqlite_insert_items,
    sqlite_get_recent_items,
    sqlite_get_existing_urls,
    sqlite_export_json,
    SQLITE_DB_FILE,
    MAX_ITEMS_DB,
    ProxyPool,
    create_proxy_pool,
)

# ============================================================
# 4. 结构化日志
# ============================================================

logger = logging.getLogger("fast_check")
logger.setLevel(logging.DEBUG)

_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.INFO)
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)

# ============================================================
# 配置
# ============================================================

FAST_LOG_FILE: str = "fast_log.jsonl"

# 代理池（初始化后全局可用，None 表示直连模式）
_proxy_pool: Optional[ProxyPool] = None

# 高频检查站点（按活跃度排序的 top 12）
FAST_SITES: List[Dict[str, str]] = [
    {"url": "https://www.zhuanyes.com/xianbao/", "name": "专业线报"},
    {"url": "https://news.ixbk.net/", "name": "线报酷"},
    {"url": "https://news.ixbk.fun/", "name": "线报酷"},
    {"url": "http://www.0818tuan.com/", "name": "0818团"},
    {"url": "https://www.huifabu.cn/", "name": "汇发部"},
    {"url": "https://cjx8.com/", "name": "超级线报"},
    {"url": "https://xianbao.icu/", "name": "线报ICU"},
    {"url": "https://www.baicaio.com/", "name": "白菜哦"},
    {"url": "https://www.iqnew.com/", "name": "爱Q社区"},
    {"url": "https://www.51kanong.com/", "name": "51卡农"},
    {"url": "https://v1.xianbao.net/", "name": "线报网"},
    {"url": "http://www.xiaodigu.com/", "name": "小嘀咕"},
]

# 爬虫配置
REQUEST_TIMEOUT: int = 10
MAX_RETRIES: int = 3                       # 3 次尝试
RETRY_BACKOFF_BASE: float = 1.0            # 退避基数 (秒): 1, 2, 4
RESPECT_ROBOTS_TXT: bool = False            # robots.txt 合规开关（线报站 robots.txt 通常过严，个人监控建议关闭）

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


def get_random_profile() -> Dict[str, Any]:
    """随机返回一组一致的浏览器配置（UA + 指纹 + 语言匹配）"""
    return random.choice(BROWSER_PROFILES)


# ============================================================
# 6. robots.txt 缓存 (按域名)
# ============================================================

_robots_cache: Dict[str, RobotFileParser] = {}
_robots_fetch_failures: Set[str] = set()  # 获取失败的域名，默认允许


def _get_robots_parser(scheme_host: str) -> RobotFileParser:
    """获取或创建指定域名的 robots.txt 解析器（带缓存）"""
    if scheme_host in _robots_cache:
        return _robots_cache[scheme_host]

    rp = RobotFileParser()
    robots_url = f"{scheme_host}/robots.txt"
    try:
        # 使用 urllib 抓取 robots.txt（同步、简单、一次性调用）
        req = urllib.request.Request(robots_url, headers={"User-Agent": "*"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                rp.parse(body.splitlines())
            elif resp.status in (401, 403):
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                _robots_fetch_failures.add(scheme_host)
    except Exception:
        _robots_fetch_failures.add(scheme_host)
        # 获取失败时默认允许抓取（宽容策略）
    _robots_cache[scheme_host] = rp
    return rp


def is_allowed_by_robots(url: str, user_agent: str = "*") -> bool:
    """检查 URL 是否被 robots.txt 允许抓取"""
    if not RESPECT_ROBOTS_TXT:
        return True

    parsed = urlparse(url)
    scheme_host = f"{parsed.scheme}://{parsed.netloc}"

    if scheme_host in _robots_fetch_failures:
        return True  # robots.txt 不可达时默认允许

    rp = _get_robots_parser(scheme_host)
    return rp.can_fetch(user_agent, url)


# ============================================================
# 8. 指标追踪
# ============================================================

class Metrics:
    """简单的运行时指标收集器"""

    def __init__(self) -> None:
        self.request_count: int = 0
        self.success_count: int = 0
        self.fail_count: int = 0
        self._total_response_time: float = 0.0

    def record_success(self, elapsed: float) -> None:
        self.request_count += 1
        self.success_count += 1
        self._total_response_time += elapsed

    def record_failure(self, elapsed: float = 0.0) -> None:
        self.request_count += 1
        self.fail_count += 1
        self._total_response_time += elapsed

    @property
    def avg_response_time(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self._total_response_time / self.request_count

    def summary(self) -> str:
        return (
            f"请求 {self.request_count} | "
            f"成功 {self.success_count} | "
            f"失败 {self.fail_count} | "
            f"平均响应 {self.avg_response_time:.2f}s"
        )


metrics = Metrics()


# ============================================================
# 3. 指数退避重试 (async) + aiohttp
# ============================================================

async def _fetch_with_retry_async(
    session: aiohttp.ClientSession,
    url: str,
    headers: Dict[str, str],
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> Tuple[Optional[aiohttp.ClientResponse], Optional[bytes]]:
    """
    Async exponential backoff fetch using aiohttp with proxy pool support.
    Attempts: max_retries (default 3), backoff: 1s, 2s, 4s.
    Returns (response, body) on completion, or (None, None) on total failure.
    """
    last_exc: Optional[Exception] = None
    active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
    for attempt in range(max_retries):
        start = time.monotonic()
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
                proxy=active_proxy,
            ) as resp:
                elapsed = time.monotonic() - start
                if resp.status == 200:
                    # Read the body before returning (aiohttp needs this within context)
                    body = await resp.read()
                    metrics.record_success(elapsed)
                    if active_proxy and _proxy_pool:
                        _proxy_pool.report_success(active_proxy)
                    return resp, body
                # Server error: retryable
                if resp.status >= 500 and attempt < max_retries - 1:
                    backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                    if active_proxy and _proxy_pool:
                        _proxy_pool.report_failure(active_proxy)
                    logger.debug(
                        "  %s 返回 HTTP %d，%.1fs 后重试 (%d/%d)",
                        url, resp.status, backoff, attempt + 1, max_retries,
                    )
                    metrics.record_failure(elapsed)
                    await asyncio.sleep(backoff)
                    active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
                    continue
                # Client error (4xx): not retryable
                metrics.record_failure(elapsed)
                if active_proxy and _proxy_pool:
                    _proxy_pool.report_failure(active_proxy)
                body = await resp.read()
                return resp, body
        except asyncio.TimeoutError as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            metrics.record_failure(elapsed)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            if attempt < max_retries - 1:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(
                    "  %s 超时，%.1fs 后重试 (%d/%d)",
                    url, backoff, attempt + 1, max_retries,
                )
                await asyncio.sleep(backoff)
                active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
        except aiohttp.ClientError as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            metrics.record_failure(elapsed)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            if attempt < max_retries - 1:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(
                    "  %s 请求异常 (%s)，%.1fs 后重试 (%d/%d)",
                    url, type(exc).__name__, backoff, attempt + 1, max_retries,
                )
                await asyncio.sleep(backoff)
                active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None

    # All retries exhausted
    logger.warning("  %s 全部 %d 次重试失败: %s", url, max_retries, last_exc)
    return None, None


# ============================================================
# 抓取 & 解析 (async)
# ============================================================

async def fetch_and_extract_async(
    site: Dict[str, str],
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> Tuple[str, str, List[Dict[str, Any]], Optional[str]]:
    """Async fetch and extract items from a site."""
    async with semaphore:
        await asyncio.sleep(random.uniform(0.3, 0.8))  # Inter-request delay

        url = site["url"]
        name = site["name"]
        profile = get_random_profile()

        # robots.txt check
        if not is_allowed_by_robots(url):
            logger.info("  [robots.txt 拒绝] %s: %s", name, url)
            return name, url, [], "robots.txt 拒绝"

        # Referer header - use site homepage
        parsed_url = urlparse(url)
        referer = f"{parsed_url.scheme}://{parsed_url.netloc}/"

        headers: Dict[str, str] = {
            "User-Agent": profile['user_agent'],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": profile['accept_language'],
            "Accept-Encoding": "gzip, deflate",
            "Referer": referer,
        }
        headers.update(profile.get('fingerprint', {}))

        result = await _fetch_with_retry_async(session, url, headers)
        if result[0] is None:
            return name, url, [], f"重试 {MAX_RETRIES} 次后仍失败"

        resp, body = result

        # SSRF Protection - block private/internal IP addresses
        final_host = urlparse(str(resp.url)).hostname or ''
        if final_host.startswith(('127.', '10.', '172.16.', '192.168.', '169.254.', '0.')):
            return name, url, [], f"SSRF blocked: {final_host}"

        if resp.status != 200:
            return name, url, [], f"HTTP {resp.status}"

        # Response size limit - 10MB
        if len(body) > 10 * 1024 * 1024:
            return name, url, [], "Response too large"

        try:
            # Parse HTML
            soup = BeautifulSoup(body, 'html.parser')

            # Remove noise elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
                tag.decompose()

            body_tag = soup.find("body")
            if not body_tag:
                return name, url, [], "无 body"

            items: List[Dict[str, Any]] = []
            seen: Set[str] = set()

            # Extract <a> tag link entries
            for a_tag in body_tag.find_all("a", href=True):
                raw_text: str = a_tag.get_text(strip=True)
                if not raw_text:
                    continue

                # Text sanitization
                text = sanitize_text(raw_text)
                if not text or is_junk(text):
                    continue
                if len(text) > 120:
                    continue
                if text in seen:
                    continue

                # href sanitization (javascript: etc.)
                raw_href: str = a_tag["href"].strip()
                href = sanitize_href(raw_href)
                if not href:
                    continue
                if href.startswith("#"):
                    continue
                if href.startswith("/") or not href.startswith("http"):
                    href = urljoin(url, href)

                seen.add(text)
                items.append({
                    "url": href,
                    "text": text,
                    "source": name,
                    "time": get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"),
                    "category": auto_categorize(text),
                })

            return name, url, items, None

        except Exception as e:
            return name, url, [], str(e)[:80]


# ============================================================
# 主流程 (async)
# ============================================================

async def main() -> None:
    global _proxy_pool
    logger.info("=" * 50)
    logger.info("[快速检查] 开始 %s", get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 50)

    # 初始化代理池（从环境变量 / 配置文件加载，无可用代理则直连）
    _proxy_pool = create_proxy_pool()
    if _proxy_pool.total_count > 0:
        logger.info("[代理池] 已加载 %d 个代理 (%d 活跃)",
                    _proxy_pool.total_count, _proxy_pool.active_count)
    else:
        logger.info("[代理池] 无可用代理，使用直连模式")

    # 1. Git pull to get latest data
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "--strategy-option=theirs", "origin", "main"],
            capture_output=True,
            timeout=30,
        )
        logger.info("[Git] 已拉取最新数据")
    except Exception as e:
        logger.warning("[Git] 拉取失败（继续）: %s", e)

    # 2. Initialize SQLite and load existing URLs
    db_conn = init_sqlite()
    existing_urls: Set[str] = sqlite_get_existing_urls(db_conn)
    logger.info("[数据] 现有 %d 条线报", len(existing_urls))

    # 3. Concurrent async fetch
    connector = aiohttp.TCPConnector(limit=12, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(4)
        tasks = [
            fetch_and_extract_async(site, session, semaphore)
            for site in FAST_SITES
        ]
        results = await asyncio.gather(*tasks)

    # 4. Process results
    all_new_items: List[Dict[str, Any]] = []
    for name, url, items, error in results:
        if error:
            logger.warning("  [失败] %s: %s", name, error)
            continue

        # Filter out already-existing items
        fresh = [it for it in items if it["url"] not in existing_urls]
        if fresh:
            logger.info(
                "  [新增] %s: %d 条新线报 (共提取 %d 条)",
                name, len(fresh), len(items),
            )
            all_new_items.extend(fresh)
            for it in fresh:
                existing_urls.add(it["url"])
        else:
            logger.info(
                "  [正常] %s: 无新内容 (提取 %d 条)",
                name, len(items),
            )

    # 5. Save to SQLite
    if all_new_items:
        added = sqlite_insert_items(db_conn, all_new_items)
        logger.info("[结果] 新增 %d 条", added)
    else:
        logger.info("[结果] 无新增")

    # 6. Export items.json for frontend
    sqlite_export_json(db_conn)

    # 7. Metrics
    logger.info("[指标] %s", metrics.summary())

    # 8. Log entry
    log_entry: Dict[str, Any] = {
        "time": get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"),
        "new_items": len(all_new_items),
        "sites_checked": len(FAST_SITES),
        "metrics": {
            "requests": metrics.request_count,
            "success": metrics.success_count,
            "fail": metrics.fail_count,
            "avg_response_time": round(metrics.avg_response_time, 3),
        },
    }
    try:
        with open(FAST_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # Rotate fast_log: keep last 30 entries
    try:
        with open(FAST_LOG_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
        if len(lines) > 30:
            lines = lines[-30:]
            with open(FAST_LOG_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
    except Exception:
        pass

    # 9. Git commit
    if all_new_items:
        try:
            subprocess.run(
                ["git", "add", "items.json", SQLITE_DB_FILE, FAST_LOG_FILE],
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(
                [
                    "git", "commit", "-m",
                    f"快速更新: 新增 {len(all_new_items)} 条线报 "
                    f"({get_beijing_time().strftime('%H:%M')})",
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                # Push with retry
                for attempt in range(3):
                    push_result = subprocess.run(
                        ["git", "push", "origin", "main"],
                        capture_output=True,
                        timeout=30,
                    )
                    if push_result.returncode == 0:
                        logger.info("[Git] 已推送")
                        break
                    time.sleep(3)
                    subprocess.run(
                        ["git", "pull", "--rebase", "--strategy-option=theirs", "origin", "main"],
                        capture_output=True,
                        timeout=30,
                    )
                else:
                    logger.warning("[Git] 推送失败")
            else:
                logger.info("[Git] 无变更需要提交")
        except Exception as e:
            logger.error("[Git] 提交失败: %s", e)

    db_conn.close()
    logger.info("=" * 50)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("用户中断")
