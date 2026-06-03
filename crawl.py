#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Actions 多站点更新监控系统
功能：爬取46个站点 → MD5比对检测更新 → 163邮箱SMTP推送 → 本地备份归档
时间：每4小时执行一次（00:00, 04:00, 08:00, 12:00, 16:00, 20:00）
时区：Asia/Shanghai（北京时间）
"""

import os
import sys
import time
import hashlib
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
import json
import subprocess

# ============================================================
# 配置区域
# ============================================================

# 46个监控站点完整列表
MONITOR_SITES = [
    "https://xianbaomi.com/",
    "http://www.0818tuan.com/",
    "http://79tao.linejia.com/",
    "https://www.daydayzhuan.com/",
    "https://cjx8.com/",
    "https://907k.cn/",
    "https://ym2.cc/",
    "https://b1.ymxianbao.cn/",
    "https://www.007ymd.com/",
    "http://news.ixbk.net/",
    "https://www.zhuanyes.com/xianbao/",
    "https://www.iqshw.com/",
    "https://www.huodong5.com/",
    "https://www.yxssp.com/",
    "https://www.wobangzhao.com/",
    "https://www.kxdao.net/forum-42-1.html",
    "https://www.baicaio.com/",
    "https://yangmao.wang/",
    "https://www.12345pro.com/",
    "https://news.ixbk.fun/",
    "https://www.h6room.com/",
    "https://www.ithome.com/zt/xijiayi",
    "https://free.apprcn.com/",
    "https://www.lsapk.com/",
    "https://www.ghxi.com/category/all",
    "https://www.appinn.com/",
    "https://www.423down.com/",
    "https://foxirj.com/",
    "https://store.steampowered.com/search/?specials=1&os=win",
    "https://www.gog.com/partner/free_games",
    "https://store.steampowered.com/",
    "https://www.ziyuanting.com/",
    "https://www.wycad.com/",
    "https://www.ddooo.com/",
    "https://www.onlinedown.net/",
    "https://www.downxia.com/",
    "https://www.ypojie.com/",
    "https://www.52hb.com/forum.php",
    "https://m.hybase.com/",
    "https://xzba.cc/",
    "https://pc.qq.com/category/rank.html",
    "https://store.epicgames.com/zh-CN/free-games",
    "https://feed.iplaysoft.com",
    "https://plink.anyfeeder.com/ign/cn",
    "https://plink.anyfeeder.com/3dm",
    "https://plink.anyfeeder.com/gamersky",
]

# 文件存储配置
HASH_RECORD_FILE = "hash_record.txt"
EMAIL_BACKUP_DIR = "email_backup"

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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
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
    import random
    return random.choice(USER_AGENTS)


def get_random_delay():
    """随机返回请求延迟时间（秒）"""
    import random
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
# 爬虫核心逻辑
# ============================================================

def fetch_page_content(url):
    """
    爬取页面完整正文
    返回：(成功标志, 内容/错误信息)
    内容包含：(text, title, summary)
    """
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
    }
    
    try:
        print(f"[爬取] {url}")
        response = requests.get(
            url, 
            headers=headers, 
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )
        
        # 检查HTTP状态码
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        
        # 获取页面编码
        encoding = response.encoding or 'utf-8'
        if encoding.lower() in ['gb2312', 'gbk']:
            encoding = 'gbk'
        
        content = response.content.decode(encoding, errors='ignore')
        
        # 使用BeautifulSoup提取正文内容
        soup = BeautifulSoup(content, 'html.parser')
        
        # 获取页面标题
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else url
        
        # 移除脚本、样式、注释等干扰内容
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()
        
        # 获取正文文本
        body = soup.find('body')
        if body:
            text = body.get_text(separator=' ', strip=True)
        else:
            text = soup.get_text(separator=' ', strip=True)
        
        # 清理多余空白
        text = ' '.join(text.split())
        
        if not text:
            return False, "页面正文为空"
        
        # 生成摘要（前300个字符）
        summary = text[:300] + '...' if len(text) > 300 else text
        
        # 返回包含标题和摘要的字典
        return True, {
            'text': text,
            'title': title,
            'summary': summary
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
        'summary': result['summary']
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

def generate_email_html(round_num, all_site_results, check_time):
    """
    生成邮件HTML内容 — 显示所有站点状态
    all_site_results: 列表，每个元素为 {'url':..., 'title':..., 'summary':..., 'status': 'updated'|'no_update'|'error'|'first', 'message':..., 'page_info':...}
    排序规则：有更新的排在前面，无更新的排在后面
    返回：(主题, HTML正文, 纯文本正文)
    """
    # 统计
    updated_results = [r for r in all_site_results if r['status'] == 'updated']
    no_update_results = [r for r in all_site_results if r['status'] in ('no_update', 'first')]
    error_results = [r for r in all_site_results if r['status'] == 'error']
    total = len(all_site_results)
    updated_count = len(updated_results)

    # 排序：更新的前面，无更新的后面
    sorted_results = updated_results + no_update_results + error_results

    # 邮件标题
    if updated_count > 0:
        subject = f"【站点更新提醒】第{round_num}轮巡检 | {updated_count}个站点有更新"
    else:
        subject = f"【站点巡检报告】第{round_num}轮巡检 | 暂无更新"

    # ===== 纯文本正文 =====
    text_body = f"""站点更新监控巡检报告

