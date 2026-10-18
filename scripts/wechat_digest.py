#!/usr/bin/env python3
"""Build an editorial-style RSS digest and push it to WeChat through pushplus.

Dependency-free: standard library only, matching the rest of this repository.

Usage examples
--------------
    python scripts/wechat_digest.py --dry-run --out build/digest
    PUSHPLUS_TOKEN=xxx python scripts/wechat_digest.py --pack all
    python scripts/wechat_digest.py --probe-translators --out build/probe

Design notes
------------
The rendered HTML follows an international-magazine (归藏 skills) look: a
portrait, single-column canvas on light grey paper, black body copy at a small
size, dark grey metadata, and a fluorescent green accent used sparingly for
kickers, indices and rules. All styling is inline so pushplus/WeChat webviews
render it without a stylesheet.

Every card can also carry an AI line: a Chinese take on what the article says,
written by the same OpenAI-compatible endpoint, at most 100 characters long, and
in a human register rather than a machine one. The line is dropped — not printed
— when the source carries too little text, when the model declines, or when the
answer still reads like a model wrote it.

English titles and summaries are translated by walking a provider chain (keyed
providers first, then the keyless endpoints) and stopping at the first endpoint
that answers; a provider that fails twice is retired for the rest of the run. The
English original is never overwritten, so a failed translation only drops the
appended line, and every run reports which provider answered.

The keyed half of that chain is one `llm` entry for any OpenAI-compatible chat
endpoint. One key alone configures it — `DEEPSEEK_API_KEY` (the preset the guide
recommends), `OPENAI_API_KEY`, `DASHSCOPE_API_KEY`, `MOONSHOT_API_KEY`,
`ZHIPUAI_API_KEY`, `OPENROUTER_API_KEY` — while `TRANSLATE_LLM_API_KEY` plus
`TRANSLATE_LLM_BASE_URL`/`TRANSLATE_LLM_MODEL` (and `TRANSLATE_LLM_PROVIDER` to
name a preset) stay available for anything else.
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
from functools import partial
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
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
# Translation runs through a chain of providers instead of a single endpoint:
# Google's public gtx endpoint answered for the first releases of this digest but
# rate-limits and often refuses datacenter addresses such as GitHub-hosted
# runners, which silently removed every appended translation. The chain tries
# the configured keyed providers first, then the keyless ones, retires a provider
# that fails twice, and remembers the provider that worked, so one reachable
# endpoint keeps a whole issue bilingual at one request per string.
GOOGLE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
# Google's dictionary client, a separate endpoint with its own quota.
GOOGLE_CHROME_ENDPOINT = "https://clients5.google.com/translate_a/t"
# Microsoft Edge's keyless translator: the successor of the removed
# edge.microsoft.com/translate/auth + api-edge flow. The body is a bare JSON
# array of strings; from/to are the only parameters it honors.
EDGE_ENDPOINT = "https://edge.microsoft.com/translate/translatetext"
MYMEMORY_ENDPOINT = "https://api.mymemory.translated.net/get"
DEEPL_FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_ENDPOINT = "https://api.deepl.com/v2/translate"
LLM_BASE_URL_DEFAULT = "https://api.openai.com/v1"
LLM_MODEL_DEFAULT = "gpt-4o-mini"
# OpenAI-compatible presets: one environment variable is enough to switch the
# `llm` provider on with an endpoint and a model that belong to that key.
# DeepSeek is the one the guide recommends configuring first, and its model IDs
# move — ``deepseek-chat`` and ``deepseek-reasoner`` were retired on 2026-07-24 —
# so a preset carries a current ID instead of leaving the caller to guess one.
LLM_PRESETS = {
    "deepseek": {
        "keys": ("DEEPSEEK_API_KEY",),
        "host": "api.deepseek.com",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",
        # DeepSeek thinks by default, and thinking mode ignores `temperature`
        # and bills the reasoning tokens: a translation wants neither.
        "body": {"thinking": {"type": "disabled"}},
    },
    "openai": {
        "keys": ("OPENAI_API_KEY",),
        "host": "api.openai.com",
        "base_url": LLM_BASE_URL_DEFAULT,
        "model": LLM_MODEL_DEFAULT,
    },
    "dashscope": {
        "keys": ("DASHSCOPE_API_KEY",),
        "host": "dashscope.aliyuncs.com",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "moonshot": {
        "keys": ("MOONSHOT_API_KEY",),
        "host": "api.moonshot.cn",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "zhipu": {
        "keys": ("ZHIPUAI_API_KEY",),
        "host": "open.bigmodel.cn",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
    },
    "openrouter": {
        "keys": ("OPENROUTER_API_KEY",),
        "host": "openrouter.ai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
    },
}
# Model IDs that answer with an error today; naming the replacement beats letting
# the endpoint's own "model not exist" reply go unexplained in the report.
RETIRED_LLM_MODELS = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-flash",
}
TRANSLATE_SOURCE = "en"
TRANSLATE_TARGET = "zh-CN"
# Network-level failures are retried with a short backoff, while a malformed
# answer is not retried because the next attempt returns the same garbage.
TRANSLATE_RETRIES = 3
TRANSLATE_BACKOFF_SECONDS = (1.0, 2.0)
TRANSLATE_MAX_WORKERS = 4
# Retire a provider after this many consecutive failures, so a blocked endpoint
# costs a couple of timeouts per run instead of a couple per translated string.
TRANSLATE_MAX_FAILURES = 2

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

# Digest branding — the title no longer carries the date or pushplus branding.
DIGEST_TITLE = "章鱼 AI · 营销热点助手"
DIGEST_SUBTITLE = "每天三分钟！去噪音！留干货！"
# Scope note rendered with the intro block on the first page.
DIGEST_SCOPE = (
    "全网兼顾中文和英文内容，覆盖 AI、工程、安全、科技媒体、周刊、科研、"
    "新闻、产品、商业、博客、社区、文化、视频和播客。"
)
DIGEST_INTRO = (
    "全网境内外为你寻找蛛丝马迹 - 提供全景视野分析，由多模型协同推理决策。"
    "底层所使用的大语言模型（LLM）多模式背后结合使用了多种不同的先进模型，"
    "包括但不限于 Claude、ChatGPT、Gemini、Grok、Qwen 以及 Kimi。"
    "根据不同的资产管理任务需求，更好地发挥各个模型的优势来提供数据支持！[加油]"
)
AUTHOR_LINE = "作者：章鱼 ai · 仅供参考，分析研究"


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


CJK_RANGES = (
    (0x3000, 0x303F),  # CJK punctuation such as 。、「」《》
    (0x3040, 0x30FF),  # kana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # full-width forms, including ，
)
LATIN_LETTER = re.compile(r"[A-Za-z]")
MIN_LATIN_LETTERS = 3
CJK_SHARE_LIMIT = 0.3


def is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in CJK_RANGES)


def is_english(text: str) -> bool:
    """Return whether text reads as English and therefore needs translation.

    One Chinese character is not enough to call a title Chinese: a headline that
    quotes a Chinese product name is still English, while a Chinese sentence that
    quotes an English brand stays Chinese. The share of CJK characters among the
    characters that carry language decides, so mixed text follows the side that
    dominates it.
    """
    if not text or re.fullmatch(r"https?://\S+", text.strip()):
        return False
    cjk = sum(1 for char in text if is_cjk(char))
    letters = len(LATIN_LETTER.findall(text))
    # Avoid sending version numbers, URLs, source names, and punctuation alone.
    if letters < MIN_LATIN_LETTERS:
        return False
    if cjk == 0:
        return True
    return cjk / (cjk + letters) < CJK_SHARE_LIMIT


class TranslationError(RuntimeError):
    """A provider could not translate a string.

    ``retryable`` separates a transient fault (timeout, rate limit, connection
    reset) from an answer that will never be usable (an HTML block page, a
    missing translation field), so the chain only sleeps on the former.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def _translate_request(url: str, *, data=None, headers=None, timeout: int = 8) -> bytes:
    """Fetch one translation URL, turning network faults into retryable errors."""
    request = Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise TranslationError(f"{type(error).__name__}: {error}") from error


