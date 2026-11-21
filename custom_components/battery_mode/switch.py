"""Charge to 100 % now: a one-shot override that ends by itself when the battery is full."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import BatteryModeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ChargeNowSwitch(entry)])


class ChargeNowSwitch(BatteryModeEntity, SwitchEntity):
    def __init__(self, entry) -> None:
        super().__init__(entry, "charge_now")

    @property
    def is_on(self) -> bool:
        return self.controller.charge_now

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_charge_now(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_charge_now(False)
