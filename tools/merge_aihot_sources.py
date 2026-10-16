#!/usr/bin/env python3
"""Merge the verified AIHOT demo-source round into the catalog.

The candidate list comes from `sources/aihot-curated.json`; the fetch-and-parse
evidence comes from one or more round files produced by
`tools/validate_aihot_sources.py`. A candidate is published only when every round
returned a parseable feed with at least one item.

    python tools/merge_aihot_sources.py --date 2026-10-17 \
      --curated sources/aihot-curated.json \
      --output data/feeds.json \
      --report reports/aihot-curation.json \
      --summary reports/validation-summary.json \
      --validation reports/aihot-validation-round-1.json \
      --validation reports/aihot-validation-round-2.json \
      --validation reports/aihot-validation-round-3.json
"""

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
    "Tidings parseFeedUrl; the 2026-10-17 AIHOT demo-source round was verified by HTTP fetch plus feed parse"
)
CRITERIA_SUFFIX = (
    " The 2026-10-17 AIHOT demo-source round was verified by direct HTTP fetch plus RSS/Atom/JSON Feed parsing"
    " rather than Tidings; every published addition returned a parseable feed with at least one item in every"
    " round, and the endpoints already published in the catalog were re-checked in the same rounds."
)


def feed_id(feed_url: str) -> str:
    return hashlib.sha256(normalize_url(feed_url).encode()).hexdigest()[:12]


