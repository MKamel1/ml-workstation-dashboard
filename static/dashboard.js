// WebSocket connection and dashboard logic
//
// Wire format: the `metrics` object handled below (metrics.gpu, metrics.cpu,
// metrics.memory, metrics.storage, metrics.ml, metrics.fans, ...) is the JSON
// serialization of MetricsSnapshot in metrics/schema.py -- that file is the
// canonical source of truth for field names/shape. This file stays plain
// dict-based JS; update the Python TypedDicts first if the shape changes.

let ws = null;
let reconnectInterval = null;
let isConnecting = false;  // Guards connectWebSocket() against overlapping reconnect attempts
let charts = {};

// Keyed per GPU index so multi-GPU systems get independent chart history instead of sharing one series
let gpuHistories = {};  // { 0: {gpu_util: [], gpu_temp: [], timestamps: []}, 1: {...}, ... }

// Global history for CPU and memory
let metricsHistory = {
    cpu_util: [],
    memory_pct: [],
    timestamps: []
};

const MAX_HISTORY_POINTS = 60; // 60 seconds of history

// Note: DOMContentLoaded initialization moved to bottom of file

// Connection uptime tracking
let connectionStartTime = null;
let uptimeInterval = null;

function initCPUChart() {
    const cpuCtx = document.getElementById('cpu-util-chart');
    if (cpuCtx && !charts.cpuUtil) {
        charts.cpuUtil = new Chart(cpuCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'CPU Utilization',
                    data: [],
                    borderColor: '#00ff88',
                    backgroundColor: 'rgba(0, 255, 136, 0.1)',
                    tension: 0.4,
                    fill: true
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        ticks: { color: getChartThemeColors().tickColor },
                        grid: { color: getChartThemeColors().gridColor }
                    },
                    x: {
                        // Capped at 4 labels to avoid overlap on the x-axis
                        ticks: { color: getChartThemeColors().tickColor, maxTicksLimit: 4 },
                        grid: {
                            color: getChartThemeColors().gridColor,
                            display: true
                        }
                    }
                }
            }
        });
    }
}

function updateCharts(metrics) {
    const now = new Date();
    const timeStr = now.toLocaleTimeString();

    // Update global history (CPU, memory)
    metricsHistory.timestamps.push(timeStr);
    if (metrics.cpu) {
        metricsHistory.cpu_util.push(metrics.cpu.utilization_total || 0);
    }
    if (metrics.memory) {
        metricsHistory.memory_pct.push(metrics.memory.percent || 0);
    }

    // Keep only last 60 points for global history
    if (metricsHistory.timestamps.length > MAX_HISTORY_POINTS) {
        metricsHistory.timestamps.shift();
        metricsHistory.cpu_util.shift();
        metricsHistory.memory_pct.shift();
    }

    // Update per-GPU history
    if (metrics.gpu && metrics.gpu.length > 0) {
        metrics.gpu.forEach((gpu, index) => {
            // Initialize history for this GPU if it doesn't exist
            if (!gpuHistories[index]) {
                gpuHistories[index] = {
                    gpu_util: [],
                    gpu_temp: [],
                    timestamps: []
                };
            }

            // Add current data point
            gpuHistories[index].timestamps.push(timeStr);
            gpuHistories[index].gpu_util.push(gpu.gpu_util || 0);
            gpuHistories[index].gpu_temp.push(gpu.temperature || 0);

            // Keep only last 60 points
            if (gpuHistories[index].timestamps.length > MAX_HISTORY_POINTS) {
                gpuHistories[index].timestamps.shift();
                gpuHistories[index].gpu_util.shift();
                gpuHistories[index].gpu_temp.shift();
            }

            // Update chart for this GPU
            const chartKey = `gpu${index}Util`;
            if (charts[chartKey]) {
                charts[chartKey].data.labels = gpuHistories[index].timestamps;
                charts[chartKey].data.datasets[0].data = gpuHistories[index].gpu_util;
                charts[chartKey].update('none');
            }
        });
    }

    // Update CPU chart
    if (charts.cpuUtil) {
        charts.cpuUtil.data.labels = metricsHistory.timestamps;
        charts.cpuUtil.data.datasets[0].data = metricsHistory.cpu_util;
        charts.cpuUtil.update('none');
    }
}

function connectWebSocket() {
    // Bail out if a connection attempt is already in flight
    if (isConnecting) {
        console.log('[WebSocket] Already connecting, ignoring duplicate request');
        return;
    }

    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
        console.log('[WebSocket] Already connected or connecting');
        return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    console.log(`[WebSocket] Connecting to ${wsUrl}...`);
    isConnecting = true;  // Set flag before creating connection
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('[WebSocket] ✅ Connected successfully');
        isConnecting = false;  // Clear flag on successful connection

        // Clear any reconnection interval
        if (reconnectInterval) {
            clearInterval(reconnectInterval);
            reconnectInterval = null;
        }
        updateConnectionStatus(true);
    };

    ws.onmessage = (event) => {
        try {
            const metrics = JSON.parse(event.data);

            // Handle error state from server
            if (metrics.error) {
                console.warn('[Metrics] Server reported error:', metrics.error_message);
                // Could display error banner to user here
            }

            updateDashboard(metrics);
        } catch (error) {
            console.error('[WebSocket] Error parsing metrics:', error);
        }
    };

    ws.onerror = (error) => {
        console.error('[WebSocket] ❌ Connection error:', error);
        isConnecting = false;  // Clear flag on error
    };

    ws.onclose = (event) => {
        console.log(`[WebSocket] 🔌 Connection closed (code: ${event.code})`);
        ws = null;
        isConnecting = false;  // Clear flag on close

        stopUptimeCounter();
        connectionStartTime = null;

        updateConnectionStatus(false);

        // Auto-reconnect after 3 seconds (if not already reconnecting)
        if (!reconnectInterval) {
            console.log('[WebSocket] ⏱️  Will reconnect in 3 seconds...');
            reconnectInterval = setTimeout(() => {
                reconnectInterval = null;
                connectWebSocket();
            }, 3000);
        }
    };
}

// Uptime tracking functions
function startUptimeCounter() {
    stopUptimeCounter(); // Clear any existing
    uptimeInterval = setInterval(updateUptimeDisplay, 1000);
    updateUptimeDisplay();
}

function stopUptimeCounter() {
    if (uptimeInterval) {
        clearInterval(uptimeInterval);
        uptimeInterval = null;
    }
}

