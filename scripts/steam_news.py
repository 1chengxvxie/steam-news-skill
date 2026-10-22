#!/usr/bin/env python3
"""Keyless Steam / PC game news digest.

Fetches official Steam news, the public store featured-categories feed, and
optional Chinese headlines through RSSHub. Stdlib only. Failures are recorded
in the output; this script never invents items.

Exit codes: 0 at least one section succeeded, 2 every requested section failed,
1 usage or config error.
"""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "steam-news-skill/1.0 (+https://github.com/1chengxvxie/steam-news-skill)"
STEAM_NEWS_ENDPOINT = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
STORE_ENDPOINT = "https://store.steampowered.com/api/featuredcategories"
MAX_BYTES = 1_500_000
SECTION_ORDER = (
    "steam-news",
    "featured",
    "specials",
    "top-sellers",
    "new-releases",
    "rss",
)
STORE_KEYS = {
    "specials": "specials",
    "top-sellers": "top_sellers",
    "new-releases": "new_releases",
}
DEFAULT_RSS = (
    {"name": "小黑盒", "path": "/xiaoheihe/news"},
    {"name": "机核", "path": "/gcores/news"},
    {"name": "游民星空", "path": "/gamersky/news/pc"},
)
STATUS_ZH = {"ok": "全部成功", "partial": "部分成功", "failed": "全部失败"}
SECTION_ZH = {
    "steam-news": "关注列表 · Steam 官方新闻",
    "featured": "商店焦点",
    "specials": "特惠",
    "top-sellers": "热销",
    "new-releases": "新品",
    "rss": "中文游戏资讯",
}
CURRENCY_SYMBOLS = {
    "USD": "$",
    "CNY": "¥",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "KRW": "₩",
    "TWD": "NT$",
    "HKD": "HK$",
    "RUB": "₽",
    "CAD": "C$",
    "AUD": "A$",
}
ZERO_DECIMAL = {"JPY", "KRW", "VND"}
STORE_KIND = {0: ("app", "app"), 1: ("package", "sub"), 2: ("bundle", "bundle")}
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class FetchError(Exception):
    """A source could not be fetched or parsed. The message is safe to publish."""


class ConfigError(Exception):
    """The invocation or watchlist is invalid."""


def plain_text(value: str, limit: int) -> str:
    text = _TAG_RE.sub(" ", value or "")
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    if limit >= 0 and len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def unix_to_iso(timestamp: int) -> str:
    return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return unix_to_iso(int(raw))
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def minor_units(value: object) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def format_money(amount: int, currency: str) -> str:
    code = (currency or "").upper()
    major = amount / 100
    if code in ZERO_DECIMAL:
        shown = str(int(round(major)))
    else:
        shown = f"{major:.2f}"
    symbol = CURRENCY_SYMBOLS.get(code, "")
    if symbol:
        return f"{symbol}{shown}"
    if code:
        return f"{shown} {code}"
    return shown


def price_text(raw: dict) -> str | None:
    if not any(key in raw for key in ("original_price", "final_price", "currency")):
        return None
    original = minor_units(raw.get("original_price"))
    final = minor_units(raw.get("final_price"))
    currency = str(raw.get("currency") or "")
    percent = minor_units(raw.get("discount_percent")) or 0
    if original is None and final in (None, 0):
        return "未标价"
    if final == 0 and original == 0:
        return "免费"
    amount = final if final is not None else original
    if amount is None:
        return "未标价"
    formatted = format_money(amount, currency)
    if (
        percent > 0
        and original is not None
        and final is not None
        and original != final
    ):
        return f"{formatted}（原价 {format_money(original, currency)}，-{percent}%）"
    return formatted


def fetch_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(400)
        snippet = body.decode("utf-8", "replace").strip().splitlines()
        detail = snippet[0][:180] if snippet else ""
        message = f"HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail}"
        raise FetchError(message) from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"network error: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise FetchError(f"network error: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise FetchError(f"response exceeded {MAX_BYTES} bytes")
    return data


def loads_json(data: bytes) -> dict:
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FetchError("JSON response was not an object")
    return parsed


