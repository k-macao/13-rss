#!/usr/bin/env python3
"""Build an editorial-style RSS digest and push it to WeChat through pushplus.

Dependency-free: standard library only, matching the rest of this repository.

Usage examples
--------------
    python scripts/wechat_digest.py --dry-run --out build/digest
    PUSHPLUS_TOKEN=xxx python scripts/wechat_digest.py --pack top200

Design notes
------------
The rendered HTML follows an international-magazine (归藏 skills) look: a
portrait, single-column canvas on light grey paper, black body copy at a small
size, dark grey metadata, and a fluorescent green accent used sparingly for
kickers, indices and rules. All styling is inline so pushplus/WeChat webviews
render it without a stylesheet.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import ssl
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = "TidingsRSSDigest/1.0 (+https://github.com/fuxiaoai/tidings-rss)"
HOST_LIMITS = defaultdict(lambda: threading.BoundedSemaphore(2))

PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
# pushplus WeChat channel: 100,000 characters per message for member accounts.
PUSHPLUS_CONTENT_LIMIT = 100_000
PUSHPLUS_TITLE_LIMIT = 100
# Leave room for the page wrapper that is added after content is packed.
DEFAULT_PAGE_BUDGET = 92_000

CHINA = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# Palette — international magazine, portrait, fluorescent green on light grey
# ---------------------------------------------------------------------------
PAPER = "#EFEFEA"       # 浅灰色地
CARD = "#F7F7F4"
INK = "#0B0B0B"         # 黑色正文
GRAPHITE = "#3A3A38"    # 深灰字
MUTE = "#7C7C76"
NEON = "#CCFF00"        # 萤光绿
NEON_DEEP = "#A8D400"
HAIRLINE = "#D6D6CE"

SANS = (
    "'HelveticaNeue-CondensedBold','Helvetica Neue',Helvetica,"
    "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei',sans-serif"
)
MONO = "'SF Mono',SFMono-Regular,Menlo,Consolas,'Courier New',monospace"

CATEGORY_LABELS = {
    "Artificial Intelligence": "人工智能",
    "Engineering & Technology": "工程与技术",
    "Security": "安全",
    "Technology Media": "科技媒体",
    "Tech Newsletters & Weeklies": "周刊与通讯",
    "Research & Science": "研究与科学",
    "News": "新闻",
    "Product & Design": "产品与设计",
    "Business & Startups": "商业与创业",
    "Personal Blogs": "个人博客",
    "Communities": "社区",
    "Culture & Ideas": "文化与观点",
    "Videos": "视频",
    "Podcasts": "播客",
}
CATEGORY_ORDER = list(CATEGORY_LABELS)

EDITIONS = {
    "morning": {"title": "晨间简报", "kicker": "MORNING EDITION", "window_hours": 15},
    "evening": {"title": "晚间简报", "kicker": "EVENING EDITION", "window_hours": 9},
}


# ---------------------------------------------------------------------------
# Feed fetching and parsing
# ---------------------------------------------------------------------------
def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", value or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def clamp(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    total = 0
    out = []
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if total + step > width - 1:
            break
        out.append(char)
        total += step
    return "".join(out).rstrip() + "…"


def parse_datetime(value: str):
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    candidate = value.replace("Z", "+00:00")
    candidate = re.sub(r"(\.\d{3})\d+", r"\1", candidate)
    for pattern in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(candidate, pattern)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _xml_text(node) -> str:
    return "".join(node.itertext()) if node is not None else ""


def parse_xml_items(payload: bytes):
    root = ET.fromstring(payload)
    items = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        summary = ""
        published = ""
        alternate = ""
        for child in node:
            name = local_name(child.tag)
            text = (child.text or "").strip()
            if name == "title" and not title:
                title = strip_html(_xml_text(child))
            elif name == "link":
                href = child.get("href") or text
                rel = (child.get("rel") or "alternate").lower()
                if href and rel == "alternate" and not link:
                    link = href.strip()
                elif href and not alternate:
                    alternate = href.strip()
            elif name in {"pubdate", "published", "updated", "date", "issued"} and not published:
                published = text
            elif name in {"description", "summary", "content", "encoded", "subtitle"} and not summary:
                summary = strip_html(_xml_text(child))
            elif name == "guid" and not alternate and text.startswith("http"):
                alternate = text
        link = link or alternate
        if not (title or link):
            continue
        items.append(
            {
                "title": title or link,
                "link": link,
                "summary": summary,
                "published_at": parse_datetime(published),
            }
        )
    return items


def parse_json_items(payload: bytes):
    data = json.loads(payload)
    items = []
    for entry in data.get("items") or []:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") or entry.get("content_text") or entry.get("content_html") or ""
        items.append(
            {
                "title": strip_html(entry.get("title") or "") or entry.get("url", ""),
                "link": entry.get("url") or entry.get("external_url") or "",
                "summary": strip_html(summary),
                "published_at": parse_datetime(entry.get("date_published") or entry.get("date_modified") or ""),
            }
        )
    return items


def parse_feed_payload(payload: bytes, content_type: str = ""):
    stripped = payload.lstrip()
    if "json" in (content_type or "").lower() or stripped.startswith(b"{"):
        return parse_json_items(payload)
    return parse_xml_items(payload)


def fetch_feed(feed, timeout, window_start, max_per_feed):
    request = Request(
        feed["feed_url"],
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, application/atom+xml, application/feed+json, "
                "application/json, text/xml, application/xml, */*;q=0.1"
            ),
        },
    )
    host = (urlsplit(feed["feed_url"]).hostname or "").lower()
    try:
        with HOST_LIMITS[host]:
            with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                payload = response.read(MAX_BYTES + 1)
                if len(payload) > MAX_BYTES:
                    raise ValueError("response exceeds 5 MiB")
                parsed = parse_feed_payload(payload, response.headers.get("Content-Type", ""))
    except (HTTPError, URLError, ValueError, ET.ParseError, TimeoutError, OSError, json.JSONDecodeError) as error:
        return {"feed": feed, "ok": False, "error": str(error)[:200], "items": []}

    selected = []
    for item in parsed:
        published = item["published_at"]
        if published is None or published < window_start:
            continue
        selected.append(
            {
                "title": item["title"],
                "link": item["link"],
                "summary": item["summary"],
                "published_at": published,
                "source": feed["title"],
                "site_url": feed.get("site_url", ""),
                "category": feed.get("category", "News"),
                "language": feed.get("language", "en"),
            }
        )
    selected.sort(key=lambda entry: entry["published_at"], reverse=True)
    return {"feed": feed, "ok": True, "error": "", "items": selected[:max_per_feed]}


def collect_items(feeds, *, window_start, timeout, concurrency, max_per_feed):
    items = []
    stats = {"feeds": len(feeds), "ok": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(fetch_feed, feed, timeout, window_start, max_per_feed) for feed in feeds]
        for future in as_completed(futures):
            result = future.result()
            if result["ok"]:
                stats["ok"] += 1
                items.extend(result["items"])
            else:
                stats["failed"] += 1
    return dedupe(items), stats


def dedupe(items):
    seen_links = set()
    seen_titles = set()
    unique = []
    for item in sorted(items, key=lambda entry: entry["published_at"], reverse=True):
        link_key = (item["link"] or "").split("#", 1)[0].rstrip("/").casefold()
        title_key = re.sub(r"\W+", "", item["title"]).casefold()[:80]
        if link_key and link_key in seen_links:
            continue
        if title_key and title_key in seen_titles:
            continue
        if link_key:
            seen_links.add(link_key)
        if title_key:
            seen_titles.add(title_key)
        unique.append(item)
    return unique


# ---------------------------------------------------------------------------
# Rendering — 归藏 editorial style, portrait
# ---------------------------------------------------------------------------
def esc(value: str) -> str:
    return html.escape(value or "", quote=True)


def rule(color=HAIRLINE, height="1px", top="14px", bottom="14px"):
    return (
        f'<div style="height:{height};line-height:{height};font-size:0;'
        f'background:{color};margin:{top} 0 {bottom};"></div>'
    )


def render_header(*, title, kicker, stamp, page_index, page_count, item_count, source_count):
    pager = f"{page_index:02d} / {page_count:02d}"
    return f"""