function updateUptimeDisplay() {
    if (!connectionStartTime) return;

    const uptime = Math.floor((Date.now() - connectionStartTime) / 1000);
    const hours = Math.floor(uptime / 3600);
    const minutes = Math.floor((uptime % 3600) / 60);
    const seconds = uptime % 60;

    let uptimeText = hours > 0 ? `${hours}h ${minutes}m`
        : minutes > 0 ? `${minutes}m ${seconds}s`
            : `${seconds}s`;

    // Update display
    const statusEl = document.querySelector('.connection-status');
    if (statusEl) {
        let uptimeSpan = statusEl.querySelector('.uptime');
        if (!uptimeSpan) {
            uptimeSpan = document.createElement('span');
            uptimeSpan.className = 'uptime';
            uptimeSpan.style.cssText = 'margin-left: 0.5rem; color: #888; font-size: 0.85rem;';
            statusEl.appendChild(uptimeSpan);
        }
        uptimeSpan.textContent = `(${uptimeText})`;
    }
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connection-status');
    if (statusEl) {
        statusEl.className = `connection-status ${connected ? 'connected' : 'disconnected'}`;
        statusEl.innerHTML = `
            <div class="status-dot"></div>
            ${connected ? 'Connected' : 'Disconnected'}
        `;
    }
}

function updateDashboard(metrics) {
    // Update GPU metrics (multi-GPU support)
    if (metrics.gpu && metrics.gpu.length > 0) {
        updateMultiGPUPanel(metrics.gpu);
    }

    // Update CPU metrics
    if (metrics.cpu) {
        updateCPUPanel(metrics.cpu);
    }

    // Update memory metrics
    if (metrics.memory) {
        updateMemoryPanel(metrics.memory);
    }

    // Update storage metrics
    if (metrics.storage) {
        updateStoragePanel(metrics.storage);
    }

    // Update network throughput
    if (metrics.network) {
        updateNetworkPanel(metrics.network);
    }

    // Update ML metrics
    if (metrics.ml) {
        updateMLPanel(metrics.ml);
    }

    // Update fans
    if (metrics.fans) {
        updateFansPanel(metrics.gpu, metrics.fans);
    }

    // Update alerts (bottlenecks + anomalies)
    const allAlerts = [...(metrics.bottlenecks || []), ...(metrics.anomalies || [])];
    updateAlertsPanel(allAlerts);

    // Update charts with historical data
    updateCharts(metrics);
}

let currentGPU = 0;

function updateMultiGPUPanel(gpuList) {
    const tabsContainer = document.getElementById('gpu-tabs');
    const containersDiv = document.getElementById('gpu-containers');

    if (!tabsContainer || !containersDiv) return;

    // Create tabs if needed
    if (tabsContainer.children.length !== gpuList.length) {
        tabsContainer.innerHTML = '';
        gpuList.forEach((gpu, index) => {
            const tab = document.createElement('div');
            tab.className = `gpu-tab ${index === currentGPU ? 'active' : ''}`;
            tab.textContent = `GPU ${index}: ${gpu.name || 'Unknown'}`;
            tab.onclick = () => switchGPU(index);
            tabsContainer.appendChild(tab);
        });
    }

    // Create/update GPU containers
    if (containersDiv.children.length !== gpuList.length) {
        containersDiv.innerHTML = '';
        gpuList.forEach((gpu, index) => {
            const container = document.createElement('div');
            container.className = `gpu-container ${index === currentGPU ? 'active' : ''}`;
            container.id = `gpu-${index}`;
            container.innerHTML = createGPUContent(index);
            containersDiv.appendChild(container);
        });

        // Initialize charts for each GPU
        gpuList.forEach((gpu, index) => {
            initGPUChart(index);
        });
    }

    // Update each GPU's data
    gpuList.forEach((gpu, index) => {
        updateSingleGPU(gpu, index);
    });
}

function switchGPU(index) {
    currentGPU = index;

    // Update tabs
    document.querySelectorAll('.gpu-tab').forEach((tab, i) => {
        tab.classList.toggle('active', i === index);
    });

    // Update containers
    document.querySelectorAll('.gpu-container').forEach((container, i) => {
        container.classList.toggle('active', i === index);
    });
}

function createGPUContent(index) {
    return `
        <div class="metrics-row">
            <div class="metric">
                <div class="metric-label">Utilization</div>
                <div class="metric-value" id="gpu-${index}-util">-</div>
                <div class="progress-bar" id="gpu-${index}-util-bar">
                    <div class="progress-fill"></div>
                </div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Memory</div>
                <div class="metric-value" id="gpu-${index}-memory" style="font-size: 1.1rem;">-</div>
                <div class="progress-bar" id="gpu-${index}-memory-bar">
                    <div class="progress-fill"></div>
                </div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Temperature</div>
                <div class="metric-value" id="gpu-${index}-temp">-</div>
                <div class="progress-bar" id="gpu-${index}-temp-bar">
                    <div class="progress-fill"></div>
                </div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Fan Speed</div>
                <div class="metric-value" id="gpu-${index}-fan">-</div>
                <div class="progress-bar" id="gpu-${index}-fan-bar">
                    <div class="progress-fill"></div>
                </div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Power</div>
                <div class="metric-value" id="gpu-${index}-power" style="font-size: 1.1rem;">-</div>
                <div class="progress-bar" id="gpu-${index}-power-bar">
                    <div class="progress-fill"></div>
                </div>
            </div>
        </div>
        
        <div class="metrics-row" style="margin-top: 1rem;">
            <div class="metric">
                <div class="metric-label">CUDA Cores</div>
                <div class="metric-value" id="gpu-${index}-cuda-cores" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Tensor Cores</div>
                <div class="metric-value" id="gpu-${index}-tensor-cores" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">FP32 Performance</div>
                <div class="metric-value" id="gpu-${index}-tflops" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Memory Bandwidth</div>
                <div class="metric-value" id="gpu-${index}-bandwidth" style="font-size: 1rem;">-</div>
            </div>
        </div>
        
        <div class="metrics-row" style="margin-top: 1rem;">
            <div class="metric">
                <div class="metric-label">Architecture</div>
                <div class="metric-value" id="gpu-${index}-arch" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Compute Capability</div>
                <div class="metric-value" id="gpu-${index}-compute" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">GPU Clock</div>
                <div class="metric-value" id="gpu-${index}-gpu-clock" style="font-size: 1rem;">-</div>
            </div>
            
            <div class="metric">
                <div class="metric-label">Memory Clock</div>
                <div class="metric-value" id="gpu-${index}-mem-clock" style="font-size: 1rem;">-</div>
            </div>
        </div>
        
        <div class="metrics-row" style="margin-top: 1rem;">
            <div class="metric">
                <div class="metric-label">PCIe</div>
                <div class="metric-value" id="gpu-${index}-pcie" style="font-size: 1rem;">-</div>
            </div>
        </div>
        
        <div style="margin-top: 1.5rem;">
            <div class="metric-label">Throttling Status</div>
            <div id="gpu-${index}-throttle" style="font-size: 1rem; font-weight: 600; margin-top: 0.5rem;">-</div>
        </div>
        
        <div style="margin-top: 1.5rem;">
            <div class="metric-label">Top GPU Processes</div>
            <div id="gpu-${index}-processes" class="process-list" style="margin-top: 0.5rem;">
                <div style="color: var(--text-secondary);">Waiting for data...</div>
            </div>
        </div>
        
        <div style="margin-top: 1.5rem;">
            <div class="metric-label">GPU ${index} Utilization History (60s)</div>
            <div class="chart-container">
                <canvas id="gpu-${index}-util-chart"></canvas>
            </div>
        </div>
    `;
}

function initGPUChart(index) {
    const ctx = document.getElementById(`gpu-${index}-util-chart`);
    if (!ctx || charts[`gpu${index}Util`]) return;

    charts[`gpu${index}Util`] = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: `GPU ${index} Utilization`,
                data: [],
                borderColor: '#00d4ff',
                backgroundColor: 'rgba(0, 212, 255, 0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 100,
                    ticks: { color: getChartThemeColors().tickColor },
                    grid: { color: getChartThemeColors().gridColor }
                },
                x: {
                    ticks: { color: getChartThemeColors().tickColor, maxTicksLimit: 6 },
                    grid: { color: getChartThemeColors().gridColor }
                }
            }
        }
    });
}

function updateSingleGPU(gpu, index) {
    // Update utilization
    const utilEl = document.getElementById(`gpu-${index}-util`);
    if (utilEl) utilEl.textContent = `${gpu.gpu_util || 0}%`;
    updateProgressBar(`gpu-${index}-util-bar`, gpu.gpu_util || 0);

    // Update memory
    const memEl = document.getElementById(`gpu-${index}-memory`);
    if (memEl) memEl.textContent = `${gpu.memory_used_gb || 0} / ${gpu.memory_total_gb || 0} GB`;
    updateProgressBar(`gpu-${index}-memory-bar`, gpu.memory_util_pct || 0);

    // Update temperature
    const tempEl = document.getElementById(`gpu-${index}-temp`);
    if (tempEl) tempEl.textContent = gpu.temperature ? `${gpu.temperature}°C` : '-';
    updateProgressBar(`gpu-${index}-temp-bar`, gpu.temperature || 0, 100);

    // Update fan speed
    const fanEl = document.getElementById(`gpu-${index}-fan`);
    if (fanEl) fanEl.textContent = gpu.fan_speed_pct != null ? `${gpu.fan_speed_pct}%` : '-';
    updateProgressBar(`gpu-${index}-fan-bar`, gpu.fan_speed_pct || 0);

    // Update power
    const powerEl = document.getElementById(`gpu-${index}-power`);
    if (powerEl && gpu.power_draw_w && gpu.power_limit_w) {
        powerEl.textContent = `${gpu.power_draw_w}W / ${gpu.power_limit_w}W`;
        updateProgressBar(`gpu-${index}-power-bar`, gpu.power_pct || 0);
    }

    // Update architecture
    const archEl = document.getElementById(`gpu-${index}-arch`);
    if (archEl) archEl.textContent = gpu.architecture || '-';

    // Update GPU clocks
    const clockEl = document.getElementById(`gpu-${index}-clock`);
    if (clockEl && gpu.clock_graphics_mhz) {
        clockEl.textContent = `${gpu.clock_graphics_mhz} / ${gpu.max_clock_graphics_mhz || '-'} MHz`;
    }

    const memClockEl = document.getElementById(`gpu-${index}-mem-clock`);
    if (memClockEl && gpu.clock_mem_mhz) {
        memClockEl.textContent = `${gpu.clock_mem_mhz} / ${gpu.max_clock_mem_mhz || '-'} MHz`;
    }

    // Update PCIe with idle state tooltip
    const pcieEl = document.getElementById(`gpu-${index}-pcie`);
    if (pcieEl) {
        const pcieText = `Gen${gpu.pcie_gen || '?'} x${gpu.pcie_width || '?'}`;
        pcieEl.textContent = pcieText;

        // Color-code based on whether PCIe is degraded under load.
        // Gen1 at idle is normal (ASPM power saving), so only warn if
        // Gen < Max while the GPU is actively being used.
        if (gpu.pcie_gen && gpu.max_pcie_gen && gpu.pcie_gen < gpu.max_pcie_gen) {
            // Check if GPU is actually being used (>10% utilization)
            if (gpu.gpu_util > 10) {
                pcieEl.style.color = '#ffa500'; // Orange - degraded under load
                pcieEl.title = `Warning: Running at Gen${gpu.pcie_gen} but slot supports Gen${gpu.max_pcie_gen}`;
            } else {
                pcieEl.style.color = '#00ff88'; // Green - Gen1 at idle is normal
                pcieEl.title = `Gen${gpu.pcie_gen} at idle (power saving) - will boost to Gen${gpu.max_pcie_gen} under load`;
            }
        } else {
            pcieEl.style.color = '#00ff88'; // Green - at max
            pcieEl.title = `PCIe running at maximum supported speed`;
        }
    }

    // Update throttling
    const throttleEl = document.getElementById(`gpu-${index}-throttle`);
    if (throttleEl && gpu.throttle_reasons) {
        const throttling = Object.entries(gpu.throttle_reasons)
            .filter(([_, v]) => v && !['gpu_idle', 'applications_clocks_setting'].includes(_))
            .map(([k,]) => k);
        if (throttling.length > 0) {
            throttleEl.textContent = '⚠️ ' + throttling.join(', ');
            throttleEl.style.color = '#ff4444';
        } else {
            throttleEl.textContent = '✓ No Throttling';
            throttleEl.style.color = '#00ff88';
        }
    }

    // Deep Learning Metrics
    setTextContent(`gpu-${index}-cuda-cores`,
        gpu.cuda_cores > 0 ? gpu.cuda_cores.toLocaleString() : 'Unknown');
    setTextContent(`gpu-${index}-tensor-cores`,
        gpu.tensor_cores > 0 ? String(gpu.tensor_cores) : 'N/A');
    setTextContent(`gpu-${index}-tflops`,
        gpu.fp32_tflops > 0 ? `${gpu.fp32_tflops} TFLOPS` : 'Unknown');
    setTextContent(`gpu-${index}-bandwidth`,
        gpu.memory_bandwidth_gbps > 0 ? `${gpu.memory_bandwidth_gbps} GB/s` : 'Unknown');
    setTextContent(`gpu-${index}-compute`, gpu.compute_capability || 'Unknown');
    setTextContent(`gpu-${index}-gpu-clock`,
        gpu.clock_graphics_mhz ? `${gpu.clock_graphics_mhz} / ${gpu.max_clock_graphics_mhz || '-'} MHz` : '-');


    // Update processes  
    const procsEl = document.getElementById(`gpu-${index}-processes`);
    if (procsEl && gpu.top_processes) {
        if (gpu.top_processes.length === 0) {
            procsEl.innerHTML = '<div style="color: var(--text-secondary);">No active processes</div>';
        } else {
            procsEl.innerHTML = gpu.top_processes.map(proc => `
                <div class="process-item">
                    <strong>${proc.name || 'Unknown'}</strong> (PID: ${proc.pid || '-'})
                    <br>
                    <span style="color: var(--accent);">VRAM: ${(proc.memory_used_mb / 1024).toFixed(2)} GB</span>
                    ${proc.cmdline ? `<br><span style="color: var(--text-secondary); font-size: 0.85rem;">${proc.cmdline}</span>` : ''}
                </div>
            `).join('');
        }
    }
}

