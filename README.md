# Site Monitor

Automated content change tracker. Runs on a schedule, compares page hashes, and sends email alerts when updates are detected.

---

## Setup

### 1. Fork this repo

### 2. Add Secrets

Go to **Settings** → **Secrets and variables** → **Actions** → add:

| Secret | Value |
|---|---|
| `SMTP_USER` | your email address |
| `SMTP_PASSWORD` | app password |

### 3. Enable Workflow

Go to **Actions** → enable **Site Update Monitor**.

---

## Config

Edit `MONITOR_SITES` list in `crawl.py` to add or remove sites.
Change schedule in `.github/workflows/crawl.yml`.
