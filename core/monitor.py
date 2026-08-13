from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.config import AppConfig, FeedConfig, ScanStrategy, load_config
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


@dataclass(frozen=True)
class ResolvedFeed:
    feed: FeedConfig
    folder: Path
    filename: str
    candidate: Path


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


def scan_ready_files(
    feeds: list[FeedConfig],
    state: State,
    business_date: date,
    scan_strategy: ScanStrategy = "GROUPED_PER_FILE",
    directory_listing_timeout_seconds: int = 5,
    skip_feed_names: set[str] | None = None,
) -> list[ReadyFeed]:
    """Return ready waiting feeds without mutating state or sending notifications."""
    skip_feed_names = skip_feed_names or set()
    grouped = group_waiting_feeds(feeds, state, business_date, skip_feed_names)
    if scan_strategy == "GROUPED_PER_FILE":
        return scan_grouped_per_file(grouped)
    if scan_strategy == "LIST_DIRECTORY":
        return scan_list_directory(grouped, directory_listing_timeout_seconds, fallback_to_per_file=False)
    return scan_list_directory(grouped, directory_listing_timeout_seconds, fallback_to_per_file=True)


def group_waiting_feeds(
    feeds: list[FeedConfig],
    state: State,
    business_date: date,
    skip_feed_names: set[str],
) -> dict[Path, list[ResolvedFeed]]:
    grouped: dict[Path, list[ResolvedFeed]] = {}
    for feed in feeds:
        if feed.name in skip_feed_names:
            continue

        item = state[feed.name]
        if item.get("status") == "Ready":
            continue

        resolved = resolve_feed(feed, business_date)
        grouped.setdefault(resolved.folder, []).append(resolved)
    return grouped


def scan_grouped_per_file(grouped: dict[Path, list[ResolvedFeed]]) -> list[ReadyFeed]:
    ready: list[ReadyFeed] = []
    dir_exists_cache: dict[Path, bool] = {}
    for resolved_feeds in grouped.values():
        for resolved in resolved_feeds:
            matched = find_ready_candidate(resolved.candidate, resolved.folder, dir_exists_cache)
            if matched is not None:
                ready.append(ReadyFeed(feed_name=resolved.feed.name, matched_file=matched.name))
    return ready


def scan_list_directory(
    grouped: dict[Path, list[ResolvedFeed]],
    directory_listing_timeout_seconds: int,
    fallback_to_per_file: bool,
) -> list[ReadyFeed]:
    ready: list[ReadyFeed] = []
    for folder, resolved_feeds in grouped.items():
        listed_files = list_directory_file_names(folder, directory_listing_timeout_seconds)
        if listed_files is None:
            if not fallback_to_per_file:
                continue
            for resolved in resolved_feeds:
                matched = find_ready_candidate(resolved.candidate, resolved.folder, {folder: True})
                if matched is not None:
                    ready.append(ReadyFeed(feed_name=resolved.feed.name, matched_file=matched.name))
            continue

        for resolved in resolved_feeds:
            if resolved.filename in listed_files and is_stable_file(resolved.candidate):
                ready.append(ReadyFeed(feed_name=resolved.feed.name, matched_file=resolved.filename))
    return ready


def collect_ready_batch(config: AppConfig, state: State, business_date: date) -> list[ReadyFeed]:
    ready = scan_ready_files(
        config.feeds,
        state,
        business_date,
        scan_strategy=config.scan_strategy,
        directory_listing_timeout_seconds=config.directory_listing_timeout_seconds,
    )
    if not ready:
        return []

    if config.aggregation_window_seconds > 0:
        time.sleep(config.aggregation_window_seconds)

    seen = {item.feed_name for item in ready}
    for item in scan_ready_files(
        config.feeds,
        state,
        business_date,
        scan_strategy=config.scan_strategy,
        directory_listing_timeout_seconds=config.directory_listing_timeout_seconds,
        skip_feed_names=seen,
    ):
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


def find_ready_file(
    feed: FeedConfig,
    business_date: date,
    dir_exists_cache: dict[Path, bool] | None = None,
) -> Path | None:
    resolved = resolve_feed(feed, business_date)
    return find_ready_candidate(resolved.candidate, resolved.folder, dir_exists_cache)


def find_ready_candidate(
    candidate: Path,
    folder: Path,
    dir_exists_cache: dict[Path, bool] | None = None,
) -> Path | None:
    try:
        if dir_exists_cache is not None and folder in dir_exists_cache:
            feed_dir_ready = dir_exists_cache[folder]
        else:
            feed_dir_ready = folder.is_dir()
            if dir_exists_cache is not None:
                dir_exists_cache[folder] = feed_dir_ready

        if not feed_dir_ready:
            logging.warning("Feed directory is missing: %s", folder)
            return None

        if not candidate.exists() or not candidate.is_file():
            return None
    except PermissionError:
        logging.warning("Permission denied while checking %s", candidate)
        return None
    except OSError as exc:
        logging.warning("Could not check %s: %s", candidate, exc)
        return None

    if is_stable_file(candidate):
        return candidate
    return None


def resolve_feed(feed: FeedConfig, business_date: date) -> ResolvedFeed:
    folder = Path(render_filename_template(str(feed.path), business_date))
    filename = render_filename_template(feed.filename, business_date)
    return ResolvedFeed(feed=feed, folder=folder, filename=filename, candidate=folder / filename)


def list_directory_file_names(folder: Path, timeout_seconds: int) -> set[str] | None:
    start = time.monotonic()
    try:
        if not folder.is_dir():
            logging.warning("Feed directory is missing: %s", folder)
            return set()

        names: set[str] = set()
        for item in folder.iterdir():
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                logging.warning(
                    "Directory listing timed out after %.2fs for %s; scanned %d entries; falling back to per-file checks",
                    elapsed,
                    folder,
                    len(names),
                )
                return None
            if item.is_file():
                names.add(item.name)
        elapsed = time.monotonic() - start
        logging.info("Listed %d files from %s in %.2fs", len(names), folder, elapsed)
        return names
    except PermissionError:
        logging.warning("Permission denied while listing %s; falling back to per-file checks", folder)
        return None
    except OSError as exc:
        logging.warning("Could not list %s: %s; falling back to per-file checks", folder, exc)
        return None


def is_stable_file(candidate: Path) -> bool:
    try:
        first_size = candidate.stat().st_size
        time.sleep(2)
        second_size = candidate.stat().st_size
    except PermissionError:
        logging.warning("Permission denied while checking %s", candidate)
        return False
    except OSError as exc:
        logging.warning("Could not check %s: %s", candidate, exc)
        return False

    if first_size == second_size:
        return True
    logging.info("File is still changing: %s", candidate)
    return False


def all_feeds_ready(feeds: list[FeedConfig], state: State) -> bool:
    return all(state[feed.name].get("status") == "Ready" for feed in feeds)
