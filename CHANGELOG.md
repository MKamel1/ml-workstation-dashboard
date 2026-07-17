# ML Workstation Dashboard - Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

### Added

- **Fan Profiles**: Quiet/Performance toggle on the dashboard, backed by CoolerControl. Performance mode runs the case/AIO fans and the GPU's own fans on a more aggressive curve for sustained DL/LLM training load; the AIO pump always stays fixed at 100% regardless of mode. Requires `COOLERCONTROL_PASSWORD` set in the environment.
- **Live trend charts**: Memory usage and network throughput now have 60-second history charts (matching the existing CPU chart), not just a current number.
- **System Power panel**: Estimated total power draw (GPU + CPU package, via NVML and Linux RAPL) with a live chart. CPU package power needs a one-time sudo step to unlock a kernel permission (see README) — without it, the total falls back to GPU-only.
- **OpenRGB self-recovery**: the dashboard now tries to start `openrgb.service` itself if it's down when the Lighting panel connects, since it's been observed not to auto-start after a reboot. Needs a one-time narrowly-scoped sudo rule (see README) to be allowed to do so.

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
