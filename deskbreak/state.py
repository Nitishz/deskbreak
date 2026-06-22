"""Load, save, and append to the single ``state.json`` file.

The state file lives inside the repo folder (next to this package) so that it
travels with the editable install and is shared between the launchd daemon and
the CLI commands invoked from any terminal.

All writes are atomic: we write to a temp file in the same directory and then
``os.replace`` it over the target, so a crash mid-write can never leave a
half-written (corrupt) state file behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Repo root is the parent of this package directory. With ``pip install -e .``
# the package stays inside the cloned repo, so this points at the repo folder.
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "state.json"
DAEMON_LOG_PATH = REPO_ROOT / "daemon.log"


def now() -> datetime:
    """Current local time, seconds resolution (keeps state.json tidy)."""
    return datetime.now().replace(microsecond=0)


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def from_iso(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def default_state() -> Dict[str, Any]:
    return {
        "config": {
            "start_time": None,
            "end_time": None,
            "duration_minutes": 45,
        },
        "cycle": {
            "running": False,
            "next_alert_at": None,
            "effective_duration": 45,
            "escalation_step": 0,
            "awaiting_response": False,
            "awaiting_since": None,
            "last_response": None,
        },
        "log": [],
    }


def load_state() -> Dict[str, Any]:
    """Read state.json, returning a fresh default if missing or unreadable."""
    if not STATE_PATH.exists():
        return default_state()
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default_state()
    # Merge over defaults so older/partial state files gain any new keys.
    merged = default_state()
    for section in ("config", "cycle"):
        if isinstance(data.get(section), dict):
            merged[section].update(data[section])
    if isinstance(data.get("log"), list):
        merged["log"] = data["log"]
    return merged


def save_state(state: Dict[str, Any]) -> None:
    """Atomically persist state to disk."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(STATE_PATH.parent), prefix=".state-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, STATE_PATH)
    except BaseException:
        # Clean up the temp file on any failure so we don't litter the folder.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def append_log(state: Dict[str, Any], response: str, when: Optional[datetime] = None) -> None:
    """Append a ``{timestamp, response}`` entry to the in-memory state.

    The caller is responsible for ``save_state`` afterwards. ``response`` is one
    of ``"yes"``, ``"no"``, or ``"timeout"``.
    """
    entry = {"timestamp": to_iso(when or now()), "response": response}
    state.setdefault("log", []).append(entry)