def _translate_json(payload: bytes):
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except ValueError as error:
        raise TranslationError(f"malformed response: {error}", retryable=False) from error


def google_translate(text: str, *, timeout: int) -> str:
    """Google's public gtx endpoint.

    The payload is ``[[["你好", "Hello", …]], null, "en"]``; long input comes
    back as several segments that are concatenated in order.
    """
    query = urlencode({"client": "gtx", "sl": TRANSLATE_SOURCE, "tl": TRANSLATE_TARGET, "dt": "t", "q": text})
    payload = _translate_json(_translate_request(f"{GOOGLE_ENDPOINT}?{query}", timeout=timeout))
    try:
        return "".join(part[0] for part in payload[0] if part and part[0])
    except (TypeError, IndexError, KeyError) as error:
        raise TranslationError(f"unexpected payload: {payload!r}"[:200], retryable=False) from error


def google_chrome_translate(text: str, *, timeout: int) -> str:
    """Google's dictionary client (``dict-chrome-ex``).

    A separate service from the gtx endpoint, with its own quota. One string
    comes back either as ``[["你好", "en"]]`` or as the gtx-style nested shape,
    depending on which edge node answers, so both are accepted.
    """
    query = urlencode({"client": "dict-chrome-ex", "sl": TRANSLATE_SOURCE, "tl": TRANSLATE_TARGET, "q": text})
    payload = _translate_json(_translate_request(f"{GOOGLE_CHROME_ENDPOINT}?{query}", timeout=timeout))
    try:
        if isinstance(payload[0][0], str):
            return payload[0][0]
        return "".join(part[0] for part in payload[0] if part and part[0])
    except (TypeError, IndexError, KeyError) as error:
        raise TranslationError(f"unexpected payload: {payload!r}"[:200], retryable=False) from error


def edge_translate(text: str, *, timeout: int) -> str:
    """Microsoft Edge's keyless translator, the current Bing fallback.

    The endpoint runs an HTML tag aligner over every request, so a bare ``<``
    fuses into a pseudo-tag and tag names get translated; escaped entities
    round-trip verbatim, so markup characters are escaped before the call and
    the answer is unescaped once afterwards.
    """
    escaped = "<" in text or ">" in text
    body = json.dumps([html.escape(text, quote=False) if escaped else text]).encode("utf-8")
    query = urlencode({"from": TRANSLATE_SOURCE, "to": "zh-Hans", "isEnterpriseClient": "false"})
    payload = _translate_json(
        _translate_request(
            f"{EDGE_ENDPOINT}?{query}",
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    )
    try:
        translated = payload[0]["translations"][0]["text"]
    except (TypeError, IndexError, KeyError) as error:
        raise TranslationError(f"unexpected payload: {payload!r}"[:200], retryable=False) from error
    if not isinstance(translated, str) or not translated.strip():
        raise TranslationError("no translation in response", retryable=False)
    return html.unescape(translated.strip()) if escaped else translated.strip()


def mymemory_translate(text: str, *, timeout: int) -> str:
    """MyMemory's public translation memory, keyed by nothing but the text.

    Quota and language errors arrive as ordinary sentences inside
    ``translatedText``, so those are turned back into failures.
    """
    query = {"q": text, "langpair": f"{TRANSLATE_SOURCE}|{TRANSLATE_TARGET}"}
    email = os.environ.get("MYMEMORY_EMAIL", "").strip()
    if email:
        query["de"] = email
    payload = _translate_json(_translate_request(f"{MYMEMORY_ENDPOINT}?{urlencode(query)}", timeout=timeout))
    if not isinstance(payload, dict):
        raise TranslationError(f"unexpected payload: {payload!r}"[:200], retryable=False)
    data = payload.get("responseData") or {}
    translated = html.unescape(str(data.get("translatedText", ""))).strip()
    if not translated:
        raise TranslationError(f"no translation in response (status {payload.get('responseStatus')!r})", retryable=False)
    if "MYMEMORY WARNING" in translated.upper():
        raise TranslationError(f"quota reached: {translated[:120]}")
    return translated


def deepl_translate(text: str, *, timeout: int, api_key: str, endpoint: str) -> str:
    """DeepL's translate API; the free keys (``…:fx``) use the api-free host."""
    body = urlencode({"text": text, "target_lang": "ZH", "source_lang": "EN"}).encode("utf-8")
    payload = _translate_json(
        _translate_request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"DeepL-Auth-Key {api_key}"},
            timeout=timeout,
        )
    )
    try:
        return payload["translations"][0]["text"]
    except (TypeError, IndexError, KeyError) as error:
        raise TranslationError(f"unexpected payload: {payload!r}"[:200], retryable=False) from error


LLM_SYSTEM_PROMPT = (
    "You are a professional translator. Translate the user's text into Simplified Chinese. "
    "Keep product names, brands, and code identifiers in their original form. "
    "Reply with the translation only: no quotes, no notes, no extra lines."
)


def _llm_chat(messages, *, timeout: int, api_key: str, base_url: str, model: str,
              extra=None, temperature: int = 0) -> str:
    """One OpenAI-compatible chat completion, or a ``TranslationError`` saying why not.

    Both the translation chain and the AI summary talk to the same kind of
    endpoint, so the wire format lives here once: a network fault stays retryable
    while a payload that will never parse does not.
    """
    payload = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "stream": False,
    }
    payload.update(extra or {})
    body = json.dumps(payload).encode("utf-8")
    answer = _translate_json(
        _translate_request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    )
    try:
        content = answer["choices"][0]["message"]["content"]
    except (TypeError, IndexError, KeyError) as error:
        raise TranslationError(f"unexpected payload: {answer!r}"[:200], retryable=False) from error
    if not isinstance(content, str) or not content.strip():
        raise TranslationError("empty completion", retryable=False)
    return content.strip().strip("`").strip()