def steam_news_url(appid: int, count: int, maxlength: int) -> str:
    query = urllib.parse.urlencode(
        {"appid": appid, "count": count, "maxlength": maxlength, "format": "json"}
    )
    return f"{STEAM_NEWS_ENDPOINT}?{query}"


def store_url(cc: str, lang: str) -> str:
    query = urllib.parse.urlencode({"cc": cc, "l": lang})
    return f"{STORE_ENDPOINT}?{query}"


def rss_url(base: str, path: str) -> str:
    return base.rstrip("/") + path


def parse_news_items(payload: dict, limit: int, summary_limit: int) -> list[dict]:
    appnews = payload.get("appnews")
    if not isinstance(appnews, dict):
        raise FetchError("response missing appnews")
    raw_items = appnews.get("newsitems") or []
    if not isinstance(raw_items, list):
        raise FetchError("newsitems was not a list")
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        date = ""
        if isinstance(raw.get("date"), int):
            date = unix_to_iso(raw["date"])
        items.append(
            {
                "gid": str(raw.get("gid") or ""),
                "title": plain_text(str(raw.get("title") or ""), 300),
                "url": str(raw.get("url") or ""),
                "author": plain_text(str(raw.get("author") or ""), 120),
                "feed": plain_text(str(raw.get("feedlabel") or raw.get("feedname") or ""), 120),
                "date": date,
                "summary": plain_text(str(raw.get("contents") or ""), summary_limit),
            }
        )
    items.sort(key=lambda item: item["date"], reverse=True)
    return items[:limit]


def store_item(raw: dict, group: str | None) -> dict:
    kind, slug = STORE_KIND.get(raw.get("type"), ("unknown", "app"))
    url = str(raw.get("url") or "")
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://store.steampowered.com" + url
    item_id = raw.get("id")
    if not isinstance(item_id, int):
        item_id = None
        if not url:
            kind = "spotlight"
    elif not raw.get("url"):
        if raw.get("type") not in STORE_KIND:
            kind, slug = "unknown", "app"
        url = f"https://store.steampowered.com/{slug}/{item_id}"
    else:
        kind = STORE_KIND.get(raw.get("type"), ("spotlight", "app"))[0]
    if item_id is None and url:
        kind = "spotlight"
    ends = None
    if isinstance(raw.get("discount_expiration"), int):
        ends = unix_to_iso(raw["discount_expiration"])
    summary = plain_text(str(raw.get("body") or ""), 240)
    return {
        "id": item_id,
        "kind": kind,
        "name": plain_text(str(raw.get("name") or ""), 200),
        "url": url,
        "group": group or "",
        "currency": raw.get("currency") or None,
        "discount_percent": minor_units(raw.get("discount_percent")) or 0,
        "original_price_minor": minor_units(raw.get("original_price")),
        "final_price_minor": minor_units(raw.get("final_price")),
        "price_text": price_text(raw),
        "discount_ends": ends,
        "header_image": raw.get("header_image") or None,
        "summary": summary,
    }


def parse_store(payload: dict, sections: list[str], count: int) -> dict[str, dict]:
    parsed: dict[str, dict] = {}
    if "featured" in sections:
        items: list[dict] = []
        for key, block in payload.items():
            if not isinstance(block, dict):
                continue
            if block.get("id") not in {"cat_spotlight", "cat_dailydeal"}:
                continue
            group = str(block.get("name") or key)
            for raw in block.get("items") or []:
                if isinstance(raw, dict):
                    items.append(store_item(raw, group))
                if len(items) >= 12:
                    break
            if len(items) >= 12:
                break
        parsed["featured"] = {"ok": True, "error": None, "items": items}
    for section, key in STORE_KEYS.items():
        if section not in sections:
            continue
        block = payload.get(key)
        if not isinstance(block, dict):
            parsed[section] = {
                "ok": False,
                "error": f"category {key} not in response",
                "name": None,
                "items": [],
            }
            continue
        items = []
        for raw in block.get("items") or []:
            if isinstance(raw, dict):
                items.append(store_item(raw, str(block.get("name") or "")))
            if len(items) >= count:
                break
        parsed[section] = {
            "ok": True,
            "error": None,
            "name": block.get("name"),
            "items": items,
        }
    return parsed


