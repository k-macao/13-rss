#!/usr/bin/env python3
"""Merge repeatedly checked Chinese blogs with the strongest current catalog feeds."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.catalog import CATEGORIES, normalize_url


MAX_ALL = 730
MAX_BLOGS = 400
PACK_PRIORITY = {"news": 35, "research": 35, "ai": 30, "security": 28, "tech-media": 26, "weeklies": 25, "communities": 24, "videos": 24, "podcasts": 24, "company-tech": 20, "wechat": 18, "engineering": 14}


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def site_key(value):
    host = (urlsplit(value or "").hostname or "").lower().removeprefix("www.")
    return host


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def blog_category(topics):
    text = " ".join(topics)
    if re.search(r"编程|技术|开发|架构|算法|前端|后端|运维|开源|Linux|iOS|Android|AI|人工智能", text, re.I):
        return "Engineering & Technology"
    if re.search(r"产品|设计|独立开发", text):
        return "Product & Design"
    if re.search(r"创业|商业|投资|财经|金融", text):
        return "Business & Startups"
    if re.search(r"文学|历史|哲学|读书|写作|艺术|文化", text):
        return "Culture & Ideas"
    return "Personal Blogs"


def current_score(feed, results, now):
    passed = [item for item in results if item and item.get("ok")]
    if len(passed) < len(results):
        return None
    dates = [parse_date(item.get("latest_item_at")) for item in passed]
    dates = [item for item in dates if item]
    latest = max(dates) if dates else parse_date(feed.get("latest_item_at"))
    age = max(0, (now - latest).days) if latest else 9999
    freshness = 20 if age <= 30 else 15 if age <= 90 else 10 if age <= 180 else 5 if age <= 365 else 1 if age <= 730 else -10
    priority = sum(PACK_PRIORITY.get(pack, 0) for pack in feed["packs"])
    return priority + freshness + (3 if feed["feed_url"].startswith("https://") else 0), latest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--catalog-validation", action="append", required=True)
    parser.add_argument("--blogs", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    old = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
    validations = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.catalog_validation]
    maps = [{normalize_url(item["feed_url"]): item for item in payload["results"]} for payload in validations]
    blog_payload = json.loads(Path(args.blogs).read_text(encoding="utf-8"))
    blogs = blog_payload["candidates"][:MAX_BLOGS]
    now = datetime.fromisoformat(args.date).replace(tzinfo=timezone.utc)

    selected = []
    used_urls = set()
    used_sites = set()
    for blog in blogs:
        normalized = normalize_url(blog["feed_url"])
        site = blog.get("site_url") or blog.get("site_url_hint", "")
        if not site.startswith(("http://", "https://")) or site_key(site) in {"127.0.0.1", "localhost"}:
            site = blog.get("site_url_hint", "")
        site = site if site.startswith(("http://", "https://")) else blog["feed_url"]
        current_site_key = site_key(site)
        if normalized in used_urls or (current_site_key and current_site_key in used_sites):
            continue
        topics = blog.get("topics", [])
        category = blog_category(topics)
        packs = {"all", "blogs", "chinese"}
        if category == "Engineering & Technology":
            packs.add("engineering")
        if re.search(r"AI|人工智能|机器学习|大模型", " ".join(topics), re.I):
            packs.add("ai")
        if topics:
            description = "主要写" + "、".join(topics[:4]) + "。"
        else:
            description = f"持续更新的中文独立博客，主分类为 {category}。"
        selected.append(
            {
                "id": hashlib.sha256(normalized.encode()).hexdigest()[:12],
                "title": clean_text(blog.get("title") or blog.get("title_hint") or blog["feed_url"]),
                "feed_url": blog["feed_url"],
                "site_url": site,
                "description": description,
                "category": category,
                "kind": "article",
                "language": "zh",
                "packs": sorted(packs),
                "sources": ["chinese-independent-blogs"],
                "validated_at": args.date,
                "latest_item_at": blog.get("latest_item_at"),
            }
        )
        used_urls.add(normalized)
        if site_key(site):
            used_sites.add(site_key(site))

    ranked = []
    for feed in old["feeds"]:
        if not set(feed["packs"]) & set(PACK_PRIORITY):
            continue
        normalized = normalize_url(feed["feed_url"])
        if normalized in used_urls or (site_key(feed.get("site_url")) and site_key(feed.get("site_url")) in used_sites):
            continue
        scored = current_score(feed, [items.get(normalized) for items in maps], now)
        if not scored:
            continue
        score, latest = scored
        cleaned = dict(feed)
        cleaned["packs"] = sorted(set(feed["packs"]) - {"blogs"})
        cleaned["validated_at"] = args.date
        if latest:
            cleaned["latest_item_at"] = latest.isoformat().replace("+00:00", "Z")
        cleaned["description"] = {
            "video": "视频频道",
            "podcast": "播客节目",
        }.get(cleaned["kind"], f"{cleaned['category']} 订阅源")
        ranked.append((score, cleaned))

    ranked.sort(key=lambda item: (-item[0], item[1]["title"].casefold()))
    for _score, feed in ranked:
        if len(selected) >= MAX_ALL:
            break
        normalized = normalize_url(feed["feed_url"])
        if normalized in used_urls:
            continue
        selected.append(feed)
        used_urls.add(normalized)

    category_order = {name: index for index, name in enumerate(CATEGORIES)}
    selected.sort(key=lambda item: (category_order[item["category"]], item["title"].casefold()))
    catalog = {
        "version": 1,
        "generated_at": args.date,
        "validation": {
            "engine": "Tidings parseFeedUrl",
            "criteria": "Chinese independent blogs passed three parser rounds, had at least two dated posts and updated within 180 days; retained catalog feeds passed two current parser rounds.",
            "candidate_count": old["validation"]["candidate_count"] + blog_payload["source_candidate_count"],
            "parser_passed": len(selected),
            "retained_count": len(selected),
        },
        "feeds": selected,
    }
    Path(args.output).write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"built {len(selected)} feeds: {sum('blogs' in item['packs'] for item in selected)} Chinese blogs")


if __name__ == "__main__":
    main()
