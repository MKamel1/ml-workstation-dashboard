"""MCP server exposing the ML workstation dashboard to Claude and other
AI agents.

This is a thin client, not a reimplementation: every tool below is a
direct HTTP call to the dashboard's existing REST API (see app.py in the
parent directory). No metrics collection, database, or hardware-control
logic lives here -- that would duplicate what the dashboard already does
and risk drifting out of sync with it. If the dashboard's API changes,
update the call here, not the other way around.

Configuration (environment variables):
  DASHBOARD_URL     Base URL of the dashboard. Default: http://127.0.0.1:8000
  DASHBOARD_TIMEOUT Request timeout in seconds. Default: 10
"""

import os
from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8000").rstrip("/")
DASHBOARD_TIMEOUT = float(os.environ.get("DASHBOARD_TIMEOUT", "10"))

mcp = FastMCP("workstation-dashboard")


def _request(method: str, path: str, **kwargs) -> dict:
    """Call the dashboard's REST API and return the parsed JSON body.

    Raises a plain RuntimeError with a clear message on any failure
    (unreachable dashboard, timeout, non-2xx response) -- FastMCP turns an
    exception raised inside a tool into an error result for the calling
    agent automatically, so there's no need to hand-roll an error dict
    per tool here.
    """
    url = f"{DASHBOARD_URL}{path}"
    try:
        response = httpx.request(method, url, timeout=DASHBOARD_TIMEOUT, **kwargs)
    except httpx.RequestError as e:
        raise RuntimeError(
            f"Could not reach the dashboard at {url}: {e}. "
            f"Is it running? (check with: systemctl --user status ml-dashboard)"
        ) from e

    if response.status_code >= 400:
        try:
            detail = response.json().get("error", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(f"Dashboard returned {response.status_code} for {path}: {detail}")

    return response.json()


@mcp.tool()
def get_current_metrics() -> dict:
    """Get a live snapshot of every metric the dashboard collects right now:
    GPU (utilization, VRAM, temperature, power, clocks, top processes),
    CPU, memory, storage, network throughput, fan speeds, ML framework/
    process detection, and rule-based bottleneck alerts.

    Does NOT include anomaly alerts -- those depend on a rolling window fed
    only by the dashboard's own live websocket stream, and polling this
    tool must not perturb that baseline. Use it for "what's the state of
    the machine right now", not for anomaly history.
    """
    return _request("GET", "/api/metrics")


@mcp.tool()
def get_dashboard_config() -> dict:
    """Get the dashboard's current configuration: the metrics update
    interval (seconds) and the alert thresholds (GPU/CPU temperature, VRAM,
    swap, disk I/O, ...) that drive get_current_metrics()'s bottleneck alerts.
    """
    return _request("GET", "/api/config")


@mcp.tool()
def get_db_stats() -> dict:
    """Get how much history the dashboard has persisted: total record
    count, oldest/newest timestamp (unix seconds), and the time span in
    hours. Useful before calling get_history()/export_history() to know
    what range of data actually exists.
    """
    return _request("GET", "/api/db/stats")


@mcp.tool()
def get_history(start: Optional[int] = None, end: Optional[int] = None, limit: int = 1000) -> dict:
    """Query persisted historical metrics for a time range.

    :param start: Range start, unix seconds. Defaults to 1 hour ago.
    :param end: Range end, unix seconds. Defaults to now.
    :param limit: Max rows to return (most recent first within the range).
    """
    params = {"limit": limit}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return _request("GET", "/api/history", params=params)


@mcp.tool()
def export_history(
    start: Optional[int] = None,
    end: Optional[int] = None,
    components: Optional[str] = None,
    limit: int = 200000,
) -> dict:
    """Export historical metrics for a time range, limited to specific
    components -- the same data get_history() returns, but filterable down
    to only what's needed (e.g. just "gpu,cpu" for a training-run
    postmortem) and with a much higher default row limit meant for a full
    range export rather than a quick recent-history check.

    :param start: Range start, unix seconds. Defaults to 1 hour ago.
    :param end: Range end, unix seconds. Defaults to now.
    :param components: Comma-separated subset of: gpu, cpu, memory,
        storage, ml, fans, network, bottlenecks, anomalies. Omit for all
        of them.
    :param limit: Max rows to return.
    """
    params = {"limit": limit}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    if components is not None:
        params["components"] = components
    return _request("GET", "/api/export/history", params=params)


@mcp.tool()
def get_lighting_state() -> dict:
    """Get the current RGB lighting state (motherboard + GPU, via OpenRGB):
    whether it's available at all, power on/off, the active pattern, base
    color, and brightness/speed (0-100).
    """
    return _request("GET", "/api/lighting")


@mcp.tool()
def get_lighting_modes() -> dict:
    """List the RGB lighting patterns the hardware actually supports (e.g.
    Direct, Static, Breathing, Wave, Rainbow), each tagged with whether it
    has an animation speed to control. Call this before set_lighting() to
    know which mode names are valid and which ones accept a speed value.
    """
    return _request("GET", "/api/lighting/modes")


@mcp.tool()
def set_lighting(
    power: str,
    mode: str = "direct",
    color: str = "#ffffff",
    brightness: int = 100,
    speed: int = 50,
) -> dict:
    """Turn the RGB lighting on/off, and when turning on, set its pattern,
    color, brightness, and animation speed.

    :param power: "on" or "off". Required.
    :param mode: Pattern name from get_lighting_modes() (case-insensitive).
        Falls back to "direct" on any device that doesn't support the
        requested pattern (e.g. the GPU only supports Direct). Ignored
        when power="off".
    :param color: 6-digit hex color like "#ff8800". Ignored when power="off"
        or when the chosen mode has no settable color (e.g. Rainbow).
    :param brightness: 0-100. Ignored when power="off".
    :param speed: 0-100 (0=slowest, 100=fastest). Only affects modes that
        support animation speed (see get_lighting_modes()); ignored
        otherwise, and ignored when power="off".
    """
    if power not in ("on", "off"):
        raise ValueError('power must be "on" or "off"')
    body = {"power": power}
    if power == "on":
        body.update({"mode": mode, "color": color, "brightness": brightness, "speed": speed})
    return _request("POST", "/api/lighting", json=body)


if __name__ == "__main__":
    mcp.run()
