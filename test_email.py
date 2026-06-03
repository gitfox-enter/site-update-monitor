#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试版本：强制发送邮件
"""

import os
import sys
import time
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# 只测试3个简单站点
TEST_SITES = [
    "https://www.baidu.com",
    "https://www.github.com",
    "https://www.python.org",
]

# Claw配置
CLAW_AUTH_URL = os.getenv("CLAW_AUTH_URL", "t1/VSrSdLb78uqwSSCtWBTAbTRwZki")
CLAW_API_KEY = os.getenv("CLAWEMAIL_API_KEY", "")
CLAW_USER = os.getenv("CLAWEMAIL_USER", "")

def get_beijing_time():
    beijing_tz = timezone(timedelta(hours=8))
    return datetime.now(beijing_tz)

def get_random_ua():
    import random
    return random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    ])

def calculate_md5(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def fetch_page_content(url):
    """爬取页面内容"""
    headers = {
        'User-Agent': get_random_ua(),
        'Accept': 'text/html,application/xhtml+xml',
    }
    
    try:
        print(f"[爬取] {url}")
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"
        
        # 解析HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 移除脚本和样式
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        
        text = soup.get_text(separator=' ', strip=True)
        text = ' '.join(text.split())
        
        return True, text
        
    except Exception as e:
        return False, str(e)

def send_claw_email(subject, html_body):
    """发送邮件"""
    if not CLAW_API_KEY or not CLAW_USER:
        print("[错误] Claw邮箱未配置")
        return False, "未配置"
    
    try:
        url = "https://api.claw.163.com/api/send"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {CLAW_API_KEY}"
        }
        
        data = {
            "authUrl": CLAW_AUTH_URL,
            "to": CLAW_USER,
            "subject": subject,
            "html": html_body,
            "text": subject
        }
        
        print(f"[邮件] 发送到: {CLAW_USER}")
        response = requests.post(url, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success') or result.get('code') == 0:
                print(f"[邮件] ✓ 发送成功")
                return True, None
            else:
                return False, result.get('message', '未知错误')
        else:
            return False, f"HTTP {response.status_code}"
            
    except Exception as e:
        return False, str(e)

def generate_email_html(updated_sites, check_time):
    """生成邮件HTML"""
    subject = f"【站点更新提醒】测试邮件 - 共{len(updated_sites)}个网站"
    
    body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
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
        }}
        .info-box {{
            background: white;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 15px;
            border-left: 4px solid #667eea;
        }}
        .site-item {{
            padding: 10px;
            margin: 8px 0;
            background: #f0f4ff;
            border-radius: 5px;
        }}
        .site-item a {{
            color: #667eea;
            text-decoration: none;
        }}
        .footer {{
            text-align: center;
            padding: 15px;
            color: #999;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h2 style="margin: 0;">🔔 站点更新监控提醒</h2>
    </div>
    
    <div class="content">
        <div class="info-box">
            <p>📅 发送时间：{check_time}</p>
            <p>📊 检测到 <strong>{len(updated_sites)}</strong> 个网站内容更新</p>
        </div>
        
        <div style="background: white; border-radius: 5px; padding: 15px;">
            <h3>更新站点列表</h3>
"""
    
    for idx, site in enumerate(updated_sites, 1):
        body += f"""
            <div class="site-item">
                <strong>{idx}.</strong> <a href="{site}" target="_blank">{site}</a>
            </div>
"""
    
    body += """
        </div>
    </div>
    
    <div class="footer">
        <p>🤖 GitHub Actions 站点巡检机器人</p>
        <p>⏱ 每4小时自动巡检 | 零运维 | 稳定可靠</p>
    </div>
</body>
</html>
"""
    
    return subject, body

def main():
    print("=" * 60)
    print("测试邮件发送")
    print("=" * 60)
    
    now = get_beijing_time()
    check_time = now.strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"时间: {check_time}")
    print(f"站点数: {len(TEST_SITES)}")
    print()
    
    # 爬取站点
    updated_sites = []
    for url in TEST_SITES:
        success, result = fetch_page_content(url)
        if success:
            print(f"[成功] 内容长度: {len(result)}")
            updated_sites.append(url)
        else:
            print(f"[失败] {result}")
        time.sleep(0.5)
    
    print()
    print("=" * 60)
    
    # 强制发送邮件
    if CLAW_API_KEY and CLAW_USER:
        subject, html_body = generate_email_html(updated_sites, check_time)
        
        # 保存HTML备份
        os.makedirs("email_backup", exist_ok=True)
        backup_file = f"email_backup/test_{now.strftime('%Y%m%d_%H%M%S')}.html"
        with open(backup_file, 'w', encoding='utf-8') as f:
            f.write(html_body)
        print(f"[备份] {backup_file}")
        
        # 发送邮件
        success, error = send_claw_email(subject, html_body)
        if success:
            print("\n✓ 邮件发送成功！请检查您的邮箱")
        else:
            print(f"\n✗ 邮件发送失败: {error}")
    else:
        print("\n[错误] 请配置环境变量:")
        print("  export CLAWEMAIL_API_KEY='your-api-key'")
        print("  export CLAWEMAIL_USER='your@email.com'")

if __name__ == "__main__":
    main()
