# steam-news

给 Claude、Cursor 和其他 agent 用的 Steam / PC 游戏新闻技能。不需要 Steam API key。脚本只使用 Python 3 标准库，抓取失败会写进结果，不会编造新闻。

读者是中文用户。`SKILL.md` 的操作说明是英文，并要求 agent 用简体中文输出。

## 安装

需要 Python 3.9 或更高版本。没有依赖，不用 pip。

```bash
git clone https://github.com/1chengxvxie/steam-news-skill.git
cd steam-news-skill
python3 scripts/steam_news.py --help
```

把本仓库目录交给 agent，或让它读取根目录的 `SKILL.md`。

## 用法

关注列表在 [`examples/watchlist.json`](examples/watchlist.json)。`apps[].name` 只是你写的标签，Steam 新闻接口不返回游戏名。

```bash
python3 scripts/steam_news.py \
  --config examples/watchlist.json \
  --json digest.json \
  --markdown digest.md
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `--appids 730,570` | 本次用这些 appid，替换配置里的关注列表 |
| `--count 5` | 每个游戏、每个商店列表、每个 RSS 源的条数（1–30，默认 5） |
| `--cc cn --lang schinese` | 商店地区。对应 `featuredcategories` 的 `cc` 和 `l` |
| `--sections steam-news,featured,specials,top-sellers,new-releases,rss` | 选择板块 |
| `--rsshub https://rsshub.app` | RSSHub 根地址，默认公共实例 |
| `--no-rss` | 不抓中文 RSS |
| `--timeout 20` | 单个请求超时（秒） |
| `--smoke` | 只检查 appid 730 的官方新闻和商店特惠 |

`--json` 与 `--markdown` 可以只写其中一个。不加这两个参数时，Markdown 打到标准输出；`--format json` 则输出 JSON。

退出码：`0` 至少一个板块成功，`2` 请求的板块全部失败，`1` 参数或配置错误。部分成功仍是 `0`，失败明细在 `failures` 里。

## 每日摘要提示词

把下面这段交给已经加载本技能的 agent：

```text
请用 steam-news 生成本期 Steam / PC 游戏日报。运行：
python3 scripts/steam_news.py --config examples/watchlist.json --json digest.json --markdown digest.md
只根据本次 digest.json 用简体中文写日报，顺序为：关注游戏、商店焦点、特惠、热销、新品、中文资讯、抓取失败。
每条保留接口原标题和链接。非中文摘要只做忠实翻译，不补充事实。
failures 里的每一条都要原样写出错误，并写「本节没有可用条目」。
不要用记忆、旧日报或其他网站填补失败或空白的来源。不要编造折扣、日期、评分或标题。
```

## 数据源

不需要 key。

| 板块 | 地址 |
| --- | --- |
| 每个 app 的官方新闻 | `https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=<id>&count=<n>` |
| 焦点、特惠、热销、新品 | `https://store.steampowered.com/api/featuredcategories?cc=<cc>&l=<lang>` |
| 小黑盒 | `{rsshub}/xiaoheihe/news` |
| 机核 | `{rsshub}/gcores/news` |
| 游民星空 | `{rsshub}/gamersky/news/pc` |

`featured` 对应接口里的 spotlight 和 daily deal。`specials`、`top_sellers`、`new_releases` 是同一次响应里的三个列表。价格字段是货币最小单位，脚本写成 `price_text`（例如分转成 `¥89.40`）。

游民星空的 `type` 还可以是 `today`、`ns`、`mobile`、`web`、`industry`、`hardware`、`tech`。机核旧路径 `/gcores/category/news` 不稳定，请用 `/gcores/news`。RSS 路径写在配置的 `rss` 数组里，必须是以 `/` 开头的 RSSHub 路由。

## 局限

- 公共实例 `https://rsshub.app` 经常超时、限流或返回错误。建议自建 RSSHub，再用 `--rsshub` 指向它。某个源失败时，其他板块仍然保留。
- `featuredcategories` 是商店公开接口，不是带 key 的 Web API，字段可能变化。
- `GetNewsForApp` 返回的是该应用在 Steam 上的新闻流，包含 Steam 挂上来的外部媒体，不一定是开发者公告。用条目里的 `feed` 区分来源，不要把媒体稿改写成官方补丁说明。
- 新闻正文按 `--maxlength` 截断，并去掉 HTML。摘要不够时不要自行补全。
- Spotlight 的 `body` 可能含未填充的占位符（如 `%1$s`）。脚本原样保留，不要编造日期。
- 脚本不会调用大模型。润色和翻译由 agent 完成，而且不能增加事实。
- 看不到需要登录的内容，也不能代替 Steam 商店的购买或愿望单操作。
- `examples/` 里的样例输出来自某一次真实抓取，不是持续更新的新闻源。

