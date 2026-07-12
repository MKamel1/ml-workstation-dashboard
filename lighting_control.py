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
from typing import TypedDict

from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

from util import lazy_singleton

# Some RGB controller firmware (this ASRock Polychrome device included)
# won't accept a color write immediately after a mode-switch command --
# observed as the write silently having no visible effect despite the SDK
# reporting success. A brief pause after set_mode() before set_color()
# avoids that race. ponytail: a fixed guess, not a measured minimum -- raise
# it if colors still don't stick; hardware timing like this needs
# calibration a static number can't fully capture.
_MODE_SWITCH_SETTLE_SECONDS = 0.15

_HEX_COLOR_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')


class LightingState(TypedDict):
    available: bool
    power: str  # "on" or "off"
    color: str  # "#rrggbb" -- the primary device's current color when "on"


def _parse_hex_color(value: str) -> RGBColor:
    """Raise ValueError if `value` isn't a 6-digit hex color like '#ff8800'."""
    if not isinstance(value, str) or not _HEX_COLOR_RE.match(value):
        raise ValueError(f"Invalid color {value!r}, expected a 6-digit hex string like '#ff8800'")
    value = value.lstrip('#')
    return RGBColor(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


class LightingController:
    """Controls every OpenRGB-managed device (motherboard + GPU RGB) as one
    unit -- a single power toggle and color, applied identically to every
    device via OpenRGB's 'Direct' mode. Direct is the one mode the GPU
    supports besides Off, while the motherboard also offers a dozen effect
    modes (Rainbow, Breathing, ...) this dashboard doesn't expose -- keeping
    control to the mode every device shares avoids per-device special-casing
    for a feature that's meant to be "turn the lights on/off and pick a
    color", not a full lighting-effects editor.
    """

    def __init__(self):
        try:
            self.client = OpenRGBClient(address='127.0.0.1', port=6742, name='ml-dashboard')
        except Exception:
            self.client = None

    def is_available(self) -> bool:
        return self.client is not None

    def get_state(self) -> LightingState:
        """Read the primary device's actual current mode/color from the
        OpenRGB server (not a locally-tracked shadow state), so this stays
        correct even if something else (OpenRGB's own GUI, another tool)
        changed the lights since this dashboard last set them.

        Raises whatever the underlying SDK call raises rather than
        swallowing it into a fake "off"/unavailable-looking result -- a
        real failure here must be visibly distinguishable from a real,
        successful "off" read, not silently indistinguishable from one.
        """
        if not self.is_available():
            return {"available": False, "power": "off", "color": "#000000"}
        self.client.update()
        primary = self.client.devices[0]
        is_on = primary.modes[primary.active_mode].name.lower() != 'off'
        if is_on and primary.colors:
            c = primary.colors[0]
            color = f"#{c.red:02x}{c.green:02x}{c.blue:02x}"
        else:
            color = "#000000"
        return {"available": True, "power": "on" if is_on else "off", "color": color}

    def set_color(self, color_hex: str) -> LightingState:
        """Turn every device on and set it to `color_hex` via Direct mode.

        Propagates any hardware/SDK error instead of catching it -- see
        get_state()'s docstring for why silently swallowing it here would be
        worse than letting the caller (the API layer) see the real failure.
        """
        rgb = _parse_hex_color(color_hex)  # validate before touching hardware
        if self.is_available():
            for dev in self.client.devices:
                dev.set_mode('direct')
                time.sleep(_MODE_SWITCH_SETTLE_SECONDS)
                dev.set_color(rgb)
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
