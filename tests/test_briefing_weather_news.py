"""Tests for briefing.weather and briefing.news, and their wiring in morning.py.

No real network, no real LLM, no real DDGS — everything is mocked or injected.
Clock is injected; env vars are cleaned up in tearDown.
"""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on sys.path (mirrors test_briefing_upgraded.py).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from briefing.morning import MorningBriefing  # noqa: E402
from briefing.news import NewsFetcher, _parse_json_list  # noqa: E402
from briefing.weather import WeatherFetcher  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 20, 6, 0, tzinfo=timezone.utc)

# Canned wttr.in j1 JSON response.
_WTTR_PAYLOAD = json.dumps(
    {
        "current_condition": [
            {
                "temp_C": "28",
                "FeelsLikeC": "31",
                "weatherDesc": [{"value": "Partly cloudy"}],
                "humidity": "72",
            }
        ]
    }
).encode()


class FakeStore:
    """In-memory store: list_pending returns a fixed list."""

    def __init__(self, pending=()):
        self._pending = list(pending)

    def list_pending(self, user_id):
        return list(self._pending)


def _silent_log(briefing: MorningBriefing) -> None:
    briefing._log = lambda line: None  # type: ignore[method-assign]


def _make_briefing(complete_fn=None):
    """Return (MorningBriefing, captured_dict) — no store, no network."""
    captured: dict = {}

    def _capture(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return "good morning from Jack"

    b = MorningBriefing(
        user_path="/nonexistent/USER.md",
        store=FakeStore(),
        complete_fn=complete_fn or _capture,
    )
    _silent_log(b)
    return b, captured


# ---------------------------------------------------------------------------
# WeatherFetcher unit tests
# ---------------------------------------------------------------------------


class TestWeatherFetcherSuccess(unittest.TestCase):
    """WeatherFetcher.get_weather parses the wttr.in j1 payload correctly."""

    def setUp(self):
        os.environ.pop("JACK_WEATHER_LOCATION", None)

    def tearDown(self):
        os.environ.pop("JACK_WEATHER_LOCATION", None)

    def _fake_urlopen(self, req, timeout=None):
        """Return a context-manager-compatible fake response with canned bytes."""

        class _FakeResp:
            def read(self_inner):
                return _WTTR_PAYLOAD

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                pass

        return _FakeResp()

    def test_get_weather_returns_expected_fields(self):
        with patch("urllib.request.urlopen", self._fake_urlopen):
            w = WeatherFetcher().get_weather("Ichalkaranji, India")

        self.assertIsNotNone(w)
        self.assertEqual(w["temp_c"], 28)
        self.assertEqual(w["feels_like_c"], 31)
        self.assertEqual(w["condition"], "Partly cloudy")
        self.assertEqual(w["humidity"], 72)
        self.assertEqual(w["location"], "Ichalkaranji")

    def test_get_weather_uses_env_location_default(self):
        os.environ["JACK_WEATHER_LOCATION"] = "Mumbai, India"
        captured_urls = []

        def _capture_url(req, timeout=None):
            captured_urls.append(req.full_url)

            class _FakeResp:
                def read(self_inner):
                    return _WTTR_PAYLOAD

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *args):
                    pass

            return _FakeResp()

        with patch("urllib.request.urlopen", _capture_url):
            WeatherFetcher().get_weather()

        self.assertTrue(any("Mumbai" in u for u in captured_urls))

    def test_summary_text_formats_correctly(self):
        with patch("urllib.request.urlopen", self._fake_urlopen):
            text = WeatherFetcher().summary_text("Ichalkaranji, India")

        self.assertIn("28°C", text)
        self.assertIn("Partly cloudy", text)
        self.assertIn("Ichalkaranji", text)
        self.assertIn("31°C", text)


