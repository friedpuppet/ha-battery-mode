"""Pure register/profile logic."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.battery_mode.const import SLOT_POWER
from custom_components.battery_mode.registers import (
    REG_FLAGS,
    REG_GRID_CHARGE_CURRENT,
    REG_SLOT_CHARGE,
    REG_SLOT_SOC,
    REG_SLOT_TIME,
    Field,
    Settings,
    Slot,
    active_slot,
    decode_clock,
    discharge_floor,
    diff,
    encode_clock,
    mismatches,
    profile_drain,
    profile_max,
    profile_night,
    slot_layout,
    write_runs,
)

from .conftest import LIVE

SETTINGS = Settings("23:00", "07:00", reserve=30, floor=5, normal_current=40, fast_current=60, peak_limit=5000, slot_power=SLOT_POWER)


def test_slot_layout_night_23_07() -> None:
    assert slot_layout("23:00", "07:00") == [
        Slot(180, True),
        Slot(420, False),
        Slot(660, False),
        Slot(900, False),
        Slot(1140, False),
        Slot(1380, True),
    ]


def test_slot_layout_accepts_seconds_and_other_windows() -> None:
    layout = slot_layout("22:00:00", "06:00:00")
    assert [s.start for s in layout] == [120, 360, 600, 840, 1080, 1320]
    assert [s.night for s in layout] == [True, False, False, False, False, True]
    # a window that doesn't cross midnight
    assert [s.night for s in slot_layout("01:00", "05:00")] == [True, True, False, False, False, False]


def test_slot_layout_rejects_whole_day() -> None:
    with pytest.raises(ValueError):
        slot_layout("07:00", "07:00")


def test_night_profile() -> None:
    profile = profile_night(SETTINGS)
    assert [profile[REG_SLOT_TIME + i].value for i in range(6)] == [300, 700, 1100, 1500, 1900, 2300]
    assert [profile[REG_SLOT_SOC + i].value for i in range(6)] == [100, 30, 30, 30, 30, 100]
    assert all(profile[REG_SLOT_CHARGE + i] == Field(1, 0x3) for i in range(6))
    assert profile[REG_GRID_CHARGE_CURRENT].value == 60
    assert profile[REG_FLAGS] == Field(0x100, 0x100)


def test_drain_profile_no_grid_charge() -> None:
    profile = profile_drain(SETTINGS)
    assert [profile[REG_SLOT_SOC + i].value for i in range(6)] == [5] * 6
    assert all(profile[REG_SLOT_CHARGE + i].value == 0 for i in range(6))


def test_diff_keeps_other_bits() -> None:
    registers = dict(LIVE)
    registers[REG_FLAGS] = 0x1000  # some other flag, peak shaving off
    registers[REG_SLOT_CHARGE] = 0x4  # inverter's bit 2, grid off
    changes = diff(profile_max(SETTINGS), registers)
    assert changes[REG_FLAGS] == 0x1100
    assert changes[REG_SLOT_CHARGE] == 0x5


def test_live_state_differs_from_max_only_in_slot_times() -> None:
    changes = diff(profile_max(SETTINGS), LIVE)
    assert set(changes) == {REG_SLOT_TIME + i for i in range(6)}
    assert mismatches(profile_max(SETTINGS), LIVE)["slot_time_1"] == {"want": 300, "have": 100}


def test_write_runs_merge_small_gaps() -> None:
    registers = {a: 0 for a in range(0x100, 0x120)}
    changes = {0x100: 1, 0x101: 2, 0x104: 3, 0x110: 4}
    assert write_runs(changes, registers) == [(0x100, [1, 2, 0, 0, 3]), (0x110, [4])]


def test_clock_round_trip() -> None:
    tz = ZoneInfo("Europe/Kyiv")
    now = datetime(2026, 11, 21, 1, 29, 26, tzinfo=tz)
    values = encode_clock(now)
    assert values == [(26 << 8) | 11, (21 << 8) | 1, (29 << 8) | 26]
    assert decode_clock(values, tz) == now


def test_active_slot_wraps_midnight() -> None:
    registers = dict(LIVE)
    for i, t in enumerate([300, 700, 1100, 1500, 1900, 2300]):
        registers[REG_SLOT_TIME + i] = t
    assert active_slot(registers, 3 * 60) == 0
    assert active_slot(registers, 12 * 60) == 2
    assert active_slot(registers, 23 * 60 + 30) == 5
    assert active_slot(registers, 60) == 5  # 01:00 is still the 23:00 slot


def test_discharge_floor() -> None:
    registers = dict(LIVE)  # Low Batt 35, shutdown 20
    for i, (t, soc) in enumerate(zip([300, 700, 1100, 1500, 1900, 2300], [100, 30, 30, 30, 30, 100])):
        registers[REG_SLOT_TIME + i] = t
        registers[REG_SLOT_SOC + i] = soc
    assert discharge_floor(registers, 12 * 60, grid=True) == 35  # slot 30 clamped to Low Batt
    assert discharge_floor(registers, 1 * 60, grid=True) == 100
    assert discharge_floor(registers, 12 * 60, grid=False) == 20
    registers[0xDB] = 25
    assert discharge_floor(registers, 12 * 60, grid=True) == 30
