"""Anomaly detection for performance regressions."""

from typing import Dict, List, Optional
from collections import deque
import statistics
import config


class AnomalyDetector:
    """Detects anomalies in system metrics using statistical methods."""
    
    def __init__(self):
        """Initialize anomaly detector."""
        self.settings = config.ANOMALY_DETECTION
        self.window_size = self.settings['window_size']
        self.z_threshold = self.settings['z_score_threshold']
        
        # Rolling windows for key metrics
        self.gpu_util_history = deque(maxlen=self.window_size)
        self.gpu_mem_bw_history = deque(maxlen=self.window_size)
        self.cpu_util_history = deque(maxlen=self.window_size)
        
    def update_and_detect(self, metrics: Dict) -> List[Dict]:
        """
        Update history with new metrics and detect anomalies.
        
        Returns list of anomaly alerts.
        """
        if not self.settings['enabled']:
            return []
        
        anomalies = []
        
        # Extract current metrics
        gpu_data = metrics.get('gpu', [])
        cpu_data = metrics.get('cpu', {})
        
        if gpu_data:
            gpu = gpu_data[0]
            gpu_util = gpu.get('gpu_util', 0)
            
            # Update history
            self.gpu_util_history.append(gpu_util)
            self.cpu_util_history.append(cpu_data.get('utilization_total', 0))
            
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


# Singleton instance
_anomaly_detector = None

def detect_anomalies(metrics: Dict) -> List[Dict]:
    """Detect anomalies in system metrics."""
    global _anomaly_detector
    if _anomaly_detector is None:
        _anomaly_detector = AnomalyDetector()
    return _anomaly_detector.update_and_detect(metrics)