function updateCPUPanel(cpu) {
    setTextContent('cpu-name', cpu.brand);
    setTextContent('cpu-util', `${cpu.utilization_total}%`);
    setTextContent('cpu-freq', `${cpu.frequency_current_mhz} MHz`);
    setTextContent('cpu-temp', cpu.temperature ? `${cpu.temperature.toFixed(1)}°C` : 'N/A');
    setTextContent('cpu-load', `${cpu.load_1min}`);

    updateProgressBar('cpu-util-bar', cpu.utilization_total, 80);

    // Per-core utilization
    const perCoreEl = document.getElementById('cpu-per-core');
    if (perCoreEl && cpu.per_core_utils && Array.isArray(cpu.per_core_utils)) {
        perCoreEl.innerHTML = cpu.per_core_utils.map((util, index) => {
            // Defensive check: ensure util is a number
            const utilValue = typeof util === 'number' ? util : 0;

            // Color-code based on utilization
            let bgColor, textColor;
            if (utilValue >= 90) {
                // Critical: red
                bgColor = 'rgba(255, 68, 68, 0.3)';
                textColor = '#ff4444';
            } else if (utilValue >= 70) {
                // High: orange
                bgColor = 'rgba(255, 165, 0, 0.3)';
                textColor = '#ffa500';
            } else if (utilValue >= 40) {
                // Medium: yellow
                bgColor = 'rgba(255, 193, 7, 0.3)';
                textColor = '#ffc107';
            } else {
                // Low: green
                bgColor = 'rgba(0, 255, 136, 0.2)';
                textColor = '#00ff88';
            }

            return `
                <div style="
                    display: inline-block;
                    width: 65px;
                    padding: 0.4rem 0.6rem;
                    margin: 0.2rem;
                    background: ${bgColor};
                    border: 1px solid ${textColor};
                    border-radius: 6px;
                    text-align: center;
                    transition: all 0.3s ease;
                    box-sizing: border-box;
                ">
                    <div style="font-size: 0.7rem; color: var(--text-secondary); margin-bottom: 0.1rem;">Core ${index}</div>
                    <div style="font-weight: 600; color: ${textColor};">${utilValue.toFixed(0)}%</div>
                </div>
            `;
        }).join('');
    }

    // Features
    const features = cpu.features || {};
    const featuresText = Object.entries(features)
        .filter(([_, enabled]) => enabled)
        .map(([name, _]) => name.toUpperCase())
        .join(', ');
    setTextContent('cpu-features', featuresText || 'N/A');
}

function updateMemoryPanel(memory) {
    setTextContent('memory-used', `${memory.used_gb} / ${memory.total_gb} GB`);
    setTextContent('memory-available', `${memory.available_gb} GB`);
    setTextContent('memory-percent', `${memory.percent}%`);

    updateProgressBar('memory-bar', memory.percent, 85);

    // Swap warning (only alert if >0.1 GB = 100 MB)
    const swapEl = document.getElementById('memory-swap');
    if (swapEl) {
        if (memory.swap_used_gb > 0.1) {
            swapEl.innerHTML = `<span style="color: var(--critical);">⚠️ SWAP ACTIVE: ${memory.swap_used_gb} GB</span>`;
        } else if (memory.swap_used_gb > 0) {
            swapEl.innerHTML = `<span style="color: var(--text-secondary);">Swap: ${memory.swap_used_gb} GB (normal)</span>`;
        } else {
            swapEl.innerHTML = '<span style="color: var(--success);">✓ No Swap</span>';
        }
    }

    // Advanced diagnostics
    const speedEl = document.getElementById('memory-speed');
    if (speedEl) {
        if (memory.ram_speed_mhz) {
            speedEl.innerHTML = `<span style="color: var(--accent);">${memory.ram_speed_mhz} MHz</span>`;
        } else {
            speedEl.textContent = 'Unknown';
        }
    }

    const numaEl = document.getElementById('memory-numa');
    if (numaEl) {
        numaEl.innerHTML = `<span style="color: var(--accent);">${memory.numa_nodes || 1}</span>`;
    }

    const activeInactiveEl = document.getElementById('memory-active-inactive');
    if (activeInactiveEl && memory.active_gb !== undefined) {
        const activeGB = memory.active_gb.toFixed(2);
        const inactiveGB = (memory.inactive_gb || 0).toFixed(2);
        activeInactiveEl.innerHTML = `<span style="color: var(--success);">${activeGB} GB</span> / <span style="color: var(--text-secondary);">${inactiveGB} GB</span>`;
    }
}

function updateStoragePanel(storage) {
    // Main partition
    const partitions = storage.partitions || [];
    const mainPartition = partitions.find(p => p.mountpoint === '/') || partitions[0];

    if (mainPartition) {
        setTextContent('storage-used', `${mainPartition.used_gb} / ${mainPartition.total_gb} GB`);
        setTextContent('storage-free', `${mainPartition.free_gb} GB`);
        updateProgressBar('storage-bar', mainPartition.percent, 85);
    }

    // Disk I/O
    const diskIO = storage.disk_io || [];
    if (diskIO.length > 0) {
        const totalRead = diskIO.reduce((sum, d) => sum + d.read_mb_s, 0);
        const totalWrite = diskIO.reduce((sum, d) => sum + d.write_mb_s, 0);

        // Show in KB/s if values are very small (< 0.1 MB/s)
        if (totalRead < 0.1 && totalRead > 0) {
            setTextContent('storage-read', `${(totalRead * 1024).toFixed(1)} KB/s`);
        } else {
            setTextContent('storage-read', `${totalRead.toFixed(2)} MB/s`);
        }

        if (totalWrite < 0.1 && totalWrite > 0) {
            setTextContent('storage-write', `${(totalWrite * 1024).toFixed(1)} KB/s`);
        } else {
            setTextContent('storage-write', `${totalWrite.toFixed(2)} MB/s`);
        }
    } else {
        setTextContent('storage-read', '0.0 MB/s');
        setTextContent('storage-write', '0.0 MB/s');
    }

    // HuggingFace cache
    setTextContent('storage-hf-cache', `${storage.huggingface_cache_gb.toFixed(2)} GB`);
}

