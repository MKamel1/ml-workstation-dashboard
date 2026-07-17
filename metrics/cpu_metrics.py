"""CPU metrics collector."""

import psutil
import cpuinfo
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from metrics.schema import CPUMetrics
from util import lazy_singleton


class CPUMetricsCollector:
    """Collects detailed CPU metrics."""

    def __init__(self):
        """Initialize CPU info."""
        self.cpu_info = cpuinfo.get_cpu_info()
        self.cpu_count = psutil.cpu_count(logical=False)
        self.logical_count = psutil.cpu_count(logical=True)

        self._rapl_path = self._find_rapl_package_path()
        self._rapl_max_range_uj = self._read_int_file(self._rapl_path / 'max_energy_range_uj') if self._rapl_path else None
        if self._rapl_max_range_uj is None:
            self._rapl_path = None
        # previous_time deliberately None, not time.time(): the first
        # collect() call must skip the power computation entirely rather
        # than divide by whatever near-zero time has elapsed since
        # construction -- same convention as NetworkMetricsCollector's
        # previous_time=None (metrics/network_metrics.py).
        self._prev_energy_uj = self._read_int_file(self._rapl_path / 'energy_uj') if self._rapl_path else None
        self._prev_energy_time = None

    @staticmethod
    def _find_rapl_package_path() -> Optional[Path]:
        """Find the RAPL 'package-N' powercap domain (CPU socket-level total
        energy) -- not one of its 'core'/'uncore' subdomains, which would
        double-count energy already included in the parent package reading.

        Requires root to actually read energy_uj (kernel default
        permissions); returns the path regardless so collect() can detect
        that case and report None rather than crashing (see README for the
        one-time udev rule that unlocks it for a regular user).
        """
        base = Path('/sys/class/powercap')
        if not base.is_dir():
            return None
        for domain in sorted(base.glob('intel-rapl:*')):
            if domain.name.count(':') != 1:
                continue  # skip subdomains like intel-rapl:0:0 (core/uncore)
            try:
                if (domain / 'name').read_text().strip().startswith('package'):
                    return domain
            except OSError:
                continue
        return None

    @staticmethod
    def _read_int_file(path: Path) -> Optional[int]:
        try:
            return int(path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _read_package_power_w(self) -> Optional[float]:
        """Average CPU package power (Watts) since the last collect() call,
        computed from RAPL's cumulative energy counter -- same delta-over-
        interval approach as NetworkMetricsCollector.collect() (bytes ->
        rate), just energy -> power. None if RAPL isn't available/readable,
        or on the very first tick (needs a previous sample for the delta).
        """
        if not self._rapl_path:
            return None
        current_energy = self._read_int_file(self._rapl_path / 'energy_uj')
        current_time = time.time()
        if current_energy is None:
            return None

        power_w = None
        if self._prev_energy_time is not None:
            time_delta = current_time - self._prev_energy_time
            if time_delta > 0:
                energy_delta = current_energy - self._prev_energy_uj
                if energy_delta < 0:
                    # The counter wrapped around (hits max_energy_range_uj
                    # then resets to 0) -- add the range back to get the
                    # real elapsed energy instead of a bogus negative power.
                    energy_delta += self._rapl_max_range_uj
                power_w = round((energy_delta / 1_000_000) / time_delta, 1)

        self._prev_energy_uj = current_energy
        self._prev_energy_time = current_time
        return power_w

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

        # Package power (RAPL, needs root -- see _read_package_power_w)
        package_power_w = self._read_package_power_w()

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
            
            # Power
            "package_power_w": package_power_w,

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


_get_cpu_collector = lazy_singleton(CPUMetricsCollector)

def get_cpu_metrics() -> CPUMetrics:
    """Get current CPU metrics. See metrics/schema.py:CPUMetrics for the shape."""
    return _get_cpu_collector().collect()