def load_rounds(paths):
    rounds = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rounds.append(
            {
                "round": payload["round"],
                "by_url": {item["feed_url"]: item for item in payload["results"]},
            }
        )
    rounds.sort(key=lambda item: item["round"])
    return rounds


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
    parser.add_argument("--curated", default="sources/aihot-curated.json")
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--validation", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0, help="publish at most this many additions")
    args = parser.parse_args()

    if not args.validation:
        raise SystemExit("at least one --validation round file is required")

    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    curated = json.loads(Path(args.curated).read_text(encoding="utf-8"))
    rounds = load_rounds(args.validation)
    review = curated["review"]
    existing = {normalize_url(feed["feed_url"]) for feed in catalog["feeds"]}
    company_keys = {
        (feed.get("organization", "").casefold(), feed.get("company_direction", "").casefold())
        for feed in catalog["feeds"]
        if "company-tech" in feed.get("packs", [])
    }
    decisions = []
    added = 0

    for candidate in curated["candidates"]:
        checks = [round["by_url"].get(candidate["feed_url"]) for round in rounds]
        checks = [check for check in checks if check]
        reason = candidate.get("reason", "")
        selected = bool(candidate.get("selected")) and not candidate.get("existing_entry_check")
        passed = bool(checks) and all(check.get("ok") for check in checks)

        if selected and not passed:
            selected = False
            reason = "rejected: did not return a parseable feed with items in every validation round"

        if selected:
            key = normalize_url(candidate["feed_url"])
            if key in existing:
                selected = False
                reason = "already published in the catalog"
            pair = (
                candidate.get("organization", "").casefold(),
                candidate.get("company_direction", "").casefold(),
            )
            if "company-tech" in candidate.get("packs", []):
                if not all(pair):
                    selected = False
                    reason = "rejected: company-technology feed without an organization and direction"
                elif pair in company_keys:
                    selected = False
                    reason = "rejected: duplicate organization and direction in the company-technology bundle"
            if selected and args.limit and added >= args.limit:
                selected = False
                reason = "rejected: complete-collection cap reached for this round"

        decision = {
            "requested_source": candidate.get("requested_source", ""),
            "aihot_id": candidate.get("aihot_id", ""),
            "aihot_tier": candidate.get("aihot_tier", ""),
            "title": candidate["title"],
            "feed_url": candidate["feed_url"],
            "site_url": candidate.get("site_url", ""),
            "selected": selected,
            "reason": reason,
            "checks": checks,
        }
        if candidate.get("duplicate_of"):
            decision["duplicate_of"] = candidate["duplicate_of"]
        if candidate.get("existing_entry_check"):
            decision["existing_entry_check"] = True
        decisions.append(decision)
        if not selected:
            continue

        latest = checks[-1]
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
            "latest_item_at": latest.get("latest_item_at"),
            "description": candidate["description"],
            "description_en": candidate["description_en"],
        }
        if candidate.get("organization"):
            feed["organization"] = candidate["organization"]
        if candidate.get("company_direction"):
            feed["company_direction"] = candidate["company_direction"]
        unknown = set(feed["packs"]) - set(PACKS)
        if unknown:
            raise SystemExit(f"{feed['title']}: unknown packs {sorted(unknown)}")

        key = normalize_url(candidate["feed_url"])
        existing.add(key)
        if "company-tech" in feed["packs"]:
            company_keys.add(
                (feed.get("organization", "").casefold(), feed.get("company_direction", "").casefold())
            )
        insert(feed, catalog["feeds"])
        added += 1

    if len(catalog["feeds"]) > MAX_ALL_FEEDS:
        raise SystemExit(
            f"catalog would exceed the {MAX_ALL_FEEDS} feed limit "
            f"({len(catalog['feeds'])} feeds); raise the cap or publish fewer candidates"
        )

    catalog["generated_at"] = args.date
    catalog["validation"]["engine"] = ENGINE_NOTE
    if CRITERIA_SUFFIX.strip() not in catalog["validation"]["criteria"]:
        catalog["validation"]["criteria"] += CRITERIA_SUFFIX
    catalog["validation"]["candidate_count"] += len(curated["candidates"])
    catalog["validation"]["parser_passed"] = len(catalog["feeds"])
    catalog["validation"]["retained_count"] = len(catalog["feeds"])

    report = {
        "review": review,
        "origin": curated["origin"],
        "validated_at": args.date,
        "engine": "HTTP fetch + RSS/Atom/JSON Feed parse",
        "engine_note": curated["method"],
        "requested_sources": curated["requested_sources"],
        "validation_rounds": [round["round"] for round in rounds],
        "criterion": "A candidate is published only when every round returned a parseable feed with at least one item.",
        "candidate_count": len(curated["candidates"]),
        "selected_count": added,
        "rejected_count": len(curated["candidates"]) - added,
        "published_total": len(catalog["feeds"]),
        "decisions": decisions,
    }

    Path(args.output).write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # `published_total` is the catalog size after a round was merged, so every earlier
    # report has to follow the catalog forward instead of freezing a stale number.
    report_path = Path(args.report).resolve()
    for path in sorted(Path("reports").glob("*.json")):
        if path.resolve() == report_path:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("published_total") != len(catalog["feeds"]):
            data["published_total"] = len(catalog["feeds"])
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.summary:
        summary_path = Path(args.summary)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["published"] = len(catalog["feeds"])
        summary.setdefault("limits", {})["complete_collection_maximum"] = MAX_ALL_FEEDS
        summary["by_pack"] = {
            pack: sum(pack in feed["packs"] for feed in catalog["feeds"]) for pack in sorted(PACKS)
        }
        published = [item for item in decisions if item["selected"]]
        summary["aihot_demo_sources_review"] = {
            "validated_at": args.date,
            "engine": report["engine"],
            "source_project": curated["origin"]["project"],
            "source_repository": curated["origin"]["repository"],
            "candidates": len(curated["candidates"]),
            "validation_rounds": len(rounds),
            "published_additions": added,
            "rejected_already_published": sum(
                1 for item in decisions if not item["selected"] and item["reason"] == "already published in the catalog"
            ),
            "rejected_as_duplicate_publisher": sum(1 for item in decisions if item.get("duplicate_of")),
            "rejected_after_failed_checks": sum(
                1 for item in decisions if not item["selected"] and item["reason"].startswith("rejected:")
            ),
            "existing_entry_checks": sum(1 for item in decisions if item.get("existing_entry_check")),
            "tidings_parser_rounds": 0,
            "selection_evidence": str(args.report),
            "published_titles": [item["title"] for item in published],
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = Counter(feed["category"] for feed in catalog["feeds"])
    print(f"added {added} feeds; catalog has {len(catalog['feeds'])} feeds")
    for decision in decisions:
        if not decision["selected"]:
            print(f"  not published: {decision['title']} — {decision['reason']}")
    print(f"  new category totals: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
