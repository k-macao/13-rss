import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.wechat_digest import (
    AUTHOR_LINE,
    CHINA,
    DIGEST_INTRO,
    DIGEST_SUBTITLE,
    DIGEST_TITLE,
    EDITIONS,
    NEON,
    PUSHPLUS_CONTENT_LIMIT,
    PUSHPLUS_TITLE_LIMIT,
    clamp,
    dedupe,
    page_title,
    parse_datetime,
    parse_feed_payload,
    render_pages,
    resolve_edition,
    strip_html,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/wechat-digest.yml"
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def make_items(count, *, category="News", summary="摘要内容，用于测试排版和截断。"):
    return [
        {
            "title": f"测试标题 {index} — an editorial headline about models",
            "link": f"https://example.com/{index}",
            "summary": summary,
            "published_at": NOW - timedelta(minutes=index),
            "source": f"来源 {index % 7}",
            "site_url": "https://example.com",
            "category": category,
            "language": "zh",
        }
        for index in range(count)
    ]


def render(items, **kwargs):
    options = {
        "title": DIGEST_TITLE,
        "kicker": "EVENING EDITION",
        "generated_at": "2026-08-19 18:00",
        "window_hours": 9,
    }
    options.update(kwargs)
    return render_pages(items, **options)


class ParsingTests(unittest.TestCase):
    def test_strip_html_removes_markup_scripts_and_whitespace(self):
        raw = "<p>Hello <b>world</b></p><script>alert(1)</script>\n  &amp; more\u00a0text"
        self.assertEqual(strip_html(raw), "Hello world & more text")

    def test_parse_datetime_accepts_rfc822_and_iso8601(self):
        rfc = parse_datetime("Tue, 19 Aug 2026 09:30:00 +0800")
        iso = parse_datetime("2026-08-19T01:30:00Z")
        self.assertEqual(rfc, iso)
        self.assertIsNone(parse_datetime(""))
        self.assertIsNone(parse_datetime("not a date"))

    def test_parse_datetime_assumes_utc_for_naive_values(self):
        self.assertEqual(parse_datetime("2026-08-19").tzinfo, timezone.utc)

    def test_parse_rss_atom_and_json_feeds(self):
        rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>RSS &amp; item</title><link>https://example.com/a</link>
        <description>&lt;p&gt;body&lt;/p&gt;</description>
        <pubDate>Tue, 19 Aug 2026 09:30:00 +0800</pubDate></item></channel></rss>"""
        atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Atom item</title><link rel="alternate" href="https://example.org/b"/>
        <updated>2026-08-19T01:30:00Z</updated><summary>atom body</summary></entry></feed>"""
        jsonfeed = json.dumps(
            {"items": [{"title": "JSON item", "url": "https://example.net/c", "summary": "json body",
                        "date_published": "2026-08-19T01:30:00Z"}]}
        ).encode("utf-8")

        rss_items = parse_feed_payload(rss)
        atom_items = parse_feed_payload(atom)
        json_items = parse_feed_payload(jsonfeed, "application/feed+json")

        self.assertEqual(rss_items[0]["title"], "RSS & item")
        self.assertEqual(rss_items[0]["link"], "https://example.com/a")
        self.assertEqual(rss_items[0]["summary"], "body")
        self.assertEqual(atom_items[0]["link"], "https://example.org/b")
        self.assertEqual(json_items[0]["link"], "https://example.net/c")
        for parsed in (rss_items, atom_items, json_items):
            self.assertEqual(parsed[0]["published_at"], datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc))

    def test_dedupe_drops_repeated_links_and_titles_keeping_newest_first(self):
        base = make_items(1)[0]
        older = dict(base, published_at=base["published_at"] - timedelta(hours=2))
        same_title = dict(base, link="https://other.example/x", published_at=base["published_at"] - timedelta(hours=1))
        other = dict(base, title="另一个标题", link="https://example.com/other")
        unique = dedupe([older, base, same_title, other])
        self.assertEqual([item["link"] for item in unique], [base["link"], other["link"]])

    def test_dedupe_ignores_trailing_slash_and_fragment(self):
        first = make_items(1)[0]
        second = dict(first, title="不同标题", link=first["link"] + "/#comments")
        self.assertEqual(len(dedupe([first, second])), 1)

    def test_clamp_truncates_on_display_width(self):
        self.assertEqual(clamp("短标题", 20), "短标题")
        clamped = clamp("中文标题" * 10, 20)
        self.assertTrue(clamped.endswith("…"))
        self.assertLessEqual(len(clamped), 11)


class EditionTests(unittest.TestCase):
    def test_editions_cover_the_two_scheduled_pushes(self):
        self.assertEqual(set(EDITIONS), {"morning", "evening"})
        self.assertTrue(all(profile["window_hours"] > 0 for profile in EDITIONS.values()))

    def test_resolve_edition_follows_beijing_time(self):
        morning = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)   # 09:00 CST
        evening = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)  # 18:00 CST
        self.assertEqual(resolve_edition("auto", morning), "morning")
        self.assertEqual(resolve_edition("auto", evening), "evening")
        self.assertEqual(resolve_edition("evening", morning), "evening")

    def test_edition_windows_cover_the_gap_between_pushes(self):
        # Morning covers the 15h overnight gap, evening the 9h working day.
        self.assertGreaterEqual(EDITIONS["morning"]["window_hours"] + EDITIONS["evening"]["window_hours"], 24)

    def test_page_title_is_numbered_only_when_paginated(self):
        self.assertEqual(page_title("08月19日 晚间简报", 1, 1), "08月19日 晚间简报")
        self.assertEqual(page_title("08月19日 晚间简报", 2, 3), "08月19日 晚间简报 2/3")
        self.assertLessEqual(len(page_title("标题" * 200, 2, 3)), PUSHPLUS_TITLE_LIMIT)


