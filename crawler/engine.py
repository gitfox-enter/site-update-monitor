# -*- coding: utf-8 -*-
"""Crawler engine: Playwright handler, main crawl loop, run log, pause, graceful shutdown."""

import logging
import os
import signal
import subprocess
import sys
import time
import random
import json
import asyncio
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
from bs4 import BeautifulSoup

from common import (
    JsonFormatter, get_beijing_time, auto_categorize, CATEGORY_KEYWORDS,
    load_items_db, save_items_db, load_blacklist, is_blacklisted,
    build_source_name_index, get_source_name as _get_source_name_by_index,
    calculate_md5, upgrade_to_https, DomainRateLimiter, sanitize_href,
    sanitize_text, is_junk, ITEMS_DB_FILE, BLACKLIST_FILE, CRAWL_STATUS_FILE, MAX_ITEMS_DB,
    ProxyPool, create_proxy_pool,
)
from crawler.config import JS_RENDER_SITES, MAX_RETRIES, REQUEST_TIMEOUT, RETRY_BASE_DELAY, RUN_LOG_FILE, MONITOR_SITES, is_dead_site, get_source_name, get_site_tier, init_adaptive_tiers, update_adaptive_tier, save_adaptive_tiers, get_all_adaptive_tiers
from crawler.storage import get_current_round, load_notified_items, save_notified_items, filter_new_items, merge_items_into_db, load_hash_records, save_hash_records, export_items_latest_json, get_random_delay, get_random_profile, get_referer
from crawler.network import MetricsTracker, metrics, get_conditional_headers, rate_limiter, is_allowed_by_robots, update_conditional_cache
from crawler.parsers import _match_parser, extract_article_items, parse_rss_feed, parse_ghxi_items, fetch_page_content, fetch_ghxi_items_async

# Playwright: optional dependency for JS-rendered sites
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None  # type: ignore

# Set up logger for engine
engine_logger = logging.getLogger('crawl')
engine_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
engine_logger.addHandler(_handler)

# Alias for backward compatibility
logger = engine_logger

# Global proxy pool (initialized in main_async)
_proxy_pool = None

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


def _parse_response_html(
    content: bytes,
    encoding: str,
    url: str,
    elapsed: float = 0.0,
    ghxi_items: Optional[List[Dict[str, str]]] = None,
) -> Tuple[bool, Any]:
    """Parse HTML response bytes → extracted content dict or error.

    Shared by both Playwright and aiohttp fetch paths.
    Returns (True, result_dict) on success, (False, error_msg) on failure.

    Args:
        ghxi_items: Optional pre-fetched items from ghxi.com WP API
                     (avoids blocking event loop with sync requests.get in async path).
    """
    soup = BeautifulSoup(content, 'html.parser')

    # Encoding fallback: BS4 may misdetect Chinese pages as ISO-8859-1 / Windows-1252
    # when the HTML lacks a <meta charset> tag. Re-decode if:
    #   1. BS4 detected no encoding at all, OR
    #   2. BS4 detected a Western/latin encoding but the page is likely Chinese
    detected_enc = (soup.original_encoding or '').lower()
    western_encodings = ('iso-8859-1', 'iso-8859-2', 'windows-1252', 'latin-1', 'latin1', 'ascii')
    needs_redecode = not detected_enc or detected_enc in western_encodings
    if needs_redecode:
        # Use the passed encoding hint, or try to detect from content
        enc = encoding or 'utf-8'
        if enc.lower() in ('gb2312', 'gbk', 'gb18030'):
            enc = 'gbk'
        # Heuristic: if a significant fraction of bytes are > 127, it's likely multi-byte
        if detected_enc in western_encodings and enc == 'utf-8':
            try:
                high_bytes = sum(1 for b in content[:4096] if b > 127)
                if high_bytes > len(content[:4096]) * 0.1:
                    # Try GBK first for Chinese sites, fall back to utf-8
                    try:
                        decoded = content.decode('gbk', errors='strict')
                        soup = BeautifulSoup(decoded, 'html.parser')
                    except (UnicodeDecodeError, LookupError):
                        decoded = content.decode('utf-8', errors='ignore')
                        soup = BeautifulSoup(decoded, 'html.parser')
                else:
                    decoded = content.decode(enc, errors='ignore')
                    soup = BeautifulSoup(decoded, 'html.parser')
            except Exception:
                decoded = content.decode(enc, errors='ignore')
                soup = BeautifulSoup(decoded, 'html.parser')
        else:
            decoded = content.decode(enc, errors='ignore')
            soup = BeautifulSoup(decoded, 'html.parser')

    title_tag = soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else url

    # Site-specific parser dispatch
    parser_pair = _match_parser(url)

    # Special handling: RSS/Atom Feed
    if 'feed.iplaysoft.com' in url or url.endswith('.xml'):
        article_items = parse_rss_feed(content, url)
        text = '\n'.join(item['text'] for item in article_items)
    elif 'ghxi.com' in url:
        # Use pre-fetched async items if available, otherwise fall back to sync API call
        if ghxi_items is not None:
            article_items = ghxi_items
            text = '\n'.join(item['text'] for item in article_items)
        else:
            article_items = parse_ghxi_items(soup, url)
        if not article_items:
            article_items = extract_article_items(soup, url)
            body = soup.find('body')
            text = body.get_text(separator=' ', strip=True) if body else ''
            text = ' '.join(text.split())
        elif ghxi_items is None:
            text = '\n'.join(item['text'] for item in article_items)
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
        text = body.get_text(separator=' ', strip=True) if body else soup.get_text(separator=' ', strip=True)
        text = ' '.join(text.split())

    if not text:
        return False, "页面正文为空"

    summary = text[:300] + '...' if len(text) > 300 else text
    return True, {
        'text': text, 'title': title, 'summary': summary,
        'items': article_items, 'response_time': round(elapsed, 3),
    }


