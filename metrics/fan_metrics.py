"""System fan monitoring for motherboard and case fans."""

import os
import glob
from typing import Dict, List, Optional

from metrics.schema import FanMetrics
from util import lazy_singleton


class SystemFanCollector:
    """Collects system fan metrics from hwmon (motherboard sensors)."""
    
    def __init__(self):
        """Initialize system fan collector and detect available sensors."""
        self.hwmon_path = self._find_fan_hwmon()
        self.fan_count = 0
        self.fan_labels = {}
        
        if self.hwmon_path:
            self._detect_fans()
    
    def _find_fan_hwmon(self) -> Optional[str]:
        """Find the hwmon device that has fan sensors (usually nct6799/nct6775)."""
        # Look for common fan controller chips
        target_chips = ['nct6799', 'nct6775', 'nct6683', 'it8792', 'it87']
        
        for hwmon_dir in glob.glob('/sys/class/hwmon/hwmon*'):
            try:
                name_file = os.path.join(hwmon_dir, 'name')
                if os.path.exists(name_file):
                    with open(name_file, 'r') as f:
                        chip_name = f.read().strip()
                        if any(target in chip_name for target in target_chips):
                            # Verify it has fan sensors
                            fan_files = glob.glob(os.path.join(hwmon_dir, 'fan*_input'))
                            if fan_files:
                                return hwmon_dir
            except Exception:
                continue
        
        return None
    
    def _detect_fans(self):
        """Detect available fan inputs."""
        if not self.hwmon_path:
            return
        
        # Count fan inputs
        fan_files = glob.glob(os.path.join(self.hwmon_path, 'fan*_input'))
        self.fan_count = len(fan_files)
        
        # Try to read labels for each fan
        for i in range(1, 10):  # Check up to fan9
            label_file = os.path.join(self.hwmon_path, f'fan{i}_label')
            if os.path.exists(label_file):
                try:
                    with open(label_file, 'r') as f:
                        self.fan_labels[i] = f.read().strip()
                except Exception:
                    pass
    
    def _read_fan_rpm(self, fan_num: int) -> Optional[int]:
        """Read RPM for a specific fan number."""
        if not self.hwmon_path:
            return None
        
        fan_file = os.path.join(self.hwmon_path, f'fan{fan_num}_input')
        try:
            if os.path.exists(fan_file):
                with open(fan_file, 'r') as f:
                    rpm = int(f.read().strip())
                    return rpm  # Return actual value including 0 for inactive fans
        except Exception:
            pass
        
        return None
    
    def _read_pwm_value(self, fan_num: int) -> Optional[int]:
        """Read PWM value (0-255) for a specific fan."""
        if not self.hwmon_path:
            return None
        
        pwm_file = os.path.join(self.hwmon_path, f'pwm{fan_num}')
        try:
            if os.path.exists(pwm_file):
                with open(pwm_file, 'r') as f:
                    pwm = int(f.read().strip())
                    return round((pwm / 255) * 100)  # Convert to percentage
        except Exception:
            pass
        
        return None
    
    def collect(self) -> Dict:
        """
        Collect all system fan metrics.
        
        Returns dict with fan speeds in RPM and PWM percentages.
        """
        if not self.hwmon_path:
            return {
                'available': False,
                'fans': []
            }
        
        fans = []
        
        # Collect data for each fan
        for fan_num in range(1, 10):  # Check fans 1-9
            rpm = self._read_fan_rpm(fan_num)
            
            if rpm is None:
                continue  # Fan sensor doesn't exist
            
            # Get custom label to check if this fan is configured
            label = self._get_fan_label(fan_num)
            is_configured = label != f"System Fan {fan_num}"
            
            # Include fans that are either:
            # 1. Spinning (RPM > 0)
            # 2. Configured with custom name (even if 0 RPM) - for VRM/special fans
            if rpm > 0 or is_configured:
                pwm_pct = self._read_pwm_value(fan_num)
                
                # Get fan type first (needed for percentage calculation)
                fan_type = self._identify_fan_type(fan_num, rpm)
                
                # Calculate RPM as percentage of typical max
                rpm_pct = self._calculate_rpm_percentage(rpm, fan_type) if rpm > 0 else 0
                
                fans.append({
                    'index': fan_num,
                    'label': label,
                    'type': fan_type,
                    'rpm': rpm,
                    'rpm_pct': rpm_pct,  # RPM as percentage of max
                    'pwm_pct': pwm_pct   # PWM control percentage
                })
        
        return {
            'available': True,
            'chip': os.path.basename(self.hwmon_path),
            'fans': fans,
            'total_fans': len(fans)
        }
    
    def _identify_fan_type(self, fan_num: int, rpm: int) -> str:
        """
        Identify fan type based on configuration file.
        Falls back to heuristic detection if not in config.
        """
        try:
            from fan_config import FAN_CONFIG
            if fan_num in FAN_CONFIG:
                name, fan_type, desc = FAN_CONFIG[fan_num]
                if fan_type:
                    return fan_type
        except Exception:
            pass
        
        # Fallback to heuristic detection
        if rpm > 2500:
            return 'aio_pump'
        elif 800 <= rpm <= 2000:
            if fan_num in [1, 2]:
                return 'case_front'
            elif fan_num == 3:
                return 'case_rear'
            else:
                return 'aio_fan'
        elif rpm < 800:
            return 'case_fan'
        
        return 'unknown'
    
    def _get_fan_label(self, fan_num: int) -> str:
        """Get custom fan label from configuration."""
        try:
            from fan_config import FAN_CONFIG
            if fan_num in FAN_CONFIG:
                name, fan_type, desc = FAN_CONFIG[fan_num]
                if name:
                    return name
        except Exception:
            pass
        
        return f"System Fan {fan_num}"
    
    def _calculate_rpm_percentage(self, rpm: int, fan_type: str) -> int:
        """
        Convert RPM to percentage based on expected max RPM for fan type.
        
        Typical max RPMs:
        - AIO Pump: 5000-6000 RPM
        - Case fans: 1500-2000 RPM
        """
        if fan_type == 'aio_pump':
            max_rpm = 5000
        elif fan_type in ['case_front', 'case_rear', 'case_fan']:
            max_rpm = 2000
        elif fan_type == 'aio_fan':
            max_rpm = 2500
        else:
            max_rpm = 2000  # Default
        
        percentage = min(100, round((rpm / max_rpm) * 100))
        return percentage


_get_system_fan_collector = lazy_singleton(SystemFanCollector)

def get_system_fan_metrics() -> FanMetrics:
    """Get system fan metrics. See metrics/schema.py:FanMetrics for the shape."""
    return _get_system_fan_collector().collect()
