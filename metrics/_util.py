"""Shared helpers for metrics collectors."""

from typing import Any, Callable, Optional


def safe_read(fn: Callable[[], Any], default: Optional[Any] = None) -> Any:
    """Call fn(), returning default (None unless overridden) if it raises.

    Use for a single sensor/API read where failure should be reported as
    "unavailable" rather than masked with a misleading real-looking value
    like 0.
    """
    try:
        return fn()
    except Exception:
        return default