## 开发

```bash
python3 -m unittest discover -s tests -v
python3 scripts/steam_news.py --smoke
```

单元测试默认不访问网络。`STEAM_NEWS_LIVE=1 python3 -m unittest tests.test_steam_news.LiveTests` 会再跑一次线上 smoke。

---

# steam-news

An agent skill for daily Steam and PC game news. No Steam API key. The script uses the Python 3 standard library only. Fetch failures are written into the result; the tool does not invent news.

The primary audience reads Chinese. `SKILL.md` tells the agent, in English, to answer in Simplified Chinese.

## Install

Python 3.9 or newer. No third-party packages.

```bash
git clone https://github.com/1chengxvxie/steam-news-skill.git
cd steam-news-skill
python3 scripts/steam_news.py --help
```

Point an agent at this repository, or have it read `SKILL.md`.

## Usage

The sample watchlist is [`examples/watchlist.json`](examples/watchlist.json). Each `apps[].name` is a label you wrote. The news API does not return the game's name.

```bash
python3 scripts/steam_news.py \
  --config examples/watchlist.json \
  --json digest.json \
  --markdown digest.md
```

| Flag | Role |
| --- | --- |
| `--appids 730,570` | Appids for this run; replaces configured apps |
| `--count 5` | Items per app, store list, and RSS feed (1–30, default 5) |
| `--cc cn --lang schinese` | Store region, sent as `cc` and `l` |
| `--sections steam-news,featured,specials,top-sellers,new-releases,rss` | Which blocks to fetch |
| `--rsshub https://rsshub.app` | RSSHub base URL (public instance by default) |
| `--no-rss` | Skip Chinese RSS |
| `--timeout 20` | Per-request timeout in seconds |
| `--smoke` | Check appid 730 news and store specials only |

Omit `--json` and `--markdown` to print Markdown on stdout. `--format json` prints JSON instead.

Exit codes: `0` if at least one section succeeded, `2` if every requested section failed, `1` on usage or config errors. Partial success is still `0`; the errors are in `failures`.

## Daily digest prompt

```text
Use the steam-news skill for today's Steam / PC digest. Run:
python3 scripts/steam_news.py --config examples/watchlist.json --json digest.json --markdown digest.md
From this run's digest.json only, write Simplified Chinese in this order: watched games, store spotlights, specials, top sellers, new releases, Chinese feeds, failures.
Keep each original title and URL. Translate a non-Chinese summary faithfully and add no facts.
For every object in failures, quote the error and write 本节没有可用条目.
Do not fill a failed or empty source from memory, an older digest, or another site. Do not invent discounts, dates, scores, or titles.
```

## Sources

No API key.

| Section | URL |
| --- | --- |
| Official news per app | `https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid=<id>&count=<n>` |
| Spotlights, specials, top sellers, new releases | `https://store.steampowered.com/api/featuredcategories?cc=<cc>&l=<lang>` |
| 小黑盒 Xiaoheihe | `{rsshub}/xiaoheihe/news` |
| 机核 Gcores | `{rsshub}/gcores/news` |
| 游民星空 Gamersky | `{rsshub}/gamersky/news/pc` |

`featured` is the spotlights and daily deal in that store payload. `specials`, `top_sellers`, and `new_releases` are the other lists in the same response. Prices are minor currency units; the script formats `price_text` (cents become `¥89.40`).

Gamersky `type` also accepts `today`, `ns`, `mobile`, `web`, `industry`, `hardware`, and `tech`. The older Gcores path `/gcores/category/news` is unreliable; use `/gcores/news`. RSS paths live in the config `rss` array and must be RSSHub routes starting with `/`.

## Limitations

- Public `https://rsshub.app` often times out, rate-limits, or returns an error. Self-host RSSHub and pass `--rsshub`. A failed feed does not drop sections that succeeded.
- `featuredcategories` is a public store endpoint, not the keyed Web API, and its shape can change.
- `GetNewsForApp` is the app's Steam news feed, including external outlets Steam attaches to the app. Use the item's `feed` value. Do not rewrite a press story as a developer patch note.
- News bodies are truncated to `--maxlength` and stripped of HTML. Do not complete them from memory.
- Spotlight `body` text may contain an unfilled placeholder such as `%1$s`. The script keeps it. Do not invent the date.
- The script does not call a model. The agent may edit wording and translate, and may not add facts.
- Login-only Steam content, purchases, and wishlists are out of scope.
- Sample output under `examples/` is one real run, not a live feed.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/steam_news.py --smoke
```

Unit tests stay offline. `STEAM_NEWS_LIVE=1 python3 -m unittest tests.test_steam_news.LiveTests` repeats the live smoke check.
