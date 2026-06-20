# OPML Bulk Subscription

OPML is the standard format for exporting RSS feed subscriptions in bulk, allowing you to import multiple feeds into any RSS reader at once.

## Unified OPML File

RSSForge provides a single unified OPML file containing all feed sources:

```
https://gitfox-enter.github.io/RSSForge/opml.xml
```

> **Tip**: After forking, replace `gitfox-enter` with your GitHub username.

## How to Import OPML

### Reeder (Mac/iOS)

1. Open Reeder → **File** → **Add Feed...**
2. Select **Add OPML File...**
3. Paste the OPML URL: `https://your-username.github.io/RSSForge/opml.xml`
4. Click **Import**

### Inoreader

1. Log in to inoreader.com
2. Left sidebar menu → **Subscriptions** → **Manage Subscriptions** (top-right)
3. **Import OPML** → Paste the URL or select a local file

### NetNewsWire (Mac/iOS)

1. **File** → **Import Subscriptions...**
2. Select the OPML file

### FeedMe (Android)

1. Long press the **+** button at the bottom
2. Select **Import from OPML**
3. Paste the OPML URL

### Miniflux (Self-hosted)

```bash
curl -X POST https://your-miniflux.io/feed/import \
  -H "Content-Type: application/xml" \
  -u "user:api-key" \
  --data-binary @opml.xml
```

## OPML File Format

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>RSSForge - Feed Sources</title>
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

## Periodic OPML Updates

After each GitHub Actions run, the OPML file is automatically updated to reflect the latest site list.

> **Recommendation**: Periodically re-import the OPML to ensure your reader has the most up-to-date subscription links.