def _entry_parts(element) -> dict:
    title = ""
    links: list[tuple[str, str]] = []
    dates: dict[str, str] = {}
    summary = ""
    encoded = ""
    for child in list(element):
        name = child.tag.rsplit("}", 1)[-1]
        text = "".join(child.itertext()).strip()
        if name == "title" and not title:
            title = text
        elif name == "link":
            href = (child.attrib.get("href") or text).strip()
            if href:
                links.append((child.attrib.get("rel", "alternate"), href))
        elif name in {"pubDate", "published", "updated", "date"} and name not in dates:
            dates[name] = text
        elif name in {"description", "summary"} and text and not summary:
            summary = text
        elif name == "encoded" and text and not encoded:
            encoded = text
    link = ""
    for rel, href in links:
        if rel == "alternate":
            link = href
            break
    if not link and links:
        link = links[0][1]
    raw_date = dates.get("pubDate") or dates.get("published") or dates.get("updated") or dates.get("date") or ""
    return {
        "title": title,
        "url": link,
        "date": normalize_date(raw_date),
        "summary": summary or encoded,
    }


def parse_feed(data: bytes, limit: int, summary_limit: int) -> list[dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FetchError(f"invalid feed XML: {exc}") from exc
    items = []
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1]
        if name not in {"item", "entry"}:
            continue
        parts = _entry_parts(element)
        if not parts["title"] and not parts["url"]:
            continue
        parts["title"] = plain_text(parts["title"], 300)
        parts["summary"] = plain_text(parts["summary"], summary_limit)
        items.append(parts)
        if len(items) >= limit:
            break
    return items


def _app_record(app: dict, *, ok: bool, error: str | None, items: list[dict]) -> dict:
    return {
        "appid": app["appid"],
        "name": app.get("name") or "",
        "ok": ok,
        "error": error,
        "items": items,
    }


def fetch_app(app: dict, options, fetcher) -> dict:
    url = steam_news_url(app["appid"], options["count"], options["maxlength"])
    try:
        payload = loads_json(fetcher(url, options["timeout"]))
        items = parse_news_items(payload, options["count"], options["maxlength"])
    except FetchError as exc:
        return _app_record(app, ok=False, error=str(exc), items=[])
    return _app_record(app, ok=True, error=None, items=items)


def fetch_feed(feed: dict, options, fetcher) -> dict:
    url = rss_url(options["rsshub_base"], feed["path"])
    record = {"name": feed["name"], "url": url, "ok": False, "error": None, "items": []}
    try:
        items = parse_feed(fetcher(url, options["timeout"]), options["count"], options["maxlength"])
    except FetchError as exc:
        record["error"] = str(exc)
        return record
    record["ok"] = True
    record["items"] = items
    return record


