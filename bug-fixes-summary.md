# Bug Fixes Summary — 2026-06-22

## 修复范围
所有 bug 涉及 9 个文件，共修复 14 个问题。

---

## crawler/network.py

### Bug #86 — SSL 证书验证全局禁用
- **问题**: engine.py 中 aiohttp SSL 上下文设置了 `verify_mode = CERT_NONE`，存在 MITM 风险
- **修复**: SSL 验证改为默认严格模式（`ssl_ctx = create_default_context()`），仅在设置了 `DISABLE_SSL_VERIFY` 环境变量时才降级

### Bug #120 — robots.txt 和 ETag 缓存永不过期
- **问题**: `_robots_cache` 和 `_conditional_cache` 是无 TTL 的进程内字典，永不过期
- **修复**:
  - `_robots_cache` 条目改为 `{'rp': RobotFileParser, 'cached_at': float}`，TTL=5分钟
  - `_conditional_cache` 条目改为包含 `cached_at` 时间戳，TTL=1小时
  - `get_conditional_headers()` 和 `is_allowed_by_robots()` 在读取前检查 TTL，过期则删除并重新获取
  - 添加 `_cleanup_expired_cache()` 辅助函数；`_conditional_cache` 超过 1000 条时自动清理

---

## crawler/engine.py

### Bug #84 — SSRF 检查在 resp.read() 之后才执行
- **问题**: SSRF 检查发生在 `session.get()` 之后、响应体读取之后，实际已在请求发出后
- **修复**: 在 `fetch_page_content_async` 和 `_fetch_paginated` 的 `session.get()` 调用**之前**，通过 `socket.getaddrinfo()` 预解析域名，检查所有解析到的 IP 是否为 private/loopback/link-local/reserved，是则提前返回错误；metadata 主机名（localhost、169.254.169.254 等）也直接阻断

### Bug #86 续 — engine.py SSL 上下文（见 network.py）

---

## common.py

### Bug #85 — DomainRateLimiter.async_wait() 竞态条件
- **问题**: 原实现在锁外 `asyncio.sleep()`，锁内读取时间戳后，sleep 期间另一协程可同时通过检查突破限流
- **修复**: 改用 `async with self._lock:` 包裹整个 wait+reserve 逻辑，sleep 在锁内更新 `last_request` 后才释放锁，确保多协程无法同时突破

### Bug #118 — is_blacklisted() 未解码 URL 编码
- **问题**: 直接用原始 URL 比对黑名单，`%2F` 等编码可绕过
- **修复**: 在 `urlparse()` 前添加 `unquote(url)`，将 `%XX` 解码后再比对主机名

### Bug #122 — is_junk() 使用 `==` 而非 `in`
- **问题**: `if clean == jp` 要求标题**完全等于**某个 pattern 才触发，误判率高
- **修复**: 改为 `if junk_word in clean` 逐词子串匹配；同时 `JUNK_PATTERNS` 改为 `frozenset`（O(1) 迭代）

### Bug #121 — JUNK_PATTERNS 包含常见软件分类词
- **问题**: "安卓软件"、"办公软件"、"安全软件" 等软件分类词在正常文章标题中常见，被误判为垃圾
- **修复**: 删除上述软件分类词；保留真正的 UI 元素词（"查看详情"、"直达链接"、"继续阅读"、"更多"、"首页"、"登录"、"注册"、"搜索"、"javascript:"）和页脚词（"关于我们"、"联系我们"、"免责声明"、"版权声明"、"友情链接"）

---

## crawler/storage.py

### Bug #87 — merge_items_into_db O(n²) 性能
- **问题**: 模糊去重命中时，用 `for existing in db['items']` 线性搜索 URL，时间复杂度 O(n²)
- **修复**: 新增 `existing_url_to_item: Dict[str, int]` 索引（url → 列表下标），将多源聚合的线性搜索改为 O(1) 字典查找，整体 O(n²) → O(n)

### Bug #119 — _fuzzy_dedupe_key() 截断到 20 字符（字节）
- **问题**: Python 字符串按字符索引，但切片 `[:20]` 对字节串截断；中文字符 UTF-8 占 3 字节，"京东红包活动"（7字）在 GBK 环境只有 3 字符，易产生哈希碰撞
- **修复**: 改为 `normalized[:50]`（按字符数），50 字符足够覆盖各种语言的长标题，同时保持 key 简短

---

## requirements.txt

### Bug #90 — requests>=2.31.0 受 CVE-2024-35195 影响
- **修复**: `requests>=2.31.0` → `requests>=2.32.0`

---

## alerter.py

### Bug #93 — check_existing_issue() 永远返回 True（实际行为：异常时返回 False）
- **问题**: `subprocess.run` 异常时返回 False，导致在 CI 环境中即使已有 Issue 也不检测，重复创建
- **修复**: 显式解析 `stdout` 行数（忽略空行），只有当实际有输出行时才返回 True；异常仍返回 False（安全默认值）

---

## crawler/config.py

### Bug #104 — update_adaptive_tier() 使用 datetime.now() 无时区
- **问题**: `entry['updated_at'] = datetime.now().strftime(...)` 与 UTC 时间戳比较逻辑不一致
- **修复**: 改为 `datetime.now(timezone.utc).strftime(...)`（显式 UTC）；文件顶部已导入 `timezone`）

---

## opml_generator.py

### Bug #99 — 使用已弃用的 datetime.utcnow()
- **修复**: `datetime.utcnow()` → `datetime.now(timezone.utc)`；添加 `timezone` 到 import

---

## crawler/smart_scheduler.py

### Bug #115 — _SCHEDULER_STATE_FILE 使用相对路径
- **问题**: `"scheduler_state.json"` 为相对路径，cwd 变化时操作错误文件
- **修复**: 改为 `os.path.join(_SCRIPT_DIR, "scheduler_state.json")`，其中 `_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`（rssforge/ 的父目录）
