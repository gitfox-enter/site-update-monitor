# Feed Source Overview

## Feed File Structure

Each subscribed website generates a standalone Atom Feed file:

```
feeds/
├── 线报酷.xml       # Feed for 线报酷 (XianBaoKu)
├── 线报ICU.xml      # Feed for 线报ICU (XianBao ICU)
├── 423down.xml      # Feed for 423down
└── ...
```

Feed URL format:

```
https://USERNAME.github.io/RSSForge/feeds/SITE-NAME.xml
```

## Feed Format

RSSForge uses the **Atom 1.0** format (a more rigorous specification than RSS 2.0):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>线报酷 - RSSForge</title>
  <subtitle>RSS feed for 线报酷 (XianBaoKu)</subtitle>
  <icon>https://www.google.com/s2/favicons?domain=news.ixbk.fun&sz=64</icon>
  <link href="https://..." rel="alternate"/>
  <link href="https://..." rel="self" type="application/atom+xml"/>
  <id>https://...</id>
  <updated>2026-06-17T12:00:00+08:00</updated>
  <generator uri="https://github.com/gitfox-enter/RSSForge">RSSForge</generator>
  <sy:updatePeriod xmlns:sy="http://purl.org/syndication/1.0">hourly</sy:updatePeriod>
  <sy:updateFrequency xmlns:sy="http://purl.org/syndication/1.0">4</sy:updateFrequency>

  <entry>
    <title>Article Title</title>
    <link href="https://original-link"/>
    <id>https://original-link</id>
    <updated>2026-06-17T10:30:00+08:00</updated>
    <content type="text">Source: 线报酷 | Category: Deals | Link: https://...</content>
    <media:thumbnail xmlns:media="http://search.yahoo.com/mrss/" url="https://.../thumb.jpg" width="300"/>
  </entry>
</feed>
```

## Feed Metadata

The `feeds_meta.json` file stores metadata for each feed, used for front-end display:

```json
{
  "线报酷": {
    "interval": 15,
    "freq_label": "Every 15 minutes",
    "count": 256,
    "feed_url": "https://...",
    "icon": "https://www.google.com/s2/favicons?domain=..."
  }
}
```

## Entry Fields

Each feed entry contains the following fields:

| Field | Description |
|-------|-------------|
| `<title>` | Entry title (automatically filtered for spam) |
| `<link>` | Link to the original article |
| `<updated>` | Publication time (Beijing Time) |
| `<content>` | Content summary + source + category |
| `<media:thumbnail>` | First image from the content |
| `<category>` | Auto-generated category tags |

## Feed Update Frequency

The `<sy:updatePeriod>` and `<sy:updateFrequency>` elements inform RSS readers of the recommended polling interval:

| Interval | updatePeriod | updateFrequency |
|----------|--------------|-----------------|
| 15 minutes | hourly | 4 times/hour |
| 30 minutes | hourly | 2 times/hour |
| 1 hour | hourly | 1 time/hour |
| 4 hours | daily | 6 times/day |

> Note: These are recommended values. The actual update frequency is determined by the GitHub Actions schedule.
