"""Self-check: a failed sensor/API read must come back as None, not 0 or a crash.

Monkeypatches the underlying NVML/pathlib calls to raise, then asserts the
corresponding metric field is None. Plain asserts, no test framework.

Run directly:  python tests/test_metrics_error_handling.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pynvml

from metrics.gpu_metrics import GPUMetricsCollector
import metrics.storage_metrics as storage_metrics


def _boom(*args, **kwargs):
    raise RuntimeError("simulated sensor failure")


def test_gpu_power_read_failure_returns_none():
    collector = GPUMetricsCollector()
    assert collector.initialized, "NVML did not initialize - can't exercise this check here"

    real_power_usage = pynvml.nvmlDeviceGetPowerUsage
    real_power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit
    pynvml.nvmlDeviceGetPowerUsage = _boom
    pynvml.nvmlDeviceGetEnforcedPowerLimit = _boom
    try:
        gpu = collector._collect_single_gpu(collector.handles[0], 0)
    finally:
        pynvml.nvmlDeviceGetPowerUsage = real_power_usage
        pynvml.nvmlDeviceGetEnforcedPowerLimit = real_power_limit

    assert gpu["power_draw_w"] is None, gpu["power_draw_w"]
    assert gpu["power_limit_w"] is None, gpu["power_limit_w"]
    assert gpu["power_pct"] is None, gpu["power_pct"]
    print("PASS: gpu power read failure -> None (not 0)")


def test_gpu_temperature_read_failure_returns_none():
    collector = GPUMetricsCollector()
    assert collector.initialized, "NVML did not initialize - can't exercise this check here"

    real_temp = pynvml.nvmlDeviceGetTemperature
    pynvml.nvmlDeviceGetTemperature = _boom
    try:
        gpu = collector._collect_single_gpu(collector.handles[0], 0)
    finally:
        pynvml.nvmlDeviceGetTemperature = real_temp

    assert gpu["temperature"] is None, gpu["temperature"]
    print("PASS: gpu temperature read failure -> None (not 0)")


def test_storage_hf_cache_read_failure_returns_none():
    collector = storage_metrics.StorageMetricsCollector()
    collector._hf_cache_computed_at = 0.0  # force a recompute regardless of TTL

    real_exists = Path.exists
    real_rglob = Path.rglob
    Path.exists = lambda self: True
    Path.rglob = _boom
    try:
        result = collector.collect()
    finally:
        Path.exists = real_exists
        Path.rglob = real_rglob

    assert result["huggingface_cache_gb"] is None, result["huggingface_cache_gb"]
    print("PASS: storage HF cache read failure -> None (not 0)")


if __name__ == "__main__":
    test_gpu_power_read_failure_returns_none()
    test_gpu_temperature_read_failure_returns_none()
    test_storage_hf_cache_read_failure_returns_none()
    print("All metrics error-handling checks passed.")
