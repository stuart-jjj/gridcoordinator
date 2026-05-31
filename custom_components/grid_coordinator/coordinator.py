"""GridCoordinator — 10-second Voltx battery control loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .budget import build_coordinator_data, compute_voltx_command
from .const import (
    CONF_EXPORT_LIMIT,
    CONF_IMPORT_LIMIT,
    CONF_MPC_SIGN_INVERTED,
    CONF_PLAN_STALE_MINUTES,
    CONF_RAMP_STEP,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_IMPORT_LIMIT,
    DEFAULT_MPC_SIGN_INVERTED,
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_RAMP_STEP,
    DOMAIN,
    ENTITY_ENABLED,
    ENTITY_GRID_POWER,
    ENTITY_MPC_GRID_POWER,
    ENTITY_SOC_MAX,
    ENTITY_SOC_MIN,
    ENTITY_VOLTX_CMD,
    ENTITY_VOLTX_MAX_CHARGE,
    ENTITY_VOLTX_MAX_DISCHARGE,
    ENTITY_VOLTX_SOC,
    ENTITY_VOLTX_WORK_MODE,
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


def _plan_age_minutes(hass: HomeAssistant) -> float:
    """Return minutes since mpc_grid_power was last updated (inf if missing)."""
    state = hass.states.get(ENTITY_MPC_GRID_POWER)
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
        self._entry = entry
        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    # ── config properties ─────────────────────────────────────────────────────

    @property
    def _import_limit(self) -> float:
        return float(self._entry.data.get(CONF_IMPORT_LIMIT, DEFAULT_IMPORT_LIMIT))

    @property
    def _export_limit(self) -> float:
        return float(self._entry.data.get(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT))

    @property
    def _ramp_step(self) -> float:
        return float(self._entry.data.get(CONF_RAMP_STEP, DEFAULT_RAMP_STEP))

    @property
    def _stale_minutes(self) -> float:
        return float(self._entry.data.get(CONF_PLAN_STALE_MINUTES, DEFAULT_PLAN_STALE_MINUTES))

    @property
    def _mpc_sign_inverted(self) -> bool:
        return bool(self._entry.data.get(CONF_MPC_SIGN_INVERTED, DEFAULT_MPC_SIGN_INVERTED))

    # ── main loop ─────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> CoordinatorData:
        """Called every UPDATE_INTERVAL_SECONDS by the DataUpdateCoordinator."""
        hass = self.hass

        # ── disabled gate ──────────────────────────────────────────────────
        if _str(hass, ENTITY_ENABLED, "off") != "on":
            return build_coordinator_data(
                mode=CoordinatorMode.DISABLED,
                grid_actual=_float(hass, ENTITY_GRID_POWER, 0.0),
                grid_target=0.0,
                voltx_command=self._prev_cmd,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                plan_age_minutes=_plan_age_minutes(hass),
            )

        # ── read grid power (critical — fail fast if unavailable) ──────────
        grid_state = hass.states.get(ENTITY_GRID_POWER)
        if grid_state is None or grid_state.state in ("unavailable", "unknown"):
            raise UpdateFailed(f"{ENTITY_GRID_POWER} is unavailable")
        try:
            grid_actual = float(grid_state.state)
        except (ValueError, TypeError) as exc:
            raise UpdateFailed(f"{ENTITY_GRID_POWER} has non-numeric state") from exc

        # ── read EMHASS setpoint ───────────────────────────────────────────
        plan_age = _plan_age_minutes(hass)
        mpc_raw = _float(hass, ENTITY_MPC_GRID_POWER, 0.0)
        # Correct for EMHASS injection convention if needed so that both
        # grid_actual and grid_target share the "positive = import" basis.
        grid_target = -mpc_raw if self._mpc_sign_inverted else mpc_raw
        plan_is_stale = plan_age > self._stale_minutes

        # ── read battery / inverter state ──────────────────────────────────
        soc = _float(hass, ENTITY_VOLTX_SOC, 50.0)
        soc_min = _float(hass, ENTITY_SOC_MIN, 20.0)
        soc_max = _float(hass, ENTITY_SOC_MAX, 95.0)
        max_charge = _float(hass, ENTITY_VOLTX_MAX_CHARGE, 5000.0)
        max_discharge = _float(hass, ENTITY_VOLTX_MAX_DISCHARGE, 5000.0)

        # ── compute command (pure function, no HA calls) ───────────────────
        command, mode = compute_voltx_command(
            grid_actual=grid_actual,
            grid_target=grid_target if not plan_is_stale else 0.0,
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
        )

        # ── write to inverter ──────────────────────────────────────────────
        await self._async_write_voltx(command)
        self._prev_cmd = command

        LOGGER.debug(
            "tick | grid=%.0fW target=%.0fW cmd=%.0fW soc=%.0f%% mode=%s age=%.1fmin",
            grid_actual,
            grid_target,
            command,
            soc,
            mode,
            plan_age,
        )

        return build_coordinator_data(
            mode=mode,
            grid_actual=grid_actual,
            grid_target=grid_target,
            voltx_command=command,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            plan_age_minutes=plan_age,
        )

    # ── inverter write ────────────────────────────────────────────────────────

    async def _async_write_voltx(self, command: float) -> None:
        """Ensure Custom work mode then apply the power setpoint."""
        hass = self.hass

        if _str(hass, ENTITY_VOLTX_WORK_MODE) != VOLTX_WORK_MODE_CUSTOM:
            await hass.services.async_call(
                "select",
                "select_option",
                {
                    "entity_id": ENTITY_VOLTX_WORK_MODE,
                    "option": VOLTX_WORK_MODE_CUSTOM,
                },
                blocking=True,
            )

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ENTITY_VOLTX_CMD, "value": str(command)},
            blocking=True,
        )