def llm_translate(text: str, *, timeout: int, api_key: str, base_url: str, model: str, extra=None) -> str:
    """Any OpenAI-compatible chat endpoint, for a key the user already owns.

    The same call reaches DeepSeek, Qwen/DashScope, Moonshot, Zhipu, or any
    gateway, which makes it the most reliable option when the keyless endpoints
    refuse GitHub's runner addresses.

    ``extra`` carries the fields a preset has to add on top of the OpenAI shape —
    DeepSeek, for instance, thinks by default and is told to skip that here.
    """
    answer = _llm_chat(
        [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        timeout=timeout,
        api_key=api_key,
        base_url=base_url,
        model=model,
        extra=extra,
        temperature=0,
    )
    answer = re.sub(r"^(翻译|译文|translation)\s*[:：]\s*", "", answer, flags=re.IGNORECASE)
    return answer.strip().strip('"').strip("“”").strip()


class Provider:
    """One named translation backend in the chain."""

    def __init__(self, name: str, call, label: str, *, keyless: bool = True):
        self.name = name
        self.call = call
        self.label = label
        self.keyless = keyless

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Provider({self.name!r})"


class Translator:
    """Translate through a chain of providers, remembering what works.

    Providers are tried in order, a provider that fails ``TRANSLATE_MAX_FAILURES``
    times in a row is retired for the rest of the run, and the provider that last
    answered is tried first from then on. That keeps one broken endpoint from
    costing a timeout per article while still letting the next endpoint take over
    the moment the working one starts failing. A shared instance is thread-safe,
    so concurrent workers cooperate instead of each rediscovering the chain.
    """

    def __init__(self, providers, *, timeout: int = 8, retries: int = TRANSLATE_RETRIES,
                 backoff: tuple = TRANSLATE_BACKOFF_SECONDS):
        self.providers = list(providers)
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff or ()
        self._lock = threading.Lock()
        self._failures = defaultdict(int)
        self._retired = []
        self._active = ""
        self.stats = {
            provider.name: {"attempts": 0, "ok": 0, "empty": 0, "errors": 0, "last_error": ""}
            for provider in self.providers
        }
        self.attempted = 0
        self.translated = 0

    def _order(self):
        with self._lock:
            live = [provider for provider in self.providers if provider.name not in self._retired]
            if self._active:
                live.sort(key=lambda provider: provider.name != self._active)
            return live

    def _record(self, name: str, outcome: str, error: str = ""):
        with self._lock:
            entry = self.stats[name]
            entry["attempts"] += 1
            if outcome == "ok":
                entry["ok"] += 1
                self._failures[name] = 0
                self._active = name
            elif outcome == "empty":
                entry["empty"] += 1
                self._failures[name] = 0
            else:
                entry["errors"] += 1
                entry["last_error"] = error[:160]
                self._failures[name] += 1
                if self._failures[name] >= TRANSLATE_MAX_FAILURES and name not in self._retired:
                    self._retired.append(name)
                    if self._active == name:
                        self._active = ""

    def _wait(self, attempt: int):
        if self.backoff:
            time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])

    def _ask(self, provider: Provider, text: str) -> str:
        """Ask one provider, retrying transient faults; "" means nothing to add."""
        for attempt in range(self.retries):
            try:
                answer = provider.call(text, timeout=self.timeout)
            except TranslationError as error:
                self._record(provider.name, "error", str(error))
                if not error.retryable:
                    return ""
            except Exception as error:  # a provider must never break the digest
                self._record(provider.name, "error", f"{type(error).__name__}: {error}")
            else:
                answer = (answer or "").strip()
                if answer and answer != text:
                    self._record(provider.name, "ok")
                    return answer
                self._record(provider.name, "empty")
                return ""
            if attempt + 1 < self.retries:
                self._wait(attempt)
        return ""

    def translate(self, text: str) -> str:
        """Return the Chinese translation, or the original when nothing answers."""
        text = (text or "").strip()
        if not is_english(text):
            return text
        self.attempted += 1
        for provider in self._order():
            answer = self._ask(provider, text)
            if answer:
                self.translated += 1
                return answer
        return text

    def summary(self) -> dict:
        with self._lock:
            return {
                "attempted": self.attempted,
                "translated": self.translated,
                "active": self._active,
                "retired": list(self._retired),
                "providers": [
                    dict(
                        self.stats[provider.name],
                        name=provider.name,
                        label=provider.label,
                        keyless=provider.keyless,
                        retired=provider.name in self._retired,
                    )
                    for provider in self.providers
                ],
            }

    def report_line(self) -> str:
        """One log line naming the provider that answered and the ones retired."""
        summary = self.summary()
        parts = [f"translation: translated={summary['translated']}/{summary['attempted']}",
                 f"provider={summary['active'] or 'none'}"]
        if summary["retired"]:
            parts.append("retired=" + ",".join(summary["retired"]))
        return " ".join(parts)

    def report_markdown(self) -> str:
        """A table for the GitHub Actions step summary and the probe report."""
        summary = self.summary()
        rows = ["| Provider | Kind | Attempts | Translated | Empty | Errors | State | Last error |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |"]
        for provider in summary["providers"]:
            rows.append(
                f"| `{provider['name']}` | {'keyless' if provider['keyless'] else 'keyed'} "
                f"| {provider['attempts']} | {provider['ok']} | {provider['empty']} | {provider['errors']} "
                f"| {'retired' if provider['retired'] else 'available'} | {provider['last_error'] or '—'} |"
            )
        return "\n".join(
            [
                f"Translation: **{summary['translated']}** of **{summary['attempted']}** strings via "
                f"`{summary['active'] or 'no provider'}`.",
                "",
                *rows,
            ]
        )


def preset_for_base_url(url: str) -> str:
    """Name the preset an endpoint belongs to, if any.

    A key plus a base URL is how most people configure this, and the host says
    which preset's model and request fields apply.
    """
    host = urlsplit(url or "").netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    for name, preset in LLM_PRESETS.items():
        if host == preset["host"] or host.endswith("." + preset["host"]):
            return name
    return ""


