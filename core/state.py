from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.utils import ensure_dir


FeedState = dict[str, Any]
State = dict[str, FeedState]


def state_file_for(state_dir: Path, yyyymm: str) -> Path:
    return state_dir / f"state_{yyyymm}.json"


def load_state(path: Path) -> State:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid state JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"State file must contain a JSON object: {path}")
    return data


def save_state(path: Path, state: State) -> None:
    ensure_dir(path.parent)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def default_feed_state() -> FeedState:
    return {
        "status": "Waiting",
        "matched_file": None,
        "ready_time": None,
        "notification_sent": False,
    }


def ensure_feed_states(state: State, feed_names: list[str]) -> None:
    for name in feed_names:
        state.setdefault(name, default_feed_state())
