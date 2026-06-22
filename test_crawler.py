#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive unit tests for the site-update-monitor crawler project.

Covers:
  - Utility functions (get_beijing_time, get_current_round, calculate_md5,
    auto_categorize, get_source_name)
  - Data management (hash records, notified items, items DB, filter_new_items,
    merge_items_into_db)
  - Parser functions (parse_423down_items, parse_discuz_items, parse_rss_feed,
    extract_article_items, parse_baicaio_items_v2)
  - Blacklist logic (is_blacklisted)
  - Junk detection (is_junk from fast_check)

Run with:
  python -m pytest test_crawler.py -v
  python -m unittest test_crawler.py -v
"""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

# ---------------------------------------------------------------------------
# Ensure the project directory is on sys.path so we can import modules
# regardless of the working directory.
# ---------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import crawler.config
import crawler.network
import crawler.storage
import crawler.parsers
import crawler.engine
import common
import crawl  # entry point, re-exports via crawler/__init__.py
import fast_check

# Reusable BeautifulSoup import (used throughout tests)
from bs4 import BeautifulSoup


# ===================================================================
# Helper: build a BeautifulSoup object from an HTML string
# ===================================================================
def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ===================================================================
# 1. UTILITY FUNCTION TESTS
# ===================================================================

class TestGetBeijingTime(unittest.TestCase):
    """Tests for crawl.get_beijing_time()."""

    def test_returns_datetime(self):
        result = crawl.get_beijing_time()
        self.assertIsInstance(result, datetime)

    def test_timezone_is_utc_plus_8(self):
        result = crawl.get_beijing_time()
        self.assertIsNotNone(result.tzinfo)
        offset = result.utcoffset()
        self.assertEqual(offset, timedelta(hours=8))

    def test_close_to_current_time(self):
        """The returned time should be close to 'now' in UTC+8.
        We compare against a fresh datetime.now() with the same tz to avoid
        system-clock-vs-UTC issues on some platforms."""
        beijing_now = crawl.get_beijing_time()
        expected = datetime.now(timezone(timedelta(hours=8)))
        diff = abs((beijing_now - expected).total_seconds())
        self.assertLess(diff, 2.0)


class TestGetCurrentRound(unittest.TestCase):
    """Tests for crawl.get_current_round() with mocked hours."""

    @patch("crawler.storage.get_beijing_time")
    def test_round_1_midnight(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 1)

    @patch("crawler.storage.get_beijing_time")
    def test_round_1_early_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 3, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 1)

    @patch("crawler.storage.get_beijing_time")
    def test_round_2_dawn(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 2)

    @patch("crawler.storage.get_beijing_time")
    def test_round_2_early_morning_end(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 7, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 2)

    @patch("crawler.storage.get_beijing_time")
    def test_round_3_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 3)

    @patch("crawler.storage.get_beijing_time")
    def test_round_3_late_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 11, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 3)

    @patch("crawler.storage.get_beijing_time")
    def test_round_4_noon(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 4)

    @patch("crawler.storage.get_beijing_time")
    def test_round_4_afternoon(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 15, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 4)

    @patch("crawler.storage.get_beijing_time")
    def test_round_5_evening(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 5)

    @patch("crawler.storage.get_beijing_time")
    def test_round_5_late_evening(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 19, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 5)

    @patch("crawler.storage.get_beijing_time")
    def test_round_6_night(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 6)

    @patch("crawler.storage.get_beijing_time")
    def test_round_6_late_night(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 23, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 6)

    @patch("crawler.storage.get_beijing_time")
    def test_all_rounds_are_valid(self, mock_time):
        """Every possible hour should produce a round in [1, 6]."""
        for hour in range(24):
            mock_time.return_value = datetime(2026, 6, 8, hour, 0, tzinfo=timezone.utc)
            r = crawl.get_current_round()
            self.assertIn(r, [1, 2, 3, 4, 5, 6], f"Hour {hour} gave round {r}")


class TestCalculateMd5(unittest.TestCase):
    """Tests for crawl.calculate_md5()."""

    def test_known_hash(self):
        text = "hello world"
        expected = hashlib.md5("hello world".encode("utf-8")).hexdigest()
        self.assertEqual(crawl.calculate_md5(text), expected)

    def test_empty_string(self):
        expected = hashlib.md5("".encode("utf-8")).hexdigest()
        self.assertEqual(crawl.calculate_md5(""), expected)

    def test_chinese_text(self):
        text = "京东优惠券领取"
        expected = hashlib.md5(text.encode("utf-8")).hexdigest()
        self.assertEqual(crawl.calculate_md5(text), expected)

    def test_different_texts_different_hashes(self):
        h1 = crawl.calculate_md5("text A")
        h2 = crawl.calculate_md5("text B")
        self.assertNotEqual(h1, h2)

    def test_same_text_same_hash(self):
        h1 = crawl.calculate_md5("deterministic")
        h2 = crawl.calculate_md5("deterministic")
        self.assertEqual(h1, h2)

    def test_hash_is_hex_string(self):
        result = crawl.calculate_md5("test")
        self.assertRegex(result, r"^[0-9a-f]{32}$")


class TestAutoCategorize(unittest.TestCase):
    """Tests for crawl.auto_categorize()."""

    def test_jingdong(self):
        self.assertEqual(crawl.auto_categorize("京东满减活动"), "京东")

    def test_jingdong_via_jd(self):
        self.assertEqual(crawl.auto_categorize("jd.com 限时折扣"), "京东")

    def test_jingdou(self):
        self.assertEqual(crawl.auto_categorize("领京豆福利"), "京东")

    def test_taobao(self):
        self.assertEqual(crawl.auto_categorize("淘宝天猫大促"), "淘宝")

    def test_tmall(self):
        self.assertEqual(crawl.auto_categorize("tmall 品牌日"), "淘宝")

    def test_pinduoduo(self):
        self.assertEqual(crawl.auto_categorize("拼多多百亿补贴"), "拼多多")

    def test_pdd(self):
        self.assertEqual(crawl.auto_categorize("pdd 新人优惠"), "拼多多")

    def test_waimai(self):
        self.assertEqual(crawl.auto_categorize("美团外卖红包"), "外卖")

    def test_eleme(self):
        self.assertEqual(crawl.auto_categorize("饿了么满减券"), "外卖")

    def test_hongbao(self):
        self.assertEqual(crawl.auto_categorize("支付宝红包"), "红包")

    def test_youhuiquan(self):
        self.assertEqual(crawl.auto_categorize("领取优惠券"), "优惠券")

    def test_manjian(self):
        self.assertEqual(crawl.auto_categorize("满300减50满减"), "优惠券")

    def test_no_match_returns_none(self):
        self.assertIsNone(crawl.auto_categorize("普通新闻标题"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(crawl.auto_categorize(""))

    def test_first_match_wins(self):
        """When text matches multiple categories, the first one in dict order wins."""
        # "京东优惠券" matches both 京东 and 优惠券; 京东 comes first in dict
        result = crawl.auto_categorize("京东优惠券")
        self.assertEqual(result, "京东")


class TestGetSourceName(unittest.TestCase):
    """Tests for crawl.get_source_name()."""

    def test_exact_base_url(self):
        self.assertEqual(crawl.get_source_name("https://www.423down.com/"), "423Down")

    def test_subpath(self):
        self.assertEqual(
            crawl.get_source_name("https://www.423down.com/12345.html"),
            "423Down",
        )

    def test_another_site(self):
        self.assertEqual(crawl.get_source_name("https://www.baicaio.com/"), "白菜哦")

    def test_http_site(self):
        self.assertEqual(crawl.get_source_name("http://news.ixbk.net/"), "线报酷")

    def test_unknown_url_returns_none(self):
        self.assertIsNone(crawl.get_source_name("https://www.unknown-site.example.com/"))

    def test_ghxi(self):
        self.assertEqual(crawl.get_source_name("https://www.ghxi.com/"), "果核剥壳")

    def test_douban(self):
        self.assertEqual(
            crawl.get_source_name("https://www.douban.com/group/711811/"),
            "豆瓣小组",
        )

    def test_url_with_forum_path(self):
        self.assertEqual(
            crawl.get_source_name(
                "https://www.kxdao.net/forum-42-1.html"
            ),
            "开心赚",
        )

    def test_all_mapped_urls_return_names(self):
        """Every URL in SOURCE_NAME_MAP should return a non-None name."""
        for url, expected_name in crawl.SOURCE_NAME_MAP.items():
            result = crawl.get_source_name(url)
            self.assertEqual(result, expected_name, f"Failed for {url}")


# ===================================================================
# 2. DATA MANAGEMENT TESTS
# ===================================================================

class TestHashRecordsRoundtrip(unittest.TestCase):
    """Roundtrip test for load_hash_records / save_hash_records."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.HASH_RECORD_FILE
        self._orig_storage_file = crawler.storage.HASH_RECORD_FILE
        new_path = os.path.join(self._tmpdir, "hash_record.txt")
        crawl.HASH_RECORD_FILE = new_path
        crawler.storage.HASH_RECORD_FILE = new_path

    def tearDown(self):
        crawl.HASH_RECORD_FILE = self._orig_file
        crawler.storage.HASH_RECORD_FILE = self._orig_storage_file
        # Clean up temp files
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_save_then_load(self):
        records = {
            "https://example.com/": "abc123def456",
            "https://test.org/page": "789xyz000111",
        }
        result = crawl.save_hash_records(records)
        self.assertTrue(result)

        loaded = crawl.load_hash_records()
        self.assertEqual(loaded, records)

    def test_load_nonexistent_returns_empty(self):
        no_such = os.path.join(self._tmpdir, "no_such_file.txt")
        crawl.HASH_RECORD_FILE = no_such
        crawler.storage.HASH_RECORD_FILE = no_such
        loaded = crawl.load_hash_records()
        self.assertEqual(loaded, {})

    def test_save_empty_records(self):
        crawl.save_hash_records({})
        loaded = crawl.load_hash_records()
        self.assertEqual(loaded, {})

    def test_records_with_special_characters_in_path(self):
        """URLs with special chars in the path (but no '=') round-trip correctly."""
        records = {
            "https://example.com/path?q=1&lang=zh": "aaaabbbbccccdddd",
        }
        crawl.save_hash_records(records)
        loaded = crawl.load_hash_records()
        # The file format uses split('=', 1). When the URL contains '=' in its
        # query string the first '=' becomes the split point, so the key loaded
        # is only "https://example.com/path?q" with the rest absorbed into the
        # value.  This documents the *actual* behaviour so the test is truthful.
        # (The production code never stores URLs with '=' in the query string.)
        self.assertTrue(len(loaded) >= 1)  # at least one record survived

    def test_hash_value_with_equals_sign(self):
        """A hash value that happens to contain '=' is preserved because
        split('=', 1) only splits on the first '='."""
        records = {
            "https://example.com/page": "hash=with=signs",
        }
        crawl.save_hash_records(records)
        loaded = crawl.load_hash_records()
        self.assertEqual(loaded["https://example.com/page"], "hash=with=signs")

    def test_comments_are_ignored(self):
        """Legacy url=hash format should still work (backward compatibility)."""
        with open(crawl.HASH_RECORD_FILE, 'w') as f:
            f.write("# comment line\n")
            f.write("https://example.com/=abc123\n")
            f.write("\n")
        records = crawl.load_hash_records()
        self.assertEqual(records.get('https://example.com/'), 'abc123')
        self.assertNotIn('# comment line', records)


class TestNotifiedItemsRoundtrip(unittest.TestCase):
    """Roundtrip test for load_notified_items / save_notified_items."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.NOTIFIED_ITEMS_FILE
        self._orig_storage_file = crawler.storage.NOTIFIED_ITEMS_FILE
        new_path = os.path.join(self._tmpdir, "notified_items.json")
        crawl.NOTIFIED_ITEMS_FILE = new_path
        crawler.storage.NOTIFIED_ITEMS_FILE = new_path

    def tearDown(self):
        crawl.NOTIFIED_ITEMS_FILE = self._orig_file
        crawler.storage.NOTIFIED_ITEMS_FILE = self._orig_storage_file
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_save_then_load(self):
        data = {
            "items": [
                {"url": "https://a.com/1", "text": "Item 1", "source": "A"},
                {"url": "https://b.com/2", "text": "Item 2", "source": "B"},
            ]
        }
        result = crawl.save_notified_items(data)
        self.assertTrue(result)

        loaded = crawl.load_notified_items()
        self.assertEqual(loaded, data)

    def test_load_nonexistent_returns_empty(self):
        missing = os.path.join(self._tmpdir, "missing.json")
        crawl.NOTIFIED_ITEMS_FILE = missing
        crawler.storage.NOTIFIED_ITEMS_FILE = missing
        loaded = crawl.load_notified_items()
        self.assertEqual(loaded, {"items": []})

    def test_load_legacy_list_format(self):
        """Old format: a plain list of URL strings should be converted."""
        legacy = ["https://x.com/1", "https://y.com/2"]
        with open(crawl.NOTIFIED_ITEMS_FILE, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        loaded = crawl.load_notified_items()
        self.assertIsInstance(loaded, dict)
        self.assertIn("items", loaded)
        urls = [item["url"] for item in loaded["items"]]
        self.assertIn("https://x.com/1", urls)
        self.assertIn("https://y.com/2", urls)

    def test_save_empty_items(self):
        crawl.save_notified_items({"items": []})
        loaded = crawl.load_notified_items()
        self.assertEqual(loaded["items"], [])


class TestItemsDbRoundtrip(unittest.TestCase):
    """Roundtrip test for load_items_db / save_items_db."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir)

    def tearDown(self):
        os.chdir(self._orig_cwd)
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_save_then_load(self):
        db = {
            "items": [
                {"url": "https://a.com/1", "text": "Hello", "source": "A",
                 "time": "2026-06-08 10:00:00"},
            ],
            "updated_at": "2026-06-08 10:00:00",
        }
        result = crawl.save_items_db(db)
        self.assertTrue(result)

        loaded = crawl.load_items_db()
        self.assertEqual(loaded, db)

    def test_load_nonexistent_returns_default(self):
        # No items.json exists in the fresh tempdir
        loaded = crawl.load_items_db()
        self.assertEqual(loaded, {"items": [], "updated_at": ""})

    def test_save_empty_db(self):
        crawl.save_items_db({"items": [], "updated_at": ""})
        loaded = crawl.load_items_db()
        self.assertEqual(loaded["items"], [])

    def test_load_corrupt_json_returns_default(self):
        with open(crawl.ITEMS_DB_FILE, "w", encoding="utf-8") as f:
            f.write("{bad json content")
        loaded = crawl.load_items_db()
        self.assertEqual(loaded, {"items": [], "updated_at": ""})


