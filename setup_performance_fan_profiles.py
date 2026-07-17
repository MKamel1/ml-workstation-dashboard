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
