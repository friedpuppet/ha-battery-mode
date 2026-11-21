"""What the inverter is actually set to, and the training cycle."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ACTUAL_STATES, PHASES
from .entity import BatteryModeEntity


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
