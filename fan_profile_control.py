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
