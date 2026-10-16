# WeChat digest (pushplus)

[简体中文](WECHAT-DIGEST.zh-CN.md)

Every day at **09:00** and **18:00** Beijing time, GitHub Actions reads the catalog, collects new articles inside the time window, renders them as a portrait, international-magazine style HTML page, and pushes it to WeChat through [pushplus](https://www.pushplus.plus/).

| | |
| --- | --- |
| Workflow | [`.github/workflows/wechat-digest.yml`](.github/workflows/wechat-digest.yml) |
| Script | [`scripts/wechat_digest.py`](scripts/wechat_digest.py) (standard library only, no dependencies) |
| Tests | [`tests/test_wechat_digest.py`](tests/test_wechat_digest.py) |

## Before you start

1. Open [pushplus](https://www.pushplus.plus/), sign in by scanning the QR code with WeChat, and copy your `token` from the one-to-one push page.
2. In the repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

   | Name | Required | Purpose |
   | --- | --- | --- |
   | `PUSHPLUS_TOKEN` | Yes | Your personal pushplus token |
   | `PUSHPLUS_TOPIC` | No | Group code; when set, one push reaches every member of the group |

3. Confirm the workflow is enabled on the **Actions** page. Use **Run workflow** once to verify it end to end.

> Scheduled runs are disabled by default in a fork. Enable them once on the Actions page.

## Delivery schedule

The workflow is scheduled in UTC. The Beijing-time (UTC+8) equivalents are:

| Edition | Beijing time | cron (UTC) | Look-back window | Covers |
| --- | --- | --- | --- | --- |
| Morning briefing | 09:00 | `0 1 * * *` | 15 hours | Updates published after 18:00 the previous evening |
| Evening briefing | 18:00 | `0 10 * * *` | 9 hours | Updates published after 09:00 the same morning |

The two windows meet end to end, so they cover exactly 24 hours with almost no overlap. GitHub scheduled runs can slip by a few minutes at peak times; that is normal platform behaviour.

## What goes into an issue

- **Sources.** Reads the `top200` bundle from `data/feeds.json` by default; any other OPML bundle (`ai`, `news`, `security`, …) can be selected instead.
- **Filtering.** Keeps only articles inside the time window that carry a real publication date; at most 3 items per feed and 120 items per issue.
- **Deduplication.** Links (ignoring trailing slashes and anchors) and titles are both deduplicated.
- **Ordering.** Articles are grouped by catalog category, newest first inside each section.
- **Bilingual cards.** An English title or summary keeps its original wording and gains a Simplified Chinese translation underneath, so one card reads in both languages. The headline translation carries a fluorescent-green 译 chip and the summary translation sits below a fluorescent-green rule, so both are clearly separated from the original. Chinese articles are never translated into English.
- **Translation.** Translations come from a public translation endpoint. If it fails, times out, or returns the original text unchanged, no translation is appended and the card shows the original only — the push still goes out. Use `--no-translate` to turn the feature off entirely. Whether a string needs translating is decided by the ratio of Chinese to Latin characters, so an English headline that quotes one Chinese product name still gets a translation, while a Chinese sentence quoting an English brand does not. Bare URLs, plain numbers, and version strings are never sent for translation.
- **Failure isolation.** A feed that fails to fetch is skipped without affecting the rest of the issue. When a window has nothing new, nothing is sent, so you are not disturbed; `--allow-empty` changes that.

## Branding and copy

The push title, subtitle, and scope note are defined at the top of `scripts/wechat_digest.py`:

| Element | Copy |
| --- | --- |
| Title | 章鱼 AI · 营销热点智能体 |
| Subtitle | 每天三分钟！去噪音！留干货！ |
| Scope note | 全网兼顾中文和英文内容，覆盖 AI、工程、安全、科技媒体、周刊、科研、新闻、产品、商业、博客、社区、文化、视频和播客。 |
| Signature | 作者：章鱼 ai · 仅供参考，分析研究 |

The title carries no date and no pushplus branding; a page suffix such as `2/3` is appended automatically when an issue runs to several pages. The subtitle is printed in the masthead, and the scope note appears with the intro on the first page only — continuation pages stay lean.

## Visual style

The layout follows the electronic-magazine typography used by 归藏's skills and uses inline styles only, so WeChat webviews render it unchanged:

| Element | Value |
| --- | --- |
| Layout | Portrait single column, 640px maximum width |
| Background | Light grey `#EFEFEA`, cards `#F7F7F4` |
| Body copy | Black `#0B0B0B`, 12px |
| Secondary text | Dark grey `#3A3A38`, monospace, 10px |
| Accent | Fluorescent green `#CCFF00` (masthead, section blocks, source labels, the 译 chip, the translation rule, and the footer bar) |
| Structure | Masthead, hairline section rules, two-digit item numbers, and a continuation hint in the footer |
| Translation | English original in black above, Chinese translation in dark grey below: headlines at 14px with the 译 chip, summaries at 12px behind a fluorescent-green rule |

## The 100,000-character limit and pagination

The pushplus WeChat channel allows 100,000 characters per message for members, and 100 characters for the title. The script packs at a budget of **92,000 characters per page**, leaving room for the masthead, footer, and wrappers, and paginates automatically when an issue exceeds it:

- Page counters read `01 / 03`, and the footer reads either “continued on the next page” or “end of issue”.
- Article numbering continues across pages, and section headers repeat at the top of continuation pages.
- The title gains a `2/3` page suffix; single-page issues get no suffix.
- Pages are sent two seconds apart to stay clear of rate limits.
- Bilingual cards (original plus translation) are longer than single-language cards, so an issue with a lot of English content is split across more pages; the character budget and the limit stay the same.

## Running it locally

```bash
# Render only, no push: writes HTML to build/digest so you can preview it in a browser
python scripts/wechat_digest.py --dry-run --out build/digest --window-hours 48

# Choose the bundle and the edition
python scripts/wechat_digest.py --dry-run --pack ai --edition morning --out build/digest

# Originals only, without the Chinese translations
python scripts/wechat_digest.py --dry-run --no-translate --out build/digest

# Push for real (locally)
PUSHPLUS_TOKEN=your-token python scripts/wechat_digest.py --pack top200
```

Common arguments:

| Argument | Default | Purpose |
| --- | --- | --- |
| `--pack` | `top200` | OPML bundle to read |
| `--edition` | `auto` | `auto` picks morning or afternoon from Beijing time |
| `--window-hours` | `0` | 0 uses the default window for that edition |
| `--max-per-feed` | `3` | Maximum items taken from one feed |
| `--max-items` | `120` | Maximum articles per issue |
| `--translate` | on | Appends a Simplified Chinese translation under English text; `--no-translate` shows originals only |
| `--page-budget` | `92000` | Character budget per page |
| `--topic` | env var | pushplus group code |
| `--channel` | `wechat` | Also supports `mail`, `webhook`, and other pushplus channels |
| `--dry-run` | off | Render without pushing |
| `--allow-empty` | off | Push even when there are no new articles |

## Troubleshooting

**No push arrived?** Open the workflow run log: `items=0` means the window genuinely held no new articles, while `pushplus code=xxx` points at a token or quota problem. The rendered HTML is also uploaded as an artifact and kept for seven days, so you can download and inspect it.

**Want different delivery times?** Change the two cron expressions in the workflow (UTC = Beijing time − 8 hours) and update `window_hours` in `EDITIONS` so the windows still cover 24 hours.

**Want different colours or type?** Edit the palette constants at the top of `scripts/wechat_digest.py`; the tests assert that the main colours are still present.
