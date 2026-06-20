# 订阅源概览

## Feed 文件结构

每个订阅的网站生成一个独立的 Atom Feed 文件：

```
feeds/
├── 线报酷.xml       # 线报酷的订阅源
├── 线报ICU.xml      # 线报ICU的订阅源
├── 423down.xml      # 423down的订阅源
└── ...
```

Feed 地址格式：

```
https://用户名.github.io/RSSForge/feeds/zhan-dian-ming.xml
```

## Feed 格式

RSSForge 使用 **Atom 1.0** 格式（比 RSS 2.0 更规范）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>线报酷 - RSSForge</title>
  <subtitle>线报酷的 RSS 订阅源</subtitle>
  <icon>https://www.google.com/s2/favicons?domain=news.ixbk.fun&sz=64</icon>
  <link href="https://..." rel="alternate"/>
  <link href="https://..." rel="self" type="application/atom+xml"/>
  <id>https://...</id>
  <updated>2026-06-17T12:00:00+08:00</updated>
  <generator uri="https://github.com/gitfox-enter/RSSForge">RSSForge</generator>
  <sy:updatePeriod xmlns:sy="http://purl.org/syndication/1.0">hourly</sy:updatePeriod>
  <sy:updateFrequency xmlns:sy="http://purl.org/syndication/1.0">4</sy:updateFrequency>

  <entry>
    <title>文章标题</title>
    <link href="https://原文链接"/>
    <id>https://原文链接</id>
    <updated>2026-06-17T10:30:00+08:00</updated>
    <content type="text">来源: 线报酷 | 分类: 优惠 | 链接: https://...</content>
    <media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="https://.../thumb.jpg" width="300"/>
  </entry>
</feed>
```

## Feed 元数据

`feeds_meta.json` 保存了每个 feed 的元信息，供前端展示：

```json
{
  "线报酷": {
    "interval": 15,
    "freq_label": "每15分钟",
    "count": 256,
    "feed_url": "https://...",
    "icon": "https://www.google.com/s2/favicons?domain=..."
  }
}
```

## 每个 Feed 包含的内容

| 字段 | 说明 |
|------|------|
| `<title>` | 条目标题（自动过滤垃圾内容） |
| `<link>` | 原文链接 |
| `<updated>` | 发布时间（北京时间） |
| `<content>` | 内容摘要 + 来源 + 分类 |
| `<media:thumbnail>` | 内容中的第一张图片 |
| `<category>` | 自动分类标签 |

## Feed 更新频率

`<sy:updatePeriod>` 和 `<sy:updateFrequency>` 会告知 RSS 阅读器建议的更新频率：

| interval | updatePeriod | updateFrequency |
|---------|--------------|-----------------|
| 15 分钟 | hourly | 4 次/小时 |
| 30 分钟 | hourly | 2 次/小时 |
| 1 小时 | hourly | 1 次/小时 |
| 4 小时 | daily | 6 次/天 |

> 注意：这是建议值，实际更新频率由 GitHub Actions 调度决定。
