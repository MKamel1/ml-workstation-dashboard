"""Unit tests for critical bug fixes."""

import unittest
import time
import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


class TestCriticalFixes(unittest.TestCase):
    """Test suite for 8 critical bug fixes."""
    
    def test_timestamp_is_unix_epoch(self):
        """BUG-C06: Verify timestamp uses time.time() not event loop time."""
        # Import after sys.path is set
        from app import collect_all_metrics
        
        metrics = collect_all_metrics()
        now = time.time()
        
        # Timestamp should be within 5 seconds of current time
        # (metrics collection can take a few seconds)
        self.assertAlmostEqual(metrics['timestamp'], now, delta=5.0)
        
        # Unix timestamp should be ~1.7 billion (year 2024+)
        self.assertGreater(metrics['timestamp'], 1700000000)
        self.assertLess(metrics['timestamp'], 2000000000)  # Before year 2033
        
        print(f"✓ Timestamp is valid Unix epoch: {metrics['timestamp']}")
    
    def test_config_validation_catches_invalid_thresholds(self):
        """BUG-I11: Verify config validation catches warning > critical."""
        
        # Test 1: GPU temperature warning >= critical (should fail)
        invalid_thresholds = {
            "gpu": {
                "temperature_warning": 90,
                "temperature_critical": 80,  # Invalid: warning > critical
                "memory_warning": 90,
                "memory_critical": 95,
                "utilization_low": 30,
            },
            "cpu": {
                "temperature_warning": 80,
                "temperature_critical": 90,
                "utilization_high": 90,
            },
            "memory": {
                "usage_warning": 85,
                "usage_critical": 95,
                "swap_critical": 0,
            },
            "storage": {
                "iops_low": 1000,
                "latency_high": 50,
            },
        }
        
        # Temporarily replace THRESHOLDS
        original_thresholds = config.THRESHOLDS
        config.THRESHOLDS = invalid_thresholds
        
        with self.assertRaises(ValueError) as context:
            config.validate_config()
        
        self.assertIn("temperature_warning", str(context.exception))
        print(f"✓ Config validation caught invalid GPU temperature thresholds")
        
        # Restore original
        config.THRESHOLDS = original_thresholds
    
    def test_config_validation_passes_valid_config(self):
        """BUG-I11: Verify valid config passes validation."""
        try:
            result = config.validate_config()
            self.assertTrue(result)
            print("✓ Valid config passes validation")
        except ValueError as e:
            self.fail(f"Valid config failed validation: {e}")
    
    def test_nvml_shutdown_not_called(self):
        """BUG-C04: Verify NVML shutdown is not called in __del__."""
        from metrics.gpu_metrics import GPUMetricsCollector
        import inspect
        
        # Get the source code of __del__
        source = inspect.getsource(GPUMetricsCollector.__del__)
        
        # Should contain 'pass' indicating intentionally empty implementation
        self.assertIn("pass", source.lower())
        
        # Should NOT actually CALL nvmlShutdown (check for the function call, not just mention)
        # The docstring may mention it, but the implementation should not call it
        # Look for actual invocation pattern
        self.assertNotIn("pynvml.nvmlShutdown()", source)
        
        print("✓ NVML shutdown correctly omitted from __del__")
    
    def test_fragmentation_renamed_to_overhead(self):
        """BUG-C07: Verify fragmentation metric renamed to overhead."""
        from metrics.gpu_metrics import GPUMetricsCollector
        import inspect
        
        # Get method names
        methods = [method for method in dir(GPUMetricsCollector) if not method.startswith('_')]
        
        # Should NOT have _analyze_vram_fragmentation
        self.assertNotIn("_analyze_vram_fragmentation", dir(GPUMetricsCollector))
        
        # Should have _analyze_vram_overhead
        self.assertIn("_analyze_vram_overhead", dir(GPUMetricsCollector))
        
        print("✓ Fragmentation metric correctly renamed to overhead")
    
    def test_error_handling_in_websocket(self):
        """BUG-C03: Verify WebSocket has error handling."""
        from app import websocket_endpoint
        import inspect
        
        source = inspect.getsource(websocket_endpoint)
        
        # Should have try-except for collect_all_metrics
        self.assertIn("try:", source)
        self.assertIn("except Exception", source)
        self.assertIn("consecutive_errors", source)
        
        # Should send error state to client
        self.assertIn("error_metrics", source)
        
        print("✓ WebSocket has comprehensive error handling")


class TestMultiGPUHistory(unittest.TestCase):
    """Test multi-GPU history tracking."""
    
    def test_gpu_histories_structure(self):
        """BUG-C02: Verify gpuHistories uses per-GPU tracking."""
        # Read dashboard.js and check structure
        with open('static/dashboard.js', 'r') as f:
            content = f.read()
        
        # Should have gpuHistories object
        self.assertIn("gpuHistories", content)
        self.assertIn("gpuHistories[index]", content)
        
        # Should NOT use metricsHistory.gpu_util for GPU charts
        # (that was the bug - all GPUs shared same array)
        
        # Check that each GPU gets its own history
        self.assertIn("gpuHistories[index].gpu_util", content)
        self.assertIn("gpuHistories[index].timestamps", content)
        
        print("✓ Multi-GPU history uses per-GPU tracking")


class TestWebSocketConnectionFlag(unittest.TestCase):
    """Test WebSocket duplicate connection prevention."""
    
    def test_connecting_flag_exists(self):
        """BUG-C01: Verify isConnecting flag prevents duplicates."""
        with open('static/dashboard.js', 'r') as f:
            content = f.read()
        
        # Should have isConnecting flag
        self.assertIn("isConnecting", content)
        
        # Should check flag before connecting
        self.assertIn("if (isConnecting)", content)
        
        # Should set flag before creating WebSocket
        self.assertIn("isConnecting = true", content)
        
        # Should clear flag in all handlers
        self.assertIn("isConnecting = false", content)
        
        print("✓ WebSocket has isConnecting flag to prevent duplicates")


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)
