# deskbreak

A tiny macOS background daemon that reminds you to get up and walk every N
minutes, escalates if you keep saying no, and keeps an adherence log. Built for
a real doctor-recommended movement-break routine — so it biases toward
robustness (atomic state writes, no double-firing, no hanging on a misbehaving
`osascript`/`afplay`) over features.

- **Native alerts** — a macOS banner (`osascript`) plus a sound (`afplay`). No
  third-party notifier.
- **Answer from the terminal** — the banner isn't interactive. You reply with
  `deskbreak respond yes|no` from any terminal window.
- **Escalation** — say "no" (or ignore it) and the next reminder comes sooner.
- **launchd-managed** — survives terminal close, relaunches on crash, idles
  outside your scheduled window.

macOS only. Python 3.9+.

## Install

```bash
cd deskbreak
python3 -m pip install -e .
```

This installs the `deskbreak` console script and makes `python3 -m deskbreak`
work too (launchd uses the latter so it doesn't depend on your `PATH`).

## Usage

```bash
# Start now, default 8-hour window, 45-minute base interval.
deskbreak start

# Start at 9:30, end at 17:30, remind every 30 min.
deskbreak start --start 9:30 --end 17:30 --duration 30

# Start an hour from now for 6 hours.
deskbreak start --start +1h --end +6h
```

`--start` / `--end` accept a clock time (`14:30`) or an offset (`+1h`, `+90m`,
`+1h30m`). `--start` defaults to **now**; `--end` defaults to **start + 8h**.

When an alert fires you'll get a banner + sound. Answer it:

```bash
deskbreak respond yes   # got up — resets to a full interval
deskbreak respond no    # still sitting — next reminder comes sooner
```

Other commands:

```bash
deskbreak status      # daemon state, window, next alert, escalation, today's log
deskbreak stop        # disarm + unload/remove the launchd job
deskbreak install     # just generate + load the launchd plist
deskbreak uninstall   # just unload + remove the launchd plist
```

## How escalation works

Let `D` be your base interval (`--duration`, default 45).

| Current step | You answer no / ignore it | Next alert in |
|--------------|---------------------------|---------------|
| 0 → 1        | shorten                   | `D × 2/3`     |
| 1 → 2        | shorten more              | `D × 1/3`     |
| 2 → 0        | reset (no infinite climb) | `D`           |

A **yes** at any step immediately resets to step 0 and a full `D`. A
**timeout** (no answer within 30s) counts like a "no" for escalation but is
logged distinctly as `timeout` so you can tell "actively declined" from
"didn't see it". For `D = 45` the no-sequence of intervals is `45 → 30 → 15 →
45`.

Escalation state persists in `state.json`, so it survives daemon restarts and
machine reboots.

## How it works

| Module        | Responsibility                                            |
|---------------|-----------------------------------------------------------|
| `cli.py`      | argparse front-end + time parsing                         |
| `daemon.py`   | the `run` loop + the pure escalation state machine        |
| `notifier.py` | `osascript`/`afplay` wrapper (best-effort, timed out)     |
| `state.py`    | atomic load/save/append of `state.json`                   |
| `launchd.py`  | plist generation + `launchctl` load/unload                |

launchd runs one long-lived `deskbreak run` process (`RunAtLoad` +
`KeepAlive`). It reloads `state.json` each tick, stays idle before `start_time`
and after `end_time` (it never unloads itself — only `stop`/`uninstall` does),
and when an alert is due it fires once, waits up to 30s for your
`deskbreak respond`, then advances the schedule. If the machine sleeps through
an alert, on wake it fires a single alert rather than a backlog burst.

`state.json` and `daemon.log` live in the repo folder and are git-ignored.

## Testing

```bash
python3 -m pip install pytest
pytest
```

See the end of the build walkthrough for how to run a fast 1–2 minute cycle to
watch escalation fire live.