function updateNetworkPanel(network) {
    // Megabits/sec is already a sensible unit across the whole realistic
    // range (sub-1 to 1000+ Mbps), so unlike storage's KB/s-vs-MB/s switch,
    // one fixed unit is enough for the primary reading. The byte-based
    // figure underneath is a pure unit conversion of the same value (not a
    // separately-measured quantity) -- useful since file transfers/downloads
    // are usually reported in MB/s or KB/s, not Mbps.
    const downloadMbps = network.download_mbps || 0;
    const uploadMbps = network.upload_mbps || 0;
    setTextContent('network-download', `${downloadMbps.toFixed(2)} Mbps`);
    setTextContent('network-upload', `${uploadMbps.toFixed(2)} Mbps`);
    setTextContent('network-download-bytes', formatMbpsAsBytesPerSec(downloadMbps));
    setTextContent('network-upload-bytes', formatMbpsAsBytesPerSec(uploadMbps));
}

function formatMbpsAsBytesPerSec(mbps) {
    const bytesPerSec = (mbps * 1_000_000) / 8;
    const mbPerSec = bytesPerSec / (1024 * 1024);
    if (mbPerSec < 0.1 && mbPerSec > 0) {
        return `${(bytesPerSec / 1024).toFixed(1)} KB/s`;
    }
    return `${mbPerSec.toFixed(2)} MB/s`;
}

function updateMLPanel(ml_data) {
    // CUDA version
    const cudaEl = document.getElementById('ml-cuda');
    if (cudaEl) {
        cudaEl.textContent = ml_data.cuda_version || 'Not detected';
    }

    // Active ML Processes - Enhanced Display
    const processesEl = document.getElementById('ml-processes');
    if (processesEl && ml_data.active_processes) {
        if (ml_data.active_processes.length === 0) {
            processesEl.innerHTML = '<div style="color: var(--text-secondary);">No active ML processes</div>';
        } else {
            let html = '';
            ml_data.active_processes.forEach(proc => {
                const frameworks = proc.frameworks.join(', ');

                // Build metrics line with enhanced info
                let metricsLine = `PID: ${proc.pid} • Runtime: ${proc.runtime || 'N/A'}`;

                // Add GPU VRAM if available
                if (proc.gpu_vram_gb !== null && proc.gpu_vram_gb !== undefined) {
                    metricsLine += ` • <span style="color: var(--accent);">GPU VRAM: ${proc.gpu_vram_gb} GB</span>`;
                }

                // Add GPU utilization if available
                if (proc.gpu_util_pct !== null && proc.gpu_util_pct !== undefined) {
                    metricsLine += ` • <span style="color: var(--accent);">GPU: ${proc.gpu_util_pct}%</span>`;
                }

                // Add CPU and RAM
                metricsLine += ` • CPU: ${proc.cpu_percent}% • RAM: ${proc.memory_percent.toFixed(1)}%`;

                // Build HTML for this process
                html += `
                    <div style="margin-bottom: 1rem; padding: 0.75rem; background: var(--bg-card); border-radius: 6px; border-left: 3px solid var(--accent);">
                        <div style="font-weight: 600; color: var(--success); margin-bottom: 0.3rem;">${frameworks}</div>
                        <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.3rem;">${metricsLine}</div>
                        <div style="font-size: 0.8rem; color: var(--text-muted); font-family: 'Courier New', monospace;">${proc.cmdline}</div>
                `;

                // Add model info if detected
                if (proc.hf_model) {
                    html += `<div style="font-size: 0.8rem; color: var(--warning); margin-top: 0.3rem;">📦 Model: ${proc.hf_model}</div>`;
                }

                html += `</div>`;
            });
            processesEl.innerHTML = html;
        }
    }

    // Installed ML Packages (now categorized)
    const packagesEl = document.getElementById('ml-packages');
    if (packagesEl && ml_data.installed_packages) {
        const categories = ml_data.installed_packages;
        const categoryNames = Object.keys(categories);

        if (categoryNames.length === 0) {
            packagesEl.innerHTML = '<div style="color: var(--text-secondary);">No ML packages detected</div>';
        } else {
            let html = '';
            categoryNames.forEach(category => {
                const packages = categories[category];
                const packageCount = Object.keys(packages).length;

                if (packageCount > 0) {
                    html += `
                        <div style="margin-bottom: 1rem;">
                            <div style="font-weight: 600; color: var(--accent); margin-bottom: 0.5rem; font-size: 0.9rem;">
                                ${category} (${packageCount})
                            </div>
                            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.3rem; padding-left: 0.5rem;">
                    `;

                    Object.keys(packages).forEach(name => {
                        html += `<div style="font-size: 0.8rem; color: var(--text-secondary);">${name} <span style="color: var(--text-muted);">${packages[name]}</span></div>`;
                    });

                    html += `
                            </div>
                        </div>
                    `;
                }
            });
            packagesEl.innerHTML = html;
        }
    }
}

// Alert persistence - keep alerts until they resolve
let activeAlerts = new Map(); // key: alert.type, value: {alert, firstSeen, lastSeen}
let lastRenderedHTML = ''; // Track rendered content to avoid unnecessary DOM updates

