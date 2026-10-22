#!/usr/bin/env python3
"""Unit tests for scripts/steam_news.py. No network unless STEAM_NEWS_LIVE=1."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import steam_news  # noqa: E402

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
STEAM_FIXTURE = {
    "appnews": {
        "appid": 730,
        "newsitems": [
            {
                "gid": "1846651706695791",
                "title": "Update <b>today</b>",
                "url": "https://example.test/news/1",
                "author": "Valve",
                "contents": "<p>Hello &amp; welcome</p>",
                "feedlabel": "Community Announcements",
                "date": 1700000000,
                "feedname": "steam_community_announcements",
                "appid": 730,
            }
        ],
        "count": 1,
    }
}
STORE_FIXTURE = {
    "0": {
        "id": "cat_spotlight",
        "name": "Spotlights",
        "items": [
            {
                "name": "系列作品特卖",
                "url": "https://store.steampowered.com/sale/example",
                "body": "优惠截止时间：%1$s",
            }
        ],
    },
    "specials": {
        "id": "cat_specials",
        "name": "优惠",
        "items": [
            {
                "id": 1091500,
                "type": 0,
                "name": "赛博朋克 2077",
                "discounted": True,
                "discount_percent": 70,
                "original_price": 29800,
                "final_price": 8940,
                "currency": "CNY",
            }
        ],
    },
    "top_sellers": {
        "id": "cat_topsellers",
        "name": "热销商品",
        "items": [
            {
                "id": 32470,
                "type": 2,
                "name": "《赛博朋克 2077：终极版》",
                "discounted": False,
                "discount_percent": 0,
                "original_price": 10000,
                "final_price": 10000,
                "currency": "CNY",
            }
        ],
    },
}
XIAOHEIHE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>小黑盒标题</title>
  <link>https://xiaoheihe.cn/app/bbs/link/1</link>
  <pubDate>Thu, 22 Oct 2026 01:02:03 GMT</pubDate>
  <description>&lt;p&gt;简介&lt;/p&gt;</description>
</item>
</channel></rss>
""".encode()
ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>机核标题</title>
    <link rel="alternate" href="https://www.gcores.com/articles/1"/>
    <updated>2026-10-22T01:02:03Z</updated>
    <summary>正文 &amp; 注释</summary>
  </entry>
