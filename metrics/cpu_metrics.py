"""CPU metrics collector."""

import psutil
import cpuinfo
import os
from typing import Dict, List

from metrics.schema import CPUMetrics


class CPUMetricsCollector:
    """Collects detailed CPU metrics."""
    
    def __init__(self):
        """Initialize CPU info."""
        self.cpu_info = cpuinfo.get_cpu_info()
        self.cpu_count = psutil.cpu_count(logical=False)
        self.logical_count = psutil.cpu_count(logical=True)
        
    def collect(self) -> Dict:
        """Collect comprehensive CPU metrics."""
        
        # Per-core frequency and utilization
        per_core_freq = psutil.cpu_freq(percpu=True)
        per_core_util = psutil.cpu_percent(interval=0, percpu=True)
        
        # Aggregate metrics
        cpu_freq = psutil.cpu_freq()
        cpu_percent = psutil.cpu_percent(interval=0)
        
        # Load average
        load_avg = os.getloadavg()
        
        # Temperature (Linux-specific)
        temps = {}
        try:
            temp_info = psutil.sensors_temperatures()
            if 'coretemp' in temp_info:
                # Intel
                core_temps = [t.current for t in temp_info['coretemp'] if 'Core' in t.label]
                if core_temps:
                    temps['cores'] = core_temps
                    temps['average'] = sum(core_temps) / len(core_temps)
                    temps['max'] = max(core_temps)
            elif 'k10temp' in temp_info:
                # AMD Ryzen
                for sensor in temp_info['k10temp']:
                    if 'Tctl' in sensor.label or 'Tdie' in sensor.label:
                        temps['cpu'] = sensor.current
                        break
            elif 'zenpower' in temp_info:
                # AMD Ryzen with zenpower driver
                for sensor in temp_info['zenpower']:
                    if 'Tdie' in sensor.label:
                        temps['cpu'] = sensor.current
                        break
        except Exception:
            temps = {}
        
        # Context switches and interrupts
        ctx_switches = psutil.cpu_stats().ctx_switches
        interrupts = psutil.cpu_stats().interrupts
        
        # Per-core data
        cores = []
        for i, (freq, util) in enumerate(zip(per_core_freq or [], per_core_util)):
            core_data = {
                "index": i,
                "frequency_mhz": round(freq.current, 0) if freq else None,
                "utilization": round(util, 1),
            }
            # Add per-core temp if available
            if 'cores' in temps and i < len(temps['cores']):
                core_data['temperature'] = round(temps['cores'][i], 1)
            cores.append(core_data)
        
        # CPU features
        features = {
            "avx": 'avx' in self.cpu_info.get('flags', []),
            "avx2": 'avx2' in self.cpu_info.get('flags', []),
            "avx512": any('avx512' in flag for flag in self.cpu_info.get('flags', [])),
            "sse4_2": 'sse4_2' in self.cpu_info.get('flags', []),
        }
        
        return {
            # CPU info
            "brand": self.cpu_info.get('brand_raw', 'Unknown'),
            "physical_cores": self.cpu_count,
            "logical_cores": self.logical_count,
            "architecture": self.cpu_info.get('arch', 'Unknown'),
            
            # Aggregate metrics
            "frequency_current_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
            "frequency_max_mhz": round(cpu_freq.max, 0) if cpu_freq else None,
            "utilization_total": round(cpu_percent, 1),
            
            # Load average
            "load_1min": round(load_avg[0], 2),
            "load_5min": round(load_avg[1], 2),
            "load_15min": round(load_avg[2], 2),
            
            # Temperature
            "temperature": temps.get('cpu') or temps.get('average'),
            "temperature_max": temps.get('max'),
            
            # Per-core data
            "cores": cores,
            "per_core_utils": per_core_util,  # Simple array for frontend color-coding
            
            # Statistics
            "context_switches": ctx_switches,
            "interrupts": interrupts,
            
            # Features
            "features": features,
        }


# Singleton instance
_cpu_collector = None

def get_cpu_metrics() -> CPUMetrics:
    """Get current CPU metrics. See metrics/schema.py:CPUMetrics for the shape."""
    global _cpu_collector
    if _cpu_collector is None:
        _cpu_collector = CPUMetricsCollector()
    return _cpu_collector.collect()
