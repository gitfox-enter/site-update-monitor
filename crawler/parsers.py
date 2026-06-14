# -*- coding: utf-8 -*-
"""Site-specific parsers and parser registry for the crawler."""

import logging
import re
import time
import random
import html as html_mod
import requests
import aiohttp
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger('crawl')


# ============================================================
# 预编译正则 & 公共工具函数
# ============================================================

_RE_CHINESE = re.compile(r'[\u4e00-\u9fff]')


def _has_chinese(text: str, min_count: int = 2) -> bool:
    """检查文本中是否包含足够数量的中文字符。"""
    return len(_RE_CHINESE.findall(text)) >= min_count


def _is_valid_text(text: str, min_len: int = 4, max_len: int = 120) -> bool:
    """检查文本长度是否在有效范围内。"""
    return bool(text) and min_len <= len(text) <= max_len


def _add_item(items: List[Dict[str, str]], seen: Set[str],
              text: str, href: str, base_url: str = '') -> bool:
    """去重、转换相对URL、追加条目到列表。

    Returns:
        True 如果条目被成功追加，False 表示已重复被跳过。
    """
    if not text or text in seen:
        return False
    seen.add(text)
    if base_url and href.startswith('/'):
        href = urljoin(base_url, href)
    items.append({'text': text, 'url': href})
    return True


# 通用导航/功能性文字集合 — 各站点 skip 列表的公共基础
COMMON_SKIP_WORDS: Set[str] = {
    '首页', '关于', '联系我们', '留言', '搜索', '登录', '注册',
    '下一页', '上一页', '返回顶部', '返回首页', '关于我们',
    '登录/注册', '找回密码', '立即注册', '收藏本站', '设为首页',
    '快捷导航', '更多', '最新', '热门', '分类', '标签',
    '回复', '删除', '举报', '推荐', '点赞', '评论', '浏览',
}


def _make_skip_set(*extra_words: str) -> Set[str]:
    """在 COMMON_SKIP_WORDS 基础上创建站点专用的过滤集合。"""
    return COMMON_SKIP_WORDS | set(extra_words)


# Direct imports (no circular dependency: storage/config don't import parsers)
from crawler.storage import get_random_ua
from crawler.config import REQUEST_TIMEOUT

# ============================================================
# 站点专用解析器
# ============================================================



