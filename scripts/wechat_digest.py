#!/usr/bin/env python3
"""Tidings RSS → WeChat daily digest via PushPlus.

Builds a portrait, editorial-magazine style HTML digest (归藏 guizang style:
衬线大标题 + 非衬线正文 + 等宽元信息, hairline grid, 单一荧光绿强调色,
浅灰地 / 黑色正文 / 深灰字 / 小字) from the curated catalog, paginates it to
PushPlus's per-message character limit (10 万字 for members, configurable) and
pushes every page to WeChat through the PushPlus send API.

Scheduled twice a day (Beijing time): 09:00 晨报 and 18:00 晚报. The digest
window is contiguous between pushes, so the two editions never double-report
the same items (see compute_window).
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import gzip
import html as html_module
import json
import logging
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    import feedparser
except ImportError:  # pragma: no cover - optional until installed
    feedparser = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = REPO_ROOT / "data" / "feeds.json"
DEFAULT_PACK = "top200"

PUSHPLUS_SEND_URL = "https://www.pushplus.plus/send"
# PushPlus length limits: 2 万字 for verified users, 10 万字 for members.
DEFAULT_MAX_CHARS = 100_000
MIN_MAX_CHARS = 2_000
PUSHPLUS_TITLE_MAX = 100  # verified users (members get 200)
# Send API rate limit is 5 requests/minute; keep a safe spacing between pages.
PUSHPLUS_MIN_INTERVAL_SECONDS = 13.0

USER_AGENT = (
    "Mozilla/5.0 (compatible; TidingsRSS-Digest/1.0; "
    "+https://github.com/k-macao/13-rss)"
)

# ---------------------------------------------------------------------------
# 归藏 · 电子杂志风 palette (user-specified override)
#   浅灰地 / 黑色正文 / 深灰字 / 荧光绿, 小字号高密度阅读.
# ---------------------------------------------------------------------------
PALETTE = {
    "paper": "#EFEFED",  # 浅灰色地 (light gray ground)
    "ink": "#0A0A0B",    # 黑色正文 (black body text)
    "grey": "#4D4D53",   # 深灰字 (dark gray secondary text)
    "neon": "#00FF9C",   # 萤光绿色字体 (fluorescent green accent)
}

CATEGORY_KICKERS = {
    "Artificial Intelligence": ("人工智能", "ARTIFICIAL INTELLIGENCE"),
    "Engineering & Technology": ("工程与技术", "ENGINEERING & TECHNOLOGY"),
    "Security": ("安全", "SECURITY"),
    "Technology Media": ("科技媒体", "TECHNOLOGY MEDIA"),
    "Tech Newsletters & Weeklies": ("技术周刊", "WEEKLIES & NEWSLETTERS"),
    "News": ("新闻", "NEWS"),
    "Research & Science": ("科研与科学", "RESEARCH & SCIENCE"),
    "Personal Blogs": ("独立博客", "PERSONAL BLOGS"),
    "Communities": ("社区", "COMMUNITIES"),
    "Podcasts": ("播客", "PODCASTS"),
    "Videos": ("视频", "VIDEOS"),
    "Product & Design": ("产品与设计", "PRODUCT & DESIGN"),
    "Business & Startups": ("商业与创业", "BUSINESS & STARTUPS"),
    "Culture & Ideas": ("文化与观点", "CULTURE & IDEAS"),
}

KIND_LABELS = {"article": "文章", "podcast": "播客", "video": "视频"}

WEEKDAYS_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

BEIJING = timezone(timedelta(hours=8))

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

LOG = logging.getLogger("wechat-digest")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def load_feeds(catalog_path: Path, pack: str) -> list[dict]:
    """Load the catalog and return feeds that belong to the given pack."""
    with catalog_path.open(encoding="utf-8") as handle:
        catalog = json.load(handle)
    feeds = [f for f in catalog.get("feeds", []) if pack in f.get("packs", [])]
    if not feeds:
        raise ValueError(f"pack {pack!r} has no feeds in {catalog_path}")
    return feeds


# ---------------------------------------------------------------------------
# Digest window (contiguous morning / evening editions)
# ---------------------------------------------------------------------------


def compute_window(now: datetime, lookback_hours: float | None) -> datetime:
    """Return the UTC start of the digest window.

    Scheduled pushes run at 01:00 UTC (09:00 Beijing, 晨报) and 10:00 UTC
    (18:00 Beijing, 晚报). Without an explicit lookback the windows are
    contiguous so the two daily editions never double-report items:

    * before 10:00 UTC  → since yesterday 10:00 UTC (the previous 晚报)
    * 10:00 UTC onward  → since today 01:00 UTC (the previous 晨报)
    """
    now = now.astimezone(timezone.utc)
    if lookback_hours is not None:
        return now - timedelta(hours=lookback_hours)
    if now.hour < 10:
        return (now - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return now.replace(hour=1, minute=0, second=0, microsecond=0)


def edition_of(now: datetime) -> tuple[str, str]:
    """Return (中文版名, 英文版名) for a push happening at `now`."""
    if now.astimezone(timezone.utc).hour < 10:
        return "晨报", "MORNING EDITION"
    return "晚报", "EVENING EDITION"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_feed(feed: dict, timeout: float, attempts: int = 2):
    """Fetch and parse one feed. Returns (feed, parsed) or (feed, exception)."""
    url = feed["feed_url"]
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "gzip",
                    "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(4 * 1024 * 1024)  # 4 MB cap
                encoding = response.headers.get("Content-Encoding", "").lower()
                if encoding == "gzip" or payload[:2] == b"\x1f\x8b":
                    payload = gzip.decompress(payload)
            if feedparser is None:
                raise RuntimeError("feedparser is not installed")
            parsed = feedparser.parse(payload)
            if parsed.get("bozo") and not parsed.get("entries"):
                raise RuntimeError(f"feed parse failed: {parsed.get('bozo_exception')}")
            return feed, parsed
        except Exception as exc:  # noqa: BLE001 - reported to caller
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.0)
    return feed, last_error


# ---------------------------------------------------------------------------
# Entry extraction
# ---------------------------------------------------------------------------


def entry_time(entry, now: datetime) -> datetime | None:
    """Return the UTC publish time of a feed entry, ignoring bogus/future dates."""
    for key in ("published_parsed", "updated_parsed"):
        stamp = entry.get(key)
        if not stamp:
            continue
        try:
            when = datetime.fromtimestamp(calendar.timegm(stamp), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            continue
        if when > now + timedelta(hours=1):  # future-dated entries are noise
            continue
        return when
    return None


def clean_summary(raw: str, limit: int = 140) -> str:
    """Strip tags and collapse whitespace; truncate without splitting words."""
    text = TAG_RE.sub(" ", raw or "")
    text = html_module.unescape(text)
    text = SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        cut = text[:limit].rstrip()
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
        text = cut.rstrip("，。；、,.;:") + "…"
    return text


def normalize_link(url: str) -> str:
    """Normalize a link for dedupe: lowercase host, drop fragment & utm params."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    host = parts.netloc.lower()
    query = "&".join(
        part
        for part in parts.query.split("&")
        if part and not part.lower().startswith(("utm_", "ref=", "spm="))
    )
    return f"{host}{parts.path.rstrip('/') or '/'}" + (f"?{query}" if query else "")


