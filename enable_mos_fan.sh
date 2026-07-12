#!/bin/bash
# Enable MOS Fan Detection via ACPI Override
#
# ASRock X870E Taichi boards may hide some sensors (like MOS_FAN1) 
# behind ACPI resource protection. This script adds the kernel parameter
# to allow lm-sensors to access these protected resources.

echo "=== Enabling MOS Fan Sensor Detection ==="
echo ""

# Check current kernel version
KERNEL_VERSION=$(uname -r)
echo "Current kernel: $KERNEL_VERSION"

# Check if already configured
if grep -q "acpi_enforce_resources=lax" /etc/default/grub; then
    echo "✓ acpi_enforce_resources=lax already configured"
    exit 0
fi

echo ""
echo "Adding acpi_enforce_resources=lax to GRUB configuration..."
echo "This allows OS access to ACPI-protected sensors like MOS_FAN1"
echo ""

# Backup grub config
sudo cp /etc/default/grub /etc/default/grub.backup.$(date +%Y%m%d)
echo "✓ Backed up /etc/default/grub"

# Add the parameter
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 acpi_enforce_resources=lax"/' /etc/default/grub

# Update grub
sudo update-grub

echo ""
echo "✅ Configuration updated!"
echo ""
echo "IMPORTANT: You must REBOOT for this to take effect"
echo ""
echo "After reboot, run these commands to verify:"
echo "  1. sensors | grep -i fan"
echo "  2. ./detect_fans.sh"
echo ""
echo "The MOS_FAN1 sensor should now appear as one of the fan channels."
