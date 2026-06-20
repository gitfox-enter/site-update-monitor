# RSSForge

💗 Everything is RSSible

https://github.com/gitfox-enter/RSSForge
https://gitfox-enter.github.io/RSSForge/opml.xml
https://github.com/gitfox-enter/RSSForge/actions
https://gitfox-enter.github.io/RSSForge/

RSSForge is an open source, easy to use, and extensible RSS feed aggregator built on GitHub Actions, it's capable of generating RSS feeds from pretty much everything.

RSSForge delivers millions of contents aggregated from all kinds of sources, with pre-configured routes ready to use — no server required, 100% free.

**Unlike RSSHub**, RSSForge provides **pre-built, ready-to-subscribe feeds** for all monitored sites. Fork the project, enable GitHub Pages, and you have your own RSS service instantly. You can also contribute new site rules via PR.

## Special Thanks

https://github.com/DIYgod/RSSHub
https://github.com/RSS-Bridge/rss-bridge
https://github.com/cooderl/wewe-rss

## Related Projects

- [RSSHub](https://github.com/DIYgod/RSSHub) | Open source RSS feed aggregator, the inspiration for RSSForge
- [RSS-Bridge](https://github.com/RSS-Bridge/rss-bridge) | PHP-based RSS generator, another great reference
- [RSSHub Radar](https://github.com/DIYgod/RSSHub-Radar) | Browser extension to discover and subscribe to RSS feeds

## Features

- 🔧 **Zero server cost** — GitHub Actions free compute, 24/7 auto-run
- 📡 **Per-site RSS** — Each monitored site has its own independent feed
- 🖼️ **Real favicons** — Automatically fetches and caches website favicons
- 📋 **Unified OPML** — One OPML file to import all feeds into any RSS reader
- ⚡ **Smart scheduling** — Per-site intervals (15 min ~ 8 hrs), auto night-mode throttle
- 🔄 **Auto deduplication** — MD5 + URL + fuzzy title dedup, 7-day rolling window

## Quick Start

1. **Fork** this repository
2. **Enable GitHub Pages** — Settings → Pages → Deploy from branch → `gh-pages`
3. **Customize sites.yaml** — Add the sites you want to monitor
4. **Subscribe** — Import `opml.xml` into any RSS reader

## RSS Feeds

Each monitored site has its own RSS feed at:

```
https://gitfox-enter.github.io/RSSForge/feeds/[站点名称].xml
```

## OPML Subscription

Import the unified OPML file to subscribe all feeds at once:

```
https://gitfox-enter.github.io/RSSForge/opml.xml
```

### Supported Readers

- **Reeder** (Mac/iOS) — File → Add Feed → Add OPML File
- **Inoreader** — Subscriptions → Manage → Import OPML
- **NetNewsWire** (Mac/iOS) — File → Import Subscriptions
- **FeedMe** (Android) — Add feed → Import from OPML
- **Miniflux** (自建):
  ```bash
  curl -X POST https://your-miniflux.io/feed/import \
    -H "Content-Type: application/xml" \
    -u "user:api-key" \
    --data-binary @opml.xml
  ```

## 支持项目

如果这个项目对你有帮助，欢迎 [Star ⭐](https://github.com/gitfox-enter/RSSForge) 支持一下！

## License

MIT