def extract_items(
    parsed,
    feed: dict,
    window_start: datetime,
    now: datetime,
    max_per_feed: int,
) -> list["Item"]:
    """Collect in-window items from one parsed feed."""
    items: list[Item] = []
    site_url = feed.get("site_url") or feed["feed_url"]
    for entry in parsed.get("entries", []):
        when = entry_time(entry, now)
        if when is None or not (window_start < when <= now):
            continue
        link = (entry.get("link") or "").strip()
        title = clean_summary(entry.get("title") or "", limit=200)
        if not link or not title:
            continue
        items.append(
            Item(
                title=title,
                link=link,
                published=when,
                summary=clean_summary(entry.get("summary") or entry.get("description") or ""),
                feed_title=feed.get("title") or site_url,
                site_url=site_url,
                category=feed.get("category") or "Other",
                kind=feed.get("kind") or "article",
            )
        )
        if len(items) >= max_per_feed:
            break
    return items


@dataclass
class Item:
    title: str
    link: str
    published: datetime
    summary: str
    feed_title: str
    site_url: str
    category: str
    kind: str

    @property
    def domain(self) -> str:
        try:
            return (urlsplit(self.link).netloc or urlsplit(self.site_url).netloc).lstrip("www.")
        except ValueError:
            return ""


# ---------------------------------------------------------------------------
# Digest metadata
# ---------------------------------------------------------------------------