def _compute_hash_diff(
    result: Dict[str, Any], old_records: Dict[str, str], url: str,
) -> Tuple[bool, str, str]:
    """Compute content hash and compare with old records.

    Returns (is_updated, new_hash, message).
    """
    article_items = result.get('items', [])
    if article_items:
        items_text = json.dumps(
            [{'t': it['text'], 'u': it['url']} for it in article_items],
            ensure_ascii=False, sort_keys=True,
        )
        new_hash = calculate_md5(items_text)
    else:
        new_hash = calculate_md5(result['text'])

    old_hash = old_records.get(url)
    if old_hash is None:
        return False, new_hash, "首次监控"
    elif old_hash != new_hash:
        return True, new_hash, "内容已更新"
    return False, new_hash, "无更新"


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

    # robots.txt check
    if not is_allowed_by_robots(url):
        return False, "robots.txt 禁止爬取"

    # Per-domain rate limiting (async to avoid blocking the event loop)
    await rate_limiter.async_wait(domain)

    # === Playwright 路径：JS 渲染站点 ===
    if _needs_playwright(url):
        logger.info("Playwright 模式抓取: %s", url, extra={'site': url, 'event': 'playwright_start'})
        start_time = time.time()
        pw_ok, pw_result = await fetch_with_playwright(url)
        elapsed = time.time() - start_time
        if not pw_ok:
            metrics.record_failure(domain)
            return False, f"Playwright 抓取失败: {pw_result[:80]}"

        pw_encoding = 'utf-8'
        return _parse_response_html(pw_result.encode(pw_encoding), pw_encoding, url, elapsed)

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
                    # Last attempt: read response body for proper HTTP error reporting
                    content_bytes = await resp.read()
                    response_headers = {k: v for k, v in resp.headers.items()}
                    response = SimpleNamespace(
                        status=resp.status,
                        headers=response_headers,
                        content=content_bytes,
                        encoding=resp.get_encoding() or 'utf-8',
                    )
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
            metrics.record_failure(domain)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                logger.debug("重试 %s (%d/%d): %s", domain, attempt + 1, MAX_RETRIES, str(e)[:80])
                await asyncio.sleep(delay)
                active_proxy = _proxy_pool.get_proxy() if _proxy_pool else None
                continue
            logger.info("连接失败", extra={'site': url, 'event': 'connection_error'})
            return False, f"连接失败: {str(e)[:50]}"
        except Exception as e:
            metrics.record_failure(domain)
            if active_proxy and _proxy_pool:
                _proxy_pool.report_failure(active_proxy)
            return False, f"请求异常: {str(e)[:50]}"

    if response is None:
        return False, "请求未发出"

    # Response size limit (10MB)
    MAX_RESPONSE_SIZE = 10 * 1024 * 1024
    content_length = response.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_SIZE:
                return False, f"Response too large: {content_length} bytes"
        except (ValueError, TypeError):
            pass  # Malformed Content-Length header, ignore
    if len(response.content) > MAX_RESPONSE_SIZE:
        return False, f"Response body too large: {len(response.content)} bytes"

    # Update conditional cache using SimpleNamespace wrapper
    update_conditional_cache(url, SimpleNamespace(headers=response.headers))

    if response.status != 200:
        metrics.record_failure(domain)
        logger.info("HTTP 请求失败", extra={
            'site': url, 'event': 'http_error',
            'status_code': response.status,
        })
        return False, f"HTTP {response.status}"

    metrics.record_success(domain, elapsed)

    # Parse HTML response (shared logic)
    # For ghxi.com: pre-fetch WP API items asynchronously to avoid blocking the event loop
    ghxi_pre_fetched = None
    if 'ghxi.com' in url:
        ghxi_pre_fetched = await fetch_ghxi_items_async(session)
    ok, parse_result = _parse_response_html(
        response.content, response.encoding, url, elapsed,
        ghxi_items=ghxi_pre_fetched,
    )
    if not ok:
        return False, parse_result

    logger.info("爬取成功", extra={
        'site': url, 'event': 'crawl_success',
        'response_time': parse_result['response_time'],
    })
    return True, parse_result


