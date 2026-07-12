"""Self-check: config.THRESHOLDS is the single source every detector reads from."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# Every threshold key detection/bottleneck_detector.py reads via config.THRESHOLDS[...]
REQUIRED = {
    "gpu": ["active_util_min", "pcie_active_util_min", "vram_critical_pct"],
    "memory": [
        "swap_critical_gb", "swap_critical_ram_pct",
        "swap_warning_gb", "swap_warning_ram_pct",
        "usage_high",
    ],
    "storage": ["io_high_mbs"],
}

for section, keys in REQUIRED.items():
    assert section in config.THRESHOLDS, f"missing THRESHOLDS section: {section}"
    for key in keys:
        assert key in config.THRESHOLDS[section], f"missing THRESHOLDS['{section}']['{key}']"

assert config.validate_config() is True

assert config.RETENTION_DAYS == 7

print("ok")
