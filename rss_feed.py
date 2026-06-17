#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSS/Atom Feed 生成器 — 将线报数据导出为标准 Atom feed。
生成的 feed.xml 通过 GitHub Pages 对外提供，用户可用 RSS 阅读器订阅。
"""

import json
import os
import re
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

FEED_FILE = "feed.xml"
FEED_TITLE = "线报聚合 - 实时监控全网羊毛线报"
FEED_URL = "https://gitfox-enter.github.io/site-update-monitor/feed.xml"
SITE_URL = "https://gitfox-enter.github.io/site-update-monitor/"
FEED_DESCRIPTION = "自动聚合全网羊毛线报、优惠信息、活动促销，实时更新"
FEED_LANGUAGE = "zh-CN"
MAX_FEED_ITEMS = 50  # RSS feed 最大条目数


def generate_atom_feed(output_path: str = FEED_FILE, max_items: int = MAX_FEED_ITEMS) -> bool:
    """Generate an Atom 1.0 feed from items.json.

    Returns True on success, False on failure.
    """
    db = load_items_db()
    items = db.get('items', [])[:max_items]
    updated_at = db.get('updated_at', '')

    if not items:
        return False

    # Atom namespace
    NS = 'http://www.w3.org/2005/Atom'
    ET.register_namespace('', NS)

    root = ET.Element(f'{{{NS}}}feed')

    # Feed metadata
    ET.SubElement(root, f'{{{NS}}}title').text = FEED_TITLE
    ET.SubElement(root, f'{{{NS}}}subtitle').text = FEED_DESCRIPTION
    ET.SubElement(root, f'{{{NS}}}link', href=SITE_URL, rel='alternate')
    ET.SubElement(root, f'{{{NS}}}link', href=FEED_URL, rel='self', type='application/atom+xml')
    ET.SubElement(root, f'{{{NS}}}id').text = SITE_URL
    ET.SubElement(root, f'{{{NS}}}updated').text = _to_iso8601(updated_at)
    ET.SubElement(root, f'{{{NS}}}generator', uri='https://github.com/gitfox-enter/site-update-monitor').text = 'site-update-monitor'

    author = ET.SubElement(root, f'{{{NS}}}author')
    ET.SubElement(author, f'{{{NS}}}name').text = '线报聚合'

    # Feed entries
    for item in items:
        entry = ET.SubElement(root, f'{{{NS}}}entry')

        title = _sanitize_xml(item.get('text', '无标题'))
        # Atom title requires type="html" if content has special chars
        title_el = ET.SubElement(entry, f'{{{NS}}}title')
        title_el.text = title
        title_el.set('type', 'text')

        url = _sanitize_xml(item.get('url', ''))
        ET.SubElement(entry, f'{{{NS}}}link', href=url, rel='alternate')
        ET.SubElement(entry, f'{{{NS}}}id').text = url or f"tag:gitfox-enter,{updated_at[:10]}:{hash(title)}"

        time_str = item.get('time', updated_at)
        ET.SubElement(entry, f'{{{NS}}}updated').text = _to_iso8601(time_str)
        ET.SubElement(entry, f'{{{NS}}}published').text = _to_iso8601(time_str)

        # Content summary
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
        content_el.text = content_text
        content_el.set('type', 'text')

        # Category tag
        if category:
            ET.SubElement(entry, f'{{{NS}}}category', term=category)

    # Write to file (atomic)
    tmp_path = output_path + '.tmp'
    try:
        tree = ET.ElementTree(root)
        ET.indent(tree, space='  ')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(f, encoding='unicode', xml_declaration=False)
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def _sanitize_xml(text: str) -> str:
    """Remove characters invalid in XML 1.0 (control chars, surrogates, etc.)."""
    if not text:
        return text
    return _INVALID_XML_RE.sub('', text)


def _to_iso8601(time_str: str) -> str:
    """Convert 'YYYY-MM-DD HH:MM:SS' to ISO 8601 format."""
    if not time_str:
        return datetime.now(timezone(timedelta(hours=8))).isoformat()
    try:
        dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone(timedelta(hours=8))).isoformat()


if __name__ == '__main__':
    success = generate_atom_feed()
    if success:
        print("RSS feed generated successfully")
    else:
        print("Failed to generate RSS feed")
