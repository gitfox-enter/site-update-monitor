# -*- coding: utf-8 -*-
"""Per-site smart scheduler: adaptive crawl frequency based on site interval config.

Instead of a global "should we run?" decision, each site independently decides
whether it should be crawled based on:
  1. Its configured `interval` in sites.yaml (minutes)
  2. Time since its last successful crawl
  3. Night-time weight (22:00-08:00 Beijing: interval × 2)

This design is inspired by RSSHub's per-route cache TTL:
  - High-activity sites (线报酷 etc.) crawl every 15 min
  - Medium sites (购物比价 etc.) crawl every 60-120 min
  - Low-activity sites (软件站 etc.) crawl every 240-480 min

Usage:
  from crawler.smart_scheduler import get_sites_to_crawl, record_site_run
  sites = get_sites_to_crawl(all_urls)       # returns list of URLs that need crawling
  ...  # crawl those sites
  for url in crawled_urls:
      record_site_run(url)                     # record each site's crawl time
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from common import get_beijing_time

logger = logging.getLogger('scheduler')

# Beijing timezone
_BJ_TZ = timezone(timedelta(hours=8))

# Night hours (Beijing time): double the interval
NIGHT_START = 22  # 22:00
NIGHT_END = 8     # 08:00

# State file: records per-site last crawl timestamps
_SCHEDULER_STATE_FILE = "scheduler_state.json"

# Default interval when not specified in sites.yaml (minutes)
DEFAULT_INTERVAL = 30


# ============================================================
# sites.yaml interval loader
# ============================================================

_SITE_INTERVALS: Dict[str, int] = {}  # {url: interval_minutes}


def load_site_intervals() -> Dict[str, int]:
    """Load per-site interval config from sites.yaml."""
    global _SITE_INTERVALS
    if _SITE_INTERVALS:
        return _SITE_INTERVALS
    try:
        import yaml
        yaml_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "sites.yaml"
        )
        with open(yaml_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        for site in data.get('sites', []):
            url = site.get('url', '')
            interval = site.get('interval', DEFAULT_INTERVAL)
            if url:
                _SITE_INTERVALS[url] = interval
    except Exception as e:
        logger.warning("加载 sites.yaml interval 失败: %s", e)
    return _SITE_INTERVALS


def get_site_interval(url: str) -> int:
    """Get the configured interval (minutes) for a site. Falls back to DEFAULT_INTERVAL."""
    intervals = load_site_intervals()
    return intervals.get(url, DEFAULT_INTERVAL)


# ============================================================
# State persistence
# ============================================================

def _load_state() -> Dict[str, Any]:
    """Load scheduler state (per-site last crawl timestamps)."""
    if not os.path.exists(_SCHEDULER_STATE_FILE):
        return {'sites': {}}
    try:
        with open(_SCHEDULER_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'sites': {}}


def _save_state(state: Dict[str, Any]) -> None:
    """Save scheduler state atomically."""
    tmp = _SCHEDULER_STATE_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _SCHEDULER_STATE_FILE)
    except Exception as e:
        logger.warning("调度状态保存失败: %s", e)
        if os.path.exists(tmp):
            os.remove(tmp)


# ============================================================
# Core per-site scheduling logic
# ============================================================

def _is_night(now: Optional[datetime] = None) -> bool:
    """Check if current Beijing time is in night hours (22:00-08:00)."""
    if now is None:
        now = get_beijing_time()
    hour = now.hour
    return hour >= NIGHT_START or hour < NIGHT_END


def _get_effective_interval(url: str, is_night: bool = False) -> int:
    """Get effective interval in minutes, considering adaptive tier and night weight.

    Adaptive logic (#37):
      - If adaptive tier says 'low' update frequency but base interval is high,
        reduce interval (site doesn't update often, no need to check frequently).
      - If tier is 'dead', return infinity (never crawl).
    """
    base_interval = get_site_interval(url)
    multiplier = 2 if is_night else 1

    # Adaptive tier adjustment
    from crawler.config import get_all_adaptive_tiers
    tiers = get_all_adaptive_tiers()
    tier_info = tiers.get(url, {})
    tier = tier_info.get('tier', '')

    if tier == 'dead':
        return 999999  # effectively never crawl
    elif tier == 'low' and base_interval <= 60:
        # Low activity site with tight interval: loosen to save resources
        base_interval = max(base_interval, 120)

    return base_interval * multiplier


def _minutes_since_last_crawl(state: Dict[str, Any], url: str) -> Optional[int]:
    """Calculate minutes since last crawl for a specific site."""
    last_str = state.get('sites', {}).get(url, {}).get('last_crawl')
    if not last_str:
        return None
    try:
        last_dt = datetime.strptime(last_str, '%Y-%m-%d %H:%M:%S')
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=_BJ_TZ)
        now = get_beijing_time()
        delta = now - last_dt
        return int(delta.total_seconds() / 60)
    except (ValueError, TypeError):
        return None


def get_sites_to_crawl(
    all_urls: List[str],
    mode: str = 'crawl',
) -> Tuple[List[str], List[str]]:
    """Determine which sites need crawling based on per-site intervals.

    Args:
        all_urls: All candidate URLs to consider
        mode: 'crawl' or 'fast_check' (fast_check ignores intervals, always crawl)

    Returns:
        (sites_to_crawl: List[str], skipped_sites: List[str])
    """
    now = get_beijing_time()
    is_night = _is_night(now)
    state = _load_state()
    load_site_intervals()  # Ensure intervals are loaded

    if mode == 'fast_check':
        # Fast check: always run all sites (they are pre-filtered by fast_check flag)
        return all_urls, []

    to_crawl: List[str] = []
    skipped: List[str] = []

    for url in all_urls:
        effective_interval = _get_effective_interval(url, is_night)
        minutes_since = _minutes_since_last_crawl(state, url)

        if minutes_since is None:
            # Never crawled before → must crawl
            to_crawl.append(url)
            continue

        if minutes_since >= effective_interval:
            to_crawl.append(url)
        else:
            skipped.append(url)

    if skipped:
        logger.info(
            "[智能调度] 跳过 %d 个站点（间隔未到），抓取 %d 个站点 (%s)",
            len(skipped), len(to_crawl),
            '夜间' if is_night else '白天'
        )
    else:
        logger.info(
            "[智能调度] 全部 %d 个站点需要抓取 (%s)",
            len(to_crawl), '夜间' if is_night else '白天'
        )

    return to_crawl, skipped


def record_site_run(url: str) -> None:
    """Record that a site has been successfully crawled. Call after each site's crawl."""
    now = get_beijing_time()
    state = _load_state()
    if 'sites' not in state:
        state['sites'] = {}
    state['sites'][url] = {
        'last_crawl': now.strftime('%Y-%m-%d %H:%M:%S'),
    }
    _save_state(state)


def record_bulk_run(urls: List[str]) -> None:
    """Record multiple sites as crawled at the current time."""
    now = get_beijing_time()
    state = _load_state()
    if 'sites' not in state:
        state['sites'] = {}
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    for url in urls:
        state['sites'][url] = {
            'last_crawl': now_str,
        }
    _save_state(state)


def get_site_next_crawl(url: str) -> Optional[str]:
    """Get the estimated next crawl time for a site (for display in RSS/UI).

    Returns:
        Human-readable string like "约15分钟后" or "约2小时后", or None if never crawled.
    """
    state = _load_state()
    is_night = _is_night()
    effective_interval = _get_effective_interval(url, is_night)
    minutes_since = _minutes_since_last_crawl(state, url)

    if minutes_since is None:
        return "即将抓取"

    remaining = effective_interval - minutes_since
    if remaining <= 0:
        return "即将抓取"

    if remaining < 60:
        return f"约{remaining}分钟后"
    elif remaining < 1440:
        hours = remaining // 60
        mins = remaining % 60
        if mins:
            return f"约{hours}小时{mins}分钟后"
        return f"约{hours}小时后"
    else:
        days = remaining // 1440
        return f"约{days}天后"


def get_site_next_crawl_minutes(url: str) -> Optional[int]:
    """Get minutes until next crawl for a site (for programmatic use).

    Returns:
        Minutes until next crawl, 0 if due now, or None if never crawled.
    """
    state = _load_state()
    is_night = _is_night()
    effective_interval = _get_effective_interval(url, is_night)
    minutes_since = _minutes_since_last_crawl(state, url)

    if minutes_since is None:
        return 0
    remaining = effective_interval - minutes_since
    return max(0, remaining)


# ============================================================
# Legacy API compatibility (global should_run / record_run)
# These still work for fast_check's global decision
# ============================================================

def should_run(mode: str = 'crawl') -> Tuple[bool, str]:
    """Legacy API: global should_run decision.

    For 'fast_check': always returns True (per-site intervals handle crawl timing).
    For 'crawl': checks if ANY site needs crawling.
    """
    if mode == 'fast_check':
        # Fast check always runs; per-site filtering is done at the site list level
        return True, "快速检查始终执行（站点级调度）"

    # For full crawl: check if at least some sites need crawling
    # Load the full site list
    try:
        from crawler.config import MONITOR_SITES
        all_urls = MONITOR_SITES
    except ImportError:
        all_urls = list(load_site_intervals().keys())

    to_crawl, skipped = get_sites_to_crawl(all_urls, mode='crawl')
    if to_crawl:
        return True, f"有 {len(to_crawl)} 个站点需要抓取（跳过 {len(skipped)} 个）"
    return False, f"所有 {len(all_urls)} 个站点间隔未到，跳过本轮"


def record_run(mode: str = 'crawl') -> None:
    """Legacy API: record that a run has completed.

    For per-site scheduling, use record_site_run() or record_bulk_run() instead.
    This is kept for backward compatibility with fast_check.py.
    """
    now = get_beijing_time()
    state = _load_state()
    state[f'{mode}_last_run'] = now.strftime('%Y-%m-%d %H:%M:%S')
    _save_state(state)
    logger.info("[智能调度] %s: 记录全局运行时间 %s", mode,
                now.strftime('%Y-%m-%d %H:%M:%S'))
