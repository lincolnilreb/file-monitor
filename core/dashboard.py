from __future__ import annotations

from datetime import date

from core.config import FeedConfig
from core.state import State
from core.utils import now_text, render_filename_template


BOX_WIDTH = 78
INNER_WIDTH = BOX_WIDTH - 2
BAR_WIDTH = 22
READY_ICON = "✔"
WAITING_ICON = "x"


def render_dashboard(
    feeds: list[FeedConfig],
    state: State,
    business_date: date,
    interval_seconds: int,
) -> None:
    ready_feeds = [feed for feed in feeds if state[feed.name].get("status") == "Ready"]
    waiting_feeds = [feed for feed in feeds if state[feed.name].get("status") != "Ready"]
    total = len(feeds)
    ready_count = len(ready_feeds)
    percent = (ready_count / total * 100) if total else 0

    lines = [
        "\033[2J\033[H",
        _header_box(
            business_date=business_date,
            refresh_time=now_text(),
            interval_seconds=interval_seconds,
            state_text="Complete" if ready_count == total else "Monitoring",
            ready_count=ready_count,
            total=total,
            percent=percent,
        ),
        "",
        "READY",
        "-" * BOX_WIDTH,
    ]
    if ready_feeds:
        lines.append(f"{'Feed':24} {'Source':16} {'Actual Filename':32} {'Ready Time'}")
        for feed in ready_feeds:
            item = state[feed.name]
            lines.append(
                f"{_feed_label(READY_ICON, feed.name):24} "
                f"{feed.source[:16]:16} "
                f"{str(item.get('matched_file') or '')[:32]:32} "
                f"{item.get('ready_time') or ''}"
            )
    else:
        lines.append("No feeds are ready yet.")

    lines.extend(["", "WAITING", "-" * BOX_WIDTH])
    if waiting_feeds:
        lines.append(f"{'Feed':24} {'Source':16} {'Expected Filename'}")
        for feed in waiting_feeds:
            expected = render_filename_template(feed.filename, business_date)
            lines.append(f"{_feed_label(WAITING_ICON, feed.name):24} {feed.source[:16]:16} {expected}")
    else:
        lines.append("All feeds are ready.")

    lines.extend(
        [
            "",
            "-" * BOX_WIDTH,
            f"Ready: {ready_count} | Waiting: {len(waiting_feeds)} | Completion: {percent:.0f}%",
        ]
    )

    print("\n".join(lines), flush=True)


def _header_box(
    business_date: date,
    refresh_time: str,
    interval_seconds: int,
    state_text: str,
    ready_count: int,
    total: int,
    percent: float,
) -> str:
    progress = f"{_progress_bar(ready_count, total)} {ready_count} / {total} ({percent:.0f}%)"
    return "\n".join(
        [
            "╔" + "═" * INNER_WIDTH + "╗",
            _box_line("Monthly Feed Monitor".center(INNER_WIDTH)),
            "╠" + "═" * INNER_WIDTH + "╣",
            _box_line(
                _two_column(
                    f"Business Date : {business_date:%Y/%m/%d}",
                    f"Refresh : {refresh_time}",
                )
            ),
            _box_line(
                _two_column(
                    f"Interval      : {_format_interval(interval_seconds)}",
                    f"State   : {state_text}",
                )
            ),
            _box_line(f"Progress      : {progress}"),
            "╚" + "═" * INNER_WIDTH + "╝",
        ]
    )


def _box_line(content: str) -> str:
    return "║" + content[:INNER_WIDTH].ljust(INNER_WIDTH) + "║"


def _feed_label(icon: str, name: str) -> str:
    return f"{icon} {name}"[:24]


def _two_column(left: str, right: str) -> str:
    gap = max(1, INNER_WIDTH - len(left) - len(right))
    return left + (" " * gap) + right


def _progress_bar(ready_count: int, total: int) -> str:
    if total <= 0:
        return "░" * BAR_WIDTH
    filled = round(ready_count / total * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _format_interval(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    unit = "second" if seconds == 1 else "seconds"
    return f"{seconds} {unit}"
