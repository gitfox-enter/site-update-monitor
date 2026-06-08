#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 多站点更新监控系统
功能：爬取33个站点 → MD5比对检测更新 → 数据持久化到 items.json
时间：每小时执行一次
时区：Asia/Shanghai（北京时间）
"""

import os
import sys
import time
import random
import hashlib
import requests
import warnings
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from urllib.parse import urlparse
import json
import subprocess

# 忽略 BeautifulSoup 的 XML 当 HTML 解析警告
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ============================================================
# 配置区域
# ============================================================

# 32个监控站点（新增：聚合线报/鲸线报/那些免费的砖/慢慢买/拔草哦/薅羊毛小伙伴）
MONITOR_SITES = [
    "https://axutongxue.net/",
    "http://79tao.linejia.com/",
    "http://news.ixbk.net/",
    "http://www.0818tuan.com/",
    "https://907k.cn/",
    "https://b1.ymxianbao.cn/",
    "https://cjx8.com/",
    "https://m.hybase.com/",
    "https://news.ixbk.fun/",
    "https://www.007ymd.com/",
    "https://www.12345pro.com/",
    "https://www.423down.com/",
    "https://www.appinn.com/",
    "https://www.bacaoo.com/",
    "https://www.baicaio.com/",
    "https://www.daydayzhuan.com/",
    "https://www.h6room.com/",
    "https://www.huifabu.cn/",
    "https://www.huodong5.com/",
    "https://www.ithome.com/zt/xijiayi",
    "https://www.kxdao.net/forum.php?forumlist=1&mobile=2",
    "https://www.lsapk.com/",
    "https://www.manmanbuy.com/",
    "https://www.thosefree.com/",
    "https://www.wycad.com/",
    "https://www.yangmaodang.club/",
    "https://www.yxssp.com/",
    "https://www.zhuanyes.com/xianbao/",
    "https://www.ziyuanting.com/",
    "https://xianbao.icu/",
    "https://xianbaomi.com/",
    "https://xzba.cc/",
    "https://yangmao.wang/",
    # === 果核剥壳 ===
    "https://www.ghxi.com/",
    # === 新增源站（来自 huifabu.cn 参考） ===
    "https://www.iqnew.com/",
    "https://www.51kanong.com/",
    "https://v1.xianbao.net/",
    "http://www.xiaodigu.com/",
    "https://www.douban.com/group/711811/",
    "https://www.haodanku.com/",
]

# URL → 短名称映射（统一来源显示名称，避免使用页面标题导致名称过长/重复）
SOURCE_NAME_MAP = {
    "https://axutongxue.net/": "爱Q生活",
    "http://79tao.linejia.com/": "79淘",
    "http://news.ixbk.net/": "线报酷",
    "http://www.0818tuan.com/": "0818团",
    "https://907k.cn/": "907线报",
    "https://b1.ymxianbao.cn/": "羊毛线报",
    "https://cjx8.com/": "超级线报",
    "https://m.hybase.com/": "好赚网",
    "https://news.ixbk.fun/": "线报酷",
    "https://www.007ymd.com/": "007羊毛党",
    "https://www.12345pro.com/": "12345线报",
    "https://www.423down.com/": "423Down",
    "https://www.appinn.com/": "小众软件",
    "https://www.bacaoo.com/": "拔草哦",
    "https://www.baicaio.com/": "白菜哦",
    "https://www.daydayzhuan.com/": "天天赚",
    "https://www.h6room.com/": "H6线报",
    "https://www.huifabu.cn/": "汇发部",
    "https://www.huodong5.com/": "活动5",
    "https://www.ithome.com/zt/xijiayi": "IT之家",
    "https://www.kxdao.net/forum.php?forumlist=1&mobile=2": "开心赚",
    "https://www.lsapk.com/": "LSapk",
    "https://www.manmanbuy.com/": "慢慢买",
    "https://www.thosefree.com/": "免费族",
    "https://www.wycad.com/": "网赚",
    "https://www.yangmaodang.club/": "羊毛党",
    "https://www.yxssp.com/": "优惠线报",
    "https://www.zhuanyes.com/xianbao/": "专业线报",
    "https://www.ziyuanting.com/": "资源厅",
    "https://xianbao.icu/": "线报ICU",
    "https://xianbaomi.com/": "线报迷",
    "https://xzba.cc/": "新赚吧",
    "https://yangmao.wang/": "羊毛王",
    "https://www.iqnew.com/": "爱Q社区",
    "https://www.51kanong.com/": "51卡农",
    "https://v1.xianbao.net/": "线报网",
    "http://www.xiaodigu.com/": "小嘀咕",
    "https://www.douban.com/group/711811/": "豆瓣小组",
    "https://www.haodanku.com/": "好单库",
    "https://www.ghxi.com/": "果核剥壳",
}

def get_source_name(url):
    """根据 URL 获取统一短名称"""
    for base_url, name in SOURCE_NAME_MAP.items():
        if url.startswith(base_url.rstrip('/')):
            return name
    return None

# 文件存储配置
HASH_RECORD_FILE = "hash_record.txt"
NOTIFIED_ITEMS_FILE = "notified_items.json"  # 记录已通知过的条目URL，避免重复推送
RUN_LOG_FILE = "run_log.jsonl"  # 每轮运行日志（JSONL格式），用于追踪历史与自检
FAILED_SITES_FILE = "failed_sites.json"  # 连续失败站点记录，自动建议移除
PAUSED_SITES_FILE = "paused_sites.json"  # 因连续失败被暂停的站点
BLACKLIST_FILE = "blacklist.json"  # 网站黑名单（用户讨厌/付费墙/反爬/无法访问/纯工具页）
ITEMS_DB_FILE = "items.json"  # 全量线报数据库（持久化累积，供前端 SPA 加载）

# 自动移除/恢复配置
MAX_CONSECUTIVE_FAILURES = 3  # 连续失败 N 轮后自动暂停
RECOVERY_CHECK_INTERVAL = 6  # 每 N 轮尝试恢复一次暂停站点
MAX_ITEMS_DB = 1500  # items.json 最多保留条目数（控制文件体积，~84KB gzip）

# 爬虫配置
REQUEST_TIMEOUT = 15  # 单个站点超时时间（秒）
REQUEST_DELAY_MIN = 0.5  # 请求间隔最小值（秒）
REQUEST_DELAY_MAX = 1.5  # 请求间隔最大值（秒）

# 随机User-Agent池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edg/120.0.0.0 Safari/537.36",
]

# 浏览器指纹池（与 UA 配合随机化，增加反爬抗性）
BROWSER_FINGERPRINTS = [
    {
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    },
    {
        'sec-ch-ua': '"Not/A_Brand";v="8", "Chromium";v="125", "Google Chrome";v="125"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    },
    {
        'sec-ch-ua': '"Not A Brand";v="99", "Google Chrome";v="119", "Chromium";v="119"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
    },
    {
        'sec-ch-ua': '"Not A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
    },
    {
        'sec-ch-ua': '"Not_A Brand";v="8", "Firefox";v="121"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    },
    {
        'sec-ch-ua': '"Not)A;Brand";v="99", "Firefox";v="127"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    },
    {
        'sec-ch-ua': '"Chromium";v="120", "Not A Brand";v="24", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
    },
    {
        'sec-ch-ua': '"Not A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
    },
]

# Accept-Language 随机池
ACCEPT_LANGUAGES = [
    'zh-CN,zh;q=0.9,en;q=0.8',
    'zh-CN,zh;q=0.9',
    'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'zh-TW,zh-CN;q=0.9,zh;q=0.8,en;q=0.7',
    'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
]

# ============================================================
# 工具函数
# ============================================================

def get_beijing_time():
    """获取北京时间（Asia/Shanghai）"""
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(beijing_tz)


def get_current_round():
    """
    根据当前小时判断当日第几轮（固定映射，禁止计数器模式）
    - 00:00-03:59 → 第1轮
    - 04:00-07:59 → 第2轮
    - 08:00-11:59 → 第3轮
    - 12:00-15:59 → 第4轮
    - 16:00-19:59 → 第5轮
    - 20:00-23:59 → 第6轮
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


