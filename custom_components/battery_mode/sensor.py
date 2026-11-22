"""What the inverter is actually set to, and the training cycle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTUAL_STATES, PHASES
from .entity import BatteryModeEntity
from .registers import (
    FLAG_PEAK_SHAVING,
    REG_FLAGS,
    REG_GRID_CHARGE_CURRENT,
    REG_MAX_CHARGE_CURRENT,
    REG_PEAK_LIMIT,
    REG_LOW_SOC,
    REG_SHUTDOWN_SOC,
    REG_SLOT_CHARGE,
    REG_SLOT_SOC,
    REG_SLOT_TIME,
    SLOT_CHARGE_GRID,
    SLOTS,
)


def _inverter(registers: dict[int, int] | None) -> dict[str, Any]:
    """The inverter's settings as last read (ha-solarman polls these registers rarely)."""
    if not registers:
        return {}
    return {
        "slots": [
            {
                "time": f"{registers[REG_SLOT_TIME + i] // 100:02d}:{registers[REG_SLOT_TIME + i] % 100:02d}",
                "soc": registers[REG_SLOT_SOC + i],
                "grid": bool(registers[REG_SLOT_CHARGE + i] & SLOT_CHARGE_GRID),
            }
            for i in range(SLOTS)
        ],
        "grid_charge_current": registers[REG_GRID_CHARGE_CURRENT],
        "max_charge_current": registers[REG_MAX_CHARGE_CURRENT],
        "shutdown_soc": registers[REG_SHUTDOWN_SOC],
        "low_soc": registers[REG_LOW_SOC],
        "peak_shaving": bool(registers[REG_FLAGS] & FLAG_PEAK_SHAVING),
        "peak_limit": registers[REG_PEAK_LIMIT],
    }


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ActualModeSensor(entry), TrainingSensor(entry)])


def _iso(value) -> str | None:
    return value.isoformat() if value else None


class ActualModeSensor(BatteryModeEntity, SensorEntity):
    """The profile the inverter's registers match, «manual» if none."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ACTUAL_STATES)

    def __init__(self, entry) -> None:
        super().__init__(entry, "actual_mode")

    @property
    def native_value(self) -> str:
        return self.controller.actual

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        return {
            "diff": c.actual_diff,
            "pending": c.pending,
            "error": c.error,
            "last_read": _iso(c.last_read),
            "last_applied": _iso(c.last_applied),
            "clock_drift": c.clock_drift,
            "restore_shutdown_soc": c.restore_shutdown,
            "restore_low_soc": c.restore_low,
            **_inverter(c.registers),
        }


class TrainingSensor(BatteryModeEntity, SensorEntity):
    """Phase of the training cycle; the last result stays in the attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(PHASES)

    def __init__(self, entry) -> None:
        super().__init__(entry, "training")

    @property
    def native_value(self) -> str:
        return self.controller.training.get("phase", "idle")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self.controller.training
        return {k: v for k, v in t.items() if k != "phase"}
