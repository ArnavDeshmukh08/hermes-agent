"""Tests for the Garmin health query intent — no real network calls, no LLM."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure both the router directory and the project root are on the path.
_ROOT = Path(__file__).resolve().parents[1]
_ROUTER_DIR = _ROOT / "hooks" / "jack_router"
for _p in (str(_ROOT), str(_ROUTER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import jack_intent_router as router  # noqa: E402
from integrations.garmin_chat import (  # noqa: E402
    classify_health_query,
    format_garmin_for_chat,
    format_sleep,
    format_stats,
)


# ---------------------------------------------------------------------------
# 1. Intent routing — health phrases → health_query
# ---------------------------------------------------------------------------

class TestHealthQueryRouting(unittest.TestCase):
    def _assert_health(self, text: str) -> None:
        r = router.classify(text)
        self.assertIsNotNone(r, text)
        self.assertEqual(r.intent, "health_query", f"{text!r} → {r.intent!r}")

    def test_how_did_i_sleep(self):
        self._assert_health("how did I sleep")

    def test_my_steps_today(self):
        self._assert_health("my steps today")

    def test_show_me_garmin_data(self):
        self._assert_health("show me my garmin data")

    def test_body_battery(self):
        self._assert_health("body battery")

    def test_how_active_was_i(self):
        self._assert_health("how active was I today")

    def test_my_heart_rate(self):
        self._assert_health("my heart rate")

    def test_my_workouts_today(self):
        self._assert_health("my workouts today")

    def test_garmin_stats(self):
        self._assert_health("garmin stats")


# ---------------------------------------------------------------------------
# 2. No false positives — non-health phrases must NOT route to health_query
# ---------------------------------------------------------------------------

class TestHealthQueryNoFalsePositives(unittest.TestCase):
    def test_how_am_i_doing_is_goal_or_conversational(self):
        r = router.classify("how am I doing")
        self.assertIsNotNone(r)
        self.assertNotEqual(r.intent, "health_query", "\"how am I doing\" must not match health_query")


# ---------------------------------------------------------------------------
# 3. format_sleep
# ---------------------------------------------------------------------------

class TestFormatSleep(unittest.TestCase):
    def test_full_sleep_dict(self):
        result = format_sleep({"total_sleep_h": 7.2, "deep_h": 1.8, "rem_h": 1.4, "score": 74})
        self.assertIn("7.2", result)
        self.assertIn("score 74", result)

    def test_none_returns_empty_string(self):
        self.assertEqual(format_sleep(None), "")

    def test_no_score_omits_score_part(self):
        result = format_sleep({"total_sleep_h": 6.5, "deep_h": 1.2, "rem_h": 1.0, "score": None})
        self.assertIn("6.5", result)
        self.assertNotIn("score", result)

    def test_rem_included(self):
        result = format_sleep({"total_sleep_h": 7.0, "deep_h": 1.5, "rem_h": 1.4, "score": 70})
        self.assertIn("1.4", result)
        self.assertIn("REM", result)


# ---------------------------------------------------------------------------
# 4. format_stats
# ---------------------------------------------------------------------------

class TestFormatStats(unittest.TestCase):
    def test_steps_formatted_with_comma(self):
        lines = format_stats({"steps": 8432, "body_battery_high": 82})
        joined = "\n".join(lines)
        self.assertIn("8,432", joined)
        self.assertIn("82", joined)

    def test_none_returns_empty_list(self):
        self.assertEqual(format_stats(None), [])

    def test_all_fields(self):
        lines = format_stats({"steps": 9000, "body_battery_high": 90, "calories": 450, "stress_avg": 25})
        joined = "\n".join(lines)
        self.assertIn("9,000", joined)
        self.assertIn("90", joined)
        self.assertIn("450", joined)
        self.assertIn("25", joined)

    def test_partial_stats(self):
        # Only steps present — other lines omitted gracefully.
        lines = format_stats({"steps": 5000})
        self.assertEqual(len(lines), 1)
        self.assertIn("5,000", lines[0])


# ---------------------------------------------------------------------------
# 5. format_garmin_for_chat
# ---------------------------------------------------------------------------

class TestFormatGarminForChat(unittest.TestCase):
    def test_none_returns_empty_string(self):
        self.assertEqual(format_garmin_for_chat(None), "")

    def test_full_summary_non_empty(self):
        summary = {
            "sleep": {"total_sleep_h": 7.5, "deep_h": 2.0, "rem_h": 1.5, "score": 78},
            "stats": {"steps": 9000},
        }
        result = format_garmin_for_chat(summary)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_full_summary_contains_sleep_and_steps(self):
        summary = {
            "sleep": {"total_sleep_h": 7.5, "deep_h": 2.0, "rem_h": 1.5, "score": 78},
            "stats": {"steps": 9000},
        }
        result = format_garmin_for_chat(summary)
        self.assertIn("7.5", result)
        self.assertIn("9,000", result)

    def test_none_sleep_only_stats(self):
        summary = {"sleep": None, "stats": {"steps": 3000}}
        result = format_garmin_for_chat(summary)
        self.assertIn("3,000", result)

    def test_none_stats_only_sleep(self):
        summary = {"sleep": {"total_sleep_h": 6.0, "deep_h": 1.0, "rem_h": 0.8, "score": 65}, "stats": None}
        result = format_garmin_for_chat(summary)
        self.assertIn("6.0", result)


# ---------------------------------------------------------------------------
# 6. Handler — honest failure when Mac service is down (returns None)
# ---------------------------------------------------------------------------

def _run_async(coro):
    return asyncio.run(coro)


class TestRunHealthMacDown(unittest.TestCase):
    def _make_message(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        message = MagicMock()
        message.channel = channel
        return message

    def test_honest_failure_message_when_garmin_returns_none(self):
        """When get_daily_summary returns None (general query), we report honest failure.

        Uses "my garmin" which routes to the 'full' path (get_daily_summary), NOT
        the sleep path. Sleep-specific Mac-down is covered by TestSleepFallbackToYesterday.
        """
        import handler as h  # imported after sys.path is set up

        message = self._make_message()
        # "my garmin" → classify_health_query → "full" → get_daily_summary
        route = router.Route("health_query", {"text": "my garmin"})

        mock_client = MagicMock()
        mock_client.get_daily_summary.return_value = None

        with patch("integrations.garmin.GarminClient", return_value=mock_client):
            _run_async(h._run_health(message, route))

        call_args = message.channel.send.call_args[0][0]

        # Must NOT contain hallucinated setup steps or wrong service names
        forbidden = ("api key", "fitbit", "plugin", "connector", "setup")
        for word in forbidden:
            self.assertNotIn(word, call_args.lower(), f"Fabricated word {word!r} found in failure message")

        # Must use honest failure language
        honest_words = ("can't reach", "down", "unreachable")
        found_honest = any(w in call_args.lower() for w in honest_words)
        self.assertTrue(found_honest, f"Honest failure language not found in: {call_args!r}")


# ---------------------------------------------------------------------------
# 7. Handler — real data returned and formatted correctly
# ---------------------------------------------------------------------------

class TestRunHealthRealData(unittest.TestCase):
    def _make_message(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        message = MagicMock()
        message.channel = channel
        return message

    def test_real_data_sent_to_channel(self):
        """When get_daily_summary returns valid data, channel.send receives a formatted message.

        _voice is patched to return the fallback_plain (the pre-composed formatted string)
        so this test validates data routing, not the personality layer output.
        """
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "garmin stats"})

        fake_summary = {
            "sleep": {"total_sleep_h": 7.5, "deep_h": 2.0, "rem_h": 1.5, "score": 78},
            "stats": {"steps": 9000},
        }

        mock_client = MagicMock()
        mock_client.get_daily_summary.return_value = fake_summary

        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=lambda i, d, u, f: f):
            _run_async(h._run_health(message, route))

        message.channel.send.assert_called_once()
        sent = message.channel.send.call_args[0][0]
        self.assertIn("7.5", sent)
        self.assertIn("9,000", sent)


# ---------------------------------------------------------------------------
# 8. classify_health_query — routes to correct data path
# ---------------------------------------------------------------------------

class TestClassifyHealthQuery(unittest.TestCase):
    def test_sleep_phrases_classified_as_sleep(self):
        for phrase in [
            "how did I sleep",
            "my sleep score",
            "how was my sleep",
            "sleep data",
            "analyze my sleep",
            "last night's sleep",
        ]:
            self.assertEqual(classify_health_query(phrase), "sleep", f"Expected 'sleep' for: {phrase!r}")

    def test_stats_phrases_classified_as_stats(self):
        for phrase in [
            "how many steps today",
            "my steps today",
            "body battery",
            "my heart rate",
            "my stress level",
            "calories today",
            "how active was I",
        ]:
            self.assertEqual(classify_health_query(phrase), "stats", f"Expected 'stats' for: {phrase!r}")

    def test_general_phrases_classified_as_full(self):
        for phrase in [
            "my garmin",
            "garmin data",
            "garmin stats",
            "health data",
        ]:
            self.assertEqual(classify_health_query(phrase), "full", f"Expected 'full' for: {phrase!r}")

    def test_empty_string_returns_full(self):
        self.assertEqual(classify_health_query(""), "full")


# ---------------------------------------------------------------------------
# 9. Sleep fallback to yesterday when today is empty
# ---------------------------------------------------------------------------

class TestSleepFallbackToYesterday(unittest.TestCase):
    def _make_message(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        message = MagicMock()
        message.channel = channel
        return message

    def test_falls_back_to_yesterday_when_today_empty(self):
        """_voice patched to return fallback_plain — tests data routing not personality layer."""
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "how did I sleep"})

        yesterday_sleep = {"total_sleep_h": 7.0, "deep_h": 1.5, "rem_h": 1.2, "score": 72}

        mock_client = MagicMock()
        # First call (today) returns None, second call (yesterday) returns data
        mock_client.last_night_sleep.side_effect = [None, yesterday_sleep]

        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=lambda i, d, u, f: f):
            asyncio.run(h._run_health(message, route))

        sent = message.channel.send.call_args[0][0]
        self.assertIn("7.0", sent)
        # Should mention "night of" since we fell back
        self.assertIn("night of", sent.lower())

    def test_honest_message_when_both_dates_empty(self):
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "how did I sleep"})

        mock_client = MagicMock()
        mock_client.last_night_sleep.return_value = None  # both today and yesterday empty

        with patch("integrations.garmin.GarminClient", return_value=mock_client):
            asyncio.run(h._run_health(message, route))

        sent = message.channel.send.call_args[0][0].lower()
        # Must use honest language, no fabrication
        self.assertTrue(
            "no sleep data" in sent or "not synced" in sent or "sync" in sent,
            f"Expected honest sync message, got: {sent!r}",
        )
        # Must NOT fabricate setup steps
        for word in ("api key", "fitbit", "plugin", "connector", "setup"):
            self.assertNotIn(word, sent)


# ---------------------------------------------------------------------------
# 10. Steps query returns the actual number
# ---------------------------------------------------------------------------

class TestStepsQueryReturnsNumber(unittest.TestCase):
    def _make_message(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        message = MagicMock()
        message.channel = channel
        return message

    def test_steps_query_returns_count(self):
        """_voice patched to return fallback_plain — tests data routing not personality layer."""
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "how many steps today"})

        mock_client = MagicMock()
        mock_client.get_stats.return_value = {"steps": 43, "calories": 120}

        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=lambda i, d, u, f: f):
            asyncio.run(h._run_health(message, route))

        sent = message.channel.send.call_args[0][0]
        # Must contain the actual step count
        self.assertIn("43", sent)
        # Must NOT tell Arnav to rephrase
        self.assertNotIn("just ask", sent.lower())
        self.assertNotIn("try asking", sent.lower())

    def test_sleep_question_calls_last_night_sleep_not_get_daily_summary(self):
        """Sleep queries use last_night_sleep(), not get_daily_summary().

        _voice patched to return fallback_plain — tests data routing not personality layer.
        """
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "how did I sleep"})

        sleep_data = {"total_sleep_h": 8.0, "deep_h": 2.0, "rem_h": 1.5, "score": 80}
        mock_client = MagicMock()
        mock_client.last_night_sleep.return_value = sleep_data

        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=lambda i, d, u, f: f):
            asyncio.run(h._run_health(message, route))

        # last_night_sleep was called, get_daily_summary was NOT
        mock_client.last_night_sleep.assert_called()
        mock_client.get_daily_summary.assert_not_called()

        sent = message.channel.send.call_args[0][0]
        self.assertIn("8.0", sent)

    def test_stats_question_calls_get_stats_not_get_daily_summary(self):
        """Stats queries use get_stats(), not get_daily_summary().

        _voice patched to return fallback_plain — tests data routing not personality layer.
        """
        import handler as h

        message = self._make_message()
        route = router.Route("health_query", {"text": "my steps today"})

        mock_client = MagicMock()
        mock_client.get_stats.return_value = {"steps": 1234, "body_battery_high": 90}

        with patch("integrations.garmin.GarminClient", return_value=mock_client), \
             patch("handler._voice", side_effect=lambda i, d, u, f: f):
            asyncio.run(h._run_health(message, route))

        mock_client.get_stats.assert_called()
        mock_client.get_daily_summary.assert_not_called()

        sent = message.channel.send.call_args[0][0]
        self.assertIn("1,234", sent)


if __name__ == "__main__":
    unittest.main()
