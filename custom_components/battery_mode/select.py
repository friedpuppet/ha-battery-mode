"""The mode the user picks."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MODES
from .entity import BatteryModeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ModeSelect(entry)])


class ModeSelect(BatteryModeEntity, SelectEntity):
    _attr_options = list(MODES)

    def __init__(self, entry) -> None:
        super().__init__(entry, "mode")

    @property
    def current_option(self) -> str:
        return self.controller.mode

    async def async_select_option(self, option: str) -> None:
        await self.controller.async_set_mode(option)
