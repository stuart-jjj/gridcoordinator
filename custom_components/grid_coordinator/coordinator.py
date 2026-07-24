"""GridCoordinator — 10-second Voltx battery control loop."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from statistics import pstdev
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .budget import (
    SOLAX_RESIDUAL_MODES,
    build_coordinator_data,
    compute_ev_current_limit,
    compute_solax_command,
    compute_solax_share,
    compute_solax_tier1,
    compute_voltx_command,
)
from .const import (
    CONF_ENTITY_ENABLED,
    CONF_ENTITY_EV_CHARGE_CURRENT,
    CONF_ENTITY_EV_CHARGER,
    CONF_ENTITY_GRID_POWER,
    CONF_ENTITY_GRID_PRIORITY,
    CONF_ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_CONTROL_ENABLE,
    CONF_ENTITY_SOLAX_EXPORT_DURATION,
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_TRIGGER,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_VOLTX_CAPACITY,
    CONF_ENTITY_VOLTX_CMD,
    CONF_ENTITY_VOLTX_CONTROL_ENABLE,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    CONF_ENTITY_VOLTX_WORK_MODE,
    CONF_EV_CHARGER_THRESHOLD,
    CONF_EV_EMERGENCY_THROTTLE,
    CONF_EV_HEADROOM,
    CONF_EV_MAX_CHARGE_CURRENT,
    CONF_EV_MIN_CHARGE_CURRENT,
    CONF_EV_RELEASE_HOLDOFF_MINUTES,
    CONF_EV_RELEASE_RAMP_STEP,
    CONF_EV_WATTS_PER_AMP,
    CONF_EXPORT_LIMIT,
    CONF_GRID_PRIORITY_BAND,
    CONF_IMPORT_LIMIT,
    CONF_MON_LOAD_1_HEADROOM,
    CONF_MON_LOAD_1_HOLDOFF_MINUTES,
    CONF_MON_LOAD_1_THRESHOLD,
    CONF_MPC_BATT_SIGN_INVERTED,
    CONF_MPC_SIGN_INVERTED,
    CONF_PLAN_STALE_MINUTES,
    CONF_RAMP_STEP,
    CONF_SELF_CONSUMPTION_DEADBAND,
    CONF_SELF_CONSUMPTION_MODE,
    CONF_SOC_BALANCE_DEADBAND,
    CONF_SOC_BALANCE_SENSITIVITY,
    CONF_SOLAX_CMD_DEADBAND,
    CONF_SOLAX_ZERO_DEADBAND,
    CONF_SOLAX_MAX_CHARGE,
    CONF_SOLAX_MAX_DISCHARGE,
    CONF_TIER2_GAIN,
    CONF_TRACKING_DEADBAND,
    CONF_TRANSIENT_DISCHARGE_RAMP_STEP,
    CONF_TRANSIENT_EMA_ALPHA,
    CONF_TRANSIENT_VARIANCE_THRESHOLD,
    CONF_TRANSIENT_VARIANCE_WINDOW,
    DEFAULT_EV_CHARGER_THRESHOLD,
    DEFAULT_EV_EMERGENCY_THROTTLE,
    DEFAULT_EV_HEADROOM,
    DEFAULT_EV_MAX_CHARGE_CURRENT,
    DEFAULT_EV_MIN_CHARGE_CURRENT,
    DEFAULT_EV_RELEASE_HOLDOFF_MINUTES,
    DEFAULT_EV_RELEASE_RAMP_STEP,
    DEFAULT_EV_WATTS_PER_AMP,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_GRID_PRIORITY_BAND,
    DEFAULT_IMPORT_LIMIT,
    DEFAULT_MON_LOAD_1_HEADROOM,
    DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES,
    DEFAULT_MON_LOAD_1_THRESHOLD,
    DEFAULT_MPC_BATT_SIGN_INVERTED,
    DEFAULT_MPC_SIGN_INVERTED,
    DEFAULT_OVERRIDE_DURATION_MINUTES,
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_RAMP_STEP,
    DEFAULT_SELF_CONSUMPTION_DEADBAND,
    DEFAULT_SELF_CONSUMPTION_MODE,
    DEFAULT_SOC_BALANCE_DEADBAND,
    DEFAULT_SOC_BALANCE_SENSITIVITY,
    DEFAULT_SOLAX_AUTOREPEAT_DURATION,
    DEFAULT_SOLAX_CMD_DEADBAND,
    DEFAULT_SOLAX_ZERO_DEADBAND,
    DEFAULT_SOLAX_TIER1_SOC_TAPER_BAND,
    DEFAULT_SOLAX_MAX_CHARGE,
    DEFAULT_SOLAX_MAX_DISCHARGE,
    DEFAULT_TIER2_GAIN,
    DEFAULT_TRACKING_DEADBAND,
    DEFAULT_TRANSIENT_DISCHARGE_RAMP_STEP,
    DEFAULT_TRANSIENT_EMA_ALPHA,
    DEFAULT_TRANSIENT_VARIANCE_THRESHOLD,
    DEFAULT_TRANSIENT_VARIANCE_WINDOW,
    DOMAIN,
    ENTITY_EV_CHARGE_CURRENT,
    ENTITY_EV_CHARGER,
    ENTITY_ID_DEFAULTS,
    ENTITY_MON_LOAD_1,
    EV_RELEASE_MARGIN,
    LOGGER,
    SOLAX_EXPORT_DURATION_SAFE,
    SOLAX_RC_MODE_DISABLED,
    SOLAX_RC_MODE_ENABLED,
    UPDATE_INTERVAL_SECONDS,
    VOLTX_WORK_MODE_CUSTOM,
)
from .models import CoordinatorData, CoordinatorMode, SolaxMode

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


def _float_or_entity(hass: HomeAssistant, entity_id_or_value: str, default: float) -> float:
    """Return the value directly if the string is numeric, otherwise read as an entity state."""
    try:
        return float(entity_id_or_value)
    except (ValueError, TypeError):
        return _float(hass, entity_id_or_value, default)


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
        # Solax priority-2 state
        self._solax_active: bool = False  # True when coordinator is commanding Solax
        self._solax_last_written_cmd: float = 0.0  # last power setpoint written to inverter
        # EV emergency charge-current throttle state (layer-3 backstop)
        self._ev_throttle_active: bool = False  # True while a current cap is being asserted
        self._ev_current_limit: float = float(self._opt(CONF_EV_MAX_CHARGE_CURRENT, DEFAULT_EV_MAX_CHARGE_CURRENT))
        self._ev_recovered_since: datetime | None = None  # grid-below-ceiling timer for release holdoff
        # Transient (high grid-variance) damping state
        window = max(3, int(self._opt(CONF_TRANSIENT_VARIANCE_WINDOW, DEFAULT_TRANSIENT_VARIANCE_WINDOW)))
        self._grid_history: deque[float] = deque(maxlen=window)
        self._grid_ema: float = 0.0
        self._grid_ema_seeded: bool = False
        # Entities already warned about, so a missing control input logs once not 6×/min
        self._warned_missing: set[str] = set()
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    # ── config helpers ────────────────────────────────────────────────────────

    def _float_control(self, entity_id: str, default: float) -> float:
        """Read a control input as float, warning once if it cannot be read.

        Unlike the silent _float fallback, control inputs (the EMHASS setpoints)
        make the controller meaningfully wrong when absent — e.g. a missing
        battery setpoint entity silently degrades tier 1 to zero. Warn on the
        first failed read per entity; reset when the entity recovers.
        """
        state = self.hass.states.get(entity_id)
        bad = state is None or state.state in ("unavailable", "unknown", "")
        value: float | None = None
        if not bad:
            try:
                value = float(state.state)
            except (ValueError, TypeError):
                bad = True
        if bad:
            if entity_id not in self._warned_missing:
                self._warned_missing.add(entity_id)
                LOGGER.warning(
                    "control input %s is missing/unreadable (state=%s); "
                    "falling back to %.0f — check the configured entity id",
                    entity_id,
                    state.state if state else None,
                    default,
                )
            return default
        if entity_id in self._warned_missing:
            self._warned_missing.discard(entity_id)
            LOGGER.warning("control input %s is readable again", entity_id)
        return value

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

    @property
    def _tier2_gain(self) -> float:
        return float(self._opt(CONF_TIER2_GAIN, DEFAULT_TIER2_GAIN))

    @property
    def _soc_balance_sensitivity(self) -> float:
        return float(self._opt(CONF_SOC_BALANCE_SENSITIVITY, DEFAULT_SOC_BALANCE_SENSITIVITY))

    @property
    def _soc_balance_deadband(self) -> float:
        return float(self._opt(CONF_SOC_BALANCE_DEADBAND, DEFAULT_SOC_BALANCE_DEADBAND))

    @property
    def _transient_variance_threshold(self) -> float:
        return float(self._opt(CONF_TRANSIENT_VARIANCE_THRESHOLD, DEFAULT_TRANSIENT_VARIANCE_THRESHOLD))

    @property
    def _transient_variance_window(self) -> int:
        return max(3, int(self._opt(CONF_TRANSIENT_VARIANCE_WINDOW, DEFAULT_TRANSIENT_VARIANCE_WINDOW)))

    @property
    def _transient_ema_alpha(self) -> float:
        return float(self._opt(CONF_TRANSIENT_EMA_ALPHA, DEFAULT_TRANSIENT_EMA_ALPHA))

    @property
    def _transient_discharge_ramp_step(self) -> float:
        return float(self._opt(CONF_TRANSIENT_DISCHARGE_RAMP_STEP, DEFAULT_TRANSIENT_DISCHARGE_RAMP_STEP))

    # ── feature helpers ───────────────────────────────────────────────────────

    def _ev_headroom_reserve(self, hass: HomeAssistant) -> tuple[float, bool]:
        """Return (ev_headroom_reserve, ev_active).

        When the EV charger draws above threshold a block of import headroom
        (ev_headroom, default 3 kW) is reserved beneath the import ceiling.  Both
        batteries follow the EMHASS plan normally while projected grid import stays
        below (import_limit − ev_headroom); as import nears that tightened ceiling
        they ramp down charging and, if required, discharge to hold it — keeping the
        reserve free for the externally-controlled (Amber) EV draw, which has no
        visibility of grid limits.  The reserve is enforced via the same cmd_floor
        tightening as the monitored-load headroom (see compute_voltx_command /
        compute_solax_tier1); the battery is no longer SOC-floored during EV charging.
        """
        entity = str(self._opt(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER))
        if not entity:
            return 0.0, False
        threshold = float(self._opt(CONF_EV_CHARGER_THRESHOLD, DEFAULT_EV_CHARGER_THRESHOLD))
        ev_power = _float(hass, entity, 0.0)
        if ev_power > threshold:
            return float(self._opt(CONF_EV_HEADROOM, DEFAULT_EV_HEADROOM)), True
        return 0.0, False

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

    def _grid_priority_active(self, hass: HomeAssistant, grid_target: float) -> bool:
        """Return True when Voltx should use deadbeat grid tracking this tick.

        Engages when the grid target is within grid_priority_band of zero (band=0
        disables the auto-trigger) OR when the configured grid-priority entity —
        a manual input_boolean or a price-derived template boolean — is truthy.
        Accepts "on", "true", "yes", "1" (case-insensitive) to support both
        binary_sensor and sensor template entities.
        """
        band = float(self._opt(CONF_GRID_PRIORITY_BAND, DEFAULT_GRID_PRIORITY_BAND))
        if band > 0 and abs(grid_target) <= band:
            return True
        entity = str(self._opt(CONF_ENTITY_GRID_PRIORITY, "")).strip()
        return bool(entity) and _str(hass, entity, "off").lower() in ("on", "true", "yes", "1")

    def _control_enabled(self, hass: HomeAssistant, key: str) -> bool:
        """Return True when the per-battery control-enable helper permits control.

        The helper is optional: when unconfigured (blank) control is ON by default.
        When configured, control is ON while the entity reads truthy (on/true/yes/1),
        matching the grid-priority trigger parsing.  A configured-but-unavailable helper
        defaults to ON (fail-safe: keep controlling) so a flaky helper never silently
        strands a battery — the global enable gate remains the way to stop everything.
        """
        entity = str(self._opt(key, "")).strip()
        if not entity:
            return True
        return _str(hass, entity, "on").lower() in ("on", "true", "yes", "1")

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

        # ── disabled gate ──────────────────────────────────────────────────
        if _str(hass, entity_enabled, "off") != "on":
            await self._async_enter_self_consumption()
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
            await self._async_release_ev_throttle()
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
        # Sensor staleness matters for diagnosis: a laggy grid sensor makes the
        # tier-2 correction act on old data.
        grid_age_s = (datetime.now(UTC) - grid_state.last_updated).total_seconds()

        # On the first successful tick after HA starts or the integration is
        # reloaded the inverter may already be running at a non-zero command.
        # Reading the current entity state avoids an unwanted step-change on the
        # first write.  Runs after the grid-sensor gate so failed startup ticks
        # (grid sensor not loaded yet) don't consume the cold start while the
        # inverter entities are likely also still unavailable.
        if self._cold_start:
            self._cold_start = False
            entity_cmd = self._eid(CONF_ENTITY_VOLTX_CMD)
            self._prev_cmd = _float(hass, entity_cmd, 0.0)
            LOGGER.debug(
                "cold start: seeding prev_cmd=%.0fW from %s",
                self._prev_cmd,
                entity_cmd,
            )

        # ── transient (high grid-variance) detection ───────────────────────
        # Detect a rapidly fluctuating load (e.g. oven/cooktop thermostatic
        # cycling) from the rolling standard deviation of the grid sensor. When
        # engaged, compute_voltx_command tracks a smoothed grid value and ramps
        # discharge up slowly so the battery stops chasing the fast cycling and
        # over-discharging into export. Threshold 0 disables the feature.
        alpha = self._transient_ema_alpha
        if not self._grid_ema_seeded:
            self._grid_ema = grid_actual
            self._grid_ema_seeded = True
        else:
            self._grid_ema = alpha * grid_actual + (1.0 - alpha) * self._grid_ema
        window = self._transient_variance_window
        if self._grid_history.maxlen != window:
            # Window reconfigured via options without a restart — rebuild, keeping recent samples.
            self._grid_history = deque(self._grid_history, maxlen=window)
        self._grid_history.append(grid_actual)
        variance_threshold = self._transient_variance_threshold
        grid_stdev = pstdev(self._grid_history) if len(self._grid_history) >= 3 else 0.0
        transient_active = variance_threshold > 0 and grid_stdev > variance_threshold
        # Mirrors the grid_track fallback inside compute_voltx_command exactly, so the
        # tier-2 error used to size Solax's share matches what Voltx itself tracks.
        grid_track = self._grid_ema if (transient_active and self._grid_ema is not None) else grid_actual

        # ── manual override ────────────────────────────────────────────────
        # Expire and clear the override if its duration has elapsed.
        if self._override_expires and datetime.now(UTC) >= self._override_expires:
            LOGGER.debug("override expired")
            self.set_override("auto")
        if self._override_mode is not None:
            plan_age = _plan_age_minutes(hass, entity_mpc)
            await self._async_release_ev_throttle()
            return await self._async_handle_override(grid_actual, plan_age)

        # ── read EMHASS setpoints ──────────────────────────────────────────
        plan_age = _plan_age_minutes(hass, entity_mpc)
        mpc_raw = self._float_control(entity_mpc, 0.0)
        grid_target = -mpc_raw if self._mpc_sign_inverted else mpc_raw

        mpc_batt_raw = self._float_control(self._eid(CONF_ENTITY_MPC_BATT_POWER), 0.0)
        mpc_batt_sign_inv = bool(self._opt(CONF_MPC_BATT_SIGN_INVERTED, DEFAULT_MPC_BATT_SIGN_INVERTED))
        mpc_batt_cmd = -mpc_batt_raw if mpc_batt_sign_inv else mpc_batt_raw

        plan_is_stale = plan_age > self._stale_minutes

        # ── self-consumption deadband check ───────────────────────────────
        # When the effective target is within the deadband of zero, hand off to
        # the inverter's native self-consumption mode which reacts at firmware
        # speed. prev_cmd is reset to 0 so the ramp starts clean on exit.
        # Both the grid target and battery setpoint are zeroed for a stale plan
        # so neither leaks into the controller if the deadband is ever set to 0.
        effective_target = grid_target if not plan_is_stale else 0.0
        effective_mpc_batt = mpc_batt_cmd if not plan_is_stale else 0.0
        if abs(effective_target) <= self._self_consumption_deadband and abs(effective_mpc_batt) <= self._self_consumption_deadband:
            await self._async_enter_self_consumption()
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
            await self._async_release_ev_throttle()
            self._prev_cmd = 0.0
            sc_mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.SELF_CONSUMPTION
            LOGGER.debug(
                "tick | grid=%.0fW (age=%.0fs) target=%.0fW mpc_batt=%.0fW mode=%s "
                "plan_age=%.1fmin (self-consumption)",
                grid_actual, grid_age_s, grid_target, mpc_batt_cmd, sc_mode, plan_age,
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

        # ── EV charge awareness + monitored load headroom ──────────────────
        # Both reserve a block of import headroom by tightening the effective import
        # limit.  Take the larger reserve when both are active (protects the worst
        # case without double-counting).  ev_active is tracked separately so the mode
        # can be reported as ev_charging when the EV reserve is the binding one.
        ev_reserve, ev_active = self._ev_headroom_reserve(hass)
        mon_load_reserve = self._headroom_reserve(hass)
        headroom_reserve = max(ev_reserve, mon_load_reserve)

        # ── per-battery control switches ───────────────────────────────────
        # Each battery's control can be turned off with an optional binary helper
        # (blank helper → control on by default).  A battery whose control is off is
        # released to native self-consumption and never commanded this tick.
        voltx_control = self._control_enabled(hass, CONF_ENTITY_VOLTX_CONTROL_ENABLE)
        solax_on = self._solax_enabled() and self._control_enabled(
            hass, CONF_ENTITY_SOLAX_CONTROL_ENABLE
        )
        if not voltx_control:
            # Voltx (primary) control off: release it and, if Solax control is on, promote
            # Solax to sole grid tracker running the full 2-tier controller on its own params.
            return await self._async_voltx_disabled_tick(
                grid_actual=grid_actual,
                grid_age_s=grid_age_s,
                grid_target=grid_target,
                effective_target=effective_target,
                effective_mpc_batt=effective_mpc_batt,
                plan_is_stale=plan_is_stale,
                plan_age=plan_age,
                headroom_reserve=headroom_reserve,
                ev_active=ev_active,
                ev_reserve=ev_reserve,
                mon_load_reserve=mon_load_reserve,
                transient_active=transient_active,
                solax_on=solax_on,
            )

        # ── compute Solax share (dynamic SOC-balance, both tiers) ─────────
        # Base share = solax_capacity / total_capacity — the fraction that makes both
        # batteries' SOC change at equal rate (dSOC/dt = power / rated_capacity_kWh).
        # Sensitivity adjustment nudges the share to converge any existing imbalance.
        # Requires both capacity entities to be configured; falls back to 0 (Solax
        # residual-only) if either is absent or zero.
        # Computed independently for each tier: solax_share is keyed off mpc_batt_cmd's
        # direction, solax_tier2_share off the tier-2 grid error's direction — so an SOC
        # imbalance still pulls Solax into tier-2 correction even on a tick where EMHASS
        # plans zero battery use (mpc_batt_cmd == 0), which previously left Solax idle
        # (see project_unplanned_load_tier_split memory).
        # Taper both shares to zero as Solax SOC approaches its ceiling so Voltx absorbs
        # the full charge command when Solax is full.
        solax_soc = float("nan")
        solax_share = 0.0
        solax_tier2_share = 0.0
        if solax_on:
            solax_soc = _float(hass, self._eid(CONF_ENTITY_SOLAX_SOC), 50.0)
            voltx_cap = _float(hass, self._eid(CONF_ENTITY_VOLTX_CAPACITY), 0.0)
            solax_cap = _float(hass, self._eid(CONF_ENTITY_SOLAX_CAPACITY), 0.0)
            if voltx_cap > 0 and solax_cap > 0:
                solax_share = compute_solax_share(
                    voltx_soc=soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=effective_mpc_batt,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
                solax_tier2_share = compute_solax_share(
                    voltx_soc=soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=grid_track - effective_target,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
        if solax_share > 0.0 or solax_tier2_share > 0.0:
            _s_soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            _taper = min(1.0, max(0.0, _s_soc_max - solax_soc) / DEFAULT_SOLAX_TIER1_SOC_TAPER_BAND)
        else:
            _taper = 1.0
        effective_solax_share = solax_share * _taper
        effective_solax_tier2_share = solax_tier2_share * _taper

        # ── compute command (pure function, no HA calls) ───────────────────
        voltx_mpc_batt = effective_mpc_batt * (1.0 - effective_solax_share)

        grid_priority = self._grid_priority_active(hass, effective_target)

        # While the EV charges, follow the EMHASS plan (tier 1) without chasing the grid
        # target (tier 2 off): the EV load is served by grid import, not by draining the
        # battery.  The ev headroom_reserve still tightens cmd_floor so both batteries ramp
        # down charging — and discharge if required — to protect the import-headroom ceiling.
        effective_tier2_gain = 0.0 if ev_active else self._tier2_gain
        # Voltx's own tier-2 gain shrinks by Solax's tier-2 share; Solax's absolute
        # contribution (solax_tier2_term) is computed below using the same grid_track/
        # effective_target error so the two portions sum to the undivided correction.
        voltx_tier2_gain = effective_tier2_gain * (1.0 - effective_solax_tier2_share)
        solax_tier2_term = effective_tier2_gain * effective_solax_tier2_share * (grid_track - effective_target)

        prev_cmd = self._prev_cmd  # capture for logging before it is overwritten
        command, mode, diag = compute_voltx_command(
            grid_actual=grid_actual,
            grid_target=effective_target,
            mpc_batt_cmd=voltx_mpc_batt,
            prev_cmd=self._prev_cmd,
            soc=soc,
            soc_min=soc_min,
            soc_max=soc_max,
            max_charge=max_charge,
            max_discharge=max_discharge,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            ramp_step=self._ramp_step,
            plan_is_stale=plan_is_stale,
            tracking_deadband=self._tracking_deadband,
            headroom_reserve=headroom_reserve,
            tier2_gain=voltx_tier2_gain,
            grid_priority=grid_priority,
            grid_smoothed=self._grid_ema,
            transient_active=transient_active,
            discharge_ramp_step=self._transient_discharge_ramp_step,
        )

        # Remap LOAD_HEADROOM to EV_CHARGING when the binding reserve was EV-caused
        # (EV active and its reserve is the dominant one).
        if ev_active and mode == CoordinatorMode.LOAD_HEADROOM and ev_reserve >= mon_load_reserve:
            mode = CoordinatorMode.EV_CHARGING

        # ── write to Voltx ────────────────────────────────────────────────
        # Compute grid_after_voltx before updating prev_cmd so the Solax
        # calculation uses the same uncontrolled estimate as budget.py did.
        grid_after_voltx = grid_actual + self._prev_cmd - command
        if command != self._prev_cmd:
            await self._async_write_voltx(command)
        else:
            # Setpoint unchanged: skip the redundant power write but still re-assert
            # Custom work mode (a no-op if already set) so an external mode change
            # does not silently leave the inverter uncontrolled.
            await self._async_set_work_mode(VOLTX_WORK_MODE_CUSTOM)
        self._prev_cmd = command

        # ── Solax priority-2 command ───────────────────────────────────────
        prev_solax_cmd = self._solax_last_written_cmd  # capture for logging
        solax_path = "off"      # which Solax branch ran this tick (diagnostic)
        solax_suppress = False  # tier-1 charge yielded because Voltx owns the ceiling
        if solax_on:
            solax_soc_min = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MIN), 20.0)
            solax_soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            solax_max_charge = float(self._opt(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE))
            solax_max_discharge = float(self._opt(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE))
            if mode in SOLAX_RESIDUAL_MODES:
                # Voltx is constrained at a hard SOC/physical boundary — it has
                # genuinely stopped moving, so Solax can safely take the full
                # instant residual. Deliberately excludes GRID_PRIORITY: that mode
                # is a live ramp-driven convergence, and giving Solax the full
                # residual there froze Voltx mid-ramp in production (see
                # compute_solax_command's docstring). grid_priority ticks fall
                # through to the tier1/tier2-share branch below instead, which is
                # safe because it reacts to the live error each tick with no
                # memory of a stale split.
                solax_path = "resid"
                solax_cmd, solax_mode = compute_solax_command(
                    voltx_mode=mode,
                    grid_after_voltx=grid_after_voltx,
                    grid_target=effective_target,
                    solax_soc=solax_soc,
                    solax_soc_min=solax_soc_min,
                    solax_soc_max=solax_soc_max,
                    solax_max_charge=solax_max_charge,
                    solax_max_discharge=solax_max_discharge,
                    import_limit=self._import_limit,
                    export_limit=self._export_limit,
                    headroom_reserve=headroom_reserve,
                    prev_solax_cmd=self._solax_last_written_cmd,
                )
            elif solax_share > 0 or solax_tier2_share > 0 or headroom_reserve > 0:
                # Voltx in normal tracking (incl. holding a headroom ceiling): Solax executes
                # its share of mpc_batt_cmd plus its share of the tier-2 grid correction
                # (solax_tier2_term), clamped to protect the import headroom so it ramps down
                # charging — and discharges if required — alongside Voltx.  When Voltx is
                # already the one holding the ceiling, Solax yields its charging share (it is
                # computed second) so the two batteries do not round-trip through each other.
                ceiling_binding = mode in (
                    CoordinatorMode.LOAD_HEADROOM,
                    CoordinatorMode.EV_CHARGING,
                    CoordinatorMode.IMPORT_CEILING,
                )
                solax_path = "tier1"
                solax_suppress = ceiling_binding
                solax_cmd, solax_mode = compute_solax_tier1(
                    mpc_batt_cmd=effective_mpc_batt,
                    share=effective_solax_share,
                    solax_soc=solax_soc,
                    solax_soc_min=solax_soc_min,
                    solax_soc_max=solax_soc_max,
                    solax_max_charge=solax_max_charge,
                    solax_max_discharge=solax_max_discharge,
                    grid_after_voltx=grid_after_voltx,
                    import_limit=self._import_limit,
                    export_limit=self._export_limit,
                    headroom_reserve=headroom_reserve,
                    suppress_charge=ceiling_binding,
                    prev_solax_cmd=self._solax_last_written_cmd,
                    tier2_term=solax_tier2_term,
                )
            else:
                solax_path = "idle"
                solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION
            # Zero deadband: suppress small commands to avoid unnecessary inverter activity.
            solax_zero_deadband = float(self._opt(CONF_SOLAX_ZERO_DEADBAND, DEFAULT_SOLAX_ZERO_DEADBAND))
            if solax_zero_deadband > 0 and abs(solax_cmd) <= solax_zero_deadband:
                if solax_cmd != 0.0:
                    solax_path += "(zd)"  # command suppressed by the zero deadband
                solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION
            await self._async_write_solax(solax_cmd)
        else:
            # Solax not configured, or its control switch is off — release it if we were
            # commanding it, then leave it to native self-consumption.
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
            solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION

        # ── EV emergency charge-current throttle (layer 3) ─────────────────
        # Projected grid after both batteries: grid_after_voltx still carries the previous
        # Solax contribution, so replace prev_solax_cmd with this tick's solax_cmd.
        projected_grid = grid_after_voltx + prev_solax_cmd - solax_cmd
        ev_current_limit, ev_throttle_active = await self._async_ev_emergency_throttle(
            projected_grid, ev_active
        )

        LOGGER.debug(
            "tick | grid=%.0fW (age=%.0fs) target=%.0fW unctrl=%.0fW mpc_batt=%.0fW "
            "prev=%.0fW raw=%.0fW ramped=%.0fW cmd=%.0fW hold=%s mode=%s | "
            "floor=%.0fW ceil=%.0fW maxc=%.0fW maxd=%.0fW soc=%.0f%% [%.0f..%.0f] "
            "plan_age=%.1fmin stale=%s ev=%s t2g=%.2f gp=%s headroom=%.0fW "
            "transient=%s gstdev=%.0fW gema=%.0fW | "
            "solax cmd=%.0fW mode=%s path=%s supp=%s soc=%.0f%% share=%.2f(taper=%.2f) "
            "t2share=%.2f t2term=%.0fW after_voltx=%.0fW prev=%.0fW | "
            "ev_throttle=%s limit=%sA proj_grid=%.0fW",
            grid_actual,
            grid_age_s,
            grid_target,
            diag.uncontrolled,
            effective_mpc_batt,
            prev_cmd,
            diag.raw_cmd,
            diag.ramped_cmd,
            command,
            diag.deadband_hold,
            mode,
            diag.cmd_floor,
            diag.cmd_ceil,
            max_charge,
            max_discharge,
            soc,
            soc_min,
            soc_max,
            plan_age,
            plan_is_stale,
            ev_active,
            voltx_tier2_gain,
            grid_priority,
            headroom_reserve,
            transient_active,
            grid_stdev,
            self._grid_ema,
            solax_cmd,
            solax_mode,
            solax_path,
            solax_suppress,
            solax_soc,
            effective_solax_share,
            effective_solax_share / solax_share if solax_share > 0.0 else 1.0,
            effective_solax_tier2_share,
            solax_tier2_term,
            grid_after_voltx,
            prev_solax_cmd,
            ev_throttle_active,
            f"{ev_current_limit:.0f}" if ev_current_limit is not None else "-",
            projected_grid,
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
            mpc_batt_power=effective_mpc_batt,
            solax_command=solax_cmd,
            solax_mode=solax_mode,
            ev_current_limit=ev_current_limit,
            ev_throttle_active=ev_throttle_active,
        )

    # ── Voltx-control-off tick ────────────────────────────────────────────────

    async def _async_voltx_disabled_tick(
        self,
        *,
        grid_actual: float,
        grid_age_s: float,
        grid_target: float,
        effective_target: float,
        effective_mpc_batt: float,
        plan_is_stale: bool,
        plan_age: float,
        headroom_reserve: float,
        ev_active: bool,
        ev_reserve: float,
        mon_load_reserve: float,
        transient_active: bool,
        solax_on: bool,
    ) -> CoordinatorData:
        """One tick when Voltx (primary) control is switched off.

        Voltx is released to native self-consumption and never commanded.  When Solax
        control is on it is promoted to sole grid tracker: the full 2-tier controller
        (compute_voltx_command) runs on Solax's own SOC/limits and the result is written
        to Solax — so it tracks the EMHASS plan + grid correction alone, bounded by its
        own (smaller) inverter limits and SOC constraints.  When Solax is also off (or not
        configured) both inverters run native self-consumption.  The EV emergency throttle
        still runs, since it is a grid-safety backstop independent of battery control.
        """
        hass = self.hass

        # Release Voltx and clear its ramp anchor so a clean ramp resumes if re-enabled.
        await self._async_enter_self_consumption()
        self._prev_cmd = 0.0

        prev_solax_cmd = self._solax_last_written_cmd  # capture before this tick's write
        solax_soc = float("nan")

        if solax_on:
            solax_soc = _float(hass, self._eid(CONF_ENTITY_SOLAX_SOC), 50.0)
            solax_soc_min = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MIN), 20.0)
            solax_soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            solax_max_charge = float(self._opt(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE))
            solax_max_discharge = float(self._opt(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE))
            grid_priority = self._grid_priority_active(hass, effective_target)
            # Tier 2 still off during EV (its load is served by grid import, not the battery).
            effective_tier2_gain = 0.0 if ev_active else self._tier2_gain
            solax_cmd, s_mode, _diag = compute_voltx_command(
                grid_actual=grid_actual,
                grid_target=effective_target,
                mpc_batt_cmd=effective_mpc_batt,
                prev_cmd=prev_solax_cmd,
                soc=solax_soc,
                soc_min=solax_soc_min,
                soc_max=solax_soc_max,
                max_charge=solax_max_charge,
                max_discharge=solax_max_discharge,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                ramp_step=self._ramp_step,
                plan_is_stale=plan_is_stale,
                tracking_deadband=self._tracking_deadband,
                headroom_reserve=headroom_reserve,
                tier2_gain=effective_tier2_gain,
                grid_priority=grid_priority,
                grid_smoothed=self._grid_ema,
                transient_active=transient_active,
                discharge_ramp_step=self._transient_discharge_ramp_step,
            )
            # Same EV-reserve remap as the Voltx path so the mode reads ev_charging.
            if s_mode == CoordinatorMode.LOAD_HEADROOM and ev_active and ev_reserve >= mon_load_reserve:
                s_mode = CoordinatorMode.EV_CHARGING
            # Zero deadband: suppress tiny commands to avoid needless inverter activity.
            solax_zero_deadband = float(self._opt(CONF_SOLAX_ZERO_DEADBAND, DEFAULT_SOLAX_ZERO_DEADBAND))
            if solax_zero_deadband > 0 and abs(solax_cmd) <= solax_zero_deadband:
                solax_cmd = 0.0
            await self._async_write_solax(solax_cmd)
            mode = s_mode
            if solax_cmd > 0:
                solax_mode = SolaxMode.FORCE_DISCHARGE
            elif solax_cmd < 0:
                solax_mode = SolaxMode.FORCE_CHARGE
            elif s_mode == CoordinatorMode.SOC_FLOOR:
                solax_mode = SolaxMode.SOC_FLOOR
            elif s_mode == CoordinatorMode.SOC_CEILING:
                solax_mode = SolaxMode.SOC_CEILING
            else:
                solax_mode = SolaxMode.SELF_CONSUMPTION
        else:
            # Solax also off (or not configured): release it too.
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
            solax_cmd = 0.0
            solax_mode = SolaxMode.SELF_CONSUMPTION
            mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.SELF_CONSUMPTION

        # EV emergency throttle — grid-safety backstop; Voltx contributes 0 (released).
        projected_grid = grid_actual + prev_solax_cmd - solax_cmd
        ev_current_limit, ev_throttle_active = await self._async_ev_emergency_throttle(
            projected_grid, ev_active
        )

        LOGGER.debug(
            "tick | VOLTX CONTROL OFF | grid=%.0fW (age=%.0fs) target=%.0fW mpc_batt=%.0fW "
            "mode=%s | solax cmd=%.0fW mode=%s sole=%s soc=%.0f%% prev=%.0fW headroom=%.0fW "
            "ev=%s | ev_throttle=%s limit=%sA proj_grid=%.0fW",
            grid_actual, grid_age_s, effective_target, effective_mpc_batt, mode,
            solax_cmd, solax_mode, solax_on, solax_soc, prev_solax_cmd, headroom_reserve,
            ev_active, ev_throttle_active,
            f"{ev_current_limit:.0f}" if ev_current_limit is not None else "-",
            projected_grid,
        )

        return build_coordinator_data(
            mode=mode,
            grid_actual=grid_actual,
            grid_target=grid_target,
            voltx_command=0.0,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            plan_age_minutes=plan_age,
            override_mode=None,
            mpc_batt_power=effective_mpc_batt,
            solax_command=solax_cmd,
            solax_mode=solax_mode,
            ev_current_limit=ev_current_limit,
            ev_throttle_active=ev_throttle_active,
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
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
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
        # Solax is not commanded in these override modes; release it if previously active.
        if self._solax_enabled() and self._solax_active:
            await self._async_enter_solax_self_consumption()

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
                cmd = max(cmd_floor, cmd)   # import safety: prevent excess import if target < residual
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

    # ── Solax helpers ─────────────────────────────────────────────────────────

    def _solax_enabled(self) -> bool:
        """Return True when a Solax SOC entity is explicitly configured."""
        return bool(str(self._opt(CONF_ENTITY_SOLAX_SOC, "")).strip())

    async def _async_write_solax(self, command: float) -> None:
        """Command Solax at the given power or release it to self-consumption.

        command > 0 = discharge, < 0 = charge, 0 = return to self-consumption.
        Solax convention for remotecontrol_active_power is inverted: negative = discharge.

        Command deadband: only rewrite the power setpoint when it differs from the last
        written value by more than CONF_SOLAX_CMD_DEADBAND watts, or when RC mode needs
        re-establishing. The autorepeat trigger is always pressed every tick to keep the
        inverter in remote-control mode.

        Note: a separate, still-unexplained dropout (grid/battery power briefly drops to
        ~0 roughly once a minute) has been observed independent of every write strategy
        tried here — including unconditional per-tick rewrites of active_power and
        export_duration — and a write landing inside the dropout window does not clear it
        early. It is not caused or fixed by anything in this function; don't re-litigate
        write cadence here without new evidence pointing back at the coordinator.
        """
        want_active = command != 0.0
        deadband = float(self._opt(CONF_SOLAX_CMD_DEADBAND, DEFAULT_SOLAX_CMD_DEADBAND))
        if not want_active:
            if self._solax_active:
                await self._async_enter_solax_self_consumption()
            return

        # want_active — update setpoint if needed, then press trigger.
        # Setpoint writes are isolated so a Modbus failure there does not prevent
        # the trigger press for entities that don't depend on the failed write.
        # If the active-power write itself fails, the trigger press is skipped this
        # tick: pressing it anyway would re-send solax-modbus's own cached
        # remotecontrol_active_power value, which may still hold a stale/wrong power
        # (e.g. 0 W left over from a prior idle period) — this happened for real
        # (2026-07-03 14:50:07): a Modbus write hung 5s then failed with "Request
        # cancelled outside pymodbus", and the unconditional trigger press that
        # followed sent the inverter an active RC command for 0 W instead of the
        # intended setpoint. Better to miss one tick's refresh than to actively
        # command the wrong power.
        entity_rc = self._eid(CONF_ENTITY_SOLAX_RC_POWER_CONTROL)
        rc_enabled = _str(self.hass, entity_rc) == SOLAX_RC_MODE_ENABLED
        setpoint_changed = (
            not self._solax_active
            or not rc_enabled
            or abs(command - self._solax_last_written_cmd) >= deadband
        )
        setpoint_write_ok = True
        if setpoint_changed:
            try:
                if not rc_enabled:
                    async with asyncio.timeout(5):
                        await self.hass.services.async_call(
                            "select", "select_option",
                            {"entity_id": entity_rc, "option": SOLAX_RC_MODE_ENABLED},
                            blocking=True,
                        )
                # Extend hardware command-expiry timer (register 0x9F, default 4 s) once
                # per RC session so the inverter tolerates the 10-second tick gap.
                if not self._solax_active:
                    async with asyncio.timeout(5):
                        await self.hass.services.async_call(
                            "select", "select_option",
                            {"entity_id": self._eid(CONF_ENTITY_SOLAX_EXPORT_DURATION),
                             "option": SOLAX_EXPORT_DURATION_SAFE},
                            blocking=True,
                        )
                # Set active power (negate: Solax negative = discharge)
                async with asyncio.timeout(5):
                    await self.hass.services.async_call(
                        "number", "set_value",
                        {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_ACTIVE_POWER),
                         "value": str(int(-command))},
                        blocking=True,
                    )
                self._solax_last_written_cmd = command
                LOGGER.debug("solax: command=%.0fW (rc_active_power=%.0f)", command, -command)
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("Solax setpoint write failed: %s", err)
                self._solax_last_written_cmd = 0.0
                setpoint_write_ok = False

        # Always refresh autorepeat duration every tick to keep RC mode alive between
        # setpoint changes — harmless and independent of whether the setpoint write
        # above succeeded.
        try:
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION),
                     "value": str(DEFAULT_SOLAX_AUTOREPEAT_DURATION)},
                    blocking=True,
                )
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Solax autorepeat refresh failed: %s", err)

        if not setpoint_write_ok:
            LOGGER.warning(
                "Solax trigger press skipped this tick: setpoint write failed, "
                "cached active_power may be stale"
            )
            return

        try:
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "button", "press",
                    {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_TRIGGER)},
                    blocking=True,
                )
            self._solax_active = True
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Solax trigger failed: %s", err)
            self._solax_active = False
            self._solax_last_written_cmd = 0.0

    async def _async_enter_solax_self_consumption(self) -> None:
        """Release Solax back to its native self-consumption mode."""
        try:
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_ACTIVE_POWER), "value": "0"},
                    blocking=True,
                )
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "select", "select_option",
                    {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_POWER_CONTROL),
                     "option": SOLAX_RC_MODE_DISABLED},
                    blocking=True,
                )
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "button", "press",
                    {"entity_id": self._eid(CONF_ENTITY_SOLAX_RC_TRIGGER)},
                    blocking=True,
                )
            LOGGER.debug("solax: released to self-consumption")
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("Solax release failed: %s", err)
        finally:
            self._solax_active = False
            self._solax_last_written_cmd = 0.0

    # ── EV emergency charge-current throttle (layer-3 backstop) ────────────────

    async def _async_ev_emergency_throttle(
        self, projected_grid: float, ev_active: bool
    ) -> tuple[float | None, bool]:
        """Cap the EV charge current to hold the import-headroom ceiling when the batteries
        cannot.  Last resort beneath both battery layers; usurps the external EV controller
        (Amber) only while engaged, then restores max on release so Amber resumes control.

        Returns (current_limit_written_or_None, throttle_active).
        """
        enabled = bool(self._opt(CONF_EV_EMERGENCY_THROTTLE, DEFAULT_EV_EMERGENCY_THROTTLE))
        entity = str(self._opt(CONF_ENTITY_EV_CHARGE_CURRENT, ENTITY_EV_CHARGE_CURRENT)).strip()
        if not enabled or not entity:
            # Feature turned off (or entity cleared) while a cap is still asserted: release it
            # so a reduced setpoint is not stranded on the charger. No-op when not throttling.
            await self._async_release_ev_throttle()
            return None, False

        max_current = float(self._opt(CONF_EV_MAX_CHARGE_CURRENT, DEFAULT_EV_MAX_CHARGE_CURRENT))

        # EV not charging: stay out of the way, releasing once if we were still throttling.
        if not ev_active:
            if self._ev_throttle_active:
                await self._async_write_ev_current(entity, max_current)
                self._ev_throttle_active = False
            self._ev_current_limit = max_current
            self._ev_recovered_since = None
            return None, False

        target_grid = self._import_limit - float(self._opt(CONF_EV_HEADROOM, DEFAULT_EV_HEADROOM))

        # Release holdoff: start the timer once grid is comfortably below the ceiling.
        if projected_grid <= target_grid - EV_RELEASE_MARGIN:
            if self._ev_recovered_since is None:
                self._ev_recovered_since = datetime.now(UTC)
        else:
            self._ev_recovered_since = None
        holdoff = float(self._opt(CONF_EV_RELEASE_HOLDOFF_MINUTES, DEFAULT_EV_RELEASE_HOLDOFF_MINUTES))
        release_ready = (
            self._ev_recovered_since is not None
            and (datetime.now(UTC) - self._ev_recovered_since).total_seconds() / 60 >= holdoff
        )

        ev_power = _float(self.hass, str(self._opt(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER)), 0.0)
        new_limit, active = compute_ev_current_limit(
            projected_grid=projected_grid,
            target_grid=target_grid,
            ev_power=ev_power,
            watts_per_amp=float(self._opt(CONF_EV_WATTS_PER_AMP, DEFAULT_EV_WATTS_PER_AMP)),
            min_current=float(self._opt(CONF_EV_MIN_CHARGE_CURRENT, DEFAULT_EV_MIN_CHARGE_CURRENT)),
            max_current=max_current,
            prev_limit=self._ev_current_limit,
            release_ramp_step=float(self._opt(CONF_EV_RELEASE_RAMP_STEP, DEFAULT_EV_RELEASE_RAMP_STEP)),
            release_ready=release_ready,
        )
        self._ev_current_limit = new_limit

        # Diagnostic line for the throttle decision — emitted only when near/over the ceiling
        # or while a cap is being held/released, so it stays quiet during normal EV charging.
        overshoot = projected_grid - target_grid
        if active or self._ev_throttle_active or overshoot > -EV_RELEASE_MARGIN:
            recovered_s = (
                (datetime.now(UTC) - self._ev_recovered_since).total_seconds()
                if self._ev_recovered_since is not None
                else 0.0
            )
            LOGGER.debug(
                "ev throttle | proj=%.0fW ceil=%.0fW ovr=%+.0fW evP=%.0fW "
                "limit=%.1fA active=%s rel_ready=%s recovered=%.0fs/%.0fmin",
                projected_grid, target_grid, overshoot, ev_power,
                new_limit, active, release_ready, recovered_s, holdoff,
            )

        if active:
            # Re-assert the cap every tick so Amber cannot raise it back between ticks.
            await self._async_write_ev_current(entity, new_limit)
            self._ev_throttle_active = True
            return new_limit, True
        if self._ev_throttle_active:
            # Fully recovered: restore max once and hand control back to Amber.
            await self._async_write_ev_current(entity, max_current)
            self._ev_throttle_active = False
            self._ev_recovered_since = None
        return None, False

    async def _async_release_ev_throttle(self) -> None:
        """Release any active EV current cap, handing control back to the external charger.

        Called from the control paths that bypass the layer-3 computation (disabled gate,
        self-consumption, manual override) so a held cap is never left stale on the charger.
        Cheap no-op when not currently throttling.
        """
        if not self._ev_throttle_active:
            return
        entity = str(self._opt(CONF_ENTITY_EV_CHARGE_CURRENT, ENTITY_EV_CHARGE_CURRENT)).strip()
        max_current = float(self._opt(CONF_EV_MAX_CHARGE_CURRENT, DEFAULT_EV_MAX_CHARGE_CURRENT))
        if entity:
            await self._async_write_ev_current(entity, max_current)
        self._ev_throttle_active = False
        self._ev_current_limit = max_current
        self._ev_recovered_since = None

    async def _async_write_ev_current(self, entity_id: str, current: float) -> None:
        """Write the EV charge-current setpoint (Amps) via number.set_value."""
        value = int(round(current))
        try:
            async with asyncio.timeout(5):
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
            LOGGER.debug("ev throttle: set %s = %dA", entity_id, value)
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("EV charge-current write failed: %s", err)

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