@dataclass
class DigestMeta:
    edition: str
    edition_en: str
    date_cst: str
    weekday_zh: str
    time_cst: str
    pack: str
    feed_count: int
    item_count: int
    failed_feeds: int
    window_hours: float


def build_meta(now: datetime, window_start: datetime, pack: str, feed_count: int,
               item_count: int, failed_feeds: int) -> DigestMeta:
    edition, edition_en = edition_of(now)
    cst = now.astimezone(BEIJING)
    return DigestMeta(
        edition=edition,
        edition_en=edition_en,
        date_cst=f"{cst:%Y.%m.%d}",
        weekday_zh=WEEKDAYS_ZH[cst.weekday()],
        time_cst=f"{cst:%H:%M}",
        pack=pack,
        feed_count=feed_count,
        item_count=item_count,
        failed_feeds=failed_feeds,
        window_hours=round((now - window_start).total_seconds() / 3600, 1),
    )


# ---------------------------------------------------------------------------
# HTML rendering — 归藏 · 电子杂志风, portrait magazine sheet
# ---------------------------------------------------------------------------

SERIF_STACK = (
    '"Playfair Display", "Noto Serif SC", "Songti SC", "STSong", "SimSun", '
    "Georgia, serif"
)
SANS_STACK = (
    '-apple-system, "PingFang SC", "Noto Sans SC", "Microsoft YaHei", '
    '"Helvetica Neue", Helvetica, Arial, sans-serif'
)
MONO_STACK = (
    '"IBM Plex Mono", "SF Mono", "SFMono-Regular", Menlo, Consolas, '
    '"Courier New", monospace'
)

