"""Standalone check for AnomalyDetector: no globals, no real hardware.

Run directly: python tests/test_anomaly_detector.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detection.anomaly_detector import AnomalyDetector


def make_metrics(gpu_util: float) -> dict:
    return {"gpu": [{"gpu_util": gpu_util}], "cpu": {"utilization_total": 50}}


def test_flags_sudden_gpu_drop():
    detector = AnomalyDetector(window_size=60)  # fresh instance, no shared state

    # Steady utilization around 80% -- builds up the rolling window.
    anomalies = []
    for _ in range(15):
        anomalies = detector.update(make_metrics(80))
    assert anomalies == [], "steady utilization should not be flagged"

    # Sudden drop should be flagged as an anomaly.
    anomalies = detector.update(make_metrics(5))
    assert len(anomalies) == 1
    assert anomalies[0]["type"] == "gpu_util_drop"


def test_no_anomaly_with_too_few_samples():
    detector = AnomalyDetector(window_size=60)
    anomalies = detector.update(make_metrics(80))
    assert anomalies == []


if __name__ == "__main__":
    test_flags_sudden_gpu_drop()
    test_no_anomaly_with_too_few_samples()
    print("OK: all anomaly detector checks passed")
