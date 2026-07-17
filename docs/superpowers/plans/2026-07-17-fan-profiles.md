# Quiet/Performance Fan Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Quiet/Performance fan-profile toggle to the ML workstation dashboard, backed by two CoolerControl profile sets (the existing quiet curves, and a new, more aggressive Performance set for sustained DL/LLM training load).

**Architecture:** A new `fan_profile_control.py` module (mirroring the existing `lighting_control.py` pattern) talks to coolercontrold's REST API (`https://127.0.0.1:11987`, local, stdlib-only HTTP client) to read/write which CoolerControl profile is assigned to each of 4 fan channels (2 on the motherboard's nct6799 chip, 2 on the GPU). A one-time provisioning script creates the new "Performance" CoolerControl profiles (the "Quiet" ones already exist from prior tuning work). New FastAPI routes expose get/set over HTTP; a new dashboard panel (mirroring the existing Lighting panel's button-toggle UI) drives it.

**Tech Stack:** Python 3.12, FastAPI, stdlib `urllib`/`http.cookiejar`/`ssl` (no new dependency), vanilla JS/CSS (existing dashboard.js/dashboard.css conventions).

## Global Constraints

- No new pip dependency for CoolerControl HTTP access — stdlib `urllib` only (spec: "New `fan_profile_control.py`... stdlib-only").
- `COOLERCONTROL_PASSWORD` must never be hardcoded (repo is public on GitHub) — read from environment variable only.
- `fan3`, `fan4` (AIO pump), `fan5` are never written by any code in this feature. `fan4` stays fixed 100% always.
- CoolerControl device UIDs are hardcoded constants (stable hashes of hardware identity, precedented by `fan_config.py`'s per-machine header mapping); CoolerControl *profile* UIDs are always resolved by name at runtime (they're regenerated whenever a profile is recreated, so a hardcoded profile UID would silently go stale).
- Everything must be tested locally (dashboard run on a non-conflicting port, endpoints curled, buttons clicked in a browser, CoolerControl state cross-checked) before pushing.

Known-good constants used throughout this plan (already confirmed live against this machine's coolercontrold this session):

```python
NCT6799_DEVICE_UID = "00a4da18625f56275c89e2fcd25a83c08c5ad3326452fa7e252fcc8a89c92493"
GPU_DEVICE_UID = "4af42a443b8bcadbfacf573544f5420a72c27498f0148d3781117fc8f4fb9d5d"
CPU_DEVICE_UID = "f98fcca0ec98fb32c1a40f148747c0d2308083af19eab157f32b777743c1c77c"
GPU_FUNCTION_UID = "91838416-3687-4cd2-983d-cf7caa2cd8f4"   # existing tuned hysteresis, reused
CPU_FUNCTION_UID = "912ae382-bb8c-499f-87b3-1a4c43c038f4"   # existing tuned hysteresis, reused
```

---

### Task 1: `fan_profile_control.py` — CoolerControl REST client + controller

**Files:**
- Create: `fan_profile_control.py`
- Test: `tests/test_fan_profile_control.py`

**Interfaces:**
- Consumes: `util.lazy_singleton` (existing, `util.py:4`)
- Produces:
  - `class FanProfileState(TypedDict)`: `{"available": bool, "mode": str}` where `mode` is one of `"quiet"`, `"performance"`, `"mixed"`, `"unknown"`
  - `class FanProfileController` with `.is_available() -> bool`, `.get_state() -> FanProfileState`, `.set_profile(mode: str) -> FanProfileState`
  - `get_fan_profile_controller() -> FanProfileController` (module-level singleton accessor, for `app.py` and the setup script in Task 2 to import)
  - `_classify_mode(current: dict, quiet_uids: dict, perf_uids: dict) -> str` (pure, unit-tested directly)
  - `_CoolerControlClient` class with `.get(path) -> dict`, `.put(path, body) -> dict`, `.post(path, body) -> dict` (Task 2's provisioning script imports and reuses this)

- [ ] **Step 1: Write `fan_profile_control.py`**

```python
"""Quiet/Performance fan profile control via CoolerControl's REST API.

Separate from metrics/ for the same reason as lighting_control.py: metrics/*
are read-only sensor collectors, this module writes to hardware (fan curves)
via a local, already-running service (coolercontrold on 127.0.0.1:11987).

fan3/fan5 are dead headers and fan4 is the AIO pump (must stay fixed 100%
always) -- see fan_config.py's per-machine header mapping. None of those are
touched here; this module only ever writes fan1/fan2 on the motherboard
(nct6799) and fan1/fan2 on the GPU's own fan channels.
"""

import base64
import json
import os
import ssl
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from typing import Dict, Optional, Tuple, TypedDict

from util import lazy_singleton

_BASE_URL = "https://127.0.0.1:11987"
_USERNAME = "CCAdmin"  # local-only login, not sensitive
_PASSWORD_ENV_VAR = "COOLERCONTROL_PASSWORD"

# Device UIDs are stable hashes of hardware identity in CoolerControl (unlike
# profile UIDs, which are regenerated every time a profile is recreated) --
# hardcoding them here follows the same precedent as fan_config.py's
# per-machine header mapping.
NCT6799_DEVICE_UID = "00a4da18625f56275c89e2fcd25a83c08c5ad3326452fa7e252fcc8a89c92493"
GPU_DEVICE_UID = "4af42a443b8bcadbfacf573544f5420a72c27498f0148d3781117fc8f4fb9d5d"

# (device_uid, channel_name) -> CoolerControl profile *name*. Resolved to a
# uid at call time via GET /profiles, never hardcoded -- profile uids are
# regenerated whenever a profile is recreated by hand, so a hardcoded uid
# would silently go stale.
_QUIET_PROFILES = {
    (NCT6799_DEVICE_UID, "fan1"): "mixing gpu and cpu",
    (NCT6799_DEVICE_UID, "fan2"): "quiteCPU",
    (GPU_DEVICE_UID, "fan1"): "Unmanaged",
    (GPU_DEVICE_UID, "fan2"): "Unmanaged",
}
_PERFORMANCE_PROFILES = {
    (NCT6799_DEVICE_UID, "fan1"): "mixing gpu and cpu performance",
    (NCT6799_DEVICE_UID, "fan2"): "performanceCPU",
    (GPU_DEVICE_UID, "fan1"): "GPU performance",
    (GPU_DEVICE_UID, "fan2"): "GPU performance",
}


class FanProfileState(TypedDict):
    available: bool
    mode: str  # "quiet" | "performance" | "mixed" | "unknown"


class _CoolerControlClient:
    """Thin stdlib-only REST client for coolercontrold: login/cookie/re-auth,
    JSON GET/PUT/POST. No third-party HTTP library needed for a handful of
    call sites against one local service.
    """

    def __init__(self, password: str):
        self._password = password
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPSHandler(context=self._ssl_context()),
        )
        self._logged_in = False

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        # coolercontrold serves a self-signed cert on localhost -- there's no
        # CA to validate against, same trust model as the `curl -k` calls
        # used to explore this API during setup.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _login(self):
        creds = base64.b64encode(f"{_USERNAME}:{self._password}".encode()).decode()
        req = urllib.request.Request(
            f"{_BASE_URL}/login", method="POST",
            headers={"Authorization": f"Basic {creds}"},
        )
        with self._opener.open(req, timeout=5) as resp:
            if resp.status != 200:
                raise RuntimeError(f"CoolerControl login failed: HTTP {resp.status}")
        self._logged_in = True

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> dict:
        if not self._logged_in:
            self._login()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{_BASE_URL}{path}", method=method, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with self._opener.open(req, timeout=5) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 401 and self._logged_in:
                # Session cookie expired -- re-authenticate once and retry.
                self._logged_in = False
                self._login()
                return self._request(method, path, body)
            raise RuntimeError(f"CoolerControl {method} {path} failed: HTTP {e.code}") from e

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def put(self, path: str, body: dict) -> dict:
        return self._request("PUT", path, body)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)


def _classify_mode(current: dict, quiet_uids: dict, perf_uids: dict) -> str:
    """Pure comparison, no I/O -- split out so the quiet/performance/mixed
    classification can be unit tested without a live CoolerControl
    connection.
    """
    if current == quiet_uids:
        return "quiet"
    if current == perf_uids:
        return "performance"
    return "mixed"


class FanProfileController:
    """Toggles between the "Quiet" and "Performance" CoolerControl profile
    sets for fan1/fan2 on the motherboard (nct6799) and the GPU's own fan
    channels. fan3/fan4(pump)/fan5 are never touched here.
    """

    def __init__(self):
        password = os.environ.get(_PASSWORD_ENV_VAR)
        self._client = _CoolerControlClient(password) if password else None

    def is_available(self) -> bool:
        return self._client is not None

    def _resolve_profile_uids(self) -> Dict[str, str]:
        """Name -> uid, re-fetched on every call -- cheap (one GET) and
        avoids ever acting on a stale uid if a profile gets recreated by
        hand in the CoolerControl UI between dashboard requests.
        """
        profiles = self._client.get("/profiles")["profiles"]
        return {p["name"]: p["uid"] for p in profiles}

    def _current_profile_uids(self) -> Dict[Tuple[str, str], Optional[str]]:
        current: Dict[Tuple[str, str], Optional[str]] = {}
        for device_uid in (NCT6799_DEVICE_UID, GPU_DEVICE_UID):
            settings = self._client.get(f"/devices/{device_uid}/settings")["settings"]
            by_channel = {s["channel_name"]: s["profile_uid"] for s in settings}
            for (d_uid, channel) in _QUIET_PROFILES:
                if d_uid == device_uid:
                    current[(d_uid, channel)] = by_channel.get(channel)
        return current

    def get_state(self) -> FanProfileState:
        """`available` reflects whether COOLERCONTROL_PASSWORD is set at all
        -- not whether the request below succeeds. A real request failure
        once available is True propagates rather than collapsing into a
        fake unavailable/unknown result, same rationale as
        lighting_control.py's get_state(): a genuine error must stay
        visibly distinguishable from a successful read.
        """
        if not self.is_available():
            return {"available": False, "mode": "unknown"}
        by_name = self._resolve_profile_uids()
        current = self._current_profile_uids()
        quiet_uids = {k: by_name.get(v) for k, v in _QUIET_PROFILES.items()}
        perf_uids = {k: by_name.get(v) for k, v in _PERFORMANCE_PROFILES.items()}
        return {"available": True, "mode": _classify_mode(current, quiet_uids, perf_uids)}

    def set_profile(self, mode: str) -> FanProfileState:
        if mode not in ("quiet", "performance"):
            raise ValueError(f"Invalid mode {mode!r}, expected 'quiet' or 'performance'")
        if not self.is_available():
            return {"available": False, "mode": "unknown"}
        target = _QUIET_PROFILES if mode == "quiet" else _PERFORMANCE_PROFILES
        by_name = self._resolve_profile_uids()
        for (device_uid, channel), profile_name in target.items():
            profile_uid = by_name.get(profile_name)
            if profile_uid is None:
                raise RuntimeError(
                    f"CoolerControl profile {profile_name!r} not found -- "
                    f"has setup_performance_fan_profiles.py been run?"
                )
            self._client.put(
                f"/devices/{device_uid}/settings/{channel}/profile",
                {"profile_uid": profile_uid},
            )
        return self.get_state()


_get_fan_profile_controller = lazy_singleton(FanProfileController)

def get_fan_profile_controller() -> FanProfileController:
    """Get or create the fan profile controller."""
    return _get_fan_profile_controller()
```

- [ ] **Step 2: Write `tests/test_fan_profile_control.py`**

Follows the same convention as `tests/test_lighting_control.py`: plain asserts,
no test framework, run directly. Pure logic only in this task (the live
round-trip against real "performance" profiles happens in Task 5, after
Task 2 has created them).

```python
"""Standalone check for fan_profile_control.py's pure classification logic,
and a read-only live check against coolercontrold if it's reachable (same
convention as testing collectors against real hardware rather than
mocking it -- see tests/test_lighting_control.py).

Plain asserts, no test framework. Run directly: python tests/test_fan_profile_control.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fan_profile_control import _classify_mode, get_fan_profile_controller

quiet = {("d1", "fan1"): "uidA", ("d1", "fan2"): "uidB"}
perf = {("d1", "fan1"): "uidC", ("d1", "fan2"): "uidD"}

assert _classify_mode(quiet, quiet, perf) == "quiet"
assert _classify_mode(perf, quiet, perf) == "performance"
assert _classify_mode({("d1", "fan1"): "uidA", ("d1", "fan2"): "uidD"}, quiet, perf) == "mixed", (
    "a channel matching neither full set must classify as mixed, not silently pick one side"
)
assert _classify_mode({}, quiet, perf) == "mixed", "no channels read yet must not equal either full set"

controller = get_fan_profile_controller()
if controller.is_available():
    state = controller.get_state()
    assert state["available"] is True
    assert state["mode"] in ("quiet", "performance", "mixed"), f"unexpected mode: {state['mode']}"
    print(f"tests/test_fan_profile_control.py passed (pure logic + live read-only check, mode={state['mode']})")
else:
    print("tests/test_fan_profile_control.py passed (pure logic only -- COOLERCONTROL_PASSWORD not set here)")
```

- [ ] **Step 3: Run the test**

Run: `COOLERCONTROL_PASSWORD=<password> venv/bin/python tests/test_fan_profile_control.py`
Expected: `tests/test_fan_profile_control.py passed (pure logic + live read-only check, mode=quiet)`

(Without the env var set: `tests/test_fan_profile_control.py passed (pure logic only -- COOLERCONTROL_PASSWORD not set here)` — both are pass conditions.)

- [ ] **Step 4: Commit**

```bash
git add fan_profile_control.py tests/test_fan_profile_control.py
git commit -m "Add fan_profile_control.py: CoolerControl REST client for Quiet/Performance toggle"
```

---

### Task 2: One-time CoolerControl provisioning script for the Performance profiles

**Files:**
- Create: `setup_performance_fan_profiles.py`

**Interfaces:**
- Consumes: `from fan_profile_control import _CoolerControlClient, _PASSWORD_ENV_VAR, GPU_DEVICE_UID` (all real module-level names defined in Task 1's `fan_profile_control.py`). `CPU_DEVICE_UID` and the two function UIDs are *not* defined in `fan_profile_control.py` (only needed for one-time provisioning, not at toggle-time) — this script defines them itself, as module-level constants, using the values from the Global Constraints section above.
- Produces: 5 new CoolerControl profiles (4 Graph + 1 Mix), idempotent by name (safe to re-run).

- [ ] **Step 1: Write `setup_performance_fan_profiles.py`**

```python
#!/usr/bin/env python3
"""One-time (idempotent) setup: creates the "Performance" CoolerControl
profile set used by fan_profile_control.py's Performance mode. The "Quiet"
profiles already exist from prior fan-tuning work and are not touched here.

Run once against the live coolercontrold before exercising the dashboard's
Performance toggle end-to-end:

    COOLERCONTROL_PASSWORD=<password> venv/bin/python setup_performance_fan_profiles.py

Safe to re-run: an existing profile (matched by name) is updated in place
rather than duplicated.
"""

import os
import sys
import uuid

from fan_profile_control import _CoolerControlClient, _PASSWORD_ENV_VAR, GPU_DEVICE_UID

CPU_DEVICE_UID = "f98fcca0ec98fb32c1a40f148747c0d2308083af19eab157f32b777743c1c77c"
GPU_FUNCTION_UID = "91838416-3687-4cd2-983d-cf7caa2cd8f4"   # existing tuned hysteresis, reused
CPU_FUNCTION_UID = "912ae382-bb8c-499f-87b3-1a4c43c038f4"   # existing tuned hysteresis, reused

# The two Graph profiles that feed the Mix profile driving nct6799 fan1.
GRAPH_PROFILES = [
    {
        "name": "GPU temp performance",
        "temp_source": {"temp_name": "GPU Temp", "device_uid": GPU_DEVICE_UID},
        "function_uid": GPU_FUNCTION_UID,
        "speed_profile": [[0.0, 40], [50.0, 40], [60.0, 55], [68.0, 70], [74.0, 85], [80.0, 100], [100.0, 100]],
    },
    {
        "name": "CPU temp performance",
        "temp_source": {"temp_name": "temp1", "device_uid": CPU_DEVICE_UID},
        "function_uid": CPU_FUNCTION_UID,
        "speed_profile": [[0.0, 40], [50.0, 40], [60.0, 55], [68.0, 70], [74.0, 85], [80.0, 100], [100.0, 100]],
    },
    {
        "name": "performanceCPU",
        "temp_source": {"temp_name": "temp1", "device_uid": CPU_DEVICE_UID},
        "function_uid": CPU_FUNCTION_UID,
        "speed_profile": [[0.0, 40], [45.0, 40], [55.0, 55], [65.0, 70], [72.0, 85], [80.0, 100], [100.0, 100]],
    },
    {
        "name": "GPU performance",
        "temp_source": {"temp_name": "GPU Temp", "device_uid": GPU_DEVICE_UID},
        "function_uid": GPU_FUNCTION_UID,
        "speed_profile": [[0.0, 30], [50.0, 30], [60.0, 45], [68.0, 60], [75.0, 80], [78.0, 100], [100.0, 100]],
    },
]

MIX_PROFILE_NAME = "mixing gpu and cpu performance"
MIX_MEMBER_NAMES = ["GPU temp performance", "CPU temp performance"]


def _graph_body(uid: str, spec: dict) -> dict:
    return {
        "uid": uid, "p_type": "Graph", "name": spec["name"],
        "speed_fixed": None, "speed_profile": spec["speed_profile"],
        "temp_source": spec["temp_source"], "temp_min": 0.0, "temp_max": 100.0,
        "function_uid": spec["function_uid"], "member_profile_uids": [],
        "mix_function_type": None, "offset_profile": [],
    }


def _mix_body(uid: str, member_uids: list) -> dict:
    return {
        "uid": uid, "p_type": "Mix", "name": MIX_PROFILE_NAME,
        "speed_fixed": None, "speed_profile": [],
        "temp_source": None, "temp_min": None, "temp_max": None,
        "function_uid": "0", "member_profile_uids": member_uids,
        "mix_function_type": "Max", "offset_profile": [],
    }


def _upsert(client: _CoolerControlClient, by_name: dict, name: str, body_fn) -> str:
    """Create the profile if `name` doesn't exist yet, else update it in
    place using its existing uid. Returns the profile's uid either way.
    """
    existing_uid = by_name.get(name)
    uid = existing_uid or str(uuid.uuid4())
    body = body_fn(uid)
    if existing_uid:
        client.put("/profiles", body)
        print(f"updated: {name} ({uid})")
    else:
        client.post("/profiles", body)
        print(f"created: {name} ({uid})")
    return uid


def main():
    password = os.environ.get(_PASSWORD_ENV_VAR)
    if not password:
        print(f"error: {_PASSWORD_ENV_VAR} is not set", file=sys.stderr)
        sys.exit(1)

    client = _CoolerControlClient(password)
    by_name = {p["name"]: p["uid"] for p in client.get("/profiles")["profiles"]}

    graph_uids = {}
    for spec in GRAPH_PROFILES:
        graph_uids[spec["name"]] = _upsert(client, by_name, spec["name"], lambda uid, spec=spec: _graph_body(uid, spec))

    member_uids = [graph_uids[name] for name in MIX_MEMBER_NAMES]
    _upsert(client, by_name, MIX_PROFILE_NAME, lambda uid: _mix_body(uid, member_uids))

    print("Performance profile set ready.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the live daemon**

Run: `COOLERCONTROL_PASSWORD=<password> venv/bin/python setup_performance_fan_profiles.py`
Expected output: 5 lines like `created: GPU temp performance (<uuid>)` ... ending with `Performance profile set ready.`

- [ ] **Step 3: Verify via the API**

Run:
```bash
curl -sk -b /tmp/cc_cookie https://127.0.0.1:11987/profiles | \
  python3 -c "import json,sys; names={p['name'] for p in json.load(sys.stdin)['profiles']}; \
  need={'GPU temp performance','CPU temp performance','performanceCPU','GPU performance','mixing gpu and cpu performance'}; \
  print('OK' if need <= names else f'MISSING: {need - names}')"
```
Expected: `OK`

(If the cookie in `/tmp/cc_cookie` has expired, re-login first: `curl -sk -c /tmp/cc_cookie -X POST https://127.0.0.1:11987/login -u "CCAdmin:<password>"`.)

- [ ] **Step 4: Re-run to confirm idempotency**

Run: `COOLERCONTROL_PASSWORD=<password> venv/bin/python setup_performance_fan_profiles.py`
Expected: 5 lines now say `updated:` instead of `created:`, same uuids as Step 2's output, no errors.

- [ ] **Step 5: Commit**

```bash
git add setup_performance_fan_profiles.py
git commit -m "Add one-time setup script for the Performance CoolerControl profile set"
```

---

### Task 3: `app.py` routes

**Files:**
- Modify: `app.py` (add import near existing `from lighting_control import get_lighting_controller` at line 27; add routes near the existing `/api/lighting` routes, `app.py:222-260`)

**Interfaces:**
- Consumes: `fan_profile_control.get_fan_profile_controller()` (Task 1), returns `FanProfileState`
- Produces: `GET /api/fans/profile`, `POST /api/fans/profile` — consumed by Task 4's frontend JS

- [ ] **Step 1: Add the import**

In `app.py`, right after the existing lighting import:

```python
# Import lighting control
from lighting_control import get_lighting_controller

# Import fan profile control
from fan_profile_control import get_fan_profile_controller
```

- [ ] **Step 2: Add the routes**

Add immediately after the existing `/api/lighting` POST route (`app.py:240-260` region), same file:

```python
@app.get("/api/fans/profile")
async def get_fan_profile():
    """Get the current fan profile mode (quiet/performance/mixed/unknown)."""
    try:
        return get_fan_profile_controller().get_state()
    except Exception as e:
        return JSONResponse(content={"error": f"Reading fan profile failed: {e}"}, status_code=500)


@app.post("/api/fans/profile")
async def set_fan_profile(payload: dict):
    """Set the fan profile. Body: {"mode": "quiet"|"performance"}."""
    mode = payload.get("mode")
    if mode not in ("quiet", "performance"):
        return JSONResponse(content={"error": "mode must be 'quiet' or 'performance'"}, status_code=400)
    try:
        return get_fan_profile_controller().set_profile(mode)
    except Exception as e:
        return JSONResponse(content={"error": f"Setting fan profile failed: {e}"}, status_code=500)
```

- [ ] **Step 3: Start the dashboard locally on a non-conflicting port and smoke-test**

The live production instance already occupies port 8000 (per `config.py`, `PORT = 8000`). Run the worktree's copy on a different port for testing:

```bash
cd /home/omar/ai-projects/workstation-dashboard/.claude/worktrees/fuzzy-drifting-globe
COOLERCONTROL_PASSWORD=<password> venv/bin/python -c "
import uvicorn, app as app_module
uvicorn.run(app_module.app, host='127.0.0.1', port=8001)
" &
sleep 2
curl -s http://127.0.0.1:8001/api/fans/profile
```
Expected: `{"available":true,"mode":"quiet"}`

```bash
curl -s -X POST http://127.0.0.1:8001/api/fans/profile -H "Content-Type: application/json" -d '{"mode":"performance"}'
```
Expected: `{"available":true,"mode":"performance"}`

```bash
curl -s -X POST http://127.0.0.1:8001/api/fans/profile -H "Content-Type: application/json" -d '{"mode":"quiet"}'
```
Expected: `{"available":true,"mode":"quiet"}` (back to quiet — don't leave the test process in Performance mode)

Then stop the background test server: `kill %1`

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "Add /api/fans/profile GET/POST routes"
```

---

### Task 4: Frontend — Fan Profile panel

**Files:**
- Modify: `static/index.html` (new panel after the existing Lighting panel, `static/index.html:140-192`)
- Modify: `static/dashboard.css` (new button styles, near `.theme-btn`, `static/dashboard.css:520-550`)
- Modify: `static/dashboard.js` (new functions near the lighting JS block, `static/dashboard.js:1267-1424`; new init call near `static/dashboard.js:1071`)

**Interfaces:**
- Consumes: `GET /api/fans/profile`, `POST /api/fans/profile` (Task 3)
- Produces: nothing consumed by later tasks (UI leaf)

- [ ] **Step 1: Add the panel HTML**

In `static/index.html`, immediately after the closing `</div>` of the Lighting panel's `dashboard-grid` (right after line 192, before the `<!-- Export History -->` comment):

```html
        <!-- Fan Profile -->
        <div class="dashboard-grid">
            <div class="panel">
                <div class="panel-header">
                    <h2 class="panel-title">🌀 Fan Profile</h2>
                </div>

                <div class="metrics-row" style="align-items: center;">
                    <div class="metric">
                        <div class="metric-label">Mode</div>
                        <div class="fan-profile-switcher" role="group" aria-label="Fan profile selection">
                            <button type="button" class="fan-profile-btn" id="fan-profile-btn-quiet"
                                    onclick="setFanProfile('quiet')" title="Quiet">🤫 Quiet</button>
                            <button type="button" class="fan-profile-btn" id="fan-profile-btn-performance"
                                    onclick="setFanProfile('performance')" title="Performance">🚀 Performance</button>
                        </div>
                    </div>

                    <div class="metric">
                        <div class="metric-label">Status</div>
                        <div class="metric-value" id="fan-profile-status" style="font-size: 0.9rem;">Loading...</div>
                    </div>
                </div>
            </div>
        </div>
```

- [ ] **Step 2: Add the CSS**

In `static/dashboard.css`, immediately after the existing `.theme-btn`/`.theme-btn.active` rules (after the `[data-theme="light"] .theme-btn.active { color: #ffffff; }` block):

```css
.fan-profile-switcher {
    display: inline-flex;
    gap: 0.35rem;
}

.fan-profile-btn {
    background: transparent;
    border: 1px solid var(--border-color);
    color: var(--text-secondary);
    padding: 0.35rem 0.75rem;
    border-radius: 6px;
    font-size: 0.82rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.25s ease;
}

.fan-profile-btn:hover {
    color: var(--text-primary);
    background: var(--bg-panel-hover);
}

.fan-profile-btn.active {
    background: var(--accent);
    color: #ffffff;
    border-color: var(--accent);
    box-shadow: 0 2px 8px rgba(0, 212, 255, 0.3);
}
```

- [ ] **Step 3: Add the JS**

In `static/dashboard.js`, immediately after the existing lighting block ends (after the `setLightingSpeed` function, i.e. after line 1424's closing brace):

```javascript
// Fan profile control (Quiet/Performance toggle, via CoolerControl) -- same
// interaction shape as the lighting power toggle above: fetch state on
// load, POST on click, re-render from whatever the server actually applied.
let fanProfileAvailable = false;

function applyFanProfileState(state) {
    fanProfileAvailable = state.available;
    const quietBtn = document.getElementById('fan-profile-btn-quiet');
    const perfBtn = document.getElementById('fan-profile-btn-performance');

    if (!state.available) {
        setTextContent('fan-profile-status', 'CoolerControl not available');
        quietBtn.disabled = true;
        perfBtn.disabled = true;
        quietBtn.classList.remove('active');
        perfBtn.classList.remove('active');
        return;
    }

    quietBtn.disabled = false;
    perfBtn.disabled = false;
    quietBtn.classList.toggle('active', state.mode === 'quiet');
    perfBtn.classList.toggle('active', state.mode === 'performance');

    if (state.mode === 'mixed') {
        setTextContent('fan-profile-status', 'Mixed (channels don\'t match either profile)');
    } else {
        setTextContent('fan-profile-status', state.mode === 'quiet' ? 'Quiet' : 'Performance');
    }
}

async function loadFanProfileState() {
    try {
        const response = await fetch('/api/fans/profile');
        const data = await response.json();
        if (data.error) {
            console.error('Failed to load fan profile state:', data.error);
            applyFanProfileState({ available: false, mode: 'unknown' });
            return;
        }
        applyFanProfileState(data);
    } catch (error) {
        console.error('Failed to load fan profile state:', error);
        applyFanProfileState({ available: false, mode: 'unknown' });
    }
}

async function setFanProfile(mode) {
    if (!fanProfileAvailable) return;
    try {
        const response = await fetch('/api/fans/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        const data = await response.json();
        if (data.error) {
            showToast(data.error || 'Fan profile update failed', 'error');
            return;
        }
        applyFanProfileState(data);
    } catch (error) {
        console.error('Fan profile update failed:', error);
        showToast('Fan profile update failed: ' + error.message, 'error');
    }
}
```

- [ ] **Step 4: Wire up the init call**

In `static/dashboard.js`, in the `DOMContentLoaded` handler (around line 1071), add the new load call next to `loadLightingState()`:

```javascript
    setTimeout(initCopyToClipboard, 1000); // Wait for metrics to populate
    loadLightingState();
    loadFanProfileState();
    initExportHistoryDefaults();
```

- [ ] **Step 5: Manual browser test**

With the worktree's dashboard still running on port 8001 (restart it if Task 3's Step 3 killed it):

```bash
cd /home/omar/ai-projects/workstation-dashboard/.claude/worktrees/fuzzy-drifting-globe
COOLERCONTROL_PASSWORD=<password> venv/bin/python -c "
import uvicorn, app as app_module
uvicorn.run(app_module.app, host='127.0.0.1', port=8001)
" &
sleep 2
```

Open `http://127.0.0.1:8001` in a browser. Confirm:
- The "🌀 Fan Profile" panel appears below Lighting, with "Quiet" highlighted and Status showing "Quiet"
- Clicking "🚀 Performance" highlights it instead, Status updates to "Performance", and no error toast appears
- Clicking "🤫 Quiet" switches back

Then stop the test server: `kill %1`

- [ ] **Step 6: Commit**

```bash
git add static/index.html static/dashboard.css static/dashboard.js
git commit -m "Add Quiet/Performance fan profile panel to the dashboard UI"
```

---

### Task 5: End-to-end verification against real hardware + docs + PR

**Files:**
- Modify: `CHANGELOG.md` (new entry)
- Modify: `README.md` (document the `COOLERCONTROL_PASSWORD` env var requirement, in the existing `## Development` section)

**Interfaces:**
- Consumes: everything from Tasks 1-4
- Produces: nothing (terminal task)

- [ ] **Step 1: Full round-trip test with real sensor cross-check**

With the worktree's dashboard running on port 8001 (same startup command as Task 4 Step 5):

```bash
echo "--- before: sensors baseline ---"
sensors nct6799-isa-0290 2>/dev/null | grep -i fan

curl -s -X POST http://127.0.0.1:8001/api/fans/profile -H "Content-Type: application/json" -d '{"mode":"performance"}'
echo ""
sleep 15
echo "--- after switching to performance: fan1/fan2 should show higher minimum duty ---"
sensors nct6799-isa-0290 2>/dev/null | grep -i fan
curl -sk -b /tmp/cc_cookie "https://127.0.0.1:11987/devices/00a4da18625f56275c89e2fcd25a83c08c5ad3326452fa7e252fcc8a89c92493/settings" | \
  python3 -c "import json,sys; [print(s['channel_name'], s['speed_fixed'], s['profile_uid']) for s in json.load(sys.stdin)['settings']]"
```

(If `nct6799-isa-0290` doesn't match on the machine running this plan, run bare `sensors` first and copy the exact chip header it prints — hwmon numbering/addresses aren't guaranteed stable across reboots.)
Expected: `fan4`'s line shows `speed_fixed=100` (pump untouched, still fixed) both before and after; `fan1`/`fan2` show a different `profile_uid` after the switch than before, and RPM in the `sensors` output for fan1/fan2 is at or above their new floor (40%) rather than the quiet floor (20-25%).

```bash
curl -s -X POST http://127.0.0.1:8001/api/fans/profile -H "Content-Type: application/json" -d '{"mode":"quiet"}'
```
Expected: `{"available":true,"mode":"quiet"}` — leave the machine in Quiet when done testing.

Stop the test server: `kill %1`

- [ ] **Step 2: Add CHANGELOG entry**

In `CHANGELOG.md`, add a new top section above the existing `## [1.1.0] - 2025-12-21` entry:

```markdown
## [Unreleased]

### Added

- **Fan Profiles**: Quiet/Performance toggle on the dashboard, backed by CoolerControl. Performance mode runs the case/AIO fans and the GPU's own fans on a more aggressive curve for sustained DL/LLM training load; the AIO pump always stays fixed at 100% regardless of mode. Requires `COOLERCONTROL_PASSWORD` set in the environment.

```

- [ ] **Step 3: Document the env var in README**

In `README.md`, in the `## Development` section, add after the existing venv-activation line:

```markdown
The Fan Profile panel talks to CoolerControl and needs its login password:

```bash
export COOLERCONTROL_PASSWORD=<your CoolerControl CCAdmin password>
```

Without it, the panel just shows "CoolerControl not available" — everything else in the dashboard works normally.
```

- [ ] **Step 4: Run the full existing test suite to confirm no regressions**

Run: `venv/bin/python tests/run_tests.py`
Expected: all existing tests still pass (this feature added no changes to any existing module).

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "Document Quiet/Performance fan profiles in README and CHANGELOG"
```

- [ ] **Step 6: Push and open a draft PR**

```bash
git push -u origin worktree-fuzzy-drifting-globe
gh pr create --draft --title "Add Quiet/Performance fan profile toggle to dashboard" --body "$(cat <<'EOF'
## Summary
- New `fan_profile_control.py` toggles CoolerControl's fan profile assignment between the existing "Quiet" curves and a new, more aggressive "Performance" set (case/AIO fans + GPU's own fans) for sustained DL/LLM training load.
- New `/api/fans/profile` GET/POST routes and a dashboard panel to switch between them.
- The AIO pump (`fan4`) is never touched by either mode -- always fixed 100%, as configured previously.
- One-time `setup_performance_fan_profiles.py` provisions the new CoolerControl profiles (run once against the live daemon; idempotent).

## Test plan
- [x] `tests/test_fan_profile_control.py` passes (pure logic + live read)
- [x] `setup_performance_fan_profiles.py` run against live coolercontrold, profiles verified via API, idempotent re-run confirmed
- [x] `/api/fans/profile` GET/POST smoke-tested via curl on a local test port
- [x] Buttons clicked in a browser, panel updates correctly
- [x] Real hardware cross-check via `sensors`: fan1/fan2 duty changes with mode, fan4 (pump) stays fixed 100% throughout
- [x] Full existing test suite (`tests/run_tests.py`) still passes
EOF
)"
```

Report the PR URL back to the user.