class TestWeatherFetcherFailure(unittest.TestCase):
    """WeatherFetcher returns None / '' and never raises on failures."""

    def tearDown(self):
        os.environ.pop("JACK_WEATHER_LOCATION", None)

    def test_get_weather_returns_none_on_network_error(self):
        def _boom(req, timeout=None):
            raise OSError("network unreachable")

        with patch("urllib.request.urlopen", _boom):
            result = WeatherFetcher().get_weather("Ichalkaranji, India")

        self.assertIsNone(result)

    def test_get_weather_returns_none_on_bad_json(self):
        def _bad_json(req, timeout=None):
            class _Resp:
                def read(self_inner):
                    return b"not json {{"

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *args):
                    pass

            return _Resp()

        with patch("urllib.request.urlopen", _bad_json):
            result = WeatherFetcher().get_weather("Ichalkaranji, India")

        self.assertIsNone(result)

    def test_get_weather_returns_none_on_missing_keys(self):
        def _missing(req, timeout=None):
            class _Resp:
                def read(self_inner):
                    return json.dumps({"current_condition": [{}]}).encode()

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *args):
                    pass

            return _Resp()

        with patch("urllib.request.urlopen", _missing):
            result = WeatherFetcher().get_weather("Ichalkaranji, India")

        self.assertIsNone(result)

    def test_summary_text_returns_empty_on_failure(self):
        def _boom(req, timeout=None):
            raise ConnectionError("timeout")

        with patch("urllib.request.urlopen", _boom):
            text = WeatherFetcher().summary_text("Ichalkaranji, India")

        self.assertEqual(text, "")


# ---------------------------------------------------------------------------
# NewsFetcher unit tests
# ---------------------------------------------------------------------------

_RAW_ITEMS = [
    {
        "title": "Anthropic releases Claude 4 with improved agentic capabilities",
        "body": "Major update improves tool use and multi-step reasoning.",
        "url": "https://example.com/claude4",
    },
    {
        "title": "India SaaS startup raises Series A",
        "body": "A Bengaluru-based SaaS startup raised $5M for expansion.",
        "url": "https://example.com/saas",
    },
    {
        "title": "Generic tech headline not relevant",
        "body": "Some unrelated article.",
        "url": "https://example.com/other",
    },
]

_CURATED_JSON = json.dumps(
    [
        {
            "headline": "Anthropic releases Claude 4",
            "summary_one_line": "Improved agentic capabilities and tool use.",
            "why_relevant": "Directly relevant to Jack and AI agent work.",
        }
    ]
)


class TestNewsFetcherSuccess(unittest.TestCase):
    """NewsFetcher filters raw items via the injected LLM and returns dicts."""

    def _make_search(self, items=None):
        items = items if items is not None else _RAW_ITEMS

        def _search(query, max_results=10):
            return items

        return _search

    def _make_complete(self, response=_CURATED_JSON):
        def _complete(system, user, **kwargs):
            return response

        return _complete

    def test_get_curated_news_returns_list_of_dicts(self):
        fetcher = NewsFetcher(
            search_fn=self._make_search(),
            complete_fn=self._make_complete(),
        )
        result = fetcher.get_curated_news()

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIn("headline", result[0])
        self.assertIn("summary_one_line", result[0])
        self.assertIn("why_relevant", result[0])

    def test_get_curated_news_passes_items_to_llm(self):
        captured = {}

        def _capture_complete(system, user, **kwargs):
            captured["user"] = user
            return _CURATED_JSON

        fetcher = NewsFetcher(
            search_fn=self._make_search(),
            complete_fn=_capture_complete,
        )
        fetcher.get_curated_news()

        # All raw titles should appear in the user prompt sent to the LLM.
        self.assertIn("Anthropic", captured["user"])

    def test_summary_text_renders_items(self):
        fetcher = NewsFetcher(
            search_fn=self._make_search(),
            complete_fn=self._make_complete(),
        )
        text = fetcher.summary_text()

        self.assertIsInstance(text, str)
        self.assertIn("Anthropic", text)

    def test_summary_text_empty_when_no_relevant_items(self):
        fetcher = NewsFetcher(
            search_fn=self._make_search(),
            complete_fn=self._make_complete(response="[]"),
        )
        text = fetcher.summary_text()

        self.assertEqual(text, "")