def normalize_llm_base_url(url: str) -> str:
    """Accept the base URL the way people actually paste it.

    ``https://api.deepseek.com``, ``https://api.deepseek.com/v1/`` and even a
    full ``…/v1/chat/completions`` all have to land on the same endpoint: the
    base URL is the easiest thing to get subtly wrong by hand, and every wrong
    spelling used to look like one more dead provider.
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    if urlsplit(url).path in ("", "/") and preset_for_base_url(url) in ("deepseek", "openai"):
        url = f"{url}/v1"
    return url


def resolve_llm_config():
    """Work out which OpenAI-compatible endpoint the environment asks for.

    Resolution order, so that the guide's promise — one DeepSeek secret and
    nothing else — holds, while an explicit configuration always wins:

    1. ``TRANSLATE_LLM_API_KEY`` with ``TRANSLATE_LLM_BASE_URL`` and
       ``TRANSLATE_LLM_MODEL``, the generic route documented before presets.
    2. ``TRANSLATE_LLM_PROVIDER`` naming a preset, whose own key variable,
       endpoint, and model are then used.
    3. The presets in order, so a bare ``DEEPSEEK_API_KEY`` is enough.

    Returns ``None`` when no key is configured; nothing here prints or logs a key.
    """
    explicit_key = os.environ.get("TRANSLATE_LLM_API_KEY", "").strip()
    base_override = os.environ.get("TRANSLATE_LLM_BASE_URL", "").strip()
    model_override = os.environ.get("TRANSLATE_LLM_MODEL", "").strip()
    wanted = os.environ.get("TRANSLATE_LLM_PROVIDER", "").strip().lower()
    if wanted and wanted not in LLM_PRESETS:
        raise SystemExit(
            f"unknown TRANSLATE_LLM_PROVIDER: {wanted} (known: {', '.join(LLM_PRESETS)})"
        )

    def config(key, source, preset_name, preset):
        return {
            "key": key,
            "source": source,
            "preset": preset_name,
            "base_url": normalize_llm_base_url(
                base_override or preset.get("base_url") or LLM_BASE_URL_DEFAULT
            ),
            "model": model_override or preset.get("model") or LLM_MODEL_DEFAULT,
            "body": dict(preset.get("body") or {}),
        }

    if explicit_key:
        # The preset is named by TRANSLATE_LLM_PROVIDER or recognised from the
        # endpoint, so a key pointed at DeepSeek still gets DeepSeek's fields.
        preset_name = wanted or preset_for_base_url(base_override)
        return config(explicit_key, "TRANSLATE_LLM_API_KEY", preset_name, LLM_PRESETS.get(preset_name, {}))

    for preset_name in ([wanted] if wanted else list(LLM_PRESETS)):
        preset = LLM_PRESETS[preset_name]
        for env_name in preset["keys"]:
            key = os.environ.get(env_name, "").strip()
            if key:
                return config(key, env_name, preset_name, preset)
    return None


def llm_label(config: dict) -> str:
    """The label the reports show for the llm provider: model plus endpoint."""
    host = urlsplit(config["base_url"]).netloc or config["base_url"]
    return f"LLM ({config['model']} @ {host})"


def provider_notes():
    """Describe the translation configuration for the run reports.

    A key is never printed, but which *variable* it came from, which endpoint it
    points at, and which model answers are exactly what a reader needs when a
    translation goes missing.
    """
    notes = []
    llm = resolve_llm_config()
    if not llm:
        presets = ", ".join(f"`{name}`" for name in LLM_PRESETS)
        notes.append(
            "`llm` is off: set a provider key such as `DEEPSEEK_API_KEY`, or "
            f"`TRANSLATE_LLM_API_KEY` with `TRANSLATE_LLM_BASE_URL` and `TRANSLATE_LLM_MODEL` "
            f"(presets: {presets})."
        )
    else:
        preset = preset_for_base_url(llm["base_url"])
        endpoint = f"`{llm['base_url']}`" + (f" ({preset} preset)" if preset else "")
        notes.append(f"`llm`: key from `{llm['source']}`, endpoint {endpoint}, model `{llm['model']}`.")
        if (llm["source"] == "TRANSLATE_LLM_API_KEY" and not llm["preset"]
                and not os.environ.get("TRANSLATE_LLM_BASE_URL", "").strip()):
            # The generic route with nothing but a key means OpenAI's defaults,
            # which is worth saying when the key belongs to someone else.
            notes.append(
                f"`llm`: no `TRANSLATE_LLM_BASE_URL` is set, so the endpoint is `{llm['base_url']}` "
                f"and the model is `{llm['model']}`; set it (and `TRANSLATE_LLM_MODEL`, or "
                "`TRANSLATE_LLM_PROVIDER`) when the key belongs to another provider."
            )
        if "thinking" in llm["body"]:
            notes.append(
                "`llm`: thinking mode is disabled, because a translation needs no chain of thought."
            )
    if os.environ.get("DEEPL_API_KEY", "").strip():
        notes.append("`deepl` is enabled and is tried before the llm endpoint.")
    else:
        notes.append("`deepl` is off: `DEEPL_API_KEY` adds the highest-quality machine translation.")
    return notes


def llm_report_line() -> str:
    """One line naming the configured llm endpoint, without the key.

    The probe's table says whether an endpoint answered; this says which endpoint
    it was asked to answer for, which is what a reader needs when the provider is
    missing from the table altogether.
    """
    llm = resolve_llm_config()
    if not llm:
        return "config: llm=off"
    host = urlsplit(llm["base_url"]).netloc or llm["base_url"]
    return f"config: llm={llm['model']} @ {host} via {llm['source']}"


def config_warnings():
    """Configuration mistakes that would otherwise look like a dead endpoint."""
    warnings = []
    llm = resolve_llm_config()
    wanted = os.environ.get("TRANSLATE_LLM_PROVIDER", "").strip()
    if llm:
        replacement = RETIRED_LLM_MODELS.get(llm["model"].strip().lower())
        if replacement:
            warnings.append(
                f"TRANSLATE_LLM_MODEL={llm['model']} was retired on 2026-07-24; use {replacement}"
            )
    elif wanted:
        keys = " or ".join(f"`{name}`" for name in LLM_PRESETS[wanted.lower()]["keys"])
        warnings.append(
            f"TRANSLATE_LLM_PROVIDER={wanted} is set but {keys} is empty, so the llm provider is off"
        )
    return warnings


def configured_providers(names=None):
    """Build the provider chain: configured keyed endpoints first, then keyless.

    ``TRANSLATE_PROVIDERS`` (or ``--translators``) selects and orders providers by
    name; an unknown name is reported instead of being ignored, because a typo
    would otherwise look like a broken endpoint.
    """
    deepl_key = os.environ.get("DEEPL_API_KEY", "").strip()
    providers = []
    if deepl_key:
        endpoint = os.environ.get("DEEPL_API_URL", "").strip() or (
            DEEPL_FREE_ENDPOINT if deepl_key.rstrip().endswith(":fx") else DEEPL_PRO_ENDPOINT
        )
        providers.append(
            Provider("deepl", partial(deepl_translate, api_key=deepl_key, endpoint=endpoint), "DeepL", keyless=False)
        )
    llm = resolve_llm_config()
    if llm:
        providers.append(
            Provider(
                "llm",
                partial(
                    llm_translate,
                    api_key=llm["key"],
                    base_url=llm["base_url"],
                    model=llm["model"],
                    extra=llm["body"],
                ),
                llm_label(llm),
                keyless=False,
            )
        )
    providers.extend(
        [
            Provider("google", google_translate, "Google Translate (gtx)"),
            Provider("google-chrome", google_chrome_translate, "Google Translate (chrome)"),
            Provider("edge", edge_translate, "Microsoft Edge (Bing)"),
            Provider("mymemory", mymemory_translate, "MyMemory"),
        ]
    )
    wanted = [name.strip() for name in (names or []) if name and name.strip()]
    if not wanted:
        return providers
    known = {provider.name: provider for provider in providers}
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise SystemExit(
            f"unknown translator(s): {', '.join(unknown)} "
            f"(known: {', '.join(known)})"
        )
    return [known[name] for name in wanted]


# Original field -> the field that carries its appended Chinese translation.
# The English original is never overwritten, so a card can show both.
TRANSLATED_FIELDS = {"title": "title_zh", "summary": "summary_zh"}


def translate_to_chinese(
    text: str,
    *,
    timeout: int = 8,
    retries: int = TRANSLATE_RETRIES,
    backoff: tuple = TRANSLATE_BACKOFF_SECONDS,
    providers=None,
) -> str:
    """Translate one English string through the provider chain.

    The original is returned whenever every provider fails, so an unavailable
    translation only drops the extra line and never blocks a digest.
    """
    translator = Translator(providers or configured_providers(), timeout=timeout, retries=retries, backoff=backoff)
    return translator.translate(text)


def translate_items(items, *, timeout: int, concurrency: int, translator=None):
    """Append a Chinese translation to English titles and summaries.

    Translation runs concurrently before rendering. The source text stays in
    ``title``/``summary`` and the translation lands in ``title_zh``/``summary_zh``
    so the digest reads as original first, translation second. Nothing is
    appended when every provider fails or echoes the original back, which keeps
    a card from printing the same sentence twice. Pass a shared ``translator`` to
    keep its statistics and its provider bookkeeping for the whole run.
    """
    translator = translator or Translator(configured_providers(), timeout=timeout)
    fields = [(item, field) for item in items for field in TRANSLATED_FIELDS if is_english(item.get(field, ""))]
    if not fields:
        return items
    workers = max(1, min(concurrency, TRANSLATE_MAX_WORKERS, len(fields)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(translator.translate, item[field]): (item, field) for item, field in fields}
        for future in as_completed(futures):
            item, field = futures[future]
            try:
                translated = future.result()
            except Exception:
                # Keep the original RSS value if an unexpected translation error occurs.
                continue
            if translated and translated != item[field]:
                item[TRANSLATED_FIELDS[field]] = translated
    return items


# Two short strings are enough to tell a reachable endpoint from a blocked one.
PROBE_TEXTS = (
    "OpenAI ships a smaller model that runs on device",
    "The Federal Reserve held rates steady on Wednesday, and the index closed at a record high.",
)


def probe_providers(providers, *, timeout: int = 10, texts=PROBE_TEXTS):
    """Call every provider directly, so one run shows which endpoints answer.

    The chain hides which endpoint failed; this bypasses it on purpose and keeps
    the raw answer or the raw error for each provider and probe string.
    """
    rows = []
    for provider in providers:
        for text in texts:
            started = time.monotonic()
            try:
                answer = (provider.call(text, timeout=timeout) or "").strip()
                status = "ok" if answer else "empty"
            except Exception as error:
                answer, status = f"{type(error).__name__}: {error}", "error"
            rows.append(
                {
                    "provider": provider.name,
                    "label": provider.label,
                    "keyless": provider.keyless,
                    "source": text,
                    "status": status,
                    "seconds": round(time.monotonic() - started, 2),
                    "output": answer[:200],
                }
            )
    return rows


def probe_markdown(rows, *, generated_at: str, notes=()) -> str:
    """Render probe rows as the markdown posted to the workflow summary."""
    lines = [
        f"### Translation probe · {generated_at}",
        "",
        "| Provider | Kind | Source | Status | Seconds | Output |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for row in rows:
        source = row["source"] if len(row["source"]) <= 48 else row["source"][:47] + "…"
        output = row["output"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{row['provider']}` | {'keyless' if row['keyless'] else 'keyed'} | {source} "
            f"| {row['status']} | {row['seconds']} | {output or '—'} |"
        )
    working = [row["provider"] for row in rows if row["status"] == "ok"]
    lines += ["", f"Providers that answered: {', '.join(sorted(set(working))) or '**none**'}", ""]
    if notes:
        # Which key the run found is part of the answer: a provider that is
        # missing from the table is usually a secret that is missing too.
        lines += ["### Configuration", "", *(f"- {note}" for note in notes), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AI 深度总结 — one short, human-sounding line per article
# ---------------------------------------------------------------------------
# A card carries what the source wrote; the AI line says what it means. It runs
# on the same OpenAI-compatible endpoint the translation chain uses, writes at
# most ``SUMMARY_MAX_CHARS`` characters of Chinese, and is dropped rather than
# printed when the source is too thin, when the model declines, or when the
# answer still reads like a machine wrote it. A missing line costs the reader
# nothing — a padded one costs them the habit of reading the digest.
SUMMARY_MAX_CHARS = 100          # 一百字以内
SUMMARY_MIN_CHARS = 8            # shorter than this is a label, not a take
SUMMARY_MIN_SOURCE_CHARS = 40    # 内容太稀少就不送模型
SUMMARY_SOURCE_CHAR_LIMIT = 1500  # only the opening of the body is worth sending
SUMMARY_MAX_WORKERS = 4
SUMMARY_MAX_FAILURES = 3         # stop asking after this many failures in a row
SUMMARY_TIMEOUT = 25
SUMMARY_TEMPERATURE = 0.4        # a take may vary; a translation may not
SUMMARY_RETRIES = 2
SUMMARY_BACKOFF_SECONDS = (1.0,)
SUMMARY_SKIP_TOKEN = "SKIP"

# 去 AI 味：模型最爱复述的套话。命中就整条丢弃，宁可留空也不印一段机器腔。
AI_FLAVOR_PATTERNS = (
    "本文", "本文章", "这篇文章", "该文章", "文章介绍", "文章讲述",
    "深入探讨", "深度解析", "深度剖析", "深入剖析", "全面解析",
    "值得注意的是", "需要注意的是", "值得一提",
    "综上所述", "总而言之", "总的来说", "总结来说", "总结一下",
    "首先", "其次", "最后，", "此外，", "不仅如此", "不仅", "而且",
    "赋能", "助力", "彰显", "保驾护航",
    "引发广泛", "广泛关注", "引发热议", "备受关注", "不容错过", "不容忽视",
    "一文读懂", "带你了解", "干货满满", "前景广阔", "意义重大",
    "进一步", "显著提升", "大幅提升", "极大地",
    "小结", "总结如下", "以下是", "内容如下", "让我们一起",
)

# 引流、广告、占位文本：这类原文本身就没有可总结的内容。
TEASER_PATTERNS = (
    "read more", "continue reading", "subscribe", "sign up", "click here",
    "查看全文", "阅读全文", "阅读原文", "点击阅读", "扫码", "加入社群",
    "广告", "赞助", "sponsored",
)

# Lead-ins a model adds even when told not to; they are stripped, not fatal.
SUMMARY_LEAD_INS = ("要点", "总结", "摘要", "核心要点", "一句话总结", "结论", "AI 总结", "AI总结")

SUMMARY_SYSTEM_PROMPT = (
    "你是干了十年的一线行业编辑，在给同事写一句「这篇到底讲了什么、值不值得点开」。\n"
    f"硬性要求：简体中文，不超过 {SUMMARY_MAX_CHARS} 个字，一段话，"
    "只输出这句话本身，不要前缀、编号、引号、列表符号或换行。\n"
    "内容：说清主体、动作、关键数字和结论，让人不点开也知道发生了什么、为什么值得看。\n"
    "语气：像人写给同事的口信，短句、口语、敢下判断，专有名词和产品名保留原文。\n"
    "禁止 AI 腔：不要出现「本文/文章介绍了」「深入探讨」「值得注意的是」「综上所述」「总之」"
    "「首先…其次…最后」「不仅…而且」「赋能」「助力」「彰显」「深度解析」「引发广泛关注」"
    "「不容错过」「一文读懂」「干货满满」「前景广阔」「意义重大」「带你了解」这类套话；"
    "不要用破折号或分号罗列，不要以提问收尾，不要复述标题，不要写「点击查看」。\n"
    "不许编造原文没有的信息。\n"
    f"原文信息太少、只是引流或广告、或话题不适合概括时，只回复 {SUMMARY_SKIP_TOKEN}。"
)

SUMMARY_USER_TEMPLATE = (
    "来源：{source}\n标题：{title}\n正文：\n{body}\n\n"
    "用不超过 {limit} 个汉字写一句要点；不适合总结就只回复 {skip}。"
)


def summary_source_text(item, *, limit: int = SUMMARY_SOURCE_CHAR_LIMIT) -> str:
    """The text a summary is written from: the article body, trimmed."""
    body = strip_html((item or {}).get("summary") or "")
    return re.sub(r"\s+", " ", body).strip()[:limit]


def is_summarizable(item, *, min_chars: int = SUMMARY_MIN_SOURCE_CHARS) -> bool:
    """Whether an article carries enough text to be worth one line.

    A title standing in for a body, an echo of the headline, and a teaser that
    only asks for a click are all content the model cannot honestly summarise,
    so they never reach the endpoint.
    """
    body = summary_source_text(item)
    title = re.sub(r"\s+", " ", strip_html((item or {}).get("title") or "")).strip()
    if len(body) < min_chars:
        return False
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", body):
        return False
    if title and re.sub(r"\W+", "", body.casefold()) == re.sub(r"\W+", "", title.casefold()):
        return False
    lowered = body.casefold()
    if any(pattern in lowered for pattern in TEASER_PATTERNS) and len(body) < min_chars * 3:
        return False
    return True


def shorten_text(text: str, limit: int) -> str:
    """Cut to ``limit`` characters, preferring the last sentence boundary.

    The hard cut spends one character on the ellipsis, so the result is never
    longer than the limit the prompt was given.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    for stop in ("。", "！", "？", "；", "，"):
        index = window.rfind(stop)
        if index >= limit * 0.6:
            return window[: index + 1]
    return text[: limit - 1].rstrip() + "…"


