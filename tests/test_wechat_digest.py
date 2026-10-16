import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from scripts.wechat_digest import (
    AUTHOR_LINE,
    CHINA,
    DIGEST_INTRO,
    DIGEST_SCOPE,
    DIGEST_SUBTITLE,
    DIGEST_TITLE,
    EDITIONS,
    NEON,
    PUSHPLUS_CONTENT_LIMIT,
    PUSHPLUS_TITLE_LIMIT,
    TRANSLATION_TAG,
    clamp,
    dedupe,
    display_width,
    page_title,
    parse_datetime,
    parse_feed_payload,
    is_english,
    render_pages,
    translate_items,
    translate_to_chinese,
    resolve_edition,
    strip_html,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/wechat-digest.yml"
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def section_head(label):
    """Match a rendered category header, not the same word inside intro copy."""
    return f">{label}</span>"


def translate_response(mapping):
    """Stand in for the Google Translate endpoint, answering from `mapping`.

    A source string missing from the mapping is echoed back, which is what the
    real endpoint effectively does for text it cannot translate.
    """

    def _urlopen(request, **kwargs):
        source = parse_qs(urlsplit(request.full_url).query)["q"][0]
        target = mapping.get(source, source)

        class Response:
            def read(self):
                return json.dumps([[[target, source, None, None, 1]], None, "en"]).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    return _urlopen


def offline_urlopen(request, **kwargs):
    raise URLError("translation endpoint unreachable")


ENGLISH_PAIRS = {
    "OpenAI ships a smaller model that runs on device": "OpenAI 推出可在设备端运行的更小模型",
    "The new model keeps most of the reasoning quality at a tenth of the cost.":
        "新模型以十分之一的成本保留了大部分推理能力。",
    "A new model improves reasoning": "新模型提升了推理能力",
}


def make_english_item(index=0, *, category="Artificial Intelligence"):
    return {
        "title": "OpenAI ships a smaller model that runs on device",
        "link": f"https://example.com/en/{index}",
        "summary": "The new model keeps most of the reasoning quality at a tenth of the cost.",
        "published_at": NOW - timedelta(minutes=index),
        "source": "Example Weekly",
        "site_url": "https://example.com",
        "category": category,
        "language": "en",
    }


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

    def test_english_detection_skips_chinese_and_translates_english(self):
        self.assertTrue(is_english("A new model improves reasoning"))
        self.assertFalse(is_english("一个新的模型"))
        self.assertFalse(is_english("https://example.com"))

        class Response:
            def read(self):
                return '[[["一个新的模型", "A new model", null, null, 1]], null, "en"]'.encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch("scripts.wechat_digest.urlopen", return_value=Response()):
            self.assertEqual(translate_to_chinese("A new model"), "一个新的模型")

    def test_english_detection_follows_the_dominant_script_in_mixed_text(self):
        # A Chinese product name inside an English headline must not mask the English.
        self.assertTrue(is_english("Baidu's 文心一言 gets an upgrade"))
        self.assertTrue(is_english("NVIDIA 发布 RTX Spark: 128GB unified memory"))
        self.assertTrue(is_english("A new model improves reasoning"))
        # A Chinese sentence that quotes an English brand stays Chinese.
        self.assertFalse(is_english("OpenAI 发布 GPT-6，性能翻倍"))
        self.assertFalse(is_english("Anthropic 关于 Claude Opus 5.5 的官方说明：fast mode 结束预览"))
        self.assertFalse(is_english("Kimi K3 上线，支持 1M 上下文"))
        # Chinese punctuation, kana, and full-width forms count towards the Chinese share.
        self.assertFalse(is_english("「新しいモデル」で日本語のテキスト"))
        # Version numbers and bare dates carry no language.
        self.assertFalse(is_english("v1.2.3"))
        self.assertFalse(is_english("2026 年第 3 季度"))

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


class TranslationTests(unittest.TestCase):
    """English originals stay; the Chinese translation is appended beside them."""

    def test_english_item_keeps_the_original_and_gains_a_chinese_copy(self):
        item = make_english_item()
        with patch("scripts.wechat_digest.urlopen", side_effect=translate_response(ENGLISH_PAIRS)):
            translate_items([item], timeout=8, concurrency=4)

        self.assertEqual(item["title"], "OpenAI ships a smaller model that runs on device")
        self.assertEqual(item["summary"],
                         "The new model keeps most of the reasoning quality at a tenth of the cost.")
        self.assertEqual(item["title_zh"], "OpenAI 推出可在设备端运行的更小模型")
        self.assertEqual(item["summary_zh"], "新模型以十分之一的成本保留了大部分推理能力。")

    def test_chinese_item_is_left_alone(self):
        item = make_items(1)[0]
        item["title"] = "国产大模型开源周：三个新权重发布"
        item["summary"] = "本周国内团队连续开源三个模型，覆盖长上下文与推理。"
        with patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen) as mocked:
            translate_items([item], timeout=8, concurrency=4)

        self.assertNotIn("title_zh", item)
        self.assertNotIn("summary_zh", item)
        self.assertEqual(item["title"], "国产大模型开源周：三个新权重发布")
        mocked.assert_not_called()  # Chinese copy is never sent to the endpoint

    def test_mixed_headline_dominated_by_latin_letters_is_still_translated(self):
        item = make_english_item()
        item["title"] = "NVIDIA 发布 RTX Spark: 128GB unified memory"
        item["summary"] = ""
        mapping = {item["title"]: "NVIDIA 发布 RTX Spark：128GB 统一内存"}
        with patch("scripts.wechat_digest.urlopen", side_effect=translate_response(mapping)):
            translate_items([item], timeout=8, concurrency=4)

        self.assertEqual(item["title"], "NVIDIA 发布 RTX Spark: 128GB unified memory")
        self.assertEqual(item["title_zh"], "NVIDIA 发布 RTX Spark：128GB 统一内存")

    def test_unreachable_translation_endpoint_appends_nothing(self):
        item = make_english_item()
        with patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen):
            translate_items([item], timeout=8, concurrency=4)

        self.assertNotIn("title_zh", item)
        self.assertNotIn("summary_zh", item)
        self.assertEqual(item["title"], "OpenAI ships a smaller model that runs on device")

    def test_translation_that_echoes_the_original_is_not_appended(self):
        # A proper noun or an untranslated string must not be printed twice.
        item = make_english_item()
        item["title"] = "NVIDIA"
        item["summary"] = ""
        with patch("scripts.wechat_digest.urlopen", side_effect=translate_response({})):
            translate_items([item], timeout=8, concurrency=4)

        self.assertNotIn("title_zh", item)

    def test_only_english_fields_of_an_item_are_translated(self):
        item = make_english_item()
        item["summary"] = "本周国内团队连续开源三个模型。"
        with patch("scripts.wechat_digest.urlopen", side_effect=translate_response(ENGLISH_PAIRS)):
            translate_items([item], timeout=8, concurrency=4)

        self.assertIn("title_zh", item)
        self.assertNotIn("summary_zh", item)


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

    def test_footer_hides_generation_time_and_source_attribution(self):
        page = render(make_items(1), generated_at="2026-08-20 00:11")[0]
        self.assertNotIn("GENERATED", page)
        self.assertNotIn("SOURCE github.com/fuxiaoai/tidings-rss", page)

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
            self.assertIn(section_head("新闻"), page)  # section header repeated on every page
        self.assertIn("本期结束", pages[-1])

    def test_items_are_numbered_continuously_across_pages(self):
        pages = render(make_items(60, summary="摘要内容 " * 40), page_budget=20_000)
        numbers = [int(value) for page in pages for value in re.findall(r">\s*(\d{2})</span>\s*</td>", page)]
        self.assertEqual(numbers, list(range(1, 61)))

    def test_sections_are_grouped_and_ordered_by_catalog_category(self):
        items = make_items(4, category="News") + make_items(4, category="Artificial Intelligence")
        page = render(items)[0]
        self.assertLess(page.index(section_head("人工智能")), page.index(section_head("新闻")))
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

    def test_english_card_prints_the_original_then_the_appended_translation(self):
        item = make_english_item()
        item["title_zh"] = "OpenAI 推出可在设备端运行的更小模型"
        item["summary_zh"] = "新模型以十分之一的成本保留了大部分推理能力。"
        page = render([item])[0]

        # Both languages are present, original first, translation appended below.
        self.assertIn("OpenAI ships a smaller model that runs on device", page)
        self.assertIn(item["title_zh"], page)
        self.assertIn("The new model keeps most of the reasoning quality", page)
        self.assertIn(item["summary_zh"], page)
        self.assertLess(page.index("OpenAI ships a smaller model"), page.index(item["title_zh"]))
        self.assertLess(page.index("The new model keeps most of the reasoning"), page.index(item["summary_zh"]))
        # The appended copy is marked as a translation and hung on the neon accent.
        self.assertIn(TRANSLATION_TAG, page)
        self.assertIn(f"border-left:2px solid {NEON}", page)
        # The original title keeps the link; the appended translation is plain text.
        self.assertEqual(page.count('href="https://example.com/en/0"'), 1)

    def test_chinese_card_carries_no_translation_marker(self):
        page = render(make_items(3))[0]
        self.assertNotIn(TRANSLATION_TAG, page)
        self.assertNotIn(f"border-left:2px solid {NEON}", page)

    def test_translation_and_rendering_work_together(self):
        items = [make_english_item(index) for index in range(4)] + make_items(4)
        with patch("scripts.wechat_digest.urlopen", side_effect=translate_response(ENGLISH_PAIRS)):
            translate_items(items, timeout=8, concurrency=4)
        page = render(items)[0]

        self.assertIn("OpenAI ships a smaller model that runs on device", page)   # original kept
        self.assertIn("OpenAI 推出可在设备端运行的更小模型", page)                   # translation appended
        self.assertIn(TRANSLATION_TAG, page)
        self.assertNotIn("title_zh", page)

    def test_appended_translation_is_escaped_and_clamped(self):
        item = make_english_item()
        item["title_zh"] = '模型 <script>alert("x")</script> & 更多'
        item["summary_zh"] = "很长的中文摘要" * 40
        page = render([item])[0]

        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&amp;", page)
        rendered_summary = re.search(r"border-left:2px solid [^>]+>([^<]+)</div>", page).group(1)
        self.assertTrue(rendered_summary.endswith("…"))
        self.assertLessEqual(display_width(rendered_summary), 190)

    def test_bilingual_digest_still_paginates_inside_the_pushplus_limit(self):
        items = make_items(400, summary="A summary that needs translating " * 6)
        for item in items:
            item["title_zh"] = "需要翻译的中文标题，用于测试分页与字符预算。"
            item["summary_zh"] = "需要翻译的中文摘要，用于测试分页与字符预算。" * 4
        pages = render(items)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), PUSHPLUS_CONTENT_LIMIT)
        combined = "".join(pages)
        for item in items:
            self.assertEqual(combined.count(f'href="{item["link"]}"'), 1, item["link"])
            self.assertIn(item["title_zh"], combined)

    def test_empty_digest_still_renders_one_page(self):
        pages = render([])
        self.assertEqual(len(pages), 1)
        self.assertIn("没有新的文章", pages[0])

    def test_branding_title_subtitle_intro_and_author_are_rendered(self):
        page = render(make_items(4))[0]
        self.assertIn(DIGEST_TITLE, page)
        self.assertIn(DIGEST_SUBTITLE, page)
        self.assertIn(DIGEST_SCOPE, page)
        self.assertIn("全网境内外为你寻找蛛丝马迹", page)
        self.assertIn("Claude、ChatGPT、Gemini、Grok、Qwen 以及 Kimi", page)
        self.assertIn(AUTHOR_LINE, page)

    def test_scope_note_covers_all_categories_and_both_languages(self):
        self.assertIn("中文和英文", DIGEST_SCOPE)
        for topic in (
            "AI", "工程", "安全", "科技媒体", "周刊", "科研", "新闻",
            "产品", "商业", "博客", "社区", "文化", "视频", "播客",
        ):
            self.assertIn(topic, DIGEST_SCOPE)

    def test_intro_only_on_first_page_and_author_only_on_last(self):
        pages = render(make_items(220, summary="摘要内容 " * 25))
        self.assertGreater(len(pages), 1)
        self.assertIn(DIGEST_INTRO, pages[0])
        self.assertIn(DIGEST_SCOPE, pages[0])
        for page in pages[1:]:
            self.assertNotIn(DIGEST_INTRO, page)
            self.assertNotIn(DIGEST_SCOPE, page)
        self.assertIn(AUTHOR_LINE, pages[-1])
        for page in pages[:-1]:
            self.assertNotIn(AUTHOR_LINE, page)

    def test_digest_title_has_no_date_or_pushplus_branding(self):
        self.assertNotIn("pushplus", DIGEST_TITLE.casefold())
        self.assertNotRegex(DIGEST_TITLE, r"\d")
        self.assertEqual(DIGEST_TITLE, "章鱼 AI · 营销热点智能体")
        self.assertEqual(DIGEST_SUBTITLE, "每天三分钟！去噪音！留干货！")
        self.assertEqual(page_title(DIGEST_TITLE, 2, 3), "章鱼 AI · 营销热点智能体 2/3")

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
