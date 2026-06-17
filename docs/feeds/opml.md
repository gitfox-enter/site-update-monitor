# OPML 批量订阅

OPML 是 RSS 订阅源的批量导出格式，可以一次把多个订阅源导入任何 RSS 阅读器。

## RSSForge 提供的 OPML 文件

部署后，RSSForge 自动生成以下 OPML 文件：

| 文件 | 内容 |
|------|------|
| `opml.xml` | 全部站点 |
| `opml-线报站.xml` | 线报类站点 |
| `opml-购物比价.xml` | 比价类站点 |
| `opml-软件站.xml` | 软件类站点 |
| `opml-论坛.xml` | 论坛类站点 |
| `opml-其他.xml` | 未分类的站点 |

OPML 文件地址：

```
https://用户名.github.io/RSSForge/opml.xml
https://用户名.github.io/RSSForge/opml-线报站.xml
```

## 如何导入 OPML

### Reeder（Mac/iOS）

1. 打开 Reeder → **File** → **Add Feed...**
2. 选择 **Add OPML File...**
3. 粘贴 OPML 文件地址或本地文件
4. 点击 **Import**

### Inoreader

1. 登录 inoreader.com
2. 左侧菜单 → **订阅** → 右上角 **管理订阅**
3. **导入 OPML** → 选择文件或粘贴地址

### NetNewsWire（Mac/iOS）

1. **File** → **Import Subscriptions...**
2. 选择 OPML 文件

### Miniflux（自建）

```bash
curl -X POST https://your-miniflux.io/feed/import \
  -H "Content-Type: application/xml" \
  -u "user:api-key" \
  --data-binary @opml.xml
```

## OPML 文件格式

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>RSSForge 订阅源</title>
    <dateCreated>Wed, 17 Jun 2026 12:00:00 +0800</dateCreated>
  </head>
  <body>
    <outline text="线报酷"
             title="线报酷"
             type="atom"
             xmlUrl="https://用户名.github.io/RSSForge/feeds/线报酷.xml"
             htmlUrl="https://news.ixbk.fun/"/>
    <outline text="423down"
             title="423down"
             type="atom"
             xmlUrl="https://用户名.github.io/RSSForge/feeds/423down.xml"
             htmlUrl="https://www.423down.com/"/>
  </body>
</opml>
```

## 按分类订阅

如果你只想订阅某一类站点，导入对应的分类 OPML 文件即可：

```
opml-线报站.xml   → 只订阅线报类
opml-软件站.xml   → 只订阅软件类
```

## 定期更新 OPML

每次 GitHub Actions 运行后，OPML 文件会自动更新，反映最新的站点列表。

> 建议定期重新导入 OPML，确保阅读器中的链接是最新的。
