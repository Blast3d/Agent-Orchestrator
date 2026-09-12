from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
"""Regression checks for quota orientation, incomplete displays and freshness."""
import unittest
from quota_tui import parse_grok_screen, parse_claude_screen


def screen(used=0):
    return ("Grok Build  v1.0.13\n"
            "Session usage: no model calls yet in this session.\n"
            f"Weekly limit: {used}%\n"
            "Next reset: September 9, 20:14\n")


class GrokQuotaParserTests(unittest.TestCase):
    def test_remaining_is_conservative_and_not_used_percentage(self):
        for used, remaining in [(0, 99), (79, 20), (97, 2), (98, 1), (99, 0), (100, 0)]:
            with self.subTest(used=used):
                value = parse_grok_screen(screen(used))["windows"][0]
                self.assertEqual(value["remaining_pct"], remaining)
                self.assertIsNone(value["reset_at"])

    def test_incomplete_or_ambiguous_displays_are_rejected(self):
        bad = [screen(101), screen(-1), screen("98.5"),
               screen().replace("20:14", "20:"),
               screen().replace("Session usage: no model calls yet in this session.", ""),
               screen().replace("v1.0.13", "v1.0.14"),
               screen() + "Weekly limit: 2%\nNext reset: September 9, 20:14\n",
               screen() + "Couldn't load usage: network error\n",
               screen() + "Loading usage...\n",
               screen() + "Cached data\n",
               screen() + "Context usage  Usage limit  Session info\n"]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_grok_screen(value)

    def test_observation_time_is_consistent(self):
        stamp = "2026-09-08T03:00:00Z"
        reading = parse_grok_screen(screen(), stamp)
        self.assertEqual(reading["observed_at"], stamp)
        self.assertEqual(reading["windows"][0]["observed_at"], stamp)
        self.assertEqual(reading["windows"][0]["max_age_seconds"], 600)


def claude_screen():
    # Basic labeled-window fixture, with separate tests for the live renderer.
    return ("Usage\nCurrent session\n[===] 97% used\nResets 4pm (America/Denver)\n"
            "Current week (all models)\n[===] 79% used\nResets Sep 9 at 4pm (America/Denver)\n"
            "Current week (Sonnet only)\n[===] 10% used\nResets Sep 9 at 4pm (America/Denver)\n")


class ClaudeQuotaParserTests(unittest.TestCase):
    def test_live_screen_reader_labels_exclude_local_analytics_and_credits(self):
        display = ("Session\nUsage: 0 input, 0 output, 0 cache read, 0 cache write\n"
            "Current session\n29% 29% used\nResets 3:40pm (America/Denver)\n"
            "Current week (all models)\n2% 2% used\nResets Sep 12, 1pm (America/Denver)\n"
            "+50% weekly limits promo through Sep 13\n"
            "What's contributing to your limits usage?\nLocal sessions: 80% used\n"
            "Usage credits\nUnlimited\n")
        result = parse_claude_screen(display, cli_version="2.1.263")
        self.assertEqual([w['remaining_pct'] for w in result['windows']], [70, 97])
        self.assertEqual(result['windows'][0]['reset_display'], '3:40pm (America/Denver)')
        with self.assertRaises(ValueError):
            parse_claude_screen(display + 'Refreshing…\n', cli_version="2.1.263")

    def test_account_and_model_windows_stay_separate(self):
        result = parse_claude_screen(claude_screen(), cli_version="fixture")
        self.assertEqual([w["id"] for w in result["windows"]],
                         ["claude-five-hour", "claude-seven-day", "claude-seven-day-sonnet"])
        self.assertEqual([w["remaining_pct"] for w in result["windows"]], [2, 20, 89])

    def test_context_and_ambiguous_or_incomplete_bars_are_rejected(self):
        bad = ["Context window: 97% used\nResets tomorrow\n",
               claude_screen().replace("Current week (all models)", "Weekly context"),
               claude_screen().replace("97% used", "97% remaining"),
               claude_screen() + "Loading usage...\n",
               claude_screen() + "Failed to load current usage\n",
               claude_screen() + "Cached usage\n",
               claude_screen().replace("97% used", "101% used"),
               claude_screen().replace("97% used", "97.5% used"),
               claude_screen() + claude_screen(),
               claude_screen().replace("Resets 4pm (America/Denver)\n", "")]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_claude_screen(value, cli_version="fixture")


if __name__ == "__main__":
    unittest.main()