class RenderTests(unittest.TestCase):
    def test_single_page_for_a_small_digest(self):
        pages = render(make_items(12))
        self.assertEqual(len(pages), 1)
        self.assertIn("01 / 01", pages[0])
        self.assertIn("本期结束", pages[0])

    def test_pages_stay_within_the_pushplus_content_limit(self):
        pages = render(make_items(400, summary="摘要内容 " * 25))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), PUSHPLUS_CONTENT_LIMIT)

    def test_pagination_keeps_every_item_exactly_once(self):
        items = make_items(220, summary="摘要内容 " * 25)
        pages = render(items)
        combined = "".join(pages)
        for item in items:
            self.assertEqual(combined.count(f'href="{item["link"]}"'), 1, item["link"])

    def test_pagination_marks_continuation_and_repeats_section_headers(self):
        pages = render(make_items(400, summary="摘要内容 " * 25))
        count = len(pages)
        for index, page in enumerate(pages[:-1], start=1):
            self.assertIn(f"{index:02d} / {count:02d}", page)
            self.assertIn(f"CONTINUED IN {index + 1:02d}/{count:02d}", page)
            self.assertIn("新闻", page)  # section header repeated on every page
        self.assertIn("本期结束", pages[-1])

    def test_items_are_numbered_continuously_across_pages(self):
        pages = render(make_items(60, summary="摘要内容 " * 40), page_budget=20_000)
        numbers = [int(value) for page in pages for value in re.findall(r">\s*(\d{2})</span>\s*</td>", page)]
        self.assertEqual(numbers, list(range(1, 61)))

    def test_sections_are_grouped_and_ordered_by_catalog_category(self):
        items = make_items(4, category="News") + make_items(4, category="Artificial Intelligence")
        page = render(items)[0]
        self.assertLess(page.index("人工智能"), page.index("新闻"))
        self.assertIn("Artificial Intelligence", page)

    def test_style_uses_the_editorial_palette_and_small_body_copy(self):
        page = render(make_items(6))[0]
        self.assertIn(NEON, page)            # fluorescent green accent
        self.assertIn("#EFEFEA", page)       # light grey paper
        self.assertIn("#0B0B0B", page)       # black body copy
        self.assertIn("#3A3A38", page)       # dark grey metadata
        self.assertIn("max-width:640px", page)   # portrait single column
        self.assertIn("font-size:12px", page)    # small body copy
        self.assertNotIn("<style", page)         # inline styles only for WeChat

    def test_titles_and_summaries_are_escaped(self):
        item = make_items(1)[0]
        item["title"] = 'Tom & Jerry <script>alert("x")</script>'
        item["summary"] = "5 < 6 & 7 > 3"
        page = render([item])[0]
        self.assertNotIn("<script>", page)
        self.assertIn("&amp;", page)

    def test_empty_digest_still_renders_one_page(self):
        pages = render([])
        self.assertEqual(len(pages), 1)
        self.assertIn("没有新的文章", pages[0])

    def test_branding_title_subtitle_intro_and_author_are_rendered(self):
        page = render(make_items(4))[0]
        self.assertIn(DIGEST_TITLE, page)
        self.assertIn(DIGEST_SUBTITLE, page)
        self.assertIn("全网境内外为你寻找蛛丝马迹", page)
        self.assertIn("Claude、ChatGPT、Gemini、Grok、Qwen 以及 Kimi", page)
        self.assertIn(AUTHOR_LINE, page)

    def test_intro_only_on_first_page_and_author_only_on_last(self):
        pages = render(make_items(220, summary="摘要内容 " * 25))
        self.assertGreater(len(pages), 1)
        self.assertIn(DIGEST_INTRO, pages[0])
        for page in pages[1:]:
            self.assertNotIn(DIGEST_INTRO, page)
        self.assertIn(AUTHOR_LINE, pages[-1])
        for page in pages[:-1]:
            self.assertNotIn(AUTHOR_LINE, page)

    def test_digest_title_has_no_date_or_pushplus_branding(self):
        self.assertNotIn("pushplus", DIGEST_TITLE.casefold())
        self.assertNotRegex(DIGEST_TITLE, r"\d")
        self.assertEqual(page_title(DIGEST_TITLE, 2, 3), "章鱼 AI 全景分析 2/3")

    def test_timestamps_are_rendered_in_beijing_time(self):
        item = make_items(1)[0]
        item["published_at"] = datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc)
        page = render([item])[0]
        self.assertIn(item["published_at"].astimezone(CHINA).strftime("%m-%d %H:%M"), page)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_runs_at_nine_and_eighteen_beijing_time(self):
        crons = re.findall(r'cron:\s*"([^"]+)"', self.text)
        self.assertEqual(crons, ["0 1 * * *", "0 10 * * *"])  # 09:00 and 18:00 UTC+8

    def test_workflow_reads_the_token_from_secrets_only(self):
        self.assertIn("PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}", self.text)
        self.assertNotIn("token=", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)

    def test_workflow_supports_manual_runs_and_calls_the_digest_script(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("scripts/wechat_digest.py", self.text)


if __name__ == "__main__":
    unittest.main()
