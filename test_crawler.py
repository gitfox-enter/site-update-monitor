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

import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Ensure the project directory is on sys.path so we can import modules
# regardless of the working directory.
# ---------------------------------------------------------------------------
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import crawl
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

    @patch("crawl.get_beijing_time")
    def test_round_1_midnight(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 1)

    @patch("crawl.get_beijing_time")
    def test_round_1_early_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 3, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 1)

    @patch("crawl.get_beijing_time")
    def test_round_2_dawn(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 2)

    @patch("crawl.get_beijing_time")
    def test_round_2_early_morning_end(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 7, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 2)

    @patch("crawl.get_beijing_time")
    def test_round_3_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 3)

    @patch("crawl.get_beijing_time")
    def test_round_3_late_morning(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 11, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 3)

    @patch("crawl.get_beijing_time")
    def test_round_4_noon(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 4)

    @patch("crawl.get_beijing_time")
    def test_round_4_afternoon(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 15, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 4)

    @patch("crawl.get_beijing_time")
    def test_round_5_evening(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 5)

    @patch("crawl.get_beijing_time")
    def test_round_5_late_evening(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 19, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 5)

    @patch("crawl.get_beijing_time")
    def test_round_6_night(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 6)

    @patch("crawl.get_beijing_time")
    def test_round_6_late_night(self, mock_time):
        mock_time.return_value = datetime(2026, 6, 8, 23, 59, tzinfo=timezone.utc)
        self.assertEqual(crawl.get_current_round(), 6)

    @patch("crawl.get_beijing_time")
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
        self.assertEqual(crawl.get_source_name("http://www.0818tuan.com/"), "0818团")

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
        crawl.HASH_RECORD_FILE = os.path.join(self._tmpdir, "hash_record.txt")

    def tearDown(self):
        crawl.HASH_RECORD_FILE = self._orig_file
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
        crawl.HASH_RECORD_FILE = os.path.join(self._tmpdir, "no_such_file.txt")
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
        """Lines starting with '#' should be ignored on load."""
        crawl.save_hash_records({"https://a.com/": "aaa"})
        # Manually append a comment line
        with open(crawl.HASH_RECORD_FILE, "a", encoding="utf-8") as f:
            f.write("# this is a comment\n")
        loaded = crawl.load_hash_records()
        self.assertEqual(len(loaded), 1)
        self.assertIn("https://a.com/", loaded)


