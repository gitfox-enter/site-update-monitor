# Site Monitor

Automated content change tracker. Runs on a schedule, crawls deal sites, and aggregates results into a live web page.

---

## Setup

### 1. Fork this repo

### 2. Enable Workflows

Go to **Actions** → enable **Site Update Monitor** and **Fast Check**.

### 3. View Results

After the first run, visit your GitHub Pages site:
`https://<your-username>.github.io/<repo-name>/`

---

## How It Works

- **Full Crawl** (`crawl.yml`): Every hour, checks all 33 sites for updates
- **Fast Check** (`fast_check.yml`): Every 5 minutes, checks top 8 active sites for new items
- **Frontend** (`index.html`): SPA that loads from `items.json`, with search, categories, and infinite scroll

## Config

Edit `MONITOR_SITES` list in `crawl.py` to add or remove sites.
Edit `FAST_SITES` list in `fast_check.py` to change high-frequency sites.
