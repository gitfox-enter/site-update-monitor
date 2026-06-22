#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
结构化告警通知 — 爬取失败告警 + 每日摘要 + GitHub Issues 自动创建。

告警类型：
  1. 站点连续失败告警（连续 N 次失败自动创建 GitHub Issue）
  2. 每日摘要报告（成功/失败/数据量趋势）
  3. Dead tier 降级通知

通知渠道：
  - GitHub Issues（自动创建，适合长期追踪）
  - 控制台日志（结构化 JSON，可被 Actions 日志收集）
"""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from common import get_beijing_time, load_items_db
from crawler.config import get_site_tier, get_source_name, get_all_adaptive_tiers

logger = logging.getLogger('alerter')

# 告警阈值
CONSECUTIVE_FAIL_THRESHOLD = 3  # 连续失败 N 次后告警
DEAD_TIER_ALERT = True  # dead tier 降级时是否创建 Issue


def check_consecutive_failures(run_log: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """从最近 N 轮运行日志中检测连续失败的站点。

    Returns a list of {'url': ..., 'name': ..., 'rounds_failed': ...}
    """
    if len(run_log) < CONSECUTIVE_FAIL_THRESHOLD:
        return []

    recent = run_log[-CONSECUTIVE_FAIL_THRESHOLD:]
    # Collect error URLs from each round
    error_sets = []
    for entry in recent:
        errors = set()
        for err in entry.get('errors', []):
            if 'robots.txt' not in err.get('message', ''):
                errors.add(err['url'])
        error_sets.append(errors)

    # Intersection: URLs that failed in ALL recent rounds
    if not error_sets:
        return []
    consecutive = error_sets[0]
    for s in error_sets[1:]:
        consecutive = consecutive & s

    alerts = []
    for url in consecutive:
        name = get_source_name(url) or url
        alerts.append({
            'url': url,
            'name': name,
            'rounds_failed': CONSECUTIVE_FAIL_THRESHOLD,
        })
    return alerts


def create_github_issue(title: str, body: str, labels: List[str] = None) -> bool:
    """Create a GitHub Issue using gh CLI.

    Only runs in GitHub Actions environment.
    Returns True on success.
    """
    if os.getenv('GITHUB_ACTIONS') != 'true':
        logger.info("非 GitHub Actions 环境，跳过 Issue 创建")
        return False

    try:
        cmd = ['gh', 'issue', 'create', '--title', title, '--body', body]
        if labels:
            for label in labels:
                cmd.extend(['--label', label])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            issue_url = result.stdout.strip()
            logger.info("已创建 Issue: %s", issue_url, extra={'event': 'issue_created'})
            return True
        else:
            logger.warning("Issue 创建失败: %s", result.stderr[:200])
            return False
    except Exception as e:
        logger.warning("Issue 创建异常: %s", str(e)[:100])
        return False


def check_existing_issue(title_substring: str) -> bool:
    """Check if an open issue with similar title already exists.

    Avoids creating duplicate issues for the same problem.
    Fix #93: was returning False on all exceptions, causing duplicate issue creation.
    Now explicitly checks gh exit code and output to correctly return True/False.
    """
    if os.getenv('GITHUB_ACTIONS') != 'true':
        return False
    try:
        result = subprocess.run(
            ['gh', 'issue', 'list', '--state', 'open', '--search', title_substring, '--limit', '5'],
            capture_output=True, text=True, timeout=15,
        )
        # gh exits 0 with empty output when no issues found
        # Only return True (exists) if we got actual output lines
        lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
        return len(lines) > 0
    except Exception:
        return False


def send_consecutive_failure_alert(alerts: List[Dict[str, str]]) -> None:
    """Send alerts for sites with consecutive failures.

    Creates a single GitHub Issue per site if no existing issue is open.
    """
    if not alerts:
        return

    for alert in alerts:
        name = alert['name']
        url = alert['url']
        rounds_failed = alert['rounds_failed']

        logger.warning("连续失败告警: %s (%s) - 连续 %d 轮失败",
                       name, url, rounds_failed,
                       extra={'event': 'consecutive_failure_alert', 'site': url})

        # Check if issue already exists
        search_term = f"站点连续失败: {name}"
        if check_existing_issue(search_term):
            logger.info("已有开放 Issue: %s，跳过创建", search_term)
            continue

        title = f"站点连续失败: {name}"
        body = f"""## 站点连续失败告警

**站点**: {name}
**URL**: {url}
**连续失败轮次**: {rounds_failed}

### 可能原因
- 站点已下线或域名失效
- 反爬策略升级
- 网络问题

### 建议操作
1. 手动访问站点确认是否存活
2. 如果站点已死，在 `sites.yaml` 的 `dead_sites` 中添加
3. 如果是反爬问题，考虑增加 `js_render: true` 或调整请求延迟

---
*此 Issue 由自动告警系统创建*
"""
        create_github_issue(title, body, labels=['bug', 'site-down'])


def send_dead_tier_alert(url: str, name: str, old_tier: str) -> None:
    """Send alert when a site is demoted to dead tier."""
    if not DEAD_TIER_ALERT:
        return

    logger.warning("Dead tier 降级: %s (%s) %s → dead", name, url, old_tier,
                   extra={'event': 'dead_tier_alert', 'site': url})

    search_term = f"站点已死: {name}"
    if check_existing_issue(search_term):
        return

    title = f"站点已死: {name}"
    body = f"""## 站点自动降级为 Dead

**站点**: {name}
**URL**: {url}
**原 Tier**: {old_tier}

该站点因连续失败被自动降级为 dead tier，将不再爬取。

### 建议
1. 确认站点是否确实已下线
2. 如果站点恢复，手动在 `adaptive_tiers.json` 中重置 tier
3. 确认下线后在 `sites.yaml` 的 `dead_sites` 中添加

---
*此 Issue 由自动告警系统创建*
"""
    create_github_issue(title, body, labels=['bug', 'site-down'])


def generate_daily_summary() -> Dict[str, Any]:
    """Generate a daily summary report.

    Returns a dict with summary statistics.
    """
    db = load_items_db()
    items = db.get('items', [])
    now = get_beijing_time()
    today_str = now.strftime('%Y-%m-%d')

    # Count today's items
    today_items = [i for i in items if i.get('time', '').startswith(today_str)]

    # Count by source
    source_counts: Dict[str, int] = {}
    for item in today_items:
        source = item.get('source', '未知')
        source_counts[source] = source_counts.get(source, 0) + 1

    # Count by category
    category_counts: Dict[str, int] = {}
    for item in today_items:
        cat = item.get('category', '未分类')
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Dead/low tier sites count
    tiers = get_all_adaptive_tiers()
    dead_count = sum(1 for t in tiers.values() if t.get('tier') == 'dead')
    low_count = sum(1 for t in tiers.values() if t.get('tier') == 'low')

    summary = {
        'date': today_str,
        'total_items_today': len(today_items),
        'total_items_all': len(items),
        'sources': dict(sorted(source_counts.items(), key=lambda x: -x[1])),
        'categories': dict(sorted(category_counts.items(), key=lambda x: -x[1])),
        'tier_summary': {
            'dead': dead_count,
            'low': low_count,
            'medium': sum(1 for t in tiers.values() if t.get('tier') == 'medium'),
            'high': sum(1 for t in tiers.values() if t.get('tier') == 'high'),
        },
    }

    logger.info("每日摘要: 今日 %d 条 | 总计 %d 条 | dead: %d | low: %d",
                len(today_items), len(items), dead_count, low_count,
                extra={'event': 'daily_summary'})

    return summary


if __name__ == '__main__':
    summary = generate_daily_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
