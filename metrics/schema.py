"""Authoritative shape of the "metrics dict" produced by metrics/*.py.

This is the single source of truth for CONTRACT-01: every collector's
public get_x_metrics() function, detection/bottleneck_detector.py,
detection/anomaly_detector.py, database/__init__.py, and static/dashboard.js
all agree on this shape *by convention only* -- nothing enforces it except
this file (via type-checker/IDE support) and tests/test_metrics_schema.py
(which asserts real collector output matches these keys at runtime).

These TypedDicts document the CURRENT wire format, warts included. They do
NOT redesign it: renaming a key here would be a breaking change out of scope
for this file. If you need to change a collector's output shape, update the
TypedDict in the same commit.

Comments below call out fields that only one side of a producer/consumer
pair actually uses -- that's the kind of drift this schema is meant to catch
before it silently breaks something.
"""

from typing import Dict, List, Optional, TypedDict


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------

class ThrottleReasons(TypedDict, total=False):
    """From GPUMetricsCollector._collect_single_gpu (metrics/gpu_metrics.py).

    Comes back as {} (not one of the flags below) when the NVML read fails --
    detection/bottleneck_detector.py does unconditional .get() calls on this
    dict, so it relies on missing-key-defaults-to-falsy rather than an
    explicit False for every key.
    """
    gpu_idle: bool
    applications_clocks_setting: bool
    sw_power_cap: bool
    hw_slowdown: bool
    sync_boost: bool
    sw_thermal_slowdown: bool
    hw_thermal_slowdown: bool
    hw_power_brake_slowdown: bool


class GPUProcessInfo(TypedDict, total=False):
    """One entry in GPUMetrics['top_processes']."""
    pid: int
    name: str
    cmdline: str
    memory_used_mb: float
    memory_used_gb: float
    gpu_util_pct: Optional[int]  # None if the driver doesn't support per-process util


class VRAMOverhead(TypedDict, total=False):
    """GPUMetrics['vram_overhead']. Keys differ depending on whether any
    process was using the GPU when this was computed (see
    GPUMetricsCollector._analyze_vram_overhead) -- the no-process branch
    only sets overhead_percentage/free_vram_gb/overhead_status.
    """
    overhead_percentage: float
    free_vram_gb: float
    overhead_status: str
    allocated_by_processes_gb: float  # only present when processes list is non-empty
    driver_overhead_gb: float  # only present when processes list is non-empty


class GPUMetrics(TypedDict, total=False):
    """One entry of the list returned by get_gpu_metrics() (metrics/gpu_metrics.py).

    NOTE: on a per-GPU collection error, GPUMetricsCollector.collect() appends
    {"index": i, "error": str(e)} instead of this full shape (see collect()
    in gpu_metrics.py) -- a consumer that blindly assumes every field below
    is present will KeyError/None on that GPU for that tick. Existing
    consumers use .get() with defaults, which tolerates this.
    """
    index: int
    name: str
    error: str  # only present on a per-GPU collection failure, replaces the rest of this dict's fields
    gpu_util: int
    memory_util: int  # read by detection/bottleneck_detector.py; not currently read by dashboard.js
    memory_used_gb: float
    memory_free_gb: float
    memory_total_gb: float
    memory_util_pct: float
    temperature: Optional[float]
    fan_speed_pct: Optional[int]
    power_draw_w: Optional[float]
    power_limit_w: Optional[float]
    power_pct: Optional[float]
    clock_graphics_mhz: Optional[int]
    clock_sm_mhz: Optional[int]
    clock_mem_mhz: Optional[int]
    max_clock_graphics_mhz: Optional[int]
    max_clock_sm_mhz: Optional[int]
    max_clock_mem_mhz: Optional[int]
    pcie_gen: Optional[int]
    pcie_width: Optional[int]
    max_pcie_gen: Optional[int]
    max_pcie_width: Optional[int]
    pcie_bandwidth_mbps: Optional[float]
    architecture: Optional[str]
    compute_capability: Optional[str]
    cuda_cores: int
    tensor_cores: int
    fp32_tflops: Optional[float]
    memory_bandwidth_gbps: float
    throttle_reasons: ThrottleReasons
    top_processes: List[GPUProcessInfo]
    vram_overhead: VRAMOverhead


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------

class CPUCoreMetrics(TypedDict, total=False):
    """One entry of CPUMetrics['cores']. 'temperature' is only present when
    per-core temps were readable (see CPUMetricsCollector.collect)."""
    index: int
    frequency_mhz: Optional[float]
    utilization: float
    temperature: float


class CPUFeatures(TypedDict):
    avx: bool
    avx2: bool
    avx512: bool
    sse4_2: bool


class CPUMetrics(TypedDict):
    """Returned by get_cpu_metrics() (metrics/cpu_metrics.py)."""
    brand: str
    physical_cores: Optional[int]
    logical_cores: Optional[int]
    architecture: str
    frequency_current_mhz: Optional[float]
    frequency_max_mhz: Optional[float]
    utilization_total: float
    load_1min: float
    load_5min: float
    load_15min: float
    temperature: Optional[float]
    temperature_max: Optional[float]
    cores: List[CPUCoreMetrics]
    per_core_utils: List[float]  # dashboard.js reads this flat array directly; 'cores' above is the richer per-core structure
    context_switches: int
    interrupts: int
    features: CPUFeatures


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class MemoryMetrics(TypedDict):
    """Returned by get_memory_metrics() (metrics/memory_metrics.py)."""
    total_gb: float
    used_gb: float
    free_gb: float
    available_gb: float
    buffers_gb: float
    cached_gb: float
    percent: float
    swap_total_gb: float
    swap_used_gb: float
    swap_percent: float
    swap_active: bool
    actual_used_gb: float
    ram_speed_mhz: Optional[int]  # None if dmidecode requires a sudo password (see _detect_ram_speed)
    numa_nodes: int
    active_gb: float
    inactive_gb: float


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

