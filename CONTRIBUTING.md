# Contributing

[简体中文](CONTRIBUTING.zh-CN.md)

Thank you for helping people discover feeds worth following. A useful contribution is small, verifiable, and respectful of publishers.

## Suggest a feed

Open a **Feed suggestion** issue or edit `data/feeds.json` in a pull request. Include:

- a public RSS, Atom, or JSON Feed URL;
- the publisher's website;
- the most appropriate category and bundle;
- a short, concrete reason the source is useful;
- evidence that the endpoint currently parses and contains at least one item.

We favor original reporting, first-party research, practitioner writing, official project blogs, and channels with a clear editorial identity. Scraped mirrors, credentialed feeds, spam, SEO farms, copied content, and sources that primarily promote affiliate links are not accepted.

Chinese independent blogs must have published within the last 180 days, return at least two reliably dated articles, and pass repeated Tidings parser checks. `tidings-blogs.opml` is capped at 400 feeds and the complete collection at 730; once a cap is reached, a new source must displace a weaker one.

Community feeds must use an official endpoint or a publicly documented fallback, contain recent discussions, and pass three current Tidings parser rounds. A parseable forum feed does not imply that Tidings can fetch its full reply thread; describe that capability separately when proposing a source.

Security, technology-media, and technical-newsletter feeds must also pass three current Tidings parser rounds and return recently dated items. Near-duplicate sections are not collected unless each feed serves a clear, distinct reading use case.

Trending boards, aggregator pages, and market-data sources need a real feed endpoint. A first-party RSS feed wins; otherwise use a publicly documented bridge route such as RSSHub and state which board the feed carries. Aggregators that only render HTML — NewsNow, REBANG, and login-walled research platforms such as DataYes Robo — are recorded as rejected candidates in that round's report instead of being published as feeds. When a bridge stamps every item with the snapshot build time instead of a publication date, say so in the report.

WeChat feeds must respond quickly, expose recent articles, and parse through Tidings. For company technology feeds, include the organization and technical direction. Only one feed is kept for each organization/direction pair, and an official website RSS feed takes priority over a matching WeChat bridge.

The Top 200 is a strict subset of the complete catalog. Selection is reproducible through `tools/select_top200.py`: all primary categories must remain represented, publishers are deduplicated by default, and every selected feed must pass three current Tidings parser rounds with articles and real publication dates.

Reproduce the current selection:

```bash
python tools/select_top200.py --date 2026-08-13 \
  --candidate-snapshot reports/top200-candidates.json \
  --output reports/top200-curation.json \
  --validation reports/top200-validation-round-1.json \
  --validation reports/top200-validation-round-2.json \
  --validation reports/top200-validation-round-3.json \
  --video-validation reports/top200-video-validation-round-1.json \
  --video-validation reports/top200-video-validation-round-2.json \
  --video-validation reports/top200-video-validation-round-3.json \
  --apply
```

Reproduce the 2026-09-03 hot-list and market-data round:

```bash
python tools/merge_hotlist_sources.py --date 2026-09-03 \
  --curated sources/hotlist-curated.json \
  --output data/feeds.json \
  --report reports/hotlist-curation.json \
  --summary reports/validation-summary.json
python scripts/catalog.py generate
python tools/generate_source_appendix.py
```

## Update the generated files

Python 3.10 or newer is sufficient; the project has no runtime dependencies.

```bash
python scripts/catalog.py generate
python scripts/catalog.py check
python -m unittest discover -s tests -v
```

Never edit files under `opml/` or `reports/catalog-summary.md` by hand. They are deterministic outputs of `data/feeds.json`.

## Removing or correcting a feed

Removal PRs are welcome when a feed is permanently unavailable, hijacked, paywalled at the feed endpoint, empty, or no longer represents the listed publisher. A temporary timeout alone is not enough; include repeatable evidence and the date checked.

## Rights and privacy

This repository catalogs public endpoints. It does not republish article bodies. By contributing original catalog metadata, you agree to release that contribution under CC0-1.0. Feed content and publisher names remain the property of their respective owners.
