from __future__ import annotations

import tempfile
import unittest
from io import StringIO
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core.config import AppConfig, FeedConfig, load_config
from core.dashboard import render_dashboard
from core.monitor import (
    ReadyFeed,
    collect_ready_batch,
    find_ready_file,
    list_directory_file_names,
    mark_ready_batch,
    run_monitor,
    scan_ready_files,
)
from core.notifier import build_ready_notification, notify_ready
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
        self.assertEqual(config.aggregation_window_seconds, 3)
        self.assertEqual(config.directory_listing_timeout_seconds, 5)
        self.assertEqual(config.scan_strategy, "GROUPED_PER_FILE")
        self.assertEqual(config.feeds[0].filename, "customer_{yyyymmdd}.csv")

    def test_load_config_rejects_invalid_scan_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                "\n".join(
                    [
                        "{",
                        '  "scan_strategy": "BAD",',
                        '  "feeds": [',
                        "    {",
                        '      "name": "Customer",',
                        '      "source": "EDW",',
                        '      "path": ".",',
                        '      "filename": "customer.csv"',
                        "    }",
                        "  ]",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "scan_strategy"):
                load_config(path)


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

    def test_scan_ready_files_does_not_send_notifications(self) -> None:
        feed = FeedConfig(
            name="Customer",
            source="EDW",
            path=Path("."),
            filename="customer.csv",
        )
        state = {"Customer": default_feed_state()}

        with (
            patch("core.monitor.list_directory_file_names", return_value=None),
            patch("core.monitor.find_ready_candidate", return_value=Path("customer.csv")),
            patch("core.monitor.notify_ready") as notify_mock,
        ):
            ready = scan_ready_files([feed], state, date(2026, 7, 31))

        self.assertEqual(ready, [ReadyFeed(feed_name="Customer", matched_file="customer.csv")])
        notify_mock.assert_not_called()

    def test_scan_ready_files_reuses_directory_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed_dir = Path(tmp)
            first_file = feed_dir / "customer.csv"
            second_file = feed_dir / "orders.csv"
            first_file.write_text("ready", encoding="utf-8")
            second_file.write_text("ready", encoding="utf-8")
            feeds = [
                FeedConfig(name="Customer", source="EDW", path=feed_dir, filename="customer.csv"),
                FeedConfig(name="Orders", source="EDW", path=feed_dir, filename="orders.csv"),
            ]
            state = {
                "Customer": default_feed_state(),
                "Orders": default_feed_state(),
            }
            original_is_dir = Path.is_dir

            with (
                patch("core.monitor.time.sleep", return_value=None),
                patch.object(Path, "is_dir", autospec=True, side_effect=lambda path: original_is_dir(path)) as is_dir_mock,
            ):
                ready = scan_ready_files(feeds, state, date(2026, 7, 31))

        self.assertEqual(len(ready), 2)
        self.assertEqual(is_dir_mock.call_count, 1)

    def test_grouped_per_file_is_default_scan_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed_dir = Path(tmp)
            (feed_dir / "customer.csv").write_text("ready", encoding="utf-8")
            feeds = [FeedConfig(name="Customer", source="EDW", path=feed_dir, filename="customer.csv")]
            state = {"Customer": default_feed_state()}

            with (
                patch("core.monitor.time.sleep", return_value=None),
                patch("core.monitor.list_directory_file_names") as listing_mock,
            ):
                ready = scan_ready_files(feeds, state, date(2026, 7, 31))

        self.assertEqual(ready, [ReadyFeed(feed_name="Customer", matched_file="customer.csv")])
        listing_mock.assert_not_called()

    def test_list_directory_scan_strategy_uses_directory_listing_for_folder_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feed_dir = Path(tmp)
            (feed_dir / "customer.csv").write_text("ready", encoding="utf-8")
            feeds = [
                FeedConfig(name="Customer", source="EDW", path=feed_dir, filename="customer.csv"),
                FeedConfig(name="Orders", source="EDW", path=feed_dir, filename="orders.csv"),
            ]
            state = {
                "Customer": default_feed_state(),
                "Orders": default_feed_state(),
            }

            with (
                patch("core.monitor.time.sleep", return_value=None),
                patch("core.monitor.find_ready_file") as fallback_mock,
            ):
                ready = scan_ready_files(feeds, state, date(2026, 7, 31), scan_strategy="LIST_DIRECTORY")

        self.assertEqual(ready, [ReadyFeed(feed_name="Customer", matched_file="customer.csv")])
        fallback_mock.assert_not_called()

    def test_list_directory_scan_strategy_does_not_fallback_when_directory_listing_times_out(self) -> None:
        feed = FeedConfig(name="Customer", source="EDW", path=Path("."), filename="customer.csv")
        state = {"Customer": default_feed_state()}

        with (
            patch("core.monitor.list_directory_file_names", return_value=None),
            patch("core.monitor.find_ready_file") as fallback_mock,
        ):
            ready = scan_ready_files([feed], state, date(2026, 7, 31), scan_strategy="LIST_DIRECTORY")

        self.assertEqual(ready, [])
        fallback_mock.assert_not_called()

    def test_auto_scan_strategy_falls_back_when_directory_listing_times_out(self) -> None:
        feed = FeedConfig(name="Customer", source="EDW", path=Path("."), filename="customer.csv")
        state = {"Customer": default_feed_state()}

        with (
            patch("core.monitor.list_directory_file_names", return_value=None),
            patch("core.monitor.find_ready_candidate", return_value=Path("customer.csv")) as fallback_mock,
        ):
            ready = scan_ready_files([feed], state, date(2026, 7, 31), scan_strategy="AUTO")

        self.assertEqual(ready, [ReadyFeed(feed_name="Customer", matched_file="customer.csv")])
        fallback_mock.assert_called_once()

    def test_list_directory_file_names_logs_success_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "customer.csv").write_text("ready", encoding="utf-8")
            (folder / "orders.csv").write_text("ready", encoding="utf-8")

            with self.assertLogs(level="INFO") as logs:
                names = list_directory_file_names(folder, timeout_seconds=5)

        self.assertEqual(names, {"customer.csv", "orders.csv"})
        self.assertTrue(any("Listed 2 files from" in message for message in logs.output))

    def test_list_directory_file_names_logs_timeout_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "customer.csv").write_text("ready", encoding="utf-8")

            with (
                patch("core.monitor.time.monotonic", side_effect=[0.0, 6.0]),
                self.assertLogs(level="WARNING") as logs,
            ):
                names = list_directory_file_names(folder, timeout_seconds=5)

        self.assertIsNone(names)
        self.assertTrue(any("Directory listing timed out after 6.00s" in message for message in logs.output))
        self.assertTrue(any("falling back to per-file checks" in message for message in logs.output))

    def test_collect_ready_batch_adds_files_seen_during_aggregation_window(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[],
        )
        first_scan = [ReadyFeed(feed_name="Customer", matched_file="customer.csv")]
        second_scan = [
            ReadyFeed(feed_name="Customer", matched_file="customer.csv"),
            ReadyFeed(feed_name="Orders", matched_file="orders.csv"),
        ]

        with (
            patch("core.monitor.scan_ready_files", side_effect=[first_scan, second_scan]) as scan_mock,
            patch("core.monitor.time.sleep") as sleep_mock,
        ):
            ready = collect_ready_batch(config, {}, date(2026, 7, 31))

        self.assertEqual(
            ready,
            [
                ReadyFeed(feed_name="Customer", matched_file="customer.csv"),
                ReadyFeed(feed_name="Orders", matched_file="orders.csv"),
            ],
        )
        self.assertEqual(scan_mock.call_count, 2)
        sleep_mock.assert_called_once_with(3)

    def test_collect_ready_batch_skips_first_scan_hits_during_second_scan(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[],
        )
        first_scan = [ReadyFeed(feed_name="Customer", matched_file="customer.csv")]

        with (
            patch("core.monitor.scan_ready_files", side_effect=[first_scan, []]) as scan_mock,
            patch("core.monitor.time.sleep"),
        ):
            collect_ready_batch(config, {}, date(2026, 7, 31))

        self.assertEqual(scan_mock.call_args_list[1].kwargs["skip_feed_names"], {"Customer"})

    def test_collect_ready_batch_skips_notification_window_when_no_files_are_ready(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[],
        )

        with (
            patch("core.monitor.scan_ready_files", return_value=[]),
            patch("core.monitor.time.sleep") as sleep_mock,
        ):
            ready = collect_ready_batch(config, {}, date(2026, 7, 31))

        self.assertEqual(ready, [])
        sleep_mock.assert_not_called()

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
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
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
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
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
            self.assertLogs(level="WARNING"),
        ):
            run_monitor()

        self.assertGreaterEqual(save_state_mock.call_count, 1)
        self.assertEqual(state["Customer"]["status"], "Waiting")

    def test_monitor_sends_one_notification_for_ready_batch(self) -> None:
        config = AppConfig(
            check_interval_seconds=10,
            aggregation_window_seconds=3,
            directory_listing_timeout_seconds=5,
            scan_strategy="GROUPED_PER_FILE",
            business_date="2026-07-31",
            date_rule="LAST_DAY_PREVIOUS_MONTH",
            feeds=[
                FeedConfig(name="Customer", source="EDW", path=Path("."), filename="customer.csv"),
                FeedConfig(name="Orders", source="EDW", path=Path("."), filename="orders.csv"),
                FeedConfig(name="Payments", source="EDW", path=Path("."), filename="payments.csv"),
            ],
        )
        state = {
            "Customer": default_feed_state(),
            "Orders": default_feed_state(),
            "Payments": default_feed_state(),
        }
        ready_batch = [
            ReadyFeed(feed_name="Customer", matched_file="customer.csv"),
            ReadyFeed(feed_name="Orders", matched_file="orders.csv"),
            ReadyFeed(feed_name="Payments", matched_file="payments.csv"),
        ]

        with (
            patch("core.monitor.load_config", return_value=config),
            patch("core.monitor.resolve_business_date", return_value=date(2026, 7, 31)),
            patch("core.monitor.load_state", return_value=state),
            patch("core.monitor.save_state"),
            patch("core.monitor.now_text", return_value="2026/08/04 10:30:15"),
            patch("core.monitor.collect_ready_batch", return_value=ready_batch),
            patch("core.monitor.notify_ready") as notify_mock,
            patch("core.monitor.render_dashboard"),
            patch("sys.stdout", StringIO()),
        ):
            run_monitor()

        notify_mock.assert_called_once_with(
            ["customer.csv", "orders.csv", "payments.csv"],
            "2026/08/04 10:30:15",
        )
        self.assertEqual(state["Customer"]["ready_time"], "2026/08/04 10:30:15")
        self.assertEqual(state["Orders"]["ready_time"], "2026/08/04 10:30:15")
        self.assertEqual(state["Payments"]["ready_time"], "2026/08/04 10:30:15")

    def test_mark_ready_batch_uses_same_ready_time_for_all_files(self) -> None:
        state = {
            "Customer": default_feed_state(),
            "Orders": default_feed_state(),
        }
        ready_batch = [
            ReadyFeed(feed_name="Customer", matched_file="customer.csv"),
            ReadyFeed(feed_name="Orders", matched_file="orders.csv"),
        ]

        changed = mark_ready_batch(state, ready_batch, "2026/08/04 10:30:15")

        self.assertTrue(changed)
        self.assertEqual(state["Customer"]["ready_time"], "2026/08/04 10:30:15")
        self.assertEqual(state["Orders"]["ready_time"], "2026/08/04 10:30:15")
        self.assertTrue(state["Customer"]["notification_sent"])
        self.assertTrue(state["Orders"]["notification_sent"])


