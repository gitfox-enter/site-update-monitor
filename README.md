# Site Update Monitor

基于 GitHub Actions 的多站点线报聚合服务。定时爬取 42 个网站，生成 RSS/OPML 订阅源，部署到 GitHub Pages。

👉 [在线页面](https://gitfox-enter.github.io/site-update-monitor/)

## 核心功能

- **42 站点监控** — 线报、购物比价、软件资源、社区论坛，支持 HTML/RSS/JS 渲染多种抓取方式
- **每站独立抓取间隔** — 线报站 15 分钟、购物站 60 分钟、软件站 4-8 小时，夜间自动降频
- **RSS 订阅** — 全量聚合 feed + 每站独立 feed，支持 OPML 一键导入
- **智能调度** — 根据站点更新频率自动决定本轮是否抓取，避免无效请求
- **自适应分级** — 根据抓取成功率自动升降站点优先级，连续失败自动降为 dead
- **数据去重** — MD5 指纹 + URL 去重 + 模糊标题合并（多源聚合），7 天滚动保留

## 数据流

```
sites.yaml (站点配置)
      │
      ▼
crawl.yml (每30分钟)       fast_check.yml (每30分钟, 偏移15分)
      │                            │
      ▼                            ▼
smart_scheduler 过滤         仅爬 fast_check 站点
(每站独立判断间隔是否到期)
      │                            │
      └────────┬───────────────────┘
               ▼
         items.json (数据存储)
               │
      ┌────────┼────────┐
      ▼        ▼        ▼
  rss_feed.py      opml_generator.py
      │             │
      ├─ feed.xml   ├─ opml.xml (全量)
      ├─ feeds/*.xml└─ opml-{分类}.xml
      └─ feeds_meta.json
               │
               ▼
         pages.yml → GitHub Pages
               │
               ▼
         index.html (读 feed.xml 展示)
```

## 抓取调度

两个工作流交替运行，合计每 **15 分钟**检查一次：

| 工作流 | Cron | 说明 |
|--------|------|------|
| crawl.yml | `0,30 * * * *` | 全量爬取，受智能调度过滤 |
| fast_check.yml | `15,45 * * * *` | 快速增量，仅爬 `fast_check: true` 站点 |

**智能调度规则**：每个站点根据 `sites.yaml` 中的 `interval` 字段独立判断是否需要抓取。距离上次抓取不足间隔则跳过。22:00-08:00（北京时间）间隔自动翻倍。

## 站点配置 (sites.yaml)

所有站点配置的单一真相源，其他模块均从此文件加载：

```yaml
sites:
  - url: "https://example.com/"    # 站点 URL（必填）
    name: 示例站                     # 显示名称（必填）
    tier: high                      # 优先级: high | medium | low
    interval: 15                    # 最小抓取间隔（分钟，必填）
    fast_check: true                # 是否参与快速检查（可选）
    js_render: true                 # 是否需要 Playwright 渲染（可选）
    rss_feed: "https://..."         # 直接用 RSS 源代替 HTML 抓取（可选）
    parser: ghxi                    # 强制指定解析器（可选）

dead_sites:
  "https://dead-site.com/":
    reason: "域名已过期"
    confirmed_at: "2026-01-01"
    test_result: "Connection refused"
```

**interval 参考值**：

| 站点类型 | interval | 示例 |
|----------|----------|------|
| 活跃线报站 | 15-20 min | 线报酷、线报ICU、专业线报 |
| 普通线报站 | 30 min | 羊毛王、羊毛党、H6线报 |
| 购物比价 | 60-120 min | 白菜哦、慢慢买 |
| 社区论坛 | 60-120 min | 豆瓣小组、开心赚 |
| 软件资源 | 240-480 min | 果核剥壳、423Down、小众软件 |

## RSS 订阅输出

| 文件 | 说明 |
|------|------|
| `feed.xml` | 全量聚合 feed（所有来源） |
| `feeds/{站名}.xml` | 每站独立 feed（无条数上限） |
| `feeds_meta.json` | 每站更新频率元数据（前端展示用） |
| `opml.xml` | 全量 OPML（按分类组织） |
| `opml-{分类}.xml` | 按分类的 OPML（线报站/购物比价/软件站/论坛/其他） |

每个 feed 包含 `sy:updatePeriod` 和 `sy:updateFrequency` 元素，RSS 阅读器可据此判断更新频率。

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
      └── rss_parsers.py   RSS/Atom 解析器

feeds/                     生成的每站独立 feed
.github/workflows/
  ├── crawl.yml            全量爬取（每30分钟）
  ├── fast_check.yml       快速检查（每30分钟，偏移15分）
  ├── pages.yml            GitHub Pages 部署
  └── daily_summary.yml    每日摘要
```

## 添加新站点

1. 在 `sites.yaml` 中添加站点，设置 `url`/`name`/`tier`/`interval`
2. 如果是标准 HTML 站点，无需额外操作（通用解析器自动处理）
3. 如果站点有特殊结构，在 `crawler/parsers/` 对应模块中添加解析函数并注册到 `PARSER_REGISTRY`
4. 如果是 SPA 站点（Angular/React 等），设置 `js_render: true`
5. 如果站点提供 RSS 源，设置 `rss_feed` 字段（跳过 HTML 抓取，直接用 RSS）
6. 本地运行 `python crawl.py` 验证

## 自适应分级

站点优先级会根据抓取结果自动调整：

- 连续成功 ≥ 2 次 → 提升 1 级
- 连续失败 ≥ 2 次 → 降低 1 级
- low 级别连续失败 → 标记为 dead（不再爬取）
- dead 站点恢复 → 回到 low 级别

## License

MIT
