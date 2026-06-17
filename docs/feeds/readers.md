# RSS 阅读器推荐

有了 RSSForge 生成的订阅源，还需要一个好用的 RSS 阅读器来阅读。

## 跨平台阅读器

### [Folo](https://folo.io) ⭐ 推荐

> RSSHub 官方推荐的阅读器，AI 加持，专为 RSSHub 设计。

- ✅ 支持 RSSHub 的路由格式
- ✅ 支持 RSSForge 的独立 feed
- ✅ AI 智能推荐订阅
- ✅ iOS / Android / Web
- 💰 免费 + 付费

### [Inoreader](https://inoreader.com)

老牌在线 RSS 阅读器，功能丰富。

- ✅ 在线同步，跨设备方便
- ✅ 支持 OPML 导入导出
- ✅ 规则过滤和自动化
- ✅ 免费版功能足够日常使用
- 💰 免费 + 付费（¥70/年起）

### [Feedly](https://feedly.com)

最流行的在线 RSS 聚合阅读器。

- ✅ 界面美观
- ✅ 社区分享
- ✅ 品牌信任度高
- ❌ 免费版有广告和条数限制
- 💰 免费 + Pro（¥12/月）

## Mac / iOS 原生阅读器

### [Reeder](https://reederapp.com) ⭐ Mac/iOS 最优选

- ✅ 设计精美，阅读体验极佳
- ✅ 支持 RSS / Atom / JSON Feed
- ✅ 支持 Fever、PocketBook 等后端
- ✅ iCloud 同步
- 💰 ¥78（Mac）/ ¥50（iOS），买断制

### [NetNewsWire](https://netnewswire.com)

免费开源的 Mac/iOS 阅读器。

- ✅ 完全免费开源
- ✅ 界面简洁
- ✅ 支持 Feedbin、NewsBlur 等后端
- ✅ Apple Silicon 优化
- 💰 免费

## 自建阅读器

### [Miniflux](https://miniflux.app)

轻量自建阅读器，Docker 一键部署。

```yaml
version: '3'
services:
  miniflux:
    image: miniflux/miniflux:latest
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgres://miniflux:secret@db/miniflux?sslmode=disable
      - CREATE_ADMIN=true
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=your-password
    depends_on:
      - db
  db:
    image: postgres:16
    environment:
      - POSTGRES_USER=miniflux
      - POSTGRES_PASSWORD=secret
      - POSTGRES_DB=miniflux
```

### [FreshRSS](https://freshrss.org)

功能丰富的自建阅读器。

- ✅ Web 界面，支持多用户
- ✅ 扩展生态丰富
- ✅ 订阅导入 OPML
- ✅ Docker 部署简单

## 命令行阅读器

### [Newsboat](http://newsboat.org)

Linux/Mac 终端里的 RSS 阅读器，极简主义。

```bash
# Ubuntu/Debian
sudo apt install newsboat

# macOS
brew install newsboat

# 配置
echo "https://用户名.github.io/RSSForge/opml.xml" >> ~/.newsboat/urls
newsboat
```

## 如何选择

| 使用场景 | 推荐 |
|---------|------|
| 追求最佳阅读体验 | Reeder（买断，¥128） |
| 想要 AI 辅助 | Folo |
| 多设备同步，免费 | Inoreader |
| Mac/iOS 为主，免费 | NetNewsWire |
| 自建，Docker 部署 | Miniflux |
| 终端爱好者 | Newsboat |
