"""The long-lived run loop and the escalation state machine.

``deskbreak run`` (invoked only by launchd) calls :func:`run_loop`. The loop:

  * reloads state each tick so config changes from ``deskbreak start`` and the
    response written by ``deskbreak respond`` are picked up;
  * stays idle before ``start_time`` and after ``end_time`` (it never unloads
    itself — only ``deskbreak stop``/``uninstall`` removes the launchd job);
  * when ``next_alert_at`` is reached, fires one alert, waits up to 30s for a
    response, then advances the escalation state.

The escalation math (:func:`compute_next`) is a pure function so it can be unit
tested in isolation.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Dict, Tuple

from . import notifier
from .state import (
    append_log,
    from_iso,
    load_state,
    now,
    save_state,
    to_iso,
)

# Loop tick: how often we re-read state when idle / counting down to an alert.
POLL_INTERVAL = 2.0
# How long to wait for a `deskbreak respond` after firing an alert.
RESPONSE_TIMEOUT = 30.0
# How often we re-check state while awaiting a response.
RESPONSE_POLL = 1.0

MAX_STEP = 2  # steps 0, 1, 2 then reset


def compute_next(step: int, response: str, base_duration: float) -> Tuple[int, int]:
    """Pure escalation transition.

    Given the *current* escalation ``step`` and the ``response`` to the alert
    that just fired, return ``(next_step, minutes_until_next_alert)``.

    For ``base_duration`` D:
      * "yes" at any step          -> reset to step 0, full D.
      * "no"/"timeout" at step 0   -> step 1, D * 2/3.
      * "no"/"timeout" at step 1   -> step 2, D * 1/3.
      * "no"/"timeout" at step 2   -> reset to step 0, full D (no infinite climb).
    Minutes are rounded to the nearest whole minute.
    """
    if response == "yes":
        return 0, round(base_duration)
    # "no" or "timeout" -> escalate (shorten the next interval).
    if step <= 0:
        return 1, round(base_duration * 2 / 3)
    if step == 1:
        return 2, round(base_duration * 1 / 3)
    # step >= MAX_STEP: a response here resets the climb.
    return 0, round(base_duration)


def _within_window(cfg: Dict[str, Any], current) -> bool:
    start = from_iso(cfg.get("start_time"))
    end = from_iso(cfg.get("end_time"))
    if start and current < start:
        return False
    if end and current >= end:
        return False
    return True


def _advance(state: Dict[str, Any], response: str) -> None:
    """Apply the escalation transition for ``response`` and schedule next alert."""
    cycle = state["cycle"]
    base = state["config"].get("duration_minutes", 45)
    next_step, minutes = compute_next(cycle.get("escalation_step", 0), response, base)
    cycle["escalation_step"] = next_step
    cycle["effective_duration"] = minutes
    cycle["next_alert_at"] = to_iso(now() + timedelta(minutes=minutes))


def fire_alert(state: Dict[str, Any]) -> str:
    """Fire one alert, wait for a response or timeout, return the response.

    Logs yes/no via the ``respond`` command path; logs ``timeout`` here. Updates
    escalation + ``next_alert_at`` and persists everything.
    """
    cycle = state["cycle"]
    cycle["awaiting_response"] = True
    cycle["awaiting_since"] = to_iso(now())
    cycle["last_response"] = None
    save_state(state)

    notifier.notify()

    # Poll the state file for `deskbreak respond` clearing the flag, or time out.
    deadline = time.monotonic() + RESPONSE_TIMEOUT
    response = None
    while time.monotonic() < deadline:
        current = load_state()
        if not current["cycle"].get("awaiting_response"):
            response = current["cycle"].get("last_response")
            state = current  # adopt the log entry written by `respond`
            break
        time.sleep(RESPONSE_POLL)

    if response is None:
        # No answer in time: treat as a timeout (distinct from an explicit "no").
        state = load_state()
        state["cycle"]["awaiting_response"] = False
        state["cycle"]["last_response"] = "timeout"
        append_log(state, "timeout")
        response = "timeout"

    _advance(state, response)
    save_state(state)
    return response


def run_loop(sleep=time.sleep) -> None:
    """The launchd entry point. Loops forever; ``sleep`` is injectable for tests."""
    # Clear any stale awaiting flag left by a crash/restart mid-alert.
    state = load_state()
    if state["cycle"].get("awaiting_response"):
        state["cycle"]["awaiting_response"] = False
        save_state(state)

    while True:
        state = load_state()
        cycle = state["cycle"]
        cfg = state["config"]

        if not cycle.get("running"):
            sleep(POLL_INTERVAL)
            continue

        current = now()
        if not _within_window(cfg, current):
            # Idle before start / after end. Stay loaded, fire nothing.
            sleep(POLL_INTERVAL)
            continue

        next_alert = from_iso(cycle.get("next_alert_at"))
        if next_alert is None:
            # No schedule armed yet; wait for `start` to set one.
            sleep(POLL_INTERVAL)
            continue

        if current >= next_alert:
            # Fire exactly once. If we woke from sleep with a long-overdue alert
            # this still fires a single alert (no backlog burst) because
            # next_alert_at is recomputed forward from "now" afterwards.
            fire_alert(state)
        else:
            # Sleep until the alert, capped at POLL_INTERVAL so config changes
            # and a system-sleep-induced overshoot are noticed promptly.
            remaining = (next_alert - current).total_seconds()
            sleep(max(0.0, min(POLL_INTERVAL, remaining)))
