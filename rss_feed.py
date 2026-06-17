#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS/Atom Feed 生成器 — 为每个订阅站点生成独立 feed。

输出:
  feeds/线报酷.xml   — 按来源拆分的独立 feed
  feeds/白菜哦.xml   — ...
"""

import json
import os
import re
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
)

# XML 1.0 不允许的控制字符和 Unicode 代理对
_INVALID_XML_RE = re.compile(
    '[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x84\x86-\x9f\ud800-\udfff\ufffe\uffff]'
)

# ============================================================
# 配置
# ============================================================

SITE_URL = "https://gitfox-enter.github.io/RSSForge/"
FEEDS_DIR = "feeds"
FEED_TITLE = "RSSForge"
FEED_DESCRIPTION = "基于 GitHub Actions 的免费 RSS 订阅源生成器"

# ============================================================
# 图标检测（自动为每个 feed 提取对应网站图标）
# ============================================================

_IMG_EXT_RE = __import__('re').compile(r'\.(jpg|jpeg|png|gif|webp|bmp|svg)\b', __import__('re').IGNORECASE)


def _extract_favicon_url(url: str) -> str:
    """根据 URL 返回网站 favicon。

    原理：使用 Google Favicon Proxy 服务
    https://www.google.com/s2/favicons?domain={domain}&sz=64
    - 自动处理 HTTPS/HTTP 混合
    - 自动处理 www/non-www
    - 返回 PNG 格式，大多数 RSS 阅读器都支持
    """
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ''
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


def _extract_item_image(item, site_url: str = '') -> str:
    """从条目字段中提取第一张图片 URL。

    优先级：item['image'] > content 中的 <img> > url 本身是图片文件
    """
    img = item.get('image', '')
    if img and _IMG_EXT_RE.search(img):
        return img

    raw = item.get('content', '') or item.get('html', '') or item.get('summary', '')
    if raw:
        m = __import__('re').search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, __import__('re').IGNORECASE)
        if m and _IMG_EXT_RE.search(m.group(1)):
            return m.group(1)

    url = item.get('url', '')
    if url and _IMG_EXT_RE.search(url):
        return url
    return ''



# 来源名 → 安全文件名映射
def _safe_filename(name: str) -> str:
    """将来源名称转为安全的文件名。"""
    return re.sub(r'[^\w\u4e00-\u9fff]', '', name)


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
    """Convert interval (minutes) to sy:updatePeriod string for RSS readers."""
    if interval_min <= 15:
        return 'hourly'
    elif interval_min <= 60:
        return 'hourly'
    elif interval_min <= 360:
        return 'daily'
    else:
        return 'daily'


def _interval_to_update_frequency(interval_min: int) -> int:
    """Convert interval (minutes) to sy:updateFrequency (times per period)."""
    if interval_min <= 15:
        return 4  # 4 times per hour
    elif interval_min <= 30:
        return 2  # 2 times per hour
    elif interval_min <= 60:
        return 1  # once per hour
    elif interval_min <= 360:
        return 24 * 60 // interval_min  # N times per day
    else:
        return 1  # once per day


def _build_atom_feed(items: List[Dict], title: str, feed_url: str,
                      description: str = "", updated_at: str = "",
                      interval_min: Optional[int] = None) -> ET.Element:
    """构建 Atom feed 根元素（不写文件）。
    
    Args:
        interval_min: 站点抓取间隔（分钟），用于设置 sy:updatePeriod
    """
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
    ET.SubElement(root, f'{{{NS}}}link', href=SITE_URL, rel='alternate')
    ET.SubElement(root, f'{{{NS}}}link', href=feed_url, rel='self', type='application/atom+xml')
    ET.SubElement(root, f'{{{NS}}}id').text = feed_url
    ET.SubElement(root, f'{{{NS}}}updated').text = _to_iso8601(updated_at)
    ET.SubElement(root, f'{{{NS}}}generator', uri='https://github.com/gitfox-enter/RSSForge').text = 'RSSForge'

    # sy:updatePeriod — 让 RSS 阅读器知道更新频率
    if interval_min is not None:
        ET.SubElement(root, f'{{{SY_NS}}}updatePeriod').text = _interval_to_update_period(interval_min)
        ET.SubElement(root, f'{{{SY_NS}}}updateFrequency').text = str(_interval_to_update_frequency(interval_min))
        ET.SubElement(root, f'{{{SY_NS}}}updateBase').text = '2000-01-01T00:00:00+08:00'

    author = ET.SubElement(root, f'{{{NS}}}author')
    ET.SubElement(author, f'{{{NS}}}name').text = 'RSSForge'

    # Feed 级别 icon（Google Favicon Proxy）
    icon_url = _extract_favicon_url(feed_url)
    ET.SubElement(root, f'{{{NS}}}icon').text = icon_url

    for item in items:
        entry = ET.SubElement(root, f'{{{NS}}}entry')

        title_text = _sanitize_xml(item.get('text', '无标题'))
        title_el = ET.SubElement(entry, f'{{{NS}}}title')
        title_el.text = title_text
        title_el.set('type', 'text')

        url = _sanitize_xml(item.get('url', ''))
        ET.SubElement(entry, f'{{{NS}}}link', href=url, rel='alternate')
        ET.SubElement(entry, f'{{{NS}}}id').text = url or f"tag:gitfox-enter,{updated_at[:10]}:{hash(title_text)}"

        time_str = item.get('time', updated_at)
        ET.SubElement(entry, f'{{{NS}}}updated').text = _to_iso8601(time_str)
        ET.SubElement(entry, f'{{{NS}}}published').text = _to_iso8601(time_str)

        source = _sanitize_xml(item.get('source', ''))
        category = _sanitize_xml(item.get('category', ''))
        content_parts = []
        if source:
            content_parts.append(f"来源: {source}")
        if category:
            content_parts.append(f"分类: {category}")
        content_parts.append(f"链接: {url}")
        content_text = ' | '.join(content_parts)

        content_el = ET.SubElement(entry, f'{{{NS}}}content')
        content_el.text = _sanitize_xml(content_text)
        content_el.set('type', 'text')

        # 自动提取条目图片（缩略图）
        thumb = _extract_item_image(item, feed_url)
        if thumb:
            ET.SubElement(entry, f'{{{MEDIA_NS}}}thumbnail', url=thumb, width='300')

        if category:
            ET.SubElement(entry, f'{{{NS}}}category', term=category)

    return root


def _write_feed(root: ET.Element, output_path: str) -> bool:
    """将 Atom feed 写入文件（原子写入）。"""
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


def generate_all_feeds() -> Dict[str, int]:
    """生成每站独立 feed（不再生成全量聚合 feed.xml）。

    Returns:
        dict: {'total': N, 'per_site': {name: count}, 'feeds_generated': M}
    """
    # 加载站点 interval 配置
    try:
        from crawler.config import SOURCE_NAME_MAP
        from crawler.smart_scheduler import load_site_intervals
        site_intervals = load_site_intervals()
        # 构建 name → interval 映射
        _name_intervals: Dict[str, int] = {}
        for url, name in SOURCE_NAME_MAP.items():
            interval = site_intervals.get(url, 30)
            _name_intervals[name] = min(_name_intervals.get(name, 9999), interval)
    except Exception:
        _name_intervals = {}

    db = load_items_db()
    items = db.get('items', [])
    updated_at = db.get('updated_at', '')

    if not items:
        print("无数据，跳过 feed 生成")
        return {'total': 0, 'per_site': {}, 'feeds_generated': 0}

    stats = {'total': len(items), 'per_site': {}, 'feeds_generated': 0}

    # 1. 按来源拆分的 per-site feed
    os.makedirs(FEEDS_DIR, exist_ok=True)

    by_source: Dict[str, List[Dict]] = {}
    for item in items:
        source = item.get('source', '未知')
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(item)

    for source, source_items in by_source.items():
        safe_name = _safe_filename(source)
        filename = f"{FEEDS_DIR}/{safe_name}.xml"
        feed_url = SITE_URL + filename
        title = f"{source} - RSSForge"
        desc = f"{source} 的 RSS 订阅源（由 RSSForge 生成）"
        interval = _name_intervals.get(source, 30)

        root = _build_atom_feed(source_items, title, feed_url, desc, updated_at,
                                interval_min=interval)
        if _write_feed(root, filename):
            stats['feeds_generated'] += 1
            stats['per_site'][source] = len(source_items)

    # 3. 生成 feeds_meta.json（前端展示更新频率用）
    meta = {}
    for source, count in stats['per_site'].items():
        interval = _name_intervals.get(source, 30)
        if interval <= 15:
            freq_label = "每15分钟"
        elif interval <= 30:
            freq_label = f"每{interval}分钟"
        elif interval <= 60:
            freq_label = "每小时"
        elif interval <= 120:
            freq_label = "每2小时"
        elif interval <= 240:
            freq_label = "每4小时"
        elif interval <= 360:
            freq_label = "每6小时"
        elif interval <= 480:
            freq_label = "每8小时"
        else:
            freq_label = f"每{interval // 60}小时"
        # 提取第一个条目的 URL 用于获取 favicon
        su_items = by_source.get(source, [])
        _su = su_items[0].get('url', '') if su_items else ''
        meta[source] = {
            'interval': interval,
            'freq_label': freq_label,
            'count': count,
            'feed_url': SITE_URL + FEEDS_DIR + '/' + _safe_filename(source) + '.xml',
            'icon': _extract_favicon_url(_su) if _su else _extract_favicon_url(SITE_URL),
        }
    try:
        tmp_path = 'feeds_meta.json.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, 'feeds_meta.json')
    except Exception as e:
        print(f"写入 feeds_meta.json 失败: {e}")

    print(f"按来源生成 {len(by_source)} 个独立 feed")
    return stats


if __name__ == '__main__':
    result = generate_all_feeds()
    if result['total'] > 0:
        print(f"完成: {result['total']} 条数据, {result['feeds_generated']} 个 feed 文件")
    else:
        print("无数据，未生成 feed")
