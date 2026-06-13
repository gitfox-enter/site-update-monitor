# -*- coding: utf-8 -*-
"""Crawler storage layer: hash records, notified items, items.json DB management."""

import os
import json
import random
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

from common import (
    ITEMS_DB_FILE, ITEMS_LATEST_FILE, BLACKLIST_FILE, MAX_ITEMS_DB,
    load_items_db, save_items_db, load_blacklist, is_blacklisted,
    build_source_name_index, get_source_name as _get_source_name_by_index,
    calculate_md5, upgrade_to_https, get_beijing_time, auto_categorize,
    ProxyPool, create_proxy_pool,
)
from crawler.config import NOTIFIED_ITEMS_FILE, HASH_RECORD_FILE, REQUEST_DELAY_MIN, REQUEST_DELAY_MAX, BROWSER_PROFILES

logger = logging.getLogger('crawl')

# ============================================================
# 工具函数
# ============================================================

def get_current_round() -> int:
    """
    根据当前小时判断当日第几轮（固定映射，禁止计数器模式）
    - 00:00-03:59 -> 第1轮
    - 04:00-07:59 -> 第2轮
    - 08:00-11:59 -> 第3轮
    - 12:00-15:59 -> 第4轮
    - 16:00-19:59 -> 第5轮
    - 20:00-23:59 -> 第6轮
    """
    hour = get_beijing_time().hour
    if 0 <= hour < 4:
        return 1
    elif 4 <= hour < 8:
        return 2
    elif 8 <= hour < 12:
        return 3
    elif 12 <= hour < 16:
        return 4
    elif 16 <= hour < 20:
        return 5
    else:  # 20 <= hour < 24
        return 6


def get_random_profile() -> Dict[str, Any]:
    """随机返回一组一致的浏览器配置（UA + 指纹 + 语言匹配）"""
    return random.choice(BROWSER_PROFILES)


def get_random_ua() -> str:
    """随机返回一个User-Agent（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['user_agent']


def get_random_fingerprint() -> Dict[str, str]:
    """随机返回一组浏览器指纹头部（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['fingerprint']


def get_random_accept_language() -> str:
    """随机返回一个Accept-Language（从 BROWSER_PROFILES 中选取，保持一致性）"""
    return random.choice(BROWSER_PROFILES)['accept_language']


def get_random_delay() -> float:
    """随机返回请求延迟时间（秒）"""
    return random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)