<div style="padding:26px 0 0;">
  <div style="font-family:{MONO};font-size:10px;letter-spacing:.28em;color:{GRAPHITE};text-transform:uppercase;">
    TIDINGS&nbsp;RSS&nbsp;&nbsp;·&nbsp;&nbsp;{esc(kicker)}
  </div>
  <div style="margin:10px 0 0;">
    <span style="display:inline-block;background:{NEON};color:{INK};font-family:{SANS};font-size:26px;
      font-weight:800;letter-spacing:.04em;line-height:1.15;padding:2px 10px 4px;">{esc(title)}</span>
  </div>
  <div style="margin:12px 0 0;font-family:{SANS};font-size:11px;letter-spacing:.16em;color:{GRAPHITE};">
    每日精选 · CURATED FEED DIGEST
  </div>
  {rule(INK, "2px", "16px", "10px")}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
    <tr>
      <td style="font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.12em;">{esc(stamp)}</td>
      <td align="right" style="font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.12em;">
        {item_count} ARTICLES · {source_count} SOURCES · {pager}
      </td>
    </tr>
  </table>
  {rule(HAIRLINE, "1px", "10px", "22px")}
</div>
""".strip()


def render_section_head(category, count):
    label_zh = CATEGORY_LABELS.get(category, category)
    return f"""
