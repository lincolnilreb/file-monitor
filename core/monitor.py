from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

from core.config import AppConfig, FeedConfig, load_config
from core.dashboard import render_dashboard
from core.notifier import notify_ready
from core.state import State, ensure_feed_states, load_state, save_state, state_file_for
from core.utils import ensure_dir, now_text, render_filename_template, resolve_business_date


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "monitor.log"


def run_monitor() -> None:
    ensure_dir(LOG_DIR)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.info("Startup")

    try:
        config = load_config(CONFIG_PATH)
        business_date = resolve_business_date(config.business_date, config.date_rule)
        state_path = state_file_for(STATE_DIR, business_date.strftime("%Y%m"))
        state = load_state(state_path)
        ensure_feed_states(state, [feed.name for feed in config.feeds])
        save_state(state_path, state)
    except Exception as exc:
        logging.exception("Startup failed")
        raise SystemExit(f"Startup failed: {exc}") from exc

    try:
        while True:
            logging.info("Refresh")
            changed = scan_waiting_feeds(config, state, business_date)
            if changed:
                save_state(state_path, state)

            render_dashboard(config.feeds, state, business_date, config.check_interval_seconds)
            if all_feeds_ready(config.feeds, state):
                print()
                print("All feeds are ready.")
                logging.info("Shutdown: all feeds ready")
                break

            time.sleep(config.check_interval_seconds)
    except KeyboardInterrupt:
        save_state(state_path, state)
        logging.info("Shutdown: keyboard interrupt")
        print("\nStopped by operator.")


def scan_waiting_feeds(config: AppConfig, state: State, business_date: date) -> bool:
    changed = False
    for feed in config.feeds:
        item = state[feed.name]
        if item.get("status") == "Ready":
            continue

        matched = find_ready_file(feed, business_date)
        if matched is None:
            continue

        item["status"] = "Ready"
        item["matched_file"] = matched.name
        if not item.get("ready_time"):
            item["ready_time"] = now_text()

        if not item.get("notification_sent"):
            notify_ready(feed.name, item["ready_time"])
            item["notification_sent"] = True

        logging.info("Ready: %s (%s)", feed.name, matched)
        changed = True
    return changed


def find_ready_file(feed: FeedConfig, business_date: date) -> Path | None:
    feed_path = Path(render_filename_template(str(feed.path), business_date))
    filename = render_filename_template(feed.filename, business_date)
    candidate = feed_path / filename

    try:
        if not feed_path.exists() or not feed_path.is_dir():
            logging.warning("Feed directory is missing: %s", feed_path)
            return None

        if not candidate.exists() or not candidate.is_file():
            return None

        first_size = candidate.stat().st_size
        time.sleep(2)
        second_size = candidate.stat().st_size
    except PermissionError:
        logging.warning("Permission denied while checking %s", candidate)
        return None
    except OSError as exc:
        logging.warning("Could not check %s: %s", candidate, exc)
        return None

    if first_size == second_size:
        return candidate
    logging.info("File is still changing: %s", candidate)
    return None


def all_feeds_ready(feeds: list[FeedConfig], state: State) -> bool:
    return all(state[feed.name].get("status") == "Ready" for feed in feeds)
