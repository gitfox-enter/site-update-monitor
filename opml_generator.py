#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPML 生成器 — 从 feeds 目录生成统一的 OPML 订阅列表。

功能:
  - 扫描 feeds/ 目录中的所有 XML 文件
  - 生成扁平结构的 OPML（所有 outline 直接在 body 下）
  - 不生成任何分类子文件夹（兼容所有 RSS 阅读器）
"""

import os
import re
import json
import xml.etree.ElementTree as ET
from typing import List, Dict
from common import slugify

FEEDS_DIR = "feeds"
SITE_URL = "https://gitfox-enter.github.io/RSSForge/"


def _safe_filename(name: str) -> str:
    """ASCII 安全文件名 (fix #9)."""
    return slugify(name)


def _feed_has_entries(filepath: str) -> bool:
    """检查 feed 文件是否包含至少一个 <entry>。"""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        # 处理 Atom namespace
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        if not entries:
            # 尝试无 namespace
            entries = root.findall('entry')
        return len(entries) > 0
    except Exception:
        return False


def _load_feeds_from_directory() -> List[Dict]:
    """从 feeds 目录加载所有非空 XML feed."""
    feeds = []
    
    if not os.path.exists(FEEDS_DIR):
        print(f"警告: {FEEDS_DIR} 目录不存在")
        return feeds
    
    for filename in os.listdir(FEEDS_DIR):
        if filename.endswith('.xml'):
            filepath = os.path.join(FEEDS_DIR, filename)
            
            # 跳过空 feed (fix #1)
            if not _feed_has_entries(filepath):
                continue
            
            feed_name = os.path.splitext(filename)[0]
            feed_url = f"{SITE_URL}{FEEDS_DIR}/{filename}"
            
            # 尝试从 feeds_meta.json 获取更多信息
            html_url = ""
            icon_url = ""
            try:
                if os.path.exists('feeds_meta.json'):
                    with open('feeds_meta.json', 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    if feed_name in meta:
                        html_url = meta[feed_name].get('site_url', '')
                        icon_url = meta[feed_name].get('icon', '')
            except Exception:
                pass
            
            feeds.append({
                'name': feed_name,
                'feed_url': feed_url,
                'html_url': html_url,
                'icon': icon_url,
            })
    
    # 按名称排序
    feeds.sort(key=lambda x: x['name'])
    return feeds


def _build_opml(feeds: List[Dict], title: str) -> ET.Element:
    """构建 OPML 根元素（扁平结构）."""
    root = ET.Element('opml')
    root.set('version', '2.0')

    head = ET.SubElement(root, 'head')
    ET.SubElement(head, 'title').text = title
    ET.SubElement(head, 'ownerName').text = 'RSSForge'
    ET.SubElement(head, 'ownerEmail').text = 'noreply@gitfox-enter.github.io'
    ET.SubElement(head, 'dateCreated').text = '2026-06-18'

    body = ET.SubElement(root, 'body')

    # 扁平结构: 所有订阅直接放在 body 下
    for feed in feeds:
        outline = ET.SubElement(body, 'outline')
        outline.set('type', 'rss')
        outline.set('text', feed['name'])
        outline.set('title', feed['name'])
        outline.set('xmlUrl', feed['feed_url'])
        if feed['html_url']:
            outline.set('htmlUrl', feed['html_url'])
        # 某些阅读器支持 icon
        if feed.get('icon'):
            outline.set('iconUrl', feed['icon'])

    return root


def _write_opml(root: ET.Element, output_path: str) -> bool:
    """写入 OPML 文件（原子写入）."""
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
        print(f"写入 OPML 失败 {output_path}: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False


def generate_opml() -> Dict[str, int]:
    """生成统一 OPML 文件.
    
    Returns:
        dict: {'feeds_count': N, 'opml_generated': 0/1}
    """
    feeds = _load_feeds_from_directory()
    
    if not feeds:
        print("警告: 未找到任何 feed")
        return {'feeds_count': 0, 'opml_generated': 0}

    stats = {
        'feeds_count': len(feeds),
        'opml_generated': 0,
    }

    # 生成扁平结构的统一 OPML
    root = _build_opml(feeds, "RSSForge - 订阅源")
    
    if _write_opml(root, "opml.xml"):
        stats['opml_generated'] = 1
        print(f"✓ OPML 生成成功: {len(feeds)} 个订阅源")
        for feed in feeds:
            print(f"  - {feed['name']}: {feed['feed_url']}")

    return stats


if __name__ == '__main__':
    result = generate_opml()
    print(f"\n完成: {result}")
