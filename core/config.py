from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


ScanStrategy = Literal["GROUPED_PER_FILE", "LIST_DIRECTORY", "AUTO"]
SCAN_STRATEGIES = {"GROUPED_PER_FILE", "LIST_DIRECTORY", "AUTO"}


@dataclass(frozen=True)
class FeedConfig:
    name: str
    source: str
    path: Path
    filename: str


@dataclass(frozen=True)
class AppConfig:
    check_interval_seconds: int
    aggregation_window_seconds: int
    directory_listing_timeout_seconds: int
    scan_strategy: ScanStrategy
    business_date: str
    date_rule: str
    feeds: list[FeedConfig]


def load_config(path: Path) -> AppConfig:
    try:
        raw = json.loads(_strip_comment_lines(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid config JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a JSON object")
    return _parse_config(raw)


def _strip_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def _parse_config(raw: dict[str, Any]) -> AppConfig:
    interval = raw.get("check_interval_seconds", 300)
    if not isinstance(interval, int) or interval <= 0:
        raise ValueError("check_interval_seconds must be a positive integer")

    aggregation_window = raw.get("aggregation_window_seconds", 3)
    if not isinstance(aggregation_window, int) or aggregation_window < 0:
        raise ValueError("aggregation_window_seconds must be a non-negative integer")

    listing_timeout = raw.get("directory_listing_timeout_seconds", 5)
    if not isinstance(listing_timeout, int) or listing_timeout <= 0:
        raise ValueError("directory_listing_timeout_seconds must be a positive integer")

    scan_strategy = raw.get("scan_strategy", "GROUPED_PER_FILE")
    if scan_strategy not in SCAN_STRATEGIES:
        raise ValueError("scan_strategy must be GROUPED_PER_FILE, LIST_DIRECTORY, or AUTO")

    business_date = raw.get("business_date", "AUTO")
    if not isinstance(business_date, str):
        raise ValueError("business_date must be a string")

    date_rule = raw.get("date_rule", "LAST_DAY_PREVIOUS_MONTH")
    if date_rule not in {"LAST_DAY_PREVIOUS_MONTH", "SAME_DAY_PREVIOUS_MONTH"}:
        raise ValueError("date_rule must be LAST_DAY_PREVIOUS_MONTH or SAME_DAY_PREVIOUS_MONTH")

    feeds_raw = raw.get("feeds")
    if not isinstance(feeds_raw, list) or not feeds_raw:
        raise ValueError("feeds must be a non-empty list")

    feeds = [_parse_feed(item, index) for index, item in enumerate(feeds_raw, start=1)]
    names = [feed.name for feed in feeds]
    if len(names) != len(set(names)):
        raise ValueError("feed names must be unique")

    return AppConfig(
        check_interval_seconds=interval,
        aggregation_window_seconds=aggregation_window,
        directory_listing_timeout_seconds=listing_timeout,
        scan_strategy=scan_strategy,
        business_date=business_date,
        date_rule=date_rule,
        feeds=feeds,
    )


def _parse_feed(raw: Any, index: int) -> FeedConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"feed #{index} must be an object")

    required = ("name", "source", "path", "filename")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"feed #{index} is missing: {', '.join(missing)}")

    return FeedConfig(
        name=str(raw["name"]),
        source=str(raw["source"]),
        path=Path(str(raw["path"])),
        filename=str(raw["filename"]),
    )