class TestNewsFetcherFailures(unittest.TestCase):
    """NewsFetcher returns [] and never raises on any failure."""

    def test_returns_empty_when_search_raises(self):
        def _boom(query, max_results=10):
            raise RuntimeError("ddgs not installed")

        fetcher = NewsFetcher(
            search_fn=_boom,
            complete_fn=lambda s, u, **k: _CURATED_JSON,
        )
        result = fetcher.get_curated_news()
        self.assertEqual(result, [])

    def test_returns_empty_when_llm_raises(self):
        def _boom(system, user, **kwargs):
            raise ConnectionError("groq down")

        fetcher = NewsFetcher(
            search_fn=lambda q, max_results=10: _RAW_ITEMS,
            complete_fn=_boom,
        )
        result = fetcher.get_curated_news()
        self.assertEqual(result, [])

    def test_returns_empty_when_llm_returns_bad_json(self):
        fetcher = NewsFetcher(
            search_fn=lambda q, max_results=10: _RAW_ITEMS,
            complete_fn=lambda s, u, **k: "not valid json }{",
        )
        result = fetcher.get_curated_news()
        self.assertEqual(result, [])

    def test_returns_empty_when_llm_returns_empty_list(self):
        fetcher = NewsFetcher(
            search_fn=lambda q, max_results=10: _RAW_ITEMS,
            complete_fn=lambda s, u, **k: "[]",
        )
        result = fetcher.get_curated_news()
        self.assertEqual(result, [])

    def test_returns_empty_when_no_raw_items(self):
        fetcher = NewsFetcher(
            search_fn=lambda q, max_results=10: [],
            complete_fn=lambda s, u, **k: _CURATED_JSON,
        )
        result = fetcher.get_curated_news()
        self.assertEqual(result, [])


class TestParseJsonList(unittest.TestCase):
    """_parse_json_list handles edge cases."""

    def test_parses_clean_json(self):
        result = _parse_json_list('[{"headline": "X", "summary_one_line": "Y", "why_relevant": "Z"}]')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["headline"], "X")

    def test_strips_markdown_fences(self):
        text = '```json\n[{"headline": "A"}]\n```'
        result = _parse_json_list(text)
        self.assertEqual(len(result), 1)

    def test_returns_empty_on_bad_json(self):
        self.assertEqual(_parse_json_list("not json"), [])

    def test_returns_empty_on_empty_list(self):
        self.assertEqual(_parse_json_list("[]"), [])

    def test_returns_empty_on_non_list(self):
        self.assertEqual(_parse_json_list('{"key": "val"}'), [])


# ---------------------------------------------------------------------------
# MorningBriefing integration tests — weather + news wired in
# ---------------------------------------------------------------------------


class TestBriefingWeatherEnabled(unittest.TestCase):
    """Weather block reaches the LLM prompt when JACK_WEATHER_ENABLED=1."""

    def setUp(self):
        os.environ["JACK_WEATHER_ENABLED"] = "1"
        os.environ.pop("JACK_NEWS_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_WEATHER_ENABLED", None)
        os.environ.pop("JACK_WEATHER_LOCATION", None)
        os.environ.pop("JACK_NEWS_ENABLED", None)

    def test_weather_appears_in_llm_prompt(self):
        b, captured = _make_briefing()

        with patch.object(b, "_weather_block", return_value="28°C and Partly cloudy in Ichalkaranji (feels like 31°C)"):
            b.compile_briefing("42", now=_NOW)

        self.assertIn("Weather today", captured["user"])
        self.assertIn("28°C", captured["user"])

    def test_briefing_still_returns_string_with_weather(self):
        b, _ = _make_briefing()

        with patch.object(b, "_weather_block", return_value="28°C and Sunny in Ichalkaranji (feels like 30°C)"):
            out = b.compile_briefing("42", now=_NOW)

        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)


class TestBriefingWeatherDisabled(unittest.TestCase):
    """Weather block is absent from prompt when JACK_WEATHER_ENABLED is unset/false."""

    def setUp(self):
        os.environ.pop("JACK_WEATHER_ENABLED", None)
        os.environ.pop("JACK_NEWS_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_WEATHER_ENABLED", None)
        os.environ.pop("JACK_NEWS_ENABLED", None)

    def test_weather_absent_from_prompt_when_disabled(self):
        b, captured = _make_briefing()
        b.compile_briefing("42", now=_NOW)

        self.assertNotIn("Weather today", captured.get("user", ""))

    def test_weather_block_returns_empty_when_env_unset(self):
        b, _ = _make_briefing()
        self.assertEqual(b._weather_block(), "")

    def test_weather_block_returns_empty_on_falsy_values(self):
        b, _ = _make_briefing()
        for falsy in ("0", "false", "no", "off", ""):
            with self.subTest(value=falsy):
                os.environ["JACK_WEATHER_ENABLED"] = falsy
                self.assertEqual(b._weather_block(), "")
        os.environ.pop("JACK_WEATHER_ENABLED", None)

    def test_weather_outage_does_not_block_briefing(self):
        """_weather_block swallows WeatherFetcher exceptions."""
        os.environ["JACK_WEATHER_ENABLED"] = "1"

        # Inject a fake briefing.weather module whose WeatherFetcher raises.
        fake_mod = types.ModuleType("briefing.weather")

        class _BoomFetcher:
            def summary_text(self, location=None):
                raise ConnectionError("wttr.in down")

        fake_mod.WeatherFetcher = _BoomFetcher
        b, _ = _make_briefing()

        with patch.dict(sys.modules, {"briefing.weather": fake_mod}):
            result = b._weather_block()

        self.assertEqual(result, "")
        os.environ.pop("JACK_WEATHER_ENABLED", None)


