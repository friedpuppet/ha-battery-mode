# battery-mode — Home Assistant integration

Subproject of `homeassistant` (see `../AGENTS.md`). Custom integration **`battery_mode`**: battery usage and charging
modes for the Deye SUN-6K through ha-solarman's register services. Plan:
`~/.claude/plans/typed-inventing-wilkinson-of-5daf4d42-c9e1-4a5a-8232-7a218acbf882.md`; the inverter behaviour it relies
on (Phase 0 tests, register quirks): `../deye-inverter/battery-modes.md`. README has modes, entities and rules.

## Status

- v0.1.0 written 2026-11-21, `uv run pytest`: 33 passed (HA 2026.8.3 test harness). Not yet on GitHub / the live HA.
- Same conventions as `../grid-load-shedding/` (personal project, Marketplace custom repository, hassfest + pytest CI,
  bump `manifest.json` `version` per release).

## Layout

| Path | What |
|---|---|
| `custom_components/battery_mode/registers.py` | Pure: register map, slot layout, profiles (max / night / charge fast / drain), `diff` (keeps foreign bits), `write_runs`, clock encode/decode. |
| `.../inverter.py` | `DeyeClient`: `solarman.read_holding_registers` / `write_multiple_registers`. |
| `.../controller.py` | `Controller`: persisted mode / charge-now / training / pending / restore_shutdown (`Store` `battery_mode.<entry_id>`), apply with read-back, actual-mode detection, training state machine, clock sync. |
| `.../select.py` `sensor.py` `switch.py` `button.py` | Entities on the entry's service device. |
| `tests/conftest.py` | `FakeDeye` behind fake solarman services (live register values of 2026-11-21, silent rejection, unreachable logger). |

## Decisions (with the user, 2026-11-21)

- Night mode holds a reserve in the day natively (no 30→50 % day recharge at the day tariff).
- 60 A at night with Deye grid peak shaving at 5000 W (everything goes through the Deye; 25 A input breaker tolerates
  short overloads; lower it if the breaker trips).
- Training: one cycle, floor 5 %, back to the previous mode.
- Charge-now override added (one-shot, ends when full).
