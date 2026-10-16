import contextlib
import io
import json
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from scripts.wechat_digest import (
    AI_SUMMARY_TAG,
    AUTHOR_LINE,
    CHINA,
    DEEPL_FREE_ENDPOINT,
    DIGEST_INTRO,
    DIGEST_SCOPE,
    DIGEST_SUBTITLE,
    DIGEST_TITLE,
    EDITIONS,
    NEON,
    PUSHPLUS_CONTENT_LIMIT,
    PUSHPLUS_TITLE_LIMIT,
    SUMMARY_MAX_CHARS,
    SUMMARY_MAX_FAILURES,
    SUMMARY_SKIP_TOKEN,
    SUMMARY_SYSTEM_PROMPT,
    TRANSLATION_TAG,
    Provider,
    TranslationError,
    Translator,
    annotation_lines,
    clamp,
    clean_summary,
    config_warnings,
    configured_providers,
    dedupe,
    deepl_translate,
    display_width,
    google_chrome_translate,
    google_translate,
    edge_translate,
    llm_translate,
    main,
    mymemory_translate,
    normalize_llm_base_url,
    page_title,
    provider_notes,
    parse_datetime,
    parse_feed_payload,
    is_english,
    probe_markdown,
    probe_providers,
    render_pages,
    resolve_edition,
    resolve_llm_config,
    resolve_summary_config,
    shorten_text,
    strip_html,
    summarize_item,
    summarize_items,
    summary_notes,
    summary_report_line,
    summary_report_markdown,
    translate_items,
    translate_to_chinese,
    write_translation_report,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/wechat-digest.yml"
PROBE_WORKFLOW = ROOT / ".github/workflows/translate-probe.yml"
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
        with patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen), \
                patch("scripts.wechat_digest.time.sleep"):
            translate_items([item], timeout=8, concurrency=4)

        self.assertNotIn("title_zh", item)
        self.assertNotIn("summary_zh", item)
        self.assertEqual(item["title"], "OpenAI ships a smaller model that runs on device")

    def test_transient_failure_is_retried_then_succeeds(self):
        ok = translate_response({"A new model improves reasoning": "新模型提升了推理能力"})
        calls = []

        def flaky(request, **kwargs):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
            return ok(request, **kwargs)

        with patch("scripts.wechat_digest.urlopen", side_effect=flaky), \
                patch("scripts.wechat_digest.time.sleep") as sleep:
            result = translate_to_chinese("A new model improves reasoning")

        self.assertEqual(result, "新模型提升了推理能力")
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(1.0)

    def test_persistent_failure_gives_up_after_retries_and_keeps_original(self):
        translator = Translator([Provider("google", google_translate, "Google")], retries=3)
        with patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen) as mocked, \
                patch("scripts.wechat_digest.time.sleep"):
            result = translator.translate("A new model improves reasoning")

        self.assertEqual(result, "A new model improves reasoning")
        self.assertEqual(mocked.call_count, 3)

    def test_malformed_response_is_not_retried(self):
        class Response:
            def read(self):
                return b"<html>blocked</html>"

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        translator = Translator([Provider("google", google_translate, "Google")])
        with patch("scripts.wechat_digest.urlopen", return_value=Response()) as mocked, \
                patch("scripts.wechat_digest.time.sleep"):
            result = translator.translate("A new model improves reasoning")

        self.assertEqual(result, "A new model improves reasoning")
        self.assertEqual(mocked.call_count, 1)  # another attempt returns the same block page

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


