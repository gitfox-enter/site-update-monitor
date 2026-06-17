# Getting Started

Get RSSForge up and running in three steps.

## Step 1: Fork the Repository

Click **Fork** in the top-right corner of the GitHub page to copy the repository to your account.

```
https://github.com/gitfox-enter/RSSForge  →  Your Fork
```

## Step 2: Edit sites.yaml

Find `sites.yaml` in the repository root and add the websites you want to subscribe to:

```yaml
sites:
  - url: "https://news.ixbk.fun/"
    name: 线报酷          # Display name (deal aggregation site)
    tier: high            # high/mid/low — affects crawl frequency
    interval: 15          # Crawl interval (minutes)
    fast_check: true     # Enable fast check mode (detect changes without full parsing)
    max_pages: 3         # Number of historical pages to backfill on first run (optional)
```

Field reference:

| Field | Required | Description | Example |
|-------|----------|-------------|---------|
| `url` | Yes | Target website URL | `https://example.com/` |
| `name` | Yes | Display name | `Example Site` |
| `tier` | - | Priority tier (affects scheduling) | `high` / `mid` / `low` |
| `interval` | - | Crawl interval in minutes | `30` |
| `fast_check` | - | Fast check mode (detect changes only, skip content parsing) | `true` / `false` |
| `max_pages` | - | Number of historical pages to crawl on first run (default: 1) | `5` |

## Step 3: Enable GitHub Pages

1. Go to **Settings → Pages** in your repository
2. Set Source to **GitHub Actions**
3. Actions will automatically trigger the first build

Wait 1-2 minutes, then open your GitHub Pages URL:

```
https://your-username.github.io/RSSForge/
```

You should see your feed list.

## Subscribe to Your First Feed

Find the website you want on the homepage, click the **Subscribe** button to get the feed URL, and paste it into your RSS reader.

We recommend [Folo](https://folo.io) (an AI-powered RSS reader with RSSForge support). Inoreader, Reeder, and NetNewsWire are also great choices.

## Next Steps

- [Configure update frequency](/en/config/schedule) — Adjust crawl intervals for your sites
- [Enable historical backfill](/en/config/pagination) — Fetch historical content on first subscription
- [Bulk subscribe via OPML](/en/feeds/opml) — Subscribe to all sites at once
