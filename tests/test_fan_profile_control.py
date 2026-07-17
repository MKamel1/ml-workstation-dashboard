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
