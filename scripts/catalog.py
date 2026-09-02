#!/usr/bin/env python3
"""Validate the canonical feed catalog and generate deterministic OPML bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET


PACKS = {
    "top200": ("tidings-top200.opml", "Tidings Curated RSS — Top 200"),
    "all": ("tidings-all.opml", "Tidings Curated RSS — Complete Collection"),
    "blogs": ("tidings-blogs.opml", "Tidings Curated RSS — Chinese Independent Blogs"),
    "communities": ("tidings-communities.opml", "Tidings Curated RSS — Technical Communities"),
    "security": ("tidings-security.opml", "Tidings Curated RSS — Security"),
    "tech-media": ("tidings-tech-media.opml", "Tidings Curated RSS — Technology Media"),
    "weeklies": ("tidings-weeklies.opml", "Tidings Curated RSS — Tech Newsletters & Weeklies"),
    "ai": ("tidings-ai.opml", "Tidings Curated RSS — Artificial Intelligence"),
    "videos": ("tidings-videos.opml", "Tidings Curated RSS — Video Channels"),
    "podcasts": ("tidings-podcasts.opml", "Tidings Curated RSS — Podcasts"),
    "news": ("tidings-news.opml", "Tidings Curated RSS — Fresh News"),
    "research": ("tidings-research.opml", "Tidings Curated RSS — Research & Science"),
    "chinese": ("tidings-chinese.opml", "Tidings Curated RSS — Chinese Sources"),
    "engineering": ("tidings-engineering.opml", "Tidings Curated RSS — Engineering & Technology"),
    "company-tech": ("tidings-company-tech.opml", "Tidings Curated RSS — Company Technology"),
    "wechat": ("tidings-wechat.opml", "Tidings Curated RSS — WeChat Official Accounts"),
}
CATEGORIES = [
    "Artificial Intelligence",
    "Engineering & Technology",
    "Security",
    "Technology Media",
    "Tech Newsletters & Weeklies",
    "Research & Science",
    "News",
    "Product & Design",
    "Business & Startups",
    "Personal Blogs",
    "Communities",
    "Culture & Ideas",
    "Videos",
    "Podcasts",
]
CATEGORY_EMOJI = {
    "Artificial Intelligence": "🤖",
    "Engineering & Technology": "🛠️",
    "Security": "🛡️",
    "Technology Media": "📰",
    "Tech Newsletters & Weeklies": "📮",
    "Research & Science": "🔬",
    "News": "🗞️",
    "Product & Design": "🎨",
    "Business & Startups": "💼",
    "Personal Blogs": "✍️",
    "Communities": "👥",
    "Culture & Ideas": "📚",
    "Videos": "🎬",
    "Podcasts": "🎧",
}
KINDS = {"article", "video", "podcast"}
LANGUAGES = {"en", "zh"}
MAX_ALL_FEEDS = 730
MAX_BLOG_FEEDS = 400
TOP200_FEEDS = 200


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def load_catalog(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_catalog(catalog):
    errors = []
    if catalog.get("version") != 1:
        errors.append("catalog version must be 1")
    feeds = catalog.get("feeds")
    if not isinstance(feeds, list) or not feeds:
        return [*errors, "feeds must be a non-empty list"]
    if len(feeds) > MAX_ALL_FEEDS:
        errors.append(f"complete collection exceeds {MAX_ALL_FEEDS} feeds")
    blog_count = sum("blogs" in feed.get("packs", []) for feed in feeds)
    if blog_count > MAX_BLOG_FEEDS:
        errors.append(f"blog collection exceeds {MAX_BLOG_FEEDS} feeds")
    top_feeds = [feed for feed in feeds if "top200" in feed.get("packs", [])]
    if len(top_feeds) != TOP200_FEEDS:
        errors.append(f"top200 collection must contain exactly {TOP200_FEEDS} feeds")
    missing_top_categories = set(CATEGORIES) - {feed.get("category") for feed in top_feeds}
    if missing_top_categories:
        errors.append(f"top200 collection missing categories {sorted(missing_top_categories)}")
    seen_ids = set()
    seen_urls = set()
    required = {"id", "title", "feed_url", "site_url", "description", "category", "kind", "language", "packs", "sources", "validated_at"}
    for index, feed in enumerate(feeds):
        label = f"feeds[{index}]"
        missing = required - set(feed)
        if missing:
            errors.append(f"{label}: missing {sorted(missing)}")
            continue
        if not feed["title"].strip():
            errors.append(f"{label}: empty title")
        if feed["id"] in seen_ids:
            errors.append(f"{label}: duplicate id {feed['id']}")
        seen_ids.add(feed["id"])
        try:
            parsed = urlsplit(feed["feed_url"])
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError
            normalized = normalize_url(feed["feed_url"])
        except (TypeError, ValueError):
            errors.append(f"{label}: invalid feed_url")
            continue
        if normalized in seen_urls:
            errors.append(f"{label}: duplicate normalized feed_url {normalized}")
        seen_urls.add(normalized)
        if feed["category"] not in CATEGORIES:
            errors.append(f"{label}: unknown category {feed['category']}")
        if feed["kind"] not in KINDS:
            errors.append(f"{label}: unknown kind {feed['kind']}")
        if feed["language"] not in LANGUAGES:
            errors.append(f"{label}: unknown language {feed['language']}")
        unknown_packs = set(feed["packs"]) - set(PACKS)
        if unknown_packs:
            errors.append(f"{label}: unknown packs {sorted(unknown_packs)}")
        if "all" not in feed["packs"]:
            errors.append(f"{label}: missing all pack")
        if "blogs" in feed["packs"] and not (
            feed["kind"] == "article"
            and feed["language"] == "zh"
            and (
                "chinese-independent-blogs" in feed["sources"]
                or "editor-reviewed-chinese-blog" in feed["sources"]
            )
        ):
            errors.append(f"{label}: blogs pack is reserved for vetted Chinese independent blogs")
        if feed["language"] == "zh" and "chinese" not in feed["packs"]:
            errors.append(f"{label}: Chinese feed missing chinese pack")
        if "wechat" in feed["packs"] and not (
            feed["kind"] == "article" and feed["language"] == "zh" and "wechat2rss" in feed["sources"]
        ):
            errors.append(f"{label}: wechat pack is reserved for validated WeChat article feeds")
        if "company-tech" in feed["packs"] and not (feed.get("organization") and feed.get("company_direction")):
            errors.append(f"{label}: company-tech feed missing organization or direction")
        for pack, category in (
            ("communities", "Communities"),
            ("security", "Security"),
            ("tech-media", "Technology Media"),
            ("weeklies", "Tech Newsletters & Weeklies"),
        ):
            if pack in feed["packs"] and feed["category"] != category:
                errors.append(f"{label}: {pack} pack requires {category} category")
    company_keys = [
        (feed.get("organization", "").casefold(), feed.get("company_direction", "").casefold())
        for feed in feeds
        if "company-tech" in feed.get("packs", [])
    ]
    duplicates = [key for key, count in Counter(company_keys).items() if count > 1]
    if duplicates:
        errors.append(f"company-tech contains duplicate organization/direction pairs: {duplicates}")
    return errors


def make_opml(catalog, pack):
    filename, title = PACKS[pack]
    del filename
    root = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(root, "head")
    for tag, value in (
        ("title", title),
        ("dateCreated", catalog["generated_at"]),
        ("dateModified", catalog["generated_at"]),
        ("ownerName", "Tidings RSS Community"),
        ("docs", "http://opml.org/spec2.opml"),
    ):
        ET.SubElement(head, tag).text = value
    body = ET.SubElement(root, "body")
    grouped = defaultdict(list)
    for feed in catalog["feeds"]:
        if pack in feed["packs"]:
            grouped[feed["category"]].append(feed)
    for category in CATEGORIES:
        feeds = grouped.get(category, [])
        if not feeds:
            continue
        label = f"{CATEGORY_EMOJI[category]} {category}"
        group = ET.SubElement(body, "outline", {"text": label, "title": label})
        for feed in sorted(feeds, key=lambda item: item["title"].casefold()):
            attrs = {
                "text": feed["title"],
                "title": feed["title"],
                "type": "rss",
                "xmlUrl": feed["feed_url"],
            }
            if feed["site_url"].startswith(("http://", "https://")):
                attrs["htmlUrl"] = feed["site_url"]
            ET.SubElement(group, "outline", attrs)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def summary_markdown(catalog):
    pack_counts = Counter(pack for feed in catalog["feeds"] for pack in feed["packs"])
    category_counts = Counter(feed["category"] for feed in catalog["feeds"])
    lines = [
        "# Catalog summary",
        "",
        f"Generated and validated on **{catalog['generated_at']}** with **{catalog['validation']['engine']}**.",
        "",
        "## Bundles",
        "",
        "| Bundle | Feeds |",
        "| --- | ---: |",
    ]
    for pack in PACKS:
        lines.append(f"| `{PACKS[pack][0]}` | {pack_counts[pack]} |")
    lines.extend(["", "## Complete collection by category", "", "| Category | Feeds |", "| --- | ---: |"])
    for category in CATEGORIES:
        lines.append(f"| {category} | {category_counts[category]} |")
    lines.extend(
        [
            "",
            "Specialized bundles overlap by design. The complete collection contains every feed only once.",
            "",
        ]
    )
    return "\n".join(lines)


def generate(catalog, root: Path):
    opml_dir = root / "opml"
    report_dir = root / "reports"
    opml_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    for pack, (filename, _) in PACKS.items():
        (opml_dir / filename).write_bytes(make_opml(catalog, pack))
    checksums = []
    for path in sorted(opml_dir.glob("*.opml")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (opml_dir / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    (report_dir / "catalog-summary.md").write_text(summary_markdown(catalog), encoding="utf-8")


def compare_generated(catalog, root: Path):
    with tempfile.TemporaryDirectory() as temp:
        generated = Path(temp)
        generate(catalog, generated)
        mismatches = []
        for path in sorted((generated / "opml").glob("*.opml")):
            actual = root / "opml" / path.name
            if not actual.exists() or actual.read_bytes() != path.read_bytes():
                mismatches.append(str(actual))
        expected_checksums = generated / "opml/SHA256SUMS.txt"
        actual_checksums = root / "opml/SHA256SUMS.txt"
        if not actual_checksums.exists() or actual_checksums.read_bytes() != expected_checksums.read_bytes():
            mismatches.append(str(actual_checksums))
        expected_report = generated / "reports/catalog-summary.md"
        actual_report = root / "reports/catalog-summary.md"
        if not actual_report.exists() or actual_report.read_bytes() != expected_report.read_bytes():
            mismatches.append(str(actual_report))
        return mismatches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--catalog", default="data/feeds.json")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    root = Path(args.root)
    catalog = load_catalog(Path(args.catalog))
    errors = validate_catalog(catalog)
    if errors:
        raise SystemExit("catalog validation failed:\n- " + "\n- ".join(errors))
    if args.command == "generate":
        generate(catalog, root)
        print(f"generated {len(PACKS)} OPML bundles")
        return
    mismatches = compare_generated(catalog, root)
    if mismatches:
        raise SystemExit("generated files are stale:\n- " + "\n- ".join(mismatches))
    print(f"catalog valid: {len(catalog['feeds'])} feeds, {len(PACKS)} OPML bundles")


if __name__ == "__main__":
    main()