def clean_summary(text: str, *, max_chars: int = SUMMARY_MAX_CHARS):
    """Trim a model answer into one credible line.

    Returns ``(line, outcome)`` where the line is "" whenever the card should
    print nothing, and the outcome says why: ``written``, ``empty``,
    ``declined`` (the model answered SKIP), or ``flavored`` (the answer is still
    stock AI phrasing, which is worse than no line at all).
    """
    text = re.sub(r"\s+", " ", (text or "").replace("\r", " ").replace("\n", " ")).strip()
    text = re.sub(r"^\s*(#{1,6}\s*|\*{1,2}|-+\s*)", "", text)
    # Curly quotes are asymmetric, so the pair has to be matched, not the character.
    for opener, closer in (("“", "”"), ("「", "」"), ("《", "》"), ("\"", "\""), ("'", "'"), ("‘", "’")):
        if len(text) > 2 and text.startswith(opener) and text.endswith(closer):
            text = text[len(opener): -len(closer)].strip()
    for lead in SUMMARY_LEAD_INS:
        text = re.sub(rf"^(AI\s*)?{re.escape(lead)}\s*[:：\-—]\s*", "", text)
    text = text.strip().strip("*").strip()
    if not text:
        return "", "empty"
    if re.fullmatch(r"(?i)skip\b[.!。]?", text):
        return "", "declined"
    if len(text) < SUMMARY_MIN_CHARS:
        return "", "empty"
    if any(pattern in text for pattern in AI_FLAVOR_PATTERNS):
        return "", "flavored"
    return shorten_text(text, max_chars), "written"