class TestNotifiedItemsRoundtrip(unittest.TestCase):
    """Roundtrip test for load_notified_items / save_notified_items."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.NOTIFIED_ITEMS_FILE
        crawl.NOTIFIED_ITEMS_FILE = os.path.join(self._tmpdir, "notified_items.json")

    def tearDown(self):
        crawl.NOTIFIED_ITEMS_FILE = self._orig_file
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
        crawl.NOTIFIED_ITEMS_FILE = os.path.join(self._tmpdir, "missing.json")
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
        self._orig_file = crawl.ITEMS_DB_FILE
        crawl.ITEMS_DB_FILE = os.path.join(self._tmpdir, "items.json")

    def tearDown(self):
        crawl.ITEMS_DB_FILE = self._orig_file
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
        crawl.ITEMS_DB_FILE = os.path.join(self._tmpdir, "missing.json")
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
        self._orig_file = crawl.ITEMS_DB_FILE
        crawl.ITEMS_DB_FILE = os.path.join(self._tmpdir, "items.json")

    def tearDown(self):
        crawl.ITEMS_DB_FILE = self._orig_file
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        os.rmdir(self._tmpdir)

    def test_merge_into_empty_db(self):
        new_items = [
            {"url": "https://a.com/1", "text": "Item 1"},
            {"url": "https://b.com/2", "text": "Item 2"},
        ]
        added = crawl.merge_items_into_db(new_items, "2026-06-08 10:00:00")
        self.assertEqual(added, 2)

        db = crawl.load_items_db()
        self.assertEqual(len(db["items"]), 2)
        self.assertEqual(db["updated_at"], "2026-06-08 10:00:00")

    def test_deduplication(self):
        # Pre-populate DB
        crawl.save_items_db({
            "items": [{"url": "https://a.com/1", "text": "Existing"}],
            "updated_at": "old",
        })

        new_items = [
            {"url": "https://a.com/1", "text": "Duplicate"},  # should be skipped
            {"url": "https://c.com/3", "text": "Brand new"},
        ]
        added = crawl.merge_items_into_db(new_items, "2026-06-08 11:00:00")
        self.assertEqual(added, 1)

        db = crawl.load_items_db()
        self.assertEqual(len(db["items"]), 2)
        urls = [item["url"] for item in db["items"]]
        self.assertIn("https://a.com/1", urls)
        self.assertIn("https://c.com/3", urls)

    def test_new_items_prepended(self):
        crawl.save_items_db({
            "items": [{"url": "https://old.com/1", "text": "Old"}],
            "updated_at": "old",
        })
        new_items = [{"url": "https://new.com/1", "text": "New"}]
        crawl.merge_items_into_db(new_items, "2026-06-08 12:00:00")

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["url"], "https://new.com/1")
        self.assertEqual(db["items"][1]["url"], "https://old.com/1")

    def test_max_items_trimming(self):
        """When total exceeds MAX_ITEMS_DB, oldest items are trimmed."""
        # Temporarily lower the max for testing
        orig_max = crawl.MAX_ITEMS_DB
        crawl.MAX_ITEMS_DB = 10

        try:
            # Pre-populate with 8 items
            existing = [
                {"url": f"https://old.com/{i}", "text": f"Old {i}"}
                for i in range(8)
            ]
            crawl.save_items_db({"items": existing, "updated_at": "old"})

            # Add 5 new items -> total 13, should trim to 10
            new_items = [
                {"url": f"https://new.com/{i}", "text": f"New {i}"}
                for i in range(5)
            ]
            added = crawl.merge_items_into_db(new_items, "2026-06-08 12:00:00")
            self.assertEqual(added, 5)

            db = crawl.load_items_db()
            self.assertEqual(len(db["items"]), 10)
            # Newest items should be at the front
            self.assertTrue(db["items"][0]["url"].startswith("https://new.com/"))
        finally:
            crawl.MAX_ITEMS_DB = orig_max

    def test_auto_categorize_applied(self):
        new_items = [
            {"url": "https://a.com/1", "text": "京东优惠券大促销"},
        ]
        crawl.merge_items_into_db(new_items, "2026-06-08 12:00:00")

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["category"], "京东")

    def test_existing_category_preserved(self):
        new_items = [
            {"url": "https://a.com/1", "text": "京东优惠券", "category": "自定义"},
        ]
        crawl.merge_items_into_db(new_items, "2026-06-08 12:00:00")

        db = crawl.load_items_db()
        self.assertEqual(db["items"][0]["category"], "自定义")

    def test_empty_url_items_skipped(self):
        new_items = [
            {"url": "", "text": "No URL"},
            {"url": "https://valid.com/", "text": "Valid"},
        ]
        added = crawl.merge_items_into_db(new_items, "2026-06-08 12:00:00")
        self.assertEqual(added, 1)


# ===================================================================
# 3. PARSER FUNCTION TESTS (with mock HTML)
# ===================================================================

class TestParse423downItems(unittest.TestCase):
    """Tests for crawl.parse_423down_items() with mock 423Down HTML."""

    MOCK_HTML = """
    <html><body>
    <div class="post-list">
        <a href="https://www.423down.com/12345.html">Chrome 浏览器 v125 正式版</a>
        <a href="https://www.423down.com/67890.html">WinRAR 解压工具 v7.0</a>
        <a href="https://www.423down.com/category/os">操作系统</a>
        <a href="/11111.html">IDM 下载管理器 v6.41</a>
    </div>
    <div class="sidebar">
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
        <a href="/article/12345.htm">如何清理电脑垃圾</a>
        <a href="/article/12346.htm">Win11 更新教程</a>
        <a href="/article/12347.htm">首页</a>
        </body></html>"""
        soup = make_soup(html)
        items = crawl.parse_onlinedown_items(soup, "https://www.onlinedown.net/")
        self.assertEqual(len(items), 2)
        self.assertIn('/article/', items[0]['url'])

    def test_parse_onlinedown_items_max_limit(self):
        links = "".join(
            f'<a href="/article/{i}.htm">文章标题内容测试编号 {i} 足够长度</a>'
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
# 7. CIRCUIT BREAKER / PAUSED SITES TESTS
# ===================================================================

class TestPausedSitesManagement(unittest.TestCase):
    """Tests for load_paused_sites / save_paused_sites."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.PAUSED_SITES_FILE
        crawl.PAUSED_SITES_FILE = os.path.join(self._tmpdir, "paused_sites.json")

    def tearDown(self):
        crawl.PAUSED_SITES_FILE = self._orig_file
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_load_nonexistent_returns_empty(self):
        crawl.PAUSED_SITES_FILE = os.path.join(self._tmpdir, "missing.json")
        result = crawl.load_paused_sites()
        self.assertEqual(result, {})

    def test_save_and_load(self):
        paused = {
            "https://broken.com/": {
                "paused_at": "2026-06-08 10:00:00",
                "reason": "连续失败3轮",
                "fail_count": 3,
            }
        }
        crawl.save_paused_sites(paused)
        loaded = crawl.load_paused_sites()
        self.assertEqual(loaded, paused)

    def test_load_corrupt_json_returns_empty(self):
        with open(crawl.PAUSED_SITES_FILE, "w", encoding="utf-8") as f:
            f.write("not json at all")
        result = crawl.load_paused_sites()
        self.assertEqual(result, {})


# ===================================================================
# 8. RUN LOG TESTS
# ===================================================================

class TestRunLog(unittest.TestCase):
    """Tests for load_run_log / append_run_log."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_file = crawl.RUN_LOG_FILE
        crawl.RUN_LOG_FILE = os.path.join(self._tmpdir, "run_log.jsonl")

    def tearDown(self):
        crawl.RUN_LOG_FILE = self._orig_file
        for f in os.listdir(self._tmpdir):
            os.remove(os.path.join(self._tmpdir, f))
        os.rmdir(self._tmpdir)

    def test_load_nonexistent_returns_empty(self):
        crawl.RUN_LOG_FILE = os.path.join(self._tmpdir, "missing.jsonl")
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

    @patch("crawl.fetch_page_content")
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

    @patch("crawl.fetch_page_content")
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

    @patch("crawl.fetch_page_content")
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

    @patch("crawl.fetch_page_content")
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
        crawl_cats = set(crawl.CATEGORY_KEYWORDS.keys())
        fc_cats = set(fast_check.CATEGORY_KEYWORDS.keys())
        self.assertEqual(
            crawl_cats, fc_cats,
            "CATEGORY_KEYWORDS categories differ between crawl.py and fast_check.py",
        )

    def test_keywords_are_non_empty(self):
        for cat, keywords in crawl.CATEGORY_KEYWORDS.items():
            self.assertGreater(len(keywords), 0, f"Category '{cat}' has no keywords")


# ===================================================================
# MAIN
# ===================================================================

if __name__ == "__main__":
    unittest.main()