<div style="margin:26px 0 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
    <tr>
      <td style="vertical-align:bottom;">
        <span style="display:inline-block;width:26px;height:8px;background:{NEON};vertical-align:middle;"></span>
        <span style="font-family:{SANS};font-size:15px;font-weight:800;color:{INK};letter-spacing:.06em;
          padding-left:8px;">{esc(label_zh)}</span>
        <span style="font-family:{MONO};font-size:9px;color:{MUTE};letter-spacing:.2em;padding-left:8px;
          text-transform:uppercase;">{esc(category)}</span>
      </td>
      <td align="right" style="font-family:{MONO};font-size:10px;color:{GRAPHITE};">{count:02d}</td>
    </tr>
  </table>
  {rule(INK, "1px", "8px", "0")}
</div>
""".strip()


def render_item(index, item):
    published = item["published_at"].astimezone(CHINA)
    title = clamp(item["title"], 96)
    summary = clamp(item["summary"], 190) if item["summary"] else ""
    link = item["link"] or item.get("site_url") or ""
    heading = (
        f'<a href="{esc(link)}" style="color:{INK};text-decoration:none;">{esc(title)}</a>'
        if link
        else esc(title)
    )
    summary_block = (
        f'<div style="margin:7px 0 0;font-family:{SANS};font-size:12px;line-height:1.75;color:{INK};">'
        f"{esc(summary)}</div>"
        if summary
        else ""
    )
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="width:100%;border-collapse:collapse;background:{CARD};margin:0 0 10px;">
  <tr>
    <td width="34" valign="top" style="width:34px;padding:14px 0 14px 12px;">
      <span style="font-family:{MONO};font-size:11px;font-weight:700;color:{NEON_DEEP};letter-spacing:.06em;">
        {index:02d}</span>
    </td>
    <td valign="top" style="padding:14px 14px 14px 4px;border-left:1px solid {HAIRLINE};">
      <div style="font-family:{SANS};font-size:15px;font-weight:700;line-height:1.5;color:{INK};
        letter-spacing:.01em;">{heading}</div>
      {summary_block}
      <div style="margin:9px 0 0;font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.08em;">
        <span style="background:{NEON};color:{INK};padding:1px 5px;">{esc(clamp(item['source'], 30))}</span>
        <span style="padding-left:8px;">{published.strftime('%m-%d %H:%M')}</span>
      </div>
    </td>
  </tr>
</table>
""".strip()


def render_footer(*, page_index, page_count, generated_at, window_hours):
    tail = (
        "本期结束 · END OF ISSUE"
        if page_index == page_count
        else f"续下页 · CONTINUED IN {page_index + 1:02d}/{page_count:02d}"
    )
    return f"""
<div style="margin:28px 0 0;">
  {rule(INK, "2px", "0", "10px")}
  <div style="font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.14em;line-height:1.9;">
    {esc(tail)}<br>
    WINDOW {window_hours}H · GENERATED {esc(generated_at)} CST<br>
    SOURCE github.com/fuxiaoai/tidings-rss
  </div>
  <div style="margin:12px 0 0;height:6px;background:{NEON};"></div>
</div>
""".strip()


