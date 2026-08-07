from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ReadyFeed:
    feed_name: str
    matched_file: str


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
            ready_time = now_text()
            ready_batch = collect_ready_batch(config, state, business_date)
            changed = mark_ready_batch(state, ready_batch, ready_time)
            if changed:
                notify_ready([item.matched_file for item in ready_batch], ready_time)
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


def scan_ready_files(feeds: list[FeedConfig], state: State, business_date: date) -> list[ReadyFeed]:
    """Return ready waiting feeds without mutating state or sending notifications."""
    ready: list[ReadyFeed] = []
    for feed in feeds:
        item = state[feed.name]
        if item.get("status") == "Ready":
            continue

        matched = find_ready_file(feed, business_date)
        if matched is not None:
            ready.append(ReadyFeed(feed_name=feed.name, matched_file=matched.name))
    return ready


def collect_ready_batch(config: AppConfig, state: State, business_date: date) -> list[ReadyFeed]:
    ready = scan_ready_files(config.feeds, state, business_date)
    if not ready:
        return []

    if config.aggregation_window_seconds > 0:
        time.sleep(config.aggregation_window_seconds)

    seen = {item.feed_name for item in ready}
    for item in scan_ready_files(config.feeds, state, business_date):
        if item.feed_name not in seen:
            ready.append(item)
            seen.add(item.feed_name)

    return sorted(ready, key=lambda item: item.matched_file)


def mark_ready_batch(state: State, ready_batch: list[ReadyFeed], ready_time: str) -> bool:
    changed = False
    for ready in ready_batch:
        item = state[ready.feed_name]
        if item.get("status") == "Ready":
            continue

        item["status"] = "Ready"
        item["matched_file"] = ready.matched_file
        if not item.get("ready_time"):
            item["ready_time"] = ready_time

        item["notification_sent"] = True
        logging.info("Ready: %s (%s)", ready.feed_name, ready.matched_file)
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