📅 当日第 {round_num} 次全自动巡检
⏰ 巡检时间：{check_time}
📊 总计 {total} 个站点 | 更新 {updated_count} 个 | 无更新 {len(no_update_results)} 个 | 异常 {len(error_results)} 个

"""
    if updated_results:
        text_body += "━━━ 有更新的站点 ━━━\n\n"
        for idx, r in enumerate(updated_results, 1):
            text_body += f"[更新] {idx}. {r['title']}\n   URL: {r['url']}\n   摘要: {r['summary']}\n\n"

    if no_update_results:
        text_body += "━━━ 无更新的站点 ━━━\n\n"
        for idx, r in enumerate(no_update_results, 1):
            text_body += f"[无新内容] {idx}. {r['title']}\n   URL: {r['url']}\n\n"

    if error_results:
        text_body += "━━━ 异常的站点 ━━━\n\n"
        for idx, r in enumerate(error_results, 1):
            text_body += f"[异常] {idx}. {r['url']}\n   原因: {r['message']}\n\n"

    text_body += """
━━━━━━━━━━━━━━━━━━━━━━━━━━
🤖 自动化监控来源：GitHub Actions 站点巡检机器人
⏱ 每4小时自动巡检 | 零运维 | 稳定可靠
✉️ 163邮箱推送服务
"""

    # ===== HTML正文 =====
    body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px 8px 0 0;
            text-align: center;
        }}
        .content {{
            background: #f9f9f9;
            padding: 20px;
            border: 1px solid #e0e0e0;
            border-top: none;
        }}
        .info-box {{
            background: white;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 15px;
            border-left: 4px solid #667eea;
        }}
        .section-title {{
            background: #667eea;
            color: white;
            padding: 10px 15px;
            border-radius: 5px;
            margin: 20px 0 10px 0;
            font-size: 15px;
        }}
        .section-title.no-update {{
            background: #9e9e9e;
        }}
        .section-title.error {{
            background: #e53935;
        }}
        .site-item.updated {{
            padding: 15px;
            margin: 10px 0;
            background: #f0f4ff;
            border-radius: 5px;
            border-left: 3px solid #667eea;
        }}
        .site-item.updated a {{
            color: #667eea;
            text-decoration: none;
            font-weight: 600;
            font-size: 15px;
        }}
        .site-item.updated a:hover {{
            text-decoration: underline;
        }}
        .site-summary {{
            margin-top: 8px;
            padding: 10px;
            background: white;
            border-radius: 3px;
            font-size: 13px;
            color: #666;
            line-height: 1.5;
        }}
        .site-item.no-update {{
            padding: 10px 15px;
            margin: 6px 0;
            background: #fafafa;
            border-radius: 5px;
            border-left: 3px solid #e0e0e0;
            color: #999;
            font-size: 14px;
        }}
        .site-item.no-update a {{
            color: #999;
            text-decoration: none;
        }}
        .site-item.error {{
            padding: 10px 15px;
            margin: 6px 0;
            background: #fff5f5;
            border-radius: 5px;
            border-left: 3px solid #e53935;
            color: #c62828;
            font-size: 14px;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 12px;
            margin-left: 8px;
        }}
        .badge.updated {{ background: #667eea; color: white; }}
        .badge.no-update {{ background: #e0e0e0; color: #666; }}
        .badge.error {{ background: #e53935; color: white; }}
        .footer {{
            text-align: center;
            padding: 15px;
            color: #999;
            font-size: 12px;
            border-top: 1px solid #e0e0e0;
            margin-top: 20px;
        }}
        .highlight {{
            color: #667eea;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h2 style="margin: 0;">🔔 站点更新监控巡检报告</h2>
    </div>

    <div class="content">
        <div class="info-box">
            <p style="margin: 5px 0;">📅 当日第 <span class="highlight">{round_num}</span> 次全自动巡检</p>
            <p style="margin: 5px 0;">⏰ 巡检时间：<span class="highlight">{check_time}</span></p>
            <p style="margin: 5px 0;">📊 总计 <span class="highlight">{total}</span> 个站点 ｜
               更新 <span class="highlight" style="color:#2e7d32">{updated_count}</span> 个 ｜
               无更新 <span class="highlight" style="color:#9e9e9e">{len(no_update_results)}</span> 个 ｜
               异常 <span class="highlight" style="color:#e53935">{len(error_results)}</span> 个</p>
        </div>
"""

    # 有更新的站点
    if updated_results:
        body += f'        <div class="section-title">✅ 有更新的站点（{len(updated_results)}个）</div>\n'
        for idx, r in enumerate(updated_results, 1):
            body += f"""
            <div class="site-item updated">
                <strong>{idx}.</strong> <a href="{r['url']}" target="_blank" rel="noopener noreferrer">{r['title']}</a>
                <span class="badge updated">已更新</span>
                <div class="site-summary">{r['summary']}</div>
            </div>
"""

    # 无更新的站点
    if no_update_results:
        body += f'        <div class="section-title no-update">⭕ 无更新的站点（{len(no_update_results)}个）</div>\n'
        for idx, r in enumerate(no_update_results, 1):
            body += f"""
            <div class="site-item no-update">
                {idx}. <a href="{r['url']}" target="_blank" rel="noopener noreferrer">{r['title']}</a>
                <span class="badge no-update">无新内容</span>
            </div>
"""

    # 异常的站点
    if error_results:
        body += f'        <div class="section-title error">❌ 异常的站点（{len(error_results)}个）</div>\n'
        for idx, r in enumerate(error_results, 1):
            body += f"""
            <div class="site-item error">
                {idx}. {r['url']} <span class="badge error">异常</span> — {r['message']}
            </div>
"""

    body += """
    </div>

    <div class="footer">
        <p style="margin: 5px 0;">🤖 自动化监控来源：GitHub Actions 站点巡检机器人</p>
        <p style="margin: 5px 0;">⏱ 每4小时自动巡检 | 零运维 | 稳定可靠</p>
        <p style="margin: 5px 0; color: #667eea;">✉️ 163邮箱推送服务</p>
    </div>
</body>
</html>
"""

    return subject, body, text_body


