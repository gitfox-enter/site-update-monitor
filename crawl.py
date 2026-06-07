#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 多站点更新监控系统
功能：爬取33个站点 → MD5比对检测更新 → 163邮箱SMTP推送 → 本地备份归档
时间：每4小时执行一次（00:00, 04:00, 08:00, 12:00, 16:00, 20:00）
时区：Asia/Shanghai（北京时间）
"""

import os
import sys
import time
import random
import hashlib
import requests
import smtplib
import warnings
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
    "https://axutongxue.net/","http://79tao.linejia.com/",
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
    "https://yangmao.wang/",]

# 文件存储配置
HASH_RECORD_FILE = "hash_record.txt"
NOTIFIED_ITEMS_FILE = "notified_items.json"  # 记录已通知过的条目URL，避免重复推送
RUN_LOG_FILE = "run_log.jsonl"  # 每轮运行日志（JSONL格式），用于追踪历史与自检
FAILED_SITES_FILE = "failed_sites.json"  # 连续失败站点记录，自动建议移除
PAUSED_SITES_FILE = "paused_sites.json"  # 因连续失败被暂停的站点
BLACKLIST_FILE = "blacklist.json"  # 网站黑名单（用户讨厌/付费墙/反爬/无法访问/纯工具页）

# 自动移除/恢复配置
MAX_CONSECUTIVE_FAILURES = 3  # 连续失败 N 轮后自动暂停
RECOVERY_CHECK_INTERVAL = 6  # 每 N 轮尝试恢复一次暂停站点

# 163邮箱SMTP配置
SMTP_SERVER = "smtp.163.com"
SMTP_PORT = 465
SMTP_USER = os.getenv("SMTP_USER", "")  # 邮箱地址
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # 授权码
EMAIL_TO = os.getenv("SMTP_USER", "")  # 发送到自己

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
    加载已通知过的条目URL集合
    返回格式：set of item URLs
    """
    notified = set()
    if os.path.exists(NOTIFIED_ITEMS_FILE):
        try:
            with open(NOTIFIED_ITEMS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                notified = set(data.get('items', []))
        except Exception as e:
            print(f"[警告] 读取已通知条目文件失败: {e}")
    return notified


def save_notified_items(notified):
    """保存已通知条目URL集合到文件"""
    try:
        with open(NOTIFIED_ITEMS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'items': sorted(notified), 'updated_at': get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')}, f, ensure_ascii=False, indent=2)
        print(f"[信息] 已通知条目记录已更新: {NOTIFIED_ITEMS_FILE} ({len(notified)} 条)")
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


def parse_yxssp(soup):
    """异星软件空间 (WordPress) - 精准提取文章列表，排除分类导航"""
    items = []
    for a in soup.select('.post-item h2 a, .entry-title a, .post-title a, article h2 a, article h3 a'):
        text = a.get_text(strip=True)
        href = a.get('href', '')
        if text and len(text) > 5:
            items.append(f"{text} ({href})")
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


def parse_baicaio_items(soup, base_url):
    """白菜哦 - 每个商品只取一条，不重复"""
    import re
    items = []
    seen_urls = set()
    seen_texts = set()
    for a in soup.find_all('a', href=True):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        if not text or len(text) < 5 or len(text) > 100:
            continue
        # 只取 /item/ 路径下的商品页
        if '/item/' not in href:
            continue
        # 过滤掉"查看详情"/"直达链接"/"阅读全文"等无意义文本
        skip_words = ['查看详情', '直达链接', '阅读全文', '去购买', '更多', '返回']
        if any(w in text for w in skip_words):
            continue
        # URL去重
        if href in seen_urls:
            continue
        # 文本去重（避免同一商品多条）
        text_key = text[:30]
        if text_key in seen_texts:
            continue
        seen_urls.add(href)
        seen_texts.add(text_key)
        if href.startswith('/'):
            from urllib.parse import urljoin
            href = urljoin(base_url, href)
        items.append({'text': text, 'url': href})
    return items[:30]


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
            article_items = parse_baicaio_items(soup, url)
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
            article_items = []
            text = parse_discuz_threadlist(soup)
        elif 'yxssp.com' in url:
            article_items = []
            text = parse_yxssp(soup)
        elif 'ghxi.com' in url:
            article_items = []
            text = parse_ghxi(soup)
        elif 'daydayzhuan.com' in url:
            article_items = parse_daydayzhuan_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif '007ymd.com' in url:
            article_items = parse_007ymd_items(soup, url)
            text = '\n'.join(item['text'] for item in article_items)
        elif 'baicaio.com' in url:
            article_items = parse_baicaio_items_v2(soup, url)
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


# ============================================================
# 邮件推送（网易Claw）
# ============================================================

    # ===== 纯文本正文 =====
    lines = []
    lines.append("站点更新监控巡检报告")
    lines.append("")
    lines.append(f"第 {round_num} 轮巡检  {check_time}")
    lines.append(f"总计 {total} 个站点 | 更新 {updated_count} 个 | 无更新 {len(no_update_results)} 个 | 异常 {len(error_results)} 个")
    lines.append("")

    idx = 1
    for r in updated_results:
        items = r.get('items', [])
        item_count = len(items)
        title = r.get('title', r['url'])
        url = r['url']
        lines.append(f"{idx}. {title} ({url}) [{item_count}条新]")
        for item in items:
            item_text = item['text'] if isinstance(item, dict) else item
            item_url = item['url'] if isinstance(item, dict) else url
            lines.append(f"  - {item_text}")
            lines.append(f"    {item_url}")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    for r in no_update_results:
        title = r.get('title', r['url'])
        url = r['url']
        lines.append(f"{idx}. {title} ({url})")
        lines.append("  暂无新内容")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    for r in error_results:
        url = r['url']
        lines.append(f"{idx}. {url}")
        lines.append(f"  异常: {r['message']}")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    lines.append("")
    lines.append("GitHub Actions 站点巡检机器人 | 每4小时自动巡检 | 163邮箱推送")

    text_body = '\n'.join(lines)

    # HTML 版本：用 <br> 标签换行，兼容所有邮箱客户端
    html_escaped = html_escape(text_body).replace('\n', '<br>\n')
    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:'Courier New',Consolas,monospace;font-size:14px;line-height:1.6;padding:20px;color:#333;">
{html_escaped}
</body>
</html>"""

    return subject, html_body, text_body, all_new_urls


def html_escape(text):
    """转义HTML特殊字符"""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def generate_email_html(round_num, all_site_results, check_time, notified=None):
    """
    生成邮件内容 — 纯文本格式
    all_site_results: 列表，每个元素为 {'url':..., 'title':..., 'summary':..., 'items':..., 'status': 'updated'|'no_update'|'error'|'first', 'message':...}
    items 格式: [{'text': '标题', 'url': '链接'}, ...]
    notified: 已通知过的条目URL集合（set），传入则过滤重复条目
    排序规则：有更新的排在前面（按条目数降序），无更新的排在后面
    返回：(主题, HTML正文, 纯文本正文, 本轮新增条目URL集合)
    """
    if notified is None:
        notified = set()

    updated_results = [r for r in all_site_results if r['status'] == 'updated']
    no_update_results = [r for r in all_site_results if r['status'] in ('no_update', 'first')]
    error_results = [r for r in all_site_results if r['status'] == 'error']

    all_new_urls = set()
    for r in updated_results:
        new_items, new_urls = filter_new_items(r.get('items', []), notified)
        r['items'] = new_items
        all_new_urls.update(new_urls)

    updated_results = [r for r in updated_results if r['items']]
    updated_results.sort(key=lambda r: len(r.get('items', [])), reverse=True)

    total = len(all_site_results)
    updated_count = len(updated_results)
    total_new_items = sum(len(r.get('items', [])) for r in updated_results)

    if updated_count > 0:
        subject = f"【站点更新提醒】第{round_num}轮巡检 | {updated_count}个站点 {total_new_items}条新内容"
    else:
        subject = f"【站点巡检报告】第{round_num}轮巡检 | 暂无更新"

    lines = []
    lines.append("站点更新监控巡检报告")
    lines.append("")
    lines.append(f"第 {round_num} 轮巡检  {check_time}")
    lines.append(f"总计 {total} 个站点 | 更新 {updated_count} 个 | 无更新 {len(no_update_results)} 个 | 异常 {len(error_results)} 个")
    lines.append("")

    idx = 1
    for r in updated_results:
        items = r.get('items', [])
        item_count = len(items)
        title = r.get('title', r['url'])
        url = r['url']
        lines.append(f"{idx}. {title} ({url}) [{item_count}条新]")
        for item in items:
            item_text = item['text'] if isinstance(item, dict) else item
            item_url = item['url'] if isinstance(item, dict) else url
            lines.append(f"  - {item_text}")
            lines.append(f"    {item_url}")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    for r in no_update_results:
        title = r.get('title', r['url'])
        url = r['url']
        lines.append(f"{idx}. {title} ({url})")
        lines.append("  暂无新内容")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    for r in error_results:
        url = r['url']
        lines.append(f"{idx}. {url}")
        lines.append(f"  异常: {r['message']}")
        lines.append("")
        lines.append("---")
        lines.append("")
        idx += 1

    lines.append("")
    lines.append("GitHub Actions 站点巡检机器人 | 每1小时自动巡检 | 163邮箱推送")
    text_body = '\n'.join(lines)

    html_escaped = html_escape(text_body).replace('\n', '<br>\n')
    html_body = ('<!DOCTYPE html>\n<html>\n<head><meta charset="utf-8"></head>\n'
                  '<body style="font-family:\'Courier New\',Consolas,monospace;font-size:14px;line-height:1.6;padding:20px;color:#333;">\n'
                  + html_escaped + '\n</body>\n</html>')

    return subject, html_body, text_body, all_new_urls


def send_email_smtp(subject, html_body, text_body=None):
    """
    通过163邮箱SMTP发送邮件
    返回：(成功标志, 错误信息)
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False, "邮箱配置缺失"

    try:
        message = MIMEMultipart('alternative')
        message['From'] = SMTP_USER
        message['To'] = EMAIL_TO
        message['Subject'] = subject

        if text_body:
            text_part = MIMEText(text_body, 'plain', 'utf-8')
            message.attach(text_part)
        else:
            text_part = MIMEText(subject, 'plain', 'utf-8')
            message.attach(text_part)

        html_part = MIMEText(html_body, 'html', 'utf-8')
        message.attach(html_part)

        print(f"[邮件] 发送到: {EMAIL_TO}")

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, EMAIL_TO, message.as_string())

        print(f"[邮件] ✓ 发送成功: {subject}")
        return True, None

    except smtplib.SMTPAuthenticationError:
        return False, "邮箱认证失败（检查授权码）"
    except smtplib.SMTPException as e:
        return False, f"SMTP错误: {e}"
    except Exception as e:
        return False, f"发送异常: {str(e)}"


def git_commit_if_changed():
    """
    检查是否有变更，仅在有变更时执行commit & push
    变更条件：哈希文件修改 或 新增邮件备份
    
    注意：此函数在GitHub Actions环境中会跳过git操作，
    因为workflow有专门的提交步骤
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


def auto_manage_failed_sites(error_urls, round_num, check_time):
    """
    根据本轮失败站点自动管理暂停/恢复
    - 连续 MAX_CONSECUTIVE_FAILURES 轮失败 → 移入暂停列表
    - 每 RECOVERY_CHECK_INTERVAL 轮尝试恢复暂停站点
    返回：本轮应移除的活跃站点列表
    """
    paused = load_paused_sites()
    run_log = load_run_log()

    # 加载 failed_sites.json 中的连续失败计数
    fail_counts = {}
    if os.path.exists(FAILED_SITES_FILE):
        try:
            with open(FAILED_SITES_FILE, 'r', encoding='utf-8') as f:
                fail_counts = json.load(f)
        except Exception:
            pass

    newly_paused = []

    # 1) 统计本轮失败站点的连续失败次数
    for url in error_urls:
        fail_counts[url] = fail_counts.get(url, 0) + 1

        if fail_counts[url] >= MAX_CONSECUTIVE_FAILURES and url not in paused:
            paused[url] = {
                'paused_at': check_time,
                'paused_round': round_num,
                'reason': f'连续 {fail_counts[url]} 轮失败',
                'fail_count': fail_counts[url]
            }
            newly_paused.append(url)
            print(f"[自动暂停] {url} — {fail_counts[url]} 轮连续失败，已移入暂停列表")

    # 2) 清除本轮成功的站点的失败计数
    all_monitored = set(MONITOR_SITES) - set(paused.keys())
    for url in all_monitored:
        if url not in error_urls and url in fail_counts:
            del fail_counts[url]

    # 3) 尝试恢复暂停站点（按间隔检查）
    total_runs = len(run_log)
    recovered = []
    if total_runs > 0 and total_runs % RECOVERY_CHECK_INTERVAL == 0 and paused:
        print(f"\n[恢复检查] 累计 {total_runs} 轮，尝试恢复暂停站点...")
        urls_to_remove = []
        for url in list(paused.keys()):
            # 简单探测：尝试抓取一次
            success, result = fetch_page_content(url)
            if success:
                recovered.append(url)
                urls_to_remove.append(url)
                print(f"[自动恢复] {url} — 站点已恢复可用")
                del paused[url]
                # 清除失败计数
                fail_counts.pop(url, None)
            time.sleep(1)  # 恢复探测也需延迟

        for url in urls_to_remove:
            if url in paused:
                del paused[url]

    # 保存状态
    save_paused_sites(paused)
    try:
        with open(FAILED_SITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(fail_counts, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return newly_paused, recovered


# ============================================================
# 站点健康面板数据
# ============================================================

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
    print(f"[信息] 已加载已通知条目: {len(notified)} 条")

    # 检查所有活跃站点更新
    all_site_results = []  # 存储所有站点状态（含标题、摘要）
    new_records = old_records.copy()
    success_count = 0
    error_count = 0
    updated_count = 0
    response_times = []

    for idx, url in enumerate(active_sites, 1):
        print(f"\n[{idx}/{len(active_sites)}] 检查: {url}")

        # 检查站点更新
        is_updated, new_hash, message, page_info = check_site_update(url, old_records)

        if is_updated is None:
            # 爬取失败
            print(f"[失败] {message}")
            error_count += 1
            all_site_results.append({
                'url': url,
                'title': url,
                'summary': '',
                'status': 'error',
                'message': message
            })
        else:
            # 更新哈希记录
            new_records[url] = new_hash
            success_count += 1

            if is_updated:
                print(f"[更新] ✅ {message}")
                updated_count += 1
                all_site_results.append({
                    'url': url,
                    'title': page_info.get('title', url) if page_info else url,
                    'summary': page_info.get('summary', '') if page_info else '',
                    'items': page_info.get('items', []) if page_info else [],
                    'status': 'updated',
                    'message': message
                })
            else:
                print(f"[正常] {message}")
                status = 'first' if url not in old_records else 'no_update'
                all_site_results.append({
                    'url': url,
                    'title': page_info.get('title', url) if page_info else url,
                    'summary': '',
                    'items': [],
                    'status': status,
                    'message': message
                })

        # 随机延迟，防止封禁
        delay = get_random_delay()
        time.sleep(delay)

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
    print("[处理] 生成巡检报告邮件...")

    # 总是更新哈希文件
    save_hash_records(new_records)

    # 生成邮件内容（传入已通知条目用于去重）
    subject, html_body, text_body, new_urls = generate_email_html(round_num, all_site_results, check_time, notified)

    # 更新已通知条目：加入本轮所有站点的items URL（去重）
    for r in all_site_results:
        if r['status'] == 'updated':
            for item in r.get('items', []):
                item_url = item['url'] if isinstance(item, dict) else item
                notified.add(item_url)
    save_notified_items(notified)

    print(f"[信息] 本轮新通知条目: {len(new_urls)} 条")

    # 发送邮件
    if SMTP_USER and SMTP_PASSWORD:
        success, error = send_email_smtp(subject, html_body, text_body)
        if not success:
            print(f"[警告] 邮件发送失败: {error}")
    else:
        print("[提示] 邮箱未配置，跳过邮件发送")
        print("[提示] 请在GitHub Secrets中配置 SMTP_USER 和 SMTP_PASSWORD")

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