</feed>
""".encode()


def sample_options(**overrides):
    data = {
        "apps": [{"appid": 730, "name": "Counter-Strike 2"}],
        "sections": ["steam-news", "specials", "rss"],
        "count": 2,
        "maxlength": 280,
        "timeout": 5,
        "cc": "cn",
        "lang": "schinese",
        "rsshub_base": "https://rsshub.example",
        "rss": [
            {"name": "小黑盒", "path": "/xiaoheihe/news"},
            {"name": "机核", "path": "/gcores/news"},
        ],
    }
    data.update(overrides)
    return steam_news.options_from_mapping(data)


def fake_fetcher(calls, *, gcores_error="timeout", steam_error=None):
    lock = threading.Lock()

    def fetcher(url, timeout):
        del timeout
        with lock:
            calls.append(url)
        if "GetNewsForApp" in url:
            appid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("appid", [""])[0]
            if steam_error or appid == "1":
                raise steam_news.FetchError(steam_error or "HTTP 403: Forbidden")
            return json.dumps(STEAM_FIXTURE).encode()
        if "featuredcategories" in url:
            return json.dumps(STORE_FIXTURE).encode()
        if url.endswith("/xiaoheihe/news"):
            return XIAOHEIHE_RSS
        if url.endswith("/gcores/news"):
            raise steam_news.FetchError(gcores_error)
        if url.endswith("/atom"):
            return ATOM
        raise steam_news.FetchError(f"unexpected URL {url}")

    return fetcher


class TextAndPriceTests(unittest.TestCase):
    def test_markdown_link_keeps_spaces_in_the_url(self):
        self.assertEqual(
            steam_news.md_link("T", "https://example.test/Rock, Paper, Shotgun/1"),
            "[T](<https://example.test/Rock, Paper, Shotgun/1>)",
        )

    def test_plain_text_strips_html_and_truncates(self):
        self.assertEqual(steam_news.plain_text("<p>Hello &amp; welcome</p>", 100), "Hello & welcome")
        self.assertEqual(steam_news.plain_text("abcdef", 3), "abc…")

    def test_price_text(self):
        self.assertEqual(
            steam_news.price_text(
                {
                    "original_price": 29800,
                    "final_price": 8940,
                    "discount_percent": 70,
                    "currency": "CNY",
                }
            ),
            "¥89.40（原价 ¥298.00，-70%）",
        )
        self.assertEqual(
            steam_news.price_text({"original_price": 0, "final_price": 0, "currency": "CNY"}),
            "免费",
        )
        self.assertEqual(
            steam_news.price_text({"original_price": None, "final_price": 0, "currency": "CNY"}),
            "未标价",
        )
        self.assertIsNone(steam_news.price_text({"name": "系列作品特卖"}))

    def test_store_item_bundle_and_spotlight_placeholder(self):
        bundle = steam_news.store_item(
            {
                "id": 32470,
                "type": 2,
                "name": "《赛博朋克 2077：终极版》",
                "currency": "CNY",
                "original_price": 10000,
                "final_price": 10000,
            },
            "热销商品",
        )
        self.assertEqual(bundle["kind"], "bundle")
        self.assertEqual(bundle["url"], "https://store.steampowered.com/bundle/32470")
        self.assertEqual(bundle["price_text"], "¥100.00")
        spotlight = steam_news.store_item(
            {
                "name": "系列作品特卖",
                "url": "https://store.steampowered.com/sale/example",
                "body": "优惠截止时间：%1$s",
            },
            "Spotlights",
        )
        self.assertEqual(spotlight["kind"], "spotlight")
        self.assertEqual(spotlight["summary"], "优惠截止时间：%1$s")
        self.assertIsNone(spotlight["price_text"])


class FeedTests(unittest.TestCase):
    def test_rss_and_atom(self):
        rss_items = steam_news.parse_feed(XIAOHEIHE_RSS, 5, 100)
        self.assertEqual(rss_items[0]["title"], "小黑盒标题")
        self.assertEqual(rss_items[0]["summary"], "简介")
        self.assertEqual(rss_items[0]["date"], "2026-10-22T01:02:03Z")
        atom_items = steam_news.parse_feed(ATOM, 5, 100)
        self.assertEqual(atom_items[0]["title"], "机核标题")
        self.assertEqual(atom_items[0]["url"], "https://www.gcores.com/articles/1")
        self.assertEqual(atom_items[0]["summary"], "正文 & 注释")

    def test_invalid_xml_is_a_fetch_error(self):
        with self.assertRaises(steam_news.FetchError):
            steam_news.parse_feed(b"<rss>", 5, 100)


class DigestTests(unittest.TestCase):
    def test_partial_rss_failure_keeps_steam_items(self):
        calls = []
        doc = steam_news.build_digest(sample_options(), fetcher=fake_fetcher(calls), now=NOW)
        self.assertEqual(doc["status"], "partial")
        news = doc["sections"]["steam-news"]["apps"][0]
        self.assertTrue(news["ok"])
        self.assertEqual(news["items"][0]["title"], "Update today")
        self.assertEqual(news["items"][0]["summary"], "Hello & welcome")
        self.assertEqual(news["items"][0]["date"], "2023-11-14T22:13:20Z")
        specials = doc["sections"]["specials"]["items"]
        self.assertEqual(specials[0]["name"], "赛博朋克 2077")
        self.assertEqual(specials[0]["price_text"], "¥89.40（原价 ¥298.00，-70%）")
        feeds = {feed["name"]: feed for feed in doc["sections"]["rss"]["feeds"]}
        self.assertTrue(feeds["小黑盒"]["ok"])
        self.assertEqual(feeds["小黑盒"]["items"][0]["title"], "小黑盒标题")
        self.assertFalse(feeds["机核"]["ok"])
        self.assertEqual(feeds["机核"]["items"], [])
        self.assertEqual(feeds["机核"]["error"], "timeout")
        self.assertEqual(
            doc["failures"],
            [{"section": "rss", "target": "机核", "error": "timeout"}],
        )
        joined = " ".join(calls)
        self.assertIn("appid=730", joined)
        self.assertIn("cc=cn", joined)
        self.assertIn("l=schinese", joined)
        self.assertIn("/xiaoheihe/news", joined)
        self.assertIn("/gcores/news", joined)

    def test_markdown_reports_failure_and_does_not_invent_items(self):
        doc = steam_news.build_digest(sample_options(), fetcher=fake_fetcher([]), now=NOW)
        markdown = steam_news.render_markdown(doc)
        expected = """# Steam / PC 游戏日报

