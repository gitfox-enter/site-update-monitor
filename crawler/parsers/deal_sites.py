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
            _add_item(items, seen, text, href, base_url)

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
        '安卓应用', '实用工具', '系统办公', '拍照修���', '影视影音',
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

        _add_item(items, seen, text, href, base_url)

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

        _add_item(items, seen, text, href, base_url)

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

        _add_item(items, seen, text, href, base_url)

    return items[:30]


# ---------------------------------------------------------------------------
# 6. www.wycad.com  (网赚 / 无忧软件网)
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

    # 策略1：提取���务端渲染的导航链接
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
            _add_item(items, seen, text, href, base_url)

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
    """好赚网 (m.hybase.com) - 提取软件/资源文章条目。

    站点为自定义 CMS，文章链接格式为 /{分类}/{id}.html。
    移动版(m.hybase.com)使用简洁列表，桌面版使用卡片布局。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Targeted selectors (both mobile and desktop)
    selectors = [
        '.top-entry a.top-entry-img-wrapper',
        '.li-type-card-title a',
        '.home-buzz .title a',
        '.axdswiper .swiper-slide a',
        '.article-list a',
        '.list-item a',
        'h3 a',
        '.post-title a',
        '.entry-title a',
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get('href', '').strip()
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not _is_valid_text(text, min_len=3):
                continue
            if text in seen:
                continue
            if not re.search(r'/\w+/\d+\.html', href):
                continue
            _add_item(items, seen, text, href, base_url)

    # Strategy 2: Fallback - scan all <a> tags matching article URL pattern
    if len(items) < 5:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not _is_valid_text(text, min_len=5):
                continue
            if not re.search(r'/\w+/\d+\.html', href):
                continue
            skip_words = _make_skip_set(
                '导航', '站点地图', '联系', '精选软件', '精选博客',
                '最新软件', '最新博客', '首页', '分类')
            if text in skip_words:
                continue
            if text in seen:
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

    WordPress 站点，文章链接格式为 huodong5.com/{id}.html。
    主列表使用 .article-list .item-title a 或 .feature-post .title a。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: Multiple selector patterns (site may have updated theme)
    for selector in [
        '.article-list .item-title a',
        '.article-list h2 a',
        '.feature-post .title a',
        'h3.slide-title a',
        '.slider-ad li a',
        '.item-title a',
        'article h2 a',
    ]:
        for a in soup.select(selector):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if text in seen:
                continue
            if not re.search(r'huodong5\.com/\d+\.html', href):
                continue
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
            _add_item(items, seen, text, href, base_url)

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

    Z-Blog 站点，最新更新使用 <ul id="gengxin"> 列表。
    文章链接格式为 yangmao.wang/{分类}/{id}.html。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()

    # Strategy 1: gengxin update list (most reliable)
    for a in soup.select('#gengxin li a, ul#gengxin li a'):
        href = a.get('href', '').strip()
        text = (a.get('title', '') or a.get_text(strip=True)).strip()
        if not _is_valid_text(text, min_len=3):
            continue
        if text in seen:
            continue
        if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
            continue
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: All article links with broader selectors
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = (a.get('title', '') or a.get_text(strip=True)).strip()
            if not _is_valid_text(text, min_len=3):
                continue
            if text in seen:
                continue
            if not re.search(r'yangmao\.wang/\w+/\d+\.html', href):
                continue
            skip_words = _make_skip_set(
                '羊毛活动', '赚钱软件', '打折优惠', '赚钱攻略',
                '关于本站', '网站地图', '首页')
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
    """51卡农 (51kanong.com) - 提取论坛帖子/文章条目���

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


# ---------------------------------------------------------------------------
# 万云积分 (10000yun.com) - 软件资源站
# ---------------------------------------------------------------------------
# HTML structure: Custom CMS
#   Articles use ### headings with links: <h3><a href="/{id}.html">title</a></h3>
#   Also: <a href="https://10000yun.com/{id}.html">title</a>
# URL pattern: 10000yun.com/{id}.html
# ---------------------------------------------------------------------------


def parse_10000yun_items(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    """万云积分 (10000yun.com) - 提取软件/资源文章条目。

    自定义 CMS 站点，文章链接格式为 10000yun.com/{id}.html。
    首页使用 h3 标题包裹文章链接，也有直接的 a 标签列表。
    """
    items: List[Dict[str, str]] = []
    seen: Set[str] = set()
    seen_ids: Set[str] = set()

    # Strategy 1: h3 > a (main article headings)
    for a in soup.select('h3 a, h2 a, .post-title a, .entry-title a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not _is_valid_text(text, min_len=3):
            continue
        if text in seen:
            continue
        if not re.search(r'10000yun\.com/\d+\.html', href) and not re.search(r'^/\d+\.html$', href):
            continue
        # Dedup by article ID
        id_match = re.search(r'/(\d+)\.html', href)
        if id_match:
            aid = id_match.group(1)
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
        _add_item(items, seen, text, href, base_url)

    # Strategy 2: Fallback - scan all <a> tags
    if len(items) < 3:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not _is_valid_text(text, min_len=5):
                continue
            if not re.search(r'10000yun\.com/\d+\.html', href) and not re.search(r'^/\d+\.html$', href):
                continue
            if text in seen:
                continue
            id_match = re.search(r'/(\d+)\.html', href)
            if id_match:
                aid = id_match.group(1)
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
            skip_words = _make_skip_set(
                '首页', '软件', '电影', '游戏', '专题', '关于', '联系',
                '网站地图', '最新软件', '热门软件')
            if text in skip_words:
                continue
            _add_item(items, seen, text, href, base_url)

    return items[:30]

