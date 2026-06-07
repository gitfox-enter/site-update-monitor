#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速增量检查器 - 高频抓取 top 站点，追加新线报到 items.json
设计目标：30-60 秒内完成，适合 GitHub Actions 每 5 分钟运行一次
"""

import os
import sys
import time
import json
import random
import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================

ITEMS_DB_FILE = "items.json"
FAST_LOG_FILE = "fast_log.jsonl"

# 高频检查站点（按活跃度排序的 top 12）
FAST_SITES = [
    {"url": "https://www.zhuanyes.com/xianbao/", "name": "专业线报"},
    {"url": "https://news.ixbk.net/", "name": "线报酷"},
    {"url": "https://news.ixbk.fun/", "name": "好赚线报"},
    {"url": "http://www.0818tuan.com/", "name": "0818团"},
    {"url": "https://www.huifabu.cn/", "name": "汇发部"},
    {"url": "https://cjx8.com/", "name": "促销吧"},
    {"url": "https://xianbao.icu/", "name": "线报ICU"},
    {"url": "https://www.baicaio.com/", "name": "拔草哦"},
    {"url": "https://www.iqnew.com/", "name": "爱Q生活"},
    {"url": "https://www.51kanong.com/", "name": "卡农羊毛"},
    {"url": "https://v1.xianbao.net/", "name": "新赚吧"},
    {"url": "http://www.xiaodigu.com/", "name": "小嘀咕"},
]

# 爬虫配置
REQUEST_TIMEOUT = 10
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# 关键词自动分类
CATEGORY_KEYWORDS = {
    "京东": ["京东", "jd.com", "jd", "京豆", "京享"],
    "淘宝": ["淘宝", "天猫", "tmall", "taobao", "淘金币"],
    "拼多多": ["拼多多", "pdd", "拼多"],
    "外卖": ["外卖", "美团", "饿了么", "美团外卖"],
    "红包": ["红包", "虹包", "鸿包", "必中红包"],
    "优惠券": ["优惠券", "券", "满减", "消费券", "领券"],
}

# 过滤词
JUNK_PATTERNS = [
    "安卓软件", "办公软件", "安全软件", "查看详情", "直达链接", "阅读全文",
    "继续阅读", "更多", "首页", "登录", "注册", "搜索", "javascript:",
    "关于我们", "联系我们", "免责声明", "版权声明", "友情链接",
]


def get_beijing_time():
    return datetime.now(timezone(timedelta(hours=8)))


def auto_categorize(text):
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return None


def is_junk(text):
    if len(text) < 5:
        return True
    if text.isdigit():
        return True
    clean = text.replace(" ", "")
    for jp in JUNK_PATTERNS:
        if clean == jp:
            return True
    return False


# ============================================================
# 数据层
# ============================================================

def load_items_db():
    if os.path.exists(ITEMS_DB_FILE):
        try:
            with open(ITEMS_DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'items' in data:
                return data
        except Exception:
            pass
    return {'items': [], 'updated_at': ''}


def save_items_db(db):
    try:
        with open(ITEMS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, separators=(',', ':'))
        return True
    except Exception as e:
        print(f"[错误] 保存失败: {e}")
        return False


# ============================================================
# 抓取 & 解析
# ============================================================

def fetch_and_extract(site):
    """抓取单个站点并提取线报条目"""
    url = site['url']
    name = site['name']
    ua = random.choice(USER_AGENTS)
    headers = {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        if resp.status_code != 200:
            return name, url, [], f"HTTP {resp.status_code}"

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 移除干扰元素
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
            tag.decompose()

        body = soup.find('body')
        if not body:
            return name, url, [], "无 body"

        items = []
        seen = set()

        # 提取 <a> 标签中的链接条目
        for a_tag in body.find_all('a', href=True):
            text = a_tag.get_text(strip=True)
            if not text or is_junk(text):
                continue
            text = ' '.join(text.split())
            if len(text) > 120:
                continue
            if text in seen:
                continue

            href = a_tag['href'].strip()
            if href.startswith('javascript:') or href.startswith('#'):
                continue
            if href.startswith('/') or not href.startswith('http'):
                href = urljoin(url, href)

            seen.add(text)
            items.append({
                'url': href,
                'text': text,
                'source': name,
                'time': get_beijing_time().strftime('%Y-%m-%d %H:%M:%S'),
                'category': auto_categorize(text),
            })

        return name, url, items, None

    except requests.exceptions.Timeout:
        return name, url, [], "超时"
    except Exception as e:
        return name, url, [], str(e)[:80]


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 50)
    print(f"[快速检查] 开始 {get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 1. 先 git pull 获取最新数据
    try:
        subprocess.run(['git', 'pull', '--rebase', '--strategy-option=theirs', 'origin', 'main'],
                       capture_output=True, timeout=30)
        print("[Git] 已拉取最新数据")
    except Exception as e:
        print(f"[Git] 拉取失败（继续）: {e}")

    # 2. 加载现有数据库
    db = load_items_db()
    existing_urls = set(item['url'] for item in db['items'])
    print(f"[数据] 现有 {len(db['items'])} 条线报")

    # 3. 并发抓取 top 站点
    all_new_items = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_and_extract, site): site for site in FAST_SITES}
        for future in as_completed(futures):
            site = futures[future]
            name, url, items, error = future.result()
            if error:
                print(f"  [失败] {name}: {error}")
                continue

            # 过滤已存在的
            fresh = [it for it in items if it['url'] not in existing_urls]
            if fresh:
                print(f"  [新增] {name}: {len(fresh)} 条新线报 (共提取 {len(items)} 条)")
                all_new_items.extend(fresh)
                for it in fresh:
                    existing_urls.add(it['url'])
            else:
                print(f"  [正常] {name}: 无新内容 (提取 {len(items)} 条)")

    # 4. 合并到数据库
    if all_new_items:
        db['items'] = all_new_items + db['items']
        db['updated_at'] = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
        save_items_db(db)
        print(f"\n[结果] 新增 {len(all_new_items)} 条，总计 {len(db['items'])} 条")
    else:
        print(f"\n[结果] 无新增，保持 {len(db['items'])} 条")

    # 5. 记录运行日志
    log_entry = {
        'time': get_beijing_time().strftime('%Y-%m-%d %H:%M:%S'),
        'new_items': len(all_new_items),
        'total': len(db['items']),
        'sites_checked': len(FAST_SITES),
    }
    try:
        with open(FAST_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    except Exception:
        pass

    # 6. Git 提交
    if all_new_items:
        try:
            subprocess.run(['git', 'add', ITEMS_DB_FILE, FAST_LOG_FILE], capture_output=True, timeout=10)
            result = subprocess.run(
                ['git', 'commit', '-m', f'快速更新: 新增 {len(all_new_items)} 条线报 ({get_beijing_time().strftime("%H:%M")})'],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                # 推送（带重试）
                for attempt in range(3):
                    push_result = subprocess.run(
                        ['git', 'push', 'origin', 'main'],
                        capture_output=True, timeout=30
                    )
                    if push_result.returncode == 0:
                        print("[Git] 已推送")
                        break
                    time.sleep(3)
                    subprocess.run(['git', 'pull', '--rebase', '--strategy-option=theirs', 'origin', 'main'],
                                   capture_output=True, timeout=30)
                else:
                    print("[Git] 推送失败")
            else:
                print("[Git] 无变更需要提交")
        except Exception as e:
            print(f"[Git] 提交失败: {e}")

    print("=" * 50)


if __name__ == '__main__':
    main()