def _map(fn, items: list) -> list:
    if not items:
        return []
    workers = min(6, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def collect_failures(sections: dict) -> list[dict]:
    rows = []
    steam = sections.get("steam-news")
    if steam:
        apps = steam.get("apps") or []
        if apps:
            for app in apps:
                if not app.get("ok"):
                    rows.append(
                        {
                            "section": "steam-news",
                            "target": f"appid {app.get('appid')}",
                            "error": app.get("error") or "unknown error",
                        }
                    )
        elif not steam.get("ok"):
            rows.append(
                {
                    "section": "steam-news",
                    "target": "ISteamNews",
                    "error": steam.get("error") or "unknown error",
                }
            )
    for key in ("featured", "specials", "top-sellers", "new-releases"):
        block = sections.get(key)
        if block and not block.get("ok"):
            rows.append(
                {
                    "section": key,
                    "target": "featuredcategories",
                    "error": block.get("error") or "unknown error",
                }
            )
    rss = sections.get("rss")
    if rss:
        feeds = rss.get("feeds") or []
        if feeds:
            for feed in feeds:
                if not feed.get("ok"):
                    rows.append(
                        {
                            "section": "rss",
                            "target": feed.get("name") or feed.get("url") or "rss",
                            "error": feed.get("error") or "unknown error",
                        }
                    )
        elif not rss.get("ok"):
            rows.append(
                {
                    "section": "rss",
                    "target": "rsshub",
                    "error": rss.get("error") or "unknown error",
                }
            )
    return rows


def build_digest(options: dict, fetcher=None, now: datetime | None = None) -> dict:
    fetcher = fetcher or fetch_bytes
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = generated.strftime("%Y-%m-%dT%H:%M:%SZ")
    sections: dict = {}
    requested = list(options["sections"])
    store_sections = [name for name in requested if name in {"featured", *STORE_KEYS}]

    if "steam-news" in requested:
        if not options["apps"]:
            sections["steam-news"] = {
                "ok": False,
                "error": "no appids configured",
                "apps": [],
            }
        else:
            apps = _map(lambda app: fetch_app(app, options, fetcher), options["apps"])
            sections["steam-news"] = {
                "ok": all(app["ok"] for app in apps),
                "error": None,
                "apps": apps,
            }

    if store_sections:
        url = store_url(options["cc"], options["lang"])
        try:
            payload = loads_json(fetcher(url, options["timeout"]))
            parsed = parse_store(payload, store_sections, options["count"])
        except FetchError as exc:
            parsed = {
                name: {"ok": False, "error": str(exc), "name": None, "items": []}
                for name in store_sections
            }
        for name in store_sections:
            sections[name] = parsed[name]

    if "rss" in requested:
        if not options["rsshub_base"]:
            sections["rss"] = {"ok": False, "error": "rsshub base URL is empty", "feeds": []}
        elif not options["rss_feeds"]:
            sections["rss"] = {"ok": False, "error": "no RSS feeds configured", "feeds": []}
        else:
            feeds = _map(lambda feed: fetch_feed(feed, options, fetcher), options["rss_feeds"])
            sections["rss"] = {
                "ok": all(feed["ok"] for feed in feeds),
                "error": None,
                "feeds": feeds,
            }

    ordered = {name: sections[name] for name in requested}
    flags = [bool(ordered[name].get("ok")) for name in requested]
    if flags and all(flags):
        status = "ok"
    elif any(flags):
        status = "partial"
    else:
        status = "failed"
    return {
        "schema_version": 1,
        "generator": "steam-news-skill",
        "generated_at": generated_at,
        "status": status,
        "region": {"cc": options["cc"], "lang": options["lang"]},
        "sources": {
            "steam_news": STEAM_NEWS_ENDPOINT,
            "store": store_url(options["cc"], options["lang"]) if store_sections else None,
            "rsshub_base": options["rsshub_base"] if "rss" in requested else None,
        },
        "sections": ordered,
        "failures": collect_failures(ordered),
    }


def md_link(title: str, url: str) -> str:
    label = (title or "未命名").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
    if not url:
        return label
    # CommonMark destinations end at the first space. Angle brackets keep the raw URL.
    if any(char in url for char in " \t()"):
        dest = "<" + url.replace(">", "%3E") + ">"
    else:
        dest = url
    return f"[{label}]({dest})"


def _bullet(title: str, url: str, meta: list[str], summary: str) -> list[str]:
    suffix = f" · {' · '.join(part for part in meta if part)}" if any(meta) else ""
    lines = [f"- {md_link(title, url)}{suffix}"]
    if summary:
        lines.append(f"  {summary}")
    return lines


def render_markdown(doc: dict) -> str:
    region = doc["region"]
    lines = [
        "# Steam / PC 游戏日报",
        "",
        f"- 生成时间（UTC）：{doc['generated_at']}",
        f"- 地区：cc={region['cc']}，lang={region['lang']}",
        f"- 状态：{STATUS_ZH.get(doc['status'], doc['status'])}",
        "- 条目仅来自本次抓取。失败来源没有新闻，不要用记忆或猜测补齐。",
        "",
    ]
    sections = doc["sections"]
    if "steam-news" in sections:
        lines.append(f"## {SECTION_ZH['steam-news']}")
        lines.append("")
        lines.append("应用名称是关注列表里的标签，不是接口返回的游戏名。")
        lines.append("")
        block = sections["steam-news"]
        if block.get("error") and not block.get("apps"):
            lines.append(f"抓取失败：{block['error']}")
            lines.append("")
            lines.append("本节没有可用条目。")
            lines.append("")
        for app in block.get("apps") or []:
            label = app.get("name") or f"appid {app['appid']}"
            lines.append(f"### {label}")
            lines.append("")
            lines.append(f"appid {app['appid']}")
            lines.append("")
            if not app.get("ok"):
                lines.append(f"抓取失败：{app.get('error')}")
                lines.append("")
                lines.append("本节没有可用条目。")
                lines.append("")
                continue
            if not app.get("items"):
                lines.append("接口成功，但没有返回新闻。")
                lines.append("")
                continue
            for item in app["items"]:
                lines.extend(
                    _bullet(
                        item.get("title") or "",
                        item.get("url") or "",
                        [item.get("date") or "", item.get("feed") or ""],
                        item.get("summary") or "",
                    )
                )
            lines.append("")
    for key in ("featured", "specials", "top-sellers", "new-releases"):
        if key not in sections:
            continue
        block = sections[key]
        lines.append(f"## {SECTION_ZH[key]}")
        lines.append("")
        api_name = block.get("name")
        if api_name:
            lines.append(f"接口分类名：{api_name}")
            lines.append("")
        if not block.get("ok"):
            lines.append(f"抓取失败：{block.get('error')}")
            lines.append("")
            lines.append("本节没有可用条目。")
            lines.append("")
            continue
        if not block.get("items"):
            lines.append("接口成功，但列表为空。")
            lines.append("")
            continue
        for item in block["items"]:
            meta = [item.get("price_text") or ""]
            lines.extend(
                _bullet(item.get("name") or "", item.get("url") or "", meta, item.get("summary") or "")
            )
        lines.append("")
    if "rss" in sections:
        lines.append(f"## {SECTION_ZH['rss']}")
        lines.append("")
        block = sections["rss"]
        if block.get("error") and not block.get("feeds"):
            lines.append(f"抓取失败：{block['error']}")
            lines.append("")
            lines.append("本节没有可用条目。")
            lines.append("")
        for feed in block.get("feeds") or []:
            lines.append(f"### {feed.get('name') or feed.get('url')}")
            lines.append("")
            lines.append(f"Feed：{feed.get('url')}")
            lines.append("")
            if not feed.get("ok"):
                lines.append(f"抓取失败：{feed.get('error')}")
                lines.append("")
                lines.append("本节没有可用条目。")
                lines.append("")
                continue
            if not feed.get("items"):
                lines.append("接口成功，但没有返回条目。")
                lines.append("")
                continue
            for item in feed["items"]:
                lines.extend(
                    _bullet(
                        item.get("title") or "",
                        item.get("url") or "",
                        [item.get("date") or ""],
                        item.get("summary") or "",
                    )
                )
            lines.append("")
    lines.append("## 抓取失败")
    lines.append("")
    if not doc.get("failures"):
        lines.append("无。")
    else:
        for failure in doc["failures"]:
            lines.append(
                f"- {failure['section']} / {failure['target']}：{failure['error']}"
            )
    lines.append("")
    lines.append("## 数据源")
    lines.append("")
    sources = doc.get("sources") or {}
    if "steam-news" in sections:
        lines.append(f"- Steam 新闻：{sources.get('steam_news')}")
    if sources.get("store"):
        lines.append(f"- Steam 商店：{sources.get('store')}")
    if sources.get("rsshub_base"):
        lines.append(f"- RSSHub：{sources.get('rsshub_base')}")
    lines.append("")
    return "\n".join(lines)


def _coerce_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ConfigError(f"{field} must be an integer")


def _positive_int(value: object, field: str, minimum: int, maximum: int) -> int:
    number = _coerce_int(value, field)
    if number < minimum or number > maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return number


def _canon_section(name: str) -> str:
    key = str(name).strip().lower().replace("_", "-")
    if key not in SECTION_ORDER:
        known = ", ".join(SECTION_ORDER)
        raise ConfigError(f"unknown section {name!r}; expected one of: {known}")
    return key


def _parse_appid(value: object) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"invalid appid {value!r}")
    if isinstance(value, int):
        appid = value
    elif isinstance(value, str) and value.strip().isdigit():
        appid = int(value.strip())
    else:
        raise ConfigError(f"invalid appid {value!r}")
    if appid <= 0:
        raise ConfigError(f"invalid appid {value!r}")
    return appid


