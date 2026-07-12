"""Configuration settings for the workstation dashboard."""

# Server settings
HOST = "0.0.0.0"  # reachable over Tailscale (and LAN) for remote viewing, not just localhost
PORT = 8000

# Update interval (seconds)
UPDATE_INTERVAL = 1.0

# Historical data retention in days.
# app.py's cleanup_old_metrics(retention_days=7) call sites should be updated
# to use retention_days=config.RETENTION_DAYS instead of the hardcoded literal.
RETENTION_DAYS = 7

# Alert thresholds - single source of truth for detection/bottleneck_detector.py.
# Every threshold the detector compares a metric against lives here; the detector
# imports config and reads config.THRESHOLDS[...] instead of using inline literals.
THRESHOLDS = {
    "gpu": {
        "temperature_warning": 80,  # °C
        "temperature_critical": 85,
        "memory_warning": 90,  # % of VRAM
        "memory_critical": 95,
        "utilization_low": 30,  # % - for bottleneck detection
        "active_util_min": 5,  # % - above this, GPU is considered actively in use (not idle)
        "pcie_active_util_min": 25,  # % - only flag PCIe link degradation above this utilization
        "vram_critical_pct": 95,  # % of VRAM - risk of OOM
    },
    "cpu": {
        "temperature_warning": 80,
        "temperature_critical": 90,
        "utilization_high": 90,
    },
    "memory": {
        "usage_warning": 85,  # % of RAM
        "usage_critical": 95,
        "usage_high": 90,  # % of RAM - standalone "high memory" warning
        # Real swap policy (per BUG-C01): swap usage alone is NOT critical.
        # It's only a problem once it's large AND RAM is under pressure -
        # a little OS swap activity with plenty of free RAM is normal and not alerted on.
        "swap_critical_gb": 1.0,
        "swap_critical_ram_pct": 85,
        "swap_warning_gb": 0.5,
        "swap_warning_ram_pct": 75,
    },
    "storage": {
        "iops_low": 1000,  # For bottleneck detection
        "latency_high": 50,  # ms
        "io_high_mbs": 500,  # MB/s combined read+write - high disk I/O for data-loading bottleneck check
    },
}

# Anomaly detection settings
ANOMALY_DETECTION = {
    "enabled": True,
    "window_size": 60,  # Number of samples for rolling statistics
    "z_score_threshold": 3.0,  # Standard deviations for anomaly
}

# Bottleneck detection settings
BOTTLENECK_DETECTION = {
    "enabled": True,
    "gpu_underutil_threshold": 50,  # % - below this is considered underutilized
    "cpu_high_threshold": 80,  # % - above this is high utilization
    "io_high_threshold": 80,  # % - disk utilization threshold
}


def validate_config():
    """Validate configuration thresholds are sensible.
    
    Raises:
        ValueError: If configuration is invalid with helpful error message
    """
    # GPU thresholds
    gpu = THRESHOLDS["gpu"]
    if gpu["temperature_warning"] >= gpu["temperature_critical"]:
        raise ValueError(
            f"GPU temperature_warning ({gpu['temperature_warning']}) must be < "
            f"temperature_critical ({gpu['temperature_critical']})"
        )
    if gpu["memory_warning"] >= gpu["memory_critical"]:
        raise ValueError(
            f"GPU memory_warning ({gpu['memory_warning']}) must be < "
            f"memory_critical ({gpu['memory_critical']})"
        )
    
    # CPU thresholds
    cpu = THRESHOLDS["cpu"]
    if cpu["temperature_warning"] >= cpu["temperature_critical"]:
        raise ValueError(
            f"CPU temperature_warning ({cpu['temperature_warning']}) must be < "
            f"temperature_critical ({cpu['temperature_critical']})"
        )
    
    # Memory thresholds
    mem = THRESHOLDS["memory"]
    if mem["usage_warning"] >= mem["usage_critical"]:
        raise ValueError(
            f"Memory usage_warning ({mem['usage_warning']}) must be < "
            f"usage_critical ({mem['usage_critical']})"
        )
    
    # Range checks
    if not (0 <= gpu["temperature_critical"] <= 200):
        raise ValueError(f"GPU temperature_critical must be 0-200°C, got {gpu['temperature_critical']}")
    if not (0 <= gpu["memory_critical"] <= 100):
        raise ValueError(f"GPU memory_critical must be 0-100%, got {gpu['memory_critical']}")
    
    return True


# Validate configuration on module import
try:
    validate_config()
except ValueError as e:
    print(f"[ERROR] Invalid configuration: {e}")
    raise
