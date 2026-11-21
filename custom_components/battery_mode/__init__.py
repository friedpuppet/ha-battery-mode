"""Battery Mode: battery usage and charging modes for a Deye hybrid inverter."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .controller import Controller

PLATFORMS = [Platform.BUTTON, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type BatteryModeConfigEntry = ConfigEntry[Controller]


async def async_setup_entry(hass: HomeAssistant, entry: BatteryModeConfigEntry) -> bool:
    controller = Controller(hass, entry)
    await controller.async_start()
    entry.runtime_data = controller
    entry.async_on_unload(controller.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BatteryModeConfigEntry) -> None:
    """New options change the profile: write it (no reload, the controller reads options live)."""
    await entry.runtime_data.async_apply()


async def async_unload_entry(hass: HomeAssistant, entry: BatteryModeConfigEntry) -> bool:
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
