"""NVIDIA GPU metrics collector using NVML."""

import pynvml
from typing import Dict, List, Optional
import time
import psutil


class GPUMetricsCollector:
    """Collects comprehensive NVIDIA GPU metrics."""
    
    def __init__(self):
        """Initialize NVML library."""
        try:
            pynvml.nvmlInit()
            self.device_count = pynvml.nvmlDeviceGetCount()
            self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(self.device_count)]
            self.initialized = True
        except Exception as e:
            print(f"Failed to initialize NVML: {e}")
            self.initialized = False
            self.device_count = 0
            self.handles = []
    
    def collect(self) -> List[Dict]:
        """Collect metrics for all GPUs."""
        if not self.initialized:
            return []
        
        metrics = []
        for i, handle in enumerate(self.handles):
            try:
                gpu_data = self._collect_single_gpu(handle, i)
                metrics.append(gpu_data)
            except Exception as e:
                print(f"Error collecting metrics for GPU {i}: {e}")
                metrics.append({"index": i, "error": str(e)})
        
        return metrics
    
    def _get_cuda_cores(self, device_name: str, compute_cap: str) -> int:
        """Get CUDA core count based on GPU model."""
        cuda_cores_map = {
            # RTX 40 series (Ada Lovelace)
            '4090': 16384, '4080': 9728, '4070 Ti': 7680, '4070': 5888, '4060 Ti': 4352, '4060': 3072,
            # RTX 30 series (Ampere)
            '3090 Ti': 10752, '3090': 10496, '3080 Ti': 10240, '3080': 8704, '3070 Ti': 6144, 
            '3070': 5888, '3060 Ti': 4864, '3060': 3584,
            # RTX 20 series (Turing)
            '2080 Ti': 4352, '2080 SUPER': 3072, '2080': 2944, '2070 SUPER': 2560, '2070': 2304,
            # A-series (Ampere - data center)
            'A100': 6912, 'A40': 10752, 'A30': 3584, 'A10': 9216,
        }
        
        for model, cores in cuda_cores_map.items():
            if model in device_name:
                return cores
        
        # Fallback: estimate from compute capability
        if compute_cap.startswith('8.'):  # Ampere
            return 10496  # Approximate for RTX 3090
        elif compute_cap.startswith('7.'):  # Turing/Volta
            return 4352  # Approximate for RTX 2080 Ti
        
        return 0  # Unknown
    
    def _get_tensor_cores(self, compute_cap: str) -> int:
        """Get tensor core count based on compute capability."""
        # Tensor cores introduced in Volta (7.0)
        if compute_cap.startswith('8.6'):  # RTX 3090
            return 328
        elif compute_cap.startswith('8.'):  # Ampere
            return 320
        elif compute_cap.startswith('7.5'):  # Turing
            return 544  # RTX 2080 Ti
        elif compute_cap.startswith('7.0'):  # Volta
            return 640  # V100
        
        return 0  # No tensor cores
    
    def _estimate_fp32_tflops(self, device_name: str, cuda_cores: int, clock_mhz: int) -> float:
        """Estimate FP32 TFLOPS performance."""
        if cuda_cores == 0 or clock_mhz == 0:
            return 0.0
        
        # FP32 TFLOPS = (CUDA cores * Clock MHz * 2 FMA ops) / 1,000,000
        tflops = (cuda_cores * clock_mhz * 2) / 1_000_000
        return round(tflops, 2)
    
    def _get_memory_bandwidth(self, device_name: str) -> float:
        """Get theoretical memory bandwidth in GB/s."""
        bandwidth_map = {
            # RTX 40 series
            '4090': 1008, '4080': 736, '4070 Ti': 504, '4070': 504,
            # RTX 30 series
            '3090 Ti': 1008, '3090': 936, '3080 Ti': 912, '3080': 760, '3070 Ti': 608,
            '3070': 448, '3060 Ti': 448, '3060': 360,
            # RTX 20 series
            '2080 Ti': 616, '2080': 448, '2070': 448,
            # A-series
            'A100': 1555, 'A40': 696, 'A30': 933,
        }
        
        for model, bw in bandwidth_map.items():
            if model in device_name:
                return bw
        
        return 0.0  # Unknown
    
    def _collect_single_gpu(self, handle, index: int) -> Dict:
        """Collect comprehensive metrics for a single GPU."""
        
        # Basic info
        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode('utf-8')
        
        # Utilization
        utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
        
        # Memory
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        
        # Temperature
        try:
            temperature = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except:
            temperature = None
        
        # Fan Speed
        try:
            fan_speed_pct = pynvml.nvmlDeviceGetFanSpeed(handle)  # Returns 0-100
        except:
            fan_speed_pct = None
        
        # Power
        try:
            power_draw = pynvml.nvmlDeviceGetPowerUsage(handle)  # mW
            power_limit = pynvml.nvmlDeviceGetEnforcedPowerLimit(handle)  # mW
        except:
            power_draw, power_limit = 0, 0
        
        # Clocks
        try:
            clock_graphics = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
            clock_sm = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_SM)
            clock_mem = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
        except:
            clock_graphics, clock_sm, clock_mem = 0, 0, 0
        
        try:
            max_clock_graphics = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
            max_clock_sm = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_SM)
            max_clock_mem = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
        except:
            max_clock_graphics, max_clock_sm, max_clock_mem = 0, 0, 0
        
        # PCIe
        try:
            pcie_gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
            max_pcie_gen = pynvml.nvmlDeviceGetMaxPcieLinkGeneration(handle)
            pcie_width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
            max_pcie_width = pynvml.nvmlDeviceGetMaxPcieLinkWidth(handle)
        except:
            pcie_gen, max_pcie_gen, pcie_width, max_pcie_width = None, None, None, None
        
        # Throttling
        try:
            clocks_throttle_reasons = pynvml.nvmlDeviceGetCurrentClocksThrottleReasons(handle)
            throttle_reasons = {
                "gpu_idle": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonGpuIdle),
                "applications_clocks_setting": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonApplicationsClocksSetting),
                "sw_power_cap": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonSwPowerCap),
                "hw_slowdown": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonHwSlowdown),
                "sync_boost": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonSyncBoost),
                "sw_thermal_slowdown": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonSwThermalSlowdown),
                "hw_thermal_slowdown": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonHwThermalSlowdown),
                "hw_power_brake_slowdown": bool(clocks_throttle_reasons & pynvml.nvmlClocksThrottleReasonHwPowerBrakeSlowdown),
            }
        except:
            throttle_reasons = {}
        
        # Architecture detection (multi-tier fallback strategy)
        architecture = None
        compute_capability = None
        
        # Tier 1: Try PCI device ID (most reliable for consumer GPUs)
        try:
            pci_info = pynvml.nvmlDeviceGetPciInfo(handle)
            pci_combined = pci_info.pciDeviceId
            pci_device_id = (pci_combined >> 16) & 0xFFFF
            
            pci_to_arch = {
                0x2204: "Ampere (RTX 3090/3090 Ti)",
                0x2206: "Ampere (RTX 3080 Ti)",
                0x2208: "Ampere (RTX 3080)",
                0x220A: "Ampere (RTX 3070 Ti)",
                0x2684: "Ada Lovelace (RTX 4090)",
                0x2704: "Ada Lovelace (RTX 4080 SUPER)",
                0x2782: "Ada Lovelace (RTX 4080)",
                0x1E04: "Turing (RTX 2080 Ti)",
                0x1E07: "Turing (RTX 2080)",
                0x1B06: "Pascal (GTX 1080 Ti)",
            }
            
            architecture = pci_to_arch.get(pci_device_id)
            if architecture:
                # Got it from PCI ID
                compute_capability = "8.6" if "Ampere" in architecture else None
        except Exception as e:
            pass
        
        # Tier 2: Parse GPU name string if PCI failed
        if not architecture:
            try:
                name_lower = name.lower()
                if "3090" in name_lower:
                    architecture = "Ampere (RTX 3090)"
                    compute_capability = "8.6"
                elif "3080" in name_lower:
                    architecture = "Ampere (RTX 3080)"
                    compute_capability = "8.6"
                elif "3070" in name_lower:
                    architecture = "Ampere (RTX 3070)"
                    compute_capability = "8.6"
                elif "4090" in name_lower:
                    architecture = "Ada Lovelace (RTX 4090)"
                    compute_capability = "8.9"
                elif "4080" in name_lower:
                    architecture = "Ada Lovelace (RTX 4080)"
                    compute_capability = "8.9"
                elif "2080 ti" in name_lower:
                    architecture = "Turing (RTX 2080 Ti)"
                    compute_capability = "7.5"
                elif "a100" in name_lower:
                    architecture = "Ampere (A100)"
                    compute_capability = "8.0"
            except:
                pass
        
        # Tier 3: Try compute capability API (unreliable - may not exist in all pynvml versions)
        if not architecture:
            try:
                # Some pynvml versions don't have this function
                if hasattr(pynvml, 'nvmlDeviceGetCudaComputeCapability'):
                    major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                    compute_capability = f"{major}.{minor}"
                    
                    arch_map = {
                        (8, 6): "Ampere",
                        (8, 9): "Ada Lovelace",
                        (8, 0): "Ampere (A100)",
                        (7, 5): "Turing",
                        (7, 0): "Volta",
                        (6, 1): "Pascal",
                    }
                    architecture = arch_map.get((major, minor), f"Compute {compute_capability}")
            except:
                pass
        
        # Final fallback
        if not architecture:
            architecture = "Unknown"
            compute_capability = None
        
        # Process information with GPU utilization (if supported)
        top_processes = self._get_top_gpu_processes(handle)
        
        # Calculate Deep Learning Metrics
        cuda_cores = self._get_cuda_cores(name, compute_capability or "")
        tensor_cores = self._get_tensor_cores(compute_capability or "")
        fp32_tflops = self._estimate_fp32_tflops(name, cuda_cores, clock_graphics) # Use clock_graphics for FP32 TFLOPS
        memory_bandwidth_gbps = self._get_memory_bandwidth(name)
        
        # VRAM driver/context overhead analysis
        vram_overhead = self._analyze_vram_overhead(handle, top_processes, mem_info)
        
        # Real PCIe bandwidth
        pcie_bandwidth = self._measure_pcie_bandwidth(handle)
        
        return {
            "index": index,
            "name": name,
            "gpu_util": utilization.gpu,
            "memory_util": utilization.memory,
            "memory_used_gb": round(mem_info.used / (1024**3), 2),
            "memory_free_gb": round(mem_info.free / (1024**3), 2),
            "memory_total_gb": round(mem_info.total / (1024**3), 2),
            "memory_util_pct": round((mem_info.used / mem_info.total) * 100, 1),
            "temperature": temperature,
            "fan_speed_pct": fan_speed_pct,
            "power_draw_w": round(power_draw / 1000, 1),
            "power_limit_w": round(power_limit / 1000, 1),
            "power_pct": round((power_draw / power_limit) * 100, 1) if power_limit > 0 else 0,
            "clock_graphics_mhz": clock_graphics,
            "clock_sm_mhz": clock_sm,
            "clock_mem_mhz": clock_mem,
            "max_clock_graphics_mhz": max_clock_graphics,
            "max_clock_sm_mhz": max_clock_sm,
            "max_clock_mem_mhz": max_clock_mem,
            "pcie_gen": pcie_gen,
            "pcie_width": pcie_width,
            "max_pcie_gen": max_pcie_gen,
            "max_pcie_width": max_pcie_width,
            "pcie_bandwidth_mbps": pcie_bandwidth,
            "architecture": architecture,
            "compute_capability": compute_capability,
            "cuda_cores": cuda_cores,
            "tensor_cores": tensor_cores,
            "fp32_tflops": fp32_tflops,
            "memory_bandwidth_gbps": memory_bandwidth_gbps,
            "throttle_reasons": throttle_reasons,
            "top_processes": top_processes,
            "vram_overhead": vram_overhead,
        }
    
    def _get_top_gpu_processes(self, handle) -> List[Dict]:
        """Get top GPU processes with memory and compute utilization."""
        try:
            processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
        except:
            try:
                processes = pynvml.nvmlDeviceGetGraphicsRunningProcesses(handle)
            except:
                return []
        
        process_list = []
        for proc in processes:
            try:
                ps_proc = psutil.Process(proc.pid)
                name = ps_proc.name()
                cmdline = ' '.join(ps_proc.cmdline())[:100]
                
                # Try to get per-process GPU utilization (newer NVML feature)
                gpu_util_pct = None
                try:
                    # This requires newer CUDA/Driver versions (CUDA 11.0+)
                    util_sample = pynvml.nvmlDeviceGetProcessUtilization(handle, proc.pid, 1000)
                    if util_sample:
                        gpu_util_pct = util_sample[0].smUtil
                except:
                    pass  # Not available on all systems
                
                # FIX BUG-C02: Calculate raw memory value, add debug logging, filter noise
                memory_mb = proc.usedGpuMemory / (1024**2)  # Raw MB value
                
                # Debug logging to diagnose VRAM reporting issues
                if memory_mb > 0:
                    print(f"[GPU Process Debug] PID {proc.pid} ({name}): {memory_mb:.3f} MB raw VRAM")
                
                # Only include processes using > 50 MB to filter noise
                # This prevents showing many tiny allocations that obscure real memory hogs
                if memory_mb >= 50:
                    process_list.append({
                        "pid": proc.pid,
                        "name": name,
                        "cmdline": cmdline,
                        "memory_used_mb": round(memory_mb, 3),  # 3 decimals for precision
                        "memory_used_gb": round(memory_mb / 1024, 3),  # Also provide GB
                        "gpu_util_pct": gpu_util_pct,  # May be None if not supported
                    })
            except psutil.NoSuchProcess:
                continue
            except Exception as e:
                continue
        
        # Sort by memory usage
        process_list.sort(key=lambda x: x['memory_used_mb'], reverse=True)
        return process_list[:10]  # Top 10
    
    def _analyze_vram_overhead(self, handle, processes: List[Dict], mem_info) -> Dict:
        """Analyze VRAM driver and context overhead.
        
        Note: This measures the gap between NVML-reported used memory and process allocations.
        This gap includes driver overhead, CUDA context, and framebuffer - NOT true fragmentation.
        True VRAM fragmentation cannot be measured via NVML APIs.
        """
        total_vram_gb = mem_info.total / (1024**3)
        used_vram_gb = mem_info.used / (1024**3)
        free_vram_gb = mem_info.free / (1024**3)
        
        if not processes:
            return {
                "overhead_percentage": 0.0,
                "free_vram_gb": free_vram_gb,
                "overhead_status": "No overhead (no processes)",
            }
        
        # Sum allocated memory from processes
        allocated_by_processes_mb = sum(p['memory_used_mb'] for p in processes)
        allocated_by_processes_gb = allocated_by_processes_mb / 1024
        
        # Calculate driver/context overhead: gap between reported used and process allocations
        overhead_vram_gb = used_vram_gb - allocated_by_processes_gb
        overhead_pct = max(0, (overhead_vram_gb / total_vram_gb) * 100)
        
        if overhead_pct > 10:
            status = "High driver overhead"
        elif overhead_pct > 5:
            status = "Normal driver overhead"
        else:
            status = "Low driver overhead"
        
        return {
            "overhead_percentage": round(overhead_pct, 1),
            "allocated_by_processes_gb": round(allocated_by_processes_gb, 2),
            "driver_overhead_gb": round(max(0, overhead_vram_gb), 2),
            "free_vram_gb": round(free_vram_gb, 2),
            "overhead_status": status,
        }
    
    def _measure_pcie_bandwidth(self, handle) -> Optional[float]:
        """Measure real-time PCIe bandwidth."""
        try:
            # Get PCIe throughput (requires newer drivers)
            tx_bytes = pynvml.nvmlDeviceGetPcieThroughput(
                handle, pynvml.NVML_PCIE_UTIL_TX_BYTES
            )
            rx_bytes = pynvml.nvmlDeviceGetPcieThroughput(
                handle, pynvml.NVML_PCIE_UTIL_RX_BYTES
            )
            
            # Convert KB/s to MB/s (NVML returns KB/s)
            total_mbps = (tx_bytes + rx_bytes) / 1024
            return round(total_mbps, 2)
        except:
            # Feature not available on all drivers
            return None
    
    def __del__(self):
        """Cleanup NVML.
        
        Note: We intentionally do NOT call nvmlShutdown() here. NVML is designed
        to stay initialized for the lifetime of the process. Calling nvmlShutdown()
        on errors prevents re-initialization and causes permanent GPU metrics failure.
        The NVML library will automatically clean up when the process exits.
        """
        pass  # Intentionally empty - see docstring


# Singleton instance
_gpu_collector = None

def get_gpu_metrics() -> List[Dict]:
    """Get current GPU metrics for all GPUs."""
    global _gpu_collector
    if _gpu_collector is None:
        _gpu_collector = GPUMetricsCollector()
    return _gpu_collector.collect()
