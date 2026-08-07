from __future__ import annotations

import logging
import platform


def build_ready_notification(feed_names: list[str], ready_time: str) -> tuple[str, str]:
    """Build the notification title and message for a ready feed batch.

    "Ready at" is the time when the system detected this batch as ready. It is
    not the exact file arrival time or the exact time the file finished writing.
    """
    count = len(feed_names)
    noun = "feed" if count == 1 else "feeds"
    title = f"{count} inbound {noun} ready"

    sorted_names = sorted(feed_names)
    if count < 5:
        body = "\n".join(sorted_names)
    else:
        body = f"{count} files are ready"

    return title, f"{body}\n\nReady at {ready_time}"


def notify_ready(feed_names: list[str], ready_time: str) -> None:
    if not feed_names:
        return

    title, message = build_ready_notification(feed_names, ready_time)

    if platform.system() != "Windows":
        logging.info("Notification: %s - %s", title, message)
        return

    try:
        from winotify import Notification
    except ImportError:
        logging.warning("winotify is not installed; Windows toast skipped for %s", ", ".join(feed_names))
        return

    try:
        toast = Notification(app_id="File Monitor", title=title, msg=message)
        toast.show()
    except Exception:
        logging.exception("Failed to show Windows toast for %s", ", ".join(feed_names))
