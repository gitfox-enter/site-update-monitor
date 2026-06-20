#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPML 生成器 — 从 feeds 目录和 feeds_meta.json 生成统一的 OPML 订阅列表。

功能:
  - 以 feeds_meta.json 为主要数据源，feeds/ 目录为补充
  - 自动跳过中文命名的重复 feed 文件（仅保留 ASCII 安全文件名的版本）
  - 使用中文站名作为 OPML 显示标题
  - 自动填充 htmlUrl、iconUrl 元数据
  - 动态日期（不硬编码）
  - 扁平结构（兼容所有 RSS 阅读器）
"""

import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from common import slugify, SITE_URL_BASE

FEEDS_DIR = "feeds"
SITE_URL = SITE_URL_BASE  # fix #17: 统一从 common.py 获取


def _has_cjk(s: str) -> bool:
    """检查字符串是否包含 CJK 字符。"""
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf]', s))


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


def _feed_entry_count(filepath: str) -> int:
    """返回 feed 文件中的 <entry> 数量。"""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('atom:entry', ns)
        if not entries:
            entries = root.findall('entry')
        return len(entries)
    except Exception:
        return 0


def _load_feeds_meta() -> Dict:
    """加载 feeds_meta.json。"""
    try:
        if os.path.exists('feeds_meta.json'):
            with open('feeds_meta.json', 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _build_slug_to_meta(feeds_meta: Dict) -> Dict[str, Tuple[str, Dict]]:
    """从 feeds_meta.json 构建 slug -> (中文站名, 元数据) 映射。

    feeds_meta.json 的 key 是中文站名（如 "线报酷"），
    其中 feed_url 字段包含 pinyin slug 文件名（如 xian-bao-ku.xml）。
    此函数提取 slug 并建立反向映射。
    """
    slug_map = {}
    for chinese_name, meta in feeds_meta.items():
        feed_url = meta.get('feed_url', '')
        # 从 URL 中提取文件名（不含扩展名）
        if feed_url:
            basename = os.path.basename(feed_url)
            slug = os.path.splitext(basename)[0]
            if slug:
                slug_map[slug] = (chinese_name, meta)
    return slug_map


def _try_load_source_name_map() -> Dict[str, str]:
    """尝试加载 SOURCE_NAME_MAP（slug -> 中文名）作为后备。"""
    try:
        from crawler.config import SOURCE_NAME_MAP
        # SOURCE_NAME_MAP 是 url -> name 的映射
        # 我们需要 name -> slug 的反向映射
        name_to_slug = {}
        for url, name in SOURCE_NAME_MAP.items():
            slug = slugify(name)
            name_to_slug[slug] = name
        return name_to_slug
    except Exception:
        return {}


def _load_feeds() -> List[Dict]:
    """从 feeds 目录和 feeds_meta.json 加载所有有效 feed。

    策略：
    1. 加载 feeds_meta.json，建立 slug → (中文名, 元数据) 映射
    2. 扫描 feeds/ 目录
    3. 跳过含 CJK 字符的文件名（这些是中文命名重复文件）
    4. 对每个 pinyin-slug 文件查找对应的中文名和元数据
    5. 仅包含至少有 1 个 entry 的 feed
    """
    feeds_meta = _load_feeds_meta()
    slug_to_meta = _build_slug_to_meta(feeds_meta)
    slug_to_chinese = _try_load_source_name_map()

    feeds = []
    skipped_chinese = []

    if not os.path.exists(FEEDS_DIR):
        print(f"警告: {FEEDS_DIR} 目录不存在")
        return feeds

    for filename in sorted(os.listdir(FEEDS_DIR)):
        if not filename.endswith('.xml'):
            continue

        feed_name = os.path.splitext(filename)[0]

        # 跳过中文命名的文件（重复文件，已有对应的 pinyin-slug 版本）
        if _has_cjk(feed_name):
            skipped_chinese.append(filename)
            continue

        filepath = os.path.join(FEEDS_DIR, filename)

        # 跳过空 feed (fix #1)
        if not _feed_has_entries(filepath):
            continue

        feed_url = f"{SITE_URL}{FEEDS_DIR}/{filename}"
        html_url = ""
        icon_url = ""
        display_name = feed_name  # 默认使用 slug

        # 从 feeds_meta.json 查找元数据
        if feed_name in slug_to_meta:
            chinese_name, meta = slug_to_meta[feed_name]
            display_name = chinese_name
            html_url = meta.get('site_url', '')
            icon_url = meta.get('icon', '')
        elif feed_name in slug_to_chinese:
            # 从 SOURCE_NAME_MAP 后备查找中文名
            display_name = slug_to_chinese[feed_name]

        feeds.append({
            'name': display_name,
            'slug': feed_name,
            'feed_url': feed_url,
            'html_url': html_url,
            'icon': icon_url,
        })

    if skipped_chinese:
        print(f"跳过 {len(skipped_chinese)} 个中文命名的重复 feed 文件: "
              f"{', '.join(skipped_chinese[:5])}{'...' if len(skipped_chinese) > 5 else ''}")

    # 按中文名称排序
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
    ET.SubElement(head, 'dateCreated').text = datetime.utcnow().strftime('%Y-%m-%d')

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


def _cleanup_legacy_files() -> int:
    """清理遗留的分类 OPML 文件和中文命名的 feed 文件。

    Returns:
        删除的文件数量
    """
    import glob
    removed = 0

    # 删除遗留的分类 OPML 文件 (opml-*.xml)
    for f in glob.glob('opml-*.xml'):
        try:
            os.remove(f)
            removed += 1
            print(f"  清理遗留 OPML: {f}")
        except Exception as e:
            print(f"  清理失败 {f}: {e}")

    # 删除中文命名的重复 feed 文件
    if os.path.exists(FEEDS_DIR):
        for filename in os.listdir(FEEDS_DIR):
            if not filename.endswith('.xml'):
                continue
            feed_name = os.path.splitext(filename)[0]
            if _has_cjk(feed_name):
                filepath = os.path.join(FEEDS_DIR, filename)
                # 检查是否有对应的 pinyin-slug 版本
                slug = slugify(feed_name)
                slug_path = os.path.join(FEEDS_DIR, f"{slug}.xml")
                if os.path.exists(slug_path):
                    try:
                        os.remove(filepath)
                        removed += 1
                        print(f"  清理重复 feed: {filepath} (保留 {slug_path})")
                    except Exception as e:
                        print(f"  清理失败 {filepath}: {e}")

    return removed


def generate_opml() -> Dict[str, int]:
    """生成统一 OPML 文件.

    Returns:
        dict: {'feeds_count': N, 'opml_generated': 0/1, 'cleaned': N}
    """
    # 先清理遗留文件
    cleaned = _cleanup_legacy_files()

    feeds = _load_feeds()

    if not feeds:
        print("警告: 未找到任何 feed")
        return {'feeds_count': 0, 'opml_generated': 0, 'cleaned': cleaned}

    stats = {
        'feeds_count': len(feeds),
        'opml_generated': 0,
        'cleaned': cleaned,
    }

    # 生成扁平结构的统一 OPML
    root = _build_opml(feeds, "RSSForge - 订阅源")

    if _write_opml(root, "opml.xml"):
        stats['opml_generated'] = 1
        print(f"✓ OPML 生成成功: {len(feeds)} 个订阅源")
        for feed in feeds:
            extra = []
            if feed['html_url']:
                extra.append(f"htmlUrl={feed['html_url']}")
            if feed.get('icon'):
                extra.append("has_icon")
            extra_str = f" ({', '.join(extra)})" if extra else ""
            print(f"  - {feed['name']}: {feed['feed_url']}{extra_str}")

    return stats


if __name__ == '__main__':
    result = generate_opml()
    print(f"\n完成: {result}")