def page_wrapper(body: str) -> str:
    return (
        f'<div style="background:{PAPER};margin:0;padding:0;">'
        f'<div style="max-width:640px;margin:0 auto;background:{PAPER};padding:0 18px 30px;'
        f'font-family:{SANS};color:{INK};-webkit-text-size-adjust:100%;">'
        f"{body}</div></div>"
    )


def group_by_category(items):
    grouped = defaultdict(list)
    for item in items:
        grouped[item["category"]].append(item)
    ordered = [(category, grouped[category]) for category in CATEGORY_ORDER if grouped.get(category)]
    ordered += [(category, entries) for category, entries in sorted(grouped.items()) if category not in CATEGORY_LABELS]
    return ordered


def render_pages(items, *, title, kicker, generated_at, window_hours, page_budget=DEFAULT_PAGE_BUDGET):
    """Render the digest into one or more pushplus-sized HTML pages."""
    source_count = len({item["source"] for item in items})
    stamp = generated_at
    blocks = []  # (kind, html) - "section" blocks repeat on continuation pages
    counter = 0
    for category, entries in group_by_category(items):
        blocks.append(("section", render_section_head(category, len(entries))))
        for entry in entries:
            counter += 1
            blocks.append(("item", render_item(counter, entry)))

    # Pack blocks into pages, keeping a section header with at least one item.
    chrome = len(
        render_header(
            title=title,
            kicker=kicker,
            stamp=stamp,
            page_index=99,
            page_count=99,
            item_count=len(items),
            source_count=source_count,
        )
    ) + len(render_footer(page_index=99, page_count=99, generated_at=stamp, window_hours=window_hours)) + 400
    budget = max(4_000, page_budget - chrome)

    pages: list[list[str]] = []
    current: list[str] = []
    size = 0
    current_section = None   # section header already printed on this page
    pending_section = None   # section header waiting for its first item
    for kind, block in blocks:
        if kind == "section":
            pending_section = block
            continue
        chunk = (pending_section or "") + block
        if current and size + len(chunk) > budget:
            pages.append(current)
            current = []
            size = 0
            # Repeat the section header at the top of the continuation page.
            chunk = (pending_section or current_section or "") + block
        if pending_section is not None:
            current_section = pending_section
            pending_section = None
        current.append(chunk)
        size += len(chunk)
    if current:
        pages.append(current)
    if not pages:
        pages = [[render_empty_state()]]

    page_count = len(pages)
    rendered = []
    for index, body_blocks in enumerate(pages, start=1):
        body = render_header(
            title=title,
            kicker=kicker,
            stamp=stamp,
            page_index=index,
            page_count=page_count,
            item_count=len(items),
            source_count=source_count,
        )
        body += "".join(body_blocks)
        body += render_footer(
            page_index=index,
            page_count=page_count,
            generated_at=stamp,
            window_hours=window_hours,
        )
        rendered.append(page_wrapper(body))
    return rendered


def render_empty_state():
    return (
        f'<div style="background:{CARD};padding:22px 16px;font-family:{SANS};font-size:13px;color:{INK};'
        f'line-height:1.8;">本时段没有新的文章。<span style="background:{NEON};padding:1px 5px;">'
        f"NO NEW ARTICLES</span></div>"
    )


def page_title(base, index, count):
    label = base if count == 1 else f"{base} {index}/{count}"
    return label[:PUSHPLUS_TITLE_LIMIT]