def check_site_update(url: str, old_records: Dict[str, str]) -> Tuple[Optional[bool], Optional[str], str, Optional[Dict[str, Any]]]:
    """
    检查单个站点是否有更新
    返回：(是否更新, 新哈希值, 错误信息, 页面信息)
    """
    success, result = fetch_page_content(url)

    if not success:
        return None, None, result, None  # 爬取失败

    page_info = {
        'url': url,
        'title': result['title'],
        'summary': result['summary'],
        'items': result['items']
    }

    is_updated, new_hash, message = _compute_hash_diff(result, old_records, url)
    return is_updated, new_hash, message, page_info


async def check_site_update_async(
    url: str,
    old_records: Dict[str, str],
    session: aiohttp.ClientSession,
) -> Tuple[Optional[bool], Optional[str], str, Optional[Dict[str, Any]]]:
    """Async version of check_site_update."""
    success, result = await fetch_page_content_async(url, session, old_records)

    if not success:
        return None, None, result, None

    page_info = {
        'url': url,
        'title': result['title'],
        'summary': result['summary'],
        'items': result['items'],
    }

    is_updated, new_hash, message = _compute_hash_diff(result, old_records, url)
    return is_updated, new_hash, message, page_info


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
            'items.json', 'items_latest.json', 'crawl_status.json',
            'hash_record.txt', 'monitor.db',
            'notified_items.json', 'run_log.jsonl', '.gitignore',
        ]
        # Resolve project root (one level up from crawler/)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for f in TRACKED_FILES:
            fpath = os.path.join(project_root, f)
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




