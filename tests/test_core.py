from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig, FeedConfig, load_config
from core.dashboard import render_dashboard
from core.monitor import find_ready_file, run_monitor
from core.state import default_feed_state, load_state, save_state
from core.utils import (
    previous_month_last_day,
    render_filename_template,
    same_day_previous_month,
)


class DateAndTemplateTests(unittest.TestCase):
    def test_previous_month_rules(self) -> None:
        self.assertEqual(previous_month_last_day(date(2026, 8, 4)), date(2026, 7, 31))
        self.assertEqual(previous_month_last_day(date(2026, 9, 3)), date(2026, 8, 31))
        self.assertEqual(same_day_previous_month(date(2026, 3, 31)), date(2026, 2, 28))

    def test_render_filename_template(self) -> None:
        business_date = date(2026, 7, 31)
        self.assertEqual(
            render_filename_template("customer_{yyyymmdd}_{yyyy-mm-dd}_{yy}.csv", business_date),
            "customer_20260731_2026-07-31_26.csv",
        )


class ConfigTests(unittest.TestCase):
    def test_load_config_ignores_comment_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                "\n".join(
                    [
                        "// top-level usage notes",
                        "{",
                        '  "check_interval_seconds": 10,',
                        '  "business_date": "AUTO",',
                        '  "date_rule": "LAST_DAY_PREVIOUS_MONTH",',
                        '  "feeds": [',
                        "    {",
                        '      "name": "Customer",',
                        '      "source": "EDW",',
                        '      "path": ".",',
                        '      "filename": "customer_{yyyymmdd}.csv"',
                        "    }",
                        "  ]",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config.check_interval_seconds, 10)
        self.assertEqual(config.feeds[0].filename, "customer_{yyyymmdd}.csv")


class StateTests(unittest.TestCase):
    def test_state_round_trip_preserves_ready_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state_202607.json"
            state = {
                "Customer": {
                    **default_feed_state(),
                    "status": "Ready",
                    "matched_file": "customer_20260731.csv",
                    "ready_time": "2026/08/04 08:15:22",
                    "notification_sent": True,
                }
            }

            save_state(path, state)
            loaded = load_state(path)

        self.assertEqual(loaded["Customer"]["ready_time"], "2026/08/04 08:15:22")


class FileDetectionTests(unittest.TestCase):
    def test_find_ready_file_requires_stable_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed_dir = Path(tmp)
            file_path = feed_dir / "customer_20260731.csv"
            file_path.write_text("ready", encoding="utf-8")
            feed = FeedConfig(
                name="Customer",
                source="EDW",
                path=feed_dir,
                filename="customer_{yyyymmdd}.csv",
            )

            with patch("core.monitor.time.sleep", return_value=None):
                matched = find_ready_file(feed, date(2026, 7, 31))

        self.assertEqual(matched, file_path)

    def test_find_ready_file_resolves_date_tokens_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feed_dir = root / "202607"
            feed_dir.mkdir()
            file_path = feed_dir / "customer.csv"
            file_path.write_text("ready", encoding="utf-8")
            feed = FeedConfig(
                name="Customer",
                source="EDW",
                path=Path(str(root / "{yyyymm}")),
                filename="customer.csv",
            )

            with patch("core.monitor.time.sleep", return_value=None):
                matched = find_ready_file(feed, date(2026, 7, 31))

        self.assertEqual(matched, file_path)


class MonitorLoopTests(unittest.TestCase):
    def test_dashboard_renders_once_when_all_feeds_are_ready(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[
                FeedConfig(
                    name="Customer",
                    source="EDW",
                    path=Path("."),
                    filename="customer.csv",
                )
            ],
        )
        state = {
            "Customer": {
                "status": "Ready",
                "matched_file": "customer.csv",
                "ready_time": "2026/08/04 08:15:22",
                "notification_sent": True,
            }
        }

        with (
            patch("core.monitor.load_config", return_value=config),
            patch("core.monitor.resolve_business_date", return_value=date(2026, 7, 31)),
            patch("core.monitor.load_state", return_value=state),
            patch("core.monitor.save_state"),
            patch("core.monitor.render_dashboard") as render_dashboard,
            patch("sys.stdout", StringIO()),
        ):
            run_monitor()

        self.assertEqual(render_dashboard.call_count, 1)

    def test_monitor_persists_initialized_state_on_startup(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[
                FeedConfig(
                    name="Customer",
                    source="EDW",
                    path=Path("missing"),
                    filename="customer.csv",
                )
            ],
        )
        state: dict[str, dict[str, object]] = {}

        with (
            patch("core.monitor.load_config", return_value=config),
            patch("core.monitor.resolve_business_date", return_value=date(2026, 7, 31)),
            patch("core.monitor.load_state", return_value=state),
            patch("core.monitor.save_state") as save_state_mock,
            patch("core.monitor.render_dashboard"),
            patch("core.monitor.time.sleep", side_effect=KeyboardInterrupt),
            patch("sys.stdout", StringIO()),
        ):
            run_monitor()

        self.assertGreaterEqual(save_state_mock.call_count, 1)
        self.assertEqual(state["Customer"]["status"], "Waiting")


class DashboardTests(unittest.TestCase):
    def test_dashboard_uses_boxed_header_and_single_redraw(self) -> None:
        feeds = [
            FeedConfig(
                name="Customer",
                source="EDW",
                path=Path("."),
                filename="customer.csv",
            ),
            FeedConfig(
                name="Bloomberg Curve",
                source="Market",
                path=Path("."),
                filename="curve.csv",
            )
        ]
        state = {
            "Customer": {
                "status": "Ready",
                "matched_file": "customer.csv",
                "ready_time": "2026/08/04 08:15:22",
                "notification_sent": True,
            },
            "Bloomberg Curve": {
                "status": "Waiting",
                "matched_file": None,
                "ready_time": None,
                "notification_sent": False,
            }
        }

        output = StringIO()
        with patch("sys.stdout", output), patch("core.dashboard.now_text", return_value="2026/08/04 09:35:00"):
            render_dashboard(feeds, state, date(2026, 7, 31), 300)

        text = output.getvalue()
        self.assertTrue(text.startswith("\033[2J\033[H"))
        self.assertIn("Monthly Feed Monitor", text)
        self.assertIn("Business Date : 2026/07/31", text)
        self.assertIn("Refresh : 2026/08/04 09:35:00", text)
        self.assertIn("Interval      : 5 minutes", text)
        self.assertIn("State   : Monitoring", text)
        self.assertIn("Progress      : ███████████░░░░░░░░░░░ 1 / 2 (50%)", text)
        self.assertIn("✔ Customer", text)
        self.assertIn("x Bloomberg Curve", text)


if __name__ == "__main__":
    unittest.main()
