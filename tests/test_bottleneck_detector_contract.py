"""Regression guard for ARCH-03: detect_bottlenecks() consumes the
metrics/schema.py TypedDict shape rather than ad-hoc dict spelunking.

Feeds synthetic metrics dicts matching MetricsSnapshot and asserts which
alerts fire/don't fire, so a future refactor that silently breaks field
access (e.g. reintroducing a typo'd .get() key) has something to catch it.
Plain asserts, no test framework.

Run directly:  python tests/test_bottleneck_detector_contract.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detection.bottleneck_detector import detect_bottlenecks


def _gpu(**overrides):
    base = dict(
        index=0, name="RTX 4090", gpu_util=10, memory_util=10,
        memory_used_gb=2.0, memory_free_gb=22.0, memory_total_gb=24.0,
        memory_util_pct=8.0, temperature=45.0, power_pct=17.8,
        pcie_gen=1, pcie_width=16, max_pcie_gen=4, max_pcie_width=16,
        throttle_reasons={}, top_processes=[],
    )
    base.update(overrides)
    return base


def _cpu(**overrides):
    base = dict(utilization_total=15.0)
    base.update(overrides)
    return base


def _memory(**overrides):
    base = dict(swap_used_gb=0.0, percent=31.0)
    base.update(overrides)
    return base


def _storage(**overrides):
    base = dict(disk_io=[])
    base.update(overrides)
    return base


def _snapshot(gpu_list, cpu, memory, storage):
    # Matches the required MetricsSnapshot keys detect_bottlenecks reads --
    # see metrics/schema.py.
    return {"timestamp": 0.0, "gpu": gpu_list, "cpu": cpu, "memory": memory, "storage": storage}


def _types(alerts):
    return {b["type"] for b in alerts}


def test_idle_system_fires_nothing():
    metrics = _snapshot([_gpu()], _cpu(), _memory(), _storage())
    assert _types(detect_bottlenecks(metrics)) == set()
    print("PASS: idle system -> no alerts")


def test_busy_healthy_gpu_fires_nothing():
    metrics = _snapshot(
        [_gpu(gpu_util=90, memory_util_pct=70.0, pcie_gen=4)],
        _cpu(utilization_total=40.0), _memory(), _storage(),
    )
    assert _types(detect_bottlenecks(metrics)) == set()
    print("PASS: busy but healthy GPU -> no alerts")


def test_swap_pressure_fires_critical_alert():
    metrics = _snapshot(
        [_gpu()], _cpu(), _memory(swap_used_gb=2.0, percent=90.0), _storage(),
    )
    assert _types(detect_bottlenecks(metrics)) == {"swap_active"}
    print("PASS: swap + RAM pressure -> swap_active")


def test_thermal_throttle_reason_fires_alert():
    metrics = _snapshot(
        [_gpu(throttle_reasons={"hw_thermal_slowdown": True}, temperature=88.0)],
        _cpu(), _memory(), _storage(),
    )
    assert _types(detect_bottlenecks(metrics)) == {"thermal_throttle"}
    print("PASS: hw_thermal_slowdown throttle reason -> thermal_throttle")


def test_vram_near_capacity_fires_alert():
    metrics = _snapshot([_gpu(memory_util_pct=97.0)], _cpu(), _memory(), _storage())
    assert _types(detect_bottlenecks(metrics)) == {"vram_full"}
    print("PASS: vram_pct 97% -> vram_full")


def test_sensor_unavailable_none_values_handled_gracefully():
    """ERR-01: an unreadable sensor comes back as None (documented Optional
    field in metrics/schema.py), not a missing key. detect_bottlenecks must
    not crash or misfire on it."""
    metrics = _snapshot(
        [_gpu(temperature=None, power_pct=None, pcie_gen=None, max_pcie_gen=None,
              pcie_width=None, max_pcie_width=None)],
        _cpu(), _memory(), _storage(),
    )
    assert _types(detect_bottlenecks(metrics)) == set()
    print("PASS: None sensor values handled gracefully -> no crash, no false alerts")


def test_errored_gpu_entry_handled_gracefully():
    """A per-GPU NVML read failure replaces the GPU's dict with a small
    {index, error} shape (see GPUMetricsCollector.collect). detect_bottlenecks
    must degrade gracefully (no alerts, no crash) rather than KeyError."""
    metrics = _snapshot(
        [{"index": 0, "error": "NVML read failed"}], _cpu(), _memory(), _storage(),
    )
    assert _types(detect_bottlenecks(metrics)) == set()
    print("PASS: errored GPU entry handled gracefully -> no crash, no alerts")


def test_no_gpus_present_handled_gracefully():
    metrics = _snapshot([], _cpu(), _memory(), _storage())
    assert _types(detect_bottlenecks(metrics)) == set()
    print("PASS: no GPUs present -> no crash, no alerts")


if __name__ == "__main__":
    test_idle_system_fires_nothing()
    test_busy_healthy_gpu_fires_nothing()
    test_swap_pressure_fires_critical_alert()
    test_thermal_throttle_reason_fires_alert()
    test_vram_near_capacity_fires_alert()
    test_sensor_unavailable_none_values_handled_gracefully()
    test_errored_gpu_entry_handled_gracefully()
    test_no_gpus_present_handled_gracefully()
    print("All bottleneck detector contract checks passed.")