class NotificationTests(unittest.TestCase):
    def test_build_ready_notification_for_one_file(self) -> None:
        title, message = build_ready_notification(["customer.csv"], "10:30:15")

        self.assertEqual(title, "1 inbound feed ready")
        self.assertEqual(message, "customer.csv\n\nReady at 10:30:15")

    def test_build_ready_notification_for_three_files(self) -> None:
        title, message = build_ready_notification(
            ["payments.csv", "customer.csv", "orders.csv"],
            "10:30:15",
        )

        self.assertEqual(title, "3 inbound feeds ready")
        self.assertEqual(message, "customer.csv\norders.csv\npayments.csv\n\nReady at 10:30:15")

    def test_build_ready_notification_for_five_files(self) -> None:
        title, message = build_ready_notification(
            ["5.csv", "4.csv", "3.csv", "2.csv", "1.csv"],
            "10:30:15",
        )

        self.assertEqual(title, "5 inbound feeds ready")
        self.assertEqual(message, "5 files are ready\n\nReady at 10:30:15")

    def test_build_ready_notification_for_eight_files(self) -> None:
        title, message = build_ready_notification(
            [f"{index}.csv" for index in range(8)],
            "10:30:15",
        )

        self.assertEqual(title, "8 inbound feeds ready")
        self.assertEqual(message, "8 files are ready\n\nReady at 10:30:15")

    def test_notify_ready_does_nothing_for_empty_batch(self) -> None:
        with patch("core.notifier.build_ready_notification") as build_mock:
            notify_ready([], "10:30:15")

        build_mock.assert_not_called()


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
