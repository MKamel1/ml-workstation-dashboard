"""RGB lighting control via OpenRGB's SDK server.

Separate from metrics/ on purpose: metrics/* are read-only sensor
collectors (see metrics/schema.py's CONTRACT-01 docstring), while this
module writes to hardware. Conflating "read a sensor" and "change a
physical light" in one package would blur that boundary.

Talks to openrgb.service (already running on this machine as a systemd
service, listening on 127.0.0.1:6742) via the openrgb-python SDK client,
rather than hand-rolling OpenRGB's network protocol.
"""

import re
import time
from typing import List, TypedDict

from openrgb import OpenRGBClient
from openrgb.utils import ModeColors, RGBColor

from util import lazy_singleton

# Some RGB controller firmware (this ASRock Polychrome device included)
# won't accept a color write immediately after a mode-switch command --
# observed as the write silently having no visible effect despite the SDK
# reporting success. A brief pause after switching modes before writing a
# color avoids that race. ponytail: a fixed guess, not a measured minimum --
# raise it if colors still don't stick; hardware timing like this needs
# calibration a static number can't fully capture.
_MODE_SWITCH_SETTLE_SECONDS = 0.15

_HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')

# Devices here (the GPU) support only a subset of the modes the motherboard
# does (just Off/Direct) -- any device that lacks the requested mode falls
# back to this one, which every OpenRGB device in this system supports.
_FALLBACK_MODE = "direct"


class LightingState(TypedDict):
    available: bool
    power: str  # "on" or "off"
    mode: str  # lowercase mode name, e.g. "direct", "static", "wave", "rainbow"
    color: str  # "#rrggbb" -- the base (unscaled) color, before brightness is applied
    brightness: int  # 0-100