class ProviderChainTests(unittest.TestCase):
    """One endpoint that answers keeps the whole issue bilingual."""

    @staticmethod
    def dead(name="dead"):
        def call(text, *, timeout):
            raise TranslationError("HTTPError: 429 Too Many Requests")

        return Provider(name, call, "Dead endpoint")

    @staticmethod
    def alive(answer="新模型提升了推理能力", name="alive"):
        def call(text, *, timeout):
            return answer

        return Provider(name, call, "Alive endpoint")

    def test_chain_falls_through_to_the_next_provider(self):
        asked = []

        def dead(text, *, timeout):
            asked.append("dead")
            raise TranslationError("HTTPError: 429")

        def alive(text, *, timeout):
            asked.append("alive")
            return "新模型提升了推理能力"

        translator = Translator([Provider("dead", dead, "Dead"), Provider("alive", alive, "Alive")], retries=1)
        self.assertEqual(translator.translate("A new model improves reasoning"), "新模型提升了推理能力")
        self.assertEqual(asked, ["dead", "alive"])
        summary = translator.summary()
        self.assertEqual(summary["active"], "alive")
        self.assertEqual(summary["translated"], 1)
        self.assertEqual(summary["providers"][0]["errors"], 1)
        self.assertEqual(summary["providers"][1]["ok"], 1)

    def test_provider_that_fails_twice_is_retired_for_the_rest_of_the_run(self):
        calls = []

        def dead(name):
            def call(text, *, timeout):
                calls.append(name)
                raise TranslationError("URLError: connection reset")

            return Provider(name, call, name)

        # Nothing answers yet, so both endpoints are asked once per string until
        # each has failed twice; after that the chain stops paying for their timeouts.
        translator = Translator([dead("first"), dead("second")], retries=1)
        for _ in range(3):
            translator.translate("A new model improves reasoning")

        self.assertEqual(calls, ["first", "second", "first", "second"])
        self.assertEqual(translator.summary()["retired"], ["first", "second"])
        self.assertEqual(translator.report_line(), "translation: translated=0/3 provider=none retired=first,second")

    def test_the_provider_that_answered_is_tried_first_next_time(self):
        order = []

        def first(text, *, timeout):
            order.append("first")
            raise TranslationError("HTTPError: 503")

        def second(text, *, timeout):
            order.append("second")
            return "译文"

        def third(text, *, timeout):
            order.append("third")
            return "另一种译文"

        translator = Translator(
            [Provider("first", first, "First"), Provider("second", second, "Second"), Provider("third", third, "Third")],
            retries=1,
        )
        translator.translate("A new model improves reasoning")
        translator.translate("Another English headline")
        self.assertEqual(order, ["first", "second", "second"])

    def test_echoed_original_is_not_a_translation_and_lets_the_next_provider_try(self):
        translator = Translator(
            [self.alive("A new model improves reasoning", "echo"), self.alive("新模型提升了推理能力", "translator")],
            retries=1,
        )
        self.assertEqual(translator.translate("A new model improves reasoning"), "新模型提升了推理能力")
        summary = translator.summary()
        self.assertEqual(summary["providers"][0]["empty"], 1)
        self.assertEqual(summary["active"], "translator")

    def test_retired_chain_returns_the_original_without_more_requests(self):
        translator = Translator([self.dead()], retries=1)
        translator.translate("A new model improves reasoning")
        translator.translate("A second English headline")
        self.assertEqual(translator.translate("A third English headline"), "A third English headline")

        self.assertEqual(translator.summary()["providers"][0]["attempts"], 2)  # retired after two
        self.assertEqual(translator.summary()["translated"], 0)

    def test_chinese_and_urlless_text_never_reaches_a_provider(self):
        asked = []

        def spy(text, *, timeout):
            asked.append(text)
            return ""

        translator = Translator([Provider("spy", spy, "Spy")])
        translator.translate("国产大模型开源周：三个新权重发布")
        translator.translate("https://example.com/article")
        translator.translate("v1.2.3")
        self.assertEqual(asked, [])
        self.assertEqual(translator.summary()["attempted"], 0)

    def test_concurrency_shares_the_chain_and_its_statistics(self):
        calls = []

        def slow_first(text, *, timeout):
            calls.append("first")
            raise TranslationError("HTTPError: 429")

        def second(text, *, timeout):
            calls.append("second")
            return f"译文：{text}"

        translator = Translator(
            [Provider("first", slow_first, "First"), Provider("second", second, "Second")], retries=1
        )
        items = [make_english_item(index) for index in range(4)]
        translate_items(items, timeout=8, concurrency=4, translator=translator)

        for item in items:
            self.assertEqual(item["title_zh"], f"译文：{item['title']}")
        summary = translator.summary()
        self.assertEqual(summary["translated"], len(items) * 2)  # title and summary each
        self.assertEqual(summary["active"], "second")

    def test_translator_report_names_the_provider_and_retired_endpoints(self):
        # Two attempts in one string are enough to retire the broken endpoint,
        # which is exactly what the log line and the run summary must say.
        translator = Translator([self.dead(), self.alive()], retries=2)
        translator.translate("A new model improves reasoning")
        line = translator.report_line()
        self.assertIn("translated=1/1", line)
        self.assertIn("provider=alive", line)
        self.assertIn("retired=dead", line)
        markdown = translator.report_markdown()
        self.assertIn("| `alive` | keyless | 1 | 1 | 0 | 0 | available |", markdown)
        self.assertIn("| `dead` | keyless | 2 | 0 | 0 | 2 | retired |", markdown)


