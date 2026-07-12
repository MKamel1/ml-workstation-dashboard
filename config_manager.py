"""Configuration manager for persistent dashboard settings."""

import json
import os
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """Manages persistent configuration for the dashboard."""
    
    DEFAULT_CONFIG = {
        "refresh_interval_ms": 1000,
        "chart_history_seconds": 60,
        "alert_thresholds": {
            "gpu_util": 95,
            "gpu_temp": 85,
            "gpu_memory": 90,
            "cpu_util": 90,
            "memory_util": 85,
            "swap_active": True,  # Alert if swap is active at all
        },
        "ui_preferences": {
            "dark_mode": True,
            "show_per_core_cpu": True,
            "compact_mode": False,
        },
        "data_retention_days": 7,
        "gpu_display_order": [],  # Empty = auto-detect order
    }
    
    def __init__(self, config_file: str = "dashboard_config.json"):
        """Initialize configuration manager."""
        self.config_file = Path(config_file)
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to handle new keys
                    config = self.DEFAULT_CONFIG.copy()
                    config.update(loaded_config)
                    return config
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
                return self.DEFAULT_CONFIG.copy()
        else:
            # Create default config file
            self.save_config(self.DEFAULT_CONFIG)
            return self.DEFAULT_CONFIG.copy()
    
    def save_config(self, config: Dict[str, Any] = None) -> bool:
        """Save configuration to file."""
        if config is None:
            config = self.config
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value: Any, save: bool = True) -> bool:
        """Set configuration value and optionally save."""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        
        if save:
            return self.save_config()
        return True
    
    def reset_to_defaults(self) -> bool:
        """Reset configuration to defaults."""
        self.config = self.DEFAULT_CONFIG.copy()
        return self.save_config()


# Singleton instance
_config_manager = None

def get_config_manager() -> ConfigManager:
    """Get global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager
