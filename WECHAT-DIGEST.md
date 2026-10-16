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
   | `DEEPSEEK_API_KEY` | No | DeepSeek key; on its own it configures the `llm` provider — endpoint and model included |
   | `TRANSLATE_LLM_API_KEY` | No | Key for any other OpenAI-compatible chat endpoint |
   | `DEEPL_API_KEY` | No | DeepL key, free (`…:fx`) or pro |
   | `MYMEMORY_EMAIL` | No | E-mail address that raises MyMemory's anonymous quota |

   `TRANSLATE_LLM_PROVIDER`, `TRANSLATE_LLM_BASE_URL`, `TRANSLATE_LLM_MODEL`, and `TRANSLATE_PROVIDERS` are repository *variables* (**Settings → Secrets and variables → Actions → Variables**), not secrets. See [Translation providers](#translation-providers) and [Configuring the llm provider](#configuring-the-llm-provider).

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
- **Translation.** Translations come from a chain of providers, not one endpoint: the configured keyed providers first, then every keyless endpoint. If a provider fails, times out, or returns the original text unchanged, the next one is asked, and when none answers the card shows the original only — the push still goes out. Use `--no-translate` to turn the feature off entirely. Whether a string needs translating is decided by the ratio of Chinese to Latin characters, so an English headline that quotes one Chinese product name still gets a translation, while a Chinese sentence quoting an English brand does not. Bare URLs, plain numbers, and version strings are never sent for translation.
- **Translation reporting.** The run states which provider answered: a `translation: translated=17/24 provider=edge retired=google` line in the log, a notice (and a warning when endpoints were retired or nothing answered) on the workflow run page, `translation-report.md` and `translation-report.json` next to the rendered pages, and the same table on the run's step summary.
- **Failure isolation.** A feed that fails to fetch is skipped without affecting the rest of the issue. When a window has nothing new, nothing is sent, so you are not disturbed; `--allow-empty` changes that.

## Translation providers

English titles and summaries are translated by walking a chain and stopping at the first endpoint that answers. Keyed providers come first because they do not depend on a public endpoint's goodwill towards GitHub's runner addresses — which is where the old single-endpoint build lost every translation.

| Provider | Key | Endpoint | Notes |
| --- | --- | --- | --- |
| `deepl` | `DEEPL_API_KEY` | `api-free.deepl.com` (keys ending in `:fx`) or `api.deepl.com` | Highest-quality machine translation; the free tier covers 500,000 characters a month |
| `llm` | one provider key, or `TRANSLATE_LLM_API_KEY` | a preset endpoint, or `TRANSLATE_LLM_BASE_URL` + `TRANSLATE_LLM_MODEL` | Any OpenAI-compatible chat endpoint: DeepSeek, OpenAI, Qwen/DashScope, Moonshot, Zhipu, OpenRouter, …; context-aware, and the one worth setting up first |
| `google` | — | `translate.googleapis.com` | The public gtx endpoint used before; rate-limits datacenter addresses |
| `google-chrome` | — | `clients5.google.com` | Google's dictionary client, a separate service with its own quota |
| `edge` | — | `edge.microsoft.com/translate/translatetext` | Microsoft Edge's keyless translator, the successor of the removed `translate/auth` + `api-edge` flow |
| `mymemory` | `MYMEMORY_EMAIL` (optional) | `api.mymemory.translated.net` | Free translation memory with a small daily quota |

How the chain behaves in a run:

- A provider that fails twice in a row is **retired** for the rest of the run, so a blocked endpoint costs a couple of timeouts instead of one per article.
- The provider that answered is tried **first** from then on; the others are skipped while it keeps working, and one is picked up again if it starts failing.
- Providers are asked in the order above. Set `TRANSLATE_PROVIDERS` (repository variable) or `--translators` to choose and order them, for example `llm,google,edge`.
- A malformed answer is not retried, only a timeout, a rate limit, or a connection error is.
- With no key set the digest still translates through the keyless endpoints; a key simply makes the outcome dependable.

### Configuring the llm provider

The `llm` provider is one OpenAI-compatible chat endpoint, so a provider key alone is enough to switch it on — the preset supplies the endpoint and a current model ID. Set one secret and nothing else:

| Preset | Secret | Endpoint | Model |
| --- | --- | --- | --- |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | `deepseek-flash` |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `dashscope` | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `moonshot` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `zhipu` | `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |

```text
# DeepSeek, exactly as it ships: one secret in the repository, nothing else
DEEPSEEK_API_KEY = sk-…

# The same key under the generic name still finds the DeepSeek preset,
# because the endpoint gives it away
TRANSLATE_LLM_API_KEY = sk-…
TRANSLATE_LLM_BASE_URL = https://api.deepseek.com/v1
```

About the DeepSeek preset, since its API changes more often than the others:

- The preset asks for **non-thinking mode** (`"thinking": {"type": "disabled"}`). DeepSeek thinks by default, and a translation needs no chain of thought; thinking mode also ignores `temperature` and bills the reasoning tokens.
- The model ID is `deepseek-flash` (DeepSeek-V4.1-Flash). The older `deepseek-chat` and `deepseek-reasoner` were **retired on 2026-07-24** — if one is still configured, the run says so in a warning annotation instead of just showing a failed endpoint.
- A base URL is accepted the way it is usually pasted: `https://api.deepseek.com`, `https://api.deepseek.com/v1/`, and a full `…/v1/chat/completions` all land on the same endpoint, and a bare DeepSeek or OpenAI host gains the `/v1` root.
- Naming the provider explicitly (`TRANSLATE_LLM_PROVIDER=deepseek`) selects the preset together with `TRANSLATE_LLM_API_KEY`. The name is checked: a typo fails the run instead of silently dropping the provider.
- `TRANSLATE_LLM_BASE_URL` and `TRANSLATE_LLM_MODEL` always win over the preset, so a gateway in front of DeepSeek is configured the same way as any other endpoint.

Nothing here is required: an unknown host is used exactly as typed, and a key that belongs to another provider simply fails like any other dead endpoint — the notes below say which endpoint and model the run actually used.

### Checking which endpoints answer

`Translation probe` ([`.github/workflows/translate-probe.yml`](.github/workflows/translate-probe.yml)) calls every provider directly, bypassing the chain, and reports the raw answer or the raw error per provider. Run it from **Actions → Translation probe → Run workflow**; it takes about a minute, prints the table below to the run page, uploads `translation-probe` as an artifact, and can post the same table as a comment on an issue or pull request (the `comment_on` input). It also runs on every pull request that touches the translation chain and comments the table on that pull request, because whether a key works from a runner is the one thing the offline tests cannot answer.

The report ends with a **Configuration** section naming the variable each key came from, the endpoint it points at, and the model that will answer — the key itself is never printed. When a provider is missing from the table, that section is usually the reason.

```bash
# The same probe locally; the report lands in build/probe/
python scripts/wechat_digest.py --probe-translators --out build/probe

# Probe one provider only
python scripts/wechat_digest.py --probe-translators --translators edge --timeout 10
```

| Provider | Kind | Source | Status | Seconds | Output |
| --- | --- | --- | --- | ---: | --- |
| `google` | keyless | OpenAI ships a smaller model… | error | 0.31 | HTTPError: 429 Too Many Requests |
| `edge` | keyless | OpenAI ships a smaller model… | ok | 0.42 | OpenAI 推出可在设备端运行的更小模型 |

## Branding and copy

The push title, subtitle, and scope note are defined at the top of `scripts/wechat_digest.py`:

| Element | Copy |
| --- | --- |
| Title | 章鱼 AI · 营销热点助手 |
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

# Ask every translation provider which ones answer, then exit
python scripts/wechat_digest.py --probe-translators --out build/probe

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
| `--translators` | env var | Provider order, e.g. `llm,google,edge`; empty uses keyed providers first, then every keyless one |
| `--probe-translators` | off | Call every provider directly, report which endpoints answer, and exit |
| `--page-budget` | `92000` | Character budget per page |
| `--topic` | env var | pushplus group code |
| `--channel` | `wechat` | Also supports `mail`, `webhook`, and other pushplus channels |
| `--dry-run` | off | Render without pushing |
| `--allow-empty` | off | Push even when there are no new articles |

## Troubleshooting

**No push arrived?** Open the workflow run log: `items=0` means the window genuinely held no new articles, while `pushplus code=xxx` points at a token or quota problem. The rendered HTML is also uploaded as an artifact and kept for seven days, so you can download and inspect it.

**English cards arrived without their Chinese lines?** The push is designed to go out anyway, so the run page says what happened: look for the `translation: …` notice and the warnings naming the endpoints that were retired or the configuration that is wrong, or open `translation-report.md` in the uploaded artifact — its Configuration section names the key variable, endpoint, and model the run used. Then run the `Translation probe` workflow, or open the comment it posts on a pull request that touches the translation chain: it shows each endpoint's raw answer from a GitHub runner in one minute. If every keyless endpoint refuses the runner (Google answers `429` most often), add `DEEPSEEK_API_KEY` or `DEEPL_API_KEY`; a key is not required for the digest to run, but it is what makes the translations dependable.

**DeepSeek returns “model not exist”?** The account is fine — the model ID is not. `deepseek-chat` and `deepseek-reasoner` were retired on 2026-07-24; use `deepseek-flash` (or `deepseek-v4-pro`) in `TRANSLATE_LLM_MODEL`, or delete that variable and let the preset pick. The run shows the same advice as a warning annotation.

**Want different delivery times?** Change the two cron expressions in the workflow (UTC = Beijing time − 8 hours) and update `window_hours` in `EDITIONS` so the windows still cover 24 hours.

**Want different colours or type?** Edit the palette constants at the top of `scripts/wechat_digest.py`; the tests assert that the main colours are still present.
