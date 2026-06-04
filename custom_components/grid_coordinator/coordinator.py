"""GridCoordinator — 10-second Voltx battery control loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .budget import build_coordinator_data, compute_voltx_command
from .const import (
    CONF_ENTITY_ENABLED,
    CONF_ENTITY_EV_CHARGER,
    CONF_ENTITY_GRID_POWER,
    CONF_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_VOLTX_CMD,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    CONF_ENTITY_VOLTX_WORK_MODE,
    CONF_EV_CHARGER_THRESHOLD,
    CONF_EXPORT_LIMIT,
    CONF_IMPORT_LIMIT,
    CONF_MON_LOAD_1_HEADROOM,
    CONF_MON_LOAD_1_HOLDOFF_MINUTES,
    CONF_MON_LOAD_1_THRESHOLD,
    CONF_MPC_SIGN_INVERTED,
    CONF_PLAN_STALE_MINUTES,
    CONF_RAMP_STEP,
    CONF_SELF_CONSUMPTION_DEADBAND,
    CONF_SELF_CONSUMPTION_MODE,
    CONF_TRACKING_DEADBAND,
    DEFAULT_EV_CHARGER_THRESHOLD,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_IMPORT_LIMIT,
    DEFAULT_MON_LOAD_1_HEADROOM,
    DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES,
    DEFAULT_MON_LOAD_1_THRESHOLD,
    DEFAULT_MPC_SIGN_INVERTED,
    DEFAULT_OVERRIDE_DURATION_MINUTES,
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_RAMP_STEP,
    DEFAULT_SELF_CONSUMPTION_DEADBAND,
    DEFAULT_SELF_CONSUMPTION_MODE,
    DEFAULT_TRACKING_DEADBAND,
    DOMAIN,
    ENTITY_EV_CHARGER,
    ENTITY_ID_DEFAULTS,
    ENTITY_MON_LOAD_1,
    LOGGER,
    UPDATE_INTERVAL_SECONDS,
    VOLTX_WORK_MODE_CUSTOM,
)
from .models import CoordinatorData, CoordinatorMode

if TYPE_CHECKING:
    from .data import GridCoordinatorConfigEntry


# ── helpers ───────────────────────────────────────────────────────────────────


def _float(hass: HomeAssistant, entity_id: str, default: float) -> float:
    """Read a HA state as float; return default if unavailable or unparseable."""
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown", ""):
        return default
    try:
        return float(state.state)
    except (ValueError, TypeError):
        return default


def _str(hass: HomeAssistant, entity_id: str, default: str = "") -> str:
    state = hass.states.get(entity_id)
    return state.state if state else default


def _plan_age_minutes(hass: HomeAssistant, entity_id: str) -> float:
    """Return minutes since the MPC entity was last updated (inf if missing)."""
    state = hass.states.get(entity_id)
    if state is None:
        return float("inf")
    return (datetime.now(UTC) - state.last_updated).total_seconds() / 60


# ── coordinator ───────────────────────────────────────────────────────────────


class GridCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Controls the Voltx battery to track the EMHASS grid setpoint.

    One instance per config entry.  Runs _async_update_data every
    UPDATE_INTERVAL_SECONDS seconds via DataUpdateCoordinator's scheduler.
    Diagnostic sensor entities subscribe to coordinator updates.
    """

    config_entry: GridCoordinatorConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GridCoordinatorConfigEntry) -> None:
        self._prev_cmd: float = 0.0
        self._cold_start: bool = True
        self._entry = entry
        # Manual override state — all cleared on HA restart or integration reload
        self._override_mode: str | None = None
        self._override_power: float | None = None
        self._override_bypass_soc: bool = False
        self._override_expires: datetime | None = None
        # Monitored load 1 — headroom reservation state
        self._mon_load_1_active: bool = False
        self._mon_load_1_below_since: datetime | None = None
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    # ── config helpers ────────────────────────────────────────────────────────

    def _opt(self, key: str, default):
        """Read from entry options first, then entry data, then supplied default."""
        if key in self._entry.options:
            return self._entry.options[key]
        return self._entry.data.get(key, default)

    def _eid(self, key: str) -> str:
        """Return the configured entity ID for the given CONF_ENTITY_* key."""
        return self._opt(key, ENTITY_ID_DEFAULTS[key])

    @property
    def _import_limit(self) -> float:
        return float(self._opt(CONF_IMPORT_LIMIT, DEFAULT_IMPORT_LIMIT))

    @property
    def _export_limit(self) -> float:
        return float(self._opt(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT))

    @property
    def _ramp_step(self) -> float:
        return float(self._opt(CONF_RAMP_STEP, DEFAULT_RAMP_STEP))

    @property
    def _stale_minutes(self) -> float:
        return float(self._opt(CONF_PLAN_STALE_MINUTES, DEFAULT_PLAN_STALE_MINUTES))

    @property
    def _mpc_sign_inverted(self) -> bool:
        return bool(self._opt(CONF_MPC_SIGN_INVERTED, DEFAULT_MPC_SIGN_INVERTED))

    @property
    def _self_consumption_mode(self) -> str:
        return str(self._opt(CONF_SELF_CONSUMPTION_MODE, DEFAULT_SELF_CONSUMPTION_MODE))

    @property
    def _self_consumption_deadband(self) -> float:
        return float(self._opt(CONF_SELF_CONSUMPTION_DEADBAND, DEFAULT_SELF_CONSUMPTION_DEADBAND))

    @property
    def _tracking_deadband(self) -> float:
        return float(self._opt(CONF_TRACKING_DEADBAND, DEFAULT_TRACKING_DEADBAND))

    # ── feature helpers ───────────────────────────────────────────────────────

    def _ev_adjusted_soc_min(self, hass: HomeAssistant, soc: float, soc_min: float) -> tuple[float, bool]:
        """Return (effective_soc_min, ev_active).

        When the EV charger draws above threshold the SOC floor is raised to
        soc + 1 % so the battery cannot discharge to compensate.  Grid import
        absorbs the EV load naturally up to the configured import limit.
        """
        entity = str(self._opt(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER))
        if not entity:
            return soc_min, False
        threshold = float(self._opt(CONF_EV_CHARGER_THRESHOLD, DEFAULT_EV_CHARGER_THRESHOLD))
        ev_power = _float(hass, entity, 0.0)
        if ev_power > threshold:
            return max(soc_min, soc + 1.0), True
        return soc_min, False

    def _headroom_reserve(self, hass: HomeAssistant) -> float:
        """Return the import headroom (W) to reserve for monitored load 1.

        Headroom is activated as soon as the load exceeds its threshold and is
        held for the configured holdoff period after power drops back below it.
        """
        entity = str(self._opt(CONF_ENTITY_MON_LOAD_1, ENTITY_MON_LOAD_1))
        if not entity:
            return 0.0
        threshold = float(self._opt(CONF_MON_LOAD_1_THRESHOLD, DEFAULT_MON_LOAD_1_THRESHOLD))
        headroom = float(self._opt(CONF_MON_LOAD_1_HEADROOM, DEFAULT_MON_LOAD_1_HEADROOM))
        holdoff_min = float(self._opt(CONF_MON_LOAD_1_HOLDOFF_MINUTES, DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES))

        power = _float(hass, entity, 0.0)
        if power > threshold:
            self._mon_load_1_active = True
            self._mon_load_1_below_since = None
        elif self._mon_load_1_active:
            if self._mon_load_1_below_since is None:
                self._mon_load_1_below_since = datetime.now(UTC)
            elif (datetime.now(UTC) - self._mon_load_1_below_since).total_seconds() / 60 >= holdoff_min:
                self._mon_load_1_active = False
                self._mon_load_1_below_since = None

        return headroom if self._mon_load_1_active else 0.0

    # ── override control ──────────────────────────────────────────────────────

    def set_override(
        self,
        mode: str,
        *,
        power_w: float | None = None,
        duration_minutes: float = DEFAULT_OVERRIDE_DURATION_MINUTES,
        bypass_soc: bool = False,
    ) -> None:
        """Set or clear a manual operating-mode override.

        Call with mode='auto' to cancel any active override and return to
        normal EMHASS tracking.  The override auto-expires after duration_minutes
        and is always lost on HA restart or integration reload.
        """
        if mode == "auto":
            self._override_mode = None
            self._override_power = None
            self._override_bypass_soc = False
            self._override_expires = None
        else:
            self._override_mode = mode
            self._override_power = power_w
            self._override_bypass_soc = bypass_soc
            self._override_expires = datetime.now(UTC) + timedelta(minutes=duration_minutes)
        LOGGER.debug("override set: mode=%s power=%s duration=%.0fmin", mode, power_w, duration_minutes)

    # ── main loop ─────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> CoordinatorData:
        """Called every UPDATE_INTERVAL_SECONDS by the DataUpdateCoordinator."""
        hass = self.hass

        entity_enabled = self._eid(CONF_ENTITY_ENABLED)
        entity_grid = self._eid(CONF_ENTITY_GRID_POWER)
        entity_mpc = self._eid(CONF_ENTITY_MPC_GRID_POWER)
        entity_soc = self._eid(CONF_ENTITY_VOLTX_SOC)
        entity_max_charge = self._eid(CONF_ENTITY_VOLTX_MAX_CHARGE)
        entity_max_discharge = self._eid(CONF_ENTITY_VOLTX_MAX_DISCHARGE)
        entity_soc_min = self._eid(CONF_ENTITY_SOC_MIN)
        entity_soc_max = self._eid(CONF_ENTITY_SOC_MAX)

        # ── cold-start: seed prev_cmd from hardware ─────────────────────────
        # On the first tick after HA starts or the integration is reloaded the
        # inverter may already be running at a non-zero command.  Reading the
        # current entity state avoids an unwanted step-change on the first write.
        if self._cold_start:
            self._cold_start = False
            entity_cmd = self._eid(CONF_ENTITY_VOLTX_CMD)
            self._prev_cmd = _float(hass, entity_cmd, 0.0)
            LOGGER.debug(
                "cold start: seeding prev_cmd=%.0fW from %s",
                self._prev_cmd,
                entity_cmd,
            )

        # ── disabled gate ──────────────────────────────────────────────────
        if _str(hass, entity_enabled, "off") != "on":
            await self._async_enter_self_consumption()
            self._prev_cmd = 0.0
            return build_coordinator_data(
                mode=CoordinatorMode.DISABLED,
                grid_actual=_float(hass, entity_grid, 0.0),
                grid_target=0.0,
                voltx_command=0.0,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                plan_age_minutes=_plan_age_minutes(hass, entity_mpc),
            )

        # ── read grid power (critical — fail fast if unavailable) ──────────
        grid_state = hass.states.get(entity_grid)
        if grid_state is None or grid_state.state in ("unavailable", "unknown"):
            raise UpdateFailed(f"{entity_grid} is unavailable")
        try:
            grid_actual = float(grid_state.state)
        except (ValueError, TypeError) as exc:
            raise UpdateFailed(f"{entity_grid} has non-numeric state") from exc

        # ── manual override ────────────────────────────────────────────────
        # Expire and clear the override if its duration has elapsed.
        if self._override_expires and datetime.now(UTC) >= self._override_expires:
            LOGGER.debug("override expired")
            self.set_override("auto")
        if self._override_mode is not None:
            plan_age = _plan_age_minutes(hass, entity_mpc)
            return await self._async_handle_override(grid_actual, plan_age)

        # ── read EMHASS setpoint ───────────────────────────────────────────
        plan_age = _plan_age_minutes(hass, entity_mpc)
        mpc_raw = _float(hass, entity_mpc, 0.0)
        # Correct for EMHASS injection convention if needed so that both
        # grid_actual and grid_target share the "positive = import" basis.
        grid_target = -mpc_raw if self._mpc_sign_inverted else mpc_raw
        plan_is_stale = plan_age > self._stale_minutes

        # ── self-consumption deadband check ───────────────────────────────
        # When the effective target is within the deadband of zero, hand off to
        # the inverter's native self-consumption mode which reacts at firmware
        # speed. prev_cmd is reset to 0 so the ramp starts clean on exit.
        effective_target = grid_target if not plan_is_stale else 0.0
        if abs(effective_target) <= self._self_consumption_deadband:
            await self._async_enter_self_consumption()
            self._prev_cmd = 0.0
            sc_mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.SELF_CONSUMPTION
            LOGGER.debug(
                "tick | grid=%.0fW target=%.0fW mode=%s age=%.1fmin (self-consumption)",
                grid_actual, grid_target, sc_mode, plan_age,
            )
            return build_coordinator_data(
                mode=sc_mode,
                grid_actual=grid_actual,
                grid_target=effective_target,
                voltx_command=0.0,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                plan_age_minutes=plan_age,
                override_mode=None,
            )

        # ── read battery / inverter state ──────────────────────────────────
        soc = _float(hass, entity_soc, 50.0)
        soc_min = _float(hass, entity_soc_min, 20.0)
        soc_max = _float(hass, entity_soc_max, 95.0)
        max_charge = _float(hass, entity_max_charge, 5000.0)
        max_discharge = _float(hass, entity_max_discharge, 5000.0)

        # ── EV charge awareness ────────────────────────────────────────────
        effective_soc_min, ev_active = self._ev_adjusted_soc_min(hass, soc, soc_min)

        # ── monitored load headroom ────────────────────────────────────────
        headroom_reserve = self._headroom_reserve(hass)

        # ── compute command (pure function, no HA calls) ───────────────────
        command, mode = compute_voltx_command(
            grid_actual=grid_actual,
            grid_target=effective_target,
            prev_cmd=self._prev_cmd,
            soc=soc,
            soc_min=effective_soc_min,
            soc_max=soc_max,
            max_charge=max_charge,
            max_discharge=max_discharge,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            ramp_step=self._ramp_step,
            plan_is_stale=plan_is_stale,
            tracking_deadband=self._tracking_deadband,
            headroom_reserve=headroom_reserve,
        )

        # Remap SOC_FLOOR to EV_CHARGING when the elevated floor was EV-caused.
        if ev_active and mode == CoordinatorMode.SOC_FLOOR and soc > soc_min:
            mode = CoordinatorMode.EV_CHARGING

        # ── write to inverter ──────────────────────────────────────────────
        await self._async_write_voltx(command)
        self._prev_cmd = command

        LOGGER.debug(
            "tick | grid=%.0fW target=%.0fW cmd=%.0fW soc=%.0f%% mode=%s age=%.1fmin ev=%s headroom=%.0fW",
            grid_actual,
            grid_target,
            command,
            soc,
            mode,
            plan_age,
            ev_active,
            headroom_reserve,
        )

        return build_coordinator_data(
            mode=mode,
            grid_actual=grid_actual,
            grid_target=grid_target,
            voltx_command=command,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            plan_age_minutes=plan_age,
            override_mode=None,
        )

    # ── override dispatch ─────────────────────────────────────────────────────

    async def _async_handle_override(
        self, grid_actual: float, plan_age: float
    ) -> CoordinatorData:
        """Execute one tick under the active manual override."""
        hass = self.hass
        mode_str = self._override_mode  # guaranteed non-None by caller

        if mode_str in ("disabled", "self_consume"):
            await self._async_enter_self_consumption()
            self._prev_cmd = 0.0
            coord_mode = (
                CoordinatorMode.OVERRIDE_DISABLED
                if mode_str == "disabled"
                else CoordinatorMode.OVERRIDE_SELF_CONSUME
            )
            LOGGER.debug("tick | override=%s mode=%s", mode_str, coord_mode)
            return build_coordinator_data(
                mode=coord_mode,
                grid_actual=grid_actual,
                grid_target=0.0,
                voltx_command=0.0,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                plan_age_minutes=plan_age,
                override_mode=mode_str,
            )

        # hold_soc / force_charge / force_export — need battery state
        soc = _float(hass, self._eid(CONF_ENTITY_VOLTX_SOC), 50.0)
        soc_min = _float(hass, self._eid(CONF_ENTITY_SOC_MIN), 20.0)
        soc_max = _float(hass, self._eid(CONF_ENTITY_SOC_MAX), 95.0)
        max_charge = _float(hass, self._eid(CONF_ENTITY_VOLTX_MAX_CHARGE), 5000.0)
        max_discharge = _float(hass, self._eid(CONF_ENTITY_VOLTX_MAX_DISCHARGE), 5000.0)

        uncontrolled = grid_actual + self._prev_cmd
        cmd_floor = uncontrolled - self._import_limit   # discharge floor: import safety
        cmd_ceil = uncontrolled + self._export_limit    # charge ceiling: export safety

        if mode_str == "hold_soc":
            # Freeze battery at 0W — grid and solar absorb all loads; battery neither
            # charges nor discharges.  Grid safety still applies: if the hard import or
            # export limit would be breached at 0W, the coordinator adjusts accordingly.
            cmd = max(cmd_floor, min(0.0, cmd_ceil))
            cmd = max(-max_charge, min(max_discharge, cmd))
            coord_mode = CoordinatorMode.OVERRIDE_HOLD_SOC

        elif mode_str == "force_charge":
            target = min(
                self._override_power if self._override_power is not None else max_charge,
                max_charge,
            )
            if soc >= soc_max and not self._override_bypass_soc:
                cmd = 0.0
            else:
                cmd = max(-target, cmd_floor)
                cmd = max(-max_charge, cmd)
            coord_mode = CoordinatorMode.OVERRIDE_FORCE_CHARGE

        else:  # force_export
            target = min(
                self._override_power if self._override_power is not None else max_discharge,
                max_discharge,
            )
            if soc <= soc_min and not self._override_bypass_soc:
                cmd = 0.0
            else:
                cmd = min(target, cmd_ceil)
                cmd = min(max_discharge, cmd)
            coord_mode = CoordinatorMode.OVERRIDE_FORCE_EXPORT

        command = round(cmd)
        await self._async_write_voltx(command)
        self._prev_cmd = command

        LOGGER.debug(
            "tick | override=%s cmd=%.0fW soc=%.0f%%",
            mode_str, command, soc,
        )
        return build_coordinator_data(
            mode=coord_mode,
            grid_actual=grid_actual,
            grid_target=0.0,
            voltx_command=command,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            plan_age_minutes=plan_age,
            override_mode=mode_str,
        )

    # ── inverter write ────────────────────────────────────────────────────────

    async def _async_set_work_mode(self, mode_name: str) -> bool:
        """Switch the inverter work mode if it is not already set.

        Returns True when a switch was actually performed, False when already
        in the target mode or when the entity does not yet exist.
        No-ops silently when the entity is absent so it is safe to call from
        the disabled branch without risking UpdateFailed.
        """
        entity_work_mode = self._eid(CONF_ENTITY_VOLTX_WORK_MODE)
        current = _str(self.hass, entity_work_mode)
        if current and current != mode_name:
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": entity_work_mode, "option": mode_name},
                    blocking=True,
                )
            return True
        return False

    async def _async_enter_self_consumption(self) -> None:
        """Switch to self-consumption mode and zero the power register on transition.

        Zeroing only happens on the tick where the mode actually changes so we
        do not generate unnecessary Modbus traffic every tick.
        """
        if await self._async_set_work_mode(self._self_consumption_mode):
            entity_cmd = self._eid(CONF_ENTITY_VOLTX_CMD)
            if self.hass.states.get(entity_cmd) is not None:
                async with asyncio.timeout(5):
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": entity_cmd, "value": "0"},
                        blocking=True,
                    )

    async def _async_write_voltx(self, command: float) -> None:
        """Ensure Custom work mode then apply the power setpoint."""
        await self._async_set_work_mode(VOLTX_WORK_MODE_CUSTOM)
        async with asyncio.timeout(5):
            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": self._eid(CONF_ENTITY_VOLTX_CMD), "value": str(command)},
                blocking=True,
            )
