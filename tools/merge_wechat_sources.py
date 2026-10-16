#!/usr/bin/env python3
"""Merge verified WeChat sources and user-facing company-tech bundle metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.catalog import normalize_url


AI_RE = re.compile(r"AI|人工智能|机器学习|计算机视觉|自然语言|论文", re.I)
DESIGN_RE = re.compile(r"设计|用户体验")
CULTURE_RE = re.compile(r"历史|社会|文化|人物")


def category(topic: str) -> str:
    if AI_RE.search(topic):
        return "Artificial Intelligence"
    if DESIGN_RE.search(topic):
        return "Product & Design"
    if CULTURE_RE.search(topic):
        return "Culture & Ideas"
    return "Engineering & Technology"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default="data/feeds.json")
    parser.add_argument("--wechat", default="sources/wechat-curated.json")
    parser.add_argument("--company", default="sources/company-tech.json")
    parser.add_argument("--probe", action="append", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    curated = json.loads(Path(args.wechat).read_text(encoding="utf-8"))
    company = json.loads(Path(args.company).read_text(encoding="utf-8"))
    probes = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.probe]
    validation = json.loads(Path(args.validation).read_text(encoding="utf-8"))
    probe_maps = [{item["feed_url"]: item for item in report["results"]} for report in probes]
    parsed = {item["feed_url"]: item for item in validation["results"]}
    now = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)
    existing = {normalize_url(item["feed_url"]): item for item in catalog["feeds"]}
    decisions = []

    for item in curated:
        rounds = [mapping.get(item["feed_url"], {"ok": False}) for mapping in probe_maps]
        result = parsed.get(item["feed_url"], {"ok": False, "error": "not validated after probe failure"})
        latest = result.get("latest_item_at")
        age = (now - datetime.fromisoformat(latest.replace("Z", "+00:00"))).days if latest else None
        accepted = all(check["ok"] for check in rounds) and result.get("ok") and age is not None and age <= 180
        reason = "selected" if accepted else "failed two-second probe" if not all(check["ok"] for check in rounds) else "Tidings parse failed" if not result.get("ok") else "no recent dated article"
        decisions.append({
            "title": item["title"], "feed_url": item["feed_url"], "selected": accepted, "reason": reason,
            "probe_rounds": rounds, "tidings_ok": bool(result.get("ok")), "latest_item_at": latest,
        })
        if not accepted:
            continue
        key = normalize_url(item["feed_url"])
        feed = existing.get(key)
        topic = item["topic"]
        current_category = category(topic)
        if feed is None:
            feed = {
                "id": hashlib.sha256(key.encode()).hexdigest()[:12],
                "title": item["title"],
                "feed_url": item["feed_url"],
                "site_url": result.get("site_url") or item["feed_url"],
                "description": f"公众号，主要关注{topic}。",
                "category": current_category,
                "kind": "article",
                "language": "zh",
                "packs": ["all", "chinese", "wechat"],
                "sources": ["wechat2rss"],
                "validated_at": args.date,
                "latest_item_at": latest,
            }
            if current_category in {"Artificial Intelligence", "Engineering & Technology"}:
                feed["packs"].append("engineering")
            if current_category == "Artificial Intelligence":
                feed["packs"].append("ai")
            catalog["feeds"].append(feed)
            existing[key] = feed
        else:
            feed["language"] = "zh"
            feed["packs"] = sorted(set(feed["packs"]) | {"wechat", "chinese"})
            feed["sources"] = sorted(set(feed["sources"]) | {"wechat2rss"})
            if item.get("topic_en") and not feed.get("description_en"):
                feed["description_en"] = f"WeChat article feed covering {item['topic_en']}."
        if item["organization"]:
            feed["packs"] = sorted(set(feed["packs"]) | {"company-tech"})
            feed["organization"] = item["organization"]
            feed["company_direction"] = item["company_direction"]

    by_title = {feed["title"]: feed for feed in catalog["feeds"]}
    seen_company_directions = set()
    for item in company:
        feed = by_title.get(item["title"])
        if not feed:
            continue
        if "wechat2rss" in feed["feed_url"] and "wechat" not in feed["packs"]:
            continue
        key = (item["organization"].casefold(), item["direction"].casefold())
        if key in seen_company_directions:
            continue
        seen_company_directions.add(key)
        feed["packs"] = sorted(set(feed["packs"]) | {"company-tech"})
        feed["organization"] = item["organization"]
        feed["company_direction"] = item["direction"]

    company_groups = {}
    for feed in catalog["feeds"]:
        if "company-tech" not in feed["packs"]:
            continue
        key = (feed["organization"].casefold(), feed["company_direction"].casefold())
        company_groups.setdefault(key, []).append(feed)
    for feeds in company_groups.values():
        if len(feeds) < 2:
            continue
        preferred = min(feeds, key=lambda feed: ("wechat2rss" in feed["feed_url"], feed["title"].casefold()))
        for feed in feeds:
            if feed is preferred:
                continue
            feed["packs"].remove("company-tech")
            feed.pop("organization", None)
            feed.pop("company_direction", None)

    catalog["feeds"].sort(key=lambda item: (item["category"], item["title"].casefold()))
    catalog["validation"]["criteria"] += " WeChat additions passed two two-second time-to-first-byte probes, Tidings parsing, and a 180-day freshness check."
    catalog["validation"]["candidate_count"] += len(curated)
    catalog["validation"]["parser_passed"] = len(catalog["feeds"])
    catalog["validation"]["retained_count"] = len(catalog["feeds"])
    report = {
        "validated_at": args.date,
        "two_second_definition": "HTTP 200 XML response began within two seconds; full download and article parsing used the Tidings parser separately.",
        "candidate_count": len(curated),
        "selected_count": sum(item["selected"] for item in decisions),
        "company_tech_count": sum("company-tech" in feed["packs"] for feed in catalog["feeds"]),
        "wechat_count": sum("wechat" in feed["packs"] for feed in catalog["feeds"]),
        "decisions": decisions,
    }
    Path(args.output).write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"merged {report['selected_count']} WeChat feeds; catalog has {len(catalog['feeds'])} feeds")


if __name__ == "__main__":
    main()