def send_email_smtp(subject, html_body, text_body=None):
    """
    通过163邮箱SMTP发送邮件
    返回：(成功标志, 错误信息)
    """
    if not SMTP_USER or not SMTP_PASSWORD:
        return False, "邮箱配置缺失"
    
    try:
        # 创建邮件
        message = MIMEMultipart('alternative')
        message['From'] = SMTP_USER
        message['To'] = EMAIL_TO
        message['Subject'] = subject
        
        # 添加纯文本正文（邮件客户端会优先显示）
        if text_body:
            text_part = MIMEText(text_body, 'plain', 'utf-8')
            message.attach(text_part)
        else:
            # 如果没有提供纯文本，从主题生成
            text_part = MIMEText(subject, 'plain', 'utf-8')
            message.attach(text_part)
        
        # 添加HTML正文
        html_part = MIMEText(html_body, 'html', 'utf-8')
        message.attach(html_part)
        
        print(f"[邮件] 发送到: {EMAIL_TO}")
        
        # 连接SMTP服务器并发送
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


def save_email_backup(round_num, html_body):
    """
    保存邮件HTML到本地备份
    文件名：yyyyMMdd_第N轮_站点更新邮件备份.html
    """
    try:
        # 确保备份目录存在
        os.makedirs(EMAIL_BACKUP_DIR, exist_ok=True)
        
        # 生成文件名
        today = get_beijing_time().strftime('%Y%m%d')
        filename = f"{today}_第{round_num}轮_站点更新邮件备份.html"
        filepath = os.path.join(EMAIL_BACKUP_DIR, filename)
        
        # 保存HTML文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_body)
        
        print(f"[备份] 邮件已保存: {filepath}")
        return filepath
        
    except Exception as e:
        print(f"[错误] 邮件备份失败: {e}")
        return None


