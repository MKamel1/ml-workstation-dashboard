"""Standalone check for lighting_control.py's validation/scaling logic and,
if OpenRGB is reachable on this machine, a real on/off/mode/color/brightness
round-trip against the live openrgb.service (same convention as testing
collectors against real hardware rather than mocking it).

Plain asserts, no test framework. Run directly: python tests/test_lighting_control.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openrgb.utils import RGBColor
from lighting_control import _parse_hex_color, _parse_brightness, _scale_color, LightingController


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

# Brightness parsing: valid values (including numeric strings, since JSON
# bodies can hand either), boundaries included.
for value, expected in ((0, 0), (100, 100), (50, 50), ("75", 75)):
    assert _parse_brightness(value) == expected, f"{value!r} parsed as {_parse_brightness(value)}"

# Brightness parsing: out-of-range or non-numeric input must raise ValueError.
for bad in (-1, 101, "notanumber", None, [], 1000):
    try:
        _parse_brightness(bad)
        assert False, f"expected ValueError for {bad!r}"
    except ValueError:
        pass

# Brightness scaling: pure math, no hardware needed.
assert _scale_color(RGBColor(200, 100, 50), 100) == RGBColor(200, 100, 50)
assert _scale_color(RGBColor(200, 100, 50), 0) == RGBColor(0, 0, 0)
assert _scale_color(RGBColor(200, 100, 50), 50) == RGBColor(100, 50, 25)

controller = LightingController()
if controller.is_available():
    modes = controller.get_available_modes()
    assert isinstance(modes, list) and len(modes) > 0, f"expected a non-empty mode list, got {modes}"
    assert not any(m.lower() == 'off' for m in modes), "'off' should be excluded (that's the power toggle)"
    assert any(m.lower() == 'direct' for m in modes), "expected 'direct' to be a supported mode"

    # Real hardware round-trip: a mode every device supports (Direct).
    on_state = controller.set_state(mode="direct", color_hex="#123456", brightness=100)
    assert on_state["available"] is True
    assert on_state["power"] == "on"
    assert on_state["mode"] == "direct"
    assert on_state["color"] == "#123456", f"expected #123456, got {on_state['color']}"
    assert on_state["brightness"] == 100

    # A mode name no device supports (garbage input) must fall back to
    # Direct on every device rather than crashing or silently no-op'ing.
    fallback_state = controller.set_state(mode="not-a-real-mode", color_hex="#abcdef", brightness=50)
    assert fallback_state["available"] is True
    assert fallback_state["power"] == "on"
    assert fallback_state["brightness"] == 50

    # Every physical zone on every device must get the color -- explicitly
    # verifies the GPU's zone 0 ("0 - RGBW", the "GEFORCE RTX" logo) isn't
    # silently skipped in favor of only zone 1 (the light bar). This is the
    # kind of per-zone coverage gap that would look identical to success
    # from get_state() alone, since that only reads the primary (motherboard)
    # device -- so this checks the GPU zones directly.
    controller.set_state(mode="direct", color_hex="#aa33cc", brightness=100)
    controller.client.update()
    gpu = next(d for d in controller.client.devices if d.type.name == "GPU")
    assert len(gpu.zones) >= 2, f"expected the GPU to expose at least 2 zones, got {len(gpu.zones)}"
    for zone in gpu.zones:
        assert zone.colors and zone.colors[0] == RGBColor(0xaa, 0x33, 0xcc), (
            f"GPU zone {zone.name!r} did not receive the color: {zone.colors}"
        )

    off_state = controller.turn_off()
    # Also assert `available` here, not just `power` -- a silently-swallowed
    # failure used to fall back to {"available": False, "power": "off", ...},
    # which would make a genuine error look identical to a successful "off"
    # if only `power` were checked. Asserting `available` closes that gap.
    assert off_state["available"] is True
    assert off_state["power"] == "off"

    print("tests/test_lighting_control.py passed (validation/scaling + real hardware round-trip)")
else:
    print("tests/test_lighting_control.py passed (validation/scaling only -- OpenRGB not reachable here)")
