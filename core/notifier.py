from __future__ import annotations

import logging
import platform


def notify_ready(feed_name: str, ready_time: str) -> None:
    title = "Inbound feed ready"
    message = f"{feed_name} ready at {ready_time}"

    if platform.system() != "Windows":
        logging.info("Notification: %s - %s", title, message)
        return

    try:
        from winotify import Notification
    except ImportError:
        logging.warning("winotify is not installed; Windows toast skipped for %s", feed_name)
        return

    try:
        toast = Notification(app_id="File Monitor", title=title, msg=message)
        toast.show()
    except Exception:
        logging.exception("Failed to show Windows toast for %s", feed_name)
