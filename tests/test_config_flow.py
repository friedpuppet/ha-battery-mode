"""Config and options flows."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_mode.const import CONF_DEVICE, CONF_GRID_ENTITY, CONF_SOC_ENTITY, DOMAIN

from .conftest import FakeDeye, set_battery, setup_entry


async def test_user_flow_suggests_deye_entities(hass: HomeAssistant, deye: FakeDeye) -> None:
    solarman = MockConfigEntry(domain="solarman", title="Deye")
    solarman.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=solarman.entry_id, identifiers={("solarman", "2608201129")}, name="deye-inverter"
    )
    ent = er.async_get(hass)
    for suffix in ("battery", "battery_current", "battery_power"):
        ent.async_get_or_create("sensor", "solarman", suffix, suggested_object_id=f"inv_{suffix}", device_id=device.id)
    hass.states.async_set("binary_sensor.e_elektrika", "on")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE: device.id})
    assert result["step_id"] == "entities"
    schema = {str(k): k.description for k in result["data_schema"].schema}
    assert schema[CONF_SOC_ENTITY] == {"suggested_value": "sensor.inv_battery"}
    assert schema[CONF_GRID_ENTITY] == {"suggested_value": "binary_sensor.e_elektrika"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "soc_entity": "sensor.inv_battery",
            "current_entity": "sensor.inv_battery_current",
            "power_entity": "sensor.inv_battery_power",
            "grid_entity": "binary_sensor.e_elektrika",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "deye-inverter battery mode"
    assert result["options"]["peak_limit"] == 5000


async def test_options_flow_validates_window(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    entry = await setup_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    data = {**entry.options, "night_start": "07:00:00", "night_end": "07:00:00"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["errors"] == {"base": "bad_night_window"}
    data.update(night_start="22:00:00", night_end="06:00:00", reserve=40)
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # new options are written at once
    assert deye.registers[0xFA] == 200
