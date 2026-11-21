"""Deye hybrid holding registers and the settings profiles written to them.

Pure functions, no Home Assistant. Addresses are from ha-solarman's ``deye_hybrid.yaml`` and were
checked live on a SUN-6K-SG06LP1-EU-BM2 (see ``deye-inverter/battery-modes.md`` in the home repo):

* TOU can't be turned off over Modbus: the inverter sets the enable bit of 0x00F8 again within
  minutes. So TOU is always on (0x00FF) and every mode is a set of slot values.
* Deye ignores function 0x06; writes go through 0x10 (``write_multiple_registers``).
* Some values are rejected silently, so every write is read back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

REG_CLOCK = 0x16  # 3 registers: YY<<8|MM, DD<<8|hh, mm<<8|ss, local time
REG_MAX_CHARGE_CURRENT = 0xD2  # A
REG_SHUTDOWN_SOC = 0xD9  # %
REG_GRID_CHARGE_CURRENT = 0xE6  # A
REG_GRID_CHARGE = 0xE8  # 1 = battery charging from the grid allowed
REG_TOU = 0xF8  # bit 0 enable, bits 1..7 Monday..Sunday
REG_SLOT_TIME = 0xFA  # 6 registers, HHMM
REG_SLOT_POWER = 0x100  # 6 registers, W
REG_SLOT_SOC = 0x10C  # 6 registers, %
REG_SLOT_CHARGE = 0x112  # 6 registers, bit 0 grid, bit 1 generator; bit 2 is set by the inverter, keep it
REG_FLAGS = 0x118  # bit 8 grid peak shaving; other bits belong to other settings
REG_PEAK_LIMIT = 0x125  # W

TOU_ALL_DAYS = 0xFF
SLOT_CHARGE_GRID = 0x1
SLOT_CHARGE_MASK = 0x3
FLAG_PEAK_SHAVING = 0x100

SLOTS = 6

# What we read: every register a profile touches
READ_BLOCKS: tuple[tuple[int, int], ...] = ((REG_MAX_CHARGE_CURRENT, 0xE8 - 0xD2 + 1), (REG_TOU, 0x125 - 0xF8 + 1))


def register_name(address: int) -> str:
    """Human-readable name for the ``diff`` attribute."""
    for base, name in (
        (REG_SLOT_TIME, "slot_time"),
        (REG_SLOT_POWER, "slot_power"),
        (REG_SLOT_SOC, "slot_soc"),
        (REG_SLOT_CHARGE, "slot_charge"),
    ):
        if base <= address < base + SLOTS:
            return f"{name}_{address - base + 1}"
    return {
        REG_MAX_CHARGE_CURRENT: "max_charge_current",
        REG_SHUTDOWN_SOC: "shutdown_soc",
        REG_GRID_CHARGE_CURRENT: "grid_charge_current",
        REG_GRID_CHARGE: "grid_charge",
        REG_TOU: "time_of_use",
        REG_FLAGS: "peak_shaving",
        REG_PEAK_LIMIT: "peak_limit",
    }.get(address, f"0x{address:04X}")


@dataclass(frozen=True)
class Field:
    """Wanted bits of one register: ``value`` under ``mask``."""

    value: int
    mask: int = 0xFFFF


type Profile = dict[int, Field]


@dataclass(frozen=True)
class Slot:
    start: int  # minutes since midnight
    night: bool


def parse_hhmm(text: str) -> int:
    """'23:00' or '23:00:00' → minutes since midnight."""
    parts = text.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def slot_layout(night_start: str, night_end: str) -> list[Slot]:
    """Six TOU slots, sorted by start time: two cover the night window, four the day.

    Slot i lasts until slot i+1 starts; the last one wraps around midnight to the first.
    Starts are rounded to 5 minutes.
    """
    start, end = parse_hhmm(night_start), parse_hhmm(night_end)
    night = (end - start) % 1440 or 1440
    if night >= 1440:
        raise ValueError("the night window must be shorter than a day")
    day = 1440 - night
    starts = [(start, True), (start + night // 2, True)]
    starts += [(end + day * i // 4, False) for i in range(4)]
    slots = [Slot(round((s % 1440) / 5) * 5 % 1440, n) for s, n in starts]
    slots.sort(key=lambda s: s.start)
    if len({s.start for s in slots}) != SLOTS:
        raise ValueError("night window too short for six distinct slots")
    return slots


@dataclass(frozen=True)
class Settings:
    """Everything a profile depends on (from the entry's options)."""

    night_start: str
    night_end: str
    reserve: int
    floor: int
    normal_current: int
    fast_current: int
    peak_limit: int
    slot_power: int


def _common(settings: Settings, layout: list[Slot], current: int) -> Profile:
    profile: Profile = {
        REG_TOU: Field(TOU_ALL_DAYS),
        REG_GRID_CHARGE: Field(1),
        REG_GRID_CHARGE_CURRENT: Field(current),
        REG_MAX_CHARGE_CURRENT: Field(current),
        REG_FLAGS: Field(FLAG_PEAK_SHAVING, FLAG_PEAK_SHAVING),
        REG_PEAK_LIMIT: Field(settings.peak_limit),
    }
    for i, slot in enumerate(layout):
        profile[REG_SLOT_TIME + i] = Field(slot.start // 60 * 100 + slot.start % 60)
        profile[REG_SLOT_POWER + i] = Field(settings.slot_power)
    return profile


def _slots(profile: Profile, values: list[tuple[int, bool]]) -> Profile:
    for i, (soc, grid) in enumerate(values):
        profile[REG_SLOT_SOC + i] = Field(soc)
        profile[REG_SLOT_CHARGE + i] = Field(SLOT_CHARGE_GRID if grid else 0, SLOT_CHARGE_MASK)
    return profile


def profile_max(settings: Settings, current: int | None = None) -> Profile:
    """Hold the battery full whenever there is grid (the old non-TOU behaviour)."""
    layout = slot_layout(settings.night_start, settings.night_end)
    return _slots(_common(settings, layout, current or settings.normal_current), [(100, True)] * SLOTS)


def profile_charge_fast(settings: Settings) -> Profile:
    """Same slots as Максимум, at the fast current (charge now, training charge)."""
    return profile_max(settings, settings.fast_current)


def profile_night(settings: Settings) -> Profile:
    """Night slots charge to 100 %, day slots run from the battery down to the reserve and hold it."""
    layout = slot_layout(settings.night_start, settings.night_end)
    values = [(100 if s.night else settings.reserve, True) for s in layout]
    return _slots(_common(settings, layout, settings.fast_current), values)


def profile_drain(settings: Settings) -> Profile:
    """Discharge to the floor with the grid present, no grid charging (training).

    The shutdown SOC is lowered separately (the controller saves the original first).
    """
    layout = slot_layout(settings.night_start, settings.night_end)
    return _slots(_common(settings, layout, settings.normal_current), [(settings.floor, False)] * SLOTS)


def wanted(field: Field, current: int) -> int:
    return (current & ~field.mask & 0xFFFF) | (field.value & field.mask)


def diff(profile: Profile, registers: dict[int, int]) -> dict[int, int]:
    """Registers that differ from the profile → their new full value (other bits kept)."""
    changes = {}
    for address, field in profile.items():
        current = registers[address]
        new = wanted(field, current)
        if new != current:
            changes[address] = new
    return changes


def mismatches(profile: Profile, registers: dict[int, int]) -> dict[str, dict[str, int]]:
    """For the ``diff`` attribute: name → {"want", "have"} (masked bits only)."""
    out = {}
    for address, field in sorted(profile.items()):
        have = registers.get(address)
        if have is None or have & field.mask != field.value & field.mask:
            out[register_name(address)] = {
                "want": field.value & field.mask,
                "have": None if have is None else have & field.mask,
            }
    return out


def write_runs(changes: dict[int, int], registers: dict[int, int], gap: int = 2) -> list[tuple[int, list[int]]]:
    """Group changed registers into contiguous writes.

    Runs closer than ``gap`` unchanged registers are merged, rewriting the registers in between with
    their current values, so a profile change is a few writes instead of dozens.
    """
    runs: list[tuple[int, list[int]]] = []
    for address in sorted(changes):
        if runs:
            start, values = runs[-1]
            last = start + len(values) - 1
            if address - last - 1 <= gap:
                values.extend(registers[a] for a in range(last + 1, address))
                values.append(changes[address])
                continue
        runs.append((address, [changes[address]]))
    return runs


def encode_clock(now: datetime) -> list[int]:
    return [
        (now.year % 100) << 8 | now.month,
        now.day << 8 | now.hour,
        now.minute << 8 | now.second,
    ]


def decode_clock(values: list[int], tzinfo) -> datetime:
    return datetime(
        2000 + (values[0] >> 8),
        values[0] & 0xFF,
        values[1] >> 8,
        values[1] & 0xFF,
        values[2] >> 8,
        values[2] & 0xFF,
        tzinfo=tzinfo,
    )
