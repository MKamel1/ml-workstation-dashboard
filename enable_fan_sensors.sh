#!/bin/bash
# Enable Fan Monitoring on ASRock Taichi X870E
# This enables the NCT6775 kernel module for motherboard fan sensors

echo "🔧 Enabling ASRock Taichi X870E Fan Sensors"
echo "==========================================="
echo ""

# 1. Load the NCT6775 kernel module
echo "Step 1: Loading nct6775 kernel module..."
sudo modprobe nct6775 2>&1

if lsmod | grep -q nct6775; then
    echo "✓ nct6775 module loaded successfully"
else
    echo "✗ Failed to load nct6775 module"
    echo "  Trying alternative modules..."
    sudo modprobe nct6683 2>&1
    sudo modprobe it87 2>&1
fi
echo ""

# 2. Run sensors-detect to configure
echo "Step 2: Running sensors-detect (auto mode)..."
echo "This will detect and configure all available sensors"
echo ""
sudo sensors-detect --auto
echo ""

# 3. Verify fans are now visible
echo "Step 3: Verifying fan detection..."
echo ""
sensors -u | grep -E "(Adapter|fan.*_input|pwm.*)" || echo "⚠ No fans detected yet"
echo ""

# 4. Check hwmon for new devices
echo "Step 4: Checking hwmon devices..."
for hwmon in /sys/class/hwmon/hwmon*/; do
    name=$(cat "$hwmon/name" 2>/dev/null)
    fan_count=$(ls "$hwmon"fan*_input 2>/dev/null | wc -l)
    if [ "$fan_count" -gt 0 ]; then
        echo "  ✓ $name: $fan_count fan(s)"
    fi
done
echo ""

# 5. Make module load permanent
echo "Step 5: Making nct6775 load on boot..."
if ! grep -q "nct6775" /etc/modules 2>/dev/null; then
    echo "nct6775" | sudo tee -a /etc/modules
    echo "✓ Added nct6775 to /etc/modules"
else
    echo "✓ nct6775 already in /etc/modules"
fi
echo ""

echo "==========================================="
echo "✅ Setup complete!"
echo ""
echo "Rerun detection script to see results:"
echo "  ./detect_fans.sh"
echo ""
echo "Or restart dashboard to enable monitoring:"
echo "  ./dashboard.sh restart"
