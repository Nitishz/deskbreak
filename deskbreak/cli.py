"""Command-line interface (argparse) for deskbreak."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from typing import Optional

from . import launchd
from .daemon import run_loop
from .state import (
    append_log,
    from_iso,
    load_state,
    now,
    save_state,
    to_iso,
)

_OFFSET_RE = re.compile(r"^\+(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE)
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def parse_time(value: Optional[str], base: datetime) -> Optional[datetime]:
    """Parse a ``--start``/``--end`` value into an absolute datetime.

    Accepts:
      * ``None``                -> returns None (caller applies its default)
      * ``+1h`` / ``+90m`` / ``+1h30m`` -> ``base`` plus that offset
      * ``14:30``               -> today at that clock time (local)
    """
    if value is None:
        return None
    value = value.strip()

    m = _OFFSET_RE.match(value)
    if m and (m.group(1) or m.group(2)):
        hours = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        return (base + timedelta(hours=hours, minutes=minutes)).replace(microsecond=0)

    m = _CLOCK_RE.match(value)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError(f"invalid clock time: {value!r}")
        return base.replace(hour=hh, minute=mm, second=0, microsecond=0)

    raise ValueError(
        f"unrecognized time {value!r}; use HH:MM (e.g. 14:30) or an offset (+1h, +90m)"
    )


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def _fmt_delta(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 0:
        return "now (overdue)"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_start(args) -> int:
    base = now()
    try:
        start = parse_time(args.start, base) or base
        # --end offsets are relative to the start time, not "now".
        end = parse_time(args.end, start)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if end is None:
        end = start + timedelta(hours=8)
    if end <= start:
        print("error: --end must be after --start", file=sys.stderr)
        return 2

    duration = args.duration
    if duration <= 0:
        print("error: --duration must be a positive number of minutes", file=sys.stderr)
        return 2

    state = load_state()
    state["config"] = {
        "start_time": to_iso(start),
        "end_time": to_iso(end),
        "duration_minutes": duration,
    }
    state["cycle"] = {
        "running": True,
        "next_alert_at": to_iso(start + timedelta(minutes=duration)),
        "effective_duration": duration,
        "escalation_step": 0,
        "awaiting_response": False,
        "awaiting_since": None,
        "last_response": None,
    }
    save_state(state)

    if not launchd.is_loaded():
        path = launchd.install()
        print(f"Installed and loaded launchd job ({path}).")
    else:
        print("launchd job already loaded.")

    print(f"Schedule armed: {_fmt(start)} → {_fmt(end)}, every {duration} min.")
    print(f"First alert at {_fmt(start + timedelta(minutes=duration))}.")
    print("Answer alerts with: deskbreak respond yes|no")
    return 0


def cmd_respond(args) -> int:
    answer = args.answer
    state = load_state()
    cycle = state["cycle"]
    if not cycle.get("awaiting_response"):
        print("No alert is currently waiting for a response.")
        return 0

    append_log(state, answer)
    cycle["awaiting_response"] = False
    cycle["last_response"] = answer
    save_state(state)
    if answer == "yes":
        print("Logged: yes 🚶 — nice. Resetting to a full interval.")
    else:
        print("Logged: no — I'll check back in sooner.")
    return 0


def cmd_stop(args) -> int:
    state = load_state()
    state["cycle"]["running"] = False
    state["cycle"]["awaiting_response"] = False
    save_state(state)
    removed = launchd.uninstall()
    if removed:
        print("Stopped: launchd job unloaded and removed.")
    else:
        print("Stopped: schedule disarmed (no launchd job was loaded).")
    return 0


def cmd_install(args) -> int:
    path = launchd.install()
    print(f"launchd job installed and loaded: {path}")
    return 0


def cmd_uninstall(args) -> int:
    removed = launchd.uninstall()
    if removed:
        print("launchd job unloaded and removed.")
    else:
        print("No launchd job was installed.")
    return 0


def cmd_status(args) -> int:
    state = load_state()
    cfg = state["config"]
    cycle = state["cycle"]
    loaded = launchd.is_loaded()

    print(f"Daemon (launchd):   {'loaded/running' if loaded else 'not loaded'}")
    print(f"Schedule active:    {'yes' if cycle.get('running') else 'no'}")
    print(
        "Window:             "
        f"{_fmt(from_iso(cfg.get('start_time')))} → {_fmt(from_iso(cfg.get('end_time')))}"
    )
    print(f"Base duration:      {cfg.get('duration_minutes')} min")
    print(f"Escalation step:    {cycle.get('escalation_step', 0)} "
          f"(effective {cycle.get('effective_duration')} min)")

    current = now()
    start = from_iso(cfg.get("start_time"))
    end = from_iso(cfg.get("end_time"))
    next_alert = from_iso(cycle.get("next_alert_at"))
    if not cycle.get("running"):
        print("Next alert:         — (not running)")
    elif start and current < start:
        print(f"Next alert:         idle until window opens ({_fmt(start)})")
    elif end and current >= end:
        print("Next alert:         — (window closed; run `deskbreak start` to re-arm)")
    elif next_alert:
        print(f"Next alert:         in {_fmt_delta((next_alert - current).total_seconds())} "
              f"(at {_fmt(next_alert)})")
    else:
        print("Next alert:         —")

    if cycle.get("awaiting_response"):
        print("                    ⏳ awaiting your response now → deskbreak respond yes|no")

    _print_today_summary(state)
    return 0


def _print_today_summary(state) -> None:
    today = now().date()
    counts = {"yes": 0, "no": 0, "timeout": 0}
    for entry in state.get("log", []):
        ts = from_iso(entry.get("timestamp"))
        if ts and ts.date() == today and entry.get("response") in counts:
            counts[entry["response"]] += 1
    skipped = counts["no"] + counts["timeout"]
    print(
        f"Today:              {counts['yes']} walk(s) logged, "
        f"{skipped} skipped "
        f"({counts['no']} no, {counts['timeout']} timeout)"
    )


def cmd_run(args) -> int:
    """Hidden: the long-lived loop launchd runs. Not meant to be called by hand."""
    run_loop()
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deskbreak",
        description="macOS desk-break reminder daemon with escalating alerts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="arm the schedule and load the daemon")
    p_start.add_argument("--start", help="when the schedule begins (HH:MM or +1h). Default: now")
    p_start.add_argument("--end", help="when the schedule ends (HH:MM or +8h). Default: start+8h")
    p_start.add_argument("--duration", type=int, default=45,
                         help="base interval between alerts, in minutes (default: 45)")
    p_start.set_defaults(func=cmd_start)

    p_respond = sub.add_parser("respond", help="answer a pending alert")
    p_respond.add_argument("answer", choices=["yes", "no"], help="your answer to the alert")
    p_respond.set_defaults(func=cmd_respond)

    sub.add_parser("stop", help="disarm and remove the launchd job").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="show daemon/config/next-alert/adherence").set_defaults(func=cmd_status)
    sub.add_parser("install", help="generate + load the launchd plist").set_defaults(func=cmd_install)
    sub.add_parser("uninstall", help="unload + remove the launchd plist").set_defaults(func=cmd_uninstall)

    # Internal command run by launchd (not intended for manual use).
    p_run = sub.add_parser("run")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