def export_crawl_status(all_site_results, new_item_list, metrics_summary, output_path=None, added_count=0):
    """Export crawl_status.json for the health dashboard."""
    _output_path = output_path or CRAWL_STATUS_FILE
    sites = []
    for r in all_site_results:
        entry = {
            "url": r.get("url", ""),
            "name": get_source_name(r.get("url", "")),
            "status": "ok" if r.get("status") in ("updated", "no_update", "no_change", "first") else ("fail" if r.get("status") == "error" else "skip"),
        }
        if r.get("response_time"):
            entry["response_time"] = round(r.get("response_time"), 0)
        if r.get("message"):
            entry["error"] = str(r.get("message", ""))[:200]
        sites.append(entry)
    # Load items from JSON to get total count
    db = load_items_db()
    total_items = len(db['items'])
    status = {
        "last_run": {
            "check_time": new_item_list[0].get("time", "") if new_item_list else "",
            "total_items": total_items,
            "new_items": added_count,
            "metrics": metrics_summary,
        },
        "sites": sites,
    }
    tmp = _output_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, _output_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)


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

    # 加载自适应 tier 记录
    init_adaptive_tiers()

    # 实际监控列表
    active_sites = [upgrade_to_https(url) for url in monitor_sites]
    
    # === 分级爬取：根据轮次决定本次爬取哪些站点 ===
    # high: 每轮都爬, medium: 每2轮爬一次, low: 每4轮爬一次
    _TIER_FREQ = {'high': 1, 'medium': 2, 'low': 4}
    prev_count = len(active_sites)
    active_sites = [
        url for url in active_sites
        if round_num % _TIER_FREQ.get(get_site_tier(url), 1) == 0
    ]
    skipped = prev_count - len(active_sites)
    if skipped:
        logger.info("分级过滤跳过 %d 个站点 (medium/low 本轮不爬)", skipped)
    
    logger.info("监控站点数: %d (活跃) + %d (黑名单)",
                len(active_sites), len(blacklist_domains))

    # 加载已通知过的条目URL（去重用）
    notified = load_notified_items()
    logger.info("已加载历史条目: %d 条", len(notified.get('items', [])))

    # Shuffle site order to avoid deterministic crawl patterns
    random.shuffle(active_sites)

    # Initialize SQLite and load hash records
    old_records = load_hash_records()
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

    # === 自适应 Tier 调整：根据爬取结果升级/降级 ===
    tier_changes = []
    for r in all_site_results:
        url = r['url']
        if r['status'] in ('dead', 'robots_denied'):
            continue
        is_fail = r['status'] == 'error'
        has_items = r['status'] in ('updated', 'first')
        new_tier = update_adaptive_tier(
            url,
            status='fail' if is_fail else 'ok',
            has_new_items=has_items,
        )
        if new_tier:
            tier_changes.append(f"{url} → {new_tier}")
    # 保存自适应 tier 记录
    save_adaptive_tiers(get_all_adaptive_tiers())
    if tier_changes:
        logger.info("自适应 tier 调整: %s", '; '.join(tier_changes))

    total_count = len(all_site_results)

    # 统计死站数
    dead_count = sum(1 for r in all_site_results if r.get('status') == 'dead')

    logger.info("成功: %d | 失败: %d | 死站: %d | robots.txt跳过: %d | tier调整: %d",
                success_count, error_count, dead_count, robots_denied_count, len(tier_changes))
    logger.info("更新站点: %d 个", updated_count)

    # 输出运行指标摘要
    metrics_summary = metrics.get_summary()
    logger.info("总请求: %d | 成功: %d | 失败: %d",
                metrics_summary['total_requests'], metrics_summary['success_count'], metrics_summary['fail_count'])

    # Save hash records
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
    # Insert new items to SQLite (also adds 'category' field to items in-place)
    added = 0
    if new_item_list:
        added = merge_items_into_db(new_item_list, check_time)
        logger.info("SQLite 新增 %d 条线报", added)

    # Save notified items AFTER merge so they include 'category' field
    save_notified_items({
        'items': new_item_list,
        'updated_at': check_time,
    })

    # 计算本轮新增URL数
    existing_urls_set = set(item['url'] for item in (notified.get('items', []) if isinstance(notified, dict) else []))
    new_urls = set(item['url'] for item in new_item_list if item['url'] not in existing_urls_set)
    logger.info("本轮新通知条目: %d 条", len(new_urls))

    # Git提交 - use actual added count from merge_result, not raw new_item_list length
    export_crawl_status(all_site_results, new_item_list, metrics_summary, added_count=added)
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
        'tier_changes': tier_changes,
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

    logger.info("本轮巡检结束")


# ============================================================
# Graceful shutdown
# ============================================================

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
        import traceback
        logger.error("致命错误: %s\n%s", e, traceback.format_exc())
        sys.exit(1)