class ProviderParsingTests(unittest.TestCase):
    """Each provider understands its own wire format and nothing else."""

    @staticmethod
    def responding(payload, status=200):
        """Answer every URL with `payload`, recording requests for inspection."""

        def _urlopen(request, **kwargs):
            _urlopen.requests.append(request)

            class Response:
                def read(self):
                    body = payload(request) if callable(payload) else payload
                    return body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        _urlopen.requests = []
        return _urlopen

    def test_google_joins_the_segments_of_a_long_string(self):
        payload = [[["新模型", "A new model", None, None, 1], ["提升了推理能力", " improves reasoning", None, None, 1]], None, "en"]
        with patch("scripts.wechat_digest.urlopen", side_effect=self.responding(payload)):
            self.assertEqual(google_translate("A new model improves reasoning", timeout=8), "新模型提升了推理能力")

    def test_google_chrome_accepts_both_edge_node_shapes(self):
        flat = [["你好，世界", "en"]]
        nested = [[["你好，世界", "Hello world", None, None, 1]], None, "en"]
        for payload in (flat, nested):
            with patch("scripts.wechat_digest.urlopen", side_effect=self.responding(payload)):
                self.assertEqual(google_chrome_translate("Hello world", timeout=8), "你好，世界")

    def test_edge_posts_a_string_array_and_unescapes_markup(self):
        seen = self.responding([{"translations": [{"text": "5 &lt; 6 的中文说明"}]}])
        with patch("scripts.wechat_digest.urlopen", side_effect=seen):
            result = edge_translate("5 < 6 in Chinese", timeout=8)

        self.assertEqual(result, "5 < 6 的中文说明")
        request = seen.requests[0]
        self.assertEqual(json.loads(request.data.decode("utf-8")), ["5 &lt; 6 in Chinese"])
        self.assertIn("to=zh-Hans", request.full_url)
        self.assertIn("isEnterpriseClient=false", request.full_url)

    def test_edge_leaves_plain_text_unescaped_on_the_wire(self):
        seen = self.responding([{"translations": [{"text": "你好"}]}])
        with patch("scripts.wechat_digest.urlopen", side_effect=seen):
            edge_translate("Hello", timeout=8)
        self.assertEqual(json.loads(seen.requests[0].data.decode("utf-8")), ["Hello"])

    def test_edge_shape_that_is_not_a_translation_fails_loudly(self):
        with patch("scripts.wechat_digest.urlopen", side_effect=self.responding({"error": "blocked"})):
            with self.assertRaises(TranslationError) as caught:
                edge_translate("Hello", timeout=8)
        self.assertFalse(caught.exception.retryable)

    def test_mymemory_turns_a_quota_warning_into_a_retryable_error(self):
        payload = {"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"},
                   "responseStatus": 200}
        with patch("scripts.wechat_digest.urlopen", side_effect=self.responding(payload)):
            with self.assertRaises(TranslationError) as caught:
                mymemory_translate("Hello world", timeout=8)
        self.assertTrue(caught.exception.retryable)

    def test_mymemory_unescapes_entities_and_sends_the_email_when_configured(self):
        payload = {"responseData": {"translatedText": "你好&amp;世界"}, "responseStatus": 200}
        seen = self.responding(payload)
        with patch.dict("os.environ", {"MYMEMORY_EMAIL": "me@example.com"}), \
                patch("scripts.wechat_digest.urlopen", side_effect=seen):
            self.assertEqual(mymemory_translate("Hello & world", timeout=8), "你好&世界")
        self.assertIn("de=me%40example.com", seen.requests[0].full_url)

    def test_deepl_posts_form_fields_with_the_auth_header(self):
        seen = self.responding({"translations": [{"detected_source_language": "EN", "text": "新模型提升了推理能力"}]})
        with patch("scripts.wechat_digest.urlopen", side_effect=seen):
            result = deepl_translate(
                "A new model improves reasoning",
                timeout=8,
                api_key="abc:fx",
                endpoint=DEEPL_FREE_ENDPOINT,
            )

        self.assertEqual(result, "新模型提升了推理能力")
        request = seen.requests[0]
        self.assertEqual(request.full_url, "https://api-free.deepl.com/v2/translate")
        self.assertEqual(request.get_header("Authorization"), "DeepL-Auth-Key abc:fx")
        body = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(body["target_lang"], ["ZH"])
        self.assertEqual(body["source_lang"], ["EN"])
        self.assertEqual(body["text"], ["A new model improves reasoning"])

    def test_llm_uses_an_openai_compatible_chat_endpoint(self):
        payload = {"choices": [{"message": {"content": "  “新模型提升了推理能力”\n"}}]}
        seen = self.responding(payload)
        with patch("scripts.wechat_digest.urlopen", side_effect=seen):
            result = llm_translate(
                "A new model improves reasoning",
                timeout=8,
                api_key="sk-test",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-flash",
            )

        self.assertEqual(result, "新模型提升了推理能力")
        request = seen.requests[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-test")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["messages"][-1]["content"], "A new model improves reasoning")

    def test_llm_sends_the_extra_fields_a_preset_asks_for(self):
        payload = {"choices": [{"message": {"content": "新模型提升了推理能力"}}]}
        seen = self.responding(payload)
        with patch("scripts.wechat_digest.urlopen", side_effect=seen):
            llm_translate(
                "A new model improves reasoning",
                timeout=8,
                api_key="sk-test",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-flash",
                extra={"thinking": {"type": "disabled"}},
            )

        body = json.loads(seen.requests[0].data.decode("utf-8"))
        # DeepSeek thinks by default; a translation must not pay for that.
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["temperature"], 0)

    def test_llm_without_a_choice_field_is_a_non_retryable_error(self):
        with patch("scripts.wechat_digest.urlopen", side_effect=self.responding({"error": {"message": "no key"}})):
            with self.assertRaises(TranslationError) as caught:
                llm_translate("Hello", timeout=8, api_key="k", base_url="https://example.com/v1", model="m")
        self.assertFalse(caught.exception.retryable)


