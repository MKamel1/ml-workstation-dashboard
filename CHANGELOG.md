# ML Workstation Dashboard - Changelog

All notable changes to this project are documented in this file.

## [1.1.0] - 2025-12-21

### Added

- **Systemd Service**: Background service support with auto-start on login
- **Desktop Launcher**: Ubuntu application menu integration with quick actions
- **Custom Icon**: GPU-themed icon with monitoring elements
- **Control Script**: `dashboard.sh` for easy service management

### Fixed

- **BUG-C01**: False swap memory alerts (threshold increased from 0.1 GB to 1.0 GB + RAM pressure check)
- **BUG-C02**: VRAM process display improvements (3 decimals, filtering <50MB, debug logging)
- **BUG-C12**: False bottleneck alerts (now requires active GPU processes + >5% GPU utilization)
- **CRITICAL**: sudo dmidecode blocking issue causing dashboard to hang (added `-n` flag and `stdin=DEVNULL`)

### Verified

- **BUG-C05**: Timestamp already using Unix epoch correctly
- **BUG-C06 + ENH-C04**: Multi-GPU chart tracking already implemented

### Changed

- Memory metrics collection now non-blocking (RAM speed shows "Unknown" if sudo requires password)
- Improved error handling in all metrics collectors
- Enhanced process filtering and precision in GPU metrics

## [1.0.0] - 2025-12-20

### Initial Release

- Real-time WebSocket metrics streaming
- GPU monitoring (NVML/CUDA)
- CPU monitoring with per-core utilization
- Memory and swap monitoring
- Storage and I/O monitoring
- ML framework detection
- Bottleneck detection system
- Anomaly detection
- Chart.js visualizations
- Multi-GPU support (infrastructure)