STYLE = f"""
:root{{--paper:{PALETTE['paper']};--ink:{PALETTE['ink']};--grey:{PALETTE['grey']};
  --neon:{PALETTE['neon']};--hair:rgba(10,10,11,.10);--hair-strong:rgba(10,10,11,.24)}}
*{{margin:0;padding:0;box-sizing:border-box}}
html{{-webkit-text-size-adjust:100%}}
body{{background:var(--paper);color:var(--ink);font-family:{SANS_STACK};
  font-size:13px;line-height:1.75;-webkit-font-smoothing:antialiased}}
a{{color:inherit;text-decoration:none;border-bottom:1px dotted var(--hair-strong);padding-bottom:1px}}
.sheet{{max-width:470px;margin:0 auto;padding:16px 22px 30px;background:var(--paper)}}
.mono{{font-family:{MONO_STACK}}}
.serif{{font-family:{SERIF_STACK}}}
.chrome{{display:flex;justify-content:space-between;font-family:{MONO_STACK};
  font-size:9.5px;letter-spacing:.16em;color:var(--grey);text-transform:uppercase;
  border-bottom:1px solid var(--hair-strong);padding-bottom:6px}}
.masthead{{padding:20px 0 16px;border-bottom:1px solid var(--hair)}}
.kicker{{font-family:{MONO_STACK};font-size:10px;letter-spacing:.24em;color:var(--neon);
  text-transform:uppercase;font-weight:700}}
.display{{font-family:{SERIF_STACK};font-size:34px;line-height:1.08;letter-spacing:.06em;
  font-weight:700;margin:8px 0 2px}}
.display-zh{{font-family:{SERIF_STACK};font-size:17px;letter-spacing:.5em;
  font-weight:600;margin-bottom:8px}}
.neon-bar{{height:3px;width:64px;background:var(--neon);
  box-shadow:0 0 6px rgba(0,255,156,.55);margin:10px 0 12px}}
.lead-line{{font-family:{SERIF_STACK};font-size:11.5px;color:var(--grey);
  letter-spacing:.03em;line-height:1.9}}
.section{{margin-top:24px}}
.sec-head{{border-top:1px solid var(--hair-strong);padding-top:9px;margin-bottom:4px}}
.sec-row{{display:flex;align-items:baseline}}
.sec-mark{{display:inline-block;width:6px;height:6px;background:var(--neon);
  margin-right:8px;box-shadow:0 0 5px rgba(0,255,156,.6)}}
.sec-zh{{font-family:{SERIF_STACK};font-size:15px;font-weight:700;letter-spacing:.14em}}
.sec-en{{font-family:{MONO_STACK};font-size:8.5px;letter-spacing:.22em;color:var(--neon);
  text-transform:uppercase;margin-left:9px}}
.sec-count{{margin-left:auto;font-family:{MONO_STACK};font-size:9px;color:var(--grey);
  letter-spacing:.1em}}
.item{{padding:13px 0 12px;border-bottom:1px solid var(--hair)}}
.section .item:last-child{{border-bottom:none}}
.item-head{{display:flex;align-items:baseline;margin-bottom:4px}}
.idx{{font-family:{MONO_STACK};font-size:9.5px;color:var(--neon);letter-spacing:.04em;
  font-weight:700}}
.src{{font-family:{MONO_STACK};font-size:9.5px;letter-spacing:.09em;color:var(--grey);
  text-transform:uppercase;margin-left:8px;max-width:62%;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.title{{font-family:{SERIF_STACK};font-size:14.5px;font-weight:700;line-height:1.55;
  letter-spacing:.01em}}
.summary{{font-size:12px;line-height:1.72;color:var(--ink);margin-top:4px;opacity:.94}}
.i-meta{{font-family:{MONO_STACK};font-size:9px;letter-spacing:.07em;color:var(--grey);
  margin-top:6px}}
.lead .title{{font-size:17.5px;line-height:1.5}}
.lead .summary{{font-size:12.5px}}
.lead .kicker{{margin-bottom:6px}}
.empty{{padding:30px 0 10px}}
.empty .title{{font-size:16px}}
.empty .sub{{font-family:{MONO_STACK};font-size:10px;letter-spacing:.2em;color:var(--grey);
  margin-top:10px;text-transform:uppercase}}
.foot{{margin-top:26px;border-top:1px solid var(--hair-strong);padding-top:10px}}
.foot-row{{display:flex;justify-content:space-between;font-family:{MONO_STACK};
  font-size:9px;letter-spacing:.16em;color:var(--grey);text-transform:uppercase;
  padding:2px 0}}
.foot-row .page-mark{{color:var(--neon);font-weight:700}}
.foot .neon-bar{{margin:12px auto 0;width:44px}}
@media(min-width:520px){{.sheet{{border-left:1px solid #DBDBD9;border-right:1px solid #DBDBD9}}}}
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- 归藏 · 电子杂志风 (guizang editorial-magazine style) -->
<title>{title}</title>
<style>{style}</style>
</head>
<body>
<div class="sheet">
{chrome}
{masthead}
{body}
{foot}
</div>
</body>
</html>
"""


def _esc(text: str) -> str:
    return html_module.escape(text, quote=True)


def render_chrome(meta: DigestMeta) -> str:
    return (
        '<div class="chrome"><span>Tidings · Daily RSS</span>'
        f"<span>{meta.edition_en} / {meta.date_cst}</span></div>"
    )


def render_masthead(meta: DigestMeta, page: int, page_count: int) -> str:
    page_mark = f"P.{page:02d} / {page_count:02d}"
    stats = [
        f"北京时间 {meta.time_cst}",
        f"精选 {meta.feed_count} 源",
        f"过去 {meta.window_hours:g} 小时新增 {meta.item_count} 篇",
    ]
    if meta.failed_feeds:
        stats.append(f"{meta.failed_feeds} 源未响应")
    return (
        '<header class="masthead">'
        f'<div class="kicker">{meta.edition_en} · {meta.edition}</div>'
        '<h1 class="display">TIDINGS</h1>'
        f'<div class="display-zh">每日{meta.edition}</div>'
        '<div class="neon-bar"></div>'
        f'<p class="lead-line">{meta.date_cst} {meta.weekday_zh}'
        f" · {' · '.join(stats)} · <b>{page_mark}</b></p>"
        "</header>"
    )


