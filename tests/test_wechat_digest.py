import unittest
from datetime import datetime, timedelta, timezone

try:
    import feedparser

    HAS_FEEDPARSER = True
except ImportError:  # pragma: no cover - CI installs feedparser
    HAS_FEEDPARSER = False

from scripts.wechat_digest import (
    Item,
    build_blocks,
    build_meta,
    clean_summary,
    compute_window,
    edition_of,
    extract_items,
    normalize_link,
    paginate_blocks,
    push_title,
    render_block,
    render_body,
    render_document,
    render_empty_page,
)

UTC = timezone.utc
NOW = datetime(2026, 8, 19, 1, 0, tzinfo=UTC)

SAMPLE_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Example AI Lab</title>
    <link>https://example.com/</link>
    <description>Example feed</description>
    <item>
      <title>A &amp; B: 新鲜事</title>
      <link>https://example.com/post/1?utm_source=twitter#frag</link>
      <description><![CDATA[<p>第一段。</p>  <p>第二段, 很长的内容, 一直写下去直到超出摘要截断长度, 用来验证 clean_summary 会截断并加上省略号, 而不是把整段都塞进简报里。</p>]]></description>
      <pubDate>Mon, 18 Aug 2026 02:00:00 GMT</pubDate>
    </item>
    <item>
      <title>窗口之外</title>
      <link>https://example.com/post/0</link>
      <description>too old</description>
      <pubDate>Mon, 10 Aug 2026 02:00:00 GMT</pubDate>
    </item>
    <item>
      <title>未来日期</title>
      <link>https://example.com/post/2</link>
      <description>future dated</description>
      <pubDate>Mon, 24 Aug 2026 02:00:00 GMT</pubDate>
    </item>
    <item>
      <title>无链接条目</title>
      <description>no link</description>
      <pubDate>Mon, 18 Aug 2026 03:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def sample_feed():
    return {
        "id": "abc123",
        "title": "Example AI Lab",
        "feed_url": "https://example.com/feed.xml",
        "site_url": "https://example.com/",
        "category": "Artificial Intelligence",
        "kind": "article",
        "language": "zh",
    }


def make_item(title, hours_ago, category="Artificial Intelligence", link=None):
    return Item(
        title=title,
        link=link or f"https://example.com/{abs(hash(title)) % 100000}",
        published=NOW - timedelta(hours=hours_ago),
        summary="一段摘要文字。",
        feed_title="Example AI Lab",
        site_url="https://example.com/",
        category=category,
        kind="article",
    )


def sample_meta(item_count=0):
    return build_meta(NOW, NOW - timedelta(hours=15), "top200", 200, item_count, 0)


class WindowTests(unittest.TestCase):
    def test_morning_push_covers_since_previous_evening(self):
        # 01:00 UTC = 09:00 Beijing: covers since yesterday 10:00 UTC (18:00 Beijing)
        boundary = compute_window(datetime(2026, 8, 19, 1, 0, tzinfo=UTC), None)
        self.assertEqual(boundary, datetime(2026, 8, 18, 10, 0, tzinfo=UTC))

    def test_evening_push_covers_since_morning(self):
        # 10:00 UTC = 18:00 Beijing: covers since 01:00 UTC (09:00 Beijing)
        boundary = compute_window(datetime(2026, 8, 19, 10, 0, tzinfo=UTC), None)
        self.assertEqual(boundary, datetime(2026, 8, 19, 1, 0, tzinfo=UTC))

    def test_windows_are_contiguous(self):
        # 晨报窗口 15h (18:00→09:00 北京), 晚报窗口 9h (09:00→18:00 北京)
        morning = compute_window(datetime(2026, 8, 19, 1, 0, tzinfo=UTC), None)
        evening = compute_window(datetime(2026, 8, 19, 10, 0, tzinfo=UTC), None)
        self.assertEqual(morning + timedelta(hours=15), evening)
        self.assertEqual(evening + timedelta(hours=9), datetime(2026, 8, 19, 10, 0, tzinfo=UTC))

    def test_explicit_lookback_wins(self):
        now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
        self.assertEqual(compute_window(now, 6), now - timedelta(hours=6))

    def test_edition_labels(self):
        self.assertEqual(edition_of(datetime(2026, 8, 19, 1, 0, tzinfo=UTC)), ("晨报", "MORNING EDITION"))
        self.assertEqual(edition_of(datetime(2026, 8, 19, 10, 0, tzinfo=UTC)), ("晚报", "EVENING EDITION"))


