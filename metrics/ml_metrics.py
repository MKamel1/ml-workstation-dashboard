"""ML framework and process monitoring."""

import psutil
import subprocess
import os
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional


class MLMetricsCollector:
    """Collects ML framework and environment metrics."""
    
    # Top ML/DL frameworks to detect
    FRAMEWORK_PATTERNS = {
        'PyTorch': ['torch', 'pytorch'],
        'TensorFlow': ['tensorflow', 'tf.'],
        'JAX': ['jax'],
        'Keras': ['keras'],
        'HuggingFace': ['transformers', 'datasets', 'accelerate'],
        'FastAI': ['fastai'],
        'LightGBM': ['lightgbm', 'lgb'],
        'XGBoost': ['xgboost', 'xgb'],
        'CatBoost': ['catboost'],
        'Scikit-learn': ['sklearn', 'scikit-learn'],
        'MXNet': ['mxnet'],
        'Causal ML': ['dowhy', 'causalml', 'econml'],
        'ONNX': ['onnx'],
        'Optuna': ['optuna'],
    }
    
    def __init__(self):
        """Initialize ML environment detection."""
        self._package_cache = None
        self._cache_time = 0
    
    def collect(self) -> Dict:
        """Collect ML-related metrics."""
        
        # Get GPU handles for per-process metrics
        gpu_handles = []
        try:
            import pynvml
            pynvml.nvmlInit()
            gpu_count = pynvml.nvmlDeviceGetCount()
            gpu_handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(gpu_count)]
        except:
            pass
        
        # Detect active Python processes with ML frameworks
        ml_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_percent', 'create_time']):
            try:
                if proc.info['name'] and 'python' in proc.info['name'].lower():
                    cmdline = ' '.join(proc.info['cmdline'] or [])
                    
                    # Check for ML frameworks in command line
                    frameworks = self._detect_frameworks_in_cmdline(cmdline)
                    
                    if frameworks:
                        # Get comprehensive process info
                        proc_data = self._get_enhanced_process_info(
                            proc, 
                            cmdline, 
                            frameworks, 
                            gpu_handles
                        )
                        ml_processes.append(proc_data)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Get CUDA version
        cuda_version = self._get_cuda_version()
        
        # Get installed ML packages (with caching)
        installed_packages = self._get_installed_packages()
        
        # Detect active virtual environments
        active_envs = self._detect_active_environments()
        
        return {
            "active_processes": ml_processes,
            "cuda_version": cuda_version,
            "installed_packages": installed_packages,
            "active_environments": active_envs,
        }
    
    def _get_enhanced_process_info(self, proc, cmdline: str, frameworks: List[str], gpu_handles) -> Dict:
        """Get comprehensive ML process metrics."""
        import time
        import re
        
        proc_info = {
            'pid': proc.info['pid'],
            'cmdline': cmdline if len(cmdline) <= 150 else cmdline[:147] + '...',  # Less aggressive truncation
            'frameworks': frameworks,
            'cpu_percent': round(proc.info['cpu_percent'] or 0, 1),
            'memory_percent': round(proc.info['memory_percent'] or 0, 1),
            'create_time': proc.info['create_time'],
        }
        
        # Calculate runtime
        runtime_seconds = time.time() - proc.info['create_time']
        hours = int(runtime_seconds // 3600)
        minutes = int((runtime_seconds % 3600) // 60)
        if hours > 0:
            proc_info['runtime'] = f"{hours}h {minutes}m"
        else:
            proc_info['runtime'] = f"{minutes}m"
        proc_info['runtime_seconds'] = int(runtime_seconds)
        
        # Try to get GPU metrics
        proc_info['gpu_vram_gb'] = None
        proc_info['gpu_util_pct'] = None
        
        if gpu_handles:
            try:
                import pynvml
                for gpu_handle in gpu_handles:
                    # Get VRAM usage for this process
                    try:
                        gpu_procs = pynvml.nvmlDeviceGetComputeRunningProcesses(gpu_handle)
                        for gp in gpu_procs:
                            if gp.pid == proc.info['pid']:
                                proc_info['gpu_vram_gb'] = round(gp.usedGpuMemory / (1024**3), 2)
                                break
                    except:
                        pass
                    
                    # Try to get per-process GPU utilization (newer drivers only)
                    if proc_info['gpu_vram_gb'] is not None:
                        try:
                            # This requires CUDA 11.0+ and newer drivers
                            util_samples = pynvml.nvmlDeviceGetProcessUtilization(gpu_handle, proc.info['pid'], 1000)
                            if util_samples and len(util_samples) > 0:
                                proc_info['gpu_util_pct'] = util_samples[0].smUtil
                        except:
                            pass  # Not available on all systems
                        break
            except:
                pass
        
        # Detect HuggingFace model from command line
        model_match = re.search(r'([a-zA-Z0-9_-]+/[a-zA-Z0-9_\.-]+)', cmdline)
        if model_match:
            potential_model = model_match.group(1)
            # Basic validation - should have format like "meta-llama/Llama-2-7b"
            if '/' in potential_model and len(potential_model) > 5:
                proc_info['hf_model'] = potential_model
        
        return proc_info
    
    def _detect_frameworks_in_cmdline(self, cmdline: str) -> List[str]:
        """Detect ML frameworks in command line."""
        frameworks = []
        cmdline_lower = cmdline.lower()
        
        for framework, patterns in self.FRAMEWORK_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in cmdline_lower:
                    frameworks.append(framework)
                    break  # Only add framework once
        
        return frameworks
    
    def _get_cuda_version(self) -> str:
        """Get CUDA version from nvcc or nvidia-smi."""
        # Try nvcc first
        try:
            result = subprocess.run(['nvcc', '--version'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'release' in line.lower():
                        version = line.split('release')[1].split(',')[0].strip()
                        return version
        except:
            pass
        
        # Try nvidia-smi
        try:
            result = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'CUDA Version' in line:
                        version = line.split('CUDA Version:')[1].split()[0].strip()
                        return version
        except:
            pass
        
        return None
    
    def _get_installed_packages(self) -> Dict[str, str]:
        """Get versions of installed ML packages from current environment."""
        import time
        
        # Cache for 60 seconds
        if self._package_cache and (time.time() - self._cache_time) < 60:
            return self._package_cache

        # Define package categories
        categories = {
            'Deep Learning Frameworks': [
                'torch', 'tensorflow', 'jax', 'flax', 'mxnet', 'paddle', 'oneflow'
            ],
            'ML Libraries': [
                'sklearn', 'scikit-learn', 'xgboost', 'lightgbm', 'catboost',
                'optuna', 'ray'  
            ],
            'Transformers & NLP': [
                'transformers', 'tokenizers', 'sentencepiece', 'datasets',
                'accelerate', 'peft', 'bitsandbytes'
            ],
            'Computer Vision': [
                'opencv-python', 'cv2', 'pillow', 'torchvision', 'albumentations',
                'timm', 'mmcv'
            ],
            'Data Science': [
                'numpy', 'pandas', 'scipy', 'polars', 'dask'
            ],
            'Visualization': [
                'matplotlib', 'seaborn', 'plotly', 'bokeh', 'wandb', 'tensorboard'
            ],
            'Development Tools': [
                'jupyter', 'ipython', 'notebook', 'jupyterlab',
                'black', 'pytest'
            ]
        }
        
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'list', '--format=json'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                all_packages = json.loads(result.stdout)
                
                # Categorize packages
                categorized = {}
                for category, package_list in categories.items():
                    categorized[category] = {}
                    for pkg in all_packages:
                        pkg_name = pkg['name'].lower()
                        # Check if package matches category
                        if any(ml_pkg in pkg_name for ml_pkg in package_list):
                            categorized[category][pkg['name']] = pkg['version']
                
                # Remove empty categories
                categorized = {k: v for k, v in categorized.items() if v}
                
                self._package_cache = categorized
                self._cache_time = time.time()
                return categorized
            
        except Exception as e:
            print(f"Error getting packages: {e}")
        
        self._package_cache = {}  # Cache empty result on error
        self._cache_time = time.time()
        return self._package_cache
    
    def _detect_active_environments(self) -> List[Dict]:
        """Detect active virtual environments."""
        envs = []
        
        # Check for virtualenv/venv
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            env_path = sys.prefix
            env_name = Path(env_path).name
            envs.append({
                'type': 'venv',
                'name': env_name,
                'path': env_path,
            })
        
        # Check for conda
        conda_env = os.environ.get('CONDA_DEFAULT_ENV')
        if conda_env:
            conda_prefix = os.environ.get('CONDA_PREFIX', '')
            envs.append({
                'type': 'conda',
                'name': conda_env,
                'path': conda_prefix,
            })
        
        return envs


# Singleton instance
_ml_collector = None

def get_ml_metrics() -> Dict:
    """Get current ML metrics."""
    global _ml_collector
    if _ml_collector is None:
        _ml_collector = MLMetricsCollector()
    return _ml_collector.collect()
