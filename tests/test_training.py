"""The training cycle: drain to the floor, charge to full, measure, restore."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.battery_mode.registers import REG_GRID_CHARGE_CURRENT, REG_SHUTDOWN_SOC, REG_SLOT_CHARGE, REG_SLOT_SOC

from .conftest import ACTUAL, CANCEL, CHARGE_NOW, GRID, MODE, TRAINING, FakeDeye, set_battery, setup_entry


async def select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": MODE, "option": option}, blocking=True)
    await hass.async_block_till_done()


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float, step: float = 0.5) -> None:
    for _ in range(int(minutes / step)):
        freezer.tick(timedelta(minutes=step))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def slots(deye: FakeDeye) -> list[int]:
    return [deye.registers[REG_SLOT_SOC + i] for i in range(6)]


async def start(hass: HomeAssistant, deye: FakeDeye, soc: float = 99) -> None:
    set_battery(hass, soc)
    hass.states.async_set(GRID, "on")
    await setup_entry(hass)
    await select(hass, "night")
    await select(hass, "training")


async def test_full_cycle(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    await start(hass, deye)
    assert hass.states.get(TRAINING).state == "drain"
    assert hass.states.get(ACTUAL).state == "training_drain"
    assert slots(deye) == [5] * 6
    assert all(deye.registers[REG_SLOT_CHARGE + i] & 0x3 == 0 for i in range(6))
    assert deye.registers[REG_SHUTDOWN_SOC] == 5

    # discharging
    set_battery(hass, 30, current=12)
    await tick(hass, freezer, 5)
    assert hass.states.get(TRAINING).state == "drain"

    # bottom
    set_battery(hass, 5, current=0, power=0)
    await hass.async_block_till_done()
    assert hass.states.get(TRAINING).state == "charge"
    assert hass.states.get(ACTUAL).state == "training_charge"
    assert slots(deye) == [100] * 6
    assert deye.registers[REG_GRID_CHARGE_CURRENT] == 60
    assert deye.registers[REG_SHUTDOWN_SOC] == 5  # still at the floor: 5 % with 20 % would black out at once
    assert hass.states.get(ACTUAL).attributes["restore_shutdown_soc"] == 20

    # one hour at 50 A, 2.6 kW
    set_battery(hass, 20, current=-50, power=-2600)
    await tick(hass, freezer, 60)
    assert deye.registers[REG_SHUTDOWN_SOC] == 5
    set_battery(hass, 25, current=-50, power=-2600)
    await hass.async_block_till_done()
    assert deye.registers[REG_SHUTDOWN_SOC] == 20  # 25 ≥ 20 + 5
    attrs = hass.states.get(TRAINING).attributes
    assert attrs["charged_ah"] == pytest.approx(50, abs=1)
    assert attrs["charged_kwh"] == pytest.approx(2.6, abs=0.05)

    # full
    set_battery(hass, 99, current=-1, power=-50)
    await tick(hass, freezer, 11)
    state = hass.states.get(TRAINING)
    assert state.state == "done"
    assert state.attributes["valid"] is True
    assert state.attributes["bottom_soc"] == 5
    assert hass.states.get(MODE).state == "night"  # back to the mode before
    assert hass.states.get(ACTUAL).state == "night"
    assert deye.registers[REG_SHUTDOWN_SOC] == 20


async def test_stall_counts_as_bottom(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    """Deye stops at its own limit above the floor: 5 min without discharge near the floor."""
    await start(hass, deye)
    set_battery(hass, 7, current=0.2, power=10)
    await tick(hass, freezer, 4)
    assert hass.states.get(TRAINING).state == "drain"
    await tick(hass, freezer, 2)
    assert hass.states.get(TRAINING).state == "charge"
    assert hass.states.get(TRAINING).attributes["bottom_soc"] == 7


async def test_grid_loss_high_soc_restores_at_once(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    set_battery(hass, 50, current=10)
    hass.states.async_set(GRID, "off")
    await hass.async_block_till_done()
    state = hass.states.get(TRAINING)
    assert state.state == "aborted"
    assert state.attributes["reason"] == "grid_lost"
    assert hass.states.get(MODE).state == "night"
    assert deye.registers[REG_SHUTDOWN_SOC] == 20
    assert slots(deye) == [100, 30, 30, 30, 30, 100]


async def test_grid_loss_low_soc_keeps_floor_until_charged(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    set_battery(hass, 12, current=10)
    hass.states.async_set(GRID, "off")
    await hass.async_block_till_done()
    assert hass.states.get(TRAINING).state == "aborted"
    assert deye.registers[REG_SHUTDOWN_SOC] == 5  # 20 now would shut the flat down
    hass.states.async_set(GRID, "on")
    set_battery(hass, 24, current=-40)
    await hass.async_block_till_done()
    assert deye.registers[REG_SHUTDOWN_SOC] == 5
    set_battery(hass, 25, current=-40)
    await hass.async_block_till_done()
    assert deye.registers[REG_SHUTDOWN_SOC] == 20


async def test_unknown_grid_is_not_a_loss(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    hass.states.async_set(GRID, "unavailable")
    set_battery(hass, 50, current=10)
    await hass.async_block_till_done()
    assert hass.states.get(TRAINING).state == "drain"


async def test_grid_loss_in_charge_marks_invalid(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    await start(hass, deye)
    set_battery(hass, 5, power=0)
    await hass.async_block_till_done()
    hass.states.async_set(GRID, "off")
    await hass.async_block_till_done()
    hass.states.async_set(GRID, "on")
    set_battery(hass, 99, current=-1)
    await tick(hass, freezer, 11)
    state = hass.states.get(TRAINING)
    assert state.state == "done"
    assert state.attributes["valid"] is False
    assert state.attributes["reason"] == "grid_lost"


async def test_cancel_button(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    set_battery(hass, 60, current=10)
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": CANCEL}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(TRAINING).state == "aborted"
    assert hass.states.get(TRAINING).attributes["reason"] == "cancelled"
    assert hass.states.get(MODE).state == "night"
    assert deye.registers[REG_SHUTDOWN_SOC] == 20
    assert hass.states.get(CANCEL).state == "unavailable"


async def test_selecting_another_mode_cancels(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    set_battery(hass, 60, current=10)
    await select(hass, "max")
    assert hass.states.get(TRAINING).state == "aborted"
    assert hass.states.get(MODE).state == "max"
    assert slots(deye) == [100] * 6
    assert deye.registers[REG_SHUTDOWN_SOC] == 20


async def test_no_start_without_grid(hass: HomeAssistant, deye: FakeDeye) -> None:
    set_battery(hass, 99)
    hass.states.async_set(GRID, "off")
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError):
        await select(hass, "training")
    assert hass.states.get(MODE).state == "max"
    assert deye.registers[REG_SHUTDOWN_SOC] == 20


async def test_no_charge_now_during_training(hass: HomeAssistant, deye: FakeDeye) -> None:
    await start(hass, deye)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_on", {"entity_id": CHARGE_NOW}, blocking=True)


async def test_training_survives_restart(hass: HomeAssistant, deye: FakeDeye, freezer: FrozenDateTimeFactory) -> None:
    await start(hass, deye)
    set_battery(hass, 5, power=0)
    await hass.async_block_till_done()
    set_battery(hass, 30, current=-50, power=-2600)
    await tick(hass, freezer, 30)
    entry = hass.config_entries.async_entries("battery_mode")[0]
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(TRAINING)
    assert state.state == "charge"
    assert state.attributes["charged_ah"] == pytest.approx(25, abs=1)
    assert hass.states.get(MODE).state == "training"
    assert deye.registers[REG_SHUTDOWN_SOC] == 20  # restored (30 ≥ 25)