def render_foot(meta: DigestMeta, page: int, page_count: int) -> str:
    return (
        '<footer class="foot">'
        '<div class="foot-row"><span>Tidings · 每日{edition}</span>'
        '<span class="page-mark">P.{page:02d} / {page_count:02d}</span></div>'
        '<div class="foot-row"><span>归藏排版 · 国际杂志风</span>'
        "<span>PushPlus 推送</span></div>"
        '<div class="neon-bar"></div>'
        "</footer>"
    ).format(edition=meta.edition, page=page, page_count=page_count)


def render_section(category: str, count: int) -> str:
    """Open a section wrapper and emit its header (closed by render_body)."""
    zh, en = CATEGORY_KICKERS.get(category, (category, category.upper()))
    return (
        '<div class="section">'
        '<div class="sec-head"><div class="sec-row">'
        f'<span class="sec-mark"></span><span class="sec-zh">{_esc(zh)}</span>'
        f'<span class="sec-en">{_esc(en)}</span>'
        f'<span class="sec-count">{count} 篇</span>'
        "</div></div>"
    )


def render_item(item: Item, index: int) -> str:
    when = item.published.astimezone(BEIJING)
    meta_line = (
        f"{when:%m-%d %H:%M} · {KIND_LABELS.get(item.kind, '文章')}"
        + (f" · {_esc(item.domain)}" if item.domain else "")
    )
    lead = " lead" if index == 1 else ""
    kicker = '<div class="kicker">Lead Story · 头条</div>' if index == 1 else ""
    return (
        f'<div class="item{lead}">'
        f'<div class="item-head"><span class="idx">{index:02d}</span>'
        f'<span class="src">{_esc(item.feed_title)}</span></div>'
        f"{kicker}"
        f'<div class="title"><a href="{_esc(item.link)}">{_esc(item.title)}</a></div>'
        + (f'<p class="summary">{_esc(item.summary)}</p>' if item.summary else "")
        + f'<div class="i-meta">{_esc(meta_line)}</div>'
        "</div>"
    )


def render_document(body: str, meta: DigestMeta, page: int, page_count: int) -> str:
    """Assemble one full, self-contained HTML page (one PushPlus message)."""
    title = f"Tidings {meta.edition} · {meta.date_cst} · P.{page:02d}/{page_count:02d}"
    return TEMPLATE.format(
        title=_esc(title),
        style=STYLE,
        chrome=render_chrome(meta),
        masthead=render_masthead(meta, page, page_count),
        body=body,
        foot=render_foot(meta, page, page_count),
    )


def render_empty_page(meta: DigestMeta) -> str:
    body = (
        '<div class="empty">'
        '<div class="title serif">本时段暂无新文章</div>'
        '<div class="sub">No new items in this window</div>'
        '<div class="neon-bar"></div>'
        "</div>"
    )
    return render_document(body, meta, 1, 1)


# ---------------------------------------------------------------------------
# Pagination (PushPlus 单条 10 万字上限, 超长分页)
# ---------------------------------------------------------------------------


def build_blocks(items: list[Item]) -> list[tuple]:
    """Flatten items into (kind, payload) blocks grouped by category.

    Sections are ordered by their newest item; items inside a section are
    ordered newest first. Item payloads carry a running 1-based index.
    """
    by_category: dict[str, list[Item]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)
    ordered = sorted(
        by_category.items(),
        key=lambda kv: max(item.published for item in kv[1]),
        reverse=True,
    )
    blocks: list[tuple] = []
    index = 0
    for category, section_items in ordered:
        section_items.sort(key=lambda item: item.published, reverse=True)
        blocks.append(("section", (category, len(section_items))))
        for item in section_items:
            index += 1
            blocks.append(("item", (item, index)))
    return blocks


def render_block(block: tuple) -> str:
    kind, payload = block
    if kind == "section":
        category, count = payload
        return render_section(category, count)
    item, index = payload
    return render_item(item, index)


def paginate_blocks(blocks: list[tuple], meta: DigestMeta, max_chars: int) -> list[list[tuple]]:
    """Split blocks into pages whose full rendered documents fit max_chars.

    Pages break on item boundaries; a section header is never orphaned at the
    end of a page. The page header/footer overhead is measured once and a
    safety margin is kept, so rendered pages stay under the PushPlus limit.
    """
    if not blocks:
        return [[]]
    overhead = len(render_document("", meta, 1, 1)) + 512
    budget = max(1000, max_chars - overhead)
    pages: list[list[tuple]] = []
    current: list[tuple] = []
    used = 0
    for block in blocks:
        size = len(render_block(block))
        if current and used + size > budget:
            if current[-1][0] == "section":
                orphan = current.pop()
                if current:
                    pages.append(current)
                current, used = [orphan], len(render_block(orphan))
            else:
                pages.append(current)
                current, used = [], 0
        current.append(block)
        used += size
    if current:
        pages.append(current)
    return pages


