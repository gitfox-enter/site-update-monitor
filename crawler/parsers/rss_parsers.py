# -*- coding: utf-8 -*-
"""RSS/Atom feed parsers and special content extractors."""

import asyncio
import logging
import re
import time
import random
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import warnings
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

import html as html_mod
import aiohttp
import requests

from crawler.parsers._utils import (
    _has_chinese, _is_valid_text, _add_item, _make_skip_set, COMMON_SKIP_WORDS,
)
from crawler.storage import get_random_ua
from crawler.config import REQUEST_TIMEOUT

logger = logging.getLogger('crawl')

def parse_rss_feed(content_bytes: bytes, base_url: str) -> List[Dict[str, str]]:
    """RSS/Atom Feed 解析器 - 直接从XML提取文章条目"""
    from xml.etree import ElementTree as ET
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # 预处理：修复常见的 XML 格式问题
    text = content_bytes.decode('utf-8', errors='replace')  # fix #116/#63: replace bad bytes instead of dropping them
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


def _ghxi_fetch_sync() -> List[Dict[str, str]]:
    """果核剥壳 WP API 同步请求（供 sync 路径使用）。"""
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


async def fetch_ghxi_items_async(session) -> List[Dict[str, str]]:
    """果核剥壳 WP API 异步请求（带重试和 RSS 兜底）。"""
    api_url = "https://www.ghxi.com/wp-json/wp/v2/posts?per_page=30"
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/json, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    items: List[Dict[str, str]] = []
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.get(
                api_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    posts = await resp.json()
                    for post in posts:
                        title = html_mod.unescape(post.get('title', {}).get('rendered', ''))
                        link = post.get('link', '')
                        if title and len(title) > 3 and link:
                            items.append({'text': title, 'url': link})
                    logger.info("果核剥壳 WP API (async) 获取到 %d 篇文章", len(items))
                    return items
                else:
                    logger.info("果核剥壳 WP API (async) 返回 HTTP %d (尝试 %d/%d)", resp.status, attempt + 1, max_retries)
        except asyncio.TimeoutError:
            logger.info("果核剥壳 WP API (async) 超时 (尝试 %d/%d)", attempt + 1, max_retries)
        except Exception as e:
            logger.info("果核剥壳 WP API (async) 请求失败 [%s]: %s (尝试 %d/%d)", type(e).__name__, e, attempt + 1, max_retries)
        if attempt < max_retries - 1:
            await asyncio.sleep(2 ** (attempt + 1))
    # WP API 全部失败，尝试 RSS feed 兜底
    if not items:
        logger.info("果核剥壳 WP API 全部失败，尝试 RSS feed 兜底")
        items = await _fetch_ghxi_rss_fallback(session)
    return items


async def _fetch_ghxi_rss_fallback(session) -> List[Dict[str, str]]:
    """果核剥壳 RSS feed 兜底请求。"""
    feed_url = "https://www.ghxi.com/feed/"
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/rss+xml, application/xml, text/xml, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    try:
        async with session.get(
            feed_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
            ssl=False,
        ) as resp:
            if resp.status == 200:
                content = await resp.read()
                items = parse_rss_feed(content, feed_url)
                logger.info("果核剥壳 RSS 兜底获取到 %d 条", len(items))
                return items
            else:
                logger.info("果核剥壳 RSS 兜底返回 HTTP %d", resp.status)
    except asyncio.TimeoutError:
        logger.info("果核剥壳 RSS 兜底超时")
    except Exception as e:
        logger.info("果核剥壳 RSS 兜底失败 [%s]: %s", type(e).__name__, e)
    return []


async def fetch_rss_feed_async(session, feed_url: str, timeout_seconds: int = 25) -> List[Dict[str, str]]:
    """异步获取并解析 RSS/Atom feed。

    用于绕过主页 HTML 反爬策略（如 foxirj.com 的 403），
    RSS 端点通常不受 IP 封锁影响。

    Args:
        session: aiohttp ClientSession
        feed_url: RSS feed 完整 URL（如 https://www.foxirj.com/feed/）
        timeout_seconds: 超时时间
    Returns:
        解析后的条目列表，失败返回空列表
    """
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/rss+xml, application/xml, text/xml, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
    }
    try:
        async with session.get(
            feed_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as resp:
            if resp.status == 200:
                content = await resp.read()
                items = parse_rss_feed(content, feed_url)
                logger.info("RSS feed %s 获取到 %d 条", feed_url, len(items))
                return items
            else:
                logger.info("RSS feed %s 返回 HTTP %d", feed_url, resp.status)
    except Exception as e:
        logger.info("RSS feed %s 请求失败: %s", feed_url, e)
    return []


def parse_ghxi_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """果核剥壳 - 通过 WordPress REST API 获取文章（站点为 Vue SPA，HTML 无法直接解析）

    注意：此函数仅用于同步路径。异步路径应使用 fetch_ghxi_items_async。
    """
    return _ghxi_fetch_sync()


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
        if not _is_valid_text(text):
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
        _add_item(items, seen, text, href)

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
