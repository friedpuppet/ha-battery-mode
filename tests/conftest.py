"""Fixtures: a fake Deye behind fake ha-solarman services."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.battery_mode import controller as controller_module
from custom_components.battery_mode.const import (
    CONF_CURRENT_ENTITY,
    CONF_DEVICE,
    CONF_GRID_ENTITY,
    CONF_POWER_ENTITY,
    CONF_SOC_ENTITY,
    DEFAULTS,
    DOMAIN,
)
from custom_components.battery_mode.registers import REG_CLOCK, decode_clock, encode_clock

SOC = "sensor.deye_battery"
CURRENT = "sensor.deye_battery_current"
POWER = "sensor.deye_battery_power"
GRID = "binary_sensor.e_elektrika"
DEVICE = "deye-device-id"

MODE = "select.deye_battery_mode_mode"
ACTUAL = "sensor.deye_battery_mode_actual_mode"
TRAINING = "sensor.deye_battery_mode_training"
CHARGE_NOW = "switch.deye_battery_mode_charge_to_100_now"
APPLY = "button.deye_battery_mode_apply_mode_again"
CANCEL = "button.deye_battery_mode_cancel_training"

# Live values read on 2026-11-21 (0xD2.., 0xF8..), after Phase 0: TOU on, slots 100/Grid, peak shaving 5000 W
LIVE = {
    **dict(zip(range(0xD2, 0xE9), [40, 120, 0, 1, 1, 8, 990, 20, 25, 35, 4600, 5200, 4750, 240, 1440, 4900, 30, 40, 4900, 30, 40, 0, 1])),
    **dict(
        zip(
            range(0xF8, 0x126),
            [255, 0, 100, 500, 900, 1300, 1700, 2100]
            + [6000] * 6
            + [4900] * 6
            + [100] * 6
            + [5] * 6
            + [256, 65535, 60, 0, 10, 0, 0, 2875, 1840, 5150, 4750, 0, 6000, 5000],
        )
    ),
}


class FakeDeye:
    """Holding registers; optional silent rejection of values; reachability."""

    def __init__(self) -> None:
        self.registers: dict[int, int] = dict(LIVE)
        self.clock_offset = 0.0  # s, inverter clock minus real time
        self.reject: dict[int, set[int]] = {}  # address → values the inverter silently ignores
        self.reachable = True
        self.writes: list[tuple[int, list[int]]] = []

    def _clock(self) -> list[int]:
        return encode_clock(dt_util.now() + timedelta(seconds=self.clock_offset))

    def read(self, address: int, count: int) -> dict[int, int]:
        if not self.reachable:
            raise HomeAssistantError("timeout")
        out = {}
        for a in range(address, address + count):
            if REG_CLOCK <= a < REG_CLOCK + 3:
                out[a] = self._clock()[a - REG_CLOCK]
            else:
                out[a] = self.registers.get(a, 0)
        return out

    def write(self, address: int, values: list[int]) -> None:
        if not self.reachable:
            raise HomeAssistantError("timeout")
        self.writes.append((address, list(values)))
        if address == REG_CLOCK:
            inverter = decode_clock(values, dt_util.get_default_time_zone())
            self.clock_offset = (inverter - dt_util.now()).total_seconds()
            return
        for i, value in enumerate(values):
            if value in self.reject.get(address + i, ()):
                continue
            self.registers[address + i] = value

    def written(self) -> set[int]:
        """Addresses touched by writes (clock excluded)."""
        return {a + i for a, values in self.writes if a != REG_CLOCK for i in range(len(values))}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_timing(monkeypatch):
    monkeypatch.setattr(controller_module, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(controller_module, "RETRY_SECONDS", 0)


@pytest.fixture
async def deye(hass: HomeAssistant) -> FakeDeye:
    fake = FakeDeye()

    async def read(call: ServiceCall) -> dict[str, Any]:
        assert call.data["device"] == DEVICE
        return {str(k): v for k, v in fake.read(int(call.data["address"]), int(call.data["count"])).items()}

    async def write(call: ServiceCall) -> None:
        assert call.data["device"] == DEVICE
        fake.write(int(call.data["address"]), [int(v) for v in call.data["values"]])

    hass.services.async_register("solarman", "read_holding_registers", read, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("solarman", "write_multiple_registers", write)
    return fake


def set_battery(hass: HomeAssistant, soc: float | str, current: float = 0, power: float | None = None) -> None:
    hass.states.async_set(SOC, str(soc))
    hass.states.async_set(CURRENT, str(current))
    hass.states.async_set(POWER, str(power if power is not None else current * 52))  # + = discharging, as on Deye


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Deye battery mode",
        unique_id=DEVICE,
        data={
            CONF_DEVICE: DEVICE,
            CONF_SOC_ENTITY: SOC,
            CONF_CURRENT_ENTITY: CURRENT,
            CONF_POWER_ENTITY: POWER,
            CONF_GRID_ENTITY: GRID,
        },
        options={**DEFAULTS, **(options or {})},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
