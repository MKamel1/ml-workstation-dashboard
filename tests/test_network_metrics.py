"""Standalone check that NetworkMetricsCollector's rate math behaves:
zero on the first sample (no previous reading yet), non-negative real
numbers once a previous sample exists.

Uses real hardware (psutil) -- no mocks. Plain asserts, no test framework.
Run directly: python tests/test_network_metrics.py
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics.network_metrics import NetworkMetricsCollector


collector = NetworkMetricsCollector()

# The very first collect() call after construction has no elapsed-time
# baseline yet (previous_time is None until a first real sample lands), so
# it must report exactly 0.0/0.0 rather than dividing by a near-zero
# construction-to-first-call gap and producing an inflated/erratic rate.
first = collector.collect()
assert first == {"download_mbps": 0.0, "upload_mbps": 0.0}, (
    f"first collect() after construction should be zero, got {first}"
)

time.sleep(0.5)
second = collector.collect()
assert isinstance(second['download_mbps'], float), f"download_mbps not a float: {second}"
assert isinstance(second['upload_mbps'], float), f"upload_mbps not a float: {second}"
assert second['download_mbps'] >= 0, f"download_mbps negative: {second}"
assert second['upload_mbps'] >= 0, f"upload_mbps negative: {second}"

print("tests/test_network_metrics.py passed")
