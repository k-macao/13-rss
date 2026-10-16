#!/usr/bin/env python3
"""Fetch and parse every AIHOT demo-source candidate and record one validation round.

The catalog's usual Tidings parser is not available in every authoring environment.
This script keeps the evidence reproducible anyway: it fetches each candidate
endpoint directly, parses the payload as RSS, Atom, or JSON Feed, and records the
HTTP status, the parsed root format, the number of items actually read, the newest
item timestamp as published by the feed, and the channel's own title and language.

    python tools/validate_aihot_sources.py --round 1 \
      --input sources/aihot-curated.json \
      --output reports/aihot-validation-round-1.json
"""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT = 25
USER_AGENT = "TidingsRSSCatalog/1.0 (+https://github.com/fuxiaoai/tidings-rss)"
ACCEPT = (
    "application/rss+xml, application/atom+xml, application/feed+json, "
    "application/json, text/xml, application/xml, */*;q=0.1"
)
DATE_TAGS = {"pubdate", "published", "updated", "date", "dc:date"}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].strip().lower()


def find_text(node, *names):
    for child in node:
        if local_name(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return None


def parse_datetime(value: str):
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_or_none(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def item_date(node):
    for child in node:
        name = local_name(child.tag)
        if name in DATE_TAGS:
            parsed = parse_datetime(child.text or "")
            if parsed:
                return parsed
        if name in {"published", "updated"} and (child.text or "").strip():
            parsed = parse_datetime(child.text)
            if parsed:
                return parsed
    return None


def parse_payload(payload: bytes, content_type: str):
    """Return (format, item_count, channel_title, channel_language, latest_item_at)."""
    stripped = payload.lstrip()
    if "json" in content_type.lower() or stripped.startswith(b"{"):
        data = json.loads(payload.decode("utf-8", "replace"))
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("JSON response has no items array")
        latest = None
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("date_published", "date_modified"):
                parsed = parse_datetime(str(item.get(key) or ""))
                if parsed and (latest is None or parsed > latest):
                    latest = parsed
        title = data.get("title") or ""
        return (
            "json-feed",
            len(items),
            str(title),
            str(data.get("language") or ""),
            iso_or_none(latest),
        )

    root = ET.fromstring(payload)
    root_name = local_name(root.tag)
    if root_name not in {"rss", "feed", "rdf"}:
        raise ValueError(f"unexpected XML root: {root_name}")

    channel = None
    if root_name == "rss":
        channel = root.find("channel")
    elif root_name == "feed":
        channel = root
    if channel is None:
        channel = root

    items = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    latest = None
    for node in items:
        parsed = item_date(node)
        if parsed and (latest is None or parsed > latest):
            latest = parsed

    title = find_text(channel, "title")
    language = find_text(channel, "language") or root.get("{http://www.w3.org/XML/1998/namespace}lang") or ""
    return root_name, len(items), title or "", language, iso_or_none(latest)


def check(candidate) -> dict:
    url = candidate["feed_url"]
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
    started = time.monotonic()
    result = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": False,
        "title": candidate["title"],
        "feed_url": url,
    }
    last_error = ""
    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                request, timeout=TIMEOUT, context=ssl.create_default_context()
            ) as response:
                payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise ValueError("response exceeds 10 MiB")
                format_name, item_count, channel_title, channel_language, latest = parse_payload(
                    payload, response.headers.get("Content-Type", "")
                )
                final_url = response.geturl()
                result.update(
                    {
                        "ok": item_count >= 1,
                        "http_status": getattr(response, "status", None) or response.getcode(),
                        "format": format_name,
                        "items_captured": item_count,
                        "capture_complete": True,
                        "channel_title": channel_title,
                        "channel_language": channel_language,
                        "latest_item_at": latest,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                    }
                )
                if final_url and final_url != url:
                    result["resolved_url"] = final_url
                if item_count < 1:
                    last_error = "feed has no items"
                return result
        except Exception as error:  # noqa: BLE001 - report remote failures uniformly
            last_error = f"{type(error).__name__}: {error}"
            if attempt == 0:
                time.sleep(2)
    result.update({"error": last_error, "duration_ms": round((time.monotonic() - started) * 1000)})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="sources/aihot-curated.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--round", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=6)
    args = parser.parse_args()

    curated = json.loads(Path(args.input).read_text(encoding="utf-8"))
    candidates = curated["candidates"]
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(check, candidates))

    payload = {
        "review": curated["review"],
        "round": args.round,
        "criterion": (
            "Direct HTTPS fetch returning a parseable RSS, Atom, or JSON Feed payload with at "
            "least one item; the newest item timestamp is recorded as published by the feed."
        ),
        "engine": "HTTP fetch + RSS/Atom/JSON Feed parse",
        "checked_on": curated["checked_on"],
        "timezone_note": curated["timezone_note"],
        "candidate_count": len(candidates),
        "passed": sum(1 for item in results if item["ok"]),
        "results": results,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"AIHOT source validation round {args.round}: {payload['passed']}/{len(results)} passed")
    for item in results:
        if not item["ok"]:
            print(f"  failed: {item['title']} — {item.get('error', 'no items')}")


if __name__ == "__main__":
    main()
