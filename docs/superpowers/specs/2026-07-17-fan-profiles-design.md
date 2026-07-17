# Quiet / Performance fan profiles + dashboard toggle

## Problem

The workstation's fan curves were tuned last session for quiet, hunting-free
idle/desktop use (see CoolerControl profiles "gpu temp quite", "CPU temp
quiet", "quiteCPU"). That tuning is a poor fit for sustained DL/LLM training:
the GPU and CPU can sit at high sustained load for hours, and the quiet
curves cap fan speed low for longer than is ideal for keeping temps down
under that kind of load. We want a second, more aggressive curve set
("Performance") and a way to flip between the two from the dashboard already
running on this machine, without restarting anything.

## Fan hardware mapping (reference, not re-deriving)

Per `fan_config.py` (BIOS RPM cross-referenced, "FINAL VERIFIED
CONFIGURATION") and confirmed again by the user this session:

| nct6799 channel | Physical fan | Role |
|---|---|---|
| fan1 | Front intake 2x160mm | case airflow |
| fan2 | AIO radiator (top) | CPU AIO cooling |
| fan3 | *(unconnected header, 0 RPM)* | n/a |
| fan4 | AIO pump | **must stay fixed 100% always** |
| fan5 | *(unconnected header)* | n/a |

Plus the RTX 3090's own two GPU fan channels (separate CoolerControl device),
currently Unmanaged (Nvidia's stock auto-curve).

`fan3`/`fan5` are dead headers — never touched by either profile. `fan4`
(pump) is never touched by the toggle either; it stays fixed 100% in both
Quiet and Performance, exactly as configured last session.

## Current "Quiet" state (already live, no changes needed)

This *is* the Quiet profile. Captured here for reference only:

- `fan1` ← Mix("gpu temp quite", "CPU temp quiet"), Max — floor 20%, ramps to
  100% by ~92-100°C
- `fan2` ← "quiteCPU" — floor 25%, ramps to 100% by 95°C
- GPU's own fans ← Unmanaged (Nvidia default)

## New "Performance" profile

New CoolerControl Graph profiles, each reusing the **existing tuned
hysteresis functions** from last session (not reinventing smoothing —
that's what fixed the revving problem):

- `GPU temp performance` (sourced from 3090 GPU Temp, same function as "gpu
  temp quite") and `CPU temp performance` (sourced from CPU temp1, same
  function as "CPU temp quiet") → combined via a new Mix profile ("mixing
  gpu and cpu performance", Max) → drives `fan1`
  - Curve: floor 40% up to 50°C, ramp to 100% by 80°C
- `performanceCPU` (same function as "quiteCPU") → drives `fan2`
  - Curve: floor 40% up to 45°C, ramp to 100% by 80°C
- `GPU performance` (sourced from 3090 GPU Temp, same function as "gpu temp
  quite") → drives the GPU's own `fan1` and `fan2` channels
  - Curve: floor 30% up to 50°C, ramp to 100% by 78°C

Exact curve breakpoints are a starting point, not a hard spec — expected to
get tuned by ear/thermals after living with it, same as the quiet curves
were.

`fan3`, `fan4` (pump), `fan5` settings are not part of either profile
definition and are never written by the toggle.

## Dashboard integration

New `fan_profile_control.py`, module-for-module mirroring the existing
`lighting_control.py` pattern (same repo, same shape of problem — a
dashboard module driving external hardware-control state over a local
service's API):

- Talks to CoolerControl's REST API at `https://127.0.0.1:11987` (same box,
  no SSH tunnel needed for this — that tunnel work was for *remote* access
  to CoolerControl's own UI, unrelated to the dashboard talking to it
  locally)
- Handles session login (`POST /login`, HTTP Basic, cookie-based),
  re-authenticates on a 401
- Username `CCAdmin` is not sensitive (local login only) and can stay in
  code; the password is read from an environment variable
  (`COOLERCONTROL_PASSWORD`) — **not hardcoded**, since this repo is public
  on GitHub. If the env var is unset, the feature reports itself
  unavailable (`available: False`) rather than failing hard, same as
  `lighting_control.py` does when OpenRGB isn't reachable.
- `get_state() -> {"available": bool, "mode": "quiet"|"performance"|"mixed"}`
  — `"mixed"` if channels disagree (e.g. hand-edited outside the dashboard)
- `set_profile(mode: "quiet"|"performance")` — writes the appropriate
  `profile_uid` to each of `fan1`/`fan2` on the nct6799 device and
  `fan1`/`fan2` on the GPU device via
  `PUT /devices/{uid}/settings/{channel}/profile`
- Profile UIDs for both sets (quiet's are already fixed; performance's are
  assigned once at profile-creation time) are looked up by name once at
  startup against `GET /profiles`, not hardcoded as raw UIDs — CoolerControl
  UIDs are generated randomly at creation time, so a hardcoded UID would
  silently break if profiles are ever recreated by hand.

New routes in `app.py` (mirroring the `/api/lighting` routes exactly):

- `GET /api/fans/profile` → current mode
- `POST /api/fans/profile` body `{"mode": "quiet"|"performance"}`

New dashboard panel, placed next to the existing "💡 Lighting" panel: two
buttons, Quiet / Performance, active one visually highlighted — same
interaction shape as the lighting power toggle already in
`static/index.html` / `static/dashboard.js`.

## Error handling

- CoolerControl unreachable (service down, wrong port) → panel shows
  "unavailable", buttons disabled — same degrade-gracefully pattern as
  lighting.
- `COOLERCONTROL_PASSWORD` unset → same "unavailable" treatment.
- A profile-set-by-name lookup that finds no match (Performance profiles not
  yet created, e.g. on a fresh checkout before the setup step below has run)
  → "unavailable" with a clear message, not a crash.

## One-time CoolerControl setup

Creating the new Graph/Mix profiles in CoolerControl is a one-time,
machine-specific setup action (this machine's coolercontrold, not something
the dashboard app provisions on every boot). Done via a small one-off script
using the same REST calls documented from last session's fan-tuning work,
run once against the live daemon before the dashboard code is exercised
end-to-end.

## Testing

- Run the dashboard locally (`./dashboard.sh` / `python app.py` in the
  worktree, different port if 8000 is already taken by the live instance)
- `curl` the new endpoints directly, confirm mode read/write round-trips
- Click Quiet/Performance in a browser, confirm the panel updates
- Cross-check against `sensors` / CoolerControl's own API that the actual
  `profile_uid` assignments changed on `fan1`/`fan2` (both devices), and
  that `fan4` (pump) stayed at fixed 100% the whole time
- All of the above before pushing/opening a PR

## Out of scope

- Auto-switching based on detected ML workload (dashboard already has ML
  process detection, but the user asked for manual dashboard controls, not
  automation)
- Re-identifying fan3/fan5 — confirmed dead headers, not touched
- Changing pump behavior — stays fixed 100% always, unrelated to this
  feature