def parse_discuz_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """Discuz论坛 - 结构化条目提取"""
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for a in soup.select('.threadlist .t a, .tl .t a, #threadlist .t a, .threadlist tr td a.xst, .threadlist tr td a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or text in seen or '/thread-' not in href:
            continue
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            seen.add(text)
            items.append({'text': text, 'url': href})
    if not items:
        for tr in soup.select('.forum tbody tr, table tbody tr'):
            for a in tr.select('a'):
                text = a.get_text(strip=True)
                href = a.get('href', '').strip()
                if text and len(text) > 3 and '/thread-' in href and text not in seen:
                    if href.startswith('/'):
                        href = urljoin(base_url, href)
                    if href.startswith('http'):
                        seen.add(text)
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
        if not _is_valid_text(text, max_len=999):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Secondary: entry-title links within td modules
    for a in soup.select('.entry-title a, .td-module-title a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not _is_valid_text(text, max_len=999) or text in seen:
            continue
        if not re.search(r'yxssp\.com/\d+\.html', href):
            continue
        _add_item(items, seen, text, href, base_url)

    # Fallback: broad URL pattern match
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5, max_len=200):
                continue
            if not re.search(r'yxssp\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            _add_item(items, seen, text, href)

    return items[:30]







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
        if not _is_valid_text(text, min_len=3, max_len=999):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Fallback: any link matching the article URL pattern
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text):
                continue
            if not re.search(r'423down\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            # Skip navigation-like short text
            skip_words = _make_skip_set(
                '安卓软件', '电脑软件', '操作系统', '原创软件', '媒体播放',
                '网页浏览', '图形图像', '聊天软件', '办公软件', '上传下载',
                '实用软件', '系统辅助', '系统必备', '安全软件', '补丁相关', '硬件相关')
            if text in skip_words:
                continue
            _add_item(items, seen, text, href)

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
        if not _is_valid_text(text, min_len=2, max_len=999):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=2, max_len=999) or text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Tertiary: bulletin/announcement links
    for a in soup.select('a[href*="/bulletin/"]'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not _is_valid_text(text, max_len=999) or text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Fallback: broad pattern for any content links
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=3):
                continue
            if not re.search(r'ziyuanting\.com/(sites|app|bulletin)/\d+\.html', href):
                continue
            if text in seen:
                continue
            _add_item(items, seen, text, href, base_url)

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
    skip_texts = _make_skip_set(
        '手机软件', '电脑软件', '操作系统', '影音娱乐', '教程课程',
        '办公软件', '影音软件', '实用软件', '图形图像', '媒体工具',
        '教育教学', '上传下载', '社交聊天', '系统工具', '浏览器',
        '安卓游戏', '其他安卓', '图片图像', '视频工具', '安全防护',
        '即时通讯', '设计软件', 'Windows11', 'Windows10', 'Windows7',
        'WinPE', '原镜像', 'PC游戏', '电影分享', '音乐分享')

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not _is_valid_text(text, min_len=6, max_len=150):
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
        if not _has_chinese(text):
            continue

        _add_item(items, seen, text, href, base_url)

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
    skip_texts = _make_skip_set(
        '安卓应用', '实用工具', '系统办公', '拍照修图', '影视影音',
        '办公学习', '小说阅读', '电影动漫', '社交聊天', '资源下载',
        '音乐铃声', '天气生活', '美化壁纸', '生活服务', 'TV盒子',
        'PC软件', '技巧教程', '会员专区', '更新', '普通会员', '黄金会员')

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not _is_valid_text(text):
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
        if not _has_chinese(text):
            continue

        _add_item(items, seen, text, href)

    return items[:30]


def parse_xzba_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """游戏下载吧 (xzba.cc) - extract game entries from the WordPress post list.

    Primary: links inside post/article containers matching the URL pattern.
    Fallback: any anchor with href matching xzba.cc/{digits}.html.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Navigation / category words to skip
    skip_words = _make_skip_set(
        '最新发布', '角色扮演', '动作', '模拟', '休闲', '独立',
        '冒险', '策略', 'switch模拟', '找游戏')

    # Primary: post entry links within content containers
    for a in soup.select('.posts-row a, .home-tab-content a, .tab-content a, '
                         'article a, .post-entry a, .entry-title a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not _is_valid_text(text, min_len=2):
            continue
        if not re.search(r'xzba\.cc/\d+\.html', href):
            continue
        if text in seen or text in skip_words:
            continue
        _add_item(items, seen, text, href, base_url)

    # Fallback: broad URL pattern match across all links
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=2):
                continue
            if not re.search(r'xzba\.cc/\d+\.html', href):
                continue
            if text in seen or text in skip_words:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=3, max_len=80):
            continue
        skip = ['阅读全文', '赞', '评论', '去评论', '下一页', '上一页', '返回顶部']
        if text in skip:
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)
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
    skip_texts = _make_skip_set(
        '实时线报', '项目首码', '手机赚钱', '爆款秒杀',
        '随笔', '去下载', '资讯', '网站地图')

    # Strategy 1: Match /article/{id} links
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not _is_valid_text(text, min_len=5):
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
        if not _has_chinese(text):
            continue

        _add_item(items, seen, text, href, base_url)

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
    skip_texts = _make_skip_set(
        '长期羊毛', '有奖活动', '撸实物', '影音会员',
        '话费流量活动', '[查看详情]', '趣闲赚',
        '长期 >', '活动 >', '实物 >')

    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not _is_valid_text(text, min_len=5):
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
        if not _has_chinese(text):
            continue

        # Clean up text: remove zero-width characters
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\u202e\u202c]', '', text).strip()
        if not _is_valid_text(text, min_len=5, max_len=999):
            continue

        _add_item(items, seen, text, href)

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
        if not _is_valid_text(text, min_len=5, max_len=999):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)
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

        if not _is_valid_text(text, min_len=3, max_len=150):
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
        if not _has_chinese(text, min_count=1) and len(text) < 8:
            continue

        _add_item(items, seen, text, href)

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
        if not _is_valid_text(text, min_len=3, max_len=999):
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
        _add_item(items, seen, text, href)
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
    """果核剥壳 WP API 异步请求（供 async 路径使用，避免阻塞事件循环）。"""
    api_url = "https://www.ghxi.com/wp-json/wp/v2/posts?per_page=30"
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/json, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    items: List[Dict[str, str]] = []
    try:
        async with session.get(
            api_url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 200:
                posts = await resp.json()
                for post in posts:
                    title = html_mod.unescape(post.get('title', {}).get('rendered', ''))
                    link = post.get('link', '')
                    if title and len(title) > 3 and link:
                        items.append({'text': title, 'url': link})
                logger.info("果核剥壳 WP API (async) 获取到 %d 篇文章", len(items))
            else:
                logger.info("果核剥壳 WP API (async) 返回 HTTP %d", resp.status)
    except Exception as e:
        logger.info("果核剥壳 WP API (async) 请求失败: %s", e)
    return items


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
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        skip_words = _make_skip_set('联系')
        if text in skip_words:
            continue
        _add_item(items, seen, text, href, base_url)
    return items[:30]


def parse_wobangzhao_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """我帮找网 (wobangzhao.com) - Discuz! X forum, extract thread/post links.

    Handles both SEO-friendly (thread-{id}-{page}-{tid}.html) and
    standard (forum.php?mod=viewthread) URL formats.
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    skip_words = _make_skip_set(
        '版块', '主题', '帖子', '返回列表', 'BBS',
        '修复日志', '下载教程', '解压教程', '加入天寻计划',
        '无法登陆？', '切换到宽版', '2026精选资源')

    # Primary: thread links with title attributes (portal blocks, hot lists)
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get('title', '').strip() or a.get_text(strip=True)
        if not _is_valid_text(text, max_len=150):
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
        if text in skip_words:
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
        if not _is_valid_text(text, min_len=3):
            continue
        if text in seen:
            continue
        skip_words = _make_skip_set('联系')
        if text in skip_words:
            continue
        _add_item(items, seen, text, href, base_url)
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
        if not _is_valid_text(text, min_len=2):
            continue
        if '/softdown/' not in href:
            continue
        if text in skip_exact:
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Secondary: latest update list items (often in a dedicated section)
    for li in soup.select('.update-list li, .new-list li, .CRCSList li'):
        a_tag = li.select_one('a[href*="/softdown/"]')
        if not a_tag:
            continue
        text = a_tag.get_text(strip=True)
        href = a_tag.get('href', '').strip()
        if not _is_valid_text(text, min_len=2, max_len=999) or text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=2):
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
        if not _is_valid_text(text):
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
            if not _is_valid_text(text, min_len=3):
                continue
            if text in seen:
                continue
            # 仅保留文章链接 (/article/{id}.html)
            if not re.search(r'/article/\d+\.html', href):
                continue
            _add_item(items, seen, text, href, base_url)

    # 策略2：通用兜底 - 匹配所有 /article/{id}.html 链接
    if len(items) < 3:
        for a in soup.select('a[href*="/article/"]'):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=3):
                continue
            if not re.search(r'/article/\d+\.html', href):
                continue
            if text in seen:
                continue
            _add_item(items, seen, text, href, base_url)

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
            if not _is_valid_text(text, min_len=3):
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
            _add_item(items, seen, text, href, base_url)

    # 策略2：通用兜底 - 匹配 appinn.com 下的 slug 链接
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if text in seen:
                continue
            # 匹配 appinn.com/some-slug/ 格式
            if not re.search(r'appinn\.com/[\w-]+/$', href):
                continue
            if '/category/' in href or '/tag/' in href or '/page/' in href:
                continue
            _add_item(items, seen, text, href)

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
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # 策略2：通用兜底 - 匹配 ithome.com/0/{xxx}/{xxx}.htm 链接
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if not re.search(r'ithome\.com/0/\d+/\d+\.htm', href):
                continue
            if text in seen:
                continue
            _add_item(items, seen, text, href)

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
        if not _is_valid_text(text, min_len=3):
            continue
        if text in seen:
            continue
        # 仅保留 lsapk.com 域名下的文章链接
        if 'lsapk.com' not in href and not href.startswith('/'):
            continue
        # 排除分类、标签等导航链接
        if '/category/' in href or '/tag/' in href or '/page/' in href:
            continue
        _add_item(items, seen, text, href, base_url)

    # 策略2：通用兜底 - 匹配 lsapk.com/{digits}.html 格式
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=3):
                continue
            if not re.search(r'lsapk\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            _add_item(items, seen, text, href)

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
        if not _is_valid_text(text, min_len=3, max_len=150):
            continue
        if text in seen:
            continue
        # 排除标签、分类等导航链接
        if '/tag/' in href or '/page/' in href or '/web/' in href or '/design/' in href:
            continue
        if '/apps/' in href and '/' == href.rstrip('/').split('thosefree.com')[-1]:
            continue
        _add_item(items, seen, text, href, base_url)

    # 策略2：幻灯片封面链接
    for a in soup.select('a.pic-cover-item'):
        href = a.get('href', '').strip()
        h3 = a.select_one('.pic-cover-item-title')
        text = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not _is_valid_text(text, min_len=3, max_len=150):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # 策略3：侧边栏推荐文章
    for a in soup.select('a.sider-post-item-title'):
        href = a.get('href', '').strip()
        h3 = a.find('h3')
        text = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not _is_valid_text(text, min_len=3, max_len=150):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=3, max_len=150):
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
            if not _is_valid_text(text, min_len=3, max_len=150):
                continue
            if not re.search(r'douban\.com/group/topic/\d+', href):
                continue
            if text in seen:
                continue
            skip_words = _make_skip_set('加入小组')
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
    skip_words = _make_skip_set(
        '好单库首页', '好单库APP', '客户服务', '联系客服',
        '建议反馈', 'API文档', '活动中心', '我的应用',
        '招商入驻', '开放平台', '商家合作', 'CMS中心',
        '帮助中心', '退出登录')

    # 策略1：提取服务端渲染的导航链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not _is_valid_text(text, min_len=2, max_len=80):
            continue
        # 清除 Vue 模板碎片（如 {{oTag.index}}）
        if '{{' in text:
            text = re.sub(r'\{\{[^}]*\}\}', '', text).strip()
            if not _is_valid_text(text, min_len=2, max_len=999):
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

        _add_item(items, seen, text, href, base_url)

    # 策略2：提取公告详情链接 (notice_detail)
    for a in soup.select('a[href*="notice_detail"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True) or '好单库公告'
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # 策略3：提取 Vue 模板中嵌入的外部工具链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not _is_valid_text(text, min_len=2, max_len=999):
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
            _add_item(items, seen, text, href)

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
            if not _is_valid_text(text):
                continue
            if text in seen:
                continue
            # Must be an article link (contains .html and a numeric segment)
            if not re.search(r'/\d+\.html', href):
                continue
            _add_item(items, seen, text, href, base_url)

    # Strategy 2: Fallback - scan all <a> tags matching article URL pattern
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if not re.search(r'/\d+\.html', href):
                continue
            # Filter out navigation keywords
            skip_words = _make_skip_set(
                '导航', '站点地图', '联系', '精选软件', '精选博客',
                '最新软件', '最新博客')
            if text in skip_words:
                continue
            if text in seen:
                continue
            if not _has_chinese(text) and len(text) < 15:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=5):
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
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Broader fallback - any link matching article URL pattern
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if not re.search(r'huodong5\.com/\d+\.html', href):
                continue
            if text in seen:
                continue
            text = re.sub(r'^【推荐】', '', text).strip()
            if len(text) < 5:
                continue
            _add_item(items, seen, text, href)

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
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        # Ensure it's an article-style URL, not a category/tag page
        if not re.search(r'yangmaodang\.club/(articles|news|things)/', href):
            # Also accept standalone article pages (e.g. /dache/, /one-click-urls/)
            if not re.search(r'yangmaodang\.club/[a-z0-9\-]+/$', href):
                continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Fallback - scan article links by URL pattern
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text):
                continue
            if text in seen:
                continue
            if not re.search(r'yangmaodang\.club/(articles|news|things)/\S+', href):
                continue
            # Skip category/tag pages
            if '/category/' in href or '/tag/' in href:
                continue
            skip_words = _make_skip_set(
                '最新羊毛', '伙伴事物', '银行羊毛', '阿里羊毛',
                '腾讯羊毛', '其他羊毛', '长期羊毛', '关于本站', '联系')
            if text in skip_words:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=2):
            continue
        if text in seen:
            continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Broader fallback for all article links
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=2):
                continue
            if text in seen:
                continue
            # Accept internal article links or external redirect links
            if 'xianbaomi.com/xb/' not in href and not href.startswith('http'):
                continue
            # Filter navigation
            skip_words = _make_skip_set(
                '活动线报', '24小时热门线报', '1周热门线报',
                '最新内容', '网站地图', '查券', '一周热门', '神车群')
            if text in skip_words:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        # Must match article URL pattern
        if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
            continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Broader fallback
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not _is_valid_text(text):
                continue
            if text in seen:
                continue
            if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
                continue
            skip_words = _make_skip_set(
                '羊毛活动', '赚钱软件', '打折优惠', '赚钱攻略',
                '关于本站', '网站地图')
            if text in skip_words:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        # Must be an article link
        if not re.search(r'/(activity|news|mall)/\d+\.html', href):
            continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Banner/carousel links
    for a in soup.select('#myFocus .pic li a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not _is_valid_text(text):
            continue
        if text in seen:
            continue
        if not re.search(r'/(activity|news|mall)/\d+\.html', href):
            continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 3: Fallback - scan all links
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text):
                continue
            if text in seen:
                continue
            if not re.search(r'/(activity|news|mall)/\d+\.html', href):
                continue
            skip_words = _make_skip_set(
                'QQ活动', '最新活动', '手机活动', '电脑软件',
                '购物商城', '投稿', 'QQ群', '关注我们')
            if text in skip_words:
                continue
            if not _has_chinese(text) and len(text) < 15:
                continue
            _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=3):
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
        if not _is_valid_text(text, min_len=3):
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
        if not _is_valid_text(text, min_len=3):
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
            if not _is_valid_text(text, min_len=3):
                continue
            if text in seen:
                continue
            # Match thread patterns: xyk-{tid}-1.htm, a-{id}-1.htm, thread-*
            if not re.search(r'(xyk-\d+-\d+\.htm|a-\d+-\d+\.htm|thread-\d+)', href):
                continue
            # Filter navigation
            skip_words = _make_skip_set(
                '信用卡交流', '贷款交流', '热帖推荐', '租机交流',
                '信用卡产品', '投稿')
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
    skip_texts = _make_skip_set(
        '羊毛阁', '联系站长', '500G流量卡', '任务平台合集',
        '1', '2', '3', '4', '5', '...679')

    # Strategy 1: Match article links with numeric .html pattern on ymxianbao domain
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)

        if not _is_valid_text(text):
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
        if not _has_chinese(text):
            continue

        _add_item(items, seen, text, href, base_url)

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
        if not _is_valid_text(text, min_len=3, max_len=999) or text in seen:
            continue
        if '/huodong/' not in href and not re.search(r'/\d+\.html', href):
            continue
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            seen.add(text)
            items.append({'text': text, 'url': href})

    # 回退: 任何包含 /huodong/ 的链接
    if not items:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, max_len=999) or text in seen:
                continue
            if '/huodong/' in href:
                _add_item(items, seen, text, href, base_url)

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

    增强特性：
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
    from crawler.config import MAX_RETRIES, RETRY_BASE_DELAY
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
                # 304 时仍视为成功（页面未变更），返回特殊标记
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

        # SSRF protection: validate final URL is not internal (after redirects)
        final_url = response.url
        parsed_final = urlparse(final_url)
        hostname = parsed_final.hostname or ''
        if hostname.startswith(('127.', '10.', '172.16.', '192.168.', '169.254.', '0.', '::1', 'localhost')):
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