def render_body(blocks: list[tuple]) -> str:
    """Join blocks into the page body, closing section wrappers correctly.

    A page may end mid-section (the category continues on the next page) or
    start with plain items (a continued section), so wrapper open/close is
    tracked here rather than inside render_block.
    """
    parts: list[str] = []
    in_section = False
    for block in blocks:
        if block[0] == "section":
            if in_section:
                parts.append("</div>")
            in_section = True
        parts.append(render_block(block))
    if in_section:
        parts.append("</div>")
    return "\n".join(parts)


def render_pages(blocks: list[tuple], meta: DigestMeta, max_chars: int) -> list[str]:
    pages = paginate_blocks(blocks, meta, max_chars)
    if not blocks:
        return [render_empty_page(meta)]
    return [
        render_document(render_body(page), meta, i, len(pages))
        for i, page in enumerate(pages, start=1)
    ]


# ---------------------------------------------------------------------------
# PushPlus
# ---------------------------------------------------------------------------


def send_pushplus(
    token: str,
    title: str,
    content: str,
    topic: str | None = None,
    to: str | None = None,
    retries: int = 2,
) -> dict:
    """Send one message through the PushPlus API; returns the JSON response."""
    payload: dict = {"token": token, "title": title, "content": content, "template": "html"}
    if topic:
        payload["topic"] = topic
    if to:
        payload["to"] = to
    data = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                PUSHPLUS_SEND_URL,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
            if result.get("code") == 200:
                return result
            last_error = RuntimeError(
                f"PushPlus returned code {result.get('code')}: {result.get('msg')}"
            )
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last_error = exc
        if attempt < retries:
            time.sleep(4.0)
    raise last_error  # type: ignore[misc]


