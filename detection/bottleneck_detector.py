"""Intelligent bottleneck detection for ML/DL workloads."""

from typing import Dict, List, Optional
import config
from metrics.schema import CPUMetrics, GPUMetrics, MemoryMetrics, MetricsSnapshot, StorageMetrics
from util import lazy_singleton


class BottleneckDetector:
    """Detects performance bottlenecks in the system."""

    def __init__(self):
        """Initialize bottleneck detector."""
        self.thresholds = config.BOTTLENECK_DETECTION

    def detect(self, metrics: MetricsSnapshot) -> List[Dict]:
        """
        Detect bottlenecks from current system metrics.

        Returns list of bottleneck alerts with severity and description.
        """
        bottlenecks = []

        # Extract metrics. 'gpu'/'cpu'/'memory'/'storage' are required keys of
        # MetricsSnapshot whenever this is actually called -- app.py's
        # collect_raw_metrics() always sets all four before calling
        # detect_bottlenecks() (the error-fallback path in app.py's websocket
        # loop hardcodes bottlenecks=[] instead of calling this function at
        # all). Direct subscript so a renamed/missing key raises loudly
        # instead of silently defaulting to an empty dict/list.
        gpu_data: List[GPUMetrics] = metrics['gpu']
        cpu_data: CPUMetrics = metrics['cpu']
        memory_data: MemoryMetrics = metrics['memory']
        storage_data: StorageMetrics = metrics['storage']

        if not self.thresholds['enabled']:
            return bottlenecks

        # Get first GPU for analysis (multi-GPU support can be added)
        gpu: Optional[GPUMetrics] = gpu_data[0] if gpu_data else None

        if gpu:
            # NOTE: fields below are read with .get() rather than gpu['...']
            # even where metrics/schema.py marks them required, because a
            # per-GPU NVML read failure legitimately replaces this whole dict
            # with a smaller {"index", "error"} shape (see GPUMetrics
            # docstring in metrics/schema.py and GPUMetricsCollector.collect
            # in metrics/gpu_metrics.py) -- switching these to subscript
            # access would turn that documented, already-tolerated condition
            # into an unhandled KeyError instead of just skipping alerts for
            # that GPU this tick.
            gpu_util = gpu.get('gpu_util', 0)
            gpu_mem_util = gpu.get('memory_util', 0)

            # 1. GPU underutilization + High CPU = Data preprocessing bottleneck
            # Require an active ML workload before alerting, so idle monitoring
            # (no processes on the GPU at all) doesn't false-trigger this check.
            # 'utilization_total' is a required, always-present CPUMetrics
            # field (no error-shape variant for CPU) -- subscript directly.
            cpu_util = cpu_data['utilization_total']

            # Check if GPU has active processes (indicates ML workload is running)
            has_gpu_processes = len(gpu.get('top_processes', [])) > 0
            
            # Check if GPU is actually being used (not just idle/passive monitoring)
            gpu_is_active = gpu_util > config.THRESHOLDS['gpu']['active_util_min']  # More than idle monitoring threshold
            
            # Only alert if:
            # 1. GPU has processes running (ML workload present)
            # 2. GPU is semi-active but underutilized
            # 3. CPU is working hard (likely doing data preprocessing)
            if (has_gpu_processes and 
                gpu_is_active and
                gpu_util < self.thresholds['gpu_underutil_threshold'] and 
                cpu_util > self.thresholds['cpu_high_threshold']):
                bottlenecks.append({
                    'type': 'data_preprocessing',
                    'severity': 'warning',
                    'title': 'Data Preprocessing Bottleneck',
                    'description': f'GPU is underutilized ({gpu_util}%) while CPU is high ({cpu_util}%). '
                                   f'Your data preprocessing is likely too slow. Consider: '
                                   f'(1) Increase DataLoader num_workers, (2) Use GPU preprocessing, '
                                   f'(3) Pre-process and cache data.',
                    'metrics': {
                        'gpu_util': gpu_util,
                        'cpu_util': cpu_util,
                        'gpu_processes': len(gpu.get('top_processes', [])),
                    }
                })
            
            # 2. GPU underutilization + High disk I/O = Data loading bottleneck
            # 'disk_io' is a required StorageMetrics key (possibly an empty
            # list on the first collection tick, never a missing key) and
            # each DiskIOStats entry's read/write fields are required too --
            # subscript directly.
            disk_busy = False
            for disk in storage_data['disk_io']:
                total_io = disk['read_mb_s'] + disk['write_mb_s']
                if total_io > config.THRESHOLDS['storage']['io_high_mbs']:  # High I/O threshold
                    disk_busy = True
                    break
            
            if gpu_util < self.thresholds['gpu_underutil_threshold'] and disk_busy:
                bottlenecks.append({
                    'type': 'data_loading',
                    'severity': 'warning',
                    'title': 'Data Loading Bottleneck',
                    'description': f'GPU is underutilized ({gpu_util}%) while disk I/O is high. '
                                   f'Data loading from disk is too slow. Consider: '
                                   f'(1) Use faster storage (NVMe), (2) Increase DataLoader prefetch factor, '
                                   f'(3) Load dataset to RAM, (4) Use memory-mapped files.',
                    'metrics': {
                        'gpu_util': gpu_util,
                    }
                })
            
            # 3. Swap usage detected (CRITICAL for ML - but only if significant)
            # Require both a real swap size AND RAM pressure before alerting --
            # a little OS swap activity with plenty of free RAM is normal and
            # not worth interrupting the user over.
            # 'swap_used_gb'/'percent' are required, always-present
            # MemoryMetrics fields (no error-shape variant for memory) --
            # subscript directly.
            swap_used_gb = memory_data['swap_used_gb']
            mem_percent = memory_data['percent']
            
            # CRITICAL: Only alert if swap > 1GB AND RAM is under pressure (>85%)
            # This prevents false alerts from minimal OS swap activity
            mem_thresholds = config.THRESHOLDS['memory']
            if swap_used_gb > mem_thresholds['swap_critical_gb'] and mem_percent > mem_thresholds['swap_critical_ram_pct']:
                bottlenecks.append({
                    'type': 'swap_active',
                    'severity': 'critical',
                    'title': 'Swap Memory Active - CRITICAL',
                    'description': f'System is using {swap_used_gb:.2f} GB swap with {mem_percent:.1f}% RAM used! '
                                   f'This DESTROYS ML performance. Your RAM is exhausted. Immediate actions: '
                                   f'(1) Reduce batch size, (2) Close other applications, '
                                   f'(3) Use gradient accumulation instead of large batches, '
                                   f'(4) Enable gradient checkpointing.',
                    'metrics': {
                        'swap_used_gb': swap_used_gb,
                        'ram_percent': mem_percent,
                    }
                })
            elif swap_used_gb > mem_thresholds['swap_warning_gb'] and mem_percent > mem_thresholds['swap_warning_ram_pct']:
                # WARNING: Moderate swap with moderate RAM pressure
                bottlenecks.append({
                    'type': 'swap_warning',
                    'severity': 'warning',
                    'title': 'Moderate Swap Usage Detected',
                    'description': f'System is using {swap_used_gb:.2f} GB swap with {mem_percent:.1f}% RAM used. '
                                   f'Not critical yet, but monitor closely. Consider reducing memory usage before it impacts performance.',
                    'metrics': {
                        'swap_used_gb': swap_used_gb,
                        'ram_percent': mem_percent,
                    }
                })
            
            # 4. High memory usage warning
            # mem_percent is already defined above
            if mem_percent > mem_thresholds['usage_high']:
                bottlenecks.append({
                    'type': 'high_memory',
                    'severity': 'warning',
                    'title': 'High RAM Usage',
                    'description': f'RAM usage at {mem_percent:.1f}%. Risk of OOM. '
                                   f'Consider reducing batch size or clearing unused variables.',
                    'metrics': {
                        'memory_percent': mem_percent,
                    }
                })
            
            # 5. Thermal throttling
            throttle_reasons = gpu.get('throttle_reasons', {})
            if throttle_reasons.get('hw_thermal_slowdown') or throttle_reasons.get('sw_thermal_slowdown'):
                gpu_temp = gpu.get('temperature', 0)
                bottlenecks.append({
                    'type': 'thermal_throttle',
                    'severity': 'critical',
                    'title': 'GPU Thermal Throttling',
                    'description': f'GPU is throttling due to temperature ({gpu_temp}°C). '
                                   f'Performance is degraded. Actions: '
                                   f'(1) Improve case airflow, (2) Clean GPU fans, '
                                   f'(3) Increase fan curve, (4) Check thermal paste.',
                    'metrics': {
                        'temperature': gpu_temp,
                    }
                })
            
            # 6. Power limit throttling
            if throttle_reasons.get('sw_power_cap') or throttle_reasons.get('hw_power_brake_slowdown'):
                power_pct = gpu.get('power_pct', 0)
                bottlenecks.append({
                    'type': 'power_throttle',
                    'severity': 'warning',
                    'title': 'GPU Power Limit Throttling',
                    'description': f'GPU is hitting power limit ({power_pct:.1f}%). '
                                   f'Performance is capped. Consider: '
                                   f'(1) Increase power limit in nvidia-smi, (2) Improve PSU, '
                                   f'(3) Undervolt GPU for better efficiency.',
                    'metrics': {
                        'power_pct': power_pct,
                    }
                })
            
            # 7. PCIe bandwidth bottleneck (only alert during active load - ASPM is normal when idle)
            pcie_gen = gpu.get('pcie_gen')
            max_pcie_gen = gpu.get('max_pcie_gen')
            pcie_width = gpu.get('pcie_width')
            max_pcie_width = gpu.get('max_pcie_width')
            
            # Only alert about PCIe if GPU is actually being used (>25% util)
            # During idle, Gen1 is normal power management (ASPM)
            if pcie_gen and max_pcie_gen and pcie_gen < max_pcie_gen and gpu_util > config.THRESHOLDS['gpu']['pcie_active_util_min']:
                bottlenecks.append({
                    'type': 'pcie_degraded',
                    'severity': 'warning',
                    'title': 'PCIe Link Degraded',
                    'description': f'GPU running at PCIe Gen{pcie_gen} instead of Gen{max_pcie_gen} under load ({gpu_util}% util). '
                                   f'This reduces CPU↔GPU transfer speed. Check: '
                                   f'(1) GPU is in correct slot, (2) BIOS PCIe settings, '
                                   f'(3) Riser cable quality.',
                    'metrics': {
                        'pcie_gen': pcie_gen,
                        'max_pcie_gen': max_pcie_gen,
                        'gpu_util': gpu_util,
                    }
                })
            
            if pcie_width and max_pcie_width and pcie_width < max_pcie_width:
                bottlenecks.append({
                    'type': 'pcie_lanes_reduced',
                    'severity': 'warning',
                    'title': 'PCIe Lanes Reduced',
                    'description': f'GPU running at x{pcie_width} instead of x{max_pcie_width}. '
                                   f'Bandwidth is reduced. Verify GPU is in primary PCIe slot.',
                    'metrics': {
                        'pcie_width': pcie_width,
                        'max_pcie_width': max_pcie_width,
                    }
                })
            
            # 8. VRAM near capacity
            vram_pct = gpu.get('memory_util_pct', 0)
            if vram_pct > config.THRESHOLDS['gpu']['vram_critical_pct']:
                bottlenecks.append({
                    'type': 'vram_full',
                    'severity': 'critical',
                    'title': 'VRAM Nearly Full',
                    'description': f'VRAM usage at {vram_pct:.1f}%. Risk of OOM error. '
                                   f'Consider: (1) Reduce batch size, (2) Use gradient checkpointing, '
                                   f'(3) Enable CPU offloading, (4) Use mixed precision (FP16/BF16).',
                    'metrics': {
                        'vram_pct': vram_pct,
                    }
                })
        
        return bottlenecks


_get_bottleneck_detector = lazy_singleton(BottleneckDetector)

def detect_bottlenecks(metrics: MetricsSnapshot) -> List[Dict]:
    """Detect system bottlenecks."""
    return _get_bottleneck_detector().detect(metrics)
