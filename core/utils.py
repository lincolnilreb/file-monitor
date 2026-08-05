from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path


TIME_FORMAT = "%Y/%m/%d %H:%M:%S"


def now_text() -> str:
    return datetime.now().strftime(TIME_FORMAT)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def previous_month_last_day(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def same_day_previous_month(today: date) -> date:
    first_this_month = today.replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    day = min(today.day, last_previous_month.day)
    return last_previous_month.replace(day=day)


def resolve_business_date(value: str, date_rule: str) -> date:
    if value != "AUTO":
        return parse_date(value)

    today = date.today()
    if date_rule == "LAST_DAY_PREVIOUS_MONTH":
        return previous_month_last_day(today)
    if date_rule == "SAME_DAY_PREVIOUS_MONTH":
        return same_day_previous_month(today)
    raise ValueError(f"Unsupported date_rule: {date_rule}")


def render_filename_template(template: str, business_date: date) -> str:
    replacements = {
        "{yyyy}": business_date.strftime("%Y"),
        "{yy}": business_date.strftime("%y"),
        "{mm}": business_date.strftime("%m"),
        "{dd}": business_date.strftime("%d"),
        "{yyyymm}": business_date.strftime("%Y%m"),
        "{yyyymmdd}": business_date.strftime("%Y%m%d"),
        "{yyyy-mm-dd}": business_date.strftime("%Y-%m-%d"),
        "{yyyy_mm_dd}": business_date.strftime("%Y_%m_%d"),
    }

    filename = template
    for token, value in replacements.items():
        filename = filename.replace(token, value)
    return filename


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
