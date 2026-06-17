# 更新频率调度

RSSForge 通过 `sites.yaml` 中的 `interval` 字段和 `tier` 等级来控制每个站点的抓取频率。

## interval — 抓取间隔

```yaml
sites:
  - url: "https://news.ixbk.fun/"
    name: 线报酷
    interval: 15    # 每 15 分钟检查一次
```

| interval 值 | 实际频率（白天） | 实际频率（夜间 23:00-07:00） |
|------------|----------------|---------------------------|
| `15` | ~15 分钟 | ~45 分钟 |
| `30` | ~30 分钟 | ~90 分钟 |
| `60` | ~1 小时 | ~3 小时 |
| `120` | ~2 小时 | ~6 小时 |
| `480` | ~8 小时 | ~24 小时 |

## tier — 优先级等级

`tier` 影响 Actions 工作流内部的调度顺序：

| tier | 建议场景 | 特点 |
|------|---------|------|
| `high` | 线报站、新闻、实时资讯 | 高频抓取，抢占优先 |
| `mid` | 软件站、论坛 | 中等频率 |
| `low` | 更新缓慢的网站 | 低频，节省资源 |

```yaml
sites:
  - url: "https://news.ixbk.fun/"
    name: 线报酷
    tier: high
    interval: 15

  - url: "https://www.423down.com/"
    name: 423down
    tier: mid
    interval: 60
```

## 夜间降频

为节省 GitHub Actions 的运行时长，夜间（23:00-07:00）自动降低抓取频率。实际间隔 = `interval × 3`。

## fast_check — 快速检查模式

开启后只检测页面是否变化，不解析内容，大幅节省资源：

```yaml
sites:
  - url: "https://status.example.com/"
    name: 服务状态页
    fast_check: true
```

适合场景：
- 不需要提取详细内容，只需知道「有没有更新」
- 页面数据量大但变化少
- 想减少 Actions 运行时间

## 智能调度（adaptive_tiers）

RSSForge 还支持根据站点的活跃度动态调整优先级：

- **最近 7 天有更新** → 自动提升 tier
- **连续 3 天无更新** → 自动降低 tier
- **连续 7 天无更新** → 标记为死站，暂停抓取

调整记录保存在 `adaptive_tiers.json`，可手动恢复。

## 实际更新频率

由于 GitHub Actions 有最小触发间隔（1 分钟），实际更新频率取决于：
1. Actions 工作流的 cron 配置
2. 站点的 `interval` 设置
3. 当前队列中的站点数量

建议的 `interval` 配置：
- **极重要站点**：`15` 分钟
- **重要站点**：`30-60` 分钟
- **一般站点**：`120-480` 分钟
