"""Standalone check that each collector's real output matches the
TypedDicts in metrics/schema.py (CONTRACT-01).

Uses real hardware (NVML, psutil, hwmon) -- no mocks. Asserts the returned
dict's keys are a SUBSET of the corresponding TypedDict's declared keys, so a
collector adding a field the schema doesn't know about fails loudly instead
of silently drifting from the documented shape.

Run directly: python tests/test_metrics_schema.py
"""

import sys
import os
from typing import get_type_hints

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics.gpu_metrics import get_gpu_metrics
from metrics.cpu_metrics import get_cpu_metrics
from metrics.memory_metrics import get_memory_metrics
from metrics.storage_metrics import get_storage_metrics
from metrics.ml_metrics import get_ml_metrics
from metrics.fan_metrics import get_system_fan_metrics
from metrics.network_metrics import get_network_metrics
from metrics.schema import (
    GPUMetrics,
    CPUMetrics,
    MemoryMetrics,
    StorageMetrics,
    MLMetrics,
    FanMetrics,
    NetworkMetrics,
)


def assert_keys_subset(actual: dict, typed_dict, label: str):
    allowed = set(get_type_hints(typed_dict).keys())
    extra = set(actual.keys()) - allowed
    assert not extra, f"{label}: keys {extra} not declared in {typed_dict.__name__} -- update metrics/schema.py"


def test_cpu_metrics_matches_schema():
    assert_keys_subset(get_cpu_metrics(), CPUMetrics, "CPUMetrics")


def test_memory_metrics_matches_schema():
    assert_keys_subset(get_memory_metrics(), MemoryMetrics, "MemoryMetrics")


def test_storage_metrics_matches_schema():
    assert_keys_subset(get_storage_metrics(), StorageMetrics, "StorageMetrics")


def test_ml_metrics_matches_schema():
    assert_keys_subset(get_ml_metrics(), MLMetrics, "MLMetrics")


def test_fan_metrics_matches_schema():
    assert_keys_subset(get_system_fan_metrics(), FanMetrics, "FanMetrics")


def test_network_metrics_matches_schema():
    assert_keys_subset(get_network_metrics(), NetworkMetrics, "NetworkMetrics")


def test_gpu_metrics_matches_schema():
    gpus = get_gpu_metrics()
    for gpu in gpus:
        assert_keys_subset(gpu, GPUMetrics, "GPUMetrics")


if __name__ == "__main__":
    test_cpu_metrics_matches_schema()
    test_memory_metrics_matches_schema()
    test_storage_metrics_matches_schema()
    test_ml_metrics_matches_schema()
    test_fan_metrics_matches_schema()
    test_network_metrics_matches_schema()
    test_gpu_metrics_matches_schema()
    print("tests/test_metrics_schema.py: all collectors match metrics/schema.py")
