# How It Works

Learn how RSSForge works under the hood.

## Architecture Overview

```
sites.yaml configuration file
    ↓
GitHub Actions (scheduled triggers)
    ↓
┌─────────────────────────────────────────────────────────┐
│  crawl.py → crawler/                                    │
│  ├── engine.py         → Crawler engine                 │
│  │   ├── aiohttp       → HTTP requests                  │
│  │   ├── playwright    → JS rendering                   │
│  │   └── rate limiter  → Rate limiting                  │
│  ├── parsers/          → Content parsers                │
│  │   ├── core.py       → Generic parser                 │
│  │   └── deal_sites.py → 40+ site-specific parsers     │
│  ├── storage.py        → Data storage (SQLite)          │
│  ├── config.py         → Configuration loader           │
│  └── smart_scheduler.py → Smart scheduling              │
└─────────────────────────────────────────────────────────┘
    ↓
items.json (data)
    ↓
rss_feed.py → Atom Feed XML
    ↓
feeds/site-name.xml
    ↓
GitHub Pages → User access
```

## Scheduled Triggers

The `.github/workflows/` directory configures the cron schedules:

- **High tier** (high priority): Every 15-30 minutes
- **Mid tier** (medium priority): Every 1-2 hours
- **Low tier** (low priority): Every 4-8 hours

Daytime and nighttime have different trigger frequencies — nighttime runs at reduced frequency to conserve resources.

## Crawler Engine

### Standard HTML Crawling

Sends HTTP requests via aiohttp with randomized User-Agent strings and headers to simulate browser behavior.

### JavaScript Rendering

Some websites generate content dynamically with JavaScript (common in SPAs). RSSForge includes built-in Playwright support, which waits for the page to fully render before extracting content.

### Parsers

Every website has a different structure. RSSForge ships with 40+ site-specific parsers (`deal_sites.py`), each tailored to the HTML structure of a particular site for optimal content extraction. When no site-specific parser is found, the generic parser is used as a fallback.

## Data Storage

```
items.json
  └── items[]      All crawled content (7-day rolling cleanup)
  └── updated_at   Last update timestamp

hash_record.txt    Per-site page hashes (used to detect updates)
```

### 7-Day Rolling Cleanup

To save repository space, entries older than 7 days are automatically purged from `items.json`.

> Protection mechanism: Newly added sites have a 3-day grace period on their initial content, during which the 7-day window does not apply.

## RSS Generation

```
items.json
    ↓
rss_feed.py (grouped by source)
    ↓
feeds/site-name.xml   ← One independent feed per website
feeds_meta.json         ← Metadata for the frontend display
```

Each feed uses the **Atom 1.0** format (more standardized than RSS 2.0) and includes:
- `<icon>`: Site favicon (via Google Favicon Proxy)
- `media:thumbnail`: First image from each content entry
- `sy:updatePeriod`: Update frequency hint

## OPML Generation

`opml_generator.py` automatically generates categorized OPML files from `sites.yaml`:
- `opml.xml`: All sites
- `opml-deal-sites.xml`: Deal aggregation sites (线报站)
- `opml-software-sites.xml`: Software sites (软件站)
- And more

Import the OPML file into your reader to subscribe to an entire category at once.
