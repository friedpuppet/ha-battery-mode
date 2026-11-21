# Battery Mode

Home Assistant custom integration: battery usage and charging modes for a **Deye hybrid inverter**
read through [ha-solarman](https://github.com/davidrapan/ha-solarman). The behaviour itself lives in
the inverter (Time of Use slots, charge currents, grid peak shaving); the integration writes a profile
of registers, verifies it by reading back, and runs the parts Deye can't do alone.

Personal project, installed as a HACS / Marketplace custom repository.

## Modes (`select … Mode`)

| Mode | TOU slots | Charge current | Notes |
|---|---|---|---|
| **Max charge** | all six: SOC 100 %, grid charge | normal (40 A) | Battery held full whenever there is grid. |
| **Night charging** | night: SOC 100 %, grid charge; day: SOC = reserve (30 %), grid charge | fast (60 A) | Day runs from the battery down to the reserve, then from the grid. |
| **Training** | drain: all SOC = floor (5 %), no grid charge; then Max charge slots | fast | One cycle: shutdown SOC lowered to the floor, discharge with grid present, charge to full, measured Ah/kWh, settings restored, back to the previous mode. |

Plus **Charge to 100 % now** (`switch`): Max-charge slots at the fast current until full, then back to
the selected mode. Grid peak shaving is on in every mode (limit 5000 W by default), so the charge is
cut back when the flat draws a lot.

Six slots: two over the night window (23:00–07:00 by default), four over the day.

## Entities

- `select` **Mode**: max / night / training.
- `sensor` **Actual mode** (enum): which profile the registers match, or `manual`, or `unknown` when
  HA can't see the inverter. Attributes: `diff` (want/have per register), `pending`, `error`,
  `last_read`, `last_applied`, `clock_drift`, `restore_shutdown_soc`, `restore_low_soc`, and the inverter's `slots`, currents, `shutdown_soc`, `low_soc`, peak shaving.
- `sensor` **Training** (enum idle/drain/charge/done/aborted): attributes `started`, `start_soc`,
  `bottom_at`, `bottom_soc`, `bottom_reason`, `charged_ah`, `charged_kwh`, `finished`, `valid`, `reason`
  (`grid_lost`, `cancelled`), `original_shutdown_soc`, `original_low_soc`, `prev_mode`.
  With grid Deye discharges only down to **Low Batt** (0xDB); the shutdown SOC (0xD9) is the floor without
  grid. The drain phase lowers both to the floor. Low Batt goes back at the bottom (or on abort) at once,
  the shutdown SOC once the battery is 5 % above it.
  The bottom is reached when SOC ≤ the floor (`bottom_reason: soc`) or when the battery hasn't
  discharged (≤ 50 W) for 5 minutes at any SOC while the drain profile is in place (`stall`: Deye's
  shutdown voltage, a BMS discharge limit or its own SOC clamp stopped it).
- `switch` **Charge to 100 % now**.
- `button` **Apply mode again** (rewrites the profile, e.g. after a manual change), **Cancel training**.

## Rules

- **HA can't see the inverter → nothing is written.** The profile is queued and written once the SOC
  entity has a value again (or on the 5-min refresh).
- **No fighting manual changes.** A setting changed in the Deye app shows as `manual` with a `diff`;
  only a mode change or *Apply mode again* writes.
- Only differing registers are written, in a few contiguous Modbus writes (function 0x10; Deye ignores
  0x06), then read back. A value the inverter silently rejects is reported (persistent notification,
  `error` attribute) and not retried every 5 minutes.
- TOU stays enabled: Deye sets its enable bit again within minutes of clearing it.
- Training: a definite grid loss during the drain aborts it. The original shutdown SOC is written back
  only once the battery is 5 % above it, so restoring it never shuts the flat down at once. A grid loss
  during the charge marks the measurement invalid. "Full" = SOC ≥ 99 % and |current| ≤ 5 A for 10 min.
- The inverter clock is checked hourly and set when it is more than 60 s off (TOU follows it).

## Options

Day reserve, night window, normal / fast current, peak-shaving limit, training floor, "full" criteria.
New options are written at once.

## Development

```sh
uv sync
uv run pytest
```
