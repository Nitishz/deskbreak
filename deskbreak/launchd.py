"""launchd plist generation + ``launchctl`` load/unload helpers.

The daemon is a single long-lived process (``deskbreak run``) kept alive by
launchd. We invoke it as ``<python> -m deskbreak run`` so it does not depend on
the console script being on launchd's minimal PATH.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from .state import DAEMON_LOG_PATH, REPO_ROOT

LABEL = "com.deskbreak.daemon"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>deskbreak</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{logfile}</string>
    <key>StandardErrorPath</key>
    <string>{logfile}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>
"""


def render_plist() -> str:
    return _PLIST_TEMPLATE.format(
        label=LABEL,
        python=sys.executable,
        workdir=str(REPO_ROOT),
        logfile=str(DAEMON_LOG_PATH),
    )


def is_loaded() -> bool:
    """True if launchd currently knows about our job."""
    result = subprocess.run(
        ["launchctl", "list", LABEL],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def install() -> str:
    """Write the plist and load it. Returns the plist path as a string.

    Idempotent: if already loaded we unload first so the freshly written plist
    (e.g. a new python path) takes effect.
    """
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(render_plist(), encoding="utf-8")
    if is_loaded():
        _launchctl("unload", str(PLIST_PATH))
    _launchctl("load", str(PLIST_PATH))
    return str(PLIST_PATH)


def uninstall() -> bool:
    """Unload the job and remove the plist. Returns True if anything was removed."""
    existed = PLIST_PATH.exists()
    if existed:
        _launchctl("unload", str(PLIST_PATH))
        try:
            PLIST_PATH.unlink()
        except OSError:
            pass
    elif is_loaded():
        # Loaded without a plist on disk (unusual) — still try to unload by label.
        _launchctl("remove", LABEL)
        existed = True
    return existed


def _launchctl(*args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["launchctl", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return None
