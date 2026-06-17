# RSSForge · 万物皆可 RSS

> ⚡ 基于 GitHub Actions 的免费 RSS 订阅源生成器，无服务器、零成本、可持续更新

👉 [在线演示](https://gitfox-enter.github.io/site-update-monitor/) · 📡 [订阅全量 Feed](https://gitfox-enter.github.io/site-update-monitor/feed.xml)

---

## 什么是 RSSForge？

RSSForge 是一个运行在 GitHub Actions 上的 RSS 订阅源生成器。你无需租用服务器，只需 Fork 项目并启用 GitHub Pages，Actions 就会自动定时抓取网站，生成 RSS/OPML 订阅源。

它受到 [RSSHub](https://github.com/DIYgod/RSSHub) 和 [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge) 的启发，但核心区别是：**完全免费，无需自建**。

---

## 核心功能

- 🔧 **零服务器成本** — 直接利用 GitHub Actions 免费计算资源，24 小时自动运行
- 📡 **RSS / OPML 输出** — 全量聚合 feed + 每站独立 feed + 按分类 OPML 包
- ⚡ **每站独立调度** — 线报站 15 分钟、软件站 4-8 小时，智能降频
- 🔄 **自动去重** — MD5 指纹 + URL 去重 + 模糊标题合并，7 天滚动保留
- 📦 **一键 Fork 自用** — 任何人 Fork 后稍作配置就能拥有自己的订阅服务
- 🤝 **社区贡献** — 提交 PR 即可贡献抓取规则，所有 fork 用户自动受益

---

## 数据流

```
sites.yaml (站点配置)
        │
        ▼
  GitHub Actions 定时触发
        │
  smart_scheduler (每站独立判断是否到期)
        │
        ▼
  crawler (Playwright / aiohttp / RSS)
        │
        ▼
  items.json (数据存储)
        │
   ┌────┴────┐
   ▼         ▼
rss_feed.py   opml_generator.py
   │         │
   ├─ feed.xml      ├─ opml.xml (全量)
   ├─ feeds/*.xml    └─ opml-{分类}.xml
   └─ feeds_meta.json
        │
        ▼
  GitHub Pages → 你的网站
```

---

## 快速开始

### Step 1：Fork 本项目

点击页面右上角 **Fork** 按钮，复制到你的 GitHub 账号。

### Step 2：启用 GitHub Pages

1. 进入你的仓库 → **Settings** → **Pages**
2. **Source** 选择 **Deploy from a branch**
3. Branch 选择 `gh-pages`（没有则新建一个空的）
4. Save，等待几分钟域名生效

### Step 3：修改 sites.yaml

编辑 `sites.yaml`，添加你想监控的网站：

```yaml
sites:
  - url: "https://example.com/"
    name: 示例站点
    tier: high          # high | medium | low
    interval: 15        # 最小抓取间隔（分钟）
    fast_check: true    # 是否参与快速增量检查
    js_render: true     # 是否需要 JS 渲染（SPA 站点）
```

### Step 4：订阅 RSS

在 RSS 阅读器中添加订阅：

- **全量聚合**：`https://[你的用户名].github.io/site-update-monitor/feed.xml`
- **OPML 导入**：下载 `opml.xml`，导入 Reeder / FeedMe / inoreader 等阅读器

---

## interval 参考值

| 站点类型 | interval | 示例 |
|----------|----------|------|
| 活跃线报站 | 15-20 min | 线报酷、线报ICU、专业线报 |
| 普通线报站 | 30 min | 羊毛王、羊毛党、H6线报 |
| 购物比价 | 60-120 min | 白菜哦、慢慢买 |
| 社区论坛 | 60-120 min | 豆瓣小组、开心赚 |
| 软件资源 | 240-480 min | 果核剥壳、423Down、小众软件 |

---

## 项目结构

```
sites.yaml                 站点配置（单一真相源）
crawl.py                   全量爬取入口
fast_check.py              快速增量检查入口
rss_feed.py                Atom feed 生成器
opml_generator.py          OPML 生成器
common.py                  公共工具（日志/去重/分类/代理池）
alerter.py                 告警（连续失败/dead tier）
index.html                 前端页面（单文件 SPA）
status.html                健康监控面板
blacklist.json             域名黑名单

crawler/
  ├── config.py            配置加载（从 sites.yaml）
  ├── engine.py            爬取引擎（Playwright/aiohttp/解析调度）
  ├── smart_scheduler.py   每站独立调度器
  ├── network.py           网络层（限速/ETag/robots.txt）
  ├── storage.py           数据持久化（去重/合并/导出）
  └── parsers/             解析器（插件式注册）
      ├── core.py          PARSER_REGISTRY + 通用解析
      ├── deal_sites.py    线报/优惠站点解析器
      ├── software_sites.py 软件站点解析器
      ├── forum_sites.py   论坛站点解析器
      └── rss_parsers.py  RSS/Atom 解析器

feeds/                     生成的每站独立 feed
.github/workflows/
  ├── crawl.yml            全量爬取（每30分钟）
  ├── fast_check.yml       快速检查（每30分钟，偏移15分）
  ├── pages.yml            GitHub Pages 部署
  └── daily_summary.yml    每日摘要
```

---

## 添加新站点

1. 在 `sites.yaml` 中添加站点，设置 `url`/`name`/`tier`/`interval`
2. 标准 HTML 站点无需额外操作（通用解析器自动处理）
3. 有特殊结构的站点，在 `crawler/parsers/` 中添加解析函数并注册
4. SPA 站点设置 `js_render: true`
5. 有 RSS 源的站点，设置 `rss_feed` 字段跳过 HTML 抓取
6. 本地运行 `python crawl.py` 验证

---

## 自适应分级

- 连续成功 ≥ 2 次 → 提升 1 级
- 连续失败 ≥ 2 次 → 降低 1 级
- low 级别连续失败 → 标记为 dead（停止爬取）
- dead 站点恢复 → 回到 low 级别

---

## 贡献规则

发现了好站点？想支持这个项目？多种方式参与：

- ⭐ **Star** 本项目，让更多人看到
- 🔧 **提交 PR** 贡献新的站点抓取规则
- 🐛 **反馈问题** 站点失效 / 新功能建议
- 🧧 **支付宝领红包** 扫描页面底部的红包码，每天可领一次

---

## 灵感来源

- [RSSHub](https://github.com/DIYgod/RSSHub) — 万物皆 RSS
- [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge) — PHP 版 RSS 生成器
- [wewe-rss](https://github.com/cooderl/wewe-rss) — 微信公众号 RSS

---

## License

MIT