function updateAlertsPanel(newAlerts) {
    const now = Date.now();
    const ALERT_TIMEOUT_MS = 30000; // Remove alert if not seen for 30 seconds

    // Update active alerts
    const newAlertTypes = new Set(newAlerts.map(a => a.type));

    // Add or update alerts
    newAlerts.forEach(alert => {
        if (activeAlerts.has(alert.type)) {
            // Update existing alert
            const existing = activeAlerts.get(alert.type);
            existing.alert = alert; // Update with latest description
            existing.lastSeen = now;
        } else {
            // New alert
            activeAlerts.set(alert.type, {
                alert: alert,
                firstSeen: now,
                lastSeen: now
            });
        }
    });

    // Remove alerts that haven't been seen recently (resolved conditions)
    for (const [type, data] of activeAlerts.entries()) {
        if (!newAlertTypes.has(type) && (now - data.lastSeen) > ALERT_TIMEOUT_MS) {
            activeAlerts.delete(type);
        }
    }

    // Generate HTML
    let newHTML;
    if (activeAlerts.size === 0) {
        newHTML = '<div style="color: var(--success); padding: 1rem;">✓ No active alerts - system running smoothly</div>';
    } else {
        // Convert map to array and sort by severity
        const severityOrder = { critical: 0, warning: 1, info: 2 };
        const sortedAlerts = Array.from(activeAlerts.values())
            .sort((a, b) => severityOrder[a.alert.severity] - severityOrder[b.alert.severity]);

        newHTML = sortedAlerts.map(({ alert }) => `
            <div class="alert ${alert.severity}">
                <div class="alert-title">${alert.title}</div>
                <div class="alert-description">${alert.description}</div>
            </div>
        `).join('');
    }

    // CRITICAL: Only update DOM if content actually changed
    if (newHTML !== lastRenderedHTML) {
        const alertsEl = document.getElementById('alerts-list');
        if (alertsEl) {
            alertsEl.innerHTML = newHTML;
            lastRenderedHTML = newHTML;
        }
    }
}

function updateProcessList(elementId, processes) {
    const el = document.getElementById(elementId);
    if (!el) return;

    if (processes.length === 0) {
        el.innerHTML = '<div style="color: var(--text-secondary);">No active processes</div>';
        return;
    }

    el.innerHTML = processes.map(proc => `
        <div class="process-item">
            <div class="process-name">${proc.name}</div>
            <div class="process-detail">PID ${proc.pid} • VRAM: ${proc.memory_used_mb.toFixed(0)} MB</div>
            <div class="process-detail" style="font-size: 0.7rem;">${proc.cmdline}</div>
        </div >
                `).join('');
}

function updateProgressBar(elementId, value, warningThreshold = 80, maxValue = 100) {
    const bar = document.getElementById(elementId);
    if (!bar) return;

    const fill = bar.querySelector('.progress-fill');
    if (!fill) return;

    // Calculate percentage (value is already a percentage for most metrics)
    const percentage = Math.min(100, Math.max(0, value));

    fill.style.width = `${percentage}%`;

    // Color based on threshold
    if (percentage >= warningThreshold) {
        fill.style.background = 'linear-gradient(90deg, #ff4444, #ff6666)';
    } else if (percentage >= warningThreshold * 0.7) {
        fill.style.background = 'linear-gradient(90deg, #ffa500, #ffb732)';
    } else {
        fill.style.background = 'linear-gradient(90deg, #00ff88, #00d4ff)';
    }
}

// Copy-to-clipboard for metrics
function initCopyToClipboard() {
    // Add click listeners to all metric values
    const metricSelectors = [
        '#gpu-0-util', '#gpu-0-memory', '#gpu-0-temp', '#gpu-0-power',
        '#gpu-0-clock', '#gpu-0-mem-clock', '#gpu-0-pcie',
        '#cpu-util', '#cpu-freq', '#cpu-temp', '#cpu-load',
        '#memory-usage', '#memory-available', '#memory-active-inactive',
        '#storage-used', '#storage-read', '#storage-write', '#hf-cache-size'
    ];

    metricSelectors.forEach(selector => {
        const el = document.querySelector(selector);
        if (el) {
            el.style.cursor = 'pointer';
            el.title = 'Click to copy';

            el.addEventListener('click', async (e) => {
                const text = e.target.textContent.trim();

                try {
                    await navigator.clipboard.writeText(text);
                    showToast(`Copied: ${text}`);
                } catch (err) {
                    console.error('Failed to copy:', err);
                    showToast('Failed to copy', 'error');
                }
            });
        }
    });
}

function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed;
        bottom: 2rem;
        right: 2rem;
        padding: 1rem 1.5rem;
        background: ${type === 'success' ? 'var(--success)' : 'var(--critical)'};
        color: #ffffff;
        border-radius: 8px;
        font-weight: 600;
        z-index: 10000;
        animation: slideIn 0.3s ease;
        box-shadow: var(--shadow);
    `;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 2000);
}

// Add CSS animation
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
    @keyframes slideOut {
        from { transform: translateX(0); opacity: 1; }
        to { transform: translateX(100%); opacity: 0; }
    }
`;
document.head.appendChild(style);

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    initThemeSystem();
    initCPUChart();
    connectWebSocket();

    setTimeout(initCopyToClipboard, 1000); // Wait for metrics to populate
    loadLightingState();
    loadFanProfileState();
    initExportHistoryDefaults();
});

function setTextContent(elementId, text) {
    const el = document.getElementById(elementId);
    if (el) el.textContent = text;
}

// Utility formatters
function formatBytes(bytes) {
    const gb = bytes / (1024 ** 3);
    return `${gb.toFixed(2)} GB`;
}

function formatPercent(value) {
    return `${value.toFixed(1)} % `;
}

function formatTemp(temp) {
    return `${temp}°C`;
}

// Update fans panel
function updateFansPanel(gpu_data, fans_data) {
    // Update GPU fan
    const gpuFanEl = document.getElementById('fan-gpu');
    const gpuFan = gpu_data && gpu_data.length > 0 ? gpu_data[0].fan_speed_pct : null;

    if (gpuFanEl) {
        if (gpuFan !== null && gpuFan !== undefined) {
            gpuFanEl.textContent = `${gpuFan}%`;
            updateProgressBar('fan-gpu-bar', gpuFan);
        } else {
            gpuFanEl.textContent = '-';
        }
    }

    // Update system fans  
    const systemFansContainer = document.getElementById('system-fans-container');
    if (!systemFansContainer || !fans_data || !fans_data.available) {
        return;
    }

    const fans = fans_data.fans || [];

    // Only rebuild HTML if fan count changed
    const currentFanCount = systemFansContainer.querySelectorAll('.metric').length;
    if (currentFanCount !== fans.length) {
        let html = '';
        fans.forEach(fan => {
            const fanId = `system-fan-${fan.index}`;

            // Display name with emoji icons
            let displayName = fan.label || `System Fan ${fan.index}`;
            let icon = '';

            if (fan.type === 'aio_pump') {
                icon = '🌊';
            } else if (fan.type === 'case_front') {
                icon = '⬅️';
            } else if (fan.type === 'case_rear') {
                icon = '➡️';
            } else if (fan.type === 'aio_fan') {
                icon = '❄️';
            } else {
                icon = '🌀';
            }

            displayName = `${icon} ${displayName}`;

            html += `
                <div class="metric">
                    <div class="metric-label">${displayName}</div>
                    <div class="metric-value" id="${fanId}">-</div>
                    <div class="progress-bar" id="${fanId}-bar">
                        <div class="progress-fill"></div>
                    </div>
                </div>
            `;
        });

        systemFansContainer.innerHTML = html;
    }

    // Update fan values and progress bars
    fans.forEach(fan => {
        const fanId = `system-fan-${fan.index}`;
        const fanEl = document.getElementById(fanId);
        const rpmPct = fan.rpm_pct || 0;  // Use RPM percentage
        const pwmPct = fan.pwm_pct || 0;

        if (fanEl) {
            // Display as percentage for cleaner look
            fanEl.textContent = `${rpmPct}%`;
        }

        // Use RPM percentage for progress bar (more meaningful than PWM)
        updateProgressBar(`${fanId}-bar`, rpmPct);
    });
}