def get_random_ua():
    """随机返回一个User-Agent"""
    return random.choice(USER_AGENTS)


def get_random_fingerprint():
    """随机返回一组浏览器指纹头部"""
    return random.choice(BROWSER_FINGERPRINTS)


def get_random_accept_language():
    """随机返回一个Accept-Language"""
    return random.choice(ACCEPT_LANGUAGES)


def get_random_delay():
    """随机返回请求延迟时间（秒）"""
    return random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)


# ============================================================
# 哈希记录管理
# ============================================================

def load_hash_records():
    """
    从文件加载哈希记录
    返回格式：{url: md5_hash}
    """
    records = {}
    if os.path.exists(HASH_RECORD_FILE):
        try:
            with open(HASH_RECORD_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        url, md5_hash = line.split('=', 1)
                        records[url.strip()] = md5_hash.strip()
        except Exception as e:
            print(f"[错误] 读取哈希文件失败: {e}")
    return records


def save_hash_records(records):
    """
    保存哈希记录到文件
    格式：url=md5值（每行一个）
    """
    try:
        with open(HASH_RECORD_FILE, 'w', encoding='utf-8') as f:
            f.write("# 站点哈希记录文件 - 格式: url=md5值\n")
            f.write("# 最后更新: {}\n".format(get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')))
            for url, md5_hash in records.items():
                f.write(f"{url}={md5_hash}\n")
        print(f"[信息] 哈希文件已更新: {HASH_RECORD_FILE}")
        return True
    except Exception as e:
        print(f"[错误] 保存哈希文件失败: {e}")
        return False


# ============================================================
# 已通知条目管理（去重）
# ============================================================

def load_notified_items():
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
            print(f"[警告] 读取已通知条目文件失败: {e}")
    return {'items': []}


def save_notified_items(item_dict):
    """保存已通知条目URL集合到文件"""
    try:
        with open(NOTIFIED_ITEMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(item_dict, f, ensure_ascii=False, indent=2)
        print(f"[信息] 已通知条目记录已更新: {NOTIFIED_ITEMS_FILE} ({len(item_dict.get('items', []))} 条)")
        return True
    except Exception as e:
        print(f"[错误] 保存已通知条目失败: {e}")
        return False


def filter_new_items(items, notified):
    """
    从条目列表中过滤出未通知过的新条目
    返回：(新条目列表, 本轮新增的URL集合)
    """
    new_items = []
    new_urls = set()
    for item in items:
        item_url = item['url'] if isinstance(item, dict) else item
        if item_url not in notified:
            new_items.append(item)
            new_urls.add(item_url)
    return new_items, new_urls


# ============================================================
# 线报数据库（items.json）- 持久化累积所有历史线报
# ============================================================

# 内置关键词分类规则
CATEGORY_KEYWORDS = {
    "京东": ["京东", "jd.com", "jd", "京豆", "京享"],
    "淘宝": ["淘宝", "天猫", "tmall", "taobao", "淘金币"],
    "拼多多": ["拼多多", "pdd", "拼多"],
    "外卖": ["外卖", "美团", "饿了么", "美团外卖"],
    "红包": ["红包", "虹包", "鸿包", "必中红包"],
    "优惠券": ["优惠券", "券", "满减", "消费券", "领券"],
}

def auto_categorize(text):
    """根据关键词自动分类"""
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return None

def load_items_db():
    """加载全量线报数据库"""
    if os.path.exists(ITEMS_DB_FILE):
        try:
            with open(ITEMS_DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'items' in data:
                return data
        except Exception as e:
            print(f"[警告] 读取线报数据库失败: {e}")
    return {'items': [], 'updated_at': ''}

def save_items_db(db):
    """保存全量线报数据库"""
    try:
        with open(ITEMS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, separators=(',', ':'))
        print(f"[信息] 线报数据库已更新: {ITEMS_DB_FILE} ({len(db['items'])} 条)")
        return True
    except Exception as e:
        print(f"[错误] 保存线报数据库失败: {e}")
        return False

def merge_items_into_db(new_item_list, check_time):
    """
    将本轮新抓取的线报合并到全量数据库中（按 URL 去重）
    新条目插入到列表头部（最新的在前面）
    """
    db = load_items_db()
    existing_urls = set(item['url'] for item in db['items'])

    # 过滤出真正的新条目，并添加自动分类
    added = 0
    fresh_items = []
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

    # 超出上限时裁剪（保留最新条目）
    if len(db['items']) > MAX_ITEMS_DB:
        removed = len(db['items']) - MAX_ITEMS_DB
        db['items'] = db['items'][:MAX_ITEMS_DB]
        print(f"[数据库] 裁剪旧条目: 移除 {removed} 条，保留最新 {MAX_ITEMS_DB} 条")

    db['updated_at'] = check_time
    save_items_db(db)
    print(f"[数据库] 新增 {added} 条，总计 {len(db['items'])} 条")
    return added


# ============================================================
# 爬虫核心逻辑
# ============================================================

# ============================================================
# 站点专用解析器
# ============================================================

def parse_ypojie(soup):
    """易破解 (WordPress DUX主题) - 精准提取最新文章标题和链接"""
    items = []
    for h2 in soup.select('#content h2 a, #content .entry-title a, #main-content h2 a'):
        text = h2.get_text(strip=True)
        href = h2.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")
    if not items:
        for a in soup.select('.widget_recent a, .widgets-list a, .recent-posts a'):
            text = a.get_text(strip=True)
            href = a.get('href', '')
            if text and len(text) > 5 and not any(x in href for x in ['/page/', '/archives']):
                items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_discuz_threadlist(soup):
    """Discuz论坛通用解析器 - 精准提取帖子列表"""
    items = []
    for a in soup.select('.threadlist .t a, .tl .t a, #threadlist .t a, .threadlist tr td a.xst, .threadlist tr td a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 3 and '/thread-' in href:
            items.append(f"{text} ({href})")
    if not items:
        for tr in soup.select('.forum tbody tr, table tbody tr'):
            for a in tr.select('a'):
                text = a.get_text(strip=True)
                href = a.get('href', '')
                if text and len(text) > 3 and '/thread-' in href:
                    items.append(f"{text} ({href})")
                    break
    return '\n'.join(items[:30])


def parse_discuz_items(soup, base_url):
    """Discuz论坛 - 结构化条目提取"""
    from urllib.parse import urljoin
    items = []
    seen = set()
    for a in soup.select('.threadlist .t a, .tl .t a, #threadlist .t a, .threadlist tr td a.xst, .threadlist tr td a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or text in seen or '/thread-' not in href:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            items.append({'text': text, 'url': href})
    if not items:
        for tr in soup.select('.forum tbody tr, table tbody tr'):
            for a in tr.select('a'):
                text = a.get_text(strip=True)
                href = a.get('href', '').strip()
                if text and len(text) > 3 and '/thread-' in href and text not in seen:
                    seen.add(text)
                    if href.startswith('/'):
                        href = urljoin(base_url, href)
                    if href.startswith('http'):
                        items.append({'text': text, 'url': href})
                    break
    return items[:30]


def parse_yxssp(soup):
    """异星软件空间 (WordPress) - 精准提取文章列表，排除分类导航"""
    items = []
    for a in soup.select('.post-item h2 a, .entry-title a, .post-title a, article h2 a, article h3 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")


def parse_yxssp_items(soup, base_url):
    """异星软件空间 - 结构化条目提取"""
    from urllib.parse import urljoin
    items = []
    seen = set()
    for a in soup.select('.post-item h2 a, .entry-title a, .post-title a, article h2 a, article h3 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 5 or text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        if href.startswith('http'):
            items.append({'text': text, 'url': href})
    return items[:30]
    if not items:
        for article in soup.select('article, .post'):
            h = article.select_one('h2 a, h3 a, h4 a')
            if h:
                text = h.get_text(strip=True)
                href = h.get('href', '')
                if text and len(text) > 5:
                    items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_423down(soup):
    """423Down - 精准提取软件文章，排除分类导航和侧边栏"""
    items = []
    seen = set()
    # 主内容区的文章标题链接（格式：/数字.html 才是文章页）
    for a in soup.select('.post-list a, .content-list a, article h2 a, .entry-title a, #main a, .list-item a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if not text or len(text) < 5:
            continue
        # 只要文章页（/数字.html 格式）
        import re
        if not re.search(r'/\d+\.html', href):
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append(f"{text} ({href})")
    # 如果上面没找到，用更宽松的方式：取包含日期关键词的链接
    if not items:
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 80:
                continue
            import re
            if not re.search(r'/\d+\.html', href):
                continue
            # 排除纯英文短标题（通常是导航）
            chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
            if chinese_count < 2 and len(text) < 15:
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_423down_items(soup, base_url):
    """423Down - 提取文章条目，返回 [{'text':..., 'url':...}] 格式"""
    import re
    items = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5 or len(text) > 80:
            continue
        if not re.search(r'/\d+\.html', href):
            continue
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2 and len(text) < 15:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:30]


def parse_ziyuanting_items(soup, base_url):
    """晓晓资源网 - 只提取公告/文章，不提取网站目录导航"""
    import re
    items = []
    seen = set()
    # 只取 /bulletin/ 路径下的公告文章
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if '/bulletin/' not in href and '/article/' not in href and '/post/' not in href:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_ziyuanting(soup):
    """晓晓资源网 - 只提取公告文本用于MD5比对"""
    items = []
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        if '/bulletin/' in href or '/article/' in href or '/post/' in href:
            if text and len(text) > 5:
                items.append(f"{text} ({href})")
    return '\n'.join(items[:20])


def parse_wycad_items(soup, base_url):
    """无忧软件网 - 提取真正的软件文章，排除标签/分类链接"""
    import re
    items = []
    seen = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 4:
            continue
        # 排除 /tag/ /soft/ /category/ 等分类链接
        if any(x in href for x in ['/tag/', '/soft/', '/category/', '/page/']):
            continue
        # 只取文章页：域名下的带数字slug或有汉字的路径
        if not re.search(r'wycad\.com/\w', href):
            continue
        # 排除纯英文/数字短文本（通常是导航）
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
        if chinese_count < 2 and len(text) < 10:
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append({'text': text, 'url': href})
    return items[:20]



def parse_h6room_items(soup, base_url):
    """好料空间 - 提取最新发布的软件/资源文章"""
    items = []
    seen = set()
    # 主内容区文章标题
    for a in soup.select('.post-title a, .item-title a, h2 a, h3 a, .content a[href*="/"]'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 4 or len(text) > 80:
            continue
        if text in seen:
            continue
        # 过滤导航词
        skip = ['首页', '导航', '站点地图', '关于', '联系', '最新软件', '热门资源']
        if text in skip:
            continue
        seen.add(text)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    # 如果主选择器失败，尝试通用文章链接
    if not items:
        import re
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 80:
                continue
            if not re.search(r'h6room\.com/\w+/\w+', href):
                continue
            import re as _re
            if len(_re.findall(r'[\u4e00-\u9fff]', text)) < 1:
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})
    return items[:20]


def parse_xzba_items(soup, base_url):
    """游戏下载吧 - 提取最新游戏条目"""
    items = []
    seen = set()
    for a in soup.select('.post-title a, .item-title a, h2 a, h3 a, .game-title a, .list-item a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 2 or len(text) > 60:
            continue
        if text in seen:
            continue
        skip = ['首页', '最新发布', '角色扮演', '动作', '模拟', '休闲', '独立', '冒险']
        if text in skip:
            continue
        seen.add(text)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    # 通用备选
    if len(items) < 3:
        import re
        for a in soup.find_all('a', href=True):
            href = a.get('href', '').strip()
            text = a.get_text(strip=True)
            if not text or len(text) < 2 or len(text) > 60:
                continue
            if not re.search(r'xzba\.cc/\w+/\d+', href):
                continue
            if text in seen:
                continue
            seen.add(text)
            items.append({'text': text, 'url': href})
    return items[:20]


def parse_apprcn_items(soup, base_url):
    """反斗限免 - 提取限免软件列表"""
    items = []
    seen = set()
    for a in soup.select('article a, .post a, h2 a, h3 a, .entry-title a'):
        text = a.get_text(strip=True)
        href = a.get('href', '').strip()
        if not text or len(text) < 3 or len(text) > 80:
            continue
        skip = ['阅读全文', '赞', '评论', '去评论', '下一页', '上一页', '返回顶部']
        if text in skip:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_daydayzhuan_items(soup, base_url):
    """天天线报网 - 提取文章列表"""
    from urllib.parse import urljoin
    items = []
    seen = set()
    # 匹配 /article/{id} 模式
    for a in soup.select('a[href*="/article/"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if text in seen:
            continue
        # 过滤导航
        skip = ['首页', '实时线报', '项目首码', '手机赚钱', '爆款秒杀', '随笔', '去下载']
        if text in skip:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_007ymd_items(soup, base_url):
    """007线报网 - 提取文章列表"""
    items = []
    seen = set()
    # 匹配 ?id={数字} 模式
    import re
    for a in soup.select('a[href*="?id="]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if text in seen:
            continue
        # 过滤导航
        if text in ['首页', '关于我们', '长期羊毛', '有奖活动', '撸实物', '音影会员', '话费流量活动', '[查看详情]']:
            continue
        seen.add(text)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_baicaio_items_v2(soup, base_url):
    """白菜哦 v2 - 提取文章列表"""
    from urllib.parse import urljoin
    items = []
    seen = set()
    # 匹配 /article/ 和 /item/ 模式
    for a in soup.select('a[href*="/article/"], a[href*="/item/"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if text in seen:
            continue
        seen.add(text)
        if href.startswith('/'):
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:20]


def parse_manmanbuy_items(soup, base_url):
    """慢慢买 - 提取搜索结果"""
    items = []
    seen = set()
    # 搜索结果链接
    for a in soup.select('a[href*="s.manmanbuy.com"], a[href*="pc/search"]'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 2:
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append({'text': text, 'url': href})
    return items[:20]




def parse_axutongxue_items(soup, base_url):
    """阿虚同学的储物间 - 提取资源导航链接"""
    from urllib.parse import urljoin
    items = []
    seen = set()
    # 提取所有外部链接
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not href.startswith('http'):
            continue
        if not text or len(text) < 3:
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
        seen.add(text)
        items.append({'text': text, 'url': href})
    return items[:30]


def parse_rss_feed(content_bytes, base_url):
    """RSS/Atom Feed 解析器 - 直接从XML提取文章条目"""
    from xml.etree import ElementTree as ET
    items = []
    seen = set()
    try:
        root = ET.fromstring(content_bytes)
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
        pass
    return items[:30]


def parse_ghxi(soup):
    """果核剥壳 (新版结构 .item-content h2 a) - 精准提取文章"""
    items = []
    for a in soup.select('.item-content h2 a, .item-content h3 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")
    # 兼容旧版结构
    if not items:
        for a in soup.select('.post-item .entry-title a, .post-item h2 a'):
            text = a.get_text(strip=True)
            href = a.get('href', '')
            if text and len(text) > 5:
                items.append(f"{text} ({href})")
    return '\n'.join(items[:30])


def parse_ghxi_items(soup, base_url):
    """果核剥壳 - 通过 WordPress REST API 获取文章（站点为 Vue SPA，HTML 无法直接解析）"""
    import html as html_mod
    api_url = "https://www.ghxi.com/wp-json/wp/v2/posts?per_page=30"
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'application/json, */*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    items = []
    try:
        resp = requests.get(api_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            posts = resp.json()
            for post in posts:
                title = html_mod.unescape(post.get('title', {}).get('rendered', ''))
                link = post.get('link', '')
                if title and len(title) > 3 and link:
                    items.append({'text': title, 'url': link})
            print(f"[果核剥壳] WP API 获取到 {len(items)} 篇文章")
        else:
            print(f"[果核剥壳] WP API 返回 HTTP {resp.status_code}")
    except Exception as e:
        print(f"[果核剥壳] WP API 请求失败: {e}")
    return items


def extract_article_items(soup, base_url=''):
    """
    从页面中提取独立文章条目列表（含链接）
    返回：[{'text': '标题', 'url': '链接'}, ...] 最多50条
    """
    import re
    # 移除干扰元素
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
        tag.decompose()

    body = soup.find('body')
    if not body:
        return []

    items = []
    seen = set()

    # 策略1: 提取 <a> 标签的文本 + href
    for a_tag in body.find_all('a', href=True):
        text = a_tag.get_text(strip=True)
        if not text or len(text) < 4 or len(text) > 120:
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
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        elif not href.startswith('http'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        seen.add(text)
        items.append({'text': text, 'url': href})

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


def fetch_page_content(url):
    """
    爬取页面完整正文
    返回：(成功标志, 内容/错误信息)
    内容包含：(text, title, summary, response_time)
    """
    def make_request(ua, fingerprint=None, accept_lang=None):
        headers = {
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': accept_lang or 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
        }
        # 添加浏览器指纹头部（Chrome/Edge 特有）
        if fingerprint:
            headers.update(fingerprint)
        return requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    try:
        print(f"[爬取] {url}")
        ua = get_random_ua()
        fingerprint = get_random_fingerprint()
        accept_lang = get_random_accept_language()

        start_time = time.time()
        response = make_request(ua, fingerprint, accept_lang)
        elapsed = time.time() - start_time

        # 403 时换 UA+指纹 重试一次
        if response.status_code == 403:
            new_ua = get_random_ua()
            while new_ua == ua:
                new_ua = get_random_ua()
            new_fp = get_random_fingerprint()
            new_lang = get_random_accept_language()
            print(f"[重试] 403 → 切换 UA+指纹 重试")
            time.sleep(random.uniform(1, 3))
            start_time = time.time()
            response = make_request(new_ua, new_fp, new_lang)
            elapsed = time.time() - start_time

        # 检查HTTP状态码
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        
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

        # 站点专用解析器（精准提取正文+条目，避免抓到导航/侧边栏/旧目录）
        if 'feed.iplaysoft.com' in url or url.endswith('.xml'):
            # RSS/Atom Feed：直接解析XML
            article_items = parse_rss_feed(response.content, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif '423down.com' in url:
            article_items = parse_423down_items(soup, url)
            text = parse_423down(soup)
        elif 'ziyuanting.com' in url:
            article_items = parse_ziyuanting_items(soup, url)
            text = parse_ziyuanting(soup)
        elif 'wycad.com' in url:
            article_items = parse_wycad_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'baicaio.com' in url:
            article_items = parse_baicaio_items_v2(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'h6room.com' in url:
            article_items = parse_h6room_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'xzba.cc' in url:
            article_items = parse_xzba_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'free.apprcn.com' in url:
            article_items = parse_apprcn_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'kxdao.net' in url:
            article_items = parse_discuz_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'yxssp.com' in url:
            article_items = parse_yxssp_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'ghxi.com' in url:
            article_items = parse_ghxi_items(soup, url)
            if article_items:
                text = '\n'.join(item['text'] for item in article_items)
            else:
                # API 失败时回退到通用解析（SPA 可能拿不到内容）
                article_items = extract_article_items(soup, url)
                body = soup.find('body')
                text = body.get_text(separator=' ', strip=True) if body else ''
                text = ' '.join(text.split())
        elif 'daydayzhuan.com' in url:
            article_items = parse_daydayzhuan_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif '007ymd.com' in url:
            article_items = parse_007ymd_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'axutongxue.net' in url:
            article_items = parse_axutongxue_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'manmanbuy.com' in url:
            article_items = parse_manmanbuy_items(soup, url)
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

        # 返回包含标题、摘要和文章条目的字典
        return True, {
            'text': text,
            'title': title,
            'summary': summary,
            'items': article_items,
            'response_time': round(elapsed, 3)
        }
        
    except requests.Timeout:
        return False, "请求超时"
    except requests.ConnectionError:
        return False, "连接失败"
    except requests.RequestException as e:
        return False, f"请求异常: {str(e)[:50]}"
    except Exception as e:
        return False, f"未知错误: {str(e)[:50]}"


def calculate_md5(text):
    """计算文本的MD5哈希值"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def check_site_update(url, old_records):
    """
    检查单个站点是否有更新
    返回：(是否更新, 新哈希值, 错误信息, 页面信息)
    """
    success, result = fetch_page_content(url)
    
    if not success:
        return None, None, result, None  # 爬取失败
    
    # result现在是一个字典
    text = result['text']
    page_info = {
        'url': url,
        'title': result['title'],
        'summary': result['summary'],
        'items': result['items']
    }
    
    new_hash = calculate_md5(text)
    old_hash = old_records.get(url)
    
    if old_hash is None:
        # 首次监控，记录哈希但不视为更新
        return False, new_hash, "首次监控", page_info
    elif old_hash != new_hash:
        # 检测到更新
        return True, new_hash, "内容已更新", page_info
    else:
        # 无更新
        return False, new_hash, "无更新", page_info


def git_commit_if_changed():
    """
    检查是否有变更，仅在有变更时执行commit & push
    变更条件：哈希文件修改
    
    注意：此函数在GitHub Actions环境中会跳过git操作，
    """
    # 检查是否在GitHub Actions环境中
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print("[Git] 在GitHub Actions环境中，跳过脚本内git操作")
        print("[Git] 变更将由workflow的提交步骤处理")
        return False
    
    try:
        # 检查工作区状态
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        changes = result.stdout.strip()
        if not changes:
            print("[Git] 无变更，跳过提交")
            return False
        
        # 有变更，执行提交
        now = get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')
        commit_msg = f"站点更新检测 - {now}"
        
        # Git add所有变更
        subprocess.run(['git', 'add', '-A'], check=True, timeout=30)
        
        # Git commit
        subprocess.run(['git', 'commit', '-m', commit_msg], check=True, timeout=30)
        
        # Git pull --rebase 再 push（避免远程有更新的推送冲突）
        try:
            subprocess.run(['git', 'pull', '--rebase'], check=True, timeout=60)
        except subprocess.CalledProcessError:
            print("[Git] pull --rebase 失败，尝试直接 push")
        subprocess.run(['git', 'push'], check=True, timeout=60)

        print(f"[Git] 提交成功: {commit_msg}")
        return True

    except subprocess.CalledProcessError as e:
        print(f"[Git错误] 提交失败: {e}")
        return False
    except Exception as e:
        print(f"[Git异常] {e}")
        return False


# ============================================================
# 运行日志管理
# ============================================================

def load_run_log():
    """加载历史运行日志"""
    log = []
    if os.path.exists(RUN_LOG_FILE):
        try:
            with open(RUN_LOG_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        log.append(json.loads(line))
        except Exception:
            pass
    return log


def append_run_log(entry):
    """追加一条运行日志"""
    try:
        with open(RUN_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception as e:
        print(f"[警告] 运行日志写入失败: {e}")


def analyze_and_fix(run_result):
    """
    运行后自分析 + 自动修复
    run_result: {'success': N, 'error': N, 'updated': N, 'total': N, 'errors': [...], 'updated_sites': [...]}
    """
    print("\n" + "=" * 60)
    print("[自检] 运行后分析")
    print("-" * 60)

    issues_found = []

    # 1. 检查失败站点
    if run_result['errors']:
        for err in run_result['errors']:
            url = err['url']
            msg = err['message']

            # 403 封锁 → 建议增加延迟
            if '403' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': 'HTTP 403 被封锁',
                    'action': '已记录，建议该站点增加请求延迟或更换 User-Agent'
                })

            # 404 页面不存在 → 建议移除
            elif '404' in msg:
                issues_found.append({
                    'level': 'error',
                    'site': url,
                    'issue': 'HTTP 404 页面不存在',
                    'action': '建议从 MONITOR_SITES 中移除该站点'
                })

            # 页面正文为空 → JS 渲染问题
            elif '页面正文为空' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '页面正文为空（可能是JS渲染）',
                    'action': '该站点可能需要 Playwright 才能抓取，暂时保留观察'
                })

            # 超时
            elif '超时' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '请求超时',
                    'action': '网络问题，下轮重试'
                })

            # 连接失败
            elif '连接失败' in msg:
                issues_found.append({
                    'level': 'warn',
                    'site': url,
                    'issue': '连接失败',
                    'action': '站点可能已下线，连续3轮失败后将建议移除'
                })

    # 2. 检查失败率
    error_rate = run_result['error'] / run_result['total'] if run_result['total'] > 0 else 0
    if error_rate > 0.1:
        issues_found.append({
            'level': 'error',
            'site': '全局',
            'issue': f'失败率 {error_rate:.0%} 超过 10%',
            'action': '检查网络环境或 GitHub Actions 运行时'
        })

    # 3. 更新率异常检测（>80% 站点更新可能是哈希过于敏感）
    if run_result['total'] > 0:
        update_rate = run_result['updated'] / run_result['total']
        if update_rate > 0.8:
            issues_found.append({
                'level': 'info',
                'site': '全局',
                'issue': f'更新率 {update_rate:.0%} 异常高，可能是首次运行或哈希过于敏感',
                'action': '观察下轮结果，如持续高更新率可考虑过滤动态内容'
            })

    # 4. 检查历史趋势：连续失败的站点
    run_log = load_run_log()
    if len(run_log) >= 3:
        recent_errors = set()
        for log_entry in run_log[-3:]:
            for err in log_entry.get('errors', []):
                recent_errors.add(err['url'])

        for url in recent_errors:
            issues_found.append({
                'level': 'error',
                'site': url,
                'issue': '连续3轮巡检均失败',
                'action': '建议从 MONITOR_SITES 中移除'
            })

    # 打印分析报告
    if issues_found:
        print(f"\n[自检] 发现 {len(issues_found)} 个问题：\n")
        for i, issue in enumerate(issues_found, 1):
            level_icon = {'error': '❌', 'warn': '⚠️', 'info': '💡'}.get(issue['level'], '📋')
            print(f"  {i}. {level_icon} [{issue['site']}]\n     问题: {issue['issue']}\n     建议: {issue['action']}\n")
    else:
        print("\n  ✅ 本轮运行健康，无异常问题\n")

    return issues_found


# ============================================================
# 暂停站点管理（自动移除/恢复连续失败站点）
# ============================================================

def load_paused_sites():
    """加载被暂停的站点 {url: {'paused_at': '...', 'reason': '...', 'fail_count': N}}"""
    if os.path.exists(PAUSED_SITES_FILE):
        try:
            with open(PAUSED_SITES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_paused_sites(paused):
    """保存暂停站点"""
    try:
        with open(PAUSED_SITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(paused, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[警告] 暂停站点保存失败: {e}")


def generate聚合_page(item_list):
    """
    生成好看的聚合线报页面 index.html
    """
    from datetime import datetime
    from urllib.parse import urlparse
    from collections import Counter

    # 只保留有正文的条目，过滤垃圾
    clean_items = []
    junk_patterns = ["安卓软件", "办公软件", "安全软件", "查看详情", "直达链接", "阅读全文",
                     "继续阅读", "更多", "首页", "登录", "注册", "搜索", "javascript:"]
    for item in item_list:
        text = item.get('text', '')
        if len(text) < 5:
            continue
        if text.isdigit():
            continue
        skip = False
        for jp in junk_patterns:
            if text == jp or text.replace(" ", "") == jp:
                skip = True
                break
        if skip:
            continue
        clean_items.append(item)

    # 按时间倒序
    clean_items.sort(key=lambda x: x.get('time', ''), reverse=True)
    total = len(clean_items)

    # 按来源分组统计
    source_counts = Counter(item.get('source', '未知') for item in clean_items)
    top_sources = source_counts.most_common(5)

    # 生成每条线报的 HTML 卡片
    cards_html = ""
    for item in clean_items[:200]:
        text = item.get('text', '')
        url = item.get('url', '#')
        source = item.get('source', '未知')
        t = item.get('time', '')[:16]
        try:
            domain = urlparse(url).netloc.replace('www.', '')
        except:
            domain = source[:20]
        cards_html += f'''
        <a href="{url}" target="_blank" class="card" rel="noopener">
            <div class="card-title">{text}</div>
            <div class="card-meta">
                <span class="card-source">{domain}</span>
                <span class="card-time">{t}</span>
            </div>
        </a>
'''

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>线报聚合 - 实时更新的羊毛线报合集</title>
<meta name="description" content="聚合全网优质羊毛线报，实时更新，告别垃圾站">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  :root {{
    --bg: #f5f0eb;
    --surface: #ffffff;
    --primary: #e85d04;
    --primary-light: #fff0e6;
    --text: #1a1a1a;
    --text-muted: #888;
    --border: #e8e0d8;
    --shadow: 0 2px 8px rgba(0,0,0,0.06);
    --radius: 14px;
  }}
  body {{
    font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    line-height: 1.6;
  }}
  header {{
    background: var(--primary);
    color: #fff;
    padding: 0 24px;
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
    box-shadow: 0 2px 12px rgba(232,93,4,0.3);
  }}
  header .logo {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.5px;
  }}
  header .logo span {{
    background: rgba(255,255,255,0.2);
    padding: 2px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    margin-left: 8px;
  }}
  header .stats {{
    font-size: 13px;
    opacity: 0.9;
  }}
  .container {{
    max-width: 900px;
    margin: 0 auto;
    padding: 24px 16px 60px;
  }}
  .hero {{
    background: var(--primary);
    color: #fff;
    border-radius: var(--radius);
    padding: 28px 32px;
    margin-bottom: 20px;
    box-shadow: var(--shadow);
    display: flex;
    align-items: center;
    gap: 20px;
  }}
  .hero .fire {{ font-size: 48px; }}
  .hero h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
  .hero p {{ font-size: 14px; opacity: 0.85; }}
  .sources {{
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 24px;
  }}
  .source-tag {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 12px;
    color: var(--text-muted);
  }}
  .cards {{
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
    text-decoration: none;
    color: var(--text);
    display: flex;
    align-items: flex-start;
    gap: 12px;
    transition: all 0.15s ease;
    box-shadow: var(--shadow);
  }}
  .card:hover {{
    border-color: var(--primary);
    background: var(--primary-light);
    transform: translateX(3px);
    box-shadow: 0 4px 16px rgba(232,93,4,0.15);
  }}
  .card::before {{
    content: '◆';
    color: var(--primary);
    font-size: 8px;
    margin-top: 7px;
    flex-shrink: 0;
  }}
  .card-title {{
    flex: 1;
    font-size: 15px;
    font-weight: 500;
    line-height: 1.5;
    color: var(--text);
  }}
  .card-meta {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    flex-shrink: 0;
  }}
  .card-source {{
    font-size: 11px;
    color: var(--primary);
    font-weight: 600;
    background: var(--primary-light);
    padding: 2px 8px;
    border-radius: 10px;
    white-space: nowrap;
  }}
  .card-time {{
    font-size: 11px;
    color: var(--text-muted);
    white-space: nowrap;
  }}
  footer {{
    text-align: center;
    padding: 24px 16px;
    color: var(--text-muted);
    font-size: 12px;
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }}
  footer a {{ color: var(--primary); text-decoration: none; }}
  @media (max-width: 600px) {{
    .hero {{ padding: 20px; flex-direction: column; text-align: center; }}
    .card {{ padding: 14px 16px; }}
    .card-meta {{ display: none; }}
    header .stats {{ display: none; }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">🔥 线报聚合 <span>自动更新</span></div>
  <div class="stats">{total} 条羊毛线报</div>
</header>
<div class="container">
  <div class="hero">
    <div class="fire">🔥</div>
    <div>
      <h1>全网羊毛线报实时聚合</h1>
      <p>自动抓取多个线报站点，去重过滤，只保留真实有价值的线报信息</p>
      <p style="margin-top:6px;font-size:12px;opacity:0.7;">最后更新：{now_str} · 共收录 {total} 条</p>
    </div>
  </div>
  <div class="sources">
    <span style="font-size:12px;color:var(--text-muted);margin-right:4px;">来源：</span>
    <span class="source-tag">{top_sources[0][0]} ({top_sources[0][1]})</span>
    <span class="source-tag">{top_sources[1][0]} ({top_sources[1][1]})</span>
    <span class="source-tag">{top_sources[2][0]} ({top_sources[2][1]})</span>
  </div>
  <div class="cards">
{cards_html}
  </div>
</div>
<footer>
  <p>自动更新 · 永不停止 · <a href="https://github.com/gitfox-enter/site-update-monitor">GitHub</a></p>
</footer>
</body>
</html>'''

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"[页面] 聚合页面已生成: index.html ({total} 条)")

    """
    生成好看的聚合线报页面 index.html
    """
    from datetime import datetime
    from urllib.parse import urlparse
    from collections import Counter

    # 只保留有正文的条目，过滤垃圾
    clean_items = []
    junk_patterns = ["安卓软件", "办公软件", "安全软件", "查看详情", "直达链接", "阅读全文",
                     "继续阅读", "更多", "首页", "登录", "注册", "搜索", "javascript:"]
    for item in item_list:
        text = item.get('text', '')
        if len(text) < 5:
            continue
        if text.isdigit():
            continue
        skip = False
        for jp in junk_patterns:
            if text == jp or text.replace(" ", "") == jp:
                skip = True
                break
        if skip:
            continue
        clean_items.append(item)

    # 按时间倒序
    clean_items.sort(key=lambda x: x.get('time', ''), reverse=True)
    total = len(clean_items)

    # 按来源分组统计
    source_counts = Counter(item.get('source', '未知') for item in clean_items)
    top_sources = source_counts.most_common(5)

    # 生成每条线报的 HTML 卡片
    cards_html = ""
    for item in clean_items[:200]:
        text = item.get('text', '')
        url = item.get('url', '#')
        source = item.get('source', '未知')
        t = item.get('time', '')[:16]
        try:
            domain = urlparse(url).netloc.replace('www.', '')
        except:
            domain = source[:20]
        cards_html += (
            '\n        <a href="' + url + '" target="_blank" class="card" rel="noopener">\n'
            '            <div class="card-title">' + text + '</div>\n'
            '            <div class="card-meta">\n'
            '                <span class="card-source">' + domain + '</span>\n'
            '                <span class="card-time">' + t + '</span>\n'
            '            </div>\n'
            '        </a>\n'
        )

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = (
        '<!DOCTYPE html>\n'
        '<html lang="zh-CN">\n'
        '<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        '<title>线报聚合 - 实时更新的羊毛线报合集</title>\n'
        '<meta name="description" content="聚合全网优质羊毛线报，实时更新，告别垃圾站">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">\n'
        '<style>\n'
        '  * { margin: 0; padding: 0; box-sizing: border-box; }\n'
        '  :root {\n'
        '    --bg: #f5f0eb;\n'
        '    --surface: #ffffff;\n'
        '    --primary: #e85d04;\n'
        '    --primary-light: #fff0e6;\n'
        '    --text: #1a1a1a;\n'
        '    --text-muted: #888;\n'
        '    --border: #e8e0d8;\n'
        '    --shadow: 0 2px 8px rgba(0,0,0,0.06);\n'
        '    --radius: 14px;\n'
        '  }\n'
        '  body {\n'
        '    font-family: "Noto Sans SC", -apple-system, BlinkMacSystemFont, sans-serif;\n'
        '    background: var(--bg);\n'
        '    color: var(--text);\n'
        '    min-height: 100vh;\n'
        '    line-height: 1.6;\n'
        '  }\n'
        '  header {\n'
        '    background: var(--primary);\n'
        '    color: #fff;\n'
        '    padding: 0 24px;\n'
        '    height: 60px;\n'
        '    display: flex;\n'
        '    align-items: center;\n'
        '    justify-content: space-between;\n'
        '    position: sticky;\n'
        '    top: 0;\n'
        '    z-index: 100;\n'
        '    box-shadow: 0 2px 12px rgba(232,93,4,0.3);\n'
        '  }\n'
        '  header .logo {\n'
        '    font-size: 20px;\n'
        '    font-weight: 700;\n'
        '    letter-spacing: -0.5px;\n'
        '  }\n'
        '  header .logo span {\n'
        '    background: rgba(255,255,255,0.2);\n'
        '    padding: 2px 10px;\n'
        '    border-radius: 6px;\n'
        '    font-size: 12px;\n'
        '    font-weight: 600;\n'
        '    margin-left: 8px;\n'
        '  }\n'
        '  header .stats {\n'
        '    font-size: 13px;\n'
        '    opacity: 0.9;\n'
        '  }\n'
        '  .container {\n'
        '    max-width: 900px;\n'
        '    margin: 0 auto;\n'
        '    padding: 24px 16px 60px;\n'
        '  }\n'
        '  .hero {\n'
        '    background: var(--primary);\n'
        '    color: #fff;\n'
        '    border-radius: var(--radius);\n'
        '    padding: 28px 32px;\n'
        '    margin-bottom: 20px;\n'
        '    box-shadow: var(--shadow);\n'
        '    display: flex;\n'
        '    align-items: center;\n'
        '    gap: 20px;\n'
        '  }\n'
        '  .hero .fire { font-size: 48px; }\n'
        '  .hero h1 { font-size: 22px; font-weight: 700; margin-bottom: 6px; }\n'
        '  .hero p { font-size: 14px; opacity: 0.85; }\n'
        '  .sources {\n'
        '    display: flex;\n'
        '    gap: 8px;\n'
        '    flex-wrap: wrap;\n'
        '    margin-bottom: 24px;\n'
        '  }\n'
        '  .source-tag {\n'
        '    background: var(--surface);\n'
        '    border: 1px solid var(--border);\n'
        '    border-radius: 20px;\n'
        '    padding: 4px 12px;\n'
        '    font-size: 12px;\n'
        '    color: var(--text-muted);\n'
        '  }\n'
        '  .cards {\n'
        '    display: flex;\n'
        '    flex-direction: column;\n'
        '    gap: 8px;\n'
        '  }\n'
        '  .card {\n'
        '    background: var(--surface);\n'
        '    border: 1px solid var(--border);\n'
        '    border-radius: 12px;\n'
        '    padding: 16px 20px;\n'
        '    text-decoration: none;\n'
        '    color: var(--text);\n'
        '    display: flex;\n'
        '    align-items: flex-start;\n'
        '    gap: 12px;\n'
        '    transition: all 0.15s ease;\n'
        '    box-shadow: var(--shadow);\n'
        '  }\n'
        '  .card:hover {\n'
        '    border-color: var(--primary);\n'
        '    background: var(--primary-light);\n'
        '    transform: translateX(3px);\n'
        '    box-shadow: 0 4px 16px rgba(232,93,4,0.15);\n'
        '  }\n'
        '  .card::before {\n'
        '    content: "◆";\n'
        '    color: var(--primary);\n'
        '    font-size: 8px;\n'
        '    margin-top: 7px;\n'
        '    flex-shrink: 0;\n'
        '  }\n'
        '  .card-title {\n'
        '    flex: 1;\n'
        '    font-size: 15px;\n'
        '    font-weight: 500;\n'
        '    line-height: 1.5;\n'
        '    color: var(--text);\n'
        '  }\n'
        '  .card-meta {\n'
        '    display: flex;\n'
        '    flex-direction: column;\n'
        '    align-items: flex-end;\n'
        '    gap: 4px;\n'
        '    flex-shrink: 0;\n'
        '  }\n'
        '  .card-source {\n'
        '    font-size: 11px;\n'
        '    color: var(--primary);\n'
        '    font-weight: 600;\n'
        '    background: var(--primary-light);\n'
        '    padding: 2px 8px;\n'
        '    border-radius: 10px;\n'
        '    white-space: nowrap;\n'
        '  }\n'
        '  .card-time {\n'
        '    font-size: 11px;\n'
        '    color: var(--text-muted);\n'
        '    white-space: nowrap;\n'
        '  }\n'
        '  footer {\n'
        '    text-align: center;\n'
        '    padding: 24px 16px;\n'
        '    color: var(--text-muted);\n'
        '    font-size: 12px;\n'
        '    border-top: 1px solid var(--border);\n'
        '    margin-top: 40px;\n'
        '  }\n'
        '  footer a { color: var(--primary); text-decoration: none; }\n'
        '  @media (max-width: 600px) {\n'
        '    .hero { padding: 20px; flex-direction: column; text-align: center; }\n'
        '    .card { padding: 14px 16px; }\n'
        '    .card-meta { display: none; }\n'
        '    header .stats { display: none; }\n'
        '  }\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<header>\n'
        '  <div class="logo">🔥 线报聚合 <span>自动更新</span></div>\n'
        '  <div class="stats">' + str(total) + ' 条羊毛线报</div>\n'
        '</header>\n'
        '<div class="container">\n'
        '  <div class="hero">\n'
        '    <div class="fire">🔥</div>\n'
        '    <div>\n'
        '      <h1>全网羊毛线报实时聚合</h1>\n'
        '      <p>自动抓取多个线报站点，去重过滤，只保留真实有价值的线报信息</p>\n'
        '      <p style="margin-top:6px;font-size:12px;opacity:0.7;">最后更新：' + now_str + ' · 共收录 ' + str(total) + ' 条</p>\n'
        '    </div>\n'
        '  </div>\n'
        '  <div class="sources">\n'
        '    <span style="font-size:12px;color:var(--text-muted);margin-right:4px;">来源：</span>\n'
        '    <span class="source-tag">' + top_sources[0][0] + ' (' + str(top_sources[0][1]) + ')</span>\n'
        '    <span class="source-tag">' + top_sources[1][0] + ' (' + str(top_sources[1][1]) + ')</span>\n'
        '    <span class="source-tag">' + top_sources[2][0] + ' (' + str(top_sources[2][1]) + ')</span>\n'
        '  </div>\n'
        '  <div class="cards">\n'
        + cards_html +
        '  </div>\n'
        '</div>\n'
        '<footer>\n'
        '  <p>自动更新 · 永不停止 · <a href="https://github.com/gitfox-enter/site-update-monitor">GitHub</a></p>\n'
        '</footer>\n'
        '</body>\n'
        '</html>\n'
    )

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"[页面] 聚合页面已生成: index.html (" + str(total) + " 条)")

def main():
    """主监控流程"""
    print("=" * 60)
    print("GitHub Actions 多站点更新监控系统 v2.0")
    print("=" * 60)

    # 获取当前时间和轮次
    now = get_beijing_time()
    round_num = get_current_round()
    check_time = now.strftime('%Y-%m-%d %H:%M:%S')

    print(f"[启动] 北京时间: {check_time}")
    print(f"[启动] 当日第 {round_num} 轮巡检")

    # 加载黑名单（用户讨厌/付费墙/反爬/无法访问/纯工具页）
    blacklist_domains = []
    try:
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            blacklist_data = json.load(f)
        blacklist_domains = [entry['domain'].lower() for entry in blacklist_data.get('blacklist', [])]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        blacklist_domains = []

    def is_blacklisted(url):
        parsed = urlparse(url)
        host = parsed.hostname or parsed.netloc
        host = host.lower().lstrip('www.').lstrip('m.')
        for domain in blacklist_domains:
            domain_clean = domain.lower().lstrip('www.').lstrip('m.')
            if host == domain_clean or host.endswith('.' + domain_clean):
                return True
        return False

    # 过滤黑名单站点
    filtered_by_blacklist = [url for url in MONITOR_SITES if is_blacklisted(url)]
    monitor_sites = [url for url in MONITOR_SITES if not is_blacklisted(url)]
    if filtered_by_blacklist:
        print(f"[黑名单] 过滤 {len(filtered_by_blacklist)} 个站点: {', '.join(filtered_by_blacklist)}")

    # 加载暂停站点（连续失败被自动移除的）
    paused = load_paused_sites()
    paused_urls = set(paused.keys())

    # 实际监控列表 = 配置列表 - 黑名单 - 暂停站点
    active_sites = [url for url in monitor_sites if url not in paused_urls]
    print(f"[启动] 监控站点数: {len(active_sites)} (活跃) + {len(blacklist_domains)} (黑名单) + {len(paused_urls)} (暂停)")
    if paused_urls:
        print(f"[暂停站点] {', '.join(paused_urls)}")
    print("-" * 60)

    # 加载历史哈希记录
    old_records = load_hash_records()
    print(f"[信息] 已加载哈希记录: {len(old_records)} 条")

    # 加载已通知过的条目URL（去重用）
    notified = load_notified_items()
    print(f"[信息] 已加载历史条目: {len(notified.get('items', []))} 条")

    # 检查所有活跃站点更新
    all_site_results = []  # 存储所有站点状态（含标题、摘要）
    new_records = old_records.copy()
    success_count = 0
    error_count = 0
    updated_count = 0
    response_times = []

    # 并发抓取（max_workers=6，每个站点自带随机延迟防止封禁）
    results_lock = threading.Lock()

    def check_one(url, idx):
        is_updated, new_hash, message, page_info = check_site_update(url, old_records)
        time.sleep(get_random_delay())
        if is_updated is None:
            return {'url': url, 'title': url, 'summary': '', 'items': [], 'status': 'error', 'message': message, 'is_updated': None, 'new_hash': None, 'page_info': None}
        return {
            'url': url,
            'title': page_info.get('title', url) if page_info else url,
            'summary': page_info.get('summary', '') if page_info else '',
            'items': page_info.get('items', []) if page_info else [],
            'status': 'updated' if is_updated else ('first' if url not in old_records else 'no_update'),
            'message': message,
            'is_updated': is_updated,
            'new_hash': new_hash,
            'page_info': page_info
        }

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(check_one, url, idx): (idx, url) for idx, url in enumerate(active_sites, 1)}
        for future in as_completed(futures):
            result = future.result()
            with results_lock:
                idx, url = futures[future]
                if result['is_updated'] is None:
                    print(f"\n[{idx}/{len(active_sites)}] [失败] {result['message']}")
                    error_count += 1
                else:
                    if result['is_updated']:
                        print(f"\n[{idx}/{len(active_sites)}] [更新] ✅ {result['message']}")
                        updated_count += 1
                    else:
                        print(f"\n[{idx}/{len(active_sites)}] [正常] {result['message']}")
                    success_count += 1
                    new_records[result['url']] = result['new_hash']
                all_site_results.append({
                    'url': result['url'],
                    'title': result['title'],
                    'summary': result['summary'],
                    'items': result['items'],
                    'status': result['status'],
                    'message': result['message']
                })

    # 添加暂停站点到结果列表（标记为 paused）
    for url in paused_urls:
        all_site_results.append({
            'url': url,
            'title': url,
            'summary': '',
            'status': 'paused',
            'message': paused[url].get('reason', '已暂停')
        })

    total_count = len(all_site_results)

    print("\n" + "=" * 60)
    print(f"[统计] 成功: {success_count} | 失败: {error_count} | 暂停: {len(paused_urls)}")
    print(f"[统计] 更新站点: {updated_count} 个")

    print("\n" + "-" * 60)

    # 总是更新哈希文件
    save_hash_records(new_records)

    # 构建完整条目字典（URL + 正文 + 来源 + 时间）
    new_item_list = []
    for r in all_site_results:
        if r['status'] == 'updated':
            for item in r.get('items', []):
                item_url = item['url'] if isinstance(item, dict) else item
                item_text = item['text'] if isinstance(item, dict) else str(item)
                if item_url and not item_url.startswith('javascript:'):
                    # 优先使用统一映射的短名称，回退到页面标题
                    src_name = get_source_name(r.get('url', '')) or r.get('title', r['url'])
                    new_item_list.append({
                        'url': item_url,
                        'text': item_text,
                        'source': src_name,
                        'time': check_time
                    })
    save_notified_items({
        'items': new_item_list,
        'updated_at': check_time
    })

    # 将新线报合并到全量数据库（持久化，按URL去重）
    merge_items_into_db(new_item_list, check_time)

    # index.html 现在是静态 SPA，从 items.json 加载数据，无需每次重新生成

    # 计算本轮新增URL数
    existing_urls_set = set(item['url'] for item in (notified.get('items', []) if isinstance(notified, dict) else []))
    new_urls = set(item['url'] for item in new_item_list if item['url'] not in existing_urls_set)
    print(f"[信息] 本轮新通知条目: {len(new_urls)} 条")

    # Git提交
    git_commit_if_changed()

    # ===== 运行后自分析 =====
    errors_detail = [{'url': r['url'], 'message': r.get('message', '')} for r in all_site_results if r['status'] == 'error']
    updated_sites = [r['url'] for r in all_site_results if r['status'] == 'updated']

    # 记录本轮运行日志（含暂停/恢复信息）
    run_entry = {
        'round': round_num,
        'check_time': check_time,
        'total': total_count,
        'active': len(active_sites),
        'success': success_count,
        'error': error_count,
        'updated': updated_count,
        'paused': len(paused_urls),
        'new_items': len(new_urls),
        'errors': errors_detail,
        'updated_sites': updated_sites
    }
    append_run_log(run_entry)

    # 自分析 + 建议
    issues = analyze_and_fix({
        'success': success_count,
        'error': error_count,
        'updated': updated_count,
        'total': total_count,
        'errors': errors_detail,
        'updated_sites': updated_sites
    })

    print("\n" + "=" * 60)
    print("[完成] 本轮巡检结束")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[中断] 用户手动停止")
        sys.exit(0)
    except Exception as e:
        print(f"\n[致命错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