def _apps_from_config(data: dict) -> list[dict]:
    apps: list[dict] = []
    seen: set[int] = set()
    raw_apps = data.get("apps") or []
    if not isinstance(raw_apps, list):
        raise ConfigError("apps must be a list")
    for entry in raw_apps:
        if isinstance(entry, int):
            appid = _parse_appid(entry)
            name = ""
        elif isinstance(entry, dict):
            appid = _parse_appid(entry.get("appid", entry.get("id")))
            name = str(entry.get("name") or "")
        else:
            raise ConfigError(f"invalid app entry {entry!r}")
        if appid not in seen:
            apps.append({"appid": appid, "name": name})
            seen.add(appid)
    raw_ids = data.get("appids") or []
    if not isinstance(raw_ids, list):
        raise ConfigError("appids must be a list")
    for entry in raw_ids:
        appid = _parse_appid(entry)
        if appid not in seen:
            apps.append({"appid": appid, "name": ""})
            seen.add(appid)
    return apps


def _feeds_from_config(data: dict, explicit: bool) -> list[dict]:
    if not explicit:
        return [dict(feed) for feed in DEFAULT_RSS]
    raw = data.get("rss") or []
    if not isinstance(raw, list):
        raise ConfigError("rss must be a list")
    feeds = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"invalid rss entry {entry!r}")
        path = str(entry.get("path") or "")
        name = str(entry.get("name") or path)
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ConfigError(f"rss path must be a relative RSSHub route, got {path!r}")
        feeds.append({"name": name, "path": path})
    return feeds


