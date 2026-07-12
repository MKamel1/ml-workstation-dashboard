"""Memory subsystem metrics collector."""

import psutil
import subprocess
import re
from typing import Dict, Optional

from metrics.schema import MemoryMetrics


class MemoryMetricsCollector:
    """Collects RAM and memory subsystem metrics."""
    
    def __init__(self):
        """Initialize memory tracking."""
        self.previous_stats = None
        self.ram_speed_mhz = self._detect_ram_speed()
        self.numa_nodes = self._detect_numa()
    
    def _detect_ram_speed(self) -> Optional[int]:
        """Detect RAM speed in MHz using dmidecode.
        
        CRITICAL: Uses -n (non-interactive) and stdin=DEVNULL to prevent sudo 
        password prompt from blocking the dashboard. If sudo requires a password,
        this will fail silently and return None (RAM speed is diagnostic only).
        """
        try:
            result = subprocess.run(
                ['sudo', '-n', 'dmidecode', '-t', '17'],  # -n: non-interactive
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,  # Prevent password prompt blocking
                timeout=2
            )
            if result.returncode == 0:
                # Look for "Speed: XXXX MT/s" or "Configured Memory Speed: XXXX MT/s"
                match = re.search(r'(?:Configured )?(?:Memory )?Speed: (\d+) MT/s', result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            # Silently fail - RAM speed is diagnostic info, not critical
            pass
        return None
    
    def _detect_numa(self) -> int:
        """Detect number of NUMA nodes."""
        try:
            result = subprocess.run(
                ['lscpu'],
                capture_output=True,
                text=True,
                timeout=1
            )
            if result.returncode == 0:
                match = re.search(r'NUMA node\(s\):\s+(\d+)', result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return 1  # Default to 1 NUMA node
    
    def collect(self) -> Dict:
        """Collect memory metrics."""
        
        # Virtual memory (RAM)
        mem = psutil.virtual_memory()
        
        # Swap
        swap = psutil.swap_memory()
        
        # Get page fault stats
        vm_stat = psutil.virtual_memory()
        
        return {
            # RAM usage
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "free_gb": round(mem.free / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "buffers_gb": round(mem.buffers / (1024**3), 2) if hasattr(mem, 'buffers') else 0,
            "cached_gb": round(mem.cached / (1024**3), 2) if hasattr(mem, 'cached') else 0,
            "percent": round(mem.percent, 1),
            
            # Swap (CRITICAL to monitor for ML)
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_percent": round(swap.percent, 1) if swap.total > 0 else 0,
            "swap_active": swap.used > 0,  # Flag for alert
            
            # Derived metrics
            "actual_used_gb": round((mem.used - (getattr(mem, 'buffers', 0) + getattr(mem, 'cached', 0))) / (1024**3), 2),
            
            # Advanced diagnostics
            "ram_speed_mhz": self.ram_speed_mhz,
            "numa_nodes": self.numa_nodes,
            "active_gb": round(getattr(mem, 'active', 0) / (1024**3), 2),
            "inactive_gb": round(getattr(mem, 'inactive', 0) / (1024**3), 2),
        }


# Singleton instance
_memory_collector = None

def get_memory_metrics() -> MemoryMetrics:
    """Get current memory metrics. See metrics/schema.py:MemoryMetrics for the shape."""
    global _memory_collector
    if _memory_collector is None:
        _memory_collector = MemoryMetricsCollector()
    return _memory_collector.collect()