class DiskPartition(TypedDict):
    device: str
    mountpoint: str
    fstype: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class DiskIOStats(TypedDict):
    name: str
    read_mb_s: float
    write_mb_s: float
    read_iops: float
    write_iops: float


class StorageMetrics(TypedDict):
    """Returned by get_storage_metrics() (metrics/storage_metrics.py).

    'disk_io' is [] on the very first collection tick (needs a previous
    sample to compute a rate) -- consumers already treat it as
    possibly-empty, not possibly-missing.
    """
    partitions: List[DiskPartition]
    disk_io: List[DiskIOStats]
    huggingface_cache_gb: Optional[float]  # None only on a filesystem-walk error, distinct from a genuinely empty/missing cache (0)


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

class NetworkMetrics(TypedDict):
    """Returned by get_network_metrics() (metrics/network_metrics.py).

    Live throughput since the last collection tick, aggregated across the
    device's physical network interfaces only (see
    NetworkMetricsCollector._physical_interfaces in metrics/network_metrics.py
    for why virtual ones -- docker0, veth*, tailscale0 -- are excluded rather
    than summed alongside them). Not an active speed test against an external
    server, which would be a slower, bandwidth-consuming measurement unlike
    every other collector in this app.

    Units are megabits/sec (bits, matching how ISPs/routers report "speed"),
    which is deliberately different from disk_io's megabytes/sec convention
    -- don't confuse the two when reading/comparing values.

    Both fields are 0.0 on the very first collection tick (needs a previous
    sample to compute a rate), same convention as StorageMetrics['disk_io'].
    """
    download_mbps: float
    upload_mbps: float


# ---------------------------------------------------------------------------
# ML
# ---------------------------------------------------------------------------

class MLProcessInfo(TypedDict, total=False):
    """One entry of MLMetrics['active_processes']."""
    pid: int
    cmdline: str
    frameworks: List[str]
    cpu_percent: float
    memory_percent: float
    create_time: float
    runtime: str
    runtime_seconds: int
    gpu_vram_gb: Optional[float]
    gpu_util_pct: Optional[int]
    hf_model: str  # only present when a HuggingFace-style "org/model" pattern was found in the cmdline


class ActiveEnvironment(TypedDict):
    type: str  # "venv" or "conda"
    name: str
    path: str


class MLMetrics(TypedDict):
    """Returned by get_ml_metrics() (metrics/ml_metrics.py)."""
    active_processes: List[MLProcessInfo]
    cuda_version: Optional[str]
    installed_packages: Dict[str, Dict[str, str]]  # category name -> {package name: version}
    active_environments: List[ActiveEnvironment]


# ---------------------------------------------------------------------------
# Fans
# ---------------------------------------------------------------------------

class SystemFanInfo(TypedDict):
    index: int
    label: str
    type: str
    rpm: int
    rpm_pct: int
    pwm_pct: Optional[int]


class FanMetrics(TypedDict, total=False):
    """Returned by get_system_fan_metrics() (metrics/fan_metrics.py).

    'fans' and 'total_fans' are only present when 'available' is True (no
    hwmon fan controller detected -> only {"available": False, "fans": []}).
    """
    available: bool
    chip: str
    fans: List[SystemFanInfo]
    total_fans: int


# ---------------------------------------------------------------------------
# Top-level snapshot -- see collect_raw_metrics()/collect_all_metrics() in app.py
# ---------------------------------------------------------------------------

class MetricsSnapshot(TypedDict, total=False):
    """The full dict sent over /ws and returned by GET /api/metrics, and the
    shape database/__init__.py's insert_metrics()/query_metrics() read from
    and reconstruct.

    'bottlenecks' and 'anomalies' are produced by detection/*.py from this
    same dict (each alert is a free-form {type, severity, title, description,
    metrics} dict, not typed further here -- they're display-only payloads,
    not re-consumed structured data).

    On a collection failure, app.py's websocket loop sends a smaller
    fallback dict instead: {timestamp, error: True, error_message, gpu: [],
    cpu: {}, memory: {}, storage: {}, ml: {}, fans: {}, network: {},
    bottlenecks: [], anomalies: []} -- both paths agree on which keys are
    present, just with empty placeholder values on the error path.
    """
    timestamp: float
    gpu: List[GPUMetrics]
    cpu: CPUMetrics
    memory: MemoryMetrics
    storage: StorageMetrics
    ml: MLMetrics
    fans: FanMetrics
    network: NetworkMetrics
    bottlenecks: List[Dict]
    anomalies: List[Dict]
    error: bool  # only present on a metrics-collection failure
    error_message: str  # only present on a metrics-collection failure
    db_warning: str  # only present when a DB insert failed for this tick