def push_title(meta: DigestMeta, page: int, page_count: int) -> str:
    base = f"Tidings {meta.edition} · {meta.date_cst}"
    if page_count > 1:
        base += f"（{page}/{page_count}）"
    return base[:PUSHPLUS_TITLE_MAX]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        LOG.warning("%s=%r 不是数字, 回退到 %d", name, raw, default)
        return default


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 Tidings 精选 RSS 的新文章排版成归藏杂志风简报, 分页推送到微信 (PushPlus)."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="feeds.json 路径")
    parser.add_argument("--pack", default=os.environ.get("DIGEST_PACK", DEFAULT_PACK),
                        help="订阅源合集 (默认 top200)")
    parser.add_argument("--lookback-hours", type=float, default=None,
                        help="覆盖时间窗口(小时); 留空按推送时刻自动推断晨报/晚报")
    parser.add_argument("--max-chars", type=int, default=None,
                        help="单条消息字符上限 (默认 PUSHPLUS_MAX_CHARS 或 100000; "
                             "PushPlus 会员 10 万 / 实名 2 万)")
    parser.add_argument("--max-items", type=int, default=0, help="最多收录条数 (0 = 不限)")
    parser.add_argument("--max-per-feed", type=int, default=3, help="每个订阅源最多收录条数")
    parser.add_argument("--concurrency", type=int, default=16, help="并发抓取数")
    parser.add_argument("--timeout", type=float, default=20.0, help="单源抓取超时(秒)")
    parser.add_argument("--token", default=None, help="PushPlus token (默认取 PUSHPLUS_TOKEN)")
    parser.add_argument("--topic", default=None, help="PushPlus 群组编码 (默认取 PUSHPLUS_TOPIC)")
    parser.add_argument("--dry-run", action="store_true", help="只渲染 HTML 不发送")
    parser.add_argument("--no-push-when-empty", action="store_true",
                        help="本时段无新文章时不推送")
    parser.add_argument("--save-pages", type=Path, default=None,
                        help="把每页 HTML 存到该目录 (便于预览/归档)")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    max_chars = args.max_chars or _env_int("PUSHPLUS_MAX_CHARS", DEFAULT_MAX_CHARS)
    if max_chars < MIN_MAX_CHARS:
        LOG.error("--max-chars 过小 (至少 %d)", MIN_MAX_CHARS)
        return 2
    token = args.token or os.environ.get("PUSHPLUS_TOKEN", "").strip()
    topic = args.topic or os.environ.get("PUSHPLUS_TOPIC", "").strip()
    to = os.environ.get("PUSHPLUS_TO", "").strip()

    try:
        feeds = load_feeds(args.catalog, args.pack)
    except (OSError, ValueError) as exc:
        LOG.error("加载目录失败: %s", exc)
        return 2

    now = datetime.now(timezone.utc)
    window_start = compute_window(now, args.lookback_hours)
    LOG.info(
        "合集=%s 源数=%d 窗口=%s → %s (UTC)",
        args.pack, len(feeds),
        window_start.strftime("%m-%d %H:%M"), now.strftime("%m-%d %H:%M"),
    )

    parsed_by_id: dict[str, object] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(fetch_feed, feed, args.timeout): feed for feed in feeds}
        for future in concurrent.futures.as_completed(futures):
            feed, outcome = future.result()
            parsed_by_id[feed["id"]] = outcome

    failed_feeds = 0
    items: list[Item] = []
    for feed in feeds:
        outcome = parsed_by_id[feed["id"]]
        if isinstance(outcome, Exception):
            failed_feeds += 1
            LOG.debug("抓取失败 %s: %s", feed["feed_url"], outcome)
            continue
        items.extend(extract_items(outcome, feed, window_start, now, args.max_per_feed))

    # newest first, drop duplicates across feeds (same canonical link)
    items.sort(key=lambda item: item.published, reverse=True)
    seen: set[str] = set()
    deduped: list[Item] = []
    for item in items:
        key = normalize_link(item.link)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    items = deduped[: args.max_items] if args.max_items else deduped

    LOG.info("抓取成功=%d 失败=%d 窗口内条目=%d", len(feeds) - failed_feeds, failed_feeds, len(items))

    meta = build_meta(now, window_start, args.pack, len(feeds), len(items), failed_feeds)
    blocks = build_blocks(items)
    pages = render_pages(blocks, meta, max_chars)
    for i, page in enumerate(pages, start=1):
        LOG.info("第 %d/%d 页: %d 字符", i, len(pages), len(page))

    if args.save_pages:
        args.save_pages.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(pages, start=1):
            path = args.save_pages / f"tidings-{meta.edition}-p{i:02d}-of-{len(pages):02d}.html"
            path.write_text(page, encoding="utf-8")
            LOG.info("已保存 %s", path)
        summary = {
            "edition": meta.edition,
            "date_cst": meta.date_cst,
            "time_cst": meta.time_cst,
            "window_hours": meta.window_hours,
            "feeds": len(feeds),
            "failed_feeds": failed_feeds,
            "items": len(items),
            "pages": len(pages),
            "max_chars": max_chars,
        }
        (args.save_pages / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.dry_run:
        LOG.info("dry-run 完成, 未发送推送")
        return 0

    if not token:
        LOG.error("未配置 PUSHPLUS_TOKEN (仓库 Secrets 或 --token)")
        return 2
    if not items and args.no_push_when_empty:
        LOG.info("本时段无新文章, 已按 --no-push-when-empty 跳过推送")
        return 0

    failures = 0
    for i, page in enumerate(pages, start=1):
        try:
            result = send_pushplus(token, push_title(meta, i, len(pages)), page, topic, to)
            LOG.info("已推送第 %d/%d 页: %s", i, len(pages), result.get("msg", ""))
        except Exception as exc:  # noqa: BLE001 - one page failing must not sink the run
            failures += 1
            LOG.error("第 %d/%d 页推送失败: %s", i, len(pages), exc)
        if i < len(pages):
            time.sleep(PUSHPLUS_MIN_INTERVAL_SECONDS)

    if failures:
        LOG.error("%d/%d 页推送失败", failures, len(pages))
        return 1
    LOG.info("推送完成: %s · %d 条 · %d 页", meta.edition, len(items), len(pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
