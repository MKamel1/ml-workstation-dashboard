#!/bin/bash
# Fan Identification Helper
# Run this script and physically check which fan changes speed

echo "==================================================================="
echo "    FAN IDENTIFICATION HELPER"
echo "==================================================================="
echo ""
echo "This will help you identify which motherboard header controls"
echo "which physical fan in your system."
echo ""
echo "Current detected fans from motherboard (nct6799):"
echo ""

for i in 1 2 3 4 5; do
    rpm=$(cat /sys/class/hwmon/hwmon5/fan${i}_input 2>/dev/null)
    if [ "$rpm" != "0" ] && [ -n "$rpm" ]; then
        pwm=$(cat /sys/class/hwmon/hwmon5/pwm${i} 2>/dev/null)
        pwm_pct=$((pwm * 100 / 255))
        echo "  Fan $i: ${rpm} RPM (PWM: ${pwm_pct}%)"
    fi
done

echo ""
echo "==================================================================="
echo "To identify each fan, check your BIOS fan configuration:"
echo ""
echo "1. Reboot and enter BIOS (usually DEL or F2 key)"
echo "2. Go to H/W Monitor or Fan Configuration"
echo "3. Note which headers show active fans"
echo ""
echo "Common ASRock X870E Taichi headers:"
echo "  - CPU_FAN1 (often AIO pump)"
echo "  - CPU_FAN2 / CPU_OPT (often AIO radiator fans)"
echo "  - CHA_FAN1 / SYS_FAN1 (case fans)"
echo "  - CHA_FAN2 / SYS_FAN2 (case fans)"  
echo "  - CHA_FAN3 / SYS_FAN3 (case fans)"
echo "  - AIO_PUMP (dedicated pump header)"
echo ""
echo "==================================================================="
echo ""
echo "Once you know which BIOS header = which Fan number,"
echo "edit: /home/omar/ai-projects/workstation-dashboard/fan_config.py"
echo ""
echo "Example:"
echo '  1: ("CPU AIO Radiator", "aio_fan", "Top radiator fan"),'
echo '  2: ("Case Front Intake", "case_front", "Front 140mm fan"),'
echo '  4: ("AIO Pump", "aio_pump", "Thermalright pump"),'
echo ""
