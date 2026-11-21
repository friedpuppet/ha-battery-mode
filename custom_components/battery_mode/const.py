"""Constants for Battery Mode."""

from typing import Final

DOMAIN: Final = "battery_mode"

# Config entry data
CONF_DEVICE: Final = "device_id"  # the ha-solarman device of the Deye inverter
CONF_SOC_ENTITY: Final = "soc_entity"  # battery charge, %
CONF_CURRENT_ENTITY: Final = "current_entity"  # battery current, A, negative while charging (Deye's sign)
CONF_POWER_ENTITY: Final = "power_entity"  # battery power, W, negative while charging
CONF_GRID_ENTITY: Final = "grid_entity"  # binary_sensor, on while the flat runs from the grid

# Options
CONF_RESERVE: Final = "reserve"  # %, kept in the day in the night mode
CONF_NIGHT_START: Final = "night_start"  # "HH:MM"
CONF_NIGHT_END: Final = "night_end"
CONF_NORMAL_CURRENT: Final = "normal_current"  # A, grid charging in the Максимум mode
CONF_FAST_CURRENT: Final = "fast_current"  # A, night / charge now / training charge
CONF_PEAK_LIMIT: Final = "peak_limit"  # W, Deye grid peak shaving
CONF_FLOOR: Final = "floor"  # %, training: discharge down to this
CONF_FULL_SOC: Final = "full_soc"  # %, "full" = at least this ...
CONF_FULL_CURRENT: Final = "full_current"  # A, ... with the charge current at most this ...
CONF_FULL_MINUTES: Final = "full_minutes"  # ... for this long

DEFAULTS: Final = {
    CONF_RESERVE: 30,
    CONF_NIGHT_START: "23:00",
    CONF_NIGHT_END: "07:00",
    CONF_NORMAL_CURRENT: 40,
    CONF_FAST_CURRENT: 60,
    CONF_PEAK_LIMIT: 5000,
    CONF_FLOOR: 5,
    CONF_FULL_SOC: 99,  # Deye sits at 99 % on float for days
    CONF_FULL_CURRENT: 5,
    CONF_FULL_MINUTES: 10,
}

# Modes the user picks
MODE_MAX: Final = "max"
MODE_NIGHT: Final = "night"
MODE_TRAINING: Final = "training"
MODES: Final = (MODE_MAX, MODE_NIGHT, MODE_TRAINING)

# Profiles the inverter can be in (the "actual mode" sensor)
PROFILE_MAX: Final = "max"
PROFILE_NIGHT: Final = "night"
PROFILE_CHARGE_NOW: Final = "charge_now"
PROFILE_TRAINING_DRAIN: Final = "training_drain"
PROFILE_TRAINING_CHARGE: Final = "training_charge"
ACTUAL_MANUAL: Final = "manual"
ACTUAL_UNKNOWN: Final = "unknown"
ACTUAL_STATES: Final = (
    PROFILE_MAX,
    PROFILE_NIGHT,
    PROFILE_CHARGE_NOW,
    PROFILE_TRAINING_DRAIN,
    PROFILE_TRAINING_CHARGE,
    ACTUAL_MANUAL,
    ACTUAL_UNKNOWN,
)

# Training phases
PHASE_IDLE: Final = "idle"
PHASE_DRAIN: Final = "drain"
PHASE_CHARGE: Final = "charge"
PHASE_DONE: Final = "done"
PHASE_ABORTED: Final = "aborted"
PHASES: Final = (PHASE_IDLE, PHASE_DRAIN, PHASE_CHARGE, PHASE_DONE, PHASE_ABORTED)

# Training: the drain is over when Deye stops discharging this close to the floor
STALL_SOC_MARGIN: Final = 3  # %
STALL_POWER: Final = 50  # W
STALL_MINUTES: Final = 5
# The original shutdown SOC comes back once the battery is this far above it
RESTORE_SHUTDOWN_MARGIN: Final = 5  # %

SLOT_POWER: Final = 6000  # W, max battery power in every TOU slot
CLOCK_MAX_DRIFT: Final = 60  # s

STORAGE_VERSION: Final = 1
