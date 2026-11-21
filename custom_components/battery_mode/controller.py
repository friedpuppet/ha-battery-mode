"""The mode controller: profiles → Deye, training cycle, charge-now override, clock sync.

Principles (the user's, shared with grid_load_shedding): if HA can't see the inverter, write
nothing and retry later; don't fight settings changed by hand (show them as «manual» instead).
Writes go out only when the wanted profile changes, on the Apply button, or as a retry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CLOCK_MAX_DRIFT,
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
    ACTUAL_MANUAL,
    ACTUAL_UNKNOWN,
    MODE_MAX,
    MODE_NIGHT,
    MODE_TRAINING,
    MODES,
    PHASE_ABORTED,
    PHASE_CHARGE,
    PHASE_DONE,
    PHASE_DRAIN,
    PHASE_IDLE,
    PROFILE_CHARGE_NOW,
    PROFILE_MAX,
    PROFILE_NIGHT,
    PROFILE_TRAINING_CHARGE,
    PROFILE_TRAINING_DRAIN,
    RESTORE_SHUTDOWN_MARGIN,
    SLOT_POWER,
    STALL_MINUTES,
    STALL_POWER,
    STORAGE_VERSION,
)
from .inverter import DeyeClient, InverterError
from .registers import (
    REG_CLOCK,
    REG_SHUTDOWN_SOC,
    Field,
    Profile,
    Settings,
    decode_clock,
    diff,
    encode_clock,
    mismatches,
    profile_charge_fast,
    profile_drain,
    profile_max,
    profile_night,
    write_runs,
)

_LOGGER = logging.getLogger(__name__)

# Module level so tests can shorten them
SETTLE_SECONDS = 3.0  # between a write and its read-back
RETRY_SECONDS = 10.0
WRITE_ATTEMPTS = 3
TICK = timedelta(seconds=30)  # time-based checks (full, stall) and energy integration
REFRESH = timedelta(minutes=5)  # re-read the inverter for the actual mode
CLOCK_SYNC = timedelta(hours=1)
MAX_SAMPLE_GAP = 120  # s; a longer gap in the charge integration is skipped


class Controller:
    """One per config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.client = DeyeClient(hass, entry.data[CONF_DEVICE])
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._notification_id = f"{DOMAIN}_{entry.entry_id}"

        # persisted
        self.mode: str = MODE_MAX
        self.charge_now = False
        self.pending = False  # a profile is waiting for the inverter to be reachable
        self.training: dict[str, Any] = {"phase": PHASE_IDLE}
        self.restore_shutdown: int | None = None  # original shutdown SOC, to be written back

        # runtime
        self.registers: dict[int, int] | None = None
        self.actual: str = ACTUAL_UNKNOWN
        self.actual_diff: dict[str, Any] = {}
        self.last_read: datetime | None = None
        self.last_applied: datetime | None = None
        self.error: str | None = None
        self.clock_drift: float | None = None
        self._full_since: datetime | None = None
        self._stall_since: datetime | None = None
        self._sample: tuple[datetime, float | None, float | None] | None = None
        self._restoring = False
        self._applying = False
        self._dirty = False

    # ---- settings and inputs -------------------------------------------------------------

    def option(self, key: str) -> Any:
        return self.entry.options.get(key, DEFAULTS[key])

    @property
    def settings(self) -> Settings:
        return Settings(
            night_start=self.option(CONF_NIGHT_START),
            night_end=self.option(CONF_NIGHT_END),
            reserve=int(self.option(CONF_RESERVE)),
            floor=int(self.option(CONF_FLOOR)),
            normal_current=int(self.option(CONF_NORMAL_CURRENT)),
            fast_current=int(self.option(CONF_FAST_CURRENT)),
            peak_limit=int(self.option(CONF_PEAK_LIMIT)),
            slot_power=SLOT_POWER,
        )

    def _number(self, key: str) -> float | None:
        entity_id = self.entry.data.get(key)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    @property
    def soc(self) -> float | None:
        return self._number(CONF_SOC_ENTITY)

    @property
    def visible(self) -> bool:
        """HA sees the inverter (its SOC entity has a value)."""
        return self.soc is not None

    def _grid_off(self) -> bool:
        """True only for a definite «off»; unknown/unavailable is not a grid loss."""
        state = self.hass.states.get(self.entry.data[CONF_GRID_ENTITY])
        return state is not None and state.state == STATE_OFF

    # ---- listeners -----------------------------------------------------------------------

    @callback
    def add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # ---- lifecycle -----------------------------------------------------------------------

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        if data.get("mode") in MODES:
            self.mode = data["mode"]
        self.charge_now = bool(data.get("charge_now", False))
        self.pending = bool(data.get("pending", False))
        self.training = data.get("training") or {"phase": PHASE_IDLE}
        self.restore_shutdown = data.get("restore_shutdown")

        entities = [
            self.entry.data[k]
            for k in (CONF_SOC_ENTITY, CONF_CURRENT_ENTITY, CONF_POWER_ENTITY, CONF_GRID_ENTITY)
            if self.entry.data.get(k)
        ]
        self._unsubs += [
            async_track_state_change_event(self.hass, entities, self._on_state),
            async_track_time_interval(self.hass, self._on_tick, TICK),
            async_track_time_interval(self.hass, self._on_refresh, REFRESH),
            async_track_time_interval(self.hass, self._on_clock, CLOCK_SYNC),
            async_at_started(self.hass, self._on_started),
        ]

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    def _data(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "charge_now": self.charge_now,
            "pending": self.pending,
            "training": self.training,
            "restore_shutdown": self.restore_shutdown,
        }

    @callback
    def _save(self, delay: float = 1) -> None:
        self._store.async_delay_save(self._data, delay)

    def _background(self, coro, name: str) -> None:
        self.entry.async_create_background_task(self.hass, coro, f"{DOMAIN} {name}")

    async def _on_started(self, _hass: HomeAssistant) -> None:
        await self.async_refresh()
        await self.async_sync_clock()

    async def _on_refresh(self, _now: datetime) -> None:
        if self.mode == MODE_TRAINING:
            self._save()  # the charge counters
        await self.async_refresh()

    async def _on_clock(self, _now: datetime) -> None:
        await self.async_sync_clock()

    # ---- profiles ------------------------------------------------------------------------

    def wanted_profile(self) -> tuple[str, Profile]:
        settings = self.settings
        if self.mode == MODE_TRAINING:
            if self.training.get("phase") == PHASE_DRAIN:
                profile = profile_drain(settings)
                profile[REG_SHUTDOWN_SOC] = Field(settings.floor)
                return PROFILE_TRAINING_DRAIN, profile
            return PROFILE_TRAINING_CHARGE, profile_charge_fast(settings)
        if self.charge_now:
            return PROFILE_CHARGE_NOW, profile_charge_fast(settings)
        if self.mode == MODE_NIGHT:
            return PROFILE_NIGHT, profile_night(settings)
        return PROFILE_MAX, profile_max(settings)

    @callback
    def _set_registers(self, registers: dict[int, int]) -> None:
        self.registers = registers
        self.last_read = dt_util.utcnow()
        key, profile = self.wanted_profile()
        self.actual_diff = mismatches(profile, registers)
        if not self.actual_diff:
            self.actual = key
            return
        settings = self.settings
        for name, candidate in (
            (PROFILE_MAX, profile_max(settings)),
            (PROFILE_NIGHT, profile_night(settings)),
            (PROFILE_CHARGE_NOW, profile_charge_fast(settings)),
            (PROFILE_TRAINING_DRAIN, profile_drain(settings)),
        ):
            if not diff(candidate, registers):
                self.actual = name
                return
        self.actual = ACTUAL_MANUAL

    # ---- inverter I/O --------------------------------------------------------------------

    async def async_refresh(self) -> None:
        """Re-read the inverter; retry a pending profile; write back the shutdown SOC if due."""
        if not self.visible:
            self.actual = ACTUAL_UNKNOWN
            self._notify()
            return
        if self.pending:
            await self.async_apply()
        else:
            async with self._lock:
                try:
                    self._set_registers(await self.client.read_all())
                except InverterError as err:
                    _LOGGER.warning("Reading the inverter failed: %s", err)
            self._notify()
        await self._maybe_restore_shutdown()

    async def async_apply(self) -> bool:
        """Write the wanted profile (only what differs), read back, verify."""
        async with self._lock:
            if not self.visible:
                _LOGGER.info("Inverter not visible; profile queued")
                self.pending = True
                self.actual = ACTUAL_UNKNOWN
                self._save()
                self._notify()
                return False
            key, profile = self.wanted_profile()
            error = "unknown"
            comm_failure = False
            for attempt in range(WRITE_ATTEMPTS):
                if attempt:
                    await asyncio.sleep(RETRY_SECONDS)
                try:
                    registers = await self.client.read_all()
                    changes = diff(profile, registers)
                    if changes:
                        _LOGGER.info("Applying %s: %s", key, mismatches(profile, registers))
                        for start, values in write_runs(changes, registers):
                            await self.client.write(start, values)
                        await asyncio.sleep(SETTLE_SECONDS)
                        registers = await self.client.read_all()
                    self._set_registers(registers)
                    if not diff(profile, registers):
                        self.pending = False
                        self.error = None
                        self.last_applied = dt_util.utcnow()
                        persistent_notification.async_dismiss(self.hass, self._notification_id)
                        self._save()
                        self._notify()
                        return True
                    error = f"the inverter didn't accept: {mismatches(profile, registers)}"
                    comm_failure = False
                except InverterError as err:
                    error = str(err)
                    comm_failure = True
                _LOGGER.warning("Applying %s, attempt %d: %s", key, attempt + 1, error)
            # A communication failure is retried on the next refresh; a rejected value is not,
            # so a value the inverter refuses doesn't cost a flash write every 5 minutes.
            self.pending = comm_failure
            self.error = error
            persistent_notification.async_create(
                self.hass,
                f"Не вдалося записати режим «{key}» в інвертор: {error}",
                title="Режим батареї",
                notification_id=self._notification_id,
            )
            self._save()
            self._notify()
            return False

    async def _maybe_restore_shutdown(self) -> None:
        """Write the original shutdown SOC back once the battery is safely above it."""
        target = self.restore_shutdown
        soc = self.soc
        if target is None or soc is None or self._restoring or soc < target + RESTORE_SHUTDOWN_MARGIN:
            return
        if self.mode == MODE_TRAINING and self.training.get("phase") == PHASE_DRAIN:
            return
        self._restoring = True
        try:
            async with self._lock:
                try:
                    current = (await self.client.read(REG_SHUTDOWN_SOC, 1))[REG_SHUTDOWN_SOC]
                    if current != target:
                        await self.client.write(REG_SHUTDOWN_SOC, [target])
                        await asyncio.sleep(SETTLE_SECONDS)
                        current = (await self.client.read(REG_SHUTDOWN_SOC, 1))[REG_SHUTDOWN_SOC]
                except InverterError as err:
                    _LOGGER.warning("Restoring the shutdown SOC failed: %s", err)
                    return
                if current == target:
                    _LOGGER.info("Shutdown SOC restored to %s %%", target)
                    self.restore_shutdown = None
                    self._save()
                else:
                    _LOGGER.warning("The inverter kept shutdown SOC %s instead of %s", current, target)
        finally:
            self._restoring = False
        self._notify()

    async def async_sync_clock(self) -> None:
        if not self.visible:
            return
        async with self._lock:
            try:
                values = await self.client.read(REG_CLOCK, 3)
                inverter = decode_clock([values[REG_CLOCK + i] for i in range(3)], dt_util.get_default_time_zone())
                self.clock_drift = round((inverter - dt_util.now()).total_seconds(), 1)
                if abs(self.clock_drift) > CLOCK_MAX_DRIFT:
                    _LOGGER.info("Inverter clock is %s s off, setting it", self.clock_drift)
                    await self.client.write(REG_CLOCK, encode_clock(dt_util.now()))
                    await asyncio.sleep(SETTLE_SECONDS)
                    values = await self.client.read(REG_CLOCK, 3)
                    inverter = decode_clock(
                        [values[REG_CLOCK + i] for i in range(3)], dt_util.get_default_time_zone()
                    )
                    self.clock_drift = round((inverter - dt_util.now()).total_seconds(), 1)
            except (InverterError, ValueError) as err:
                _LOGGER.warning("Inverter clock sync failed: %s", err)
        self._notify()

    # ---- user actions --------------------------------------------------------------------

    async def async_set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise HomeAssistantError(f"unknown mode {mode}")
        if mode == self.mode:
            return
        if mode == MODE_TRAINING:
            await self._start_training()
            return
        if self.mode == MODE_TRAINING:
            self._end_training(PHASE_ABORTED, "cancelled")
        self.mode = mode
        self.charge_now = False
        self._save()
        self._notify()
        await self.async_apply()
        await self._maybe_restore_shutdown()

    async def async_set_charge_now(self, on: bool) -> None:
        if on and self.mode == MODE_TRAINING:
            raise HomeAssistantError("Під час тренування заряд і так іде до 100 %")
        if on == self.charge_now:
            return
        self.charge_now = on
        self._full_since = None
        self._save()
        self._notify()
        await self.async_apply()

    async def async_cancel_training(self) -> None:
        if self.mode != MODE_TRAINING:
            return
        self._end_training(PHASE_ABORTED, "cancelled")
        self._save()
        self._notify()
        await self.async_apply()
        await self._maybe_restore_shutdown()

    async def _start_training(self) -> None:
        if not self.visible:
            raise HomeAssistantError("HA не бачить інвертор")
        if self._grid_off():
            raise HomeAssistantError("Немає мережі: тренування починається лише з мережею")
        if self.restore_shutdown is not None:
            original = self.restore_shutdown  # the register may still hold the last floor
        else:
            try:
                async with self._lock:
                    original = (await self.client.read(REG_SHUTDOWN_SOC, 1))[REG_SHUTDOWN_SOC]
            except InverterError as err:
                raise HomeAssistantError(f"Не вдалося прочитати shutdown SOC: {err}") from err
        self.restore_shutdown = None
        self.training = {
            "phase": PHASE_DRAIN,
            "prev_mode": self.mode,
            "original_shutdown_soc": original,
            "started": dt_util.utcnow().isoformat(),
            "start_soc": self.soc,
            "bottom_at": None,
            "bottom_soc": None,
            "finished": None,
            "charged_ah": None,
            "charged_kwh": None,
            "valid": True,
            "reason": None,
        }
        self.mode = MODE_TRAINING
        self.charge_now = False
        self._stall_since = None
        self._save()
        self._notify()
        await self.async_apply()

    @callback
    def _end_training(self, phase: str, reason: str | None) -> None:
        training = self.training
        if training.get("phase") == PHASE_DRAIN:
            # the shutdown SOC is at the floor; in the charge phase restore_shutdown is already set
            # (or done)
            self.restore_shutdown = training.get("original_shutdown_soc")
        training["phase"] = phase
        training["finished"] = dt_util.utcnow().isoformat()
        if reason and not training.get("reason"):
            training["reason"] = reason
        self.mode = training.get("prev_mode", MODE_MAX)
        self._sample = None
        _LOGGER.info("Training %s (%s); back to %s", phase, reason, self.mode)

    # ---- state machine -------------------------------------------------------------------

    @callback
    def _on_state(self, _event: Event[EventStateChangedData]) -> None:
        self._evaluate()

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._evaluate()
        if self._dirty:
            self._dirty = False
            self._notify()

    @callback
    def _is_full(self, now: datetime, soc: float, current: float | None) -> bool:
        if soc >= self.option(CONF_FULL_SOC) and current is not None and abs(current) <= self.option(CONF_FULL_CURRENT):
            if self._full_since is None:
                self._full_since = now
            return now - self._full_since >= timedelta(minutes=self.option(CONF_FULL_MINUTES))
        self._full_since = None
        return False

    @callback
    def _integrate(self, now: datetime, current: float | None, power: float | None) -> None:
        """Charged Ah / kWh since the bottom (Deye sign: negative = charging)."""
        training = self.training
        if self._sample is not None:
            then, last_current, last_power = self._sample
            dt = (now - then).total_seconds()
            if 0 < dt <= MAX_SAMPLE_GAP:
                if last_current is not None:
                    training["charged_ah"] = round((training.get("charged_ah") or 0) - last_current * dt / 3600, 3)
                if last_power is not None:
                    training["charged_kwh"] = round((training.get("charged_kwh") or 0) - last_power * dt / 3.6e6, 4)
            elif dt > MAX_SAMPLE_GAP:
                training["gaps"] = training.get("gaps", 0) + 1
        self._sample = (now, current, power)

    @callback
    def _evaluate(self) -> None:
        soc = self.soc
        if soc is None:
            return  # blind: do nothing
        now = dt_util.utcnow()
        current = self._number(CONF_CURRENT_ENTITY)
        power = self._number(CONF_POWER_ENTITY)
        changed = False
        apply = False

        if self.mode == MODE_TRAINING:
            training = self.training
            floor = self.option(CONF_FLOOR)
            if training.get("phase") == PHASE_DRAIN:
                if self._grid_off():
                    self._end_training(PHASE_ABORTED, "grid_lost")
                    changed = apply = True
                elif (reason := "soc" if soc <= floor else "stall" if self._stalled(now, power) else None):
                    training.update(
                        phase=PHASE_CHARGE,
                        bottom_at=now.isoformat(),
                        bottom_soc=soc,
                        bottom_reason=reason,
                        charged_ah=0.0,
                        charged_kwh=0.0,
                    )
                    # back to the original shutdown SOC once the charge is safely above it
                    self.restore_shutdown = training.get("original_shutdown_soc")
                    self._sample = (now, current, power)
                    self._full_since = None
                    _LOGGER.info("Training: bottom at %s %% (%s), charging", soc, reason)
                    changed = apply = True
            elif training.get("phase") == PHASE_CHARGE:
                self._integrate(now, current, power)
                if self._grid_off() and training.get("valid", True):
                    training["valid"] = False
                    training["reason"] = "grid_lost"
                    changed = True
                if self._is_full(now, soc, current):
                    self._end_training(PHASE_DONE, None)
                    changed = apply = True
                else:
                    # the counters change every sample: entities on the tick, storage on the refresh
                    self._dirty = True
        elif self.charge_now and self._is_full(now, soc, current):
            _LOGGER.info("Charge now: full")
            self.charge_now = False
            changed = apply = True

        if changed:
            self._save()
            self._notify()
        if apply or (self.pending and not self._applying):
            self._background(self._apply_then_restore(), "apply")
        elif self.restore_shutdown is not None and not self._restoring:
            if soc >= self.restore_shutdown + RESTORE_SHUTDOWN_MARGIN:
                self._background(self._maybe_restore_shutdown(), "restore shutdown SOC")

    async def _apply_then_restore(self) -> None:
        self._applying = True
        try:
            await self.async_apply()
        finally:
            self._applying = False
        await self._maybe_restore_shutdown()

    @callback
    def _stalled(self, now: datetime, power: float | None) -> bool:
        """Deye stopped discharging at any SOC while the drain profile is in place.

        With every slot at the floor and grid charge off, a battery that doesn't discharge means the
        inverter or the BMS hit a limit of its own: shutdown voltage, a BMS discharge limit, its own
        SOC clamp. Whatever it was, that is the bottom.
        """
        if (
            self.actual == PROFILE_TRAINING_DRAIN
            and not self.pending
            and power is not None
            and power <= STALL_POWER
        ):
            if self._stall_since is None:
                self._stall_since = now
            return now - self._stall_since >= timedelta(minutes=STALL_MINUTES)
        self._stall_since = None
        return False
