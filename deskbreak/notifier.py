"""Native macOS notification + sound wrapper.

We deliberately avoid any third-party notifier. A banner is fired with
``osascript`` and a sound with ``afplay``, purely for visibility — the banner is
not interactive. The actual yes/no answer is given via ``deskbreak respond``.

Both subprocesses are run with a timeout so a misbehaving ``osascript`` or
``afplay`` can never hang the daemon's run loop.
"""

from __future__ import annotations

import subprocess

DEFAULT_SOUND = "/System/Library/Sounds/Glass.aiff"
DEFAULT_TITLE = "deskbreak"
DEFAULT_MESSAGE = "Time to walk! \U0001F6B6 — answer in terminal: deskbreak respond yes|no"

# Generous upper bound; a banner/sound should take well under a second.
_SUBPROCESS_TIMEOUT = 10


def _osa_quote(text: str) -> str:
    """Quote a Python string for safe embedding inside an AppleScript literal."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def _run(cmd) -> None:
    """Run a fire-and-forget subprocess, swallowing every failure mode."""
    try:
        subprocess.run(
            cmd,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Notifications are best-effort; never let them break the run loop.
        pass


def notify(message: str = DEFAULT_MESSAGE, title: str = DEFAULT_TITLE,
           sound: str = DEFAULT_SOUND) -> None:
    """Show a banner and play a sound. Best-effort, never raises."""
    script = "display notification {msg} with title {title}".format(
        msg=_osa_quote(message), title=_osa_quote(title)
    )
    _run(["osascript", "-e", script])
    _run(["afplay", sound])