def resolve_summary_config():
    """The endpoint that writes the AI lines.

    The translation chain already resolves an OpenAI-compatible endpoint from
    one key, and the same key is what a reader already owns, so the summary
    reuses it. ``SUMMARY_LLM_MODEL`` picks a different model when a deeper one
    is wanted for this job. Returns ``None`` when no key is configured.
    """
    config = resolve_llm_config()
    if not config:
        return None
    override = os.environ.get("SUMMARY_LLM_MODEL", "").strip()
    if override:
        config = dict(config, model=override)
    return config


def summary_messages(item, *, max_chars: int = SUMMARY_MAX_CHARS):
    """The two messages one article is summarised from."""
    body = summary_source_text(item)
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": SUMMARY_USER_TEMPLATE.format(
                source=(item.get("source") or "").strip(),
                title=(item.get("title") or "").strip(),
                body=body,
                limit=max_chars,
                skip=SUMMARY_SKIP_TOKEN,
            ),
        },
    ]


def summarize_item(item, config, *, timeout: int = SUMMARY_TIMEOUT, max_chars: int = SUMMARY_MAX_CHARS,
                   retries: int = SUMMARY_RETRIES, backoff: tuple = SUMMARY_BACKOFF_SECONDS):
    """Ask the endpoint for one line about ``item``.

    Returns ``(line, outcome)``; a transient fault is retried once, and a
    payload that will never parse fails immediately. See ``clean_summary`` for
    the outcomes.
    """
    messages = summary_messages(item, max_chars=max_chars)
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            answer = _llm_chat(
                messages,
                timeout=timeout,
                api_key=config["key"],
                base_url=config["base_url"],
                model=config["model"],
                extra=config.get("body"),
                temperature=SUMMARY_TEMPERATURE,
            )
        except TranslationError as error:
            last_error = error
            if not error.retryable or attempt + 1 >= retries:
                raise
            time.sleep(backoff[min(attempt, len(backoff) - 1)])
            continue
        return clean_summary(answer, max_chars=max_chars)
    raise last_error  # pragma: no cover - the loop either returns or raises


