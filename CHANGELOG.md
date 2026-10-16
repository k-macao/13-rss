# Changelog

## Unreleased

- Made the `llm` translation provider configurable with a single provider key. A preset table maps `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, `MOONSHOT_API_KEY`, `ZHIPUAI_API_KEY`, and `OPENROUTER_API_KEY` to their documented endpoint and a current model ID, so `DEEPSEEK_API_KEY` alone — the key the guide asks for — switches the provider on instead of failing with OpenAI's defaults. `TRANSLATE_LLM_PROVIDER` names a preset explicitly, `TRANSLATE_LLM_BASE_URL`/`TRANSLATE_LLM_MODEL` still override it, and an unknown provider name fails the run instead of silently dropping the provider.
- Corrected the DeepSeek defaults against the current API: the preset calls `deepseek-flash` (DeepSeek-V4.1-Flash) and asks for non-thinking mode with `"thinking": {"type": "disabled"}`, because DeepSeek thinks by default and thinking mode ignores `temperature` and bills the reasoning tokens. `deepseek-chat` and `deepseek-reasoner`, which the docs and tests had used as examples, were retired on 2026-07-24; a run with one of them still configured now emits `TRANSLATE_LLM_MODEL=deepseek-chat was retired on 2026-07-24; use deepseek-flash` as a warning annotation, so the failure names its own cause.
- Accepted the base URL the way people paste it: a trailing slash, a full `…/chat/completions` path, and a bare `api.deepseek.com` or `api.openai.com` host all normalise to the same endpoint, and an endpoint the presets do not know is used exactly as typed. A key pointed at DeepSeek without naming the preset still gets the preset's model and request fields, because the host identifies it.
- Told the reports which configuration the run used. `translation-report.md` and the probe report end with a **Configuration** section naming the variable each key came from, the endpoint, and the model — never the key itself — and the probe exists precisely because a provider that is missing from its table is usually a secret that is missing too. A key with no endpoint set now says that OpenAI's default is being assumed; a preset named without its key variable warns instead of failing the job.
- Ran the translation probe automatically on pull requests that touch the translation chain, posting its table as a comment on that pull request, and put the key-free configuration line (`config: llm=deepseek-flash @ api.deepseek.com via DEEPSEEK_API_KEY`) into the probe's annotation so the run page answers which endpoint was asked. Whether a key works from a GitHub runner is the one question the offline tests cannot answer; forks get a read-only token, so only branches in this repository comment, and a token that may not comment degrades to a warning annotation instead of failing the probe.
- Passed the DeepSeek key through both workflows as `DEEPSEEK_API_KEY` (falling back to `DEEPSEEK_KEY`) alongside `TRANSLATE_LLM_PROVIDER`, so the key works whether it was stored under the generic name or its own.
- Documented the presets, the DeepSeek setup, the non-thinking mode, the retired model IDs, and the base-URL forms in both WeChat digest guides, so one secret is enough to follow; added 17 tests for preset resolution, base-URL normalisation, notes, warnings, the probe report, and the two workflows.

- Replaced the digest's single translation endpoint with a provider chain. The public Google gtx endpoint that answered during development rate-limits and often refuses datacenter addresses such as GitHub-hosted runners, so `translate_to_chinese` failed on every string and the whole issue silently lost its appended translations while the push went out as before.
- Added four translation providers behind one interface: `deepl` (`DEEPL_API_KEY`, free `:fx` keys use the api-free host), `llm` (any OpenAI-compatible chat endpoint via `TRANSLATE_LLM_API_KEY`, `TRANSLATE_LLM_BASE_URL`, `TRANSLATE_LLM_MODEL`), and, keyless, `google-chrome` (`clients5.google.com`, a separate service and quota from gtx) and `edge` (Microsoft Edge's keyless `translatetext`, the successor of the removed `translate/auth` + `api-edge` flow) alongside `mymemory` behind the existing gtx provider.
- Made the chain cheap under failure: a provider that fails twice in a row is retired for the rest of the run, the provider that answered is tried first from then on, a malformed answer is not retried while a timeout or rate limit is, and `TRANSLATE_PROVIDERS` or `--translators` selects and orders the chain. An unknown provider name fails loudly instead of looking like a dead endpoint.
- Made a silent translation failure visible: every run prints `translation: translated=17/24 provider=edge retired=google`, writes `translation-report.md` and `translation-report.json` beside the rendered pages, appends the table to the Actions step summary, and emits a notice plus a warning (when endpoints were retired or nothing answered) as a workflow annotation.
- Added the `Translation probe` workflow and `--probe-translators`, which call every provider directly with two fixed English strings and report each endpoint's raw answer or raw error, so one manual run answers which endpoints a GitHub runner can reach. The probe can post its table as a comment on an issue or pull request.
- Passed the optional translation keys through the digest workflow as `DEEPL_API_KEY`, `TRANSLATE_LLM_API_KEY`, `MYMEMORY_EMAIL` secrets and `TRANSLATE_LLM_BASE_URL`, `TRANSLATE_LLM_MODEL`, `TRANSLATE_PROVIDERS` variables, and added a step that shows the translation report next to the rendered pages.
- Documented the chain, its provider table, the probe, the report, and the new parameters in both WeChat digest guides, and noted the chain in both READMEs' digest sections.
- Added 34 tests covering each provider's wire format, chain fall-through, stickiness, retirement, config resolution, the probe report and annotations, and the two workflows.

- Published the WeChat digest guide in English as `WECHAT-DIGEST.md`, pointed the English README's digest link at it, and cross-linked the two guides from each other; a repository test now asserts both READMEs link to the guide in their own language.
- Added `topic_en` to all 41 entries of `sources/wechat-curated.json` and taught `tools/merge_wechat_sources.py` to write an English `description_en`, then backfilled the 18 published WeChat feeds whose English appendix descriptions still embedded a Chinese topic. Both README appendices now read in a single language each, and regeneration stays reproducible.
- Made the WeChat digest bilingual instead of translation-only: an English title or summary now keeps the original text and gains its Simplified Chinese translation underneath, so a card reads as original first, translation second. `translate_items` writes `title_zh` and `summary_zh` instead of overwriting `title` and `summary`, and appends nothing when the endpoint is unreachable or echoes the original back, so a card never prints the same sentence twice.
- Rendered the appended translation in the existing editorial palette: the headline translation sits at 14px in dark grey behind a fluorescent-green 译 chip, and the summary translation at 12px on a thin neon rule, both clamped to the card's own title and summary widths. Chinese copy is still never translated, and `--no-translate` now shows originals only.
- Documented the bilingual card in the digest guide's content rules, visual-style, pagination, and parameter tables, and in both READMEs; added eleven tests covering appending, untranslated Chinese items, unreachable and echo-back translations, escaping, clamping, and pagination of a fully bilingual issue.
- Fixed English detection so a single Chinese character no longer stops an English headline from being translated: `is_english` now compares the share of CJK characters against Latin letters, counting CJK punctuation, kana, and full-width forms as Chinese, so mixed titles follow the script that dominates them while Chinese sentences quoting English brands stay untranslated.
- Added twelve feeds from the [AIHOT](https://github.com/k-macao/AIHOT) demo source bundle: Microsoft Research, NVIDIA Blog, AWS Machine Learning Blog, GitHub Blog · AI & ML, Mistral AI, Berkeley AI Research, The Verge · AI, TechCrunch · AI, Ars Technica · AI, MIT Technology Review · AI, The Decoder, and Latent Space. Every addition returned a parseable feed with dated recent items in all three rounds.
- Checked all 18 AIHOT demo URLs plus the already-published Google DeepMind endpoint and recorded the seven rejections instead of publishing them: four were already in the catalog, one duplicated a publisher the catalog already represents, and Substack answered HTTP 403 for Import AI in all three rounds.
- Published `sources/aihot-curated.json`, `tools/validate_aihot_sources.py`, and `tools/merge_aihot_sources.py` so the round is reproducible; the round files live in `reports/aihot-validation-round-{1,2,3}.json` and the decisions in `reports/aihot-curation.json`.
- Raised the complete-collection limit from 730 to 740 feeds so the twelve verified additions do not displace existing curated sources, updated both contributing guides, and refreshed the `published_total` field of earlier curation reports so every evidence file tracks the current catalog size.
- Recorded that this round was verified by direct HTTP fetch plus RSS/Atom/JSON Feed parsing on a GitHub-hosted runner, because the authoring environment has no outbound access to feed hosts.
- Renamed the digest to “章鱼 AI · 营销热点助手”, replaced the subtitle with “每天三分钟！去噪音！留干货！”, and added the explicit scope note “全网兼顾中文和英文内容，覆盖 AI、工程、安全、科技媒体、周刊、科研、新闻、产品、商业、博客、社区、文化、视频和播客。” under the first-page intro.
- Kept the scope note on the first page only, alongside the intro, so continuation pages stay lean; extended the branding tests to assert all fourteen topics and both languages appear.
- Added a 品牌与文案 section to the WeChat digest guide documenting the title, subtitle, scope note, and signature, and shifted the later section numbers.
- Added a twice-daily WeChat digest workflow that pushes new catalog articles through pushplus at 09:00 and 18:00 Beijing time.
- Rendered the digest in an international-magazine style: portrait single column, light grey paper, black small body copy, dark grey metadata, and a fluorescent green accent, using inline styles only so WeChat webviews render it unchanged.
- Paginated long issues under the 100,000-character pushplus limit, with continuous article numbering, repeated section headers, page counters, and numbered titles.
- Added dependency-free RSS, Atom, and JSON Feed parsing with time-window filtering, per-feed caps, link and title deduplication, and per-feed failure isolation.
- Added 24 unit tests covering parsing, deduplication, edition windows, pagination, escaping, palette, and the workflow schedule.
- Rebranded the digest as “章鱼 AI 全景分析”: the push title no longer carries the date or pushplus branding, the masthead and subtitle describe the cross-border, multi-model research scope, an intro paragraph runs on the first page, and a “作者：章鱼 ai · 仅供参考，分析研究” signature closes each issue.
- Added seven hot-list, aggregator, and market-data feeds from six reader-suggested sources: 英为财情 (Investing.com's Chinese all-news wire), SoPilot's X hot-post board, 今日热榜's GitHub Trending and Product Hunt boards, the Zhihu hot list, Weibo trending searches, and Hupu's daily threads.
- Checked NewsNow, REBANG, and 萝卜投研 and recorded them as rejected candidates instead of publishing unsubscribable pages: none exposes a feed, 萝卜投研 also requires a login, and the submitted `luobo.cn` is the 保卫萝卜 game site rather than robo.datayes.com.
- Published fifteen-candidate evidence in `reports/hotlist-curation.json`, including the direct Weibo and Bilibili hot-search RSSHub routes that failed twice on the shared instance, TopHub's snapshot-time item stamps, and SoPilot's valid but empty channel.
- Raised the complete-collection limit from 720 to 730 feeds so verified additions do not displace existing curated sources, and documented the hot-list rule in both contributing guides.
- Added `sources/hotlist-curated.json` and `tools/merge_hotlist_sources.py` so the round is reproducible; regenerated all 16 OPML bundles, the catalog summary, both README appendices, and the validation summary for the 725-feed catalog.
- Recorded that this round was verified by direct HTTP fetch plus RSS/Atom parsing rather than the Tidings parser, because Tidings was unavailable in the authoring environment.

## v1.4.0 — 2026-08-13

- Added `tidings-top200.opml` as the recommended first import, with all 14 primary categories represented.
- Selected established publishers, long-running independent writers, first-party sources, and authors with strong community recognition while limiting duplicate publishers, generated searches, release logs, and platform-heavy categories.
- Committed the reproducible 307-feed candidate snapshot; every published Top 200 feed passed three Tidings production-parser rounds with articles and real publication dates.
- Added canonical publisher deduplication and explicit parent/child exclusions so cross-domain sections and derivative digests cannot occupy duplicate slots.
- Rechecked video feeds separately at single concurrency to avoid confusing platform throttling with feed health; only two video channels passed all three rounds on the current network.
- Updated both READMEs and RSS guides to recommend the Top 200 before the complete collection, with explicit network-scope limits.

## v1.3.0 — 2026-08-12

- Added focused OPML bundles for technical communities, security, technology media, and technical newsletters.
- Added V2EX technology and creative feeds, the Hacker News main feed, Show HN, Ask HN, CISA, FreeBuf, MIT Technology Review, JavaScript Weekly, This Week in Rust, and Ruan Yifeng's blog after three Tidings parser rounds.
- Published the candidate-level audit showing why Naixi, NodeSeek, and the V2EX hot feed were not included.
- Added English and Chinese RSS usage guides, category icons in OPML folders, and updated download tables for all 15 bundles.
- Expanded the complete collection to 718 feeds while keeping the Chinese independent blog bundle at 349 selected sources.

## v1.2.0 — 2026-08-12

- Added eight recently active community feeds: LINUX DO documentation, a combined Reddit technology feed, Lobsters, Python Core Development, Rust Internals, NixOS Development, the OpenAI Developer Community, and Kubernetes discussions. Every addition passed three Tidings production-parser rounds.
- Added separately downloadable WeChat and company-technology bundles, with two-second response probes, current-article checks, and organization/direction deduplication that prefers first-party website feeds.
- Rewrote both homepages around choosing, downloading, and reading the collections; moved discovery projects into a short reference section and removed internal pipeline narration from the reader path.
- Rebuilt the catalog as 707 checked feeds, including 348 Chinese independent blogs selected from 1,331 candidates.
- Required three successful Tidings parser rounds and recent, reliably dated publishing activity for the Chinese blog bundle; removed duplicate sites and promotional or SEO-oriented sources.
- Rechecked the existing catalog twice and removed repeatedly failing feeds before merging.
- Added hard limits of 400 blogs and 720 complete-collection feeds.
- Rewrote both project homepages and added a generated, tested appendix listing every source, description, Feed URL, primary category, and bundle membership.
- Published the candidate-level Chinese blog curation evidence and added a reproducible collection, scoring, and catalog-build pipeline.

## v1.1.0 — 2026-07-28

- Rebuilt the English and Chinese project story around author curation, category-by-category source highlights, and reader value.
- Moved the Tidings recommendation to the final chapter and expanded it with real import, AI Radar, AI summary, article Q&A, bilingual, video, and forum screenshots.
- Replaced the previous loading-state preview with a gated, reproducible capture of a fully fetched MIT Technology Review article with a loaded lead image and no visible fetch errors.
- Replaced the persistently slow BAIR endpoint with the current official Amazon Science feed after validating it through the Tidings production parser.
- Routed the real-import preview through GitHub Camo via a version-pinned CDN URL to avoid intermittent Raw-domain blank images while keeping the original file in the repository.

## v1.0.0 — 2026-07-28

Initial public release of Tidings RSS.

- Published 627 live, deduplicated feeds across nine OPML bundles.
- Added focused AI (74), News (44), Research & Science (28), Video (93), and Podcast (86) downloads.
- Added broader Blogs (374), Chinese (239), Engineering (186), and Complete (627) collections.
- Validated 884 normalized candidates with the Tidings production parser; excluded 196 parser failures, 32 stale feeds, four persistent in-app import failures, and 25 canonical duplicates.
- Upgraded 67 working HTTP endpoints to independently verified HTTPS equivalents.
- Added deterministic OPML generation, SHA-256 checksums, unit tests, pull-request validation, and a weekly live health workflow.
- Added English and Simplified Chinese documentation, source/license boundaries, contribution templates, official Tidings product imagery, and a reproducible real-import screenshot.
