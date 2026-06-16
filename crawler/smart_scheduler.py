# -*- coding: utf-8 -*-
"""Smart scheduler: adaptive crawl frequency based on recent activity.

Decides whether a scheduled crawl/fast_check run should execute or skip,
based on the number of new items found in recent runs and current time.

Algorithm:
  1. Read recent run_log entries (last N runs)
  2. Calculate average new_items per run
  3. Apply time-of-day weight (night = lower priority)
  4. Map activity level to minimum interval
  5. If time since last run < minimum interval → skip

Thresholds (for crawl):
  | avg new_items | min interval | label    |
  |---------------|-------------|----------|
  | >= 10         | 30 min      | active   |
  | 3-9           | 60 min      | normal   |
  | 1-2           | 120 min     | low      |
  | 0             | 240 min     | idle     |

Time weights:
  - 08:00-22:00 Beijing: full thresholds (daytime, more activity)
  - 22:00-08:00 Beijing: double the interval (night, less activity)

For fast_check, thresholds are more aggressive (lighter job):
  | avg new_items | min interval |
  |---------------|-------------|
  | >= 3          | 30 min      |
  | 1-2           | 60 min      |
  | 0             | 120 min     |
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from common import get_beijing_time

logger = logging.getLogger('scheduler')

# Beijing timezone
_BJ_TZ = timezone(timedelta(hours=8))

# ============================================================
# Configuration
# ============================================================

# Crawl thresholds: (min_new_items, min_interval_minutes)
CRAWL_THRESHOLDS: List[Tuple[int, int]] = [
    (10, 30),   # active: >= 10 new items → 30 min
    (3, 60),    # normal: 3-9 new items → 60 min
    (1, 120),   # low: 1-2 new items → 120 min
    (0, 240),   # idle: 0 new items → 240 min
]

# Fast check thresholds: more aggressive (lighter job)
FAST_CHECK_THRESHOLDS: List[Tuple[int, int]] = [
    (3, 30),    # active
    (1, 60),    # normal
    (0, 120),   # idle
]

# Number of recent runs to average
AVERAGE_WINDOW = 3

# Night hours (Beijing time): double the interval
NIGHT_START = 22  # 22:00
NIGHT_END = 8     # 08:00

# Log file paths
RUN_LOG_FILE = "run_log.jsonl"
FAST_LOG_FILE = "fast_log.jsonl"

# State file: records last actual run timestamps
_SCHEDULER_STATE_FILE = "scheduler_state.json"


# ============================================================
# Core logic
# ============================================================

def _load_log_entries(log_file: str, count: int = AVERAGE_WINDOW) -> List[Dict[str, Any]]:
    """Load the most recent N entries from a JSONL log file."""
    entries: List[Dict[str, Any]] = []
    if not os.path.exists(log_file):
        return entries
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass
    return entries[-count:]


def _calc_avg_new_items(entries: List[Dict[str, Any]]) -> float:
    """Calculate average new_items from recent log entries."""
    if not entries:
        return 0.0
    total = sum(e.get('new_items', 0) for e in entries)
    return total / len(entries)


def _is_night(now: Optional[datetime] = None) -> bool:
    """Check if current Beijing time is in night hours."""
    if now is None:
        now = get_beijing_time()
    hour = now.hour
    return hour >= NIGHT_START or hour < NIGHT_END


def _get_min_interval(avg_new: float, thresholds: List[Tuple[int, int]],
                      is_night: bool = False) -> int:
    """Get minimum interval in minutes based on activity level."""
    interval = thresholds[-1][1]  # default: lowest activity
    for min_items, min_interval in thresholds:
        if avg_new >= min_items:
            interval = min_interval
            break
    if is_night:
        interval *= 2
    return interval


def _load_state() -> Dict[str, Any]:
    """Load scheduler state (last run timestamps)."""
    if not os.path.exists(_SCHEDULER_STATE_FILE):
        return {}
    try:
        with open(_SCHEDULER_STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


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


def _parse_time(time_str: str) -> Optional[datetime]:
    """Parse a time string from run log into a datetime."""
    if not time_str:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S%z'):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    return None


def _minutes_since_last_run(state: Dict[str, Any], key: str) -> Optional[int]:
    """Calculate minutes since last run for a given key ('crawl' or 'fast_check')."""
    last_str = state.get(key, {}).get('last_run')
    if not last_str:
        return None
    last_dt = _parse_time(last_str)
    if last_dt is None:
        return None
    # Make both timezone-aware in Beijing
    now = get_beijing_time()
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=_BJ_TZ)
    delta = now - last_dt
    return int(delta.total_seconds() / 60)


def should_run(mode: str = 'crawl') -> Tuple[bool, str]:
    """
    Decide whether to run the crawl or fast_check now.

    Args:
        mode: 'crawl' or 'fast_check'

    Returns:
        (should_run: bool, reason: str)
    """
    now = get_beijing_time()
    is_night = _is_night(now)
    state = _load_state()

    # Choose thresholds
    if mode == 'fast_check':
        thresholds = FAST_CHECK_THRESHOLDS
        log_file = FAST_LOG_FILE
        state_key = 'fast_check'
    else:
        thresholds = CRAWL_THRESHOLDS
        log_file = RUN_LOG_FILE
        state_key = 'crawl'

    # Load recent log entries and calculate activity
    entries = _load_log_entries(log_file)
    avg_new = _calc_avg_new_items(entries)

    # Get minimum interval based on activity
    min_interval = _get_min_interval(avg_new, thresholds, is_night)

    # Check time since last run
    minutes_since = _minutes_since_last_run(state, state_key)

    # No previous run → always run
    if minutes_since is None:
        reason = (f"首次运行或无历史记录，立即执行 "
                  f"(avg_new={avg_new:.1f}, min_interval={min_interval}min, "
                  f"{'夜间' if is_night else '白天'})")
        logger.info("[智能调度] %s: %s", mode, reason)
        return True, reason

    # Enough time has passed → run
    if minutes_since >= min_interval:
        reason = (f"距上次运行 {minutes_since}min >= 间隔 {min_interval}min，执行 "
                  f"(avg_new={avg_new:.1f}, {'夜间' if is_night else '白天'})")
        logger.info("[智能调度] %s: %s", mode, reason)
        return True, reason

    # Not enough time → skip
    reason = (f"距上次运行 {minutes_since}min < 间隔 {min_interval}min，跳过 "
              f"(avg_new={avg_new:.1f}, {'夜间' if is_night else '白天'})")
    logger.info("[智能调度] %s: %s", mode, reason)
    return False, reason


def record_run(mode: str = 'crawl') -> None:
    """Record that a run has been executed. Call after a successful run."""
    now = get_beijing_time()
    state = _load_state()
    if mode not in state:
        state[mode] = {}
    state[mode]['last_run'] = now.strftime('%Y-%m-%d %H:%M:%S')
    _save_state(state)
    logger.info("[智能调度] %s: 记录运行时间 %s", mode,
                now.strftime('%Y-%m-%d %H:%M:%S'))
