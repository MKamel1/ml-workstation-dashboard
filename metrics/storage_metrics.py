"""Storage I/O metrics collector."""

import psutil
import os
from typing import Dict, List
from pathlib import Path


class StorageMetricsCollector:
    """Collects storage I/O and health metrics."""
    
    def __init__(self):
        """Initialize storage tracking."""
        self.previous_io = psutil.disk_io_counters(perdisk=True)
        self.previous_time = None
        
    def collect(self) -> Dict:
        """Collect storage metrics."""
        
        import time
        current_time = time.time()
        
        # Disk usage for main partitions
        partitions = []
        for part in psutil.disk_partitions():
            if part.fstype and 'loop' not in part.device:
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    partitions.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": round(usage.total / (1024**3), 2),
                        "used_gb": round(usage.used / (1024**3), 2),
                        "free_gb": round(usage.free / (1024**3), 2),
                        "percent": round(usage.percent, 1),
                    })
                except:
                    continue
        
        # I/O statistics
        current_io = psutil.disk_io_counters(perdisk=True)
        
        disk_io = []
        if self.previous_io and self.previous_time:
            time_delta = current_time - self.previous_time
            
            for disk_name, counters in current_io.items():
                if disk_name in self.previous_io:
                    prev = self.previous_io[disk_name]
                    
                    # Calculate rates (bytes/sec)
                    read_bytes_sec = (counters.read_bytes - prev.read_bytes) / time_delta
                    write_bytes_sec = (counters.write_bytes - prev.write_bytes) / time_delta
                    
                    # IOPS
                    read_iops = (counters.read_count - prev.read_count) / time_delta
                    write_iops = (counters.write_count - prev.write_count) / time_delta
                    
                    disk_io.append({
                        "name": disk_name,
                        "read_mb_s": round(read_bytes_sec / (1024**2), 2),
                        "write_mb_s": round(write_bytes_sec / (1024**2), 2),
                        "read_iops": round(read_iops, 0),
                        "write_iops": round(write_iops, 0),
                    })
        
        self.previous_io = current_io
        self.previous_time = current_time
        
        # HuggingFace cache size (ML-specific)
        hf_cache_size_gb = 0
        hf_cache_path = Path.home() / '.cache' / 'huggingface'
        if hf_cache_path.exists():
            try:
                total_size = sum(f.stat().st_size for f in hf_cache_path.rglob('*') if f.is_file())
                hf_cache_size_gb = round(total_size / (1024**3), 2)
            except:
                pass
        
        return {
            "partitions": partitions,
            "disk_io": disk_io,
            "huggingface_cache_gb": hf_cache_size_gb,
        }


# Singleton instance
_storage_collector = None

def get_storage_metrics() -> Dict:
    """Get current storage metrics."""
    global _storage_collector
    if _storage_collector is None:
        _storage_collector = StorageMetricsCollector()
    return _storage_collector.collect()