// Export current metrics as JSON
async function exportMetrics() {
    try {
        const response = await fetch('/api/export');

        if (!response.ok) {
            const error = await response.json();
            showToast(error.error || 'Export failed', 'error');
            return;
        }

        const data = await response.json();

        // Create downloadable file
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dashboard_metrics_${Math.floor(Date.now() / 1000)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast('\u2713 Metrics exported successfully');
    } catch (error) {
        console.error('Export failed:', error);
        showToast('Export failed: ' + error.message, 'error');
    }
}

// Ranged history export -- a time window + a component checklist, unlike
// exportMetrics() above which is always just the single latest sample.
function initExportHistoryDefaults() {
    const now = new Date();
    const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);
    const startInput = document.getElementById('export-history-start');
    const endInput = document.getElementById('export-history-end');
    if (startInput) startInput.value = toDatetimeLocalValue(oneHourAgo);
    if (endInput) endInput.value = toDatetimeLocalValue(now);
}

function toDatetimeLocalValue(date) {
    // <input type="datetime-local"> wants "YYYY-MM-DDTHH:MM" in local time
    // (no timezone conversion/suffix).
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function exportHistoryRange() {
    const startInput = document.getElementById('export-history-start');
    const endInput = document.getElementById('export-history-end');
    const components = Array.from(document.querySelectorAll('#export-history-components input[type="checkbox"]:checked'))
        .map(cb => cb.value);

    if (components.length === 0) {
        showToast('Select at least one component to export', 'error');
        return;
    }

    const params = new URLSearchParams();
    if (startInput && startInput.value) {
        params.set('start', Math.floor(new Date(startInput.value).getTime() / 1000));
    }
    if (endInput && endInput.value) {
        params.set('end', Math.floor(new Date(endInput.value).getTime() / 1000));
    }
    params.set('components', components.join(','));

    try {
        const response = await fetch(`/api/export/history?${params.toString()}`);
        const data = await response.json();
        if (!response.ok) {
            showToast(data.error || 'History export failed', 'error');
            return;
        }

        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dashboard_history_${Math.floor(Date.now() / 1000)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showToast(`\u2713 Exported ${data.count} record${data.count === 1 ? '' : 's'}`);
    } catch (error) {
        console.error('History export failed:', error);
        showToast('History export failed: ' + error.message, 'error');
    }
}

// RGB lighting control (motherboard + GPU, via OpenRGB) -- a control
// surface, not a metric, so it's fetched once on load / on user action
// rather than riding the every-second /ws stream.
let lightingAvailable = false;
// name (lowercase) -> has_speed, from /api/lighting/modes -- lets the speed
// slider disable itself for modes with nothing to animate (e.g. Static),
// instead of hardcoding that list of mode names on the client side.
let lightingModeSpeedSupport = {};

function applyLightingState(state) {
    lightingAvailable = state.available;
    const btn = document.getElementById('lighting-power-btn');
    const modeSelect = document.getElementById('lighting-mode');
    const colorInput = document.getElementById('lighting-color');
    const brightnessInput = document.getElementById('lighting-brightness');
    const speedInput = document.getElementById('lighting-speed');

    if (!state.available) {
        setTextContent('lighting-status', 'OpenRGB not available');
        if (btn) { btn.textContent = 'Unavailable'; btn.disabled = true; }
        if (modeSelect) modeSelect.disabled = true;
        if (colorInput) colorInput.disabled = true;
        if (brightnessInput) brightnessInput.disabled = true;
        if (speedInput) speedInput.disabled = true;
        return;
    }

    if (btn) {
        btn.disabled = false;
        btn.textContent = state.power === 'on' ? '\ud83d\udca1 Turn Off' : '\ud83d\udd0c Turn On';
    }
    if (modeSelect) {
        modeSelect.disabled = false;
        if (state.mode) modeSelect.value = state.mode;
    }
    if (colorInput) {
        colorInput.disabled = false;
        if (state.color) colorInput.value = state.color;
    }
    if (brightnessInput) {
        brightnessInput.disabled = false;
        if (state.brightness !== undefined) {
            brightnessInput.value = state.brightness;
            setTextContent('lighting-brightness-value', `${state.brightness}%`);
        }
    }
    if (speedInput) {
        speedInput.disabled = !lightingModeSpeedSupport[state.mode];
        if (state.speed !== undefined) {
            speedInput.value = state.speed;
            setTextContent('lighting-speed-value', `${state.speed}%`);
        }
    }
    const modeLabel = state.mode ? state.mode.charAt(0).toUpperCase() + state.mode.slice(1) : '';
    setTextContent('lighting-status', state.power === 'on'
        ? `On \u2014 ${modeLabel}, ${state.brightness}%`
        : 'Off');
}

async function loadLightingModes() {
    try {
        const response = await fetch('/api/lighting/modes');
        const data = await response.json();
        const select = document.getElementById('lighting-mode');
        if (select && response.ok && Array.isArray(data.modes) && data.modes.length > 0) {
            lightingModeSpeedSupport = {};
            data.modes.forEach(m => { lightingModeSpeedSupport[m.name.toLowerCase()] = m.has_speed; });
            select.innerHTML = data.modes
                .map(m => `<option value="${m.name.toLowerCase()}">${m.name}</option>`)
                .join('');
        }
    } catch (error) {
        console.error('Failed to load lighting modes:', error);
    }
}

async function loadLightingState() {
    await loadLightingModes();
    try {
        const response = await fetch('/api/lighting');
        const data = await response.json();
        if (!response.ok) {
            console.error('Failed to load lighting state:', data.error);
            applyLightingState({ available: false, power: 'off', mode: 'direct', color: '#000000', brightness: 100, speed: 50 });
            return;
        }
        applyLightingState(data);
    } catch (error) {
        console.error('Failed to load lighting state:', error);
        applyLightingState({ available: false, power: 'off', mode: 'direct', color: '#000000', brightness: 100, speed: 50 });
    }
}

async function postLighting(body) {
    try {
        const response = await fetch('/api/lighting', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) {
            showToast(data.error || 'Lighting update failed', 'error');
            return;
        }
        applyLightingState(data);
    } catch (error) {
        console.error('Lighting update failed:', error);
        showToast('Lighting update failed: ' + error.message, 'error');
    }
}

// Reads the current mode/color/brightness/speed controls so any single
// change (toggling power, picking a color, sliding brightness or speed)
// reapplies all of them together -- keeps the controls consistent with
// each other instead of one silently overwriting what the others were doing.
function currentLightingControls() {
    const modeSelect = document.getElementById('lighting-mode');
    const colorInput = document.getElementById('lighting-color');
    const brightnessInput = document.getElementById('lighting-brightness');
    const speedInput = document.getElementById('lighting-speed');
    return {
        mode: modeSelect ? modeSelect.value : 'direct',
        color: colorInput ? colorInput.value : '#ffffff',
        brightness: brightnessInput ? parseInt(brightnessInput.value, 10) : 100,
        speed: speedInput ? parseInt(speedInput.value, 10) : 50,
    };
}

function toggleLighting() {
    if (!lightingAvailable) return;
    const btn = document.getElementById('lighting-power-btn');
    const turningOn = btn && btn.textContent.includes('Turn On');
    if (turningOn) {
        postLighting({ power: 'on', ...currentLightingControls() });
    } else {
        postLighting({ power: 'off' });
    }
}

function setLightingMode(mode) {
    if (!lightingAvailable) return;
    postLighting({ power: 'on', ...currentLightingControls(), mode });
}

function setLightingColor(hex) {
    if (!lightingAvailable) return;
    postLighting({ power: 'on', ...currentLightingControls(), color: hex });
}

function setLightingBrightness(value) {
    if (!lightingAvailable) return;
    postLighting({ power: 'on', ...currentLightingControls(), brightness: parseInt(value, 10) });
}

function setLightingSpeed(value) {
    if (!lightingAvailable) return;
    postLighting({ power: 'on', ...currentLightingControls(), speed: parseInt(value, 10) });
}

// Fan profile control (Quiet/Performance toggle, via CoolerControl) -- same
// interaction shape as the lighting power toggle above: fetch state on
// load, POST on click, re-render from whatever the server actually applied.
let fanProfileAvailable = false;

function applyFanProfileState(state) {
    fanProfileAvailable = state.available;
    const quietBtn = document.getElementById('fan-profile-btn-quiet');
    const perfBtn = document.getElementById('fan-profile-btn-performance');

    if (!state.available) {
        setTextContent('fan-profile-status', 'CoolerControl not available');
        quietBtn.disabled = true;
        perfBtn.disabled = true;
        quietBtn.classList.remove('active');
        perfBtn.classList.remove('active');
        return;
    }

    quietBtn.disabled = false;
    perfBtn.disabled = false;
    quietBtn.classList.toggle('active', state.mode === 'quiet');
    perfBtn.classList.toggle('active', state.mode === 'performance');

    if (state.mode === 'mixed') {
        setTextContent('fan-profile-status', 'Mixed (channels don\'t match either profile)');
    } else {
        setTextContent('fan-profile-status', state.mode === 'quiet' ? 'Quiet' : 'Performance');
    }
}

async function loadFanProfileState() {
    try {
        const response = await fetch('/api/fans/profile');
        const data = await response.json();
        if (data.error) {
            console.error('Failed to load fan profile state:', data.error);
            applyFanProfileState({ available: false, mode: 'unknown' });
            return;
        }
        applyFanProfileState(data);
    } catch (error) {
        console.error('Failed to load fan profile state:', error);
        applyFanProfileState({ available: false, mode: 'unknown' });
    }
}

async function setFanProfile(mode) {
    if (!fanProfileAvailable) return;
    try {
        const response = await fetch('/api/fans/profile', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode }),
        });
        const data = await response.json();
        if (data.error) {
            showToast(data.error || 'Fan profile update failed', 'error');
            return;
        }
        applyFanProfileState(data);
    } catch (error) {
        console.error('Fan profile update failed:', error);
        showToast('Fan profile update failed: ' + error.message, 'error');
    }
}

