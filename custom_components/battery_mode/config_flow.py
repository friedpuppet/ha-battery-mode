"""Config and options flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er, selector

from .const import (
    CONF_CURRENT_ENTITY,
    CONF_DEVICE,
    CONF_FAST_CURRENT,
    CONF_FLOOR,
    CONF_FULL_CURRENT,
    CONF_FULL_MINUTES,
    CONF_FULL_SOC,
    CONF_GRID_ENTITY,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_NORMAL_CURRENT,
    CONF_PEAK_LIMIT,
    CONF_POWER_ENTITY,
    CONF_RESERVE,
    CONF_SOC_ENTITY,
    DEFAULTS,
    DOMAIN,
)
from .registers import slot_layout

# entity_id suffixes of ha-solarman's Deye profile
_SUGGEST = {
    CONF_SOC_ENTITY: ("sensor", "_battery"),
    CONF_CURRENT_ENTITY: ("sensor", "_battery_current"),
    CONF_POWER_ENTITY: ("sensor", "_battery_power"),
}


def _number(minimum: float, maximum: float, unit: str, step: float = 1) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=selector.NumberSelectorMode.BOX
        )
    )


class BatteryModeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._device_id: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._device_id = user_input[CONF_DEVICE]
            await self.async_set_unique_id(self._device_id)
            self._abort_if_unique_id_configured()
            return await self.async_step_entities()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE): selector.DeviceSelector(
                        selector.DeviceSelectorConfig(integration="solarman")
                    )
                }
            ),
        )

    async def async_step_entities(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            device = dr.async_get(self.hass).async_get(self._device_id)
            title = (device.name_by_user or device.name) if device else "Battery mode"
            return self.async_create_entry(
                title=f"{title} battery mode",
                data={CONF_DEVICE: self._device_id, **user_input},
                options=dict(DEFAULTS),
            )
        suggested: dict[str, str] = {}
        for entry in er.async_entries_for_device(er.async_get(self.hass), self._device_id):
            for key, (domain, suffix) in _SUGGEST.items():
                if entry.domain == domain and entry.entity_id.endswith(suffix):
                    suggested[key] = entry.entity_id
        if self.hass.states.get("binary_sensor.e_elektrika"):
            suggested[CONF_GRID_ENTITY] = "binary_sensor.e_elektrika"

        def entity(key: str, domain: str, **kwargs) -> tuple:
            return (
                vol.Required(key, description={"suggested_value": suggested.get(key)}),
                selector.EntitySelector(selector.EntitySelectorConfig(domain=domain, **kwargs)),
            )

        fields = dict(
            [
                entity(CONF_SOC_ENTITY, "sensor"),
                entity(CONF_CURRENT_ENTITY, "sensor"),
                entity(CONF_POWER_ENTITY, "sensor"),
                entity(CONF_GRID_ENTITY, "binary_sensor"),
            ]
        )
        return self.async_show_form(step_id="entities", data_schema=vol.Schema(fields))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return BatteryModeOptionsFlow()


class BatteryModeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                slot_layout(user_input[CONF_NIGHT_START], user_input[CONF_NIGHT_END])
            except ValueError:
                errors["base"] = "bad_night_window"
            else:
                return self.async_create_entry(data=user_input)
        current = {**DEFAULTS, **self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Required(CONF_RESERVE, default=current[CONF_RESERVE]): _number(5, 100, "%"),
                vol.Required(CONF_NIGHT_START, default=current[CONF_NIGHT_START]): selector.TimeSelector(),
                vol.Required(CONF_NIGHT_END, default=current[CONF_NIGHT_END]): selector.TimeSelector(),
                vol.Required(CONF_NORMAL_CURRENT, default=current[CONF_NORMAL_CURRENT]): _number(0, 240, "A"),
                vol.Required(CONF_FAST_CURRENT, default=current[CONF_FAST_CURRENT]): _number(0, 240, "A"),
                vol.Required(CONF_PEAK_LIMIT, default=current[CONF_PEAK_LIMIT]): _number(1000, 22000, "W", 100),
                vol.Required(CONF_FLOOR, default=current[CONF_FLOOR]): _number(1, 50, "%"),
                vol.Required(CONF_FULL_SOC, default=current[CONF_FULL_SOC]): _number(90, 100, "%"),
                vol.Required(CONF_FULL_CURRENT, default=current[CONF_FULL_CURRENT]): _number(0, 50, "A"),
                vol.Required(CONF_FULL_MINUTES, default=current[CONF_FULL_MINUTES]): _number(1, 120, "min"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
