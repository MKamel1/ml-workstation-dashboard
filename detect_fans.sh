#!/bin/bash
# Comprehensive Fan Detection Script for ML Dashboard
# Detects all available fan sensors from multiple sources

echo "=== ML Dashboard Fan Detection ===="
echo ""

# 1. GPU Fans via NVML (already working in dashboard)
echo "🎮 GPU Fans (NVML):"
nvidia-smi --query-gpu=name,fan.speed --format=csv,noheader 2>/dev/null | while IFS=',' read -r name speed; do
    echo "  ✓ $name: $speed"
done || echo "  ✗ No NVIDIA GPUs detected"
echo ""

# 2. Motherboard/System Fans via sensors (lm-sensors)
echo "🌀 System Fans (lm-sensors):"
if command -v sensors &> /dev/null; then
    fan_count=$(sensors -u 2>/dev/null | grep -c "fan.*_input")
    if [ "$fan_count" -gt 0 ]; then
        sensors -u 2>/dev/null | grep -E "(Adapter|fan.*_input|pwm.*_input)" | while read line; do
            echo "  $line"
        done
    else
        echo "  ⚠ No fan sensors detected via sensors"
        echo "  → Try: sudo modprobe nct6775"
        echo "  → Or: sudo sensors-detect --auto"
    fi
else
    echo "  ✗ lm-sensors not installed"
fi
echo ""

# 3. Direct hwmon inspection
echo "💾 hwmon Devices:"
for hwmon in /sys/class/hwmon/hwmon*/; do
    name=$(cat "$hwmon/name" 2>/dev/null || echo "unknown")
    echo "  Device: $name"
    
    # Check for fan inputs
    fan_files=$(ls "$hwmon"fan*_input 2>/dev/null)
    if [ -n "$fan_files" ]; then
        for fan_file in $fan_files; do
            fan_value=$(cat "$fan_file" 2>/dev/null || echo "0")
            fan_name=$(basename "$fan_file" | sed 's/_input//')
            echo "    ✓ $fan_name: ${fan_value} RPM"
        done
    fi
    
    # Check for PWM controls
    pwm_files=$(ls "$hwmon"pwm* 2>/dev/null | grep -v "enable\|mode")
    if [ -n "$pwm_files" ]; then
        for pwm_file in $pwm_files; do
            if [[ "$pwm_file" == *"_input" ]] || [[ "$pwm_file" =~ pwm[0-9]+$ ]]; then
                pwm_value=$(cat "$pwm_file" 2>/dev/null || echo "0")
                pwm_name=$(basename "$pwm_file")
                pwm_pct=$((pwm_value * 100 / 255))
                echo "    ◆ $pwm_name: ${pwm_value}/255 (${pwm_pct}%)"
            fi
        done
    fi
done
echo ""

# 4. ASRock Motherboard Detection
echo "🖥️ ASRock Motherboard:"
asrock_usb=$(lsusb | grep -i "ASRock\|26ce:01a2")
if [ -n "$asrock_usb" ]; then
    echo "  ✓ ASRock LED Controller detected:"
    echo "    $asrock_usb"
    echo "  → Fan control may be via OpenRGB or ASRock Polychrome"
else
    echo "  ✗ ASRock USB controller not detected"
fi
echo ""

# 5. OpenRGB Detection
echo "🌈 OpenRGB:"
if command -v openrgb &>/dev/null; then
    echo "  ✓ OpenRGB installed at: $(which openrgb)"
    echo "  Detecting devices..."
    openrgb --list-devices 2>&1 | head -20
else
    echo "  ✗ OpenRGB not found"
fi
echo ""

# 6. Liquidctl (AIO coolers)
echo "❄️ Liquidctl (AIO Coolers):"
if command -v liquidctl &>/dev/null; then
    echo "  ✓ liquidctl installed"
    liquidctl_devices=$(liquidctl list 2>&1)
    if echo "$liquidctl_devices" | grep -q "no device"; then
        echo "  ⚠ No AIO devices detected via liquidctl"
        echo "  → Thermalright AIOs may not be supported by liquidctl"
    else
        echo "$liquidctl_devices"
    fi
else
    echo "  ✗ liquidctl not installed"
fi
echo ""

# 7. Check for loaded kernel modules
echo "🔧 Loaded Sensor Modules:"
sensor_modules=$(lsmod | grep -E "nct6775|it87|coretemp|k10temp")
if [ -n "$sensor_modules" ]; then
    echo "$sensor_modules" | awk '{print "  ✓ " $1}'
else
    echo "  ⚠ No fan controller modules loaded"
    echo "  → Try: sudo modprobe nct6775  # For ASRock boards"
fi
echo ""

# 8. Summary and Recommendations
echo "📋 Summary:"
echo "============================================"

# Count detected fans
gpu_fans=$(nvidia-smi --query-gpu=fan.speed --format=csv,noheader 2>/dev/null | wc -l)
hwmon_fans=$(find /sys/class/hwmon -name "fan*_input" 2>/dev/null | wc -l)

echo "Detected Fans:"
echo "  • GPU Fans: $gpu_fans"
echo "  • System Fans (hwmon): $hwmon_fans"
echo ""

if [ "$hwmon_fans" -eq 0 ]; then
    echo "⚠️ No system fans detected. Recommendations:"
    echo ""
    echo "1. Load motherboard sensor module:"
    echo "   sudo modprobe nct6775"
    echo "   sudo sensors-detect --auto"
    echo ""
    echo "2. Check OpenRGB for ASRock Polychrome control:"
    echo "   openrgb --list-devices"
    echo ""
    echo "3. Add yourself to required groups:"
    echo "   sudo usermod -a -G i2c,gpio \$USER"
    echo "   # Then logout and login"
    echo ""
fi

echo "============================================"
