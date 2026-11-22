# battery-mode — Home Assistant integration

Subproject of `homeassistant` (see `../AGENTS.md`). Custom integration **`battery_mode`**: battery usage and charging
modes for the Deye SUN-6K through ha-solarman's register services. Plan:
`~/.claude/plans/typed-inventing-wilkinson-of-5daf4d42-c9e1-4a5a-8232-7a218acbf882.md`; the inverter behaviour it relies
on (Phase 0 tests, register quirks): `../deye-inverter/battery-modes.md`. README has modes, entities and rules.

## Status

- v0.1.0 released 2026-11-21, installed on the live HA via Marketplace (repo id `1466029200`), entry «Батарея» `01M7XQQE5FZ7JTZBWVHFN7A1P5`. v0.1.1: `slots` / currents / peak attributes on Actual mode (ha-solarman polls those registers rarely, its entities lag). v0.1.2: the training bottom is also a stall at any SOC (was only ≤ floor + 3 %), attribute `bottom_reason` soc/stall. v0.1.3: the drain phase lowers Low Batt (0xDB) too: with grid Deye stops at Low Batt, not at the shutdown SOC (first training stopped at 35 % = Low Batt on 2026-11-21). Tests wait for background tasks (`wait_background_tasks=True`). v0.1.4 added a Discharge floor sensor (`sensor.batareia_mezha_rozriadu`) as grid_load_shedding's reserve; v0.1.5 (2026-11-22, released and live) removed it again: nothing uses it since the time left counts to Deye's shutdown SOC (the user: the full discharge, independent of mode; now `../battery-monitor/`). Tests: 37 passed.
- Same conventions as `../grid-load-shedding/` (personal project, Marketplace custom repository, hassfest + pytest CI,
  bump `manifest.json` `version` per release).

## Layout

| Path | What |
|---|---|
| `custom_components/battery_mode/registers.py` | Pure: register map, slot layout, profiles (max / night / charge fast / drain), `diff` (keeps foreign bits), `write_runs`, clock encode/decode, `active_slot` / `discharge_floor` (slot SOC, at least Low Batt, with grid; shutdown SOC without). |
| `.../inverter.py` | `DeyeClient`: `solarman.read_holding_registers` / `write_multiple_registers`. |
| `.../controller.py` | `Controller`: persisted mode / charge-now / training / pending / restore_shutdown / restore_low (`Store` `battery_mode.<entry_id>`), apply with read-back, actual-mode detection, training state machine (drain lowers shutdown SOC and Low Batt), `floor`, clock sync. |
| `.../select.py` `sensor.py` `switch.py` `button.py` | Entities on the entry's service device. |
| `tests/conftest.py` | `FakeDeye` behind fake solarman services (live register values of 2026-11-21, silent rejection, unreachable logger). |

## Decisions (with the user, 2026-11-21)

- Night mode holds a reserve in the day natively (no 30→50 % day recharge at the day tariff).
- 60 A at night with Deye grid peak shaving at 5000 W (everything goes through the Deye; 25 A input breaker tolerates
  short overloads; lower it if the breaker trips).
- Training: one cycle, floor 5 %, back to the previous mode.
- Charge-now override added (one-shot, ends when full).

## Live

- Entity ids (renamed after install): `select.batareia_rezhym`, `sensor.batareia_rezhym_fakt`, `sensor.batareia_trenuvannia`,
  `switch.batareia_zariadyty_zaraz`, `button.batareia_zastosuvaty_rezhym`, `button.batareia_skasuvaty_trenuvannia`;
  device «Батарея».
- First apply 2026-11-21 00:23 UTC: only the slot times changed (01/05/…/21 → 03/07/11/15/19/23), mode Максимум.
- Training runs (2026-11-21, details in `../deye-inverter/battery-modes.md`): the first stopped at 35 % = Low Batt
  (stall), cancelled by the user while charging; the second started 19:20 Kyiv at 39 % with Low Batt and shutdown SOC
  both 5 % (Deye accepted them).
- Updating: bump version, push, GitHub release, then WS `marketplace/repository/install` with `repository: "1466029200"`,
  `version: "vX.Y.Z"`, then restart Core (updates need a restart).
- Push: `git -c http.https://github.com/.extraheader="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$(cat ~/.config/github/token-grid-load-shedding)" | base64 -w0)" push`
  (that PAT also covers this repo).