def _check_region(cc: str, lang: str) -> None:
    if not re.fullmatch(r"[A-Za-z]{2}", cc):
        raise ConfigError(f"cc must be a 2-letter country code, got {cc!r}")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,16}", lang):
        raise ConfigError(f"lang must be 1-16 letters, digits, or hyphens, got {lang!r}")


def _check_rsshub(base: str) -> str:
    if base == "":
        return ""
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"rsshub base must be an http(s) URL, got {base!r}")
    return base.rstrip("/")


def options_from_mapping(data: dict, cli: dict | None = None) -> dict:
    cli = cli or {}
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    count = _positive_int(cli["count"] if cli.get("count") is not None else data.get("count", 5), "count", 1, 30)
    maxlength = _positive_int(
        cli["maxlength"] if cli.get("maxlength") is not None else data.get("maxlength", 280),
        "maxlength",
        40,
        4000,
    )
    timeout = cli.get("timeout") if cli.get("timeout") is not None else data.get("timeout", 20)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ConfigError("timeout must be a number")
    if timeout < 1 or timeout > 60:
        raise ConfigError("timeout must be between 1 and 60 seconds")
    cc = str(cli.get("cc") or data.get("cc") or "cn")
    lang = str(cli.get("lang") or data.get("lang") or "schinese")
    _check_region(cc, lang)
    rsshub = cli.get("rsshub") if cli.get("rsshub") is not None else data.get("rsshub_base", "https://rsshub.app")
    rsshub = _check_rsshub(str(rsshub))
    if cli.get("sections"):
        sections = [_canon_section(part) for part in cli["sections"]]
    elif data.get("sections"):
        sections = [_canon_section(part) for part in data["sections"]]
    else:
        sections = list(SECTION_ORDER)
    if cli.get("no_rss"):
        sections = [name for name in sections if name != "rss"]
    # Preserve order, drop duplicates.
    deduped = []
    for name in sections:
        if name not in deduped:
            deduped.append(name)
    if not deduped:
        raise ConfigError("at least one section is required")
    if cli.get("appids"):
        apps = [{"appid": _parse_appid(part), "name": ""} for part in cli["appids"]]
    else:
        apps = _apps_from_config(data)
    feeds = _feeds_from_config(data, explicit="rss" in data)
    return {
        "apps": apps,
        "count": count,
        "maxlength": maxlength,
        "timeout": float(timeout),
        "cc": cc.lower(),
        "lang": lang,
        "rsshub_base": rsshub,
        "sections": deduped,
        "rss_feeds": feeds if "rss" in deduped else [],
    }