- 生成时间（UTC）：2026-10-22T00:00:00Z
- 地区：cc=cn，lang=schinese
- 状态：部分成功
- 条目仅来自本次抓取。失败来源没有新闻，不要用记忆或猜测补齐。

## 关注列表 · Steam 官方新闻

应用名称是关注列表里的标签，不是接口返回的游戏名。

### Counter-Strike 2

appid 730

- [Update today](https://example.test/news/1) · 2023-11-14T22:13:20Z · Community Announcements
  Hello & welcome

## 特惠

接口分类名：优惠

- [赛博朋克 2077](https://store.steampowered.com/app/1091500) · ¥89.40（原价 ¥298.00，-70%）

## 中文游戏资讯

### 小黑盒

Feed：https://rsshub.example/xiaoheihe/news

- [小黑盒标题](https://xiaoheihe.cn/app/bbs/link/1) · 2026-10-22T01:02:03Z
  简介

### 机核

Feed：https://rsshub.example/gcores/news

抓取失败：timeout

本节没有可用条目。

## 抓取失败

- rss / 机核：timeout

## 数据源

- Steam 新闻：https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/
- Steam 商店：https://store.steampowered.com/api/featuredcategories?cc=cn&l=schinese
- RSSHub：https://rsshub.example
"""
        self.assertEqual(markdown, expected)
        self.assertNotIn("Invented Headline", markdown)
        self.assertNotIn("无。", markdown)

    def test_store_failure_does_not_drop_other_sections(self):
        def fetcher(url, timeout):
            del timeout
            if "featuredcategories" in url:
                raise steam_news.FetchError("network error: timed out")
            if "GetNewsForApp" in url:
                return json.dumps(STEAM_FIXTURE).encode()
            raise steam_news.FetchError("unexpected")

        doc = steam_news.build_digest(
            sample_options(sections=["steam-news", "featured", "specials"], rss=[]),
            fetcher=fetcher,
            now=NOW,
        )
        self.assertEqual(doc["status"], "partial")
        self.assertTrue(doc["sections"]["steam-news"]["ok"])
        self.assertFalse(doc["sections"]["featured"]["ok"])
        self.assertEqual(doc["sections"]["featured"]["items"], [])
        self.assertFalse(doc["sections"]["specials"]["ok"])
        markdown = steam_news.render_markdown(doc)
        self.assertIn("抓取失败：network error: timed out", markdown)
        self.assertIn("Update today", markdown)
        self.assertNotIn("赛博朋克 2077", markdown)

    def test_no_appids_is_a_reported_failure(self):
        doc = steam_news.build_digest(
            sample_options(apps=[], appids=[], sections=["steam-news"], rss=[]),
            fetcher=lambda url, timeout: b"{}",
            now=NOW,
        )
        self.assertEqual(doc["status"], "failed")
        self.assertEqual(doc["failures"][0]["error"], "no appids configured")
        self.assertIn("本节没有可用条目。", steam_news.render_markdown(doc))

    def test_sections_limit_requests(self):
        calls = []

        def fetcher(url, timeout):
            del timeout
            calls.append(url)
            return json.dumps(STORE_FIXTURE).encode()

        doc = steam_news.build_digest(
            sample_options(sections=["top-sellers"], rss=[]),
            fetcher=fetcher,
            now=NOW,
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("featuredcategories", calls[0])
        self.assertNotIn("GetNewsForApp", calls[0])
        item = doc["sections"]["top-sellers"]["items"][0]
        self.assertEqual(item["url"], "https://store.steampowered.com/bundle/32470")
        self.assertNotIn("steam-news", doc["sections"])

    def test_featured_keeps_unfilled_placeholder(self):
        doc = steam_news.build_digest(
            sample_options(sections=["featured"], rss=[]),
            fetcher=lambda url, timeout: json.dumps(STORE_FIXTURE).encode(),
            now=NOW,
        )
        item = doc["sections"]["featured"]["items"][0]
        self.assertEqual(item["summary"], "优惠截止时间：%1$s")
        self.assertIn("%1$s", steam_news.render_markdown(doc))

    def test_total_failure_exit_code_and_files(self):
        def fetcher(url, timeout):
            del url, timeout
            raise steam_news.FetchError("timeout")

        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "digest.json"
            md_path = Path(tmp) / "digest.md"
            original = steam_news.fetch_bytes
            steam_news.fetch_bytes = fetcher
            try:
                code = steam_news.main(
                    [
                        "--appids",
                        "730",
                        "--sections",
                        "steam-news,specials",
                        "--no-rss",
                        "--json",
                        str(json_path),
                        "--markdown",
                        str(md_path),
                        "--quiet",
                    ]
                )
            finally:
                steam_news.fetch_bytes = original
            self.assertEqual(code, 2)
            saved = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "failed")
            text = md_path.read_text(encoding="utf-8")
            self.assertIn("timeout", text)
            self.assertIn("本节没有可用条目。", text)
            self.assertNotIn("Hello", text)

    def test_cli_appids_replace_config_and_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "watch.json"
            config.write_text(
                json.dumps(
                    {
                        "apps": [{"appid": 570, "name": "Dota 2"}],
                        "sections": ["steam-news"],
                        "rss": [],
                    }
                ),
                encoding="utf-8",
            )
            json_path = Path(tmp) / "out.json"
            calls = []
            original = steam_news.fetch_bytes
            steam_news.fetch_bytes = fake_fetcher(calls)
            try:
                code = steam_news.main(
                    [
                        "--config",
                        str(config),
                        "--appids",
                        "730",
                        "--no-rss",
                        "--json",
                        str(json_path),
                        "--quiet",
                    ]
                )
            finally:
                steam_news.fetch_bytes = original
            self.assertEqual(code, 0)
            saved = json.loads(json_path.read_text(encoding="utf-8"))
            apps = saved["sections"]["steam-news"]["apps"]
            self.assertEqual([app["appid"] for app in apps], [730])
            self.assertEqual(apps[0]["name"], "")
            self.assertTrue(any("appid=730" in url for url in calls))
            self.assertFalse(any("appid=570" in url for url in calls))


class ConfigTests(unittest.TestCase):
    def test_example_watchlist(self):
        data = json.loads((ROOT / "examples" / "watchlist.json").read_text(encoding="utf-8"))
        options = steam_news.options_from_mapping(data)
        self.assertEqual(
            [feed["path"] for feed in options["rss_feeds"]],
            ["/xiaoheihe/news", "/gcores/news", "/gamersky/news/pc"],
        )
        self.assertEqual(options["cc"], "cn")
        self.assertEqual(options["lang"], "schinese")
        self.assertEqual(
            [app["appid"] for app in options["apps"]],
            [730, 570, 1091500, 1245620, 2358720],
        )

    def test_default_rss_when_omitted(self):
        options = steam_news.options_from_mapping({"sections": ["rss"], "apps": []})
        self.assertEqual(options["rss_feeds"], [dict(feed) for feed in steam_news.DEFAULT_RSS])

    def test_rejects_bad_rss_path_and_appid(self):
        with self.assertRaises(steam_news.ConfigError):
            steam_news.options_from_mapping(
                {"rss": [{"name": "x", "path": "https://evil.example/feed"}], "sections": ["rss"]}
            )
        with self.assertRaises(steam_news.ConfigError):
            steam_news.options_from_mapping({"appids": [0], "sections": ["steam-news"]})
        with self.assertRaises(steam_news.ConfigError):
            steam_news.options_from_mapping({"sections": ["not-a-section"]})

    def test_usage_error_exit_code(self):
        code = steam_news.main(["--count", "nope"])
        self.assertEqual(code, 1)
        code = steam_news.main(["--config", "missing-watchlist.json", "--quiet"])
        self.assertEqual(code, 1)

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            steam_news.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


@unittest.skipUnless(os.environ.get("STEAM_NEWS_LIVE") == "1", "set STEAM_NEWS_LIVE=1 to hit Steam")
class LiveTests(unittest.TestCase):
    def test_smoke(self):
        code = steam_news.main(["--smoke", "--timeout", "25"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
