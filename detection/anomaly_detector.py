"""Anomaly detection for performance regressions.

AnomalyDetector is stateful: it keeps a rolling window of recent samples and
flags a new sample as anomalous relative to that window. Call .update() only
from the real periodic tick (the /ws streaming loop) -- feeding it off-cadence
samples (e.g. from a debug HTTP poll) skews the baseline for every other
consumer of the live window.
"""

from typing import Dict, List, Optional
from collections import deque
import statistics
import config


class AnomalyDetector:
    """Detects anomalies in system metrics using statistical (z-score) methods."""

    def __init__(self, window_size: Optional[int] = None):
        """Initialize anomaly detector.

        window_size overrides config.ANOMALY_DETECTION['window_size']. Mainly
        so a test (or any other caller) can construct a small, self-contained
        instance without touching config or a shared singleton.
        """
        self.settings = config.ANOMALY_DETECTION
        self.window_size = window_size if window_size is not None else self.settings['window_size']
        self.z_threshold = self.settings['z_score_threshold']

        # Rolling window of GPU utilization samples. Only GPU 0 is checked,
        # and only for sudden drops -- this is a narrow, single-metric check,
        # not a general multi-metric engine.
        self.gpu_util_history = deque(maxlen=self.window_size)

    def update(self, metrics: Dict) -> List[Dict]:
        """
        Update the rolling history with a new sample and detect anomalies.

        Mutates internal state -- call once per real sample (the live
        streaming tick), never from a one-off/debug collection path.

        Returns list of anomaly alerts.
        """
        if not self.settings['enabled']:
            return []

        anomalies = []
        gpu_data = metrics.get('gpu', [])

        if gpu_data:
            gpu = gpu_data[0]
            gpu_util = gpu.get('gpu_util', 0)

            self.gpu_util_history.append(gpu_util)

            # Detect anomalies once we have enough data
            if len(self.gpu_util_history) >= 10:  # Need minimum samples

                # GPU utilization sudden drop
                gpu_anomaly = self._detect_anomaly(
                    self.gpu_util_history,
                    gpu_util,
                    metric_name="GPU Utilization"
                )
                if gpu_anomaly:
                    anomalies.append({
                        'type': 'gpu_util_drop',
                        'severity': 'info',
                        'title': 'GPU Utilization Anomaly',
                        'description': f'GPU utilization dropped significantly to {gpu_util}% '
                                       f'(mean: {gpu_anomaly["mean"]:.1f}%, std: {gpu_anomaly["std"]:.1f}%). '
                                       f'Possible data loader stall or batch processing delay.',
                        'metrics': gpu_anomaly,
                    })

        return anomalies

    def _detect_anomaly(self, history: deque, current_value: float, metric_name: str) -> Optional[Dict]:
        """
        Detect if current value is anomalous using z-score.

        Returns anomaly details if detected, None otherwise.
        """
        if len(history) < 10:
            return None

        values = list(history)
        mean = statistics.mean(values)

        try:
            std = statistics.stdev(values)
        except statistics.StatisticsError:
            return None

        if std == 0:
            return None

        z_score = (current_value - mean) / std

        # Detect significant drops (negative z-score beyond threshold)
        if z_score < -self.z_threshold:
            return {
                'metric': metric_name,
                'current': round(current_value, 2),
                'mean': round(mean, 2),
                'std': round(std, 2),
                'z_score': round(z_score, 2),
            }

        return None


# Module-level singleton for production use. The websocket streaming loop is
# the only caller that should invoke .update() on this instance -- see
# collect_all_metrics() / collect_raw_metrics() in app.py.
_detector = AnomalyDetector()


def get_anomaly_detector() -> AnomalyDetector:
    """Return the shared, stateful anomaly detector used by the live /ws stream."""
    return _detector
