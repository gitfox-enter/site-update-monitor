#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS/Atom Feed 生成器 — 为每个订阅站点生成独立 feed。

功能:
  - 为 sites.yaml 中配置的每个站点生成独立 feed
  - 自动从网站获取真实 favicon 并缓存到 public/icons/
  - 不生成主聚合 feed.xml（仅 individual feeds + unified OPML）
  - 支持 fulltext 模式（在 sites.yaml 中设置 fulltext: true）
"""

import json
import os
import re
import hashlib
from urllib.parse import urlparse
try:
    import httpx
except ImportError:
    httpx = None
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from common import (
    get_beijing_time,
    load_items_db,
    ITEMS_DB_FILE,
    slugify,
    SITE_URL_BASE,
    fetch_site_favicon,
)

# 导入站点配置
try:
    from crawler.config import (
        SOURCE_NAME_MAP, 
        get_source_name,
        SITE_INTERVALS,
        get_site_tier,
    )
except ImportError:
    SOURCE_NAME_MAP = {}
    SITE_INTERVALS = {}
    get_source_name = lambda url: url
    get_site_tier = lambda url: 'high'

# XML 1.0 不允许的控制字符和 Unicode 代理对
_INVALID_XML_RE = re.compile(
    '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ud800-\udfff\ufffe\uffff]'
)

# ============================================================
# 配置
# ============================================================

SITE_URL = SITE_URL_BASE  # fix #17: 统一从 common.py 获取
FEEDS_DIR = "feeds"
FEED_TITLE = "RSSForge"
FEED_DESCRIPTION = "基于 GitHub Actions 的免费 RSS 订阅源生成器"
ICONS_DIR = "public/icons"
ICONS_URL_PATH = "icons"  # 部署后 URL 路径（public/ 被 GitHub Pages 剥离）


# ============================================================
# Favicon 获取（强制本地存储）
# ============================================================
# ============================================================
# 工具函数
# ============================================================

def _safe_filename(name: str) -> str:
    """将来源名称转为 ASCII 安全的文件名 (fix #9)."""
    return slugify(name)


def _sanitize_xml(text: str) -> str:
    """Remove characters invalid in XML 1.0."""
    if not text:
        return text
    return _INVALID_XML_RE.sub('', text)


def _to_iso8601(time_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' to ISO 8601."""
    if not time_str:
        return datetime.now(timezone(timedelta(hours=8))).isoformat()
    try:
        dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone(timedelta(hours=8))).isoformat()


def _interval_to_update_period(interval_min: int) -> str:
    """Convert interval to sy:updatePeriod."""
    if interval_min <= 60:
        return 'hourly'
    return 'daily'


def _interval_to_update_frequency(interval_min: int) -> int:
    """Convert interval to sy:updateFrequency."""
    if interval_min <= 15:
        return 4
    elif interval_min <= 30:
        return 2
    elif interval_min <= 60:
        return 1
    elif interval_min <= 360:
        return 24 * 60 // interval_min
    return 1


def _compute_items_hash(items: List[Dict]) -> str:
    """计算站点条目列表的哈希值，用于增量生成判断 (#36).

    基于条目的 URL + time + text 生成 MD5，任何字段变化都会导致哈希变化。
    """
    h = hashlib.md5()
    for item in sorted(items, key=lambda x: x.get('url', '')):
        h.update(item.get('url', '').encode('utf-8'))
        h.update(b'|')
        h.update(item.get('time', '').encode('utf-8'))
        h.update(b'|')
        h.update(item.get('text', '').encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


def _load_previous_hashes() -> Dict[str, str]:
    """从 feeds_meta.json 加载上次生成的 items_hash (#36)."""
    try:
        if os.path.exists('feeds_meta.json'):
            with open('feeds_meta.json', 'r', encoding='utf-8') as f:
                meta = json.load(f)
            return {
                name: info.get('items_hash', '')
                for name, info in meta.items()
                if isinstance(info, dict)
            }
    except Exception:
        pass
    return {}


# ============================================================
# Feed 生成
# ============================================================

def _build_atom_feed(
    items: List[Dict], 
    title: str, 
    feed_url: str,
    description: str = "",
    updated_at: str = "",
    interval_min: Optional[int] = None,
    site_name: str = '',
    site_url: str = '',
) -> ET.Element:
    """构建 Atom feed 根元素."""
    from html import escape as html_escape

    NS = 'http://www.w3.org/2005/Atom'
    SY_NS = 'http://purl.org/syndication/1.0'
    MEDIA_NS = 'http://search.yahoo.com/mrss/'
    ET.register_namespace('', NS)
    ET.register_namespace('sy', SY_NS)
    ET.register_namespace('media', MEDIA_NS)

    root = ET.Element(f'{{{NS}}}feed')

    ET.SubElement(root, f'{{{NS}}}title').text = _sanitize_xml(title)
    if description:
        ET.SubElement(root, f'{{{NS}}}subtitle').text = _sanitize_xml(description)

    # rel='alternate' 应指向原始网站而非项目首页 (fix #4)
    alternate_url = site_url or SITE_URL
    ET.SubElement(root, f'{{{NS}}}link', href=alternate_url, rel='alternate')
    ET.SubElement(root, f'{{{NS}}}link', href=feed_url, rel='self', type='application/atom+xml')
    ET.SubElement(root, f'{{{NS}}}id').text = feed_url
    ET.SubElement(root, f'{{{NS}}}updated').text = _to_iso8601(updated_at)
    ET.SubElement(root, f'{{{NS}}}generator', uri='https://github.com/gitfox-enter/RSSForge').text = 'RSSForge'

    # 版权声明 (fix #7)
    ET.SubElement(root, f'{{{NS}}}rights').text = '内容版权归原作者所有，RSSForge 仅提供聚合索引'

    # 更新频率
    if interval_min is not None:
        ET.SubElement(root, f'{{{SY_NS}}}updatePeriod').text = _interval_to_update_period(interval_min)
        ET.SubElement(root, f'{{{SY_NS}}}updateFrequency').text = str(_interval_to_update_frequency(interval_min))
        ET.SubElement(root, f'{{{SY_NS}}}updateBase').text = '2000-01-01T00:00:00+08:00'

    author = ET.SubElement(root, f'{{{NS}}}author')
    ET.SubElement(author, f'{{{NS}}}name').text = 'RSSForge'
    ET.SubElement(author, f'{{{NS}}}uri').text = 'https://github.com/gitfox-enter/RSSForge'

    # Feed 图标 - 强制使用真实网站 favicon
    icon_url = fetch_site_favicon(site_url or feed_url, site_name or title)
    ET.SubElement(root, f'{{{NS}}}icon').text = icon_url

    # 条目
    for idx, item in enumerate(items):
        entry = ET.SubElement(root, f'{{{NS}}}entry')

        title_text = _sanitize_xml(item.get('text', item.get('title', '无标题')))
        title_el = ET.SubElement(entry, f'{{{NS}}}title')
        title_el.text = title_text
        title_el.set('type', 'text')

        url = _sanitize_xml(item.get('url', ''))
        if url:
            ET.SubElement(entry, f'{{{NS}}}link', href=url, rel='alternate')
            # 使用 tag URI 格式保证 id 永久唯一 (fix #7)
            parsed = urlparse(url)
            domain = parsed.hostname or 'unknown'
            path_short = parsed.path.rstrip('/').split('/')[-1] or 'item'
            ET.SubElement(entry, f'{{{NS}}}id').text = f"tag:{domain},{updated_at[:10]}:{path_short}"
        else:
            ET.SubElement(entry, f'{{{NS}}}id').text = f"tag:gitfox-enter,{updated_at[:10]}:{hash(title_text)}"

        # 时间戳处理 (fix #3):
        # - <published> 使用 item 的真实发布时间
        # - <updated> 使用 item 时间或 feed 更新时间（取较新者）
        item_time = item.get('time', '')
        if item_time:
            published_time = _to_iso8601(item_time)
            updated_time = published_time  # 无独立 updated 字段时与 published 相同
        else:
            # 无真实时间时，使用索引偏移避免批量相同时间戳
            from datetime import timedelta as _td
            base_dt = datetime.now(timezone(timedelta(hours=8)))
            offset_dt = base_dt - _td(seconds=idx)
            published_time = offset_dt.isoformat()
            updated_time = published_time

        ET.SubElement(entry, f'{{{NS}}}updated').text = updated_time
        ET.SubElement(entry, f'{{{NS}}}published').text = published_time

        category = _sanitize_xml(item.get('category', ''))

        # 条目级 author：标注来源站点 (fix #7)
        if site_name:
            entry_author = ET.SubElement(entry, f'{{{NS}}}author')
            ET.SubElement(entry_author, f'{{{NS}}}name').text = site_name

        # 内容摘要 (fix #2 + #5)
        summary = item.get('summary', '')

        # 构建 <summary>：优先用 item.summary，其次用 title 截断
        summary_text = summary or (title_text[:200] if title_text else '')
        if summary_text:
            summary_el = ET.SubElement(entry, f'{{{NS}}}summary')
            summary_el.text = _sanitize_xml(summary_text)
            summary_el.set('type', 'text')

        # 构建 <content>：始终包含有意义的文本
        if summary:
            html_content = '<p>' + html_escape(summary) + '</p>'
            if url:
                html_content += f'<p><a href="{html_escape(url)}">查看原文 →</a></p>'
        elif title_text:
            # 没有 summary 时，用标题作为 content 正文
            html_content = '<p>' + html_escape(title_text) + '</p>'
            if url:
                html_content += f'<p><a href="{html_escape(url)}">查看原文 →</a></p>'
        elif url:
            html_content = f'<p><a href="{html_escape(url)}">查看原文 →</a></p>'
        else:
            html_content = '<p>暂无内容</p>'

        content_el = ET.SubElement(entry, f'{{{NS}}}content')
        content_el.text = _sanitize_xml(html_content)
        content_el.set('type', 'html')

        if category:
            ET.SubElement(entry, f'{{{NS}}}category', term=category)

    return root


def _write_feed(root: ET.Element, output_path: str) -> bool:
    """将 Atom feed 写入文件（原子写入）."""
    tmp_path = output_path + '.tmp'
    try:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        tree = ET.ElementTree(root)
        ET.indent(tree, space='  ')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(f, encoding='unicode', xml_declaration=False)
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        print(f"写入 feed 失败 {output_path}: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


# ============================================================
# RSS 2.0 Feed Builder
# ============================================================

def _build_rss2_feed(
    items: List[Dict],
    title: str,
    feed_url: str,
    description: str,
    updated_at: str,
    interval_min: int = 60,
    site_name: str = '',
    site_url: str = '',
) -> ET.Element:
    """构建 RSS 2.0 feed 根元素."""
    root = ET.Element('rss', {'version': '2.0', 'xmlns:atom': 'http://www.w3.org/2005/Atom'})
    channel = ET.SubElement(root, 'channel')
    ET.SubElement(channel, 'title').text = title
    ET.SubElement(channel, 'link').text = site_url or feed_url
    ET.SubElement(channel, 'description').text = description
    ET.SubElement(channel, 'lastBuildDate').text = _to_rfc822(updated_at)
    ET.SubElement(channel, 'language').text = 'zh-cn'
    atom_link = ET.SubElement(channel, '{http://www.w3.org/2005/Atom}link')
    atom_link.set('href', feed_url)
    atom_link.set('rel', 'self')
    atom_link.set('type', 'application/rss+xml')
    ET.SubElement(channel, 'ttl').text = str(max(15, interval_min // 15))

    from html import escape as html_escape
    for item in items[:50]:
        url = item.get('url', '')
        if not url:
            continue
        rss_item = ET.SubElement(channel, 'item')
        ET.SubElement(rss_item, 'title').text = _sanitize_xml(item.get('text', item.get('title', '')))
        ET.SubElement(rss_item, 'link').text = url
        guid = ET.SubElement(rss_item, 'guid')
        guid.text = url
        guid.set('isPermaLink', 'true')
        if item.get('time'):
            ET.SubElement(rss_item, 'pubDate').text = _to_rfc822(item['time'])
        summary = item.get('summary', '')
        title_text = _sanitize_xml(item.get('text', item.get('title', '')))
        html_content = '<p>' + html_escape(summary or title_text) + '</p>'
        if url:
            html_content += f'<p><a href="{html_escape(url)}">查看原文 →</a></p>'
        content_el = ET.SubElement(rss_item, 'description')
        content_el.text = html_content
        category = item.get('category', '')
        if category:
            ET.SubElement(rss_item, 'category').text = _sanitize_xml(category)
    return root


def _to_rfc822(time_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' to RFC 822 date format."""
    if not time_str:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=8))).strftime('%a, %d %b %Y %H:%M:%S +0800')
    try:
        from datetime import datetime, timezone, timedelta
        dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%a, %d %b %Y %H:%M:%S +0800')
    except Exception:
        return time_str


# ============================================================
# 主函数: 为所有配置的站点生成 feed
# ============================================================

def generate_all_feeds() -> Dict[str, int]:
    """为 sites.yaml 中配置的每个站点生成独立 feed.
    
    即使站点暂无数据，也会生成一个空的占位 feed。
    不生成主聚合 feed.xml。
    
    Returns:
        dict: {'feeds_generated': N, 'feeds_skipped': M, 'total_sites': T}
    """
    db = load_items_db()
    items = db.get('items', [])
    updated_at = db.get('updated_at', '')

    stats = {
        'feeds_generated': 0,
        'feeds_skipped': 0,
        'feeds_empty_skipped': 0,
        'feeds_unchanged': 0,
        'total_sites': 0,
        'sites_with_items': 0,
    }

    # 加载上次生成的 items 哈希，用于增量生成 (#36)
    prev_hashes = _load_previous_hashes()

    # 按来源分组已有数据
    by_source: Dict[str, List[Dict]] = {}
    for item in items:
        source = item.get('source', '未知')
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(item)

    # 确保 feeds 目录存在
    os.makedirs(FEEDS_DIR, exist_ok=True)
    
    # 确保 icons 目录存在
    os.makedirs(ICONS_DIR, exist_ok=True)

    # 构建 URL -> Name 映射（从 SOURCE_NAME_MAP）
    url_to_name: Dict[str, str] = {}
    for url, name in SOURCE_NAME_MAP.items():
        url_to_name[url] = name

    # 获取所有配置的站点
    from crawler.config import MONITOR_SITES
    all_sites = MONITOR_SITES if 'MONITOR_SITES' in dir() else []
    
    if not all_sites:
        # 如果无法导入，使用 sites.yaml 原始数据
        try:
            import yaml
            with open('sites.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
            all_sites = [s['url'] for s in config.get('sites', [])]
        except Exception:
            all_sites = []

    stats['total_sites'] = len(all_sites)
    print(f"共配置 {len(all_sites)} 个站点")

    # 为每个站点生成 feed
    for site_url in all_sites:
        # 获取站点名称
        site_name = url_to_name.get(site_url, '')
        if not site_name:
            # 从 URL 提取域名作为备用名称
            parsed = urlparse(site_url)
            site_name = parsed.hostname or site_url
        
        safe_name = _safe_filename(site_name)
        filename = f"{FEEDS_DIR}/{safe_name}.xml"
        feed_url = SITE_URL + filename
        
        # 获取站点间隔配置
        interval = SITE_INTERVALS.get(site_url, 30)
        
        # 获取该站点的数据
        site_items = by_source.get(site_name, [])
        
        # 跳过空 feed：不生成无数据的 feed 文件 (fix #1)
        if not site_items:
            stats['feeds_empty_skipped'] += 1
            # 如果之前存在旧的空 feed 文件，删除它
            if os.path.exists(filename):
                os.remove(filename)
                print(f"  ✗ {site_name}: 移除旧的空 feed")
            else:
                print(f"  ○ {site_name}: 暂无数据，跳过")
            continue
        
        # 构建 feed
        title = f"{site_name} - RSSForge"
        desc = f"{site_name} 的 RSS 订阅源（由 RSSForge 生成）"
        
        # 按时间排序
        site_items = sorted(site_items, key=lambda x: x.get('time', ''), reverse=True)
        stats['sites_with_items'] += 1

        # ---- 增量生成：比对 items 哈希 (#36) ----
        current_hash = _compute_items_hash(site_items)
        prev_hash = prev_hashes.get(site_name, '')
        if prev_hash and prev_hash == current_hash and os.path.exists(filename):
            stats['feeds_unchanged'] += 1
            continue  # 数据无变化且 feed 文件存在，跳过生成

        root = _build_atom_feed(
            site_items, title, feed_url, desc, updated_at,
            interval_min=interval,
            site_name=site_name,
            site_url=site_url,
        )
        
        if _write_feed(root, filename):
            stats['feeds_generated'] += 1
            # Also generate RSS 2.0 version (#38)
            rss2_root = _build_rss2_feed(
                site_items, title, feed_url, desc, updated_at,
                interval_min=interval, site_name=site_name, site_url=site_url,
            )
            rss2_filename = filename.replace('.xml', '.rss2.xml')
            _write_feed(rss2_root, rss2_filename)
            print(f"  ✓ {site_name}: {len(site_items)} 条")
        else:
            stats['feeds_skipped'] += 1

    # ---- 清理旧的 feed 和 icon 文件 ----
    # 收集本次生成的有效 feed 文件名
    generated_feed_files = set()
    generated_icon_names = set()
    for site_url_key, name in url_to_name.items():
        sn = _safe_filename(name)
        generated_feed_files.add(sn + '.xml')
        generated_icon_names.add(sn)

    # 删除 feeds/ 中不属于本次生成结果的 .xml 文件
    if os.path.isdir(FEEDS_DIR):
        for f in os.listdir(FEEDS_DIR):
            if f.endswith('.xml') and f not in generated_feed_files:
                old_path = os.path.join(FEEDS_DIR, f)
                os.remove(old_path)
                print(f"  🗑 清理旧 feed: {f}")

    # 删除 public/icons/ 中含中文的旧图标文件
    if os.path.isdir(ICONS_DIR):
        _chinese_re = re.compile(r'[\u4e00-\u9fff]')
        for f in os.listdir(ICONS_DIR):
            if _chinese_re.search(f):
                old_path = os.path.join(ICONS_DIR, f)
                os.remove(old_path)
                print(f"  🗑 清理旧 icon: {f}")

    # 生成 feeds_meta.json
    _generate_feeds_meta(stats, by_source)

    print(f"\n完成: {stats['feeds_generated']} 个 feed 生成, {stats['feeds_unchanged']} 个未变化跳过, "
          f"{stats['sites_with_items']} 个站点有数据, {stats['feeds_empty_skipped']} 个空 feed 跳过")
    return stats


def _generate_feeds_meta(stats: Dict, by_source: Dict[str, List[Dict]]) -> None:
    """生成 feeds_meta.json 用于前端展示.
    
    仅包含有数据的站点，空 feed 不写入 meta (fix #1)。
    同时记录每个站点的 items_hash 用于增量生成 (#36)。
    """
    meta = {}
    
    from crawler.config import SOURCE_NAME_MAP, SITE_INTERVALS
    
    for url, name in SOURCE_NAME_MAP.items():
        # 跳过无数据的站点
        site_items = by_source.get(name, [])
        items_count = len(site_items)

        safe_name = _safe_filename(name)
        interval = SITE_INTERVALS.get(url, 30)
        
        # 强制获取本地 favicon
        icon_url = fetch_site_favicon(url, name)
        
        # 计算频率标签
        if interval <= 15:
            freq_label = "每15分钟"
        elif interval <= 30:
            freq_label = f"每{interval}分钟"
        elif interval <= 60:
            freq_label = "每小时"
        elif interval <= 120:
            freq_label = "每2小时"
        else:
            freq_label = f"每{interval // 60}小时"
        
        meta[name] = {
            'interval': interval,
            'freq_label': freq_label,
            'count': items_count,
            'feed_url': f"{SITE_URL}{FEEDS_DIR}/{safe_name}.xml",
            'icon': icon_url,
            'site_url': url,
            'items_hash': _compute_items_hash(site_items) if site_items else '',  # (#36)
        }
    
    try:
        tmp_path = 'feeds_meta.json.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, 'feeds_meta.json')
        print(f"feeds_meta.json 已更新 ({len(meta)} 个站点)")
    except Exception as e:
        print(f"写入 feeds_meta.json 失败: {e}")


if __name__ == '__main__':
    result = generate_all_feeds()
