# Changelog

## Unreleased

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
