# OPML 批量订阅

OPML 是 RSS 订阅源的批量导出格式，可以一次把多个订阅源导入任何 RSS 阅读器。

## 统一 OPML 文件

RSSForge 提供一个统一的 OPML 文件，包含所有订阅源：

```
https://gitfox-enter.github.io/RSSForge/opml.xml
```

> **提示**: Fork 项目后，将 `gitfox-enter` 替换为你的 GitHub 用户名。

## 如何导入 OPML

### Reeder（Mac/iOS）

1. 打开 Reeder → **File** → **Add Feed...**
2. 选择 **Add OPML File...**
3. 粘贴 OPML 文件地址：`https://gitfox-enter.github.io/RSSForge/opml.xml`
4. 点击 **Import**

### Inoreader

1. 登录 inoreader.com
2. 左侧菜单 → **订阅** → 右上角 **管理订阅**
3. **导入 OPML** → 粘贴 OPML 地址或本地文件

### NetNewsWire（Mac/iOS）

1. **File** → **Import Subscriptions...**
2. 选择 OPML 文件

### FeedMe（Android）

1. 长按底部 **+** 按钮
2. 选择 **导入 OPML**
3. 粘贴 OPML 地址

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
    <title>RSSForge - 订阅源</title>
    <ownerName>RSSForge</ownerName>
    <ownerEmail>noreply@gitfox-enter.github.io</ownerEmail>
    <dateCreated>2026-06-18</dateCreated>
  </head>
  <body>
    <outline type="rss" text="线报酷"
             title="线报酷"
             xmlUrl="https://gitfox-enter.github.io/RSSForge/feeds/xian-bao-ku.xml"
             htmlUrl="https://news.ixbk.fun/"/>
    <outline type="rss" text="423Down"
             title="423Down"
             xmlUrl="https://gitfox-enter.github.io/RSSForge/feeds/423Down.xml"
             htmlUrl="https://www.423down.com/"/>
  </body>
</opml>
```

## 定期更新订阅

每次 GitHub Actions 运行后，OPML 文件会自动更新，反映最新的站点列表。

> **建议**: 定期重新导入 OPML，确保阅读器中的链接是最新的。