class TranslatorConfigTests(unittest.TestCase):
    def test_keyless_chain_is_the_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                [provider.name for provider in configured_providers()],
                ["google", "google-chrome", "edge", "mymemory"],
            )

    def test_configured_keys_are_tried_before_the_keyless_endpoints(self):
        env = {
            "DEEPL_API_KEY": "abc:fx",
            "TRANSLATE_LLM_API_KEY": "sk-test",
            "TRANSLATE_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "TRANSLATE_LLM_MODEL": "deepseek-chat",
        }
        with patch.dict("os.environ", env, clear=True):
            providers = configured_providers()
        self.assertEqual([provider.name for provider in providers[:2]], ["deepl", "llm"])
        self.assertEqual([provider.keyless for provider in providers[:2]], [False, False])
        self.assertIn("deepseek-chat", providers[1].label)

    def test_free_deepl_keys_use_the_free_host_and_pro_keys_do_not(self):
        with patch.dict("os.environ", {"DEEPL_API_KEY": "abc:fx"}, clear=True):
            provider = configured_providers()[0]
            self.assertEqual(provider.call.keywords["endpoint"], DEEPL_FREE_ENDPOINT)
        with patch.dict("os.environ", {"DEEPL_API_KEY": "abc"}, clear=True):
            provider = configured_providers()[0]
            self.assertEqual(provider.call.keywords["endpoint"], "https://api.deepl.com/v2/translate")

    def test_translate_providers_selects_and_reorders_the_chain(self):
        with patch.dict("os.environ", {}, clear=True):
            providers = configured_providers(names=["edge", "google"])
        self.assertEqual([provider.name for provider in providers], ["edge", "google"])

    def test_an_unknown_translator_name_is_reported_instead_of_silently_dropped(self):
        with self.assertRaises(SystemExit) as caught:
            configured_providers(names=["googel"])
        self.assertIn("googel", str(caught.exception))

    def test_a_bare_deepseek_key_supplies_its_own_endpoint_and_model(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
            providers = configured_providers()

        llm = providers[0]
        self.assertEqual(llm.name, "llm")
        self.assertFalse(llm.keyless)
        self.assertEqual(llm.call.keywords["api_key"], "sk-deepseek")
        self.assertEqual(llm.call.keywords["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(llm.call.keywords["model"], "deepseek-flash")
        # Thinking mode is DeepSeek's default and a translation does not want it.
        self.assertEqual(llm.call.keywords["extra"], {"thinking": {"type": "disabled"}})
        self.assertIn("deepseek-flash", llm.label)
        self.assertIn("api.deepseek.com", llm.label)

    def test_a_model_override_wins_over_the_preset(self):
        env = {"DEEPSEEK_API_KEY": "sk-deepseek", "TRANSLATE_LLM_MODEL": "deepseek-v4-pro"}
        with patch.dict("os.environ", env, clear=True):
            providers = configured_providers()

        self.assertEqual(providers[0].call.keywords["model"], "deepseek-v4-pro")

    def test_a_named_preset_uses_its_own_key_variable(self):
        env = {"TRANSLATE_LLM_PROVIDER": "moonshot", "MOONSHOT_API_KEY": "sk-moon"}
        with patch.dict("os.environ", env, clear=True):
            providers = configured_providers()

        self.assertEqual(providers[0].call.keywords["api_key"], "sk-moon")
        self.assertEqual(providers[0].call.keywords["base_url"], "https://api.moonshot.cn/v1")
        self.assertEqual(providers[0].call.keywords["model"], "moonshot-v1-8k")

    def test_an_unknown_llm_provider_is_reported(self):
        with patch.dict("os.environ", {"TRANSLATE_LLM_PROVIDER": "deepsek"}, clear=True):
            with self.assertRaises(SystemExit) as caught:
                configured_providers()
        self.assertIn("deepsek", str(caught.exception))

    def test_a_preset_without_its_key_does_not_fail_the_run(self):
        with patch.dict("os.environ", {"TRANSLATE_LLM_PROVIDER": "deepseek"}, clear=True):
            providers = configured_providers()
        self.assertEqual([provider.name for provider in providers], ["google", "google-chrome", "edge", "mymemory"])

    def test_an_explicit_key_pointing_at_deepseek_still_gets_the_preset_fields(self):
        env = {"TRANSLATE_LLM_API_KEY": "sk-test", "TRANSLATE_LLM_BASE_URL": "https://api.deepseek.com"}
        with patch.dict("os.environ", env, clear=True):
            providers = configured_providers()

        # The bare host gains the /v1 root, and the endpoint identifies the preset.
        self.assertEqual(providers[0].call.keywords["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(providers[0].call.keywords["model"], "deepseek-flash")
        self.assertEqual(providers[0].call.keywords["extra"], {"thinking": {"type": "disabled"}})

    def test_base_urls_are_accepted_the_way_people_paste_them(self):
        self.assertEqual(normalize_llm_base_url("https://api.deepseek.com"), "https://api.deepseek.com/v1")
        self.assertEqual(normalize_llm_base_url("https://api.deepseek.com/v1/"), "https://api.deepseek.com/v1")
        self.assertEqual(
            normalize_llm_base_url("https://api.deepseek.com/v1/chat/completions"),
            "https://api.deepseek.com/v1",
        )
        self.assertEqual(normalize_llm_base_url("https://api.openai.com"), "https://api.openai.com/v1")
        # An endpoint the presets do not know is used exactly as typed.
        self.assertEqual(normalize_llm_base_url("https://llm.internal:4000/root/"), "https://llm.internal:4000/root")
        self.assertEqual(normalize_llm_base_url(""), "")

    def test_notes_name_the_key_the_endpoint_and_the_model(self):
        env = {"DEEPSEEK_API_KEY": "sk-deepseek"}
        with patch.dict("os.environ", env, clear=True):
            notes = "\n".join(provider_notes())

        self.assertIn("`DEEPSEEK_API_KEY`", notes)
        self.assertIn("https://api.deepseek.com/v1", notes)
        self.assertIn("deepseek-flash", notes)
        self.assertIn("thinking mode is disabled", notes)
        self.assertNotIn("sk-deepseek", notes)  # the key itself is never printed

    def test_notes_explain_how_to_turn_the_llm_provider_on(self):
        with patch.dict("os.environ", {}, clear=True):
            notes = "\n".join(provider_notes())
        self.assertIn("DEEPSEEK_API_KEY", notes)
        self.assertIn("deepl", notes)

    def test_a_retired_deepseek_model_is_warned_about(self):
        env = {
            "TRANSLATE_LLM_API_KEY": "sk-test",
            "TRANSLATE_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "TRANSLATE_LLM_MODEL": "deepseek-chat",
        }
        with patch.dict("os.environ", env, clear=True):
            warnings = config_warnings()

        self.assertEqual(len(warnings), 1)
        self.assertIn("deepseek-chat", warnings[0])
        self.assertIn("deepseek-flash", warnings[0])

    def test_a_preset_with_no_key_is_warned_about_but_still_renders(self):
        with patch.dict("os.environ", {"TRANSLATE_LLM_PROVIDER": "deepseek"}, clear=True):
            warnings = config_warnings()
        self.assertIn("DEEPSEEK_API_KEY", warnings[0])
        self.assertIn("deepseek", warnings[0])

    def test_a_key_without_an_endpoint_says_which_default_it_is_using(self):
        with patch.dict("os.environ", {"TRANSLATE_LLM_API_KEY": "sk-test"}, clear=True):
            notes = "\n".join(provider_notes())
            warnings = config_warnings()

        self.assertIn("gpt-4o-mini", notes)
        self.assertIn("TRANSLATE_LLM_BASE_URL", notes)
        self.assertEqual(warnings, [])


class TranslationProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_probe_reports_every_provider_even_the_broken_ones(self):
        def dead(text, *, timeout):
            raise TranslationError("HTTPError: 451 Unavailable For Legal Reasons")

        rows = probe_providers([Provider("dead", dead, "Dead"), Provider("live", lambda text, *, timeout: "译文", "Live")])

        self.assertEqual([row["status"] for row in rows], ["error", "error", "ok", "ok"])
        markdown = probe_markdown(rows, generated_at="2026-08-19 18:00")
        self.assertIn("### Translation probe · 2026-08-19 18:00", markdown)
        self.assertIn("HTTPError: 451", markdown)
        self.assertIn("Providers that answered: live", markdown)

    def test_probe_reports_when_nothing_answers(self):
        rows = probe_providers([Provider("dead", lambda text, *, timeout: (_ for _ in ()).throw(URLError("x")), "Dead")],
                               texts=("Hello world",))
        self.assertIn("**none**", probe_markdown(rows, generated_at="now"))
        self.assertEqual(rows[0]["status"], "error")

    def test_probe_report_says_which_key_and_endpoint_the_run_used(self):
        rows = probe_providers(
            [Provider("llm", lambda text, *, timeout: "译文", "LLM (deepseek-flash @ api.deepseek.com)")],
            texts=("Hello world",),
        )
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
            markdown = probe_markdown(rows, generated_at="now", notes=provider_notes())

        self.assertIn("### Configuration", markdown)
        self.assertIn("`DEEPSEEK_API_KEY`", markdown)
        self.assertIn("deepseek-flash", markdown)
        self.assertNotIn("sk-deepseek", markdown)

    def test_probe_mode_writes_a_report_and_never_calls_pushplus(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True), \
                patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen), \
                patch("scripts.wechat_digest.time.sleep"), \
                patch("scripts.wechat_digest.push_page") as pushed, \
                contextlib.redirect_stdout(io.StringIO()):
            code = main(["--probe-translators", "--translators", "google,edge", "--timeout", "1",
                         "--out", str(self.tmp)])

        pushed.assert_not_called()

        self.assertEqual(code, 0)
        report = json.loads((self.tmp / "translation-report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["provider"] for row in report["rows"]], ["google", "google", "edge", "edge"])
        self.assertEqual({row["status"] for row in report["rows"]}, {"error"})
        self.assertEqual(report["warnings"], [])
        markdown = (self.tmp / "translation-report.md").read_text(encoding="utf-8")
        self.assertIn("Providers that answered: **none**", markdown)
        # The probe answers the question a missing provider raises: which key did
        # the run actually find, and which endpoint and model does it point at?
        self.assertIn("### Configuration", markdown)
        self.assertIn("`DEEPSEEK_API_KEY`", markdown)
        self.assertIn("deepseek-flash", markdown)
        self.assertNotIn("sk-deepseek", markdown)

    def test_a_failed_digest_run_annotates_itself(self):
        translator = Translator([self.dead_provider("google"), self.dead_provider("edge")], retries=1)
        translator.translate("A new model improves reasoning")
        lines = annotation_lines(translator.summary())

        self.assertEqual([level for level, _ in lines], ["notice", "warning"])
        self.assertIn("translated=0/1 provider=none", lines[0][1])
        self.assertIn("every translation endpoint failed", lines[1][1])
        self.assertIn("google: HTTPError: 451", lines[1][1])
        self.assertIn("edge: HTTPError: 451", lines[1][1])

    def test_a_working_run_reports_the_provider_and_the_retired_endpoints(self):
        translator = Translator([self.dead_provider("google"), Provider("edge", lambda text, *, timeout: "译文", "Edge")],
                                retries=2)
        translator.translate("A new model improves reasoning")
        lines = annotation_lines(translator.summary())

        self.assertEqual([level for level, _ in lines], ["notice", "warning"])
        self.assertIn("translated=1/1 provider=edge", lines[0][1])
        self.assertIn("retired translation endpoints | google: HTTPError: 451", lines[1][1])

    def test_a_chinese_only_issue_produces_no_annotation(self):
        translator = Translator([Provider("edge", lambda text, *, timeout: "译文", "Edge")])
        translator.translate("国产大模型开源周：三个新权重发布")
        self.assertEqual(annotation_lines(translator.summary()), [])

    def test_a_broken_configuration_is_annotated_even_with_nothing_to_translate(self):
        lines = annotation_lines({
            "attempted": 0,
            "warnings": ["TRANSLATE_LLM_MODEL=deepseek-chat was retired on 2026-07-24; use deepseek-flash"],
        })

        self.assertEqual([level for level, _ in lines], ["warning"])
        self.assertIn("deepseek-chat", lines[0][1])

    def test_probe_annotations_name_every_provider(self):
        rows = probe_providers(
            [Provider("edge", lambda text, *, timeout: "译文", "Edge"),
             Provider("google", lambda text, *, timeout: (_ for _ in ()).throw(URLError("blocked")), "Google")],
            texts=("Hello world",),
        )
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
            lines = annotation_lines({"generated_at": "now", "rows": rows})

        self.assertEqual([level for level, _ in lines], ["notice", "notice"])
        self.assertIn("translation probe: edge=ok, google=failed", lines[0][1])
        # The second line is the configuration the probe asked about, key-free.
        self.assertIn("config: llm=deepseek-flash @ api.deepseek.com via DEEPSEEK_API_KEY", lines[1][1])
        self.assertNotIn("sk-deepseek", " ".join(message for _, message in lines))

    def test_annotations_are_printed_only_inside_github_actions(self):
        translator = Translator([Provider("edge", lambda text, *, timeout: "译文", "Edge")], retries=1)
        translator.translate("A new model improves reasoning")
        payload = translator.summary()

        silent = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(silent):
            write_translation_report(None, markdown="", payload=payload)
        self.assertEqual(silent.getvalue(), "")

        spoken = io.StringIO()
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True), \
                contextlib.redirect_stdout(spoken):
            write_translation_report(None, markdown="", payload=payload)
        self.assertIn("::notice::translation: translated=1/1 provider=edge", spoken.getvalue())

    @staticmethod
    def dead_provider(name):
        def call(text, *, timeout):
            raise TranslationError("HTTPError: 451 Unavailable For Legal Reasons")

        return Provider(name, call, name)

    def test_github_step_summary_receives_the_report(self):
        summary = self.tmp / "summary.md"
        with patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary)}):
            write_translation_report(None, markdown="### Translation probe", payload={})

        self.assertIn("### Translation probe", summary.read_text(encoding="utf-8"))


def make_summarizable_item(index=0, *, summary=None, title=None, language="en"):
    return {
        "title": title or f"OpenAI ships a smaller model that runs on device #{index}",
        "link": f"https://example.com/ai/{index}",
        "summary": ARTICLE_BODY if summary is None else summary,
        "published_at": NOW - timedelta(minutes=index),
        "source": "Example Weekly",
        "site_url": "https://example.com",
        "category": "Artificial Intelligence",
        "language": language,
    }


ARTICLE_BODY = (
    "The new model keeps most of the reasoning quality at a tenth of the cost, and it now runs "
    "on a phone, which changes what a small team can ship without renting a server."
)
CHINESE_BODY = (
    "本周国内团队连续开源三个模型，覆盖长上下文与推理场景，权重、训练细节和评测脚本一并放出，"
    "社区当天就跑出了可复现的结果。"
)
HUMAN_LINE = "价格砍到十分之一，推理几乎没掉，手机上就能跑，小团队可以先省下服务器钱。"
AI_FLAVOURED_LINE = (
    "本文深入探讨了该模型的架构与成本，首先介绍了注意力机制的改动，其次分析了推理开销，"
    "最后展望了生态。值得注意的是，其前景广阔。"
)
SUMMARY_CONFIG = {
    "key": "sk-test",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "body": {"thinking": {"type": "disabled"}},
}


class ChatEndpoint:
    """A fake OpenAI-compatible endpoint answering from prompt substring -> text.

    Anything it has no answer for comes back as the skip token, which is what a
    model does when it decides an article cannot be summarised honestly.
    """

    def __init__(self, answers, default=SUMMARY_SKIP_TOKEN):
        self.answers = answers
        self.default = default
        self.requests = []

    def __call__(self, request, **kwargs):
        self.requests.append(request)
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["messages"][-1]["content"]
        answer = next((text for needle, text in self.answers.items() if needle in prompt), self.default)
        payload = {"choices": [{"message": {"content": answer}}]}

        class Response:
            def read(self):
                return json.dumps(payload).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    @property
    def prompts(self):
        return [json.loads(request.data.decode("utf-8"))["messages"][-1]["content"] for request in self.requests]


def summarize(items, answers, *, default=SUMMARY_SKIP_TOKEN, concurrency=2, **kwargs):
    """Run `summarize_items` against a fake endpoint and return (endpoint, stats)."""
    endpoint = ChatEndpoint(answers, default=default)
    with patch("scripts.wechat_digest.urlopen", side_effect=endpoint), \
            patch("scripts.wechat_digest.time.sleep"):
        stats = summarize_items(items, config=SUMMARY_CONFIG, timeout=8, concurrency=concurrency, **kwargs)
    return endpoint, stats


class AiSummaryTests(unittest.TestCase):
    """One human line per article — and nothing at all when there is nothing to say."""

    def test_a_real_body_gains_exactly_one_ai_line(self):
        items = [make_summarizable_item()]
        endpoint, stats = summarize(items, {ARTICLE_BODY[:30]: HUMAN_LINE})

        self.assertEqual(items[0]["ai_summary"], HUMAN_LINE)
        self.assertEqual(stats["written"], 1)
        self.assertEqual(stats["candidates"], 1)
        # The source's own summary is never overwritten by the AI line.
        self.assertEqual(items[0]["summary"], ARTICLE_BODY)
        self.assertEqual(len(endpoint.requests), 1)
        self.assertIn("/chat/completions", endpoint.requests[0].full_url)

    def test_chinese_body_is_summarised_too(self):
        items = [make_summarizable_item(summary=CHINESE_BODY, language="zh")]
        _, stats = summarize(items, {CHINESE_BODY[:20]: "三个模型一次放出，权重和评测脚本都在，社区当天就复现了。"})

        self.assertEqual(stats["written"], 1)
        self.assertIn("三个模型", items[0]["ai_summary"])

    def test_too_little_source_text_never_reaches_the_endpoint(self):
        items = [make_summarizable_item(summary="一句话")]
        endpoint, stats = summarize(items, {})

        self.assertEqual(endpoint.requests, [])  # 内容太稀少，不总结
        self.assertNotIn("ai_summary", items[0])
        self.assertEqual(stats["thin"], 1)
        self.assertEqual(stats["candidates"], 0)

    def test_a_headline_echoed_as_the_body_is_not_summarised(self):
        headline = "国产大模型开源周：三个新权重同时发布，覆盖长上下文与推理"
        items = [make_summarizable_item(title=headline, summary=headline, language="zh")]
        endpoint, stats = summarize(items, {})

        self.assertEqual(endpoint.requests, [])
        self.assertEqual(stats["thin"], 1)

    def test_a_click_teaser_is_not_summarised(self):
        items = [make_summarizable_item(summary="阅读全文请扫码加入社群，更多内容点击查看", language="zh")]
        endpoint, stats = summarize(items, {})

        self.assertEqual(endpoint.requests, [])
        self.assertEqual(stats["thin"], 1)

    def test_the_model_can_decline_an_article(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {})

        self.assertNotIn("ai_summary", items[0])
        self.assertEqual(stats["declined"], 1)
        self.assertEqual(stats["written"], 0)

    def test_stock_ai_phrasing_is_dropped_rather_than_printed(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {ARTICLE_BODY[:30]: AI_FLAVOURED_LINE})

        # 去 AI 味：宁可留空，也不印一段机器腔。
        self.assertNotIn("ai_summary", items[0])
        self.assertEqual(stats["flavored"], 1)
        self.assertEqual(stats["written"], 0)

    def test_the_line_is_cut_to_the_character_limit(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {ARTICLE_BODY[:30]: "这一行故意写得非常长，用来测试脚本是否会把超出一百字的模型回答裁到上限之内。" * 4})

        self.assertLessEqual(len(items[0]["ai_summary"]), SUMMARY_MAX_CHARS)
        self.assertEqual(stats["written"], 1)

    def test_a_limit_set_on_the_command_line_is_honoured(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {ARTICLE_BODY[:30]: "一二三四五六七八九十一二三四五六七八九十一二三四五。"}, max_chars=20)

        self.assertLessEqual(len(items[0]["ai_summary"]), 20)
        self.assertEqual(stats["max_chars"], 20)

    def test_clean_summary_strips_quotes_lead_ins_and_markdown(self):
        self.assertEqual(clean_summary("“价格砍到十分之一。”")[0], "价格砍到十分之一。")
        self.assertEqual(clean_summary("AI总结：价格砍到十分之一。")[0], "价格砍到十分之一。")
        self.assertEqual(clean_summary("**价格砍到十分之一。**")[0], "价格砍到十分之一。")
        self.assertEqual(clean_summary("  \n 要点：价格砍到十分之一。 ")[0], "价格砍到十分之一。")

    def test_clean_summary_reports_why_a_line_was_dropped(self):
        self.assertEqual(clean_summary(SUMMARY_SKIP_TOKEN)[1], "declined")
        self.assertEqual(clean_summary("SKIP.")[1], "declined")
        self.assertEqual(clean_summary("")[1], "empty")
        self.assertEqual(clean_summary("太短")[1], "empty")
        self.assertEqual(clean_summary(AI_FLAVOURED_LINE)[1], "flavored")
        self.assertEqual(clean_summary(HUMAN_LINE)[1], "written")

    def test_shorten_text_prefers_a_sentence_boundary_and_stays_in_the_limit(self):
        self.assertLessEqual(len(shorten_text("一二三四五六七八九十" * 20, 100)), 100)
        self.assertTrue(shorten_text("第一句说成本下降。第二句说质量没掉。第三句还没说完", 20).endswith("。"))
        self.assertEqual(shorten_text("短句", 100), "短句")

    def test_a_dead_endpoint_costs_three_failures_not_one_per_card(self):
        items = [make_summarizable_item(index) for index in range(10)]
        endpoint = ChatEndpoint({})
        with patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen), \
                patch("scripts.wechat_digest.time.sleep"):
            stats = summarize_items(items, config=SUMMARY_CONFIG, timeout=1, concurrency=4)

        self.assertEqual(stats["failed"], SUMMARY_MAX_FAILURES)
        self.assertLess(stats["failed"] + stats["written"], len(items))
        self.assertEqual(stats["last_error"].split(":")[0], "TranslationError")

    def test_a_transient_failure_is_retried_once(self):
        item = make_summarizable_item()
        endpoint = ChatEndpoint({ARTICLE_BODY[:30]: HUMAN_LINE})
        calls = []

        def flaky(request, **kwargs):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
            return endpoint(request, **kwargs)

        with patch("scripts.wechat_digest.urlopen", side_effect=flaky), \
                patch("scripts.wechat_digest.time.sleep") as sleep:
            line, outcome = summarize_item(item, SUMMARY_CONFIG, timeout=8)

        self.assertEqual(outcome, "written")
        self.assertEqual(line, HUMAN_LINE)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(1.0)

    def test_without_a_configured_endpoint_nothing_is_written_or_asked(self):
        items = [make_summarizable_item()]
        with patch.dict("os.environ", {}, clear=True), \
                patch("scripts.wechat_digest.urlopen", side_effect=offline_urlopen) as mocked:
            stats = summarize_items(items, timeout=8)

        mocked.assert_not_called()
        self.assertFalse(stats["configured"])
        self.assertEqual(stats["written"], 0)
        self.assertIn("off", summary_report_line(stats))

    def test_the_summary_reuses_the_translation_endpoint(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-deepseek"}, clear=True):
            config = resolve_summary_config()

        self.assertEqual(config["model"], "deepseek-flash")
        self.assertEqual(config["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(config["source"], "DEEPSEEK_API_KEY")

    def test_a_model_override_applies_to_the_summary_only(self):
        env = {"DEEPSEEK_API_KEY": "sk-deepseek", "SUMMARY_LLM_MODEL": "deepseek-v4-pro"}
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(resolve_summary_config()["model"], "deepseek-v4-pro")
            self.assertEqual(resolve_llm_config()["model"], "deepseek-flash")

    def test_the_prompt_sets_a_limit_a_skip_token_and_the_banned_phrases(self):
        self.assertIn(str(SUMMARY_MAX_CHARS), SUMMARY_SYSTEM_PROMPT)
        self.assertIn(SUMMARY_SKIP_TOKEN, SUMMARY_SYSTEM_PROMPT)
        for banned in ("综上所述", "赋能", "深度解析", "首先", "值得注意的是", "一文读懂"):
            self.assertIn(banned, SUMMARY_SYSTEM_PROMPT)
        self.assertIn("像人写给同事的口信", SUMMARY_SYSTEM_PROMPT)

    def test_the_prompt_carries_the_source_the_title_and_the_body(self):
        items = [make_summarizable_item()]
        endpoint, _ = summarize(items, {})
        prompt = endpoint.prompts[0]

        self.assertIn("Example Weekly", prompt)
        self.assertIn(items[0]["title"], prompt)
        self.assertIn(ARTICLE_BODY, prompt)
        self.assertIn(str(SUMMARY_MAX_CHARS), prompt)

    def test_a_long_body_is_trimmed_before_it_reaches_the_endpoint(self):
        item = make_summarizable_item(summary=ARTICLE_BODY * 100)
        endpoint, _ = summarize([item], {})

        prompt = endpoint.prompts[0]
        self.assertLess(len(prompt), len(ARTICLE_BODY) * 100)
        self.assertIn(ARTICLE_BODY[:100], prompt)

    def test_the_card_prints_the_ai_line_underneath_the_source_summary(self):
        item = make_summarizable_item()
        item["ai_summary"] = HUMAN_LINE
        page = render([item])[0]

        self.assertIn(HUMAN_LINE, page)
        self.assertIn(f">{AI_SUMMARY_TAG}</span>", page)
        self.assertIn(f"border-left:2px solid {NEON}", page)
        # Source first, the machine's take last: a reader can tell them apart.
        self.assertLess(page.index(ARTICLE_BODY[:40]), page.index(HUMAN_LINE))
        self.assertLess(page.index(HUMAN_LINE), page.index("Example Weekly"))

    def test_a_card_without_an_ai_line_shows_no_ai_block(self):
        page = render([make_summarizable_item()])[0]

        self.assertNotIn(f">{AI_SUMMARY_TAG}</span>", page)
        self.assertNotIn(f"border-left:2px solid {NEON}", page)

    def test_the_ai_line_is_escaped_and_clamped(self):
        item = make_summarizable_item()
        item["ai_summary"] = '<script>alert("x")</script> & ' + "很长的一行中文摘要" * 30
        page = render([item])[0]

        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&amp;", page)

    def test_an_issue_full_of_ai_lines_still_fits_the_pushplus_limit(self):
        items = make_items(400, summary=ARTICLE_BODY)
        for item in items:
            item["ai_summary"] = HUMAN_LINE
        pages = render(items)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), PUSHPLUS_CONTENT_LIMIT)
        combined = "".join(pages)
        for item in items:
            self.assertEqual(combined.count(f'href="{item["link"]}"'), 1, item["link"])

    def test_the_report_says_how_many_lines_were_written_and_why(self):
        items = [make_summarizable_item(0), make_summarizable_item(1, summary="一句话")]
        _, stats = summarize(items, {ARTICLE_BODY[:30]: HUMAN_LINE})
        markdown = summary_report_markdown(stats)

        self.assertIn("### AI summary", markdown)
        self.assertIn("**1**", markdown)
        self.assertIn("deepseek-flash", markdown)
        self.assertIn("stock AI phrasing", markdown)
        self.assertIn("written=1/1", summary_report_line(stats))

    def test_the_notes_name_the_model_the_endpoint_and_the_skip_rule(self):
        _, stats = summarize([make_summarizable_item()], {ARTICLE_BODY[:30]: HUMAN_LINE})
        note = summary_notes(stats)[0]

        self.assertIn("deepseek-flash", note)
        self.assertIn("api.deepseek.com", note)
        self.assertIn(SUMMARY_SKIP_TOKEN, note)

    def test_an_unconfigured_run_says_how_to_turn_the_feature_on(self):
        with patch.dict("os.environ", {}, clear=True):
            stats = summarize_items([make_summarizable_item()], timeout=8)

        self.assertIn("DEEPSEEK_API_KEY", summary_notes(stats)[0])
        self.assertIn("Off", summary_report_markdown(stats))

    def test_a_run_that_wrote_nothing_annotates_a_warning(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {})
        lines = annotation_lines({"attempted": 0, "ai_summary": stats})

        self.assertIn(("notice", summary_report_line(stats)), lines)
        self.assertEqual([level for level, _ in lines], ["notice", "warning"])
        self.assertIn("wrote no line", lines[1][1])
        self.assertIn("declined=1", lines[1][1])

    def test_a_run_that_wrote_something_reports_it_without_a_warning(self):
        items = [make_summarizable_item()]
        _, stats = summarize(items, {ARTICLE_BODY[:30]: HUMAN_LINE})
        lines = annotation_lines({"attempted": 0, "ai_summary": stats})

        self.assertEqual([level for level, _ in lines], ["notice"])
        self.assertIn("ai-summary: written=1/1", lines[0][1])

    def test_an_unconfigured_run_is_annotated_once_and_only_as_a_notice(self):
        with patch.dict("os.environ", {}, clear=True):
            stats = summarize_items([make_summarizable_item()], timeout=8)
        lines = annotation_lines({"attempted": 0, "ai_summary": stats})

        self.assertEqual([level for level, _ in lines], ["notice"])
        self.assertIn("off", lines[0][1])


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
        self.assertEqual(DIGEST_TITLE, "章鱼 AI · 营销热点助手")
        self.assertEqual(DIGEST_SUBTITLE, "每天三分钟！去噪音！留干货！")
        self.assertEqual(page_title(DIGEST_TITLE, 2, 3), "章鱼 AI · 营销热点助手 2/3")

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

    def test_workflow_offers_translation_keys_without_requiring_them(self):
        for name in (
            "DEEPL_API_KEY",
            "TRANSLATE_LLM_API_KEY",
            "TRANSLATE_LLM_PROVIDER",
            "TRANSLATE_LLM_BASE_URL",
            "TRANSLATE_LLM_MODEL",
            "TRANSLATE_PROVIDERS",
            "MYMEMORY_EMAIL",
        ):
            self.assertIn(name, self.text)
        # A missing key must not fail the job: every provider is optional.
        self.assertIn("secrets.DEEPL_API_KEY", self.text)
        self.assertNotIn("required: true", self.text)

    def test_workflow_offers_the_ai_summary_switch_without_requiring_it(self):
        # The AI line reuses the translation endpoint, so the only new knob is
        # an optional model override and a switch to turn the line off.
        self.assertIn("SUMMARY_LLM_MODEL: ${{ vars.SUMMARY_LLM_MODEL }}", self.text)
        self.assertIn("--no-summarize", self.text)
        self.assertIn("summarize:", self.text)

    def test_workflow_accepts_a_bare_deepseek_secret(self):
        self.assertIn("DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY", self.text)
        self.assertIn("TRANSLATE_LLM_PROVIDER: ${{ vars.TRANSLATE_LLM_PROVIDER }}", self.text)

    def test_probe_workflow_reports_which_endpoint_answers(self):
        text = PROBE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("--probe-translators", text)
        self.assertIn("--out build/probe", text)
        self.assertIn("issues: write", text)
        self.assertIn("gh issue comment", text)

    def test_probe_workflow_checks_the_chain_on_a_pull_request(self):
        text = PROBE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("scripts/wechat_digest.py", text)
        # A fork's token cannot comment, so only branches in this repository do.
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", text)


if __name__ == "__main__":
    unittest.main()