def _parse_hex_color(value: str) -> RGBColor:
    """Raise ValueError if `value` isn't a 6-digit hex color like '#ff8800'."""
    if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
        raise ValueError(f"Invalid color {value!r}, expected a 6-digit hex string like '#ff8800'")
    value = value.lstrip('#')
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _parse_brightness(value) -> int:
    """Raise ValueError if `value` isn't an integer 0-100."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid brightness {value!r}, expected an integer 0-100")
    if not (0 <= value <= 100):
        raise ValueError(f"Invalid brightness {value!r}, expected an integer 0-100")
    return value


def _scale_color(color: RGBColor, brightness_pct: int) -> RGBColor:
    """Scale `color` by `brightness_pct` -- OpenRGB has no generic hardware
    "brightness" concept these two devices expose, so brightness here is
    just proportionally dimming the RGB values sent to the device.
    """
    factor = brightness_pct / 100
    return RGBColor(round(color.red * factor), round(color.green * factor), round(color.blue * factor))


class LightingController:
    """Controls every OpenRGB-managed device (motherboard + GPU RGB) as one
    unit -- one power toggle, one pattern/mode, one color, and one brightness,
    applied to every device. A requested mode that a given device doesn't
    support (e.g. the GPU only has Off/Direct, not the motherboard's dozen
    effect modes) falls back to Direct on that device rather than erroring,
    so "Wave" still lights the GPU up (statically) instead of leaving it
    untouched or rejecting the whole request.

    `power`, `mode`, `color`, and `brightness` are tracked here rather than
    always re-derived from hardware, unlike get_state() used to do for pure
    on/off: brightness has no hardware-readable equivalent to begin with
    (it's a scaling factor applied before sending a color, not a value any
    of these devices report back), and once different devices can land in
    different actual modes (the Direct-mode fallback above), there's no
    longer one unambiguous "current mode" to read back for the whole rig
    either. `power` is still real: turn_off()/set_state() apply it to
    hardware immediately, so tracked state and hardware state don't drift
    across a single dashboard's lifetime, which is what actually matters
    for a single-user tool -- reconciling with some *other* concurrent
    OpenRGB client isn't a scenario this needs to solve.
    """

    def __init__(self):
        try:
            self.client = OpenRGBClient(address='127.0.0.1', port=6742, name='ml-dashboard')
        except Exception:
            self.client = None
        self._power = "off"
        self._mode = "direct"
        self._color = "#ffffff"
        self._brightness = 100

    def is_available(self) -> bool:
        return self.client is not None

    def get_available_modes(self) -> List[str]:
        """Mode names the primary (motherboard) device supports, excluding
        'Off' -- that's the power toggle, not a pattern choice."""
        if not self.is_available():
            return []
        return [m.name for m in self.client.devices[0].modes if m.name.lower() != 'off']

    def get_state(self) -> LightingState:
        """`power` reflects the primary device's actual current hardware
        mode; `mode`/`color`/`brightness` reflect what this controller last
        applied (see the class docstring for why those aren't re-derived
        from hardware). Propagates any hardware/SDK error rather than
        swallowing it into a fake result -- a real failure here must be
        visibly distinguishable from a real, successful "off" read.
        """
        tracked = {"mode": self._mode, "color": self._color, "brightness": self._brightness}
        if not self.is_available():
            return {"available": False, "power": "off", **tracked}
        self.client.update()
        primary = self.client.devices[0]
        is_on = primary.modes[primary.active_mode].name.lower() != 'off'
        self._power = "on" if is_on else "off"
        return {"available": True, "power": self._power, **tracked}

    def set_state(self, mode: str, color_hex: str, brightness) -> LightingState:
        """Turn every device on, applying `mode` (falling back to Direct on
        any device that doesn't support it) with `color_hex` scaled by
        `brightness` (0-100).

        Propagates any hardware/SDK error -- see get_state()'s docstring.
        """
        rgb = _parse_hex_color(color_hex)  # validate before touching hardware
        brightness = _parse_brightness(brightness)
        mode = mode.lower() if isinstance(mode, str) else _FALLBACK_MODE
        scaled = _scale_color(rgb, brightness)

        self._mode, self._color, self._brightness = mode, color_hex, brightness

        if self.is_available():
            for dev in self.client.devices:
                modes_by_name = {m.name.lower(): m for m in dev.modes}
                target_name = mode if mode in modes_by_name else _FALLBACK_MODE
                current_name = dev.modes[dev.active_mode].name.lower()
                if current_name != target_name:
                    dev.set_mode(target_name)
                    time.sleep(_MODE_SWITCH_SETTLE_SECONDS)
                target_color_mode = modes_by_name[target_name].color_mode
                if target_color_mode == ModeColors.PER_LED:
                    # Explicit per-zone writes rather than one device-wide
                    # set_color() call, so every physical zone unambiguously
                    # gets its own command -- on the GPU specifically, zone 0
                    # ("0 - RGBW") is the "GEFORCE RTX" side logo and zone 1
                    # ("1 - SINGLE COLOR") is the accent light bar; relying on
                    # one combined device-level write made it easy to wonder
                    # whether the logo's zone was really included.
                    for zone in dev.zones:
                        zone.set_color(scaled)
                elif target_color_mode == ModeColors.MODE_SPECIFIC:
                    # No zone-level equivalent for this color type (it's set
                    # via the mode's own parameters, not a per-LED write) --
                    # not exercised by any mode this hardware actually has,
                    # kept for devices/modes where it would apply.
                    dev.set_color(scaled)
                # else NONE: auto-color effects (Rainbow, Wave, Spectrum
                # Cycle, ...) manage their own colors in firmware -- nothing
                # to set/scale, and brightness has no effect on those modes.
        return self.get_state()

    def turn_off(self) -> LightingState:
        """Propagates any hardware/SDK error -- see get_state()'s docstring."""
        if self.is_available():
            for dev in self.client.devices:
                dev.set_mode('off')
        return self.get_state()


_get_lighting_controller = lazy_singleton(LightingController)

def get_lighting_controller() -> LightingController:
    """Get or create the lighting controller."""
    return _get_lighting_controller()
