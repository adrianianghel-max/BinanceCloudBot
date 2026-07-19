from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def load_json_state(path: str, default_value: dict[str, Any]) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return default_value

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("State file %s does not contain a JSON object. Resetting.", path)
            return default_value
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file %s: %s", path, exc)
        return default_value


def save_json_state(path: str, value: dict[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2)


def get_alert_state(path: str) -> dict[str, Any]:
    default = {"top_symbols": [], "updated_at": None}
    return load_json_state(path, default)


def update_alert_state(path: str, symbols: list[str]) -> None:
    payload = {
        "top_symbols": symbols,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_json_state(path, payload)


def should_send_only_new(current_symbols: list[str], previous_symbols: list[str]) -> bool:
    if not current_symbols:
        return False
    previous_set = set(previous_symbols)
    current_set = set(current_symbols)
    return len(current_set - previous_set) > 0