class TextTests(unittest.TestCase):
    def test_clean_summary_strips_tags_and_truncates(self):
        text = clean_summary("<p>第一段。</p>  <p>第二段很长很长很长</p>", limit=12)
        self.assertNotIn("<", text)
        self.assertLessEqual(len(text), 13)
        self.assertTrue(text.endswith("…"))

    def test_normalize_link_drops_utm_and_fragment(self):
        a = normalize_link("https://Example.COM/post/1?utm_source=x#frag")
        b = normalize_link("https://example.com/post/1?utm_medium=y")
        self.assertEqual(a, b)
        self.assertEqual(a, "example.com/post/1")

    def test_escaping(self):
        item = Item(
            title='<script>alert("x")</script>',
            link='https://example.com/?a=1&b=2" onmouseover="x',
            published=NOW,
            summary="&<>",
            feed_title="T",
            site_url="https://example.com/",
            category="News",
            kind="article",
        )
        html = render_document(render_block(("item", (item, 1))), sample_meta(), 1, 1)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


@unittest.skipUnless(HAS_FEEDPARSER, "feedparser not installed")
class ExtractTests(unittest.TestCase):
    def test_extract_items_filters_window_and_invalid_entries(self):
        parsed = feedparser.parse(SAMPLE_RSS.encode("utf-8"))
        window_start = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
        now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
        items = extract_items(parsed, sample_feed(), window_start, now, max_per_feed=10)
        self.assertEqual([item.title for item in items], ["A & B: 新鲜事"])
        self.assertEqual(items[0].link, "https://example.com/post/1?utm_source=twitter#frag")
        self.assertEqual(items[0].category, "Artificial Intelligence")
        self.assertEqual(items[0].domain, "example.com")

    def test_max_per_feed_cap(self):
        parsed = feedparser.parse(SAMPLE_RSS)
        window_start = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
        now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
        self.assertEqual(len(extract_items(parsed, sample_feed(), window_start, now, max_per_feed=1)), 1)


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.items = [
            make_item(f"文章标题 {i}", hours_ago=i,
                      category=("Artificial Intelligence" if i % 2 else "Engineering & Technology"))
            for i in range(40)
        ]
        self.meta = sample_meta(len(self.items))

    def test_pages_fit_limit_and_preserve_order(self):
        blocks = build_blocks(self.items)
        pages = paginate_blocks(blocks, self.meta, max_chars=8000)
        self.assertGreater(len(pages), 1)
        flattened = [block for page in pages for block in page if block[0] == "item"]
        self.assertEqual(len(flattened), len(self.items))
        self.assertEqual([payload[1] for _, payload in flattened], list(range(1, 41)))
        for index, page in enumerate(pages, start=1):
            self.assertFalse(page and page[-1][0] == "section", "page must not end with orphan header")
            doc = render_document(render_body(page), self.meta, index, len(pages))
            self.assertLessEqual(len(doc), 8000)
            self.assertEqual(doc.count("<div"), doc.count("</div>"), "unbalanced divs")

    def test_single_page_under_big_limit(self):
        pages = paginate_blocks(build_blocks(self.items[:5]), self.meta, max_chars=100000)
        self.assertEqual(len(pages), 1)

    def test_empty_blocks_render_empty_page(self):
        doc = render_empty_page(sample_meta(0))
        self.assertIn("本时段暂无新文章", doc)

    def test_style_tokens_present(self):
        doc = render_document(render_block(("item", (self.items[0], 1))), self.meta, 1, 1)
        for token in ("#00FF9C", "#EFEFED", "#0A0A0B", "#4D4D53", "TIDINGS", "P.01 / 01"):
            self.assertIn(token, doc)

    def test_push_title_suffix_only_when_paginated(self):
        self.assertEqual(push_title(self.meta, 1, 1), "Tidings 晨报 · 2026.08.19")
        self.assertEqual(push_title(self.meta, 2, 3), "Tidings 晨报 · 2026.08.19（2/3）")


if __name__ == "__main__":
    unittest.main()