def _split_appids(value: str) -> list[str]:
    parts = [part.strip() for part in value.replace(" ", ",").split(",")]
    return [part for part in parts if part]


def _split_sections(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ConfigError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="steam_news.py",
        description="Fetch a keyless Steam / PC game news digest as JSON and Markdown.",
        epilog=(
            "Exit 0 when at least one section succeeded, 2 when every section failed, "
            "1 on usage or config errors. Public rsshub.app often times out; self-host and pass --rsshub."
        ),
    )
    parser.add_argument("--config", help="watchlist JSON (see examples/watchlist.json)")
    parser.add_argument("--appids", help="comma-separated Steam appids; replaces config apps")
    parser.add_argument("--count", type=int, help="items per app, store list, and RSS feed (1-30)")
    parser.add_argument("--maxlength", type=int, help="max summary characters (40-4000)")
    parser.add_argument("--cc", help="store country code, for example cn or us")
    parser.add_argument("--lang", help="store language passed as l, for example schinese")
    parser.add_argument("--rsshub", help="RSSHub base URL (default https://rsshub.app)")
    parser.add_argument("--sections", help="comma list: " + ",".join(SECTION_ORDER))
    parser.add_argument("--no-rss", action="store_true", help="skip RSSHub even if configured")
    parser.add_argument("--timeout", type=float, help="per-request timeout in seconds (1-60)")
    parser.add_argument("--json", dest="json_path", help="write JSON digest to this path")
    parser.add_argument("--markdown", dest="markdown_path", help="write Markdown digest to this path")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "both"),
        default="markdown",
        help="stdout format (default markdown). both prints markdown; use --json and --markdown for files",
    )
    parser.add_argument("--quiet", action="store_true", help="do not print the digest to stdout")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="live check: Steam news for appid 730 and store specials",
    )
    return parser


def options_from_args(args: argparse.Namespace) -> dict:
    data: dict = {}
    if args.config:
        path = Path(args.config)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"config not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config must be a JSON object")
    return options_from_mapping(
        data,
        {
            "count": args.count,
            "maxlength": args.maxlength,
            "timeout": args.timeout,
            "cc": args.cc,
            "lang": args.lang,
            "rsshub": args.rsshub,
            "sections": _split_sections(args.sections) if args.sections else None,
            "no_rss": args.no_rss,
            "appids": _split_appids(args.appids) if args.appids else None,
        },
    )


def run_smoke(timeout: float | None) -> int:
    options = options_from_mapping(
        {
            "apps": [{"appid": 730, "name": "Counter-Strike 2"}],
            "sections": ["steam-news", "specials"],
            "count": 1,
            "maxlength": 180,
            "cc": "cn",
            "lang": "schinese",
            "rss": [],
        },
        {"timeout": timeout if timeout is not None else 20, "no_rss": True},
    )
    doc = build_digest(options)
    news = doc["sections"]["steam-news"]["apps"][0]
    specials = doc["sections"]["specials"]
    report = {
        "status": "ok" if news["ok"] and specials["ok"] else "failed",
        "steam_news": {
            "ok": news["ok"],
            "error": news["error"],
            "count": len(news["items"]),
            "title": news["items"][0]["title"] if news["items"] else None,
        },
        "specials": {
            "ok": specials["ok"],
            "error": specials["error"],
            "count": len(specials["items"]),
            "title": specials["items"][0]["name"] if specials["items"] else None,
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 2


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.smoke:
        try:
            return run_smoke(args.timeout)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    try:
        options = options_from_args(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    doc = build_digest(options)
    markdown = render_markdown(doc)
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if args.json_path:
        Path(args.json_path).write_text(payload, encoding="utf-8")
    if args.markdown_path:
        Path(args.markdown_path).write_text(markdown, encoding="utf-8")
    if not args.quiet:
        if args.format == "json":
            sys.stdout.write(payload)
        else:
            sys.stdout.write(markdown)
    return 0 if doc["status"] != "failed" else 2


if __name__ == "__main__":
    sys.exit(main())
