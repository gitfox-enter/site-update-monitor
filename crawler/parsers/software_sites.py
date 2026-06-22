# -*- coding: utf-8 -*-
"""Auto-extracted parser module from parsers.py."""

import logging
import re
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

logger = logging.getLogger('crawl')

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
            _add_item(items, seen, text, href, base_url)

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
            _add_item(items, seen, text, href, base_url)

    return items[:30]


# ---------------------------------------------------------------------------
# 3. ithome.com/zt/xijiayi  (IT之家 - 喜加一专题)
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
            _add_item(items, seen, text, href, base_url)

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
            _add_item(items, seen, text, href, base_url)

    return items[:30]


# ---------------------------------------------------------------------------
# 4. lsapk.com  (LSapk / 蓝鲨应用库)
# ---------------------------------------------------------------------------




