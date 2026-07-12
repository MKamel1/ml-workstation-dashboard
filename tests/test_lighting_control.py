"""Standalone check for lighting_control.py's hex-color validation and, if
OpenRGB is reachable on this machine, a real on/off/color round-trip against
the live openrgb.service (same convention as testing collectors against
real hardware rather than mocking it).

Plain asserts, no test framework. Run directly: python tests/test_lighting_control.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lighting_control import _parse_hex_color, LightingController


# Hex parsing: valid input, with and without a leading '#'.
for value in ("#ff8800", "ff8800", "#FF8800"):
    rgb = _parse_hex_color(value)
    assert (rgb.red, rgb.green, rgb.blue) == (0xff, 0x88, 0x00), (
        f"{value!r} parsed as {(rgb.red, rgb.green, rgb.blue)}"
    )

# Hex parsing: invalid input must raise ValueError, not crash some other way.
for bad in ("notacolor", "#ff88", "#gggggg", "", None, 123):
    try:
        _parse_hex_color(bad)
        assert False, f"expected ValueError for {bad!r}"
    except ValueError:
        pass

controller = LightingController()
if controller.is_available():
    # Real hardware round-trip: turn on a known color, confirm get_state()
    # reflects it, then turn off and confirm that too.
    on_state = controller.set_color("#123456")
    assert on_state["available"] is True
    assert on_state["power"] == "on"
    assert on_state["color"] == "#123456", f"expected #123456, got {on_state['color']}"

    off_state = controller.turn_off()
    # Also assert `available` here, not just `power` -- a silently-swallowed
    # failure used to fall back to {"available": False, "power": "off", ...},
    # which would make a genuine error look identical to a successful "off"
    # if only `power` were checked. Asserting `available` closes that gap.
    assert off_state["available"] is True
    assert off_state["power"] == "off"

    print("tests/test_lighting_control.py passed (hex validation + real hardware round-trip)")
else:
    print("tests/test_lighting_control.py passed (hex validation only -- OpenRGB not reachable here)")
