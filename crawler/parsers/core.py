# -*- coding: utf-8 -*-
"""Parser registry and page content fetcher.

This module provides the central PARSER_REGISTRY and the main
fetch_page_content() function that orchestrates crawling.
"""

import logging
import re
import time
import random
import html as html_mod
import requests
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import warnings
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

from crawler.parsers._utils import (
    _has_chinese, _is_valid_text, _add_item, _make_skip_set, COMMON_SKIP_WORDS,
)
from crawler.parsers.deal_sites import (
    parse_423down_items, parse_ziyuanting_items, parse_wycad_items,
    parse_baicaio_items_v2, parse_h6room_items, parse_xzba_items,
    parse_apprcn_items, parse_daydayzhuan_items, parse_007ymd_items,
    parse_12345pro_items, parse_wobangzhao_items,
    parse_haodanku_items, parse_hybase_items, parse_huodong5_items,
    parse_yangmaodang_items, parse_xianbaomi_items, parse_yangmao_wang_items,
    parse_iqnew_items, parse_51kanong_items, parse_ymxianbao_items,
    parse_linejia_items, parse_10000yun_items,
)
from crawler.parsers.software_sites import (
    parse_yxssp_items, parse_foxirj_items, parse_ddooo_items,
    parse_onlinedown_items, parse_appinn_items, parse_lsapk_items,
    parse_thosefree_items, parse_ithome_xijiayi_items,
)
from crawler.parsers.forum_sites import (
    parse_discuz_items, parse_douban_group_items,
)
from crawler.parsers.rss_parsers import (
    parse_rss_feed,
)

logger = logging.getLogger('crawl')

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
    '10000yun.com':      (parse_10000yun_items,      None),
}


def _match_parser(url: str) -> Optional[Tuple[Any, Optional[Any]]]:
    """
    根据 URL 匹配 PARSER_REGISTRY 中的解析器。
    返回 (items_parser, text_parser) 或 None（使用通用解析）。
    使用 hostname 匹配而非子串匹配，避免误命中。
    """
    try:
        hostname = urlparse(url).hostname or ''
    except Exception:
        hostname = ''
    for domain_pattern, parsers in PARSER_REGISTRY.items():
        if hostname == domain_pattern or hostname.endswith('.' + domain_pattern):
            return parsers
    return None




def fetch_page_content(url: str) -> Tuple[bool, Any]:
    """
    爬取页面完整正文。
    返回：(成功标志, 内容/错误信息)
    内容包含：(text, title, summary, response_time)

    增强特性��
    - 指数退避重试（最多 3 次）
    - 每域名 Session 连接池复用
    - Referer 头部增强反爬抗性
    - HTTP 条件请求（ETag / If-Modified-Since）减少带宽
    - robots.txt 合规检查
    """
    # Lazy imports to avoid circular dependency (parsers ↔ network ↔ config)
    from crawler.network import (
        is_allowed_by_robots, get_session,
        get_conditional_headers, update_conditional_cache, metrics,
    )
    from crawler.config import MAX_RETRIES, RETRY_BASE_DELAY, REQUEST_TIMEOUT
    from crawler.storage import get_referer, get_random_profile

    # URL scheme validation: only allow http/https
    if not url.startswith(('http://', 'https://')):
        return False, f"Invalid URL scheme: {url[:50]}"

    parsed = urlparse(url)
    domain = parsed.hostname or parsed.netloc

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
                # 304 时仍视为成功（页面未变更）��返回特殊标记
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
            metrics.record_failure(domain)
            logger.info("HTTP 请求失败", extra={
                'site': url, 'event': 'http_error',
                'status_code': response.status_code,
            })
            return False, f"HTTP {response.status_code}"

        # 请求成功
        metrics.record_success(domain, elapsed)

        # SSRF protection: validate final URL is not internal (fix #14: 使用 ipaddress)
        import ipaddress as _ipaddress
        final_url = response.url
        parsed_final = urlparse(final_url)
        hostname = parsed_final.hostname or ''
        try:
            final_ip = _ipaddress.ip_address(hostname)
            if final_ip.is_private or final_ip.is_loopback or final_ip.is_link_local or final_ip.is_reserved:
                return False, f"SSRF blocked: redirect to internal address {hostname}"
        except ValueError:
            if hostname.lower() in ('localhost', 'metadata.google.internal'):
                return False, f"SSRF blocked: redirect to internal address {hostname}"

        # Response size limit (10MB)
        MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB
        content_length = response.headers.get('Content-Length')
        if content_length:
            try:
                if int(content_length) > MAX_RESPONSE_SIZE:
                    return False, f"Response too large: {content_length} bytes"
            except (ValueError, TypeError):
                pass  # Malformed Content-Length header, ignore
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
        # Priority: sites.yaml parser field > PARSER_REGISTRY domain match
        parser_strategy = None
        try:
            from crawler.config import get_parser_strategy as _get_parser_strategy
            parser_strategy = _get_parser_strategy(url)
        except Exception:
            pass
        parser_pair = _match_parser(url)

        # 特殊处理：RSS/Atom Feed
        if parser_strategy == 'rss' or 'feed.iplaysoft.com' in url or url.endswith('.xml'):
            # RSS/Atom Feed：直接解析XML
            article_items = parse_rss_feed(response.content, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif parser_strategy == 'ghxi' or 'ghxi.com' in url:
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
        metrics.record_failure(domain)
        logger.info("请求超时", extra={'site': url, 'event': 'timeout'})
        return False, "请求超时"
    except requests.ConnectionError:
        metrics.record_failure(domain)
        logger.info("连接失败", extra={'site': url, 'event': 'connection_error'})
        return False, "连接失败"
    except requests.RequestException as e:
        metrics.record_failure(domain)
        logger.info("请求异常", extra={'site': url, 'event': 'request_exception'})
        return False, f"请求异常: {str(e)[:50]}"
    except Exception as e:
        metrics.record_failure(domain)
        logger.info("未知错误", extra={'site': url, 'event': 'unknown_error'})
        return False, f"未知错误: {str(e)[:50]}"


# Legacy sync version - use fetch_page_content_async instead
# (kept for backward compatibility and tests)


