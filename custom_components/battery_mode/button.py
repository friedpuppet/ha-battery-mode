"""Apply the mode again; cancel the training cycle."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MODE_TRAINING
from .entity import BatteryModeEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ApplyButton(entry), CancelTrainingButton(entry)])


class ApplyButton(BatteryModeEntity, ButtonEntity):
    def __init__(self, entry) -> None:
        super().__init__(entry, "apply")

    async def async_press(self) -> None:
        await self.controller.async_apply()


class CancelTrainingButton(BatteryModeEntity, ButtonEntity):
    def __init__(self, entry) -> None:
        super().__init__(entry, "cancel_training")

    @property
    def available(self) -> bool:
        return self.controller.mode == MODE_TRAINING

    async def async_press(self) -> None:
        await self.controller.async_cancel_training()