// Theme System Management
let currentThemeMode = 'dark';

function getResolvedTheme(mode) {
    if (mode === 'auto') {
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    return mode;
}

function getChartThemeColors() {
    const resolvedTheme = getResolvedTheme(currentThemeMode);
    return {
        tickColor: resolvedTheme === 'light' ? '#475569' : '#a0a0a0',
        gridColor: resolvedTheme === 'light' ? 'rgba(15, 23, 42, 0.08)' : 'rgba(255, 255, 255, 0.1)'
    };
}

function initThemeSystem() {
    const savedMode = localStorage.getItem('dashboard_theme_preference') || 'dark';
    setTheme(savedMode, false);

    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
        if (currentThemeMode === 'auto') {
            applyResolvedTheme('auto');
        }
    });
}

function setTheme(mode, showNotification = true) {
    currentThemeMode = mode;
    localStorage.setItem('dashboard_theme_preference', mode);
    applyResolvedTheme(mode);

    // Update switcher button active state
    ['dark', 'light', 'auto'].forEach(btnMode => {
        const btn = document.getElementById(`theme-btn-${btnMode}`);
        if (btn) {
            if (btnMode === mode) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        }
    });

    if (showNotification) {
        showToast(`Theme switched to ${mode.toUpperCase()} mode`, 'success');
    }
}

function applyResolvedTheme(mode) {
    const resolvedTheme = getResolvedTheme(mode);
    document.documentElement.setAttribute('data-theme', resolvedTheme);

    // Update Chart.js themes
    updateAllChartsTheme(resolvedTheme);
}

function updateAllChartsTheme(resolvedTheme) {
    const { tickColor, gridColor } = getChartThemeColors();

    Object.values(charts).forEach(chart => {
        if (chart && chart.options && chart.options.scales) {
            if (chart.options.scales.x) {
                if (chart.options.scales.x.ticks) chart.options.scales.x.ticks.color = tickColor;
                if (chart.options.scales.x.grid) chart.options.scales.x.grid.color = gridColor;
            }
            if (chart.options.scales.y) {
                if (chart.options.scales.y.ticks) chart.options.scales.y.ticks.color = tickColor;
                if (chart.options.scales.y.grid) chart.options.scales.y.grid.color = gridColor;
            }
            chart.update('none');
        }
    });
}

