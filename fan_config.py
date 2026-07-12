# Fan Configuration for ASRock Taichi X870E + Thermalright AIO + Lancool 216 Case
#
# FINAL VERIFIED CONFIGURATION (Post-GRUB acpi_enforce_resources=lax)
#
# BIOS Fan Headers → nct6799 Sensor Mapping:
# - CPU_FAN1 (787 RPM in BIOS) → Fan 2 → AIO Radiator Fans (top mounted)
# - CHA_FAN1 (680 RPM in BIOS) → Fan 1 → Front Intake 2x 160mm fans
# - AIO_PUMP (3082 RPM in BIOS) → Fan 4 → Thermalright AIO Pump
# - MOS_FAN1 (N/A in BIOS) → NOT DETECTED → No RPM sensor (PWM control only)
#
# nct6799 Sensor Status:
# - Fan 1 → CHA_FAN1 → Front Intake (detectable)
# - Fan 2 → CPU_FAN1 → AIO Radiator Fans (detectable)
# - Fan 3 → Not connected (0 RPM)
# - Fan 4 → AIO_PUMP → AIO Pump (detectable)
# - Fan 5 → Not connected (0 RPM)

FAN_CONFIG = {
    # Fan header number: (Display Name, Type, Description)
    1: ("Front Intake 160mm", "case_front", "CHA_FAN1 - Lancool 216 front 2x 160mm intake fans"),
    2: ("AIO Radiator Fans", "aio_fan", "CPU_FAN1 - Thermalright AIO radiator fans (top mounted)"),
    3: (None, None, "Not connected"),
    4: ("AIO Pump", "aio_pump", "AIO_PUMP - Thermalright AIO water pump"),
    5: (None, None, "Not connected"),
    
    # MOS_FAN1 (VRM Cooling Fan) Investigation Results:
    # - BIOS shows "N/A" for RPM even when fan is audibly spinning at full speed
    # - No RPM changes in any fan sensor (fan1-5) when MOS_FAN1 speed is changed in BIOS
    # - Added GRUB parameter "acpi_enforce_resources=lax" - no new sensors exposed
    # - Conclusion: MOS_FAN1 has NO tachometer (RPM sensor), only PWM control
    # - Cannot be monitored via Linux lm-sensors (BIOS control only)
    #
    # Rear 120mm Exhaust Fan:
    # - Not detected in any sensor channel
    # - Likely connected directly to PSU (not motherboard controlled)
}

# Fan display settings
FAN_DISPLAY_MODE = "percentage"  # "percentage" or "rpm"
FAN_ALERT_THRESHOLD_LOW = 10  # Alert if fan drops below 10%
FAN_ALERT_THRESHOLD_PUMP = 2500  # Alert if pump drops below 2500 RPM
