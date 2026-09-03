#!/usr/bin/env python3
"""Merge the verified hot-list, aggregator, and market-data round into the catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from bisect import bisect_right
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.catalog import MAX_ALL_FEEDS, PACKS, normalize_url


ENGINE_NOTE = (
    "Tidings parseFeedUrl; the 2026-09-03 hot-list and market-data additions were verified by HTTP fetch plus feed parse"
)
CRITERIA_SUFFIX = (
    " The 2026-09-03 hot-list and market-data round was verified by direct HTTP fetch plus RSS/Atom parsing"
    " rather than Tidings; every published addition returned a parseable feed, and the SoPilot channel was"
    " valid but empty at check time because its board had no qualifying posts."
)


def feed_id(feed_url: str) -> str:
    return hashlib.sha256(normalize_url(feed_url).encode()).hexdigest()[:12]


def latest_check(candidate):
    """Return the most recent successful check, or None."""
    passed = [check for check in candidate.get("checks", []) if check.get("ok")]
    return passed[-1] if passed else None


def insert(feed, feeds):
    """Keep the existing category grouping and title order of the catalog intact."""
    category = feed["category"]
    indexes = [index for index, item in enumerate(feeds) if item["category"] == category]
    if not indexes:
        feeds.append(feed)
        return
    start, stop = indexes[0], indexes[-1] + 1
    block = feeds[start:stop]
    titles = [item["title"].casefold() for item in block]
    if titles == sorted(titles):
        offset = bisect_right(titles, feed["title"].casefold())
        feeds.insert(start + offset, feed)
    else:
        feeds.insert(stop, feed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/feeds.json")
    parser.add_argument("--curated", default="sources/hotlist-curated.json")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--summary", default="")
    args = parser.parse_args()

    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    curated = json.loads(Path(args.curated).read_text(encoding="utf-8"))
    review = curated["review"]
    existing = {normalize_url(feed["feed_url"]) for feed in catalog["feeds"]}
    decisions = []
    added = 0

    for candidate in curated["candidates"]:
        selected = bool(candidate.get("selected"))
        check = latest_check(candidate)
        if selected and (not candidate.get("feed_url") or check is None):
            raise SystemExit(f"selected candidate without a verified feed: {candidate.get('title')}")
        decision = {
            "requested_source": candidate.get("requested_source", ""),
            "title": candidate["title"],
            "feed_url": candidate.get("feed_url", ""),
            "site_url": candidate.get("site_url", ""),
            "selected": selected,
            "reason": candidate.get("reason", ""),
            "checks": candidate.get("checks", []),
        }
        if candidate.get("caveat"):
            decision["caveat"] = candidate["caveat"]
        decisions.append(decision)
        if not selected:
            continue

        key = normalize_url(candidate["feed_url"])
        if key in existing:
            decision["reason"] = "already published in the catalog"
            decision["selected"] = False
            continue
        existing.add(key)
        feed = {
            "id": feed_id(candidate["feed_url"]),
            "title": candidate["title"],
            "feed_url": candidate["feed_url"],
            "site_url": candidate["site_url"],
            "category": candidate["category"],
            "kind": candidate.get("kind", "article"),
            "language": candidate["language"],
            "packs": sorted(set(candidate["packs"]) | {"all"}),
            "sources": [review],
            "validated_at": args.date,
            "latest_item_at": check.get("latest_item_at"),
            "description": candidate["description"],
            "description_en": candidate["description_en"],
        }
        unknown = set(feed["packs"]) - set(PACKS)
        if unknown:
            raise SystemExit(f"{feed['title']}: unknown packs {sorted(unknown)}")
        insert(feed, catalog["feeds"])
        added += 1

    if len(catalog["feeds"]) > MAX_ALL_FEEDS:
        raise SystemExit(f"catalog would exceed the {MAX_ALL_FEEDS} feed limit")

    catalog["generated_at"] = args.date
    catalog["validation"]["engine"] = ENGINE_NOTE
    if CRITERIA_SUFFIX.strip() not in catalog["validation"]["criteria"]:
        catalog["validation"]["criteria"] += CRITERIA_SUFFIX
    catalog["validation"]["candidate_count"] += len(curated["candidates"])
    catalog["validation"]["parser_passed"] = len(catalog["feeds"])
    catalog["validation"]["retained_count"] = len(catalog["feeds"])

    report = {
        "review": review,
        "validated_at": args.date,
        "engine": "HTTP fetch + RSS/Atom/JSON Feed parse",
        "engine_note": curated["method"],
        "requested_sources": curated["requested_sources"],
        "candidate_count": len(curated["candidates"]),
        "selected_count": added,
        "rejected_count": len(curated["candidates"]) - added,
        "published_total": len(catalog["feeds"]),
        "decisions": decisions,
    }

    Path(args.output).write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.summary:
        summary_path = Path(args.summary)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["published"] = len(catalog["feeds"])
        summary.setdefault("limits", {})["complete_collection_maximum"] = MAX_ALL_FEEDS
        summary["by_pack"] = {
            pack: sum(pack in feed["packs"] for feed in catalog["feeds"]) for pack in sorted(PACKS)
        }
        summary["hotlist_aggregator_review"] = {
            "validated_at": args.date,
            "engine": report["engine"],
            "requested_sources": len(curated["requested_sources"]),
            "candidates": len(curated["candidates"]),
            "published_additions": added,
            "rejected_without_public_feed": sum(
                1
                for decision in decisions
                if not decision["selected"] and not decision.get("feed_url")
            ),
            "rejected_after_failed_endpoints": sum(
                1
                for decision in decisions
                if not decision["selected"] and decision.get("feed_url")
            ),
            "published_with_empty_channel": sum(
                1
                for decision in decisions
                if decision["selected"] and (decision["checks"][-1].get("items_captured") or 0) == 0
            ),
            "tidings_parser_rounds": 0,
            "selection_evidence": str(args.report),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = Counter(feed["category"] for feed in catalog["feeds"])
    print(f"added {added} feeds; catalog has {len(catalog['feeds'])} feeds")
    for decision in decisions:
        if not decision["selected"]:
            print(f"  rejected: {decision['title']}")
    print(f"  new category totals: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