# ============================================================
# Git提交管理
# ============================================================

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
        
        # Git push
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
# 主流程
# ============================================================

def main():
    """主监控流程"""
    print("=" * 60)
    print("GitHub Actions 多站点更新监控系统")
    print("=" * 60)
    
    # 获取当前时间和轮次
    now = get_beijing_time()
    round_num = get_current_round()
    check_time = now.strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"[启动] 北京时间: {check_time}")
    print(f"[启动] 当日第 {round_num} 轮巡检")
    print(f"[启动] 监控站点数: {len(MONITOR_SITES)}")
    print("-" * 60)
    
    # 加载历史哈希记录
    old_records = load_hash_records()
    print(f"[信息] 已加载哈希记录: {len(old_records)} 条")
    
    # 检查所有站点更新
    all_site_results = []  # 存储所有站点状态（含标题、摘要）
    new_records = old_records.copy()
    success_count = 0
    error_count = 0
    updated_count = 0

    for idx, url in enumerate(MONITOR_SITES, 1):
        print(f"\n[{idx}/{len(MONITOR_SITES)}] 检查: {url}")

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
                    'status': status,
                    'message': message
                })

        # 随机延迟，防止封禁
        delay = get_random_delay()
        time.sleep(delay)

    print("\n" + "=" * 60)
    print(f"[统计] 成功: {success_count} | 失败: {error_count}")
    print(f"[统计] 更新站点: {updated_count} 个")

    print("\n" + "-" * 60)
    print("[处理] 生成巡检报告邮件...")

    # 生成邮件内容（所有站点状态）
    subject, html_body, text_body = generate_email_html(round_num, all_site_results, check_time)

    # 保存邮件备份
    backup_path = save_email_backup(round_num, html_body)

    # 发送邮件
    if SMTP_USER and SMTP_PASSWORD:
        success, error = send_email_smtp(subject, html_body, text_body)
        if not success:
            print(f"[警告] 邮件发送失败: {error}")
            print("[提示] 邮件已本地备份，可手动查看")
    else:
        print("[警告] 邮箱未配置，跳过邮件发送")
        print("[提示] 请在GitHub Secrets中配置 SMTP_USER 和 SMTP_PASSWORD")

    # Git提交
    git_commit_if_changed()
    
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