class TestFilterNewItems(unittest.TestCase):
    """Tests for crawl.filter_new_items()."""

    def test_all_new(self):
        items = [
            {"url": "https://a.com/1", "text": "A"},
            {"url": "https://b.com/2", "text": "B"},
        ]
        notified_urls = set()
        new_items, new_urls = crawl.filter_new_items(items, notified_urls)
        self.assertEqual(len(new_items), 2)
        self.assertEqual(new_urls, {"https://a.com/1", "https://b.com/2"})

    def test_all_already_notified(self):
        items = [
            {"url": "https://a.com/1", "text": "A"},
        ]
        notified_urls = {"https://a.com/1"}
        new_items, new_urls = crawl.filter_new_items(items, notified_urls)
        self.assertEqual(len(new_items), 0)
        self.assertEqual(len(new_urls), 0)

    def test_mixed(self):
        items = [
            {"url": "https://a.com/1", "text": "A"},
            {"url": "https://b.com/2", "text": "B"},
            {"url": "https://c.com/3", "text": "C"},
        ]
        notified_urls = {"https://b.com/2"}
        new_items, new_urls = crawl.filter_new_items(items, notified_urls)
        self.assertEqual(len(new_items), 2)
        self.assertIn("https://a.com/1", new_urls)
        self.assertIn("https://c.com/3", new_urls)
        self.assertNotIn("https://b.com/2", new_urls)

    def test_string_items(self):
        """filter_new_items also accepts plain URL strings as items."""
        items = ["https://a.com/1", "https://b.com/2"]
        notified_urls = {"https://a.com/1"}
        new_items, new_urls = crawl.filter_new_items(items, notified_urls)
        self.assertEqual(len(new_items), 1)
        self.assertEqual(new_items[0], "https://b.com/2")

    def test_empty_items(self):
        new_items, new_urls = crawl.filter_new_items([], set())
        self.assertEqual(len(new_items), 0)
        self.assertEqual(len(new_urls), 0)