# ---------------------------------------------------------------------------
# pushplus delivery
# ---------------------------------------------------------------------------
def push_page(*, token, title, content, topic="", channel="wechat", timeout=30, retries=3):
    if len(content) > PUSHPLUS_CONTENT_LIMIT:
        raise ValueError(f"page exceeds pushplus limit: {len(content)} > {PUSHPLUS_CONTENT_LIMIT}")
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
        "channel": channel,
    }
    if topic:
        payload["topic"] = topic
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_error = ""
    for attempt in range(1, retries + 1):
        request = Request(
            PUSHPLUS_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
                raw = response.read().decode("utf-8", "replace")
            result = json.loads(raw)
            if int(result.get("code", 0)) == 200:
                return result
            last_error = f"pushplus code={result.get('code')} msg={result.get('msg')}"
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            last_error = str(error)[:200]
        if attempt < retries:
            time.sleep(2 * attempt)
    raise RuntimeError(f"pushplus delivery failed after {retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def resolve_edition(name, now):
    if name == "auto":
        return "morning" if now.astimezone(CHINA).hour < 12 else "evening"
    return name


def load_feeds(catalog_path, pack, max_feeds):
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    feeds = [feed for feed in catalog["feeds"] if pack in feed.get("packs", [])]
    if not feeds:
        raise SystemExit(f"no feeds found for pack '{pack}'")
    feeds.sort(key=lambda feed: (feed.get("category", ""), feed["title"].casefold()))
    if max_feeds and max_feeds < len(feeds):
        feeds = feeds[:max_feeds]
    return feeds


def main(argv=None):
    parser = argparse.ArgumentParser(description="Push an RSS digest to WeChat via pushplus.")
    parser.add_argument("--catalog", default=str(ROOT / "data/feeds.json"))
    parser.add_argument("--pack", default="top200", help="OPML pack to read, e.g. top200, ai, news")
    parser.add_argument("--edition", choices=["auto", "morning", "evening"], default="auto")
    parser.add_argument("--window-hours", type=float, default=0, help="0 uses the edition default")
    parser.add_argument("--max-feeds", type=int, default=0, help="0 uses every feed in the pack")
    parser.add_argument("--max-per-feed", type=int, default=3)
    parser.add_argument("--max-items", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--page-budget", type=int, default=DEFAULT_PAGE_BUDGET)
    parser.add_argument("--title", default="", help="override the digest title")
    parser.add_argument("--topic", default=os.environ.get("PUSHPLUS_TOPIC", ""))
    parser.add_argument("--channel", default=os.environ.get("PUSHPLUS_CHANNEL", "wechat"))
    parser.add_argument("--token", default=os.environ.get("PUSHPLUS_TOKEN", ""))
    parser.add_argument("--out", default="", help="also write the rendered pages to this directory")
    parser.add_argument("--dry-run", action="store_true", help="render without calling pushplus")
    parser.add_argument("--allow-empty", action="store_true", help="push even when nothing is new")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    edition = resolve_edition(args.edition, now)
    profile = EDITIONS[edition]
    window_hours = args.window_hours or profile["window_hours"]
    window_start = now - timedelta(hours=window_hours)
    local_now = now.astimezone(CHINA)

    feeds = load_feeds(args.catalog, args.pack, args.max_feeds)
    items, stats = collect_items(
        feeds,
        window_start=window_start,
        timeout=args.timeout,
        concurrency=args.concurrency,
        max_per_feed=args.max_per_feed,
    )
    items = items[: args.max_items]

    base_title = args.title or f"{local_now.strftime('%m月%d日')} {profile['title']}"
    pages = render_pages(
        items,
        title=profile["title"],
        kicker=profile["kicker"],
        generated_at=local_now.strftime("%Y-%m-%d %H:%M"),
        window_hours=int(window_hours) if float(window_hours).is_integer() else window_hours,
        page_budget=args.page_budget,
    )

    print(
        f"digest: edition={edition} window={window_hours}h feeds={stats['feeds']} "
        f"ok={stats['ok']} failed={stats['failed']} items={len(items)} pages={len(pages)} "
        f"chars={[len(page) for page in pages]}"
    )

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(pages, start=1):
            target = out_dir / f"digest-{edition}-{index:02d}.html"
            target.write_text(page, encoding="utf-8")
            print(f"wrote {target}")

    if not items and not args.allow_empty:
        print("nothing new in this window; skipping push")
        return 0
    if args.dry_run:
        print("dry run; skipping push")
        return 0
    if not args.token:
        raise SystemExit("PUSHPLUS_TOKEN is required (set the secret or pass --token)")

    for index, page in enumerate(pages, start=1):
        title = page_title(base_title, index, len(pages))
        result = push_page(
            token=args.token,
            title=title,
            content=page,
            topic=args.topic,
            channel=args.channel,
        )
        print(f"pushed page {index}/{len(pages)} ({len(page)} chars): {result.get('msg')}")
        if index < len(pages):
            time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
