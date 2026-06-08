#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速增量检查器 - 高频抓取 top 站点，追加新线报到 items.json
设计目标：30-60 秒内完成，适合 GitHub Actions 每 5 分钟运行一次

增强功能:
  - requests.Session 连接池与 Cookie 持久化
  - 指数退避重试 (3 次: 1s, 2s, 4s)
  - 结构化日志 (Python logging)
  - 完整类型标注
  - robots.txt 合规检查 (按域名缓存)
  - Referer 头 (使用站点首页)
  - 指标追踪 (请求数、成功/失败、平均响应时间)
  - 输入清洗 (javascript: 过滤、文本净化)
"""

# ============================================================
# 1. 所有导入集中在顶部
# ============================================================

import os
import re
import sys
import time
import json
import random
import hashlib
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# ============================================================
# 4. 结构化日志
# ============================================================

logger = logging.getLogger("fast_check")
logger.setLevel(logging.DEBUG)

_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.INFO)
_formatter = logging.Formatter(
    "[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
_handler.setFormatter(_formatter)
logger.addHandler(_handler)

# ============================================================
# 配置
# ============================================================

ITEMS_DB_FILE: str = "items.json"
FAST_LOG_FILE: str = "fast_log.jsonl"

# 高频检查站点（按活跃度排序的 top 12）
FAST_SITES: List[Dict[str, str]] = [
    {"url": "https://www.zhuanyes.com/xianbao/", "name": "专业线报"},
    {"url": "https://news.ixbk.net/", "name": "线报酷"},
    {"url": "https://news.ixbk.fun/", "name": "线报酷"},
    {"url": "http://www.0818tuan.com/", "name": "0818团"},
    {"url": "https://www.huifabu.cn/", "name": "汇发部"},
    {"url": "https://cjx8.com/", "name": "超级线报"},
    {"url": "https://xianbao.icu/", "name": "线报ICU"},
    {"url": "https://www.baicaio.com/", "name": "白菜哦"},
    {"url": "https://www.iqnew.com/", "name": "爱Q社区"},
    {"url": "https://www.51kanong.com/", "name": "51卡农"},
    {"url": "https://v1.xianbao.net/", "name": "线报网"},
    {"url": "http://www.xiaodigu.com/", "name": "小嘀咕"},
]

# 爬虫配置
REQUEST_TIMEOUT: int = 10
MAX_RETRIES: int = 3                       # 3 次尝试
RETRY_BACKOFF_BASE: float = 1.0            # 退避基数 (秒): 1, 2, 4
RESPECT_ROBOTS_TXT: bool = False            # 6. robots.txt 合规开关（线报站 robots.txt 通常过严，个人监控建议关闭）

USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

# 关键词自动分类
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "京东": ["京东", "jd.com", "jd", "京豆", "京享"],
    "淘宝": ["淘宝", "天猫", "tmall", "taobao", "淘金币"],
    "拼多多": ["拼多多", "pdd", "拼多"],
    "外卖": ["外卖", "美团", "饿了么", "美团外卖"],
    "红包": ["红包", "虹包", "鸿包", "必中红包"],
    "优惠券": ["优惠券", "券", "满减", "消费券", "领券"],
}

# 过滤词
JUNK_PATTERNS: List[str] = [
    "安卓软件", "办公软件", "安全软件", "查看详情", "直达链接", "阅读全文",
    "继续阅读", "更多", "首页", "登录", "注册", "搜索", "javascript:",
    "关于我们", "联系我们", "免责声明", "版权声明", "友情链接",
]

# ============================================================
# 2. requests.Session 连接池
# ============================================================

_session: requests.Session = requests.Session()
# 设置连接池大小以匹配并发 worker 数
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=4,
    pool_maxsize=4,
    max_retries=0,  # 我们自己控制重试
)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)

# ============================================================
# 6. robots.txt 缓存 (按域名)
# ============================================================

_robots_cache: Dict[str, RobotFileParser] = {}
_robots_fetch_failures: Set[str] = set()  # 获取失败的域名，默认允许


def _get_robots_parser(scheme_host: str) -> RobotFileParser:
    """获取或创建指定域名的 robots.txt 解析器（带缓存）"""
    if scheme_host in _robots_cache:
        return _robots_cache[scheme_host]

    rp = RobotFileParser()
    robots_url = f"{scheme_host}/robots.txt"
    try:
        # 使用 Session 抓取 robots.txt，享受超时与连接池
        resp = _session.get(robots_url, timeout=5, headers={"User-Agent": "*"})
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        elif resp.status_code in (401, 403):
            # 401/403 表示全部禁止
            rp.parse(["User-agent: *", "Disallow: /"])
        else:
            # 404 等表示无限制
            _robots_fetch_failures.add(scheme_host)
    except Exception:
        _robots_fetch_failures.add(scheme_host)
        # 获取失败时默认允许抓取（宽容策略）
    _robots_cache[scheme_host] = rp
    return rp


def is_allowed_by_robots(url: str, user_agent: str = "*") -> bool:
    """检查 URL 是否被 robots.txt 允许抓取"""
    if not RESPECT_ROBOTS_TXT:
        return True

    parsed = urlparse(url)
    scheme_host = f"{parsed.scheme}://{parsed.netloc}"

    if scheme_host in _robots_fetch_failures:
        return True  # robots.txt 不可达时默认允许

    rp = _get_robots_parser(scheme_host)
    return rp.can_fetch(user_agent, url)


# ============================================================
# 8. 指标追踪
# ============================================================

class Metrics:
    """简单的运行时指标收集器"""

    def __init__(self) -> None:
        self.request_count: int = 0
        self.success_count: int = 0
        self.fail_count: int = 0
        self._total_response_time: float = 0.0
        self._lock: Any = None  # 单线程写入，无需锁

    def record_success(self, elapsed: float) -> None:
        self.request_count += 1
        self.success_count += 1
        self._total_response_time += elapsed

    def record_failure(self, elapsed: float = 0.0) -> None:
        self.request_count += 1
        self.fail_count += 1
        self._total_response_time += elapsed

    @property
    def avg_response_time(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self._total_response_time / self.request_count

    def summary(self) -> str:
        return (
            f"请求 {self.request_count} | "
            f"成功 {self.success_count} | "
            f"失败 {self.fail_count} | "
            f"平均响应 {self.avg_response_time:.2f}s"
        )


metrics = Metrics()


# ============================================================
# 9. 输入清洗
# ============================================================

# 预编译正则：匹配 javascript: 协议（忽略大小写与前置空白）
_JS_HREF_RE = re.compile(r"^\s*javascript\s*:", re.IGNORECASE)
# 控制字符与零宽字符
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200c\u200d\ufeff]")


def sanitize_href(href: str) -> str:
    """清洗 href：去除前后空白、过滤 javascript: 等危险协议"""
    href = href.strip()
    if _JS_HREF_RE.match(href):
        return ""
    return href


def sanitize_text(text: str) -> str:
    """清洗文本：去除控制字符、零宽字符，合并连续空白"""
    text = _CONTROL_CHAR_RE.sub("", text)
    text = " ".join(text.split())
    return text


# ============================================================
# 工具函数
# ============================================================

def get_beijing_time() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def auto_categorize(text: str) -> Optional[str]:
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return None


def is_junk(text: str) -> bool:
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

def load_items_db() -> Dict[str, Any]:
    if os.path.exists(ITEMS_DB_FILE):
        try:
            with open(ITEMS_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "items" in data:
                return data
        except Exception:
            pass
    return {"items": [], "updated_at": ""}


def save_items_db(db: Dict[str, Any]) -> bool:
    try:
        with open(ITEMS_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
        return True
    except Exception as e:
        logger.error("保存失败: %s", e)
        return False


# ============================================================
# 3. 指数退避重试 + 2. Session 连接池 + 7. Referer 头
# ============================================================

def _fetch_with_retry(
    url: str,
    headers: Dict[str, str],
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> Optional[requests.Response]:
    """
    使用指数退避策略发送 GET 请求。
    尝试次数: max_retries (默认 3)，退避间隔: 1s, 2s, 4s
    成功返回 Response，全部失败返回 None。
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        start = time.monotonic()
        try:
            resp = _session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            elapsed = time.monotonic() - start
            if resp.status_code == 200:
                metrics.record_success(elapsed)
                return resp
            # 非 200 但属于服务器错误时可重试
            if resp.status_code >= 500 and attempt < max_retries - 1:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(
                    "  %s 返回 HTTP %d，%.1fs 后重试 (%d/%d)",
                    url, resp.status_code, backoff, attempt + 1, max_retries,
                )
                metrics.record_failure(elapsed)
                time.sleep(backoff)
                continue
            # 客户端错误 (4xx) 不重试
            metrics.record_failure(elapsed)
            return resp
        except requests.exceptions.Timeout as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            metrics.record_failure(elapsed)
            if attempt < max_retries - 1:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(
                    "  %s 超时，%.1fs 后重试 (%d/%d)",
                    url, backoff, attempt + 1, max_retries,
                )
                time.sleep(backoff)
        except requests.exceptions.RequestException as exc:
            elapsed = time.monotonic() - start
            last_exc = exc
            metrics.record_failure(elapsed)
            if attempt < max_retries - 1:
                backoff = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(
                    "  %s 请求异常 (%s)，%.1fs 后重试 (%d/%d)",
                    url, type(exc).__name__, backoff, attempt + 1, max_retries,
                )
                time.sleep(backoff)

    # 全部重试耗尽
    logger.warning("  %s 全部 %d 次重试失败: %s", url, max_retries, last_exc)
    return None


