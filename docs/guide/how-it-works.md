# 运行机制

了解 RSSForge 内部是如何工作的。

## 整体架构

```
sites.yaml 配置文件
    ↓
GitHub Actions（定时触发）
    ↓
┌─────────────────────────────────────────┐
│  crawl.py → crawler/                    │
│  ├── engine.py     → 爬虫引擎            │
│  │   ├── aiohttp   → HTTP 请求           │
│  │   ├── playwright→ JS 渲染            │
│  │   └── rate limiter→ 频率限制         │
│  ├── parsers/      → 内容解析器         │
│  │   ├── core.py   → 通用解析           │
│  │   └── deal_sites.py → 40+ 专用解析器 │
│  ├── storage.py   → 数据存储（SQLite）  │
│  ├── config.py    → 配置加载           │
│  └── smart_scheduler.py → 智能调度      │
└─────────────────────────────────────────┘
    ↓
items.json（数据）
    ↓
rss_feed.py → Atom Feed XML
    ↓
feeds/zhan-dian-ming.xml
    ↓
GitHub Pages → 用户访问
```

## 定时触发

GitHub Actions 的 `.github/workflows/` 目录配置了定时任务：

- **high tier**（高优先级）：每 15-30 分钟
- **mid tier**（中优先级）：每 1-2 小时
- **low tier**（低优先级）：每 4-8 小时

白天和夜间有不同的触发频率，夜间降频节省资源。

## 爬虫引擎

### 普通 HTML 抓取

通过 aiohttp 发送 HTTP 请求，带上随机的 User-Agent 和请求头，模拟浏览器访问。

### JavaScript 渲染

某些网站内容由 JavaScript 动态生成（常见于 SPA）。RSSForge 内置 Playwright 支持，可以等页面完全渲染后再抓取内容。

### 解析器

每个网站结构不同，RSSForge 内置了 40+ 专用解析器（`deal_sites.py`），针对每个站点的 HTML 结构定制提取逻辑。如果找不到专用解析器，会使用通用解析器尝试提取。

## 数据存储

```
items.json
  └── items[]      所有抓取的内容（7天滚动清理）
  └── updated_at   最后更新时间

hash_record.txt    每站的页面哈希（用于检测是否有更新）
```

### 7 天滚动清理

为节省仓库空间，超过 7 天的旧条目会自动从 `items.json` 中清理。

> 保护机制：新添加的站点，首次入库的内容有 3 天保护期，不受 7 天窗口影响。

## RSS 生成

```
items.json
    ↓
rss_feed.py（按来源分组）
    ↓
feeds/zhan-dian-ming.xml   ← 每个网站一个独立 feed
feeds_meta.json     ← 前端展示用的元数据
```

每个 feed 使用 **Atom 1.0** 格式（比 RSS 2.0 更规范），包含：
- `<icon>`：站点 favicon（Google Favicon Proxy）
- `media:thumbnail`：每条内容的第一张图片
- `sy:updatePeriod`：更新频率提示

## OPML 生成

`opml_generator.py` 根据 `sites.yaml` 分类自动生成 OPML 文件：
- `opml.xml`：全部站点
- `opml-线报站.xml`：线报类站点
- `opml-软件站.xml`：软件类站点
- 等等

导入 OPML 到阅读器，一次订阅整个分类。
