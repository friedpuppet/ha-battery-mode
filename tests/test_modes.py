"""Modes, the actual-mode sensor, blind inverter, rejected writes, charge now, clock."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant

from custom_components.battery_mode.registers import (
    REG_CLOCK,
    REG_GRID_CHARGE_CURRENT,
    REG_PEAK_LIMIT,
    REG_SLOT_SOC,
    REG_SLOT_TIME,
)

from .conftest import ACTUAL, APPLY, CHARGE_NOW, GRID, MODE, SOC, FakeDeye, make_entry, set_battery, setup_entry


async def select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": option}, blocking=True)
    await hass.async_block_till_done()


async def press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float, step: float = 0.5) -> None:
    for _ in range(int(minutes / step)):
        freezer.tick(timedelta(minutes=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_startup_reads_and_writes_nothing(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    hass.states.async_set(GRID, "on")
    await setup_entry(hass)
    assert hass.states.get(MODE).state == "max"
    # the live slot times (01:00, 05:00, …) aren't the integration's layout yet
    state = hass.states.get(ACTUAL)
    assert state.state == "manual"
    assert set(state.attributes["diff"]) == {f"slot_time_{i}" for i in range(1, 7)}
    assert deye.written() == set()


async def test_apply_max_then_actual_max(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    await setup_entry(hass)
    await press(hass, APPLY)
    assert deye.written() == {REG_SLOT_TIME + i for i in range(6)}
    assert [deye.registers[REG_SLOT_TIME + i] for i in range(6)] == [300, 700, 1100, 1500, 1900, 2300]
    assert hass.states.get(ACTUAL).state == "max"


async def test_night_mode(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    await setup_entry(hass)
    await select(hass, "night")
    assert [deye.registers[REG_SLOT_SOC + i] for i in range(6)] == [100, 30, 30, 30, 30, 100]
    assert deye.registers[REG_GRID_CHARGE_CURRENT] == 60
    assert deye.registers[0xD2] == 60
    assert hass.states.get(ACTUAL).state == "night"
    assert hass.states.get(MODE).state == "night"


async def test_blind_inverter_queues_the_profile(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, "unavailable")
    await setup_entry(hass)
    await select(hass, "night")
    assert deye.written() == set()
    assert hass.states.get(ACTUAL).state == "unknown"
    assert hass.states.get(ACTUAL).attributes["pending"] is True
    set_battery(hass, 80)  # inverter visible again
    await hass.async_block_till_done()
    assert hass.states.get(ACTUAL).state == "night"
    assert hass.states.get(ACTUAL).attributes["pending"] is False


async def test_unreachable_logger_is_retried(
    hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory
) -> None:
    set_battery(hass, 80)
    await setup_entry(hass)
    deye.reachable = False
    await select(hass, "night")
    assert hass.states.get(ACTUAL).attributes["pending"] is True
    deye.reachable = True
    await tick(hass, freezer, 5.5)
    assert hass.states.get(ACTUAL).state == "night"


async def test_rejected_value_is_reported_not_retried(
    hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory
) -> None:
    set_battery(hass, 99)
    await setup_entry(hass, make_entry({"peak_limit": 800}))
    deye.reject[REG_PEAK_LIMIT] = {800}
    await press(hass, APPLY)
    state = hass.states.get(ACTUAL)
    assert state.state == "manual"
    assert "peak_limit" in state.attributes["error"]
    assert state.attributes["pending"] is False
    writes = len(deye.writes)
    await tick(hass, freezer, 11)
    assert len(deye.writes) == writes  # no retry every refresh


async def test_manual_change_is_shown_not_reverted(
    hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory
) -> None:
    set_battery(hass, 99)
    await setup_entry(hass)
    await select(hass, "night")
    writes = len(deye.writes)
    deye.registers[REG_SLOT_SOC + 1] = 50  # someone changed it in the Deye app
    await tick(hass, freezer, 5.5)
    state = hass.states.get(ACTUAL)
    assert state.state == "manual"
    assert state.attributes["diff"] == {"slot_soc_2": {"want": 30, "have": 50}}
    assert len(deye.writes) == writes


async def test_charge_now_ends_when_full(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    set_battery(hass, 40)
    hass.states.async_set(GRID, "on")
    await setup_entry(hass)
    await select(hass, "night")
    await hass.services.async_call("switch", "turn_on", {"entity_id": CHARGE_NOW}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(ACTUAL).state == "charge_now"
    assert [deye.registers[REG_SLOT_SOC + i] for i in range(6)] == [100] * 6
    set_battery(hass, 99, current=-30)
    await tick(hass, freezer, 15)
    assert hass.states.get(CHARGE_NOW).state == "on"  # still charging hard
    set_battery(hass, 99, current=-2)
    await tick(hass, freezer, 11)
    assert hass.states.get(CHARGE_NOW).state == "off"
    assert hass.states.get(ACTUAL).state == "night"
    assert [deye.registers[REG_SLOT_SOC + i] for i in range(6)] == [100, 30, 30, 30, 30, 100]


async def test_mode_change_cancels_charge_now(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 40)
    await setup_entry(hass)
    await hass.services.async_call("switch", "turn_on", {"entity_id": CHARGE_NOW}, blocking=True)
    await select(hass, "night")
    assert hass.states.get(CHARGE_NOW).state == "off"
    assert hass.states.get(ACTUAL).state == "night"


async def test_clock_sync(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    set_battery(hass, 99)
    deye.clock_offset = -120
    await setup_entry(hass)
    assert any(a == REG_CLOCK for a, _ in deye.writes)
    assert abs(deye.clock_offset) < 2
    assert abs(hass.states.get(ACTUAL).attributes["clock_drift"]) < 2
    deye.writes.clear()
    deye.clock_offset = -30  # within tolerance
    await tick(hass, freezer, 61, step=5)
    assert not any(a == REG_CLOCK for a, _ in deye.writes)


async def test_mode_survives_restart(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    entry = await setup_entry(hass)
    await select(hass, "night")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(MODE).state == "night"
    assert hass.states.get(SOC).state == "99"