def get_referer(url: str) -> str:
    """
    根据目标 URL 生成 Referer 头（使用站点自身首页作为 Referer）。
    这增强了请求的真实性，降低被反爬机制拦截的概率。
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


# ============================================================
# 哈希记录管理
# ============================================================

def load_hash_records() -> Dict[str, str]:
    """Load hash records from file. Supports both JSON and legacy url=hash format."""
    records: Dict[str, str] = {}
    if os.path.exists(HASH_RECORD_FILE):
        try:
            with open(HASH_RECORD_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            # Try JSON format first
            if content.startswith('{'):
                data = json.loads(content)
                if isinstance(data, dict):
                    records = data.get('records', data)
            else:
                # Legacy url=hash format
                for line in content.split('\n'):
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        url, md5_hash = line.split('=', 1)
                        records[url.strip()] = md5_hash.strip()
        except Exception as e:
            logger.warning("读取哈希文件失败: %s", e)
    return records


def save_hash_records(records: Dict[str, str]) -> bool:
    """Save hash records as JSON (atomic write)."""
    tmp_file = HASH_RECORD_FILE + '.tmp'
    try:
        data = {
            'schema_version': 2,
            'updated_at': get_beijing_time().isoformat(),
            'records': records,
        }
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, HASH_RECORD_FILE)
        logger.info("哈希文件已更新: %d 条记录", len(records))
        return True
    except Exception as e:
        logger.error("保存哈希文件失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


# ============================================================
# 已通知条目管理（去重）
# ============================================================

def load_notified_items() -> Dict[str, Any]:
    """
    加载已通知条目
    新格式：dict{'items': [{'url', 'text', 'source', 'time'}, ...]}
    旧格式：set of URLs（向后兼容）
    """
    if os.path.exists(NOTIFIED_ITEMS_FILE):
        try:
            with open(NOTIFIED_ITEMS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data  # 新格式直接返回
            elif isinstance(data, list):
                return {'items': [{'url': u} for u in data]}  # 旧格式转新格式
            elif isinstance(data, set):
                return {'items': [{'url': u} for u in data]}
        except Exception as e:
            logger.warning("读取已通知条目文件失败: %s", e)
    return {'items': []}


def save_notified_items(item_dict: Dict[str, Any]) -> bool:
    """保存已通知条目URL集合到文件（原子写入）"""
    tmp_file = NOTIFIED_ITEMS_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(item_dict, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, NOTIFIED_ITEMS_FILE)
        logger.info("已通知条目记录已更新: %s (%d 条)", NOTIFIED_ITEMS_FILE, len(item_dict.get('items', [])))
        return True
    except Exception as e:
        logger.error("保存已通知条目失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


def filter_new_items(items: List[Any], notified: Dict[str, Any]) -> Tuple[List[Any], Set[str]]:
    """
    从条目列表中过滤出未通知过的新条目
    返回：(新条目列表, 本轮新增的URL集合)
    """
    new_items: List[Any] = []
    new_urls: Set[str] = set()
    # Handle both dict (production) and set (legacy) formats
    if isinstance(notified, dict):
        notified_urls = set(
            item['url'] if isinstance(item, dict) else item
            for item in notified.get('items', [])
        )
    else:
        notified_urls = set(notified)
    for item in items:
        item_url = item['url'] if isinstance(item, dict) else item
        if item_url not in notified_urls:
            new_items.append(item)
            new_urls.add(item_url)
    return new_items, new_urls


# ============================================================
# 线报数据库（items.json）- 持久化累积所有历史线报
# ============================================================


def merge_items_into_db(new_item_list: List[Dict[str, str]], check_time: str) -> int:
    """
    将本轮新抓取的线报合并到全量数据库中（按 URL 去重）
    新条目插入到列表头部（最新的在前面）
    保留最近 7 天的数据（按 time 字段）
    """
    db = load_items_db()
    existing_urls = set(item['url'] for item in db['items'])

    # 过滤出真正的新条目，并添加自动分类
    added = 0
    fresh_items: List[Dict[str, Any]] = []
    for item in new_item_list:
        url = item.get('url', '')
        if url and url not in existing_urls:
            # 添加自动分类
            if not item.get('category'):
                item['category'] = auto_categorize(item.get('text', ''))
            fresh_items.append(item)
            existing_urls.add(url)
            added += 1

    # 新条目插到头部
    if fresh_items:
        db['items'] = fresh_items + db['items']

    # 保留最近 7 天的数据（按 time 字段）
    cutoff = (get_beijing_time() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    original_count = len(db['items'])
    db['items'] = [
        item for item in db['items']
        if not item.get('time', '') or item['time'] >= cutoff
    ]
    retained_count = len(db['items'])
    if original_count != retained_count:
        logger.info("7天保留: 移除 %d 条旧数据，保留 %d 条", original_count - retained_count, retained_count)

    # 超出上限时裁剪（保留最新条目）
    if len(db['items']) > MAX_ITEMS_DB:
        removed = len(db['items']) - MAX_ITEMS_DB
        db['items'] = db['items'][:MAX_ITEMS_DB]
        logger.info("裁剪旧条目: 移除 %d 条，保留最新 %d 条", removed, MAX_ITEMS_DB)

    db['updated_at'] = check_time
    save_items_db(db)
    logger.info("新增 %d 条，总计 %d 条", added, len(db['items']))

    # 同时导出 items_latest.json
    export_items_latest_json()

    return added



# ============================================================
# items_latest.json 导出（用于首页快速加载）
# ============================================================

_STICKY_ITEM: Dict[str, Any] = {
    "url": "./alipay-redpacket.html",
    "text": "支付宝每日扫码领红包，大量支付红包等你来拿！",
    "source": "支付宝",
    "category": "置顶",
    "sticky": True,
}


def _ensure_sticky_in_items(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Return a new list with the Alipay sticky item pinned to the top."""
    # Drop any existing sticky-looking items to avoid duplication
    filtered = [it for it in items if it.get("source") != "支付宝"]
    return [_STICKY_ITEM] + filtered


def export_items_latest_json(json_path: str = ITEMS_LATEST_FILE) -> bool:
    """Export items to items_latest.json for fast first-page load.
    
    Format: {"items": [...], "updated_at": "...", "total_items": ...}
    """
    db = load_items_db()
    items = db['items']
    items = _ensure_sticky_in_items(items)
    updated_at = db.get('updated_at', get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"))
    total_count = len(items)
    output = {"items": items, "updated_at": updated_at, "total_items": total_count}
    tmp_file = json_path + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_file, json_path)
        logger.info("已导出 items_latest.json: %d 条", total_count)
        return True
    except Exception as e:
        logger.error("导出 items_latest.json 失败: %s", e)
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return False


def get_existing_urls() -> Set[str]:
    """Get all existing item URLs from items.json."""
    db = load_items_db()
    return set(item['url'] for item in db['items'])
