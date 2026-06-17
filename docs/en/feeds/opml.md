# OPML Bulk Subscription

OPML is the standard format for exporting RSS feed subscriptions in bulk, allowing you to import multiple feeds into any RSS reader at once.

## OPML Files Provided by RSSForge

After deployment, RSSForge automatically generates the following OPML files:

| File | Contents |
|------|----------|
| `opml.xml` | All sites |
| `opml-线报站.xml` | Deal alert sites (XianBao) |
| `opml-购物比价.xml` | Shopping & price comparison sites |
| `opml-软件站.xml` | Software resource sites |
| `opml-论坛.xml` | Forum sites |
| `opml-其他.xml` | Uncategorized sites |

OPML file URLs:

```
https://USERNAME.github.io/RSSForge/opml.xml
https://USERNAME.github.io/RSSForge/opml-线报站.xml
```

## How to Import OPML

### Reeder (Mac/iOS)

1. Open Reeder -> **File** -> **Add Feed...**
2. Select **Add OPML File...**
3. Paste the OPML file URL or choose a local file
4. Click **Import**

### Inoreader

1. Log in to inoreader.com
2. Left sidebar menu -> **Subscriptions** -> **Manage Subscriptions** (top-right)
3. **Import OPML** -> Select a file or paste the URL

### NetNewsWire (Mac/iOS)

1. **File** -> **Import Subscriptions...**
2. Select the OPML file

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
    <title>RSSForge Feed Sources</title>
    <dateCreated>Wed, 17 Jun 2026 12:00:00 +0800</dateCreated>
  </head>
  <body>
    <outline text="线报酷"
             title="线报酷"
             type="atom"
             xmlUrl="https://USERNAME.github.io/RSSForge/feeds/线报酷.xml"
             htmlUrl="https://news.ixbk.fun/"/>
    <outline text="423down"
             title="423down"
             type="atom"
             xmlUrl="https://USERNAME.github.io/RSSForge/feeds/423down.xml"
             htmlUrl="https://www.423down.com/"/>
  </body>
</opml>
```

## Subscribe by Category

If you only want to subscribe to a specific category of sites, simply import the corresponding category OPML file:

```
opml-线报站.xml   -> Deal alert sites only
opml-软件站.xml   -> Software sites only
```

## Periodic OPML Updates

After each GitHub Actions run, the OPML files are automatically updated to reflect the latest site list.

> It is recommended to re-import the OPML periodically to ensure your reader has the most up-to-date links.
