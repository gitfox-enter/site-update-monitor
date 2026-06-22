#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gist 数据存储 — 将大数据文件（items.json 等）存储到 GitHub Gist，
避免 Git 仓库因频繁更新大文件而膨胀。

工作原理：
  - 爬取完成后，调用 sync_to_gist() 将 items.json / items_latest.json 推送到 Gist
  - 前端从 Gist raw URL 加载数据（比 GitHub Pages 文件更快）
  - 本地文件仍然保留（用于兼容性），但不再 commit 到 Git

Gist ID 存储在 gist_config.json 中。
"""

import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger('gist_store')

# Gist 配置文件
GIST_CONFIG_FILE = "gist_config.json"
GIST_DESCRIPTION = "site-update-monitor data store (auto-updated by GitHub Actions)"

# 需要同步到 Gist 的数据文件
GIST_FILES = {
    "items.json": "items.json",
    "items_latest.json": "items_latest.json",
}


def load_gist_config() -> Dict[str, str]:
    """Load Gist configuration (gist ID, etc.)."""
    if os.path.exists(GIST_CONFIG_FILE):
        try:
            with open(GIST_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_gist_config(config: Dict[str, str]) -> None:
    """Save Gist configuration."""
    with open(GIST_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_gist_id() -> Optional[str]:
    """Get the data Gist ID from config."""
    return load_gist_config().get('data_gist_id')


def get_gist_raw_url(filename: str = "items_latest.json") -> Optional[str]:
    """Get the raw download URL for a Gist file.

    Note: This returns a URL pattern that works for the latest version
    of the Gist. GitHub Gist raw URLs are of the form:
    https://gist.githubusercontent.com/{user}/{gist_id}/raw/{filename}
    """
    gist_id = get_gist_id()
    if not gist_id:
        return None
    # Get username from environment or default
    username = os.getenv('GITHUB_REPOSITORY_OWNER', 'gitfox-enter')
    return f"https://gist.githubusercontent.com/{username}/{gist_id}/raw/{filename}"


def _run_gh_api(method: str, url: str, data: Optional[Dict] = None) -> Optional[Dict]:
    """Run a GitHub API call using gh CLI (avoids token in command-line args).

    Returns the JSON response or None on failure.

    Fixes #53: using curl with token in command args exposes the token in /proc/*/cmdline.
    The gh CLI reads GITHUB_TOKEN from env automatically — no token in argv.
    """
    token = os.getenv('GITHUB_TOKEN', '')
    if not token:
        logger.warning("GITHUB_TOKEN not set, Gist sync will be skipped")
        return None

    # gh api accepts full URLs or relative paths; read token from env, not argv
    cmd = ['gh', 'api', '--method', method]
    if data:
        cmd.extend(['--input', '-'])  # read body from stdin

    try:
        input_data = json.dumps(data) if data else None
        result = subprocess.run(
            cmd + [url],
            input=input_data.encode() if input_data else None,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning("GitHub API call failed: %s", str(e)[:100])
    return None


def create_gist() -> Optional[str]:
    """Create a new Gist for data storage.

    Returns the Gist ID on success.
    """
    gist_data = {
        "description": GIST_DESCRIPTION,
        "public": False,
        "files": {
            name: {"content": "{}"}
            for name in GIST_FILES.keys()
        }
    }

    response = _run_gh_api('POST', 'https://api.github.com/gists', gist_data)
    if response and 'id' in response:
        gist_id = response['id']
        config = load_gist_config()
        config['data_gist_id'] = gist_id
        save_gist_config(config)
        logger.info("Created Gist: %s", gist_id, extra={'event': 'gist_created'})
        return gist_id
    return None


def sync_to_gist() -> bool:
    """Sync local data files to GitHub Gist.

    This should be called after each crawl/fast_check run,
    AFTER committing other changes to Git.

    Returns True on success.
    """
    gist_id = get_gist_id()

    # Create Gist if it doesn't exist
    if not gist_id:
        gist_id = create_gist()
        if not gist_id:
            logger.warning("Failed to create Gist, skipping sync")
            return False

    # Read local files
    files_data = {}
    for local_name, gist_name in GIST_FILES.items():
        if os.path.exists(local_name):
            try:
                with open(local_name, 'r', encoding='utf-8') as f:
                    files_data[gist_name] = {"content": f.read()}
            except Exception as e:
                logger.warning("Failed to read %s: %s", local_name, str(e)[:50])

    if not files_data:
        logger.info("No data files to sync")
        return False

    # Update Gist
    update_data = {"files": files_data}
    response = _run_gh_api(
        'PATCH',
        f'https://api.github.com/gists/{gist_id}',
        update_data,
    )

    if response and 'id' in response:
        logger.info("Gist sync successful: %d files", len(files_data),
                     extra={'event': 'gist_synced'})
        return True
    else:
        logger.warning("Gist sync failed")
        return False


def init_gist_in_workflow() -> None:
    """Initialize Gist in GitHub Actions workflow.

    Ensures the Gist ID is available (from gist_config.json committed to repo).
    Called at the start of each workflow run.
    """
    gist_id = get_gist_id()
    if gist_id:
        logger.info("Gist ID found: %s", gist_id)
    else:
        logger.warning("No Gist ID configured. Run create_gist() first.")


if __name__ == '__main__':
    # Test: sync current data to Gist
    success = sync_to_gist()
    if success:
        print("Gist sync successful!")
        gist_id = get_gist_id()
        print(f"Gist URL: https://gist.github.com/gitfox-enter/{gist_id}")
        for name in GIST_FILES:
            url = get_gist_raw_url(name)
            print(f"  {name}: {url}")
    else:
        print("Gist sync failed (GITHUB_TOKEN not available locally)")