# ============================================================
# 抓取 & 解析
# ============================================================

def fetch_and_extract(
    site: Dict[str, str],
) -> Tuple[str, str, List[Dict[str, Any]], Optional[str]]:
    """抓取单个站点并提取线报条目"""
    url: str = site["url"]
    name: str = site["name"]
    ua: str = random.choice(USER_AGENTS)

    # 6. robots.txt 合规检查
    if not is_allowed_by_robots(url, ua):
        logger.info("  [robots.txt 拒绝] %s: %s", name, url)
        return name, url, [], "robots.txt 拒绝"

    # 7. Referer 头 - 使用站点首页作为 Referer
    parsed_url = urlparse(url)
    referer: str = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    headers: Dict[str, str] = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer": referer,
    }

    # 3. 带指数退避的请求
    resp = _fetch_with_retry(url, headers)

    if resp is None:
        return name, url, [], f"重试 {MAX_RETRIES} 次后仍失败"

    try:
        resp.encoding = resp.apparent_encoding or "utf-8"
        if resp.status_code != 200:
            return name, url, [], f"HTTP {resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")

        # 移除干扰元素
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()

        body = soup.find("body")
        if not body:
            return name, url, [], "无 body"

        items: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        # 提取 <a> 标签中的链接条目
        for a_tag in body.find_all("a", href=True):
            raw_text: str = a_tag.get_text(strip=True)
            if not raw_text:
                continue

            # 9. 文本清洗
            text = sanitize_text(raw_text)
            if not text or is_junk(text):
                continue
            if len(text) > 120:
                continue
            if text in seen:
                continue

            # 9. href 清洗（javascript: 等）
            raw_href: str = a_tag["href"].strip()
            href = sanitize_href(raw_href)
            if not href:
                continue
            if href.startswith("#"):
                continue
            if href.startswith("/") or not href.startswith("http"):
                href = urljoin(url, href)

            seen.add(text)
            items.append({
                "url": href,
                "text": text,
                "source": name,
                "time": get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"),
                "category": auto_categorize(text),
            })

        return name, url, items, None

    except Exception as e:
        return name, url, [], str(e)[:80]


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    logger.info("=" * 50)
    logger.info("[快速检查] 开始 %s", get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 50)

    # 1. 先 git pull 获取最新数据
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "--strategy-option=theirs", "origin", "main"],
            capture_output=True,
            timeout=30,
        )
        logger.info("[Git] 已拉取最新数据")
    except Exception as e:
        logger.warning("[Git] 拉取失败（继续）: %s", e)

    # 2. 加载现有数据库
    db = load_items_db()
    existing_urls: Set[str] = set(item["url"] for item in db["items"])
    logger.info("[数据] 现有 %d 条线报", len(db["items"]))

    # 3. 并发抓取 top 站点
    all_new_items: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_and_extract, site): site for site in FAST_SITES}
        for future in as_completed(futures):
            site = futures[future]
            name, url, items, error = future.result()
            if error:
                logger.warning("  [失败] %s: %s", name, error)
                continue

            # 过滤已存在的
            fresh = [it for it in items if it["url"] not in existing_urls]
            if fresh:
                logger.info(
                    "  [新增] %s: %d 条新线报 (共提取 %d 条)",
                    name, len(fresh), len(items),
                )
                all_new_items.extend(fresh)
                for it in fresh:
                    existing_urls.add(it["url"])
            else:
                logger.info(
                    "  [正常] %s: 无新内容 (提取 %d 条)",
                    name, len(items),
                )

    # 4. 合并到数据库
    if all_new_items:
        db["items"] = all_new_items + db["items"]
        db["updated_at"] = get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")
        save_items_db(db)
        logger.info("[结果] 新增 %d 条，总计 %d 条", len(all_new_items), len(db["items"]))
    else:
        logger.info("[结果] 无新增，保持 %d 条", len(db["items"]))

    # 8. 输出指标摘要
    logger.info("[指标] %s", metrics.summary())

    # 5. 记录运行日志
    log_entry: Dict[str, Any] = {
        "time": get_beijing_time().strftime("%Y-%m-%d %H:%M:%S"),
        "new_items": len(all_new_items),
        "total": len(db["items"]),
        "sites_checked": len(FAST_SITES),
        "metrics": {
            "requests": metrics.request_count,
            "success": metrics.success_count,
            "fail": metrics.fail_count,
            "avg_response_time": round(metrics.avg_response_time, 3),
        },
    }
    try:
        with open(FAST_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # 6. Git 提交
    if all_new_items:
        try:
            subprocess.run(
                ["git", "add", ITEMS_DB_FILE, FAST_LOG_FILE],
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(
                [
                    "git", "commit", "-m",
                    f"快速更新: 新增 {len(all_new_items)} 条线报 "
                    f"({get_beijing_time().strftime('%H:%M')})",
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                # 推送（带重试）
                for attempt in range(3):
                    push_result = subprocess.run(
                        ["git", "push", "origin", "main"],
                        capture_output=True,
                        timeout=30,
                    )
                    if push_result.returncode == 0:
                        logger.info("[Git] 已推送")
                        break
                    time.sleep(3)
                    subprocess.run(
                        ["git", "pull", "--rebase", "--strategy-option=theirs", "origin", "main"],
                        capture_output=True,
                        timeout=30,
                    )
                else:
                    logger.warning("[Git] 推送失败")
            else:
                logger.info("[Git] 无变更需要提交")
        except Exception as e:
            logger.error("[Git] 提交失败: %s", e)

    logger.info("=" * 50)


if __name__ == "__main__":
    main()