class TestBriefingNewsEnabled(unittest.TestCase):
    """News block reaches the LLM prompt when JACK_NEWS_ENABLED=1."""

    def setUp(self):
        os.environ["JACK_NEWS_ENABLED"] = "1"
        os.environ.pop("JACK_WEATHER_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_NEWS_ENABLED", None)
        os.environ.pop("JACK_WEATHER_ENABLED", None)

    def test_news_appears_in_llm_prompt(self):
        b, captured = _make_briefing()
        news_text = "Anthropic releases Claude 4 — Improved agentic capabilities. (relevant to Jack)"

        with patch.object(b, "_news_block", return_value=news_text):
            b.compile_briefing("42", now=_NOW)

        self.assertIn("Relevant news", captured["user"])
        self.assertIn("Anthropic", captured["user"])

    def test_briefing_still_returns_string_with_news(self):
        b, _ = _make_briefing()
        news_text = "Some news item here."

        with patch.object(b, "_news_block", return_value=news_text):
            out = b.compile_briefing("42", now=_NOW)

        self.assertIsInstance(out, str)


class TestBriefingNewsDisabled(unittest.TestCase):
    """News block is absent from prompt when JACK_NEWS_ENABLED is unset/false."""

    def setUp(self):
        os.environ.pop("JACK_NEWS_ENABLED", None)
        os.environ.pop("JACK_WEATHER_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_NEWS_ENABLED", None)
        os.environ.pop("JACK_WEATHER_ENABLED", None)

    def test_news_absent_from_prompt_when_disabled(self):
        b, captured = _make_briefing()
        b.compile_briefing("42", now=_NOW)

        self.assertNotIn("Relevant news", captured.get("user", ""))

    def test_news_block_returns_empty_when_env_unset(self):
        b, _ = _make_briefing()
        self.assertEqual(b._news_block(), "")

    def test_news_outage_does_not_block_briefing(self):
        """_news_block swallows NewsFetcher exceptions."""
        os.environ["JACK_NEWS_ENABLED"] = "1"

        fake_mod = types.ModuleType("briefing.news")

        class _BoomFetcher:
            def summary_text(self):
                raise RuntimeError("ddgs not installed")

        fake_mod.NewsFetcher = _BoomFetcher
        b, _ = _make_briefing()

        with patch.dict(sys.modules, {"briefing.news": fake_mod}):
            result = b._news_block()

        self.assertEqual(result, "")
        os.environ.pop("JACK_NEWS_ENABLED", None)


class TestBriefingGracefulWithoutWeatherOrNews(unittest.TestCase):
    """Briefing reads exactly as before when both weather and news are disabled."""

    def setUp(self):
        os.environ.pop("JACK_WEATHER_ENABLED", None)
        os.environ.pop("JACK_NEWS_ENABLED", None)
        os.environ.pop("JACK_GARMIN_ENABLED", None)
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def tearDown(self):
        os.environ.pop("JACK_WEATHER_ENABLED", None)
        os.environ.pop("JACK_NEWS_ENABLED", None)
        os.environ.pop("JACK_GARMIN_ENABLED", None)
        os.environ.pop("JACK_CALENDAR_ENABLED", None)

    def test_prompt_has_no_weather_or_news_sections(self):
        b, captured = _make_briefing()
        b.compile_briefing("42", now=_NOW)

        user = captured.get("user", "")
        self.assertNotIn("Weather today", user)
        self.assertNotIn("Relevant news", user)

    def test_briefing_returns_string(self):
        b, _ = _make_briefing()
        out = b.compile_briefing("42", now=_NOW)
        self.assertIsInstance(out, str)
        self.assertTrue(len(out) > 0)

    def test_both_blocks_empty_when_both_disabled(self):
        b, _ = _make_briefing()
        self.assertEqual(b._weather_block(), "")
        self.assertEqual(b._news_block(), "")


if __name__ == "__main__":
    unittest.main()