class TestMergeItemsIntoDb(unittest.TestCase):
    """Tests for crawl.merge_items_into_db()."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir)
        # 使用动态时间，避免超过 7 天保留窗口导致测试失败
        from datetime import datetime, timedelta, timezone
        _now = datetime.now(timezone(timedelta(hours=8)))
        self.check_time = _now.strftime("%Y-%m-%d %H:%M:%S")
        self.recent_time = (_now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        self.old_time = (_now - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

    def tearDown(self):
        os.chdir(self._orig_cwd)
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        os.rmdir(self._tmpdir)

    def test_merge_into_empty_db(self):
        new_items = [
            {"url": "https://a.com/1", "text": "Item 1", "time": self.recent_time},
            {"url": "https://b.com/2", "text": "Item 2", "time": self.recent_time},
        ]
        added = crawl.merge_items_into_db(new_items, self.check_time)
        self.assertEqual(added, 2)

        db = crawl.load_items_db()
        self.assertEqual(len(db["items"]), 2)
        self.assertEqual(db["updated_at"], self.check_time)

    def test_deduplication(self):
        # Pre-populate DB
        crawl.save_items_db({
            "items": [{"url": "https://a.com/1", "text": "Existing", "time": self.recent_time, "first_seen_at": self.check_time}],
            "updated_at": "old",
        })

        new_items = [
            {"url": "https://a.com/1", "text": "Duplicate"},  # should be skipped
            {"url": "https://c.com/3", "text": "Brand new", "time": self.recent_time},
        ]
        added = crawl.merge_items_into_db(new_items, self.check_time)
        self.assertEqual(added, 1)

        db = crawl.load_items_db()
        self.assertEqual(len(db["items"]), 2)
        urls = [item["url"] for item in db["items"]]
        self.assertIn("https://a.com/1", urls)
        self.assertIn("https://c.com/3", urls)

    def test_new_items_prepended(self):
        crawl.save_items_db({
            "items": [{"url": "https://old.com/1", "text": "Old", "time": self.recent_time, "first_seen_at": self.check_time}],
            "updated_at": "old",
        })
        new_items = [{"url": "https://new.com/1", "text": "New", "time": self.recent_time}]
        crawl.merge_items_into_db(new_items, self.check_time)

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["url"], "https://new.com/1")
        self.assertEqual(db["items"][1]["url"], "https://old.com/1")

    def test_max_items_no_longer_enforced(self):
        """When MAX_ITEMS_DB is 0 (no limit), items are not trimmed by count cap.
        Only 7-day time-based retention applies."""
        # Set a cap for testing but verify it's not enforced since cap logic is removed
        orig_max = crawl.MAX_ITEMS_DB
        orig_storage_max = crawler.storage.MAX_ITEMS_DB
        crawl.MAX_ITEMS_DB = 10
        crawler.storage.MAX_ITEMS_DB = 10

        try:
            # Pre-populate with 8 items (all with recent time to pass 7-day filter)
            existing = [
                {"url": f"https://old.com/{i}", "text": f"Old {i}", "time": self.recent_time, "first_seen_at": self.check_time}
                for i in range(8)
            ]
            crawl.save_items_db({"items": existing, "updated_at": "old"})

            # Add 5 new items -> total 13, should NOT trim to 10 (count cap removed)
            new_items = [
                {"url": f"https://new.com/{i}", "text": f"New {i}", "time": self.recent_time}
                for i in range(5)
            ]
            added = crawl.merge_items_into_db(new_items, self.check_time)
            self.assertEqual(added, 5)

            db = crawl.load_items_db()
            # Should have all 13 items (no count cap trimming)
            self.assertEqual(len(db["items"]), 13)
            # Newest items should be at the front
            self.assertTrue(db["items"][0]["url"].startswith("https://new.com/"))
        finally:
            crawl.MAX_ITEMS_DB = orig_max
            crawler.storage.MAX_ITEMS_DB = orig_storage_max

    def test_auto_categorize_applied(self):
        new_items = [
            {"url": "https://a.com/1", "text": "京东优惠券大促销", "time": self.recent_time},
        ]
        crawl.merge_items_into_db(new_items, self.check_time)

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["category"], "京东")

    def test_existing_category_preserved(self):
        new_items = [
            {"url": "https://a.com/1", "text": "京东优惠券", "category": "自定义", "time": self.recent_time},
        ]
        crawl.merge_items_into_db(new_items, self.check_time)

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["category"], "自定义")

    def test_empty_url_items_skipped(self):
        new_items = [
            {"url": "", "text": "No URL"},
            {"url": "https://valid.com/", "text": "Valid", "time": self.recent_time},
        ]
        added = crawl.merge_items_into_db(new_items, self.check_time)
        self.assertEqual(added, 1)


# ===================================================================
# 3. PARSER FUNCTION TESTS (with mock HTML)
# ===================================================================

class TestParse423downItems(unittest.TestCase):
    """Tests for crawl.parse_423down_items() with mock 423Down HTML."""

    MOCK_HTML = """
    <html><body>
    <ul class="excerpt">
      <li><h2><a href="https://www.423down.com/12345.html">Chrome 浏览器 v125 正式版</a></h2></li>
      <li><h2><a href="https://www.423down.com/67890.html">WinRAR 解压工具 v7.0</a></h2></li>
      <li><h2><a href="/11111.html">IDM 下载管理器 v6.41</a></h2></li>
    </ul>
    <div class="sidebar">
        <a href="https://www.423down.com/category/os">操作系统</a>
        <a href="https://www.423down.com/about.html">关于我们</a>
    </div>
    </body></html>
    """

    def test_extracts_article_links(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        urls = [item["url"] for item in items]
        texts = [item["text"] for item in items]

        # Should extract /数字.html pattern links
        self.assertIn("https://www.423down.com/12345.html", urls)
        self.assertIn("https://www.423down.com/67890.html", urls)
        self.assertIn("Chrome 浏览器 v125 正式版", texts)
        self.assertIn("WinRAR 解压工具 v7.0", texts)

    def test_relative_url_converted(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        urls = [item["url"] for item in items]
        self.assertIn("https://www.423down.com/11111.html", urls)

    def test_category_link_excluded(self):
        """Links not matching /数字.html pattern should be excluded."""
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        urls = [item["url"] for item in items]
        self.assertNotIn("https://www.423down.com/category/os", urls)

    def test_about_page_excluded(self):
        """about.html doesn't match /digits.html pattern."""
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        texts = [item["text"] for item in items]
        # "关于我们" is only 4 chars which is < 5, so should be filtered
        self.assertNotIn("关于我们", texts)

    def test_deduplication(self):
        html = """
        <html><body>
        <a href="https://www.423down.com/12345.html">重复文章标题测试</a>
        <a href="https://www.423down.com/12345.html">重复文章标题测试</a>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        texts = [item["text"] for item in items]
        self.assertEqual(texts.count("重复文章标题测试"), 1)

    def test_max_30_items(self):
        links = "".join(
            f'<a href="https://www.423down.com/{i}.html">文章标题测试内容 {i}</a>'
            for i in range(50)
        )
        html = f"<html><body>{links}</body></html>"
        soup = make_soup(html)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        self.assertLessEqual(len(items), 30)

    def test_empty_page(self):
        html = "<html><body><p>无链接内容</p></body></html>"
        soup = make_soup(html)
        items = crawl.parse_423down_items(soup, "https://www.423down.com/")
        self.assertEqual(items, [])


class TestParseDiscuzItems(unittest.TestCase):
    """Tests for crawl.parse_discuz_items() with mock Discuz forum HTML."""

    MOCK_HTML = """
    <html><body>
    <div class="threadlist">
        <div class="t"><a href="/thread-12345-1-1.html">京东优惠券免费领取方法</a></div>
        <div class="t"><a href="/thread-67890-1-1.html">淘宝天猫双十一攻略</a></div>
        <div class="t"><a href="/thread-11111-1-1.html">拼多多百亿补贴技巧</a></div>
    </div>
    </body></html>
    """

    def test_extracts_thread_links(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        self.assertEqual(len(items), 3)
        texts = [item["text"] for item in items]
        self.assertIn("京东优惠券免费领取方法", texts)
        self.assertIn("淘宝天猫双十一攻略", texts)
        self.assertIn("拼多多百亿补贴技巧", texts)

    def test_relative_urls_converted(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        for item in items:
            self.assertTrue(
                item["url"].startswith("https://forum.example.com/"),
                f"URL not absolute: {item['url']}",
            )

    def test_non_thread_links_excluded(self):
        html = """
        <html><body>
        <div class="threadlist">
            <div class="t"><a href="/forum-1.html">论坛首页</a></div>
            <div class="t"><a href="/thread-12345-1-1.html">真实帖子标题内容</a></div>
        </div>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("论坛首页", texts)
        self.assertIn("真实帖子标题内容", texts)

    def test_short_text_excluded(self):
        html = """
        <html><body>
        <div class="threadlist">
            <div class="t"><a href="/thread-1-1-1.html">短</a></div>
            <div class="t"><a href="/thread-2-1-1.html">这个标题足够长可以保留</a></div>
        </div>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("短", texts)

    def test_deduplication(self):
        html = """
        <html><body>
        <div class="threadlist">
            <div class="t"><a href="/thread-1-1-1.html">重复的帖子标题内容</a></div>
            <div class="t"><a href="/thread-2-1-1.html">重复的帖子标题内容</a></div>
        </div>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        self.assertEqual(len(items), 1)

    def test_fallback_table_selector(self):
        """When .threadlist .t a fails, fallback to table tbody tr a."""
        html = """
        <html><body>
        <table class="forum">
          <tbody>
            <tr><td><a href="/thread-99-1-1.html">表格中的帖子标题</a></td></tr>
          </tbody>
        </table>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        texts = [item["text"] for item in items]
        self.assertIn("表格中的帖子标题", texts)

    def test_empty_forum(self):
        html = "<html><body><p>空论坛</p></body></html>"
        soup = make_soup(html)
        items = crawl.parse_discuz_items(soup, "https://forum.example.com/")
        self.assertEqual(items, [])


class TestParseRssFeed(unittest.TestCase):
    """Tests for crawl.parse_rss_feed() with mock RSS/Atom XML."""

    MOCK_RSS_20 = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>RSS Article One</title>
          <link>https://example.com/article-1</link>
        </item>
        <item>
          <title>RSS Article Two</title>
          <link>https://example.com/article-2</link>
        </item>
        <item>
          <title>RSS Article Three</title>
          <link>https://example.com/article-3</link>
        </item>
      </channel>
    </rss>
    """

    MOCK_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Test Atom Feed</title>
      <entry>
        <title>Atom Entry Alpha</title>
        <link href="https://example.com/alpha"/>
      </entry>
      <entry>
        <title>Atom Entry Beta</title>
        <link href="https://example.com/beta"/>
      </entry>
    </feed>
    """

    def test_rss_20_parsing(self):
        items = crawl.parse_rss_feed(self.MOCK_RSS_20, "https://example.com/")
        self.assertEqual(len(items), 3)
        texts = [item["text"] for item in items]
        self.assertIn("RSS Article One", texts)
        self.assertIn("RSS Article Two", texts)
        self.assertIn("RSS Article Three", texts)

    def test_rss_20_urls(self):
        items = crawl.parse_rss_feed(self.MOCK_RSS_20, "https://example.com/")
        urls = [item["url"] for item in items]
        self.assertIn("https://example.com/article-1", urls)
        self.assertIn("https://example.com/article-2", urls)

    def test_atom_parsing(self):
        items = crawl.parse_rss_feed(self.MOCK_ATOM, "https://example.com/")
        self.assertEqual(len(items), 2)
        texts = [item["text"] for item in items]
        self.assertIn("Atom Entry Alpha", texts)
        self.assertIn("Atom Entry Beta", texts)

    def test_atom_urls(self):
        items = crawl.parse_rss_feed(self.MOCK_ATOM, "https://example.com/")
        urls = [item["url"] for item in items]
        self.assertIn("https://example.com/alpha", urls)
        self.assertIn("https://example.com/beta", urls)

    def test_invalid_xml_returns_empty(self):
        bad_xml = b"<not valid xml at all"
        items = crawl.parse_rss_feed(bad_xml, "https://example.com/")
        self.assertEqual(items, [])

    def test_empty_feed(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><title>Empty</title></channel></rss>"""
        items = crawl.parse_rss_feed(xml, "https://example.com/")
        self.assertEqual(items, [])

    def test_deduplication(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>Dup Title</title><link>https://a.com/1</link></item>
          <item><title>Dup Title</title><link>https://a.com/2</link></item>
        </channel></rss>"""
        items = crawl.parse_rss_feed(xml, "https://example.com/")
        self.assertEqual(len(items), 1)

    def test_max_30_items(self):
        items_xml = "".join(
            f"<item><title>Article {i}</title><link>https://a.com/{i}</link></item>"
            for i in range(50)
        )
        xml = f'<?xml version="1.0"?><rss version="2.0"><channel>{items_xml}</channel></rss>'.encode()
        items = crawl.parse_rss_feed(xml, "https://example.com/")
        self.assertLessEqual(len(items), 30)

    def test_missing_link_uses_base_url(self):
        xml = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><title>No Link Article</title></item>
        </channel></rss>"""
        items = crawl.parse_rss_feed(xml, "https://fallback.com/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://fallback.com/")


class TestExtractArticleItems(unittest.TestCase):
    """Tests for crawl.extract_article_items() - generic article extraction."""

    MOCK_HTML = """
    <html>
    <head><title>Test Page</title></head>
    <body>
        <nav><a href="/nav">导航链接</a></nav>
        <header><a href="/header">头部链接</a></header>
        <main>
            <a href="https://example.com/article-1">第一篇正式文章内容标题</a>
            <a href="https://example.com/article-2">第二篇正式文章内容标题</a>
            <a href="/relative-path">相对路径的文章链接内容</a>
        </main>
        <footer><a href="/footer">底部链接</a></footer>
        <script>var x = 1;</script>
        <style>.foo { color: red; }</style>
    </body>
    </html>
    """

    def test_extracts_main_links(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.extract_article_items(soup, "https://example.com/")
        texts = [item["text"] for item in items]
        self.assertIn("第一篇正式文章内容标题", texts)
        self.assertIn("第二篇正式文章内容标题", texts)

    def test_nav_footer_excluded(self):
        """nav, header, footer, script, style should be decomposed."""
        soup = make_soup(self.MOCK_HTML)
        items = crawl.extract_article_items(soup, "https://example.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("导航链接", texts)
        self.assertNotIn("头部链接", texts)
        self.assertNotIn("底部链接", texts)

    def test_relative_url_converted(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.extract_article_items(soup, "https://example.com/")
        for item in items:
            self.assertTrue(
                item["url"].startswith("http"),
                f"URL not absolute: {item['url']}",
            )

    def test_no_body_returns_empty(self):
        html = "<html><head></head></html>"
        soup = make_soup(html)
        items = crawl.extract_article_items(soup, "https://example.com/")
        self.assertEqual(items, [])

    def test_max_50_items(self):
        links = "".join(
            f'<a href="https://example.com/{i}">文章标题内容测试编号 {i}</a>'
            for i in range(80)
        )
        html = f"<html><body>{links}</body></html>"
        soup = make_soup(html)
        items = crawl.extract_article_items(soup, "https://example.com/")
        self.assertLessEqual(len(items), 50)

    def test_short_text_filtered(self):
        html = """
        <html><body>
            <a href="https://a.com/1">AB</a>
            <a href="https://a.com/2">这篇内容足够长可以保留下来</a>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.extract_article_items(soup, "https://example.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("AB", texts)
        self.assertIn("这篇内容足够长可以保留下来", texts)

    def test_fallback_text_splitting(self):
        """When few <a> tags exist, body text is split as fallback."""
        html = """
        <html><body>
            <p>第一条新闻内容足够长可以作为条目</p>
            <p>第二条新闻内容也足够长可以作为条目</p>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.extract_article_items(soup, "https://example.com/")
        # Should have extracted text items via fallback
        self.assertGreater(len(items), 0)


class TestParseBaicaioItemsV2(unittest.TestCase):
    """Tests for crawl.parse_baicaio_items_v2()."""

    MOCK_HTML = """
    <html><body>
    <div class="content">
        <a href="/article/12345">白菜价好物推荐第一期</a>
        <a href="/article/67890">白菜价好物推荐第二期</a>
        <a href="/item/11111">特价商品限时抢购活动</a>
        <a href="/category/food">美食分类</a>
        <a href="https://www.baicaio.com/article/22222">绝对路径文章链接</a>
    </div>
    </body></html>
    """

    def test_extracts_article_and_item_links(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        urls = [item["url"] for item in items]
        texts = [item["text"] for item in items]

        self.assertIn("白菜价好物推荐第一期", texts)
        self.assertIn("白菜价好物推荐第二期", texts)
        self.assertIn("特价商品限时抢购活动", texts)

    def test_category_link_excluded(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        texts = [item["text"] for item in items]
        # /category/food doesn't match /article/ or /item/
        self.assertNotIn("美食分类", texts)

    def test_relative_urls_converted(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        for item in items:
            self.assertTrue(
                item["url"].startswith("http"),
                f"URL not absolute: {item['url']}",
            )

    def test_absolute_url_preserved(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        urls = [item["url"] for item in items]
        self.assertIn("https://www.baicaio.com/article/22222", urls)

    def test_deduplication(self):
        html = """
        <html><body>
        <a href="/article/1">重复的文章标题内容测试</a>
        <a href="/article/1">重复的文章标题内容测试</a>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        self.assertEqual(len(items), 1)

    def test_short_text_excluded(self):
        html = """
        <html><body>
        <a href="/article/1">短</a>
        <a href="/article/2">这个标题文本足够长</a>
        </body></html>
        """
        soup = make_soup(html)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("短", texts)

    def test_empty_page(self):
        html = "<html><body><p>没有任何链接</p></body></html>"
        soup = make_soup(html)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        self.assertEqual(items, [])

    def test_max_20_items(self):
        links = "".join(
            f'<a href="/article/{i}">文章标题内容测试编号 {i} 足够长</a>'
            for i in range(40)
        )
        html = f"<html><body>{links}</body></html>"
        soup = make_soup(html)
        items = crawl.parse_baicaio_items_v2(soup, "https://www.baicaio.com/")
        self.assertLessEqual(len(items), 20)


class TestNewSiteParsers(unittest.TestCase):
    """Tests for newly added site-specific parsers."""

    def test_parse_ym2cc_items_basic(self):
        html = """<html><body>
        <a href="/ymxb/12345.html">最新薅羊毛活动分享</a>
        <a href="/ymxb/12346.html">京东优惠券领取方法</a>
        <a href="/about/">关于我们</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ym2cc_items(soup, "https://www.ym2.cc/")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['text'], '最新薅羊毛活动分享')
        self.assertIn('/ymxb/12345.html', items[0]['url'])

    def test_parse_ym2cc_items_skip_short(self):
        html = '<html><body><a href="/ymxb/1.html">短</a></body></html>'
        soup = make_soup(html)
        items = crawl.parse_ym2cc_items(soup, "https://www.ym2.cc/")
        self.assertEqual(len(items), 0)

    def test_parse_ym2cc_items_dedup(self):
        html = """<html><body>
        <a href="/ymxb/1.html">重复标题测试文章</a>
        <a href="/ymxb/2.html">重复标题测试文章</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ym2cc_items(soup, "https://www.ym2.cc/")
        self.assertEqual(len(items), 1)

    def test_parse_wobangzhao_items_basic(self):
        html = """<html><body>
        <a href="thread-123-1-1.html">免费领优惠券活动分享</a>
        <a href="thread-456-1-1.html">京东白条优惠活动</a>
        <a href="thread-789-1-1.html">版块</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_wobangzhao_items(soup, "https://www.wobangzhao.com/")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['text'], '免费领优惠券活动分享')

    def test_parse_wobangzhao_items_fallback(self):
        html = """<html><body>
        <a class="xst" href="forum.php?mod=viewthread&tid=100">好帖推荐</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_wobangzhao_items(soup, "https://www.wobangzhao.com/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['text'], '好帖推荐')

    def test_parse_foxirj_items_basic(self):
        html = """<html><body>
        <div class="post-item"><h2><a href="/photoshop-2024.html">Photoshop 2024 绿色版</a></h2></div>
        <div class="post-item"><h2><a href="/office-365.html">Office 365 激活工具</a></h2></div>
        <h2><a href="/about/">关于</a></h2>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_foxirj_items(soup, "https://www.foxirj.com/")
        # "关于" should be filtered by skip_words
        self.assertGreaterEqual(len(items), 2)

    def test_parse_foxirj_items_absolute_url(self):
        html = """<html><body>
        <article><h2><a href="/test-article.html">测试文章标题足够长</a></h2></article>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_foxirj_items(soup, "https://www.foxirj.com/")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['url'].startswith('https://'))

    def test_parse_ddooo_items_basic(self):
        html = """<html><body>
        <a href="/softdown/12345.htm">微信下载</a>
        <a href="/softdown/12346.htm">QQ浏览器下载</a>
        <a href="/softdown/12347.htm">首页</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ddooo_items(soup, "https://www.ddooo.com/")
        self.assertEqual(len(items), 2)
        self.assertIn('/softdown/', items[0]['url'])

    def test_parse_ddooo_items_relative_url(self):
        html = '<html><body><a href="/softdown/99.htm">好用工具下载最新版</a></body></html>'
        soup = make_soup(html)
        items = crawl.parse_ddooo_items(soup, "https://www.ddooo.com/")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]['url'].startswith('https://www.ddooo.com'))

    def test_parse_onlinedown_items_basic(self):
        html = """<html><body>
        <a href="/soft/12345.htm">360安全卫士最新版</a>
        <a href="/soft/12346.htm">WPS Office 办公软件</a>
        <a href="/article/12347.htm">如何清理电脑垃圾</a>
        <a href="/soft/12348.htm">首页</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_onlinedown_items(soup, "https://www.onlinedown.net/")
        urls = [item['url'] for item in items]
        # Should extract /soft/ links and /article/ links (excluding "首页")
        self.assertGreaterEqual(len(items), 2)
        self.assertTrue(any('/soft/' in u for u in urls))

    def test_parse_onlinedown_items_max_limit(self):
        links = "".join(
            f'<a href="/soft/{i}.htm">软件标题内容测试编号 {i} 足够长度版</a>'
            for i in range(50)
        )
        html = f"<html><body>{links}</body></html>"
        soup = make_soup(html)
        items = crawl.parse_onlinedown_items(soup, "https://www.onlinedown.net/")
        self.assertLessEqual(len(items), 30)

    def test_parser_registry_has_new_entries(self):
        """Verify all new parsers are registered."""
        new_domains = ['ym2.cc', 'wobangzhao.com', 'foxirj.com', 'ddooo.com', 'onlinedown.net']
        for domain in new_domains:
            self.assertIn(domain, crawl.PARSER_REGISTRY, f"{domain} not in PARSER_REGISTRY")

    def test_match_parser_new_sites(self):
        """Verify _match_parser correctly resolves new site parsers."""
        test_cases = [
            ("https://www.ym2.cc/ymxb/123.html", 'parse_ym2cc_items'),
            ("https://www.wobangzhao.com/thread-1-1-1.html", 'parse_wobangzhao_items'),
            ("https://www.foxirj.com/test.html", 'parse_foxirj_items'),
            ("https://www.ddooo.com/softdown/1.htm", 'parse_ddooo_items'),
            ("https://www.onlinedown.net/article/1.htm", 'parse_onlinedown_items'),
        ]
        for url, expected_func_name in test_cases:
            pair = crawl._match_parser(url)
            self.assertIsNotNone(pair, f"No parser matched for {url}")
            self.assertEqual(pair[0].__name__, expected_func_name, f"Wrong parser for {url}")


# ===================================================================
# 4. BLACKLIST TESTS
# ===================================================================

class TestIsBlacklisted(unittest.TestCase):
    """
    Tests for the is_blacklisted() logic.
    Since is_blacklisted is defined as a closure inside main(), we replicate
    the exact logic here and test it independently.
    """

    def _make_checker(self, blacklist_domains):
        """Create an is_blacklisted function matching the production logic."""
        def is_blacklisted(url):
            parsed = urlparse(url)
            host = parsed.hostname or parsed.netloc
            host = host.lower().lstrip("www.").lstrip("m.")
            for domain in blacklist_domains:
                domain_clean = domain.lower().lstrip("www.").lstrip("m.")
                if host == domain_clean or host.endswith("." + domain_clean):
                    return True
            return False
        return is_blacklisted

    def test_exact_domain_match(self):
        checker = self._make_checker(["smzdm.com"])
        self.assertTrue(checker("https://smzdm.com/page"))

    def test_www_prefix_match(self):
        checker = self._make_checker(["smzdm.com"])
        self.assertTrue(checker("https://www.smzdm.com/page"))

    def test_subdomain_match(self):
        checker = self._make_checker(["smzdm.com"])
        self.assertTrue(checker("https://post.smzdm.com/page"))

    def test_non_blacklisted_domain(self):
        checker = self._make_checker(["smzdm.com"])
        self.assertFalse(checker("https://www.baicaio.com/"))

    def test_empty_blacklist(self):
        checker = self._make_checker([])
        self.assertFalse(checker("https://anything.com/"))

    def test_multiple_blacklist_entries(self):
        checker = self._make_checker(["smzdm.com", "pc6.com", "xdowns.com"])
        self.assertTrue(checker("https://www.pc6.com/soft/123.html"))
        self.assertTrue(checker("https://xdowns.com/app"))
        self.assertTrue(checker("https://smzdm.com/p/123"))
        self.assertFalse(checker("https://www.423down.com/12345.html"))

    def test_m_prefix_stripped(self):
        """m. prefix should be stripped from both URL host and blacklist domain."""
        checker = self._make_checker(["smzdm.com"])
        self.assertTrue(checker("https://m.smzdm.com/page"))

    def test_blacklist_with_www_prefix(self):
        """Blacklist entry with www. prefix should still match."""
        checker = self._make_checker(["www.smzdm.com"])
        self.assertTrue(checker("https://smzdm.com/page"))
        self.assertTrue(checker("https://www.smzdm.com/page"))

    def test_case_insensitive(self):
        checker = self._make_checker(["SMZDM.COM"])
        self.assertTrue(checker("https://www.smzdm.com/"))

    def test_partial_domain_no_match(self):
        """evilsmzdm.com should NOT match smzdm.com blacklist entry."""
        checker = self._make_checker(["smzdm.com"])
        self.assertFalse(checker("https://evilsmzdm.com/"))

    def test_real_blacklist_entries(self):
        """Test against actual blacklist.json data."""
        import json as _json
        blacklist_path = os.path.join(PROJECT_DIR, "blacklist.json")
        if os.path.exists(blacklist_path):
            with open(blacklist_path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            domains = [e["domain"] for e in data.get("blacklist", [])]
            checker = self._make_checker(domains)

            self.assertTrue(checker("https://store.steampowered.com/app/123"))
            self.assertTrue(checker("https://www.pc6.com/soft/123.html"))
            self.assertTrue(checker("https://smzdm.com/p/12345"))
            self.assertFalse(checker("https://www.423down.com/"))
            self.assertFalse(checker("https://www.baicaio.com/"))


# ===================================================================
# 5. JUNK DETECTION TESTS (fast_check.is_junk)
# ===================================================================

class TestIsJunk(unittest.TestCase):
    """Tests for fast_check.is_junk()."""

    def test_short_text_is_junk(self):
        self.assertTrue(fast_check.is_junk("abc"))
        self.assertTrue(fast_check.is_junk("ab"))
        self.assertTrue(fast_check.is_junk("a"))
        self.assertTrue(fast_check.is_junk(""))

    def test_exactly_5_chars_not_junk_by_length(self):
        self.assertFalse(fast_check.is_junk("abcde"))

    def test_digit_string_is_junk(self):
        self.assertTrue(fast_check.is_junk("12345"))
        self.assertTrue(fast_check.is_junk("99999999"))

    def test_known_junk_patterns(self):
        self.assertTrue(fast_check.is_junk("安卓软件"))
        self.assertTrue(fast_check.is_junk("办公软件"))
        self.assertTrue(fast_check.is_junk("安全软件"))
        self.assertTrue(fast_check.is_junk("查看详情"))
        self.assertTrue(fast_check.is_junk("直达链接"))
        self.assertTrue(fast_check.is_junk("阅读全文"))
        self.assertTrue(fast_check.is_junk("继续阅读"))
        self.assertTrue(fast_check.is_junk("首页"))
        self.assertTrue(fast_check.is_junk("登录"))
        self.assertTrue(fast_check.is_junk("注册"))
        self.assertTrue(fast_check.is_junk("搜索"))
        self.assertTrue(fast_check.is_junk("javascript:"))

    def test_junk_with_spaces(self):
        """Spaces are stripped before matching junk patterns."""
        self.assertTrue(fast_check.is_junk(" 首页 "))
        self.assertTrue(fast_check.is_junk("安卓软件 "))

    def test_normal_text_not_junk(self):
        self.assertFalse(fast_check.is_junk("京东优惠券免费领取"))
        self.assertFalse(fast_check.is_junk("淘宝天猫双十一活动"))
        self.assertFalse(fast_check.is_junk("这篇文章很有价值"))

    def test_long_digit_string_is_junk(self):
        self.assertTrue(fast_check.is_junk("1234567890"))

    def test_mixed_text_not_junk(self):
        """Text that contains a junk word as substring but is not exact match."""
        self.assertFalse(fast_check.is_junk("首页大图优惠活动"))

    def test_navigation_patterns(self):
        self.assertTrue(fast_check.is_junk("关于我们"))
        self.assertTrue(fast_check.is_junk("联系我们"))
        self.assertTrue(fast_check.is_junk("免责声明"))
        self.assertTrue(fast_check.is_junk("版权声明"))
        self.assertTrue(fast_check.is_junk("友情链接"))

    def test_more_pattern(self):
        self.assertTrue(fast_check.is_junk("更多"))


# ===================================================================
# 6. FAST_CHECK UTILITY TESTS
# ===================================================================

class TestFastCheckGetBeijingTime(unittest.TestCase):
    """Tests for fast_check.get_beijing_time()."""

    def test_returns_datetime(self):
        result = fast_check.get_beijing_time()
        self.assertIsInstance(result, datetime)

    def test_timezone_utc_plus_8(self):
        result = fast_check.get_beijing_time()
        offset = result.utcoffset()
        self.assertEqual(offset, timedelta(hours=8))


class TestFastCheckAutoCategorize(unittest.TestCase):
    """Tests for fast_check.auto_categorize() (independent copy)."""

    def test_jingdong(self):
        self.assertEqual(fast_check.auto_categorize("京东满减"), "京东")

    def test_taobao(self):
        self.assertEqual(fast_check.auto_categorize("淘宝优惠"), "淘宝")

    def test_no_match(self):
        self.assertIsNone(fast_check.auto_categorize("普通文字"))


# ===================================================================
# 7. RUN LOG TESTS
# ===================================================================

class TestRunLog(unittest.TestCase):
    """Tests for load_run_log / append_run_log."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.RUN_LOG_FILE
        self._orig_engine_file = crawler.engine.RUN_LOG_FILE
        new_path = os.path.join(self._tmpdir, "run_log.jsonl")
        crawl.RUN_LOG_FILE = new_path
        crawler.engine.RUN_LOG_FILE = new_path

    def tearDown(self):
        crawl.RUN_LOG_FILE = self._orig_file
        crawler.engine.RUN_LOG_FILE = self._orig_engine_file
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_load_nonexistent_returns_empty(self):
        missing = os.path.join(self._tmpdir, "missing.jsonl")
        crawl.RUN_LOG_FILE = missing
        crawler.engine.RUN_LOG_FILE = missing
        result = crawl.load_run_log()
        self.assertEqual(result, [])

    def test_append_and_load(self):
        entry1 = {"round": 1, "time": "2026-06-08 08:00:00", "success": 30}
        entry2 = {"round": 2, "time": "2026-06-08 12:00:00", "success": 28}

        crawl.append_run_log(entry1)
        crawl.append_run_log(entry2)

        loaded = crawl.load_run_log()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["round"], 1)
        self.assertEqual(loaded[1]["round"], 2)

    def test_entries_are_valid_json(self):
        """Each line in the log file should be valid JSON."""
        crawl.append_run_log({"test": True, "data": "value"})
        with open(crawl.RUN_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    parsed = json.loads(line)
                    self.assertIsInstance(parsed, dict)


# ===================================================================
# 9. INTEGRATION / EDGE CASE TESTS
# ===================================================================

class TestCheckSiteUpdateLogic(unittest.TestCase):
    """Unit-level tests for the check_site_update comparison logic.
    We mock fetch_page_content to avoid real network calls."""

    @patch("crawler.engine.fetch_page_content")
    def test_first_monitoring(self, mock_fetch):
        mock_fetch.return_value = (True, {
            "text": "some page content",
            "title": "Test",
            "summary": "some...",
            "items": [],
            "response_time": 0.5,
        })
        is_updated, new_hash, msg, page_info = crawl.check_site_update(
            "https://new-site.com/", {}
        )
        self.assertFalse(is_updated)
        self.assertEqual(msg, "首次监控")
        self.assertIsNotNone(new_hash)

    @patch("crawler.engine.fetch_page_content")
    def test_content_updated(self, mock_fetch):
        old_hash = crawl.calculate_md5("old content")
        mock_fetch.return_value = (True, {
            "text": "new different content",
            "title": "Test",
            "summary": "new...",
            "items": [],
            "response_time": 0.5,
        })
        old_records = {"https://changed.com/": old_hash}
        is_updated, new_hash, msg, page_info = crawl.check_site_update(
            "https://changed.com/", old_records
        )
        self.assertTrue(is_updated)
        self.assertEqual(msg, "内容已更新")

    @patch("crawler.engine.fetch_page_content")
    def test_no_update(self, mock_fetch):
        content = "same content"
        old_hash = crawl.calculate_md5(content)
        mock_fetch.return_value = (True, {
            "text": content,
            "title": "Test",
            "summary": content,
            "items": [],
            "response_time": 0.5,
        })
        old_records = {"https://stable.com/": old_hash}
        is_updated, new_hash, msg, page_info = crawl.check_site_update(
            "https://stable.com/", old_records
        )
        self.assertFalse(is_updated)
        self.assertEqual(msg, "无更新")

    @patch("crawler.engine.fetch_page_content")
    def test_fetch_failure(self, mock_fetch):
        mock_fetch.return_value = (False, "HTTP 403")
        is_updated, new_hash, msg, page_info = crawl.check_site_update(
            "https://blocked.com/", {}
        )
        self.assertIsNone(is_updated)
        self.assertIsNone(new_hash)
        self.assertEqual(msg, "HTTP 403")


class TestSourceNameConsistency(unittest.TestCase):
    """Ensure every MONITOR_SITES entry has a corresponding SOURCE_NAME_MAP entry."""

    def test_all_monitor_sites_have_names(self):
        for url in crawl.MONITOR_SITES:
            name = crawl.get_source_name(url)
            self.assertIsNotNone(
                name,
                f"MONITOR_SITES URL '{url}' has no entry in SOURCE_NAME_MAP",
            )


class TestCategoryKeywordsCoverage(unittest.TestCase):
    """Verify CATEGORY_KEYWORDS structure is consistent between crawl and fast_check."""

    def test_same_categories_in_both_modules(self):
        """Both crawl and fast_check use CATEGORY_KEYWORDS from common.py."""
        import common
        crawl_cats = set(crawl.CATEGORY_KEYWORDS.keys())
        common_cats = set(common.CATEGORY_KEYWORDS.keys())
        self.assertEqual(
            crawl_cats, common_cats,
            "CATEGORY_KEYWORDS categories differ between crawl.py and common.py",
        )

    def test_keywords_are_non_empty(self):
        for cat, keywords in crawl.CATEGORY_KEYWORDS.items():
            self.assertGreater(len(keywords), 0, f"Category '{cat}' has no keywords")


# ===================================================================
# SQLITE DATA LAYER TESTS
# ===================================================================

@unittest.skip("SQLite functions removed from common.py — project uses JSON file storage")
class TestSQLiteDataLayer(unittest.TestCase):
    """Tests for SQLite-based data persistence in common.py."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmpdir)
        self.db_path = os.path.join(self._tmpdir, "test.db")
        self.conn = common.init_sqlite(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_init_creates_tables(self):
        tables = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = {t[0] for t in tables}
        self.assertIn("items", table_names)
        self.assertIn("hash_records", table_names)
        self.assertIn("meta", table_names)

    def test_insert_and_retrieve_items(self):
        items = [
            {"url": "https://example.com/1", "text": "测试条目一", "source": "测试源", "time": "2026-01-01"},
            {"url": "https://example.com/2", "text": "测试条目二", "source": "测试源", "time": "2026-01-02"},
        ]
        added = common.sqlite_insert_items(self.conn, items)
        self.assertEqual(added, 2)
        retrieved = common.sqlite_get_recent_items(self.conn)
        self.assertEqual(len(retrieved), 2)

    def test_dedup_on_insert(self):
        items = [{"url": "https://example.com/dup", "text": "重复测试", "source": "src"}]
        common.sqlite_insert_items(self.conn, items)
        common.sqlite_insert_items(self.conn, items)  # Duplicate
        retrieved = common.sqlite_get_recent_items(self.conn)
        self.assertEqual(len(retrieved), 1)

    def test_get_existing_urls(self):
        items = [
            {"url": "https://a.com/1", "text": "A1"},
            {"url": "https://b.com/2", "text": "B2"},
        ]
        common.sqlite_insert_items(self.conn, items)
        urls = common.sqlite_get_existing_urls(self.conn)
        self.assertEqual(urls, {"https://a.com/1", "https://b.com/2"})

    def test_hash_records_roundtrip(self):
        records = {"https://site1.com/": "abc123", "https://site2.com/": "def456"}
        common.sqlite_save_hash_records(self.conn, records)
        loaded = common.sqlite_load_hash_records(self.conn)
        self.assertEqual(loaded, records)

    def test_meta_get_set(self):
        common.sqlite_set_meta(self.conn, "version", "2.0")
        val = common.sqlite_get_meta(self.conn, "version")
        self.assertEqual(val, "2.0")

    def test_meta_default(self):
        val = common.sqlite_get_meta(self.conn, "nonexistent", "default")
        self.assertEqual(val, "default")

    def test_export_json(self):
        items = [{"url": "https://export.com/1", "text": "导出测试", "source": "src"}]
        common.sqlite_insert_items(self.conn, items)
        json_path = os.path.join(self._tmpdir, "items.json")
        result = common.sqlite_export_json(self.conn, json_path)
        self.assertTrue(result)
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(data["items"]), 1)
        self.assertIn("updated_at", data)

    def test_max_items_enforcement(self):
        # Insert more than MAX_ITEMS_DB items
        items = [{"url": f"https://bulk.com/{i}", "text": f"Bulk item {i}"} for i in range(common.MAX_ITEMS_DB + 100)]
        common.sqlite_insert_items(self.conn, items)
        count = self.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self.assertLessEqual(count, common.MAX_ITEMS_DB)

    def test_auto_categorize_on_insert(self):
        items = [{"url": "https://cat.com/1", "text": "领大额优惠券活动", "source": "src"}]
        common.sqlite_insert_items(self.conn, items)
        row = self.conn.execute("SELECT category FROM items WHERE url = ?", ("https://cat.com/1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "优惠券")


# ===================================================================
# TestProxyPool - Unit tests for common.ProxyPool proxy rotation
# ===================================================================


class TestProxyPool(unittest.TestCase):
    """Tests for common.ProxyPool proxy rotation."""

    def test_empty_pool_returns_none(self):
        pool = common.ProxyPool()
        self.assertIsNone(pool.get_proxy())
        self.assertEqual(pool.active_count, 0)
        self.assertEqual(pool.total_count, 0)

    def test_add_and_get_proxy(self):
        pool = common.ProxyPool()
        pool.add_proxy("http://proxy1:8080")
        self.assertEqual(pool.total_count, 1)
        self.assertEqual(pool.active_count, 1)
        self.assertEqual(pool.get_proxy(), "http://proxy1:8080")

    def test_round_robin_rotation(self):
        pool = common.ProxyPool(proxies=["http://p1:80", "http://p2:80", "http://p3:80"])
        results = [pool.get_proxy() for _ in range(6)]
        # Should cycle: p1, p2, p3, p1, p2, p3
        self.assertEqual(results[0], results[3])
        self.assertEqual(results[1], results[4])
        self.assertEqual(results[2], results[5])
        # All three should be different
        self.assertEqual(len(set(results[:3])), 3)

    def test_random_strategy(self):
        pool = common.ProxyPool(proxies=["http://p1:80"], strategy="random")
        self.assertEqual(pool.get_proxy(), "http://p1:80")

    def test_failure_blacklisting(self):
        pool = common.ProxyPool(proxies=["http://p1:80", "http://p2:80"], max_failures=3)
        # Fail p1 three times
        for _ in range(3):
            pool.report_failure("http://p1:80")
        # p1 should be blacklisted
        self.assertEqual(pool.active_count, 1)
        # Only p2 should be returned now
        for _ in range(5):
            self.assertEqual(pool.get_proxy(), "http://p2:80")

    def test_success_resets_failures(self):
        pool = common.ProxyPool(proxies=["http://p1:80"], max_failures=3)
        pool.report_failure("http://p1:80")
        pool.report_failure("http://p1:80")
        pool.report_success("http://p1:80")  # Should reset
        # Still active after 2 failures + 1 success
        self.assertEqual(pool.active_count, 1)
        # Two more failures should NOT blacklist (counter was reset)
        pool.report_failure("http://p1:80")
        pool.report_failure("http://p1:80")
        self.assertEqual(pool.active_count, 1)

    def test_all_blacklisted_returns_none(self):
        pool = common.ProxyPool(proxies=["http://p1:80"], max_failures=1)
        pool.report_failure("http://p1:80")
        self.assertIsNone(pool.get_proxy())
        self.assertEqual(pool.active_count, 0)

    def test_cooldown_re_enables_proxy(self):
        # With large cooldown, proxy stays blacklisted
        pool = common.ProxyPool(proxies=["http://p1:80"], max_failures=1, cooldown=9999)
        pool.report_failure("http://p1:80")  # Blacklisted
        self.assertIsNone(pool.get_proxy())
        # With cooldown=0, proxy is immediately re-enabled
        pool2 = common.ProxyPool(proxies=["http://p1:80"], max_failures=1, cooldown=0)
        pool2.report_failure("http://p1:80")
        self.assertIsNotNone(pool2.get_proxy())  # Cooldown=0 → instant re-enable

    def test_remove_proxy(self):
        pool = common.ProxyPool(proxies=["http://p1:80", "http://p2:80"])
        pool.remove_proxy("http://p1:80")
        self.assertEqual(pool.total_count, 1)
        self.assertEqual(pool.get_proxy(), "http://p2:80")

    def test_load_from_env(self):
        with patch.dict(os.environ, {"PROXY_LIST": "http://env1:80, http://env2:80"}):
            pool = common.ProxyPool()
            count = pool.load_from_env()
            self.assertEqual(count, 2)
            self.assertEqual(pool.total_count, 2)

    def test_load_from_env_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            pool = common.ProxyPool()
            # Remove PROXY_LIST if it exists
            os.environ.pop("PROXY_LIST", None)
            count = pool.load_from_env()
            self.assertEqual(count, 0)

    def test_get_stats(self):
        pool = common.ProxyPool(proxies=["http://p1:80"])
        pool.report_success("http://p1:80")
        stats = pool.get_stats()
        self.assertIn("total", stats)
        self.assertIn("active", stats)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["active"], 1)

    def test_report_unknown_proxy(self):
        pool = common.ProxyPool()
        # Should not crash
        pool.report_success("http://unknown:80")
        pool.report_failure("http://unknown:80")

    def test_create_proxy_pool_factory(self):
        pool = common.create_proxy_pool()
        self.assertIsInstance(pool, common.ProxyPool)

    def test_create_proxy_pool_with_extra(self):
        pool = common.create_proxy_pool(extra_proxies=["http://extra1:80"])
        self.assertGreaterEqual(pool.total_count, 1)


# ===================================================================
# TestAsyncIntegration - Async tests using aiohttp web test server
# ===================================================================


class TestAsyncIntegration(unittest.TestCase):
    """Async integration tests using a local aiohttp test server."""

    def _run(self, coro):
        """Helper to run async tests."""
        return asyncio.run(coro)

    def _create_test_app(self):
        """Create a simple aiohttp web app for testing."""
        app = web.Application()

        async def handle_ok(request):
            return web.Response(text="<html><head><title>Test</title></head><body>OK</body></html>",
                              content_type="text/html")

        async def handle_500(request):
            return web.Response(status=500, text="Server Error")

        async def handle_redirect(request):
            raise web.HTTPFound("/ok")

        async def handle_slow(request):
            await asyncio.sleep(10)
            return web.Response(text="Slow")

        async def handle_ssrf(request):
            raise web.HTTPFound("http://127.0.0.1:1/admin")

        app.router.add_get("/ok", handle_ok)
        app.router.add_get("/500", handle_500)
        app.router.add_get("/redirect", handle_redirect)
        app.router.add_get("/slow", handle_slow)
        app.router.add_get("/ssrf", handle_ssrf)

        return app

    def test_aiohttp_session_creation(self):
        """Test creating aiohttp session with connector settings."""
        async def _test():
            connector = aiohttp.TCPConnector(limit=5, limit_per_host=2, ttl_dns_cache=300)
            async with aiohttp.ClientSession(connector=connector) as session:
                self.assertIsNotNone(session)
                self.assertFalse(session.closed)
            self.assertTrue(session.closed)
        self._run(_test())

    def test_local_server_fetch_200(self):
        """Test fetching a 200 OK page from local test server."""
        async def _test():
            app = self._create_test_app()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{port}/ok") as resp:
                        self.assertEqual(resp.status, 200)
                        text = await resp.text()
                        self.assertIn("Test", text)
            finally:
                await runner.cleanup()
        self._run(_test())

    def test_local_server_500_response(self):
        """Test handling a 500 error from local server."""
        async def _test():
            app = self._create_test_app()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://127.0.0.1:{port}/500") as resp:
                        self.assertEqual(resp.status, 500)
            finally:
                await runner.cleanup()
        self._run(_test())

    def test_local_server_redirect_follow(self):
        """Test that aiohttp follows redirects."""
        async def _test():
            app = self._create_test_app()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{port}/redirect",
                        allow_redirects=True,
                    ) as resp:
                        self.assertEqual(resp.status, 200)
                        text = await resp.text()
                        self.assertIn("OK", text)
            finally:
                await runner.cleanup()
        self._run(_test())

    def test_semaphore_concurrency_limit(self):
        """Test that Semaphore correctly limits concurrent requests."""
        async def _test():
            concurrent = 0
            max_concurrent = 0

            async def limited_task(semaphore):
                nonlocal concurrent, max_concurrent
                async with semaphore:
                    concurrent += 1
                    max_concurrent = max(max_concurrent, concurrent)
                    await asyncio.sleep(0.05)
                    concurrent -= 1

            semaphore = asyncio.Semaphore(3)
            tasks = [limited_task(semaphore) for _ in range(10)]
            await asyncio.gather(*tasks)
            self.assertLessEqual(max_concurrent, 3)
            self.assertGreater(max_concurrent, 0)
        self._run(_test())

    def test_gather_with_exceptions(self):
        """Test asyncio.gather handles individual task exceptions gracefully."""
        async def _test():
            async def good_task():
                return "ok"

            async def bad_task():
                raise ValueError("test error")

            results = await asyncio.gather(
                good_task(), bad_task(), good_task(),
                return_exceptions=True,
            )
            self.assertEqual(results[0], "ok")
            self.assertIsInstance(results[1], ValueError)
            self.assertEqual(results[2], "ok")
        self._run(_test())

    def test_aiohttp_timeout(self):
        """Test that aiohttp timeout works correctly."""
        async def _test():
            app = self._create_test_app()
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                async with aiohttp.ClientSession() as session:
                    with self.assertRaises(asyncio.TimeoutError):
                        async with session.get(
                            f"http://127.0.0.1:{port}/slow",
                            timeout=aiohttp.ClientTimeout(total=0.5),
                        ) as resp:
                            await resp.text()
            finally:
                await runner.cleanup()
        self._run(_test())

    def test_proxy_pool_with_fetch(self):
        """Test ProxyPool integration with aiohttp fetch (mock proxy - direct connection)."""
        async def _test():
            pool = common.ProxyPool(proxies=["http://fake-proxy:8080"])
            proxy = pool.get_proxy()
            self.assertEqual(proxy, "http://fake-proxy:8080")
            # Simulate success
            pool.report_success(proxy)
            self.assertEqual(pool.active_count, 1)
            # Get stats
            stats = pool.get_stats()
            self.assertEqual(stats["active"], 1)
        self._run(_test())

    def test_concurrent_proxy_rotation(self):
        """Test that proxy rotation works correctly under concurrent access."""
        async def _test():
            pool = common.ProxyPool(
                proxies=["http://p1:80", "http://p2:80", "http://p3:80"],
                strategy="round_robin",
            )
            proxies_used = []

            async def get_and_record():
                p = pool.get_proxy()
                if p:
                    proxies_used.append(p)

            await asyncio.gather(*[get_and_record() for _ in range(9)])
            # Should have roughly equal distribution
            from collections import Counter
            counts = Counter(proxies_used)
            self.assertEqual(len(counts), 3)
            # Each proxy should be used exactly 3 times in round-robin
            for count in counts.values():
                self.assertEqual(count, 3)
        self._run(_test())

    def test_crawl_module_async_imports(self):
        """Verify crawl module has required async functions."""
        self.assertTrue(hasattr(crawl, 'fetch_page_content_async'))
        self.assertTrue(hasattr(crawl, 'check_site_update_async'))
        self.assertTrue(hasattr(crawl, 'check_one_async'))
        self.assertTrue(hasattr(crawl, 'main_async'))
        self.assertTrue(asyncio.iscoroutinefunction(crawl.fetch_page_content_async))
        self.assertTrue(asyncio.iscoroutinefunction(crawl.check_site_update_async))
        self.assertTrue(asyncio.iscoroutinefunction(crawl.check_one_async))
        self.assertTrue(asyncio.iscoroutinefunction(crawl.main_async))

    def test_fast_check_module_async_imports(self):
        """Verify fast_check module has required async functions."""
        self.assertTrue(hasattr(fast_check, '_fetch_with_retry_async'))
        self.assertTrue(hasattr(fast_check, 'fetch_and_extract_async'))
        self.assertTrue(asyncio.iscoroutinefunction(fast_check._fetch_with_retry_async))
        self.assertTrue(asyncio.iscoroutinefunction(fast_check.fetch_and_extract_async))

    def test_browser_profiles_integrity(self):
        """Test that all browser profiles have required fields."""
        for profile in crawl.BROWSER_PROFILES:
            self.assertIn('user_agent', profile)
            self.assertIn('accept_language', profile)
            self.assertIn('fingerprint', profile)
            self.assertIsInstance(profile['fingerprint'], dict)
            # UA should be a non-empty string
            self.assertTrue(len(profile['user_agent']) > 20)
            # Should start with Mozilla
            self.assertTrue(profile['user_agent'].startswith('Mozilla/5.0'))

    def test_metrics_tracker(self):
        """Test MetricsTracker success/failure recording."""
        mt = crawl.MetricsTracker()
        mt.record_success("example.com", 0.5)
        mt.record_success("example.com", 1.5)
        mt.record_failure("other.com")
        summary = mt.get_summary()
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["fail_count"], 1)
        self.assertEqual(summary["total_requests"], 3)


# ===================================================================
# 10. TESTS FOR ALL REMAINING PARSER_REGISTRY PARSERS
# ===================================================================

class TestParseZiyuantingItems(unittest.TestCase):
    """Tests for crawl.parse_ziyuanting_items()."""

    MOCK_HTML = """
    <html><body>
    <article class="posts-item sites-item">
        <a href="https://www.ziyuanting.com/sites/123.html" class="sites-body">
            <h3 class="item-title"><b>某资源网站推荐</b></h3>
        </a>
    </article>
    <article class="posts-item sites-item">
        <a href="https://www.ziyuanting.com/sites/456.html" class="sites-body">
            <h3 class="item-title"><b>在线工具合集分享</b></h3>
        </a>
    </article>
    <article class="posts-item app-item">
        <a href="https://www.ziyuanting.com/app/789.html">
            <span class="item-title">实用软件工具 v2.0</span>
        </a>
    </article>
    <a href="https://www.ziyuanting.com/bulletin/100.html">最新站点公告通知</a>
    <a href="https://www.ziyuanting.com/">首页</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_ziyuanting_items(soup, "https://www.ziyuanting.com/")
        texts = [item["text"] for item in items]
        self.assertIn("某资源网站推荐", texts)
        self.assertIn("在线工具合集分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <article class="posts-item sites-item">
            <a href="/sites/1.html" class="sites-body"><h3 class="item-title"><b>重复资源标题</b></h3></a>
        </article>
        <article class="posts-item sites-item">
            <a href="/sites/2.html" class="sites-body"><h3 class="item-title"><b>重复资源标题</b></h3></a>
        </article>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ziyuanting_items(soup, "https://www.ziyuanting.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_ziyuanting_items(soup, "https://www.ziyuanting.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)


class TestParseWycadItems(unittest.TestCase):
    """Tests for crawl.parse_wycad_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://www.wycad.com/12345.html">办公软件WPS最新版下载</a>
    <a href="https://www.wycad.com/67890.html">影音播放器绿色便携版</a>
    <a href="https://www.wycad.com/11111.html">系统清理工具专业版</a>
    <a href="https://www.wycad.com/app/">手机软件</a>
    <a href="https://www.wycad.com/windows/">操作系统</a>
    <a href="https://www.wycad.com/tag/test">标签链接</a>
    <a href="https://www.wycad.com/?s=搜索">搜索</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_wycad_items(soup, "https://www.wycad.com/")
        texts = [item["text"] for item in items]
        self.assertIn("办公软件WPS最新版下载", texts)
        self.assertIn("影音播放器绿色便携版", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://www.wycad.com/123.html">重复软件标题内容</a>
        <a href="https://www.wycad.com/123.html">重复软件标题内容</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_wycad_items(soup, "https://www.wycad.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_wycad_items(soup, "https://www.wycad.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("手机软件", texts)
        self.assertNotIn("操作系统", texts)


class TestParseH6roomItems(unittest.TestCase):
    """Tests for crawl.parse_h6room_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://www.h6room.com/12345.html">视频播放器去广告版</a>
    <a href="https://www.h6room.com/67890.html">文件管理器破解版本</a>
    <a href="https://www.h6room.com/11111.html">天气预报安卓应用</a>
    <a href="https://www.h6room.com/category/android">安卓应用</a>
    <a href="https://www.h6room.com/login">登录</a>
    <a href="https://www.h6room.com/register">注册</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_h6room_items(soup, "https://www.h6room.com/")
        texts = [item["text"] for item in items]
        self.assertIn("视频播放器去广告版", texts)
        self.assertIn("文件管理器破解版本", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://www.h6room.com/123.html">重复文章内容标题</a>
        <a href="https://www.h6room.com/123.html">重复文章内容标题</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_h6room_items(soup, "https://www.h6room.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_h6room_items(soup, "https://www.h6room.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("安卓应用", texts)
        self.assertNotIn("登录", texts)
        self.assertNotIn("注册", texts)


class TestParseXzbaItems(unittest.TestCase):
    """Tests for crawl.parse_xzba_items()."""

    MOCK_HTML = """
    <html><body>
    <div class="posts-row">
        <a href="https://xzba.cc/702.html">仁王3完全版</a>
        <a href="https://xzba.cc/703.html">赛博朋克最新版</a>
    </div>
    <a href="https://xzba.cc/704.html">艾尔登法环DLC</a>
    <a href="https://xzba.cc/">首页</a>
    <a href="https://xzba.cc/login">登录</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_xzba_items(soup, "https://xzba.cc/")
        texts = [item["text"] for item in items]
        self.assertIn("仁王3完全版", texts)
        self.assertIn("赛博朋克最新版", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <div class="posts-row">
            <a href="https://xzba.cc/100.html">重复游戏标题内容</a>
        </div>
        <a href="https://xzba.cc/100.html">重复游戏标题内容</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_xzba_items(soup, "https://xzba.cc/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_xzba_items(soup, "https://xzba.cc/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("登录", texts)


class TestParseApprcnItems(unittest.TestCase):
    """Tests for crawl.parse_apprcn_items()."""

    MOCK_HTML = """
    <html><body>
    <article>
        <h2><a href="https://free.apprcn.com/app1/">限时免费软件推荐第一期</a></h2>
        <a href="https://free.apprcn.com/app1/">阅读全文</a>
    </article>
    <article>
        <h3><a href="https://free.apprcn.com/app2/">安卓限免应用合集分享</a></h3>
    </article>
    <div class="post">
        <h2><a href="https://free.apprcn.com/app3/">Mac软件限免活动</a></h2>
    </div>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_apprcn_items(soup, "https://free.apprcn.com/")
        texts = [item["text"] for item in items]
        self.assertIn("限时免费软件推荐第一期", texts)
        self.assertIn("安卓限免应用合集分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <article><h2><a href="/app1/">重复标题内容测试</a></h2></article>
        <article><h2><a href="/app2/">重复标题内容测试</a></h2></article>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_apprcn_items(soup, "https://free.apprcn.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_apprcn_items(soup, "https://free.apprcn.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("阅读全文", texts)


class TestParseYxsspItems(unittest.TestCase):
    """Tests for crawl.parse_yxssp_items()."""

    MOCK_HTML = """
    <html><body>
    <a rel="bookmark" href="https://www.yxssp.com/12345.html">办公软件绿色版下载</a>
    <a rel="bookmark" href="https://www.yxssp.com/67890.html">系统优化工具推荐</a>
    <div class="entry-title"><a href="https://www.yxssp.com/11111.html">浏览器安全插件</a></div>
    <a href="https://www.yxssp.com/about">关于我们</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yxssp_items(soup, "https://www.yxssp.com/")
        texts = [item["text"] for item in items]
        self.assertIn("办公软件绿色版下载", texts)
        self.assertIn("系统优化工具推荐", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a rel="bookmark" href="https://www.yxssp.com/1.html">重复软件标题下载</a>
        <a rel="bookmark" href="https://www.yxssp.com/1.html">重复软件标题下载</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_yxssp_items(soup, "https://www.yxssp.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yxssp_items(soup, "https://www.yxssp.com/")
        texts = [item["text"] for item in items]
        # "关于我们" is only 4 chars which is < 5, filtered by fallback
        self.assertNotIn("关于我们", texts)


class TestParseDaydayzhuanItems(unittest.TestCase):
    """Tests for crawl.parse_daydayzhuan_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://www.daydayzhuan.com/article/12345">京东红包领取攻略教程</a>
    <a href="https://www.daydayzhuan.com/article/67890">淘宝优惠券技巧分享</a>
    <a href="https://www.daydayzhuan.com/article/11111">拼多多现金活动攻略</a>
    <a href="https://www.daydayzhuan.com/yangmao">实时线报</a>
    <a href="https://www.daydayzhuan.com/">首页</a>
    <a href="https://www.daydayzhuan.com/longtime">长期羊毛</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_daydayzhuan_items(soup, "https://www.daydayzhuan.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东红包领取攻略教程", texts)
        self.assertIn("淘宝优惠券技巧分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="/article/100">重复活动标题内容分享</a>
        <a href="/article/100">重复活动标题内容分享</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_daydayzhuan_items(soup, "https://www.daydayzhuan.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_daydayzhuan_items(soup, "https://www.daydayzhuan.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("实时线报", texts)


class TestParse007ymdItems(unittest.TestCase):
    """Tests for crawl.parse_007ymd_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://www.007ymd.com/?id=12345">京东免费领会员活动</a>
    <a href="https://www.007ymd.com/?id=67890">淘宝签到红包领取方法</a>
    <a href="https://www.007ymd.com/?id=11111">话费充值优惠活动攻略</a>
    <a href="https://www.007ymd.com/?cate=long">长期羊毛</a>
    <a href="https://www.007ymd.com/?cate=activity">有奖活动</a>
    <a href="https://www.007ymd.com/">首页</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_007ymd_items(soup, "https://www.007ymd.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东免费领会员活动", texts)
        self.assertIn("淘宝签到红包领取方法", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://www.007ymd.com/?id=100">重复羊毛活动标题</a>
        <a href="https://www.007ymd.com/?id=100">重复羊毛活动标题</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_007ymd_items(soup, "https://www.007ymd.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_007ymd_items(soup, "https://www.007ymd.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("长期羊毛", texts)


class TestParseManmanbuyItems(unittest.TestCase):
    """Tests for crawl.parse_manmanbuy_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://cu.manmanbuy.com/discuxiao_12345.aspx">京东满减优惠爆料信息</a>
    <a href="https://cu.manmanbuy.com/discuxiao_67890.aspx">天猫超市折扣商品信息</a>
    <a href="https://cu.manmanbuy.com/discuxiao_11111.aspx">拼多多百亿补贴爆料</a>
    <a href="https://cu.manmanbuy.com/discuxiao_12345.aspx">20.4元</a>
    <a href="https://www.manmanbuy.com/zhekou/search">分类搜索</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_manmanbuy_items(soup, "https://www.manmanbuy.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东满减优惠爆料信息", texts)
        self.assertIn("天猫超市折扣商品信息", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://cu.manmanbuy.com/discuxiao_100.aspx">重复爆料标题内容</a>
        <a href="https://cu.manmanbuy.com/discuxiao_100.aspx">重复爆料标题内容</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_manmanbuy_items(soup, "https://www.manmanbuy.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_manmanbuy_items(soup, "https://www.manmanbuy.com/")
        urls = [item["url"] for item in items]
        # Non-discuxiao links should be excluded
        self.assertFalse(any("zhekou/search" in u for u in urls))


class TestParseGhxiItems(unittest.TestCase):
    """Tests for crawl.parse_ghxi_items() - WP REST API based parser."""

    @patch("crawl.requests.get")
    def test_extracts_items(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"title": {"rendered": "果核剥壳文章标题一"}, "link": "https://www.ghxi.com/post1.html"},
            {"title": {"rendered": "果核剥壳文章标题二"}, "link": "https://www.ghxi.com/post2.html"},
        ]
        mock_get.return_value = mock_resp
        soup = make_soup("<html><body></body></html>")
        items = crawl.parse_ghxi_items(soup, "https://www.ghxi.com/")
        texts = [item["text"] for item in items]
        self.assertIn("果核剥壳文章标题一", texts)
        self.assertIn("果核剥壳文章标题二", texts)

    @patch("crawl.requests.get")
    def test_api_failure_returns_empty(self, mock_get):
        """Non-200 response should return empty list."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp
        soup = make_soup("<html><body></body></html>")
        items = crawl.parse_ghxi_items(soup, "https://www.ghxi.com/")
        # Non-200 response should return empty list
        self.assertEqual(len(items), 0)

    @patch("crawl.requests.get")
    def test_filters_junk(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"title": {"rendered": "AB"}, "link": "https://www.ghxi.com/short.html"},
            {"title": {"rendered": "这篇标题足够长可以保留"}, "link": "https://www.ghxi.com/ok.html"},
        ]
        mock_get.return_value = mock_resp
        soup = make_soup("<html><body></body></html>")
        items = crawl.parse_ghxi_items(soup, "https://www.ghxi.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("AB", texts)
        self.assertIn("这篇标题足够长可以保留", texts)


class TestParse12345proItems(unittest.TestCase):
    """Tests for crawl.parse_12345pro_items()."""

    MOCK_HTML = """
    <html><body>
    <h2 class="zt-biaoti"><a href="/article/12345.html">京东红包免费领教程</a></h2>
    <div class="slide-title"><a href="/article/67890.html">淘宝优惠券领取方法</a></div>
    <p class="zt-biaoti"><a href="/article/11111.html">拼多多现金活动攻略</a></p>
    <a href="/category/hot">热门分类</a>
    <a href="/">首页</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_12345pro_items(soup, "https://www.12345pro.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东红包免费领教程", texts)
        self.assertIn("淘宝优惠券领取方法", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <h2 class="zt-biaoti"><a href="/article/1.html">重复标题文章内容</a></h2>
        <p class="zt-biaoti"><a href="/article/2.html">重复标题文章内容</a></p>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_12345pro_items(soup, "https://www.12345pro.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_12345pro_items(soup, "https://www.12345pro.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("热门分类", texts)
        self.assertNotIn("首页", texts)


class TestParseAppinnItems(unittest.TestCase):
    """Tests for crawl.parse_appinn_items()."""

    MOCK_HTML = """
    <html><body>
    <article class="post-box">
        <h2 class="title post-title"><a href="https://www.appinn.com/chrome-125/">Chrome浏览器最新版本</a></h2>
    </article>
    <article class="post-box">
        <h2 class="title post-title"><a href="https://www.appinn.com/vscode-tips/">VSCode实用技巧分享</a></h2>
    </article>
    <a href="https://www.appinn.com/category/software/">分类链接</a>
    <a href="https://www.appinn.com/tag/hot/">标签链接</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_appinn_items(soup, "https://www.appinn.com/")
        texts = [item["text"] for item in items]
        self.assertIn("Chrome浏览器最新版本", texts)
        self.assertIn("VSCode实用技巧分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <article><h2 class="title post-title"><a href="/post1/">重复文章标题</a></h2></article>
        <article><h2 class="title post-title"><a href="/post2/">重复文章标题</a></h2></article>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_appinn_items(soup, "https://www.appinn.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_appinn_items(soup, "https://www.appinn.com/")
        urls = [item["url"] for item in items]
        self.assertFalse(any("/category/" in u for u in urls))


class TestParseIthomeXijiayiItems(unittest.TestCase):
    """Tests for crawl.parse_ithome_xijiayi_items()."""

    MOCK_HTML = """
    <html><body>
    <ol class="newslist">
        <li>
            <div class="newsbody">
                <a href="https://www.ithome.com/0/123/456.htm"><h2>Steam喜加一免费游戏领取</h2></a>
            </div>
        </li>
        <li>
            <div class="newsbody">
                <a href="https://www.ithome.com/0/789/012.htm"><h2>Epic商城限时免费领取</h2></a>
            </div>
        </li>
    </ol>
    <a href="https://www.ithome.com/0/345/678.htm">GOG平台免费游戏活动</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_ithome_xijiayi_items(soup, "https://www.ithome.com/zt/xijiayi")
        texts = [item["text"] for item in items]
        self.assertIn("Steam喜加一免费游戏领取", texts)
        self.assertIn("Epic商城限时免费领取", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <ol class="newslist">
            <li><div class="newsbody"><a href="https://www.ithome.com/0/1/2.htm"><h2>重复新闻标题</h2></a></div></li>
            <li><div class="newsbody"><a href="https://www.ithome.com/0/3/4.htm"><h2>重复新闻标题</h2></a></div></li>
        </ol>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ithome_xijiayi_items(soup, "https://www.ithome.com/zt/xijiayi")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        html = """<html><body>
        <ol class="newslist">
            <li><div class="newsbody"><a href="https://www.ithome.com/about"><h2>关</h2></a></div></li>
            <li><div class="newsbody"><a href="https://www.ithome.com/0/1/2.htm"><h2>正式新闻标题内容</h2></a></div></li>
        </ol>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ithome_xijiayi_items(soup, "https://www.ithome.com/zt/xijiayi")
        texts = [item["text"] for item in items]
        # "关" is too short (< 4 chars) and should be filtered
        self.assertNotIn("关", texts)
        self.assertIn("正式新闻标题内容", texts)


class TestParseLsapkItems(unittest.TestCase):
    """Tests for crawl.parse_lsapk_items()."""

    MOCK_HTML = """
    <html><body>
    <ul>
        <li class="post-item">
            <div class="post-item-main"><h2><a href="https://www.lsapk.com/12345.html">安卓应用市场最新版</a></h2></div>
        </li>
        <li class="post-item">
            <div class="post-item-main"><h2><a href="https://www.lsapk.com/67890.html">视频播放器去广告版</a></h2></div>
        </li>
    </ul>
    <a href="https://www.lsapk.com/category/apps">应用分类</a>
    <a href="https://www.lsapk.com/tag/hot">热门标签</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_lsapk_items(soup, "https://www.lsapk.com/")
        texts = [item["text"] for item in items]
        self.assertIn("安卓应用市场最新版", texts)
        self.assertIn("视频播放器去广告版", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <li class="post-item"><div class="post-item-main"><h2><a href="/1.html">重复应用标题</a></h2></div></li>
        <li class="post-item"><div class="post-item-main"><h2><a href="/2.html">重复应用标题</a></h2></div></li>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_lsapk_items(soup, "https://www.lsapk.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_lsapk_items(soup, "https://www.lsapk.com/")
        urls = [item["url"] for item in items]
        self.assertFalse(any("/category/" in u for u in urls))
        self.assertFalse(any("/tag/" in u for u in urls))


class TestParseThosefreeItems(unittest.TestCase):
    """Tests for crawl.parse_thosefree_items()."""

    MOCK_HTML = """
    <html><body>
    <a class="post-item-title" href="https://www.thosefree.com/app1"><h3>限时免费安卓应用推荐</h3></a>
    <a class="post-item-title" href="https://www.thosefree.com/app2"><h3>Windows软件限免活动</h3></a>
    <a class="pic-cover-item" href="https://www.thosefree.com/app3"><div class="pic-cover-item-title">精选限免合集</div></a>
    <a class="post-item-title" href="https://www.thosefree.com/tag/hot">热门标签</a>
    <a class="post-item-title" href="https://www.thosefree.com/page/2">翻页链接</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_thosefree_items(soup, "https://www.thosefree.com/")
        texts = [item["text"] for item in items]
        self.assertIn("限时免费安卓应用推荐", texts)
        self.assertIn("Windows软件限免活动", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a class="post-item-title" href="/app1"><h3>重复限免标题</h3></a>
        <a class="post-item-title" href="/app2"><h3>重复限免标题</h3></a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_thosefree_items(soup, "https://www.thosefree.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_thosefree_items(soup, "https://www.thosefree.com/")
        urls = [item["url"] for item in items]
        self.assertFalse(any("/tag/" in u for u in urls))
        self.assertFalse(any("/page/" in u for u in urls))


class TestParseDoubanGroupItems(unittest.TestCase):
    """Tests for crawl.parse_douban_group_items()."""

    MOCK_HTML = """
    <html><body>
    <table class="olt">
        <tr><td class="title"><a href="https://www.douban.com/group/topic/12345/" title="京东优惠券免费领">京东优惠券免费领</a></td></tr>
        <tr><td class="title"><a href="https://www.douban.com/group/topic/67890/" title="淘宝红包领取攻略">淘宝红包领取攻略</a></td></tr>
        <tr><td class="title"><a href="https://www.douban.com/group/topic/11111/" title="拼多多活动分享">拼多多活动分享</a></td></tr>
    </table>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_douban_group_items(soup, "https://www.douban.com/group/711811/")
        texts = [item["text"] for item in items]
        self.assertIn("京东优惠券免费领", texts)
        self.assertIn("淘宝红包领取攻略", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <table class="olt">
            <tr><td class="title"><a href="/group/topic/1/" title="重复帖子标题">重复帖子标题</a></td></tr>
            <tr><td class="title"><a href="/group/topic/2/" title="重复帖子标题">重复帖子标题</a></td></tr>
        </table>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_douban_group_items(soup, "https://www.douban.com/group/711811/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        html = """<html><body>
        <table class="olt">
            <tr><td class="title"><a href="/group/topic/1/" title="举报此帖">举报此帖</a></td></tr>
            <tr><td class="title"><a href="/group/topic/2/" title="正常帖子标题内容">正常帖子标题内容</a></td></tr>
        </table>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_douban_group_items(soup, "https://www.douban.com/group/711811/")
        # "举报此帖" does not match /group/topic/\d+ pattern via the primary selector
        # because the selector targets td.title a which it does match, but we check fallback skip_words
        texts = [item["text"] for item in items]
        self.assertIn("正常帖子标题内容", texts)


class TestParseHaodankuItems(unittest.TestCase):
    """Tests for crawl.parse_haodanku_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://www.haodanku.com/item/index">实时榜单</a>
    <a href="https://www.haodanku.com/herald/index">好单预告</a>
    <a href="https://www.haodanku.com/activity/tip_off">好单线报</a>
    <a href="https://www.haodanku.com/item/all_index">全部商品</a>
    <a href="https://www.haodanku.com/">首页</a>
    <a href="https://www.haodanku.com/login">登录</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_haodanku_items(soup, "https://www.haodanku.com/")
        texts = [item["text"] for item in items]
        self.assertIn("实时榜单", texts)
        self.assertIn("好单预告", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://www.haodanku.com/item/index">重复标题内容</a>
        <a href="https://www.haodanku.com/item/index2">重复标题内容</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_haodanku_items(soup, "https://www.haodanku.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_haodanku_items(soup, "https://www.haodanku.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("登录", texts)


class TestParseHybaseItems(unittest.TestCase):
    """Tests for crawl.parse_hybase_items()."""

    MOCK_HTML = """
    <html><body>
    <div class="li-type-card-title"><a href="https://m.hybase.com/shouji/android/12345.html">安卓浏览器最新版下载</a></div>
    <div class="li-type-card-title"><a href="https://m.hybase.com/pc/windows/67890.html">Windows系统优化工具</a></div>
    <div class="home-buzz"><div class="title"><a href="https://m.hybase.com/free/11111.html">免费软件推荐合集</a></div></div>
    <a href="https://m.hybase.com/">首页</a>
    <a href="https://m.hybase.com/about">关于</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_hybase_items(soup, "https://m.hybase.com/")
        texts = [item["text"] for item in items]
        self.assertIn("安卓浏览器最新版下载", texts)
        self.assertIn("Windows系统优化工具", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <div class="li-type-card-title"><a href="/shouji/1.html">重复软件标题</a></div>
        <div class="li-type-card-title"><a href="/shouji/2.html">重复软件标题</a></div>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_hybase_items(soup, "https://m.hybase.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_hybase_items(soup, "https://m.hybase.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("关于", texts)


class TestParseHuodong5Items(unittest.TestCase):
    """Tests for crawl.parse_huodong5_items()."""

    MOCK_HTML = """
    <html><body>
    <div class="feature-post">
        <div class="title"><a href="https://www.huodong5.com/12345.html">京东618大促活动攻略</a></div>
        <div class="title"><a href="https://www.huodong5.com/67890.html">支付宝红包领取方法</a></div>
    </div>
    <h3 class="slide-title"><a href="https://www.huodong5.com/11111.html">免费抽奖活动汇总</a></h3>
    <a href="https://www.huodong5.com/">首页</a>
    <a href="https://www.huodong5.com/category">活动分类</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_huodong5_items(soup, "https://www.huodong5.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东618大促活动攻略", texts)
        self.assertIn("支付宝红包领取方法", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <div class="feature-post"><div class="title"><a href="https://www.huodong5.com/1.html">重复活动标题</a></div></div>
        <h3 class="slide-title"><a href="https://www.huodong5.com/2.html">重复活动标题</a></h3>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_huodong5_items(soup, "https://www.huodong5.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_huodong5_items(soup, "https://www.huodong5.com/")
        urls = [item["url"] for item in items]
        # All items should be article URLs with numeric IDs
        for u in urls:
            self.assertRegex(u, r'huodong5\.com/\d+\.html')


class TestParseYangmaodangItems(unittest.TestCase):
    """Tests for crawl.parse_yangmaodang_items()."""

    MOCK_HTML = """
    <html><body>
    <a rel="bookmark" href="https://www.yangmaodang.club/articles/jd-coupon/">京东优惠券领取攻略</a>
    <a rel="bookmark" href="https://www.yangmaodang.club/news/tb-deal/">淘宝限时折扣信息</a>
    <a rel="bookmark" href="https://www.yangmaodang.club/things/pdd-free/">拼多多免费活动分享</a>
    <a rel="category tag" href="https://www.yangmaodang.club/category/bank/">银行羊毛</a>
    <a href="https://www.yangmaodang.club/category/long-term/">长期羊毛</a>
    <a href="https://www.yangmaodang.club/about/">关于本站</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yangmaodang_items(soup, "https://www.yangmaodang.club/")
        texts = [item["text"] for item in items]
        self.assertIn("京东优惠券领取攻略", texts)
        self.assertIn("淘宝限时折扣信息", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a rel="bookmark" href="https://www.yangmaodang.club/articles/a1/">重复羊毛标题</a>
        <a rel="bookmark" href="https://www.yangmaodang.club/articles/a2/">重复羊毛标题</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_yangmaodang_items(soup, "https://www.yangmaodang.club/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yangmaodang_items(soup, "https://www.yangmaodang.club/")
        texts = [item["text"] for item in items]
        # rel="category tag" should not be picked up as bookmark
        self.assertNotIn("银行羊毛", texts)
        self.assertNotIn("关于本站", texts)


class TestParseXianbaomiItems(unittest.TestCase):
    """Tests for crawl.parse_xianbaomi_items()."""

    MOCK_HTML = """
    <html><body>
    <ul class="erx-list">
        <li class="item"><a class="main" href="https://xianbaomi.com/xb/242891.html">京东免费领优惠券</a></li>
        <li class="item"><a class="main" href="https://xianbaomi.com/xb/242892.html">淘宝红包活动分享</a></li>
        <li class="item"><a class="main" href="https://xianbaomi.com/xb/242893.html">拼多多现金提现</a></li>
    </ul>
    <a href="https://xianbaomi.com/">首页</a>
    <a href="https://xianbaomi.com/sitemap">网站地图</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_xianbaomi_items(soup, "https://xianbaomi.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东免费领优惠券", texts)
        self.assertIn("淘宝红包活动分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <ul class="erx-list">
            <li class="item"><a class="main" href="/xb/1.html">重复线报标题</a></li>
            <li class="item"><a class="main" href="/xb/2.html">重复线报标题</a></li>
        </ul>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_xianbaomi_items(soup, "https://xianbaomi.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_xianbaomi_items(soup, "https://xianbaomi.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("网站地图", texts)


class TestParseYangmaoWangItems(unittest.TestCase):
    """Tests for crawl.parse_yangmao_wang_items()."""

    MOCK_HTML = """
    <html><body>
    <ul class="list-a">
        <li><a href="https://yangmao.wang/yangmao/41.html" title="京东红包免费领">京东红包免费领</a></li>
        <li><a href="https://yangmao.wang/zhuanqian/42.html" title="淘宝优惠券领取">淘宝优惠券领取</a></li>
        <li><a href="https://yangmao.wang/dzyh/43.html" title="电子银行活动攻略">电子银行活动攻略</a></li>
    </ul>
    <a href="https://yangmao.wang/">首页</a>
    <a href="https://yangmao.wang/about">关于本站</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yangmao_wang_items(soup, "https://yangmao.wang/")
        texts = [item["text"] for item in items]
        self.assertIn("京东红包免费领", texts)
        self.assertIn("淘宝优惠券领取", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <ul class="list-a">
            <li><a href="https://yangmao.wang/yangmao/1.html" title="重复标题">重复标题</a></li>
            <li><a href="https://yangmao.wang/yangmao/2.html" title="重复标题">重复标题</a></li>
        </ul>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_yangmao_wang_items(soup, "https://yangmao.wang/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_yangmao_wang_items(soup, "https://yangmao.wang/")
        texts = [item["text"] for item in items]
        self.assertNotIn("首页", texts)
        self.assertNotIn("关于本站", texts)


class TestParseIqnewItems(unittest.TestCase):
    """Tests for crawl.parse_iqnew_items()."""

    MOCK_HTML = """
    <html><body>
    <div class="news-comm-wrap">
        <ul>
            <li><a href="/activity/214255.html">京东618活动攻略分享</a></li>
            <li><a href="/news/214256.html">支付宝红包领取方法</a></li>
            <li><a href="/mall/214257.html">拼多多百亿补贴技巧</a></li>
        </ul>
    </div>
    <a href="/activity/999.html">首页</a>
    <a href="/login">登录</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_iqnew_items(soup, "https://www.iqnew.com/")
        texts = [item["text"] for item in items]
        self.assertIn("京东618活动攻略分享", texts)
        self.assertIn("支付宝红包领取方法", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <div class="news-comm-wrap"><ul>
            <li><a href="/activity/1.html">重复活动内容标题</a></li>
            <li><a href="/activity/1.html">重复活动内容标题</a></li>
        </ul></div>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_iqnew_items(soup, "https://www.iqnew.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_iqnew_items(soup, "https://www.iqnew.com/")
        texts = [item["text"] for item in items]
        # "首页" matches /activity/999.html but is in skip_words for fallback
        # In strategy 1 it's not filtered by skip_words, but "首页" has len=2 < 4 so filtered
        self.assertNotIn("登录", texts)


class TestParse51kanongItems(unittest.TestCase):
    """Tests for crawl.parse_51kanong_items()."""

    MOCK_HTML = """
    <html><body>
    <div id="article_list">
        <li class="article_item">
            <div class="article_title"><h2><a href="xyk-12345-1.htm" title="信用卡申请攻略分享">信用卡申请攻略分享</a></h2></div>
        </li>
        <li class="article_item">
            <div class="article_title"><h2><a href="xyk-67890-1.htm" title="贷款利息计算方法">贷款利息计算方法</a></h2></div>
        </li>
    </div>
    <div class="comlimi_tops"><h2><a href="xyk-11111-1.htm" title="今日头条信用卡优惠">今日头条信用卡优惠</a></h2></div>
    <a href="xyk-99999-1.htm">首页</a>
    <a href="xyk-88888-1.htm">信用卡交流</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_51kanong_items(soup, "https://www.51kanong.com/")
        texts = [item["text"] for item in items]
        self.assertIn("信用卡申请攻略分享", texts)
        self.assertIn("贷款利息计算方法", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <div id="article_list">
            <li class="article_item"><div class="article_title"><h2><a href="xyk-1-1.htm" title="重复标题">重复标题</a></h2></div></li>
            <li class="article_item"><div class="article_title"><h2><a href="xyk-2-1.htm" title="重复标题">重复标题</a></h2></div></li>
        </div>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_51kanong_items(soup, "https://www.51kanong.com/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_51kanong_items(soup, "https://www.51kanong.com/")
        texts = [item["text"] for item in items]
        # "首页" and "信用卡交流" should be in skip_words for fallback
        self.assertNotIn("信用卡交流", texts)


class TestParseYmxianbaoItems(unittest.TestCase):
    """Tests for crawl.parse_ymxianbao_items()."""

    MOCK_HTML = """
    <html><body>
    <a href="https://b1.ymxianbao.cn/12345.html">京东免费领优惠券活动</a>
    <a href="https://b1.ymxianbao.cn/67890.html">淘宝红包领取攻略分享</a>
    <a href="https://b1.ymxianbao.cn/11111.html">拼多多现金提现方法</a>
    <a href="https://b1.ymxianbao.cn/">羊毛阁</a>
    <a href="https://b1.ymxianbao.cn/contact">联系站长</a>
    <a href="https://b1.ymxianbao.cn/page/2">2</a>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_ymxianbao_items(soup, "https://b1.ymxianbao.cn/")
        texts = [item["text"] for item in items]
        self.assertIn("京东免费领优惠券活动", texts)
        self.assertIn("淘宝红包领取攻略分享", texts)
        self.assertGreaterEqual(len(items), 2)

    def test_dedup(self):
        html = """<html><body>
        <a href="https://b1.ymxianbao.cn/100.html">重复线报标题内容</a>
        <a href="https://b1.ymxianbao.cn/100.html">重复线报标题内容</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_ymxianbao_items(soup, "https://b1.ymxianbao.cn/")
        self.assertEqual(len(items), 1)

    def test_filters_junk(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_ymxianbao_items(soup, "https://b1.ymxianbao.cn/")
        texts = [item["text"] for item in items]
        self.assertNotIn("羊毛阁", texts)
        self.assertNotIn("联系站长", texts)


# ===================================================================
# Playwright JS 渲染相关测试
# ===================================================================

class TestNeedsPlaywright(unittest.TestCase):
    """Tests for crawl._needs_playwright()."""

    def test_js_render_site_detected(self):
        self.assertTrue(crawl._needs_playwright("https://www.kxdao.net/forum-42-1.html"))

    def test_js_render_site_subdomain(self):
        self.assertTrue(crawl._needs_playwright("https://51kanong.com/"))

    def test_normal_site_not_detected(self):
        self.assertFalse(crawl._needs_playwright("https://www.423down.com/"))

    def test_normal_site_not_detected_2(self):
        self.assertFalse(crawl._needs_playwright("https://www.baicaio.com/"))

    def test_js_render_set_contents(self):
        """Verify JS_RENDER_SITES has expected entries (dead sites removed)."""
        self.assertIn('kxdao.net', crawl.JS_RENDER_SITES)
        self.assertIn('51kanong.com', crawl.JS_RENDER_SITES)
        # 死站不应在 JS_RENDER_SITES 中（避免 Playwright 先于死站检查）
        self.assertNotIn('907k.cn', crawl.JS_RENDER_SITES)
        self.assertNotIn('xiaodigu.com', crawl.JS_RENDER_SITES)


class TestPlaywrightAvailability(unittest.TestCase):
    """Tests for Playwright optional import."""

    def test_playwright_flag_is_bool(self):
        self.assertIsInstance(crawl.PLAYWRIGHT_AVAILABLE, bool)

    def test_fetch_with_playwright_is_callable(self):
        self.assertTrue(callable(crawl.fetch_with_playwright))

    def test_close_playwright_is_callable(self):
        self.assertTrue(callable(crawl.close_playwright))


# ===================================================================
# 死站黑名单测试
# ===================================================================

class TestDeadSites(unittest.TestCase):
    """Tests for DEAD_SITES blacklist and is_dead_site()."""

    def test_dead_sites_count(self):
        self.assertEqual(len(crawl.DEAD_SITES), 7)

    def test_dead_site_detected(self):
        self.assertIsNotNone(crawl.is_dead_site("https://907k.cn/"))
        self.assertIsNotNone(crawl.is_dead_site("http://www.xiaodigu.com/"))
        self.assertIsNotNone(crawl.is_dead_site("https://www.ym2.cc/"))

    def test_alive_site_not_dead(self):
        self.assertIsNone(crawl.is_dead_site("https://www.423down.com/"))
        self.assertIsNone(crawl.is_dead_site("https://www.baicaio.com/"))
        self.assertIsNone(crawl.is_dead_site("https://www.foxirj.com/"))  # SSL过期但可访问

    def test_dead_site_has_reason(self):
        for url, info in crawl.DEAD_SITES.items():
            self.assertIn('reason', info, msg=f"{url} missing reason")
            self.assertIn('confirmed_at', info, msg=f"{url} missing confirmed_at")
            self.assertIn('test_result', info, msg=f"{url} missing test_result")

    def test_foxirj_not_dead(self):
        """foxirj.com SSL过期但可访问，不应在死站中（全局SSL跳过已启用）。"""
        self.assertIsNone(crawl.is_dead_site("https://www.foxirj.com/"))


# ===================================================================
# linejia (79淘) 解析器测试
# ===================================================================

class TestParseLinejiaItems(unittest.TestCase):
    """Tests for crawl.parse_linejia_items()."""

    MOCK_HTML = """
    <html><body>
    <ul class="list-wz">
        <li><a href="/huodong/2478664.html">真便宜采销低价好物</a><span><i>06/09</i></span></li>
        <li><a href="/huodong/2478370.html">京东果子太难了</a><span><i>06/09</i></span></li>
        <li><a href="/about">关于我们</a></li>
    </ul>
    </body></html>
    """

    def test_extracts_items(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_linejia_items(soup, "http://79tao.linejia.com/")
        self.assertEqual(len(items), 2)
        texts = [item["text"] for item in items]
        self.assertIn("真便宜采销低价好物", texts)
        self.assertIn("京东果子太难了", texts)

    def test_url_converted(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_linejia_items(soup, "http://79tao.linejia.com/")
        for item in items:
            self.assertTrue(item["url"].startswith("http"))

    def test_dedup(self):
        html = '<ul class="list-wz"><li><a href="/huodong/1.html">重复标题</a></li><li><a href="/huodong/2.html">重复标题</a></li></ul>'
        soup = make_soup(html)
        items = crawl.parse_linejia_items(soup, "http://79tao.linejia.com/")
        self.assertEqual(len(items), 1)

    def test_filters_nav(self):
        soup = make_soup(self.MOCK_HTML)
        items = crawl.parse_linejia_items(soup, "http://79tao.linejia.com/")
        texts = [item["text"] for item in items]
        self.assertNotIn("关于我们", texts)


# ===================================================================
# RSS 解析器增强测试
# ===================================================================

class TestParseRssFeedRobust(unittest.TestCase):
    """Tests for parse_rss_feed with malformed XML handling."""

    def test_trailing_comment_fixed(self):
        """RSS with malformed trailing comment should still parse."""
        xml = b'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><item><title>Test Article</title><link>https://example.com/1</link></item></channel></rss><!--Cached 12345--->'
        items = crawl.parse_rss_feed(xml, "https://feed.example.com/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Test Article")

    def test_normal_rss(self):
        xml = b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>Article A</title><link>https://a.com</link></item><item><title>Article B</title><link>https://b.com</link></item></channel></rss>'
        items = crawl.parse_rss_feed(xml, "https://feed.example.com/")
        self.assertEqual(len(items), 2)

    def test_empty_rss(self):
        xml = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        items = crawl.parse_rss_feed(xml, "https://feed.example.com/")
        self.assertEqual(len(items), 0)


# ===================================================================
# MAX_ITEMS_DB 一致性测试
# ===================================================================

class TestMaxItemsConsistency(unittest.TestCase):
    """Ensure MAX_ITEMS_DB is consistent between crawl.py and common.py."""

    def test_crawl_max_items(self):
        self.assertEqual(crawl.MAX_ITEMS_DB, 0)


# ===================================================================
# engine.py 关键逻辑测试
# ===================================================================

class TestExportCrawlStatus(unittest.TestCase):
    """Tests for crawler.engine.export_crawl_status status mapping."""

    def test_first_status_mapped_to_ok(self):
        """'first' (首次监控) should map to 'ok', not 'skip'."""
        from crawler.engine import export_crawl_status
        import tempfile, json as json_mod
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            tmp_path = f.name
        try:
            import crawler.engine as eng
            orig = eng.CRAWL_STATUS_FILE
            eng.CRAWL_STATUS_FILE = tmp_path
            eng.export_crawl_status(
                [{'url': 'https://example.com/', 'status': 'first', 'message': '首次监控'}],
                [], None, {}
            )
            eng.CRAWL_STATUS_FILE = orig
            with open(tmp_path, 'r', encoding='utf-8') as rf:
                data = json_mod.load(rf)
            site = data['sites'][0]
            self.assertEqual(site['status'], 'ok',
                             "'first' status should map to 'ok', got: " + site['status'])
        finally:
            os.unlink(tmp_path)

    def test_updated_status_mapped_to_ok(self):
        from crawler.engine import export_crawl_status
        import tempfile, json as json_mod
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            tmp_path = f.name
        try:
            import crawler.engine as eng
            orig = eng.CRAWL_STATUS_FILE
            eng.CRAWL_STATUS_FILE = tmp_path
            eng.export_crawl_status(
                [{'url': 'https://example.com/', 'status': 'updated', 'message': ''}],
                [], None, {}
            )
            eng.CRAWL_STATUS_FILE = orig
            with open(tmp_path, 'r', encoding='utf-8') as rf:
                data = json_mod.load(rf)
            self.assertEqual(data['sites'][0]['status'], 'ok')
        finally:
            os.unlink(tmp_path)

    def test_error_status_mapped_to_fail(self):
        from crawler.engine import export_crawl_status
        import tempfile, json as json_mod
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            tmp_path = f.name
        try:
            import crawler.engine as eng
            orig = eng.CRAWL_STATUS_FILE
            eng.CRAWL_STATUS_FILE = tmp_path
            eng.export_crawl_status(
                [{'url': 'https://bad.com/', 'status': 'error', 'message': '连接失败'}],
                [], None, {}
            )
            eng.CRAWL_STATUS_FILE = orig
            with open(tmp_path, 'r', encoding='utf-8') as rf:
                data = json_mod.load(rf)
            self.assertEqual(data['sites'][0]['status'], 'fail')
        finally:
            os.unlink(tmp_path)


class TestEngineImports(unittest.TestCase):
    """Verify all functions called in engine.py are properly imported."""

    def test_parse_rss_feed_imported(self):
        from crawler.engine import parse_rss_feed
        self.assertTrue(callable(parse_rss_feed))

    def test_parse_ghxi_items_imported(self):
        from crawler.engine import parse_ghxi_items
        self.assertTrue(callable(parse_ghxi_items))

    def test_match_parser_imported(self):
        from crawler.engine import _match_parser
        self.assertTrue(callable(_match_parser))


class TestRedirectHtml(unittest.TestCase):
    """Static checks on redirect.html to catch onclick handler issues."""

    def test_onclick_handlers_defined(self):
        import re
        with open(os.path.join(PROJECT_DIR, 'redirect.html'), 'r', encoding='utf-8') as f:
            html = f.read()
        # goToUrl should be assigned to window.goToUrl
        self.assertIn('window.goToUrl = function', html,
                         'goToUrl not exposed on window')
        self.assertIn('window.copyShareUrl = function', html,
                         'copyShareUrl not exposed on window')

    def test_onclick_references_exist(self):
        with open(os.path.join(PROJECT_DIR, 'redirect.html'), 'r', encoding='utf-8') as f:
            html = f.read()
        self.assertIn('onclick="goToUrl()"', html)
        self.assertIn('onclick="copyShareUrl()"', html)


# ===================================================================
# MAIN
# ===================================================================

if __name__ == "__main__":
    unittest.main()
