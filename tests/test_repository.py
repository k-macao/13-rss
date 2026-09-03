import html
import re
import json
import unittest
from pathlib import Path

from scripts.catalog import CATEGORIES, CATEGORY_EMOJI, MAX_ALL_FEEDS, PACKS, TOP200_FEEDS


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CONTRIBUTING.zh-CN.md",
    ROOT / "SOURCES.md",
    ROOT / "RSS-GUIDE.md",
    ROOT / "RSS-GUIDE.zh-CN.md",
]


class RepositoryTests(unittest.TestCase):
    def test_local_markdown_targets_exist(self):
        missing = []
        for markdown in MARKDOWN_FILES:
            text = markdown.read_text(encoding="utf-8")
            targets = re.findall(r"!?\[[^]]*\]\(([^)]+)\)", text)
            targets += re.findall(r"\bsrc=[\"']([^\"']+)[\"']", text)
            for target in targets:
                target = target.strip().split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (markdown.parent / target).resolve()
                if not resolved.exists():
                    missing.append(f"{markdown.name}: {target}")
        self.assertEqual(missing, [])

    def test_readmes_have_no_unresolved_count_tokens(self):
        for name in ("README.md", "README.zh-CN.md"):
            self.assertNotIn("__COUNT_", (ROOT / name).read_text(encoding="utf-8"))

    def test_readmes_use_stable_import_preview_without_feed_cap_copy(self):
        preview = (
            "https://cdn.jsdelivr.net/gh/fuxiaoai/tidings-rss@v1.1.0/"
            "assets/tidings-import-news-research.png"
        )
        forbidden = ("150 feeds", "150-feed", "150 个订阅源")
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(preview, text)
            for phrase in forbidden:
                self.assertNotIn(phrase, text)

    def test_every_bundle_has_a_latest_release_download(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for filename, _title in PACKS.values():
            self.assertIn(f"releases/latest/download/{filename}", readme)

    def test_readme_bundle_counts_match_catalog(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        expected = {
            filename: sum(pack in feed["packs"] for feed in catalog["feeds"])
            for pack, (filename, _title) in PACKS.items()
        }
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            found = {
                filename: int(count)
                for count, filename in re.findall(
                    r"\|\s*`(\d+)`\s*\|[^\n]*releases/latest/download/(tidings-[a-z0-9-]+\.opml)",
                    text,
                )
            }
            self.assertEqual(found, expected)

    def test_catalog_size_limits_and_complete_appendix(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        self.assertLessEqual(len(catalog["feeds"]), 730)
        self.assertLessEqual(sum("blogs" in feed["packs"] for feed in catalog["feeds"]), 400)
        expected_urls = {feed["feed_url"] for feed in catalog["feeds"]}
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            appendix = text.split("<!-- SOURCE_APPENDIX_START -->", 1)[1].split("<!-- SOURCE_APPENDIX_END -->", 1)[0]
            found_urls = set(re.findall(r"\[RSS\]\((https?://[^)]+)\)", appendix))
            self.assertEqual(found_urls, expected_urls)

    def test_topic_bundles_and_category_labels_are_consistent(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        requirements = {
            "communities": "Communities",
            "security": "Security",
            "tech-media": "Technology Media",
            "weeklies": "Tech Newsletters & Weeklies",
        }
        for pack, category in requirements.items():
            selected = [feed for feed in catalog["feeds"] if pack in feed["packs"]]
            self.assertGreater(len(selected), 0)
            self.assertTrue(all(feed["category"] == category for feed in selected))
            self.assertTrue(all(not re.search(r"[\U0001F300-\U0001FAFF]", feed["category"]) for feed in selected))
            opml = (ROOT / "opml" / PACKS[pack][0]).read_text(encoding="utf-8")
            label = html.escape(f"{CATEGORY_EMOJI[category]} {category}", quote=True)
            self.assertIn(f'text="{label}"', opml)

    def test_top200_is_balanced_and_repeatedly_validated(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        selected = [feed for feed in catalog["feeds"] if "top200" in feed["packs"]]
        report = json.loads((ROOT / "reports/top200-curation.json").read_text(encoding="utf-8"))
        decisions = [item for item in report["decisions"] if item["selected"]]
        self.assertEqual(len(selected), TOP200_FEEDS)
        self.assertEqual({feed["category"] for feed in selected}, set(CATEGORIES))
        self.assertEqual({feed["feed_url"] for feed in selected}, {item["feed_url"] for item in decisions})
        self.assertEqual(report["selected_count"], TOP200_FEEDS)
        self.assertEqual(report["validation"]["status"], "passed")
        summary = json.loads((ROOT / "reports/validation-summary.json").read_text(encoding="utf-8"))["top200_review"]
        self.assertEqual(summary["published"], TOP200_FEEDS)
        self.assertEqual(summary["selected_chinese"], sum(feed["language"] == "zh" for feed in selected))
        self.assertEqual(
            summary["selected_first_party_or_direct_endpoints"],
            sum(item["selection_evidence"]["first_party_or_direct_endpoint"] for item in decisions),
        )
        self.assertEqual(report["reproduction"]["candidate_snapshot"], "reports/top200-candidates.json")
        candidates = json.loads((ROOT / report["reproduction"]["candidate_snapshot"]).read_text(encoding="utf-8"))
        self.assertEqual(candidates["candidate_count"], len(candidates["candidates"]))
        self.assertEqual(candidates["candidate_count"], 307)
        self.assertEqual(len(report["validation"]["round_reports"]), 3)
        self.assertEqual(len(report["validation"]["video_round_reports"]), 3)
        self.assertTrue(all(len(item["parser_rounds"]) == 3 for item in decisions))
        self.assertTrue(all(all(round_["ok"] for round_ in item["parser_rounds"]) for item in decisions))
        self.assertTrue(all(all(round_["item_count"] > 0 for round_ in item["parser_rounds"]) for item in decisions))
        self.assertTrue(all(all(round_["latest_item_at"] for round_ in item["parser_rounds"]) for item in decisions))
        self.assertTrue(all(item["selection_evidence"]["note"] for item in decisions))

        raw_payloads = [
            json.loads((ROOT / path).read_text(encoding="utf-8"))
            for path in report["validation"]["round_reports"]
        ]
        raw_video_payloads = [
            json.loads((ROOT / path).read_text(encoding="utf-8"))
            for path in report["validation"]["video_round_reports"]
        ]
        raw_rounds = [{item["feed_url"]: item for item in payload["results"]} for payload in raw_payloads]
        raw_video_rounds = [{item["feed_url"]: item for item in payload["results"]} for payload in raw_video_payloads]
        candidate_urls = [item["feed_url"] for item in candidates["candidates"]]
        self.assertEqual(len(candidate_urls), len(set(candidate_urls)))
        for payload, raw in zip(raw_payloads, raw_rounds):
            self.assertEqual(payload["candidate_count"], len(payload["results"]))
            self.assertEqual(len(raw), len(payload["results"]))
            self.assertEqual(set(raw), set(candidate_urls))
        for payload, raw in zip(raw_video_payloads, raw_video_rounds):
            self.assertEqual(payload["candidate_count"], len(payload["results"]))
            self.assertEqual(len(raw), len(payload["results"]))
        self.assertTrue({item["feed_url"] for item in decisions}.issubset(set(candidate_urls)))
        for item in decisions:
            source_rounds = raw_video_rounds if item["category"] == "Videos" else raw_rounds
            expected = [
                {
                    "ok": raw[item["feed_url"]]["ok"],
                    "item_count": raw[item["feed_url"]]["item_count"],
                    "latest_item_at": raw[item["feed_url"]]["latest_item_at"],
                    "duration_ms": raw[item["feed_url"]]["duration_ms"],
                }
                for raw in source_rounds
            ]
            self.assertEqual(item["parser_rounds"], expected)

        selected_titles = {feed["title"] for feed in selected}
        self.assertNotIn("机器之心SOTA模型", selected_titles)
        self.assertNotIn("HackerNews每日摘要 on SuperTechFans", selected_titles)
        evidence_publishers = [item["selection_evidence"]["publisher"] for item in decisions]
        publisher_counts = {publisher: evidence_publishers.count(publisher) for publisher in set(evidence_publishers)}
        for publisher, count in publisher_counts.items():
            limit = 2 if publisher in {
                "bbc", "google", "mit", "new york times", "theguardian.com", "阿里巴巴", "腾讯"
            } else 1
            self.assertLessEqual(count, limit, publisher)

    def test_current_theme_curation_report_matches_catalog(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        report = json.loads((ROOT / "reports/theme-curation.json").read_text(encoding="utf-8"))
        selected = [item for item in report["decisions"] if item["selected"]]
        additions = {
            feed["feed_url"]
            for feed in catalog["feeds"]
            if "awesome-rsshub-routes-review-2026-08-12" in feed["sources"]
        }
        self.assertEqual({item["feed_url"] for item in selected}, additions)
        self.assertTrue(all(len(item["parser_rounds"]) == 3 for item in selected))
        self.assertTrue(all(all(round_["ok"] for round_ in item["parser_rounds"]) for item in selected))

    def test_community_additions_match_repeated_validation_report(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        report = json.loads((ROOT / "reports/community-curation.json").read_text(encoding="utf-8"))
        selected = [item for item in report["decisions"] if item["selected"]]
        selected_urls = {item["feed_url"] for item in selected}
        catalog_urls = {
            feed["feed_url"]
            for feed in catalog["feeds"]
            if "community-expansion-2026-08-12" in feed["sources"]
        }
        self.assertEqual(selected_urls, catalog_urls)
        self.assertEqual(len(selected), report["selected_count"])
        self.assertTrue(all(len(item["parser_rounds"]) == 3 for item in selected))
        self.assertTrue(all(all(round_["ok"] for round_ in item["parser_rounds"]) for item in selected))

    def test_hotlist_additions_match_curation_report(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        report = json.loads((ROOT / "reports/hotlist-curation.json").read_text(encoding="utf-8"))
        selected = [item for item in report["decisions"] if item["selected"]]
        selected_urls = {item["feed_url"] for item in selected}
        catalog_urls = {
            feed["feed_url"]
            for feed in catalog["feeds"]
            if report["review"] in feed["sources"]
        }
        self.assertEqual(selected_urls, catalog_urls)
        self.assertEqual(len(selected), report["selected_count"])
        self.assertEqual(report["candidate_count"], len(report["decisions"]))
        self.assertEqual(report["rejected_count"], len(report["decisions"]) - len(selected))
        self.assertEqual(report["published_total"], len(catalog["feeds"]))
        self.assertEqual(report["engine"], "HTTP fetch + RSS/Atom/JSON Feed parse")
        for item in selected:
            self.assertTrue(item["reason"], item["title"])
            self.assertTrue(item["checks"], item["title"])
            self.assertTrue(any(check["ok"] for check in item["checks"]), item["title"])
            if (item["checks"][-1].get("items_captured") or 0) == 0:
                self.assertTrue(item.get("caveat"), item["title"])
        rejected = [item for item in report["decisions"] if not item["selected"]]
        self.assertTrue(all(item["reason"] for item in rejected))
        self.assertEqual(
            {item["title"] for item in rejected if not item["feed_url"]},
            {"NewsNow 最热聚合页", "REBANG 极简热榜", "萝卜投研", "今日热榜 科技分类页"},
        )
        summary = json.loads((ROOT / "reports/validation-summary.json").read_text(encoding="utf-8"))
        review = summary["hotlist_aggregator_review"]
        self.assertEqual(summary["published"], len(catalog["feeds"]))
        self.assertEqual(summary["limits"]["complete_collection_maximum"], MAX_ALL_FEEDS)
        self.assertEqual(review["published_additions"], report["selected_count"])
        self.assertEqual(review["candidates"], report["candidate_count"])
        self.assertEqual(review["selection_evidence"], "reports/hotlist-curation.json")
        self.assertEqual(
            summary["by_pack"],
            {pack: sum(pack in feed["packs"] for feed in catalog["feeds"]) for pack in sorted(PACKS)},
        )

    def test_wechat_and_company_technology_bundles_are_curated(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        wechat = [feed for feed in catalog["feeds"] if "wechat" in feed["packs"]]
        company = [feed for feed in catalog["feeds"] if "company-tech" in feed["packs"]]
        self.assertGreaterEqual(len(wechat), 20)
        self.assertGreaterEqual(len(company), 30)
        self.assertTrue(all(feed["language"] == "zh" and "wechat2rss" in feed["sources"] for feed in wechat))
        keys = [(feed["organization"].casefold(), feed["company_direction"].casefold()) for feed in company]
        self.assertEqual(len(keys), len(set(keys)))
        grouped = {key: feed for key, feed in zip(keys, company)}
        for key, feed in grouped.items():
            if "wechat2rss" not in feed["feed_url"]:
                continue
            self.assertFalse(any(
                other is not feed and (other.get("organization", "").casefold(), other.get("company_direction", "").casefold()) == key
                for other in catalog["feeds"]
            ))

        report = json.loads((ROOT / "reports/wechat-curation.json").read_text(encoding="utf-8"))
        selected_urls = {item["feed_url"] for item in report["decisions"] if item["selected"]}
        self.assertEqual(selected_urls, {feed["feed_url"] for feed in wechat})
        self.assertTrue(all(len(item["probe_rounds"]) == 2 for item in report["decisions"] if item["selected"]))
        self.assertTrue(all(all(result["ok"] for result in item["probe_rounds"]) for item in report["decisions"] if item["selected"]))

    def test_historical_real_import_report_is_internally_consistent(self):
        report = json.loads((ROOT / "reports/import-verification.json").read_text(encoding="utf-8"))
        imports = {item["file"]: item for item in report["imports"]}
        expected = {"tidings-news.opml": 44, "tidings-research.opml": 28}
        self.assertEqual({name: imports[name]["imported"] for name in expected}, expected)
        self.assertTrue(all(imports[name]["final_refresh_failed"] == 0 for name in expected))
        self.assertEqual(report["failed_feeds"], [])
        self.assertEqual(report["unique_feeds"], 72)
        self.assertGreater(report["visible_articles"], 0)
        screenshot = ROOT / report["screenshot"]
        self.assertTrue(screenshot.is_file())
        self.assertGreater(screenshot.stat().st_size, 500_000)
        selected = report["selected_article"]
        self.assertEqual(selected["full_text_status"], "full")
        self.assertGreater(selected["image_blocks"], 0)
        self.assertGreaterEqual(selected["substantial_paragraphs"], 3)
        ui = report["ui_verification"]
        self.assertTrue(ui["full_text_ready"])
        self.assertFalse(ui["fetching_full_text_visible"])
        self.assertFalse(ui["full_text_error_visible"])
        self.assertFalse(ui["reader_boilerplate_visible"])
        self.assertGreater(ui["loaded_article_images"], 0)
        self.assertEqual(ui["failed_article_images"], 0)
        self.assertTrue(ui["first_image_visible"])
        self.assertGreater(report["thumbnail_verification"]["loaded"], 0)
        self.assertEqual(report["thumbnail_verification"]["hidden_or_failed"], 0)

    def test_chinese_blog_curation_report_matches_catalog(self):
        catalog = json.loads((ROOT / "data/feeds.json").read_text(encoding="utf-8"))
        report = json.loads((ROOT / "reports/chinese-blog-curation.json").read_text(encoding="utf-8"))
        selected = [item for item in report["blogs"] if item["selected"]]
        published = [feed for feed in catalog["feeds"] if "blogs" in feed["packs"]]
        upstream_published = [feed for feed in published if "chinese-independent-blogs" in feed["sources"]]
        self.assertEqual(report["candidate_count"], 1331)
        self.assertEqual(len(selected), len(upstream_published))
        self.assertTrue(all(item["successful_rounds"] == 3 for item in selected))
        self.assertTrue(all(item["latest_item_age_days"] <= 180 for item in selected))
        self.assertEqual(
            {feed["feed_url"] for feed in upstream_published},
            {item["feed_url"] for item in selected},
        )


if __name__ == "__main__":
    unittest.main()