def summarize_items(items, *, timeout: int = SUMMARY_TIMEOUT, concurrency: int = SUMMARY_MAX_WORKERS,
                    config=None, max_chars: int = SUMMARY_MAX_CHARS,
                    min_chars: int = SUMMARY_MIN_SOURCE_CHARS):
    """Write ``item["ai_summary"]`` for every article that earns one.

    Thin articles never reach the endpoint, a run of failures retires the
    endpoint for the rest of the issue instead of paying a timeout per card,
    and every outcome is counted so the run report can say what happened.
    """
    if config is None:
        config = resolve_summary_config()
    stats = {
        "configured": bool(config),
        "model": config["model"] if config else "",
        "endpoint": urlsplit(config["base_url"]).netloc if config else "",
        "max_chars": max_chars,
        "candidates": 0,
        "written": 0,
        "thin": len(items),
        "declined": 0,
        "flavored": 0,
        "empty": 0,
        "failed": 0,
        "last_error": "",
    }
    if not config:
        return stats

    candidates = [item for item in items if is_summarizable(item, min_chars=min_chars)]
    stats["candidates"] = len(candidates)
    stats["thin"] = len(items) - len(candidates)
    if not candidates:
        return stats

    lock = threading.Lock()
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, SUMMARY_MAX_WORKERS, len(candidates)))) as pool:
        futures = {
            pool.submit(summarize_item, item, config, timeout=timeout, max_chars=max_chars): item
            for item in candidates
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                line, outcome = future.result()
            except Exception as error:  # one bad answer must not lose the issue
                with lock:
                    stats["failed"] += 1
                    stats["last_error"] = f"{type(error).__name__}: {error}"[:160]
                    failures += 1
                if failures >= SUMMARY_MAX_FAILURES:
                    # A dead endpoint costs three timeouts, not one per card.
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                continue
            failures = 0
            with lock:
                stats[outcome] += 1
            if line:
                item["ai_summary"] = line
    return stats


def summary_report_line(stats: dict) -> str:
    """One log line naming what the AI summary did for this issue."""
    if not stats.get("configured"):
        return "ai-summary: off (no llm endpoint configured)"
    if not stats.get("candidates"):
        return "ai-summary: no article in this window carries enough text"
    return (
        f"ai-summary: written={stats['written']}/{stats['candidates']} "
        f"thin={stats['thin']} declined={stats['declined']} flavored={stats['flavored']} "
        f"failed={stats['failed']} model={stats['model']}"
    )


def summary_report_markdown(stats: dict) -> str:
    """The AI-summary section of the run report."""
    if not stats.get("configured"):
        return "\n".join(
            [
                "### AI summary",
                "",
                "Off: no OpenAI-compatible key is configured. The AI line reuses the translation "
                "chain's `llm` endpoint, so one key such as `DEEPSEEK_API_KEY` turns both on.",
                "",
            ]
        )
    rows = [
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Articles carrying enough text | {stats['candidates']} |",
        f"| Lines written | **{stats['written']}** |",
        f"| Skipped: too little source text | {stats['thin']} |",
        f"| Skipped: the model declined | {stats['declined']} |",
        f"| Dropped: stock AI phrasing | {stats['flavored']} |",
        f"| Dropped: empty answer | {stats['empty']} |",
        f"| Failed | {stats['failed']} |",
    ]
    lines = [
        "### AI summary",
        "",
        f"Wrote **{stats['written']}** of **{stats['candidates']}** candidate lines with "
        f"`{stats['model']}`"
        + (f" @ `{stats['endpoint']}`" if stats.get("endpoint") else "")
        + f", at most {stats['max_chars']} characters each.",
        "",
        *rows,
        "",
    ]
    if stats.get("last_error"):
        lines += [f"Last error: `{stats['last_error']}`", ""]
    return "\n".join(lines)


def summary_notes(stats: dict):
    """Configuration lines for the report, in the same shape as the translation notes."""
    if not stats.get("configured"):
        return [
            "`ai-summary` is off: it reuses the translation chain's `llm` endpoint, so a key such as "
            "`DEEPSEEK_API_KEY` turns it on; `SUMMARY_LLM_MODEL` then picks a different model for it."
        ]
    return [
        f"`ai-summary`: `{stats['model']}` @ `{stats['endpoint']}`, at most {stats['max_chars']} "
        f"characters per line. A line is dropped when the model answers {SUMMARY_SKIP_TOKEN}, when it "
        "writes stock AI phrasing, or when the article carries too little text to summarise."
    ]


def summary_warnings(stats: dict):
    """A run that wrote nothing is worth as much noise as a dead endpoint."""
    if not stats.get("configured") or not stats.get("candidates"):
        return []
    if stats.get("written"):
        return []
    return [
        f"ai-summary: wrote no line for any of the {stats['candidates']} candidates | "
        f"declined={stats['declined']} ai-flavored={stats['flavored']} empty={stats['empty']} "
        f"failed={stats['failed']} {stats.get('last_error', '')}"[:900]
    ]


def _summary_annotation_lines(stats: dict):
    if not stats:
        return []
    if not stats.get("configured"):
        return [("notice", "ai-summary: off (no llm endpoint configured)")]
    if not stats.get("candidates"):
        return [("notice", "ai-summary: no article in this window carries enough text to summarise")]
    lines = [("notice", summary_report_line(stats))]
    lines += [("warning", warning) for warning in summary_warnings(stats)]
    return lines


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
    OCTOPUS&nbsp;AI&nbsp;&nbsp;·&nbsp;&nbsp;章鱼 AI&nbsp;&nbsp;·&nbsp;&nbsp;{esc(kicker)}
  </div>
  <div style="margin:10px 0 0;">
    <span style="display:inline-block;background:{NEON};color:{INK};font-family:{SANS};font-size:26px;
      font-weight:800;letter-spacing:.04em;line-height:1.15;padding:2px 10px 4px;">{esc(title)}</span>
  </div>
  <div style="margin:12px 0 0;font-family:{SANS};font-size:11px;letter-spacing:.16em;color:{GRAPHITE};">
    {esc(DIGEST_SUBTITLE)}
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


def render_intro():
    return f"""
<div style="margin:0 0 20px;padding:14px 14px;background:{CARD};">
  <div style="font-family:{SANS};font-size:13px;line-height:1.85;color:{INK};letter-spacing:.01em;">
    {esc(DIGEST_INTRO)}
  </div>
  <div style="margin:10px 0 0;padding:9px 0 0;border-top:1px solid {HAIRLINE};
    font-family:{SANS};font-size:12px;line-height:1.8;color:{GRAPHITE};letter-spacing:.01em;">
    {esc(DIGEST_SCOPE)}
  </div>
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


# Card copy limits, measured in display width so Chinese and Latin lines match.
TITLE_WIDTH = 96
SUMMARY_WIDTH = 190
# A 100-character AI line can be 200 columns wide when it is all Chinese.
AI_SUMMARY_WIDTH = 208
# Marker printed before an appended translation, so machine-translated Chinese
# is never mistaken for the source's own wording.
TRANSLATION_TAG = "译"
# Marker printed before an AI-written line, so a reader always knows who wrote it.
AI_SUMMARY_TAG = "AI"


def render_translation(text, *, size, weight, line_height, chip=False):
    """Render the Chinese translation appended under an English original.

    The translated title carries the neon 译 chip; the translated summary hangs
    on a thin neon rule instead, so a card marks the translation once and keeps
    the fluorescent accent spare.
    """
    marker = (
        f'<span style="background:{NEON};color:{INK};font-family:{MONO};font-size:9px;font-weight:700;'
        f'padding:0 4px;margin-right:6px;vertical-align:1px;">{TRANSLATION_TAG}</span>'
        if chip
        else ""
    )
    rule = "" if chip else f"border-left:2px solid {NEON};padding-left:8px;"
    return (
        f'<div style="margin:5px 0 0;{rule}font-family:{SANS};font-size:{size};font-weight:{weight};'
        f'line-height:{line_height};color:{GRAPHITE};letter-spacing:.01em;">{marker}{esc(text)}</div>'
    )


def render_ai_summary(text):
    """Render the AI line: a neon chip over black copy on the neon rule.

    It sits below the source's own summary and its translation, so a card reads
    as source first, translation second, and the AI's take last — and the chip
    keeps that last line from being mistaken for something the author wrote.
    """
    marker = (
        f'<span style="background:{NEON};color:{INK};font-family:{MONO};font-size:9px;font-weight:700;'
        f'padding:0 4px;margin-right:6px;vertical-align:1px;">{AI_SUMMARY_TAG}</span>'
    )
    return (
        f'<div style="margin:8px 0 0;border-left:2px solid {NEON};padding-left:8px;'
        f'font-family:{SANS};font-size:12px;line-height:1.8;color:{INK};letter-spacing:.01em;">'
        f"{marker}{esc(text)}</div>"
    )


def render_item(index, item):
    published = item["published_at"].astimezone(CHINA)
    title = clamp(item["title"], TITLE_WIDTH)
    title_zh = clamp(item.get("title_zh", ""), TITLE_WIDTH)
    summary = clamp(item["summary"], SUMMARY_WIDTH) if item["summary"] else ""
    summary_zh = clamp(item.get("summary_zh", ""), SUMMARY_WIDTH) if item.get("summary_zh") else ""
    link = item["link"] or item.get("site_url") or ""
    heading = (
        f'<a href="{esc(link)}" style="color:{INK};text-decoration:none;">{esc(title)}</a>'
        if link
        else esc(title)
    )
    title_block = (
        f'<div style="font-family:{SANS};font-size:15px;font-weight:700;line-height:1.5;color:{INK};'
        f'letter-spacing:.01em;">{heading}</div>'
    )
    if title_zh:
        title_block += render_translation(title_zh, size="14px", weight="700", line_height="1.6", chip=True)
    summary_block = (
        f'<div style="margin:7px 0 0;font-family:{SANS};font-size:12px;line-height:1.75;color:{INK};">'
        f"{esc(summary)}</div>"
        if summary
        else ""
    )
    if summary_zh:
        summary_block += render_translation(summary_zh, size="12px", weight="400", line_height="1.75")
    ai_block = (
        render_ai_summary(clamp(item["ai_summary"], AI_SUMMARY_WIDTH))
        if item.get("ai_summary")
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
      {title_block}
      {summary_block}
      {ai_block}
      <div style="margin:9px 0 0;font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.08em;">
        <span style="background:{NEON};color:{INK};padding:1px 5px;">{esc(clamp(item['source'], 30))}</span>
        <span style="padding-left:8px;">{published.strftime('%m-%d %H:%M')}</span>
      </div>
    </td>
  </tr>
</table>
""".strip()


def render_footer(*, page_index, page_count):
    tail = (
        "本期结束 · END OF ISSUE"
        if page_index == page_count
        else f"续下页 · CONTINUED IN {page_index + 1:02d}/{page_count:02d}"
    )
    author = (
        f'<div style="margin:12px 0 0;font-family:{SANS};font-size:11px;color:{GRAPHITE};'
        f'letter-spacing:.06em;">{esc(AUTHOR_LINE)}</div>'
        if page_index == page_count
        else ""
    )
    return f"""
<div style="margin:28px 0 0;">
  {rule(INK, "2px", "0", "10px")}
  <div style="font-family:{MONO};font-size:10px;color:{GRAPHITE};letter-spacing:.14em;line-height:1.9;">
    {esc(tail)}
  </div>
  {author}
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
    ) + len(render_intro()) + len(render_footer(page_index=99, page_count=99)) + 400
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
        if index == 1:
            body += render_intro()
        body += "".join(body_blocks)
        body += render_footer(page_index=index, page_count=page_count)
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


def _annotation_lines(payload: dict):
    """Turn a translation report into GitHub Actions notice/warning commands.

    A missing translation is invisible in the push itself by design, so the run
    has to say it out loud: the notice names the provider that answered, and the
    warning names what the endpoints that did not answer actually said. Annotations
    survive on the run page and through the checks API, unlike a log line.
    """
    if "rows" in payload:  # probe report
        states = defaultdict(list)
        errors = {}
        for row in payload["rows"]:
            states[row["provider"]].append(row["status"])
            if row["status"] == "error":
                errors.setdefault(row["provider"], row["output"])
        if not states:
            return []
        summary = ", ".join(
            f"{name}={'ok' if 'ok' in results else 'failed'}" for name, results in states.items()
        )
        lines = [("notice", f"translation probe: {summary}")]
        if all("ok" not in results for results in states.values()):
            detail = "; ".join(f"{name}: {error}" for name, error in errors.items())
            lines.append(("warning", f"no translation provider answered | {detail}"[:900]))
        # The probe is the diagnostic, so its annotation carries the same
        # configuration line as its report.
        lines.append(("notice", llm_report_line()))
        return lines
    if "attempted" not in payload:  # nothing to report
        return []
    attempted = payload.get("attempted", 0)
    if not attempted:
        return []
    providers = {provider["name"]: provider for provider in payload.get("providers", [])}
    retired = payload.get("retired", [])
    line = f"translation: translated={payload.get('translated', 0)}/{attempted} provider={payload.get('active') or 'none'}"
    lines = [("notice", line)]

    def detail_for(names):
        return "; ".join(
            f"{name}: {providers.get(name, {}).get('last_error') or 'failed'}" for name in names[:4]
        )

    failing = [name for name, provider in providers.items() if provider.get("errors")]
    if not payload.get("translated"):
        lines.append(
            ("warning", f"every translation endpoint failed; the card keeps the original text | {detail_for(failing)}"[:900])
        )
    elif retired:
        lines.append(("warning", f"retired translation endpoints | {detail_for(retired)}"[:900]))
    return lines


def annotation_lines(payload: dict):
    """The report's annotations, plus anything wrong with the configuration.

    A misconfiguration is worth as much noise as a dead endpoint: a retired
    model ID or a preset whose key was never set fails exactly like a provider
    that refuses GitHub's runner addresses, and the annotation is where a reader
    finds out which one it is. The AI summary reports the same way, because a
    card that loses its AI line looks exactly like a card that never had one.
    """
    lines = _annotation_lines(payload)
    lines += _summary_annotation_lines(payload.get("ai_summary") or {})
    lines += [("warning", warning) for warning in payload.get("warnings", [])]
    return lines


def write_translation_report(out_dir, *, markdown: str, payload: dict):
    """Leave a machine-readable and a human-readable translation report behind.

    The artifact is what makes a silent failure visible: the pushplus card just
    loses the extra line, and the Actions log is easy to miss, while this file
    records which endpoint answered and what the others said. When GitHub Actions
    sets ``GITHUB_STEP_SUMMARY``, the same markdown lands on the run page, and the
    same summary is emitted as an annotation.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for level, message in annotation_lines(payload):
            print(f"::{level}::{message}")
    if out_dir:
        (out_dir / "translation-report.md").write_text(markdown + "\n", encoding="utf-8")
        (out_dir / "translation-report.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {out_dir / 'translation-report.md'}")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as handle:
                handle.write(markdown + "\n")
        except OSError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Push an RSS digest to WeChat via pushplus.")
    parser.add_argument("--catalog", default=str(ROOT / "data/feeds.json"))
    parser.add_argument("--pack", default="all", help="OPML pack to read (default: all), e.g. top200, ai, news")
    parser.add_argument("--edition", choices=["auto", "morning", "evening"], default="auto")
    parser.add_argument("--window-hours", type=float, default=0, help="0 uses the edition default")
    parser.add_argument("--max-feeds", type=int, default=0, help="0 uses every feed in the pack")
    parser.add_argument("--max-per-feed", type=int, default=3)
    parser.add_argument("--max-items", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--translate", action=argparse.BooleanOptionalAction, default=True,
                        help="append a Chinese translation under English titles and summaries (default: enabled)")
    parser.add_argument("--translators", default=os.environ.get("TRANSLATE_PROVIDERS", ""),
                        help="comma-separated provider order, e.g. llm,google,edge "
                             "(default: keyed providers first, then every keyless one)")
    parser.add_argument("--probe-translators", action="store_true",
                        help="call every provider directly, report which endpoints answer, and exit")
    parser.add_argument("--summarize", action=argparse.BooleanOptionalAction, default=True,
                        help="write an AI line under each article that carries enough text (default: enabled)")
    parser.add_argument("--summary-max-chars", type=int, default=SUMMARY_MAX_CHARS,
                        help=f"hard character limit for an AI line (default: {SUMMARY_MAX_CHARS})")
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
    stamp = local_now.strftime("%Y-%m-%d %H:%M")
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if args.probe_translators:
        providers = configured_providers(names=args.translators.split(","))
        rows = probe_providers(providers, timeout=min(args.timeout, 10))
        notes = provider_notes()
        warnings = config_warnings()
        report = probe_markdown(rows, generated_at=stamp, notes=notes)
        print(report)
        write_translation_report(
            out_dir,
            markdown=report,
            payload={"generated_at": stamp, "rows": rows, "notes": notes, "warnings": warnings},
        )
        return 0

    translator = Translator(
        configured_providers(names=args.translators.split(",")),
        timeout=min(args.timeout, 8),
    )

    feeds = load_feeds(args.catalog, args.pack, args.max_feeds)
    items, stats = collect_items(
        feeds,
        window_start=window_start,
        timeout=args.timeout,
        concurrency=args.concurrency,
        max_per_feed=args.max_per_feed,
    )
    items = items[: args.max_items]
    if args.translate:
        translate_items(items, timeout=min(args.timeout, 8), concurrency=args.concurrency, translator=translator)

    summary_stats = {"configured": False}
    if args.summarize:
        summary_stats = summarize_items(
            items,
            timeout=min(args.timeout, SUMMARY_TIMEOUT),
            concurrency=args.concurrency,
            max_chars=args.summary_max_chars,
        )
        # Same reasoning as the translation line: the card just loses the extra
        # line, so the run has to say how many lines it wrote and why the rest
        # were skipped.
        print(summary_report_line(summary_stats))

    base_title = args.title or DIGEST_TITLE
    pages = render_pages(
        items,
        title=DIGEST_TITLE,
        kicker=profile["kicker"],
        generated_at=stamp,
        window_hours=int(window_hours) if float(window_hours).is_integer() else window_hours,
        page_budget=args.page_budget,
    )

    print(
        f"digest: edition={edition} window={window_hours}h feeds={stats['feeds']} "
        f"ok={stats['ok']} failed={stats['failed']} items={len(items)} pages={len(pages)} "
        f"chars={[len(page) for page in pages]}"
    )
    if args.translate:
        # The pushplus card silently drops the appended line when translation
        # fails, so the run has to say out loud which provider answered.
        print(translator.report_line())

    if out_dir:
        for index, page in enumerate(pages, start=1):
            target = out_dir / f"digest-{edition}-{index:02d}.html"
            target.write_text(page, encoding="utf-8")
            print(f"wrote {target}")
    if args.translate or args.summarize:
        # The report is written even without --out, because the annotation is
        # what makes a translation failure visible on the run page. The notes
        # say which key and endpoint the run used, since the answer "no provider
        # answered" is usually a question about the configuration.
        notes = provider_notes()
        blocks = [translator.report_markdown()] if args.translate else []
        payload = translator.summary() if args.translate else {}
        if args.summarize:
            blocks.append(summary_report_markdown(summary_stats))
            notes += summary_notes(summary_stats)
            payload["ai_summary"] = summary_stats
        markdown = "\n\n".join(blocks)
        if notes:
            markdown += "\n\n### Configuration\n\n" + "\n".join(f"- {note}" for note in notes)
        payload["notes"] = notes
        payload["warnings"] = config_warnings() + (summary_warnings(summary_stats) if args.summarize else [])
        write_translation_report(out_dir, markdown=markdown, payload=payload)

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
