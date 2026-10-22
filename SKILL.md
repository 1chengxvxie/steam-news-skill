---
name: steam-news
description: Use when the user wants a daily Steam or PC game news digest, official news for specific Steam appids, store specials, top sellers, or new releases, or Chinese headlines from 小黑盒, 机核, or 游民星空, including when a fetch failed and the digest must stay factual.
---

# Steam / PC game news

## Overview

The reader reads Simplified Chinese. These instructions are English. Write the digest in Simplified Chinese, using only items fetched in this run.

If a source did not return an item, that item is not in the digest.

## When to use

- Daily Steam / PC game news, 日报, 今日特惠, 热销, 新品
- Official news for a watchlist of appids
- 小黑盒, 机核, or 游民星空 headlines
- A section came back empty or a previous fetch failed

Do not use this for purchases, inventories, reviews, or anything that needs a Steam API key or a logged-in session.

## Fetch

From this repository (Python 3.9+, stdlib only):

```bash
python3 scripts/steam_news.py \
  --config examples/watchlist.json \
  --json digest.json \
  --markdown digest.md
```

`digest.json` is the source of truth. `digest.md` is the same facts with Chinese headings. Read the JSON before you summarize.

| Flag | Role |
| --- | --- |
| `--appids 730,570` | Replaces the watchlist for this run |
| `--count 5` | Items per app, store list, and RSS feed |
| `--cc cn --lang schinese` | Store region (`cc` and `l`) |
| `--sections steam-news,featured,specials,top-sellers,new-releases,rss` | Which blocks to fetch |
| `--rsshub https://rsshub.app` | RSSHub base URL. Self-host when the public instance fails |
| `--no-rss` | Skip Chinese RSS |
| `--timeout 20` | Per-request seconds |
| `--smoke` | Live check of appid 730 news plus store specials |

Watchlist `name` is a label the user typed. It is not a field from the news API. Keep it as a heading label; do not "correct" it from memory.

`GetNewsForApp` returns the app's Steam news feed, including external outlets Steam attaches to that app. Use each item's `feed` value. Do not relabel a news post as a developer announcement unless `feed` says so.

## Output contract

Write Simplified Chinese. Required order:

1. Date, `cc`, and `lang` from this run
2. 关注游戏 — per appid, title, URL, feed, date, one-sentence summary
3. 商店焦点, 特惠, 热销, 新品 — name, URL, `price_text` when present
4. 中文资讯 — per feed
5. 抓取失败 — every object in `failures`

Every bullet has the original `title` (or store `name`) and `url` copied from the JSON. Translate a non-Chinese `summary` into Simplified Chinese without adding facts. If you cannot translate a sentence faithfully, quote it. Do not add a translated title in parentheses.

Copy unfilled placeholders such as `%1$s` as-is. Do not invent the missing date.

## Failures

Each app, store section, and feed has `ok` and `error`.

- `ok: false`: publish the `error` string and `本节没有可用条目`. Zero items from that source.
- `ok: true` and an empty `items` list: say the source returned nothing. That is not a gap to fill.
- RSSHub failures do not remove Steam sections that succeeded.
- Exit code 2, missing JSON, or `status: failed` means nothing usable was fetched. Say so. Do not write the digest from memory.

`failures: []` means you do not add a failure section with guessed problems. A non-empty `failures` list means every entry appears in the digest.

## Sources

No API key.

| Section | URL |
| --- | --- |
| steam-news | `https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=<id>&count=<n>` |
| featured, specials, top-sellers, new-releases | `https://store.steampowered.com/api/featuredcategories?cc=<cc>&l=<lang>` |
| rss | `{rsshub}/xiaoheihe/news`, `{rsshub}/gcores/news`, `{rsshub}/gamersky/news/pc` |

`featured` is the spotlights and daily deal inside `featuredcategories`. Prices on that endpoint are minor units; use `price_text` from the script instead of dividing again.

If you cannot run the script, GET those same URLs and apply this contract. Do not switch to another site or to memory.

Public `rsshub.app` often times out or returns HTTP errors. Point `--rsshub` at a self-hosted instance. A failed feed stays a failed feed. `/gcores/category/news` is not the current route.

## Quick reference

```json
{
  "status": "partial",
  "failures": [{"section": "rss", "target": "机核", "error": "timeout"}],
  "sections": {
    "steam-news": {"ok": true, "apps": [{"appid": 730, "ok": true, "items": [{"title": "...", "url": "https://...", "summary": "..."}]}]},
    "rss": {"ok": false, "feeds": [{"name": "机核", "ok": false, "error": "timeout", "items": []}]}
  }
}
```

Publish the Steam item. For 机核, publish only `timeout` and `本节没有可用条目`.

## Common mistakes

| Excuse | Do this instead |
| --- | --- |
| "I already know today's Steam news" | Use only this run's JSON |
| "RSS timed out, but I remember 机核" | Quote the error and leave the feed empty |
| "Empty specials looks unfinished" | Write that the list was empty |
| "The spotlight body is missing a date" | Keep `%1$s`; do not fill it |
| "Translate the title into a better Chinese name" | Keep the API title string |
| "An older digest can fill the gap" | Older files are not this run |

## Red flags

Stop and rebuild from `digest.json` if you notice any of these:

- A title with no `url` from this run
- A discount that is not in a store item's `price_text` or `discount_percent`
- A smooth 中文资讯 section when that feed's `ok` is false
- A digest that mentions no failures while `failures` is non-empty
