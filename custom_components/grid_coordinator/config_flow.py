"""Config flow and options flow for grid_coordinator."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

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
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_TRIGGER,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_VOLTX_CMD,
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
    CONF_SOLAX_CMD_DEADBAND,
    CONF_SOLAX_ZERO_DEADBAND,
    CONF_SOLAX_TIER1_SHARE,
    CONF_SOLAX_MAX_CHARGE,
    CONF_SOLAX_MAX_DISCHARGE,
    CONF_TEST_MODE,
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
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_RAMP_STEP,
    DEFAULT_SELF_CONSUMPTION_DEADBAND,
    DEFAULT_SELF_CONSUMPTION_MODE,
    DEFAULT_SOLAX_CMD_DEADBAND,
    DEFAULT_SOLAX_ZERO_DEADBAND,
    DEFAULT_SOLAX_TIER1_SHARE,
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
    SIM_ENTITY_IDS,
)

_NUM = selector.NumberSelectorMode.BOX
_TEXT = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT))


def _params_schema(defaults: dict) -> vol.Schema:
    """Schema for controller parameters + testing mode toggle."""
    return vol.Schema(
        {
            vol.Required(CONF_TEST_MODE, default=defaults.get(CONF_TEST_MODE, False)):
                selector.BooleanSelector(),
            vol.Required(CONF_IMPORT_LIMIT, default=defaults.get(CONF_IMPORT_LIMIT, DEFAULT_IMPORT_LIMIT)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=3000, max=20000, step=500, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_EXPORT_LIMIT, default=defaults.get(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1000, max=15000, step=500, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_RAMP_STEP, default=defaults.get(CONF_RAMP_STEP, DEFAULT_RAMP_STEP)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=200, max=5000, step=100, unit_of_measurement="W/tick", mode=_NUM,
                )),
            vol.Required(CONF_PLAN_STALE_MINUTES, default=defaults.get(CONF_PLAN_STALE_MINUTES, DEFAULT_PLAN_STALE_MINUTES)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=5, max=60, step=5, unit_of_measurement="min", mode=_NUM,
                )),
            vol.Required(CONF_MPC_SIGN_INVERTED, default=defaults.get(CONF_MPC_SIGN_INVERTED, DEFAULT_MPC_SIGN_INVERTED)):
                selector.BooleanSelector(),
            vol.Required(CONF_MPC_BATT_SIGN_INVERTED, default=defaults.get(CONF_MPC_BATT_SIGN_INVERTED, DEFAULT_MPC_BATT_SIGN_INVERTED)):
                selector.BooleanSelector(),
            vol.Required(CONF_SELF_CONSUMPTION_MODE, default=defaults.get(CONF_SELF_CONSUMPTION_MODE, DEFAULT_SELF_CONSUMPTION_MODE)):
                _TEXT,
            vol.Required(CONF_SELF_CONSUMPTION_DEADBAND, default=defaults.get(CONF_SELF_CONSUMPTION_DEADBAND, DEFAULT_SELF_CONSUMPTION_DEADBAND)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=500, step=10, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_TRACKING_DEADBAND, default=defaults.get(CONF_TRACKING_DEADBAND, DEFAULT_TRACKING_DEADBAND)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=1000, step=50, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_TIER2_GAIN, default=defaults.get(CONF_TIER2_GAIN, DEFAULT_TIER2_GAIN)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0.1, max=1.0, step=0.1, mode=_NUM,
                )),
            vol.Required(CONF_GRID_PRIORITY_BAND, default=defaults.get(CONF_GRID_PRIORITY_BAND, DEFAULT_GRID_PRIORITY_BAND)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=1000, step=50, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_TRANSIENT_VARIANCE_THRESHOLD, default=defaults.get(CONF_TRANSIENT_VARIANCE_THRESHOLD, DEFAULT_TRANSIENT_VARIANCE_THRESHOLD)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=2000, step=50, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_TRANSIENT_VARIANCE_WINDOW, default=defaults.get(CONF_TRANSIENT_VARIANCE_WINDOW, DEFAULT_TRANSIENT_VARIANCE_WINDOW)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=3, max=30, step=1, unit_of_measurement="ticks", mode=_NUM,
                )),
            vol.Required(CONF_TRANSIENT_EMA_ALPHA, default=defaults.get(CONF_TRANSIENT_EMA_ALPHA, DEFAULT_TRANSIENT_EMA_ALPHA)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0.1, max=1.0, step=0.05, mode=_NUM,
                )),
            vol.Required(CONF_TRANSIENT_DISCHARGE_RAMP_STEP, default=defaults.get(CONF_TRANSIENT_DISCHARGE_RAMP_STEP, DEFAULT_TRANSIENT_DISCHARGE_RAMP_STEP)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=50, max=2000, step=50, unit_of_measurement="W/tick", mode=_NUM,
                )),
            vol.Required(CONF_EV_CHARGER_THRESHOLD, default=defaults.get(CONF_EV_CHARGER_THRESHOLD, DEFAULT_EV_CHARGER_THRESHOLD)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=100, max=5000, step=50, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_EV_HEADROOM, default=defaults.get(CONF_EV_HEADROOM, DEFAULT_EV_HEADROOM)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=15000, step=500, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_EV_EMERGENCY_THROTTLE, default=defaults.get(CONF_EV_EMERGENCY_THROTTLE, DEFAULT_EV_EMERGENCY_THROTTLE)):
                selector.BooleanSelector(),
            vol.Required(CONF_EV_WATTS_PER_AMP, default=defaults.get(CONF_EV_WATTS_PER_AMP, DEFAULT_EV_WATTS_PER_AMP)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=110, max=1000, step=10, unit_of_measurement="W/A", mode=_NUM,
                )),
            vol.Required(CONF_EV_MIN_CHARGE_CURRENT, default=defaults.get(CONF_EV_MIN_CHARGE_CURRENT, DEFAULT_EV_MIN_CHARGE_CURRENT)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=32, step=1, unit_of_measurement="A", mode=_NUM,
                )),
            vol.Required(CONF_EV_MAX_CHARGE_CURRENT, default=defaults.get(CONF_EV_MAX_CHARGE_CURRENT, DEFAULT_EV_MAX_CHARGE_CURRENT)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=6, max=80, step=1, unit_of_measurement="A", mode=_NUM,
                )),
            vol.Required(CONF_EV_RELEASE_RAMP_STEP, default=defaults.get(CONF_EV_RELEASE_RAMP_STEP, DEFAULT_EV_RELEASE_RAMP_STEP)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1, max=16, step=1, unit_of_measurement="A/tick", mode=_NUM,
                )),
            vol.Required(CONF_EV_RELEASE_HOLDOFF_MINUTES, default=defaults.get(CONF_EV_RELEASE_HOLDOFF_MINUTES, DEFAULT_EV_RELEASE_HOLDOFF_MINUTES)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=30, step=1, unit_of_measurement="min", mode=_NUM,
                )),
            vol.Required(CONF_MON_LOAD_1_THRESHOLD, default=defaults.get(CONF_MON_LOAD_1_THRESHOLD, DEFAULT_MON_LOAD_1_THRESHOLD)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1, max=500, step=1, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_MON_LOAD_1_HEADROOM, default=defaults.get(CONF_MON_LOAD_1_HEADROOM, DEFAULT_MON_LOAD_1_HEADROOM)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=500, max=15000, step=500, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_MON_LOAD_1_HOLDOFF_MINUTES, default=defaults.get(CONF_MON_LOAD_1_HOLDOFF_MINUTES, DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=1, max=30, step=1, unit_of_measurement="min", mode=_NUM,
                )),
            vol.Required(CONF_SOLAX_MAX_CHARGE, default=defaults.get(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=500, max=6000, step=100, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_SOLAX_MAX_DISCHARGE, default=defaults.get(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=500, max=6000, step=100, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_SOLAX_CMD_DEADBAND, default=defaults.get(CONF_SOLAX_CMD_DEADBAND, DEFAULT_SOLAX_CMD_DEADBAND)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=500, step=10, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_SOLAX_ZERO_DEADBAND, default=defaults.get(CONF_SOLAX_ZERO_DEADBAND, DEFAULT_SOLAX_ZERO_DEADBAND)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=500, step=10, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_SOLAX_TIER1_SHARE, default=defaults.get(CONF_SOLAX_TIER1_SHARE, DEFAULT_SOLAX_TIER1_SHARE)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0.0, max=0.5, step=0.05, mode=_NUM,
                )),
        }
    )


def _solax_schema(defaults: dict) -> vol.Schema:
    """Schema for Solax entity ID configuration (step 3)."""
    return vol.Schema(
        {
            vol.Optional(CONF_ENTITY_SOLAX_SOC, default=defaults.get(CONF_ENTITY_SOLAX_SOC, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_SOC_MIN, default=defaults.get(CONF_ENTITY_SOLAX_SOC_MIN, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC_MIN])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_SOC_MAX, default=defaults.get(CONF_ENTITY_SOLAX_SOC_MAX, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC_MAX])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_RC_POWER_CONTROL, default=defaults.get(CONF_ENTITY_SOLAX_RC_POWER_CONTROL, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_RC_POWER_CONTROL])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_RC_ACTIVE_POWER, default=defaults.get(CONF_ENTITY_SOLAX_RC_ACTIVE_POWER, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_RC_ACTIVE_POWER])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION, default=defaults.get(CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION])): _TEXT,
            vol.Optional(CONF_ENTITY_SOLAX_RC_TRIGGER, default=defaults.get(CONF_ENTITY_SOLAX_RC_TRIGGER, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_RC_TRIGGER])): _TEXT,
        }
    )


def _entities_schema(defaults: dict) -> vol.Schema:
    """Schema for entity ID configuration — uses free-text so any entity ID is accepted."""
    return vol.Schema(
        {
            vol.Required(CONF_ENTITY_GRID_POWER, default=defaults.get(CONF_ENTITY_GRID_POWER, ENTITY_ID_DEFAULTS[CONF_ENTITY_GRID_POWER])): _TEXT,
            vol.Required(CONF_ENTITY_MPC_GRID_POWER, default=defaults.get(CONF_ENTITY_MPC_GRID_POWER, ENTITY_ID_DEFAULTS[CONF_ENTITY_MPC_GRID_POWER])): _TEXT,
            vol.Required(CONF_ENTITY_MPC_BATT_POWER, default=defaults.get(CONF_ENTITY_MPC_BATT_POWER, ENTITY_ID_DEFAULTS[CONF_ENTITY_MPC_BATT_POWER])): _TEXT,
            vol.Required(CONF_ENTITY_VOLTX_SOC, default=defaults.get(CONF_ENTITY_VOLTX_SOC, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_SOC])): _TEXT,
            vol.Required(CONF_ENTITY_VOLTX_MAX_CHARGE, default=defaults.get(CONF_ENTITY_VOLTX_MAX_CHARGE, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_MAX_CHARGE])): _TEXT,
            vol.Required(CONF_ENTITY_VOLTX_MAX_DISCHARGE, default=defaults.get(CONF_ENTITY_VOLTX_MAX_DISCHARGE, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_MAX_DISCHARGE])): _TEXT,
            vol.Required(CONF_ENTITY_SOC_MIN, default=defaults.get(CONF_ENTITY_SOC_MIN, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOC_MIN])): _TEXT,
            vol.Required(CONF_ENTITY_SOC_MAX, default=defaults.get(CONF_ENTITY_SOC_MAX, ENTITY_ID_DEFAULTS[CONF_ENTITY_SOC_MAX])): _TEXT,
            vol.Required(CONF_ENTITY_ENABLED, default=defaults.get(CONF_ENTITY_ENABLED, ENTITY_ID_DEFAULTS[CONF_ENTITY_ENABLED])): _TEXT,
            vol.Required(CONF_ENTITY_VOLTX_CMD, default=defaults.get(CONF_ENTITY_VOLTX_CMD, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_CMD])): _TEXT,
            vol.Required(CONF_ENTITY_VOLTX_WORK_MODE, default=defaults.get(CONF_ENTITY_VOLTX_WORK_MODE, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_WORK_MODE])): _TEXT,
            vol.Optional(CONF_ENTITY_EV_CHARGER, default=defaults.get(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER)): _TEXT,
            vol.Optional(CONF_ENTITY_EV_CHARGE_CURRENT, default=defaults.get(CONF_ENTITY_EV_CHARGE_CURRENT, ENTITY_EV_CHARGE_CURRENT)): _TEXT,
            vol.Optional(CONF_ENTITY_MON_LOAD_1, default=defaults.get(CONF_ENTITY_MON_LOAD_1, ENTITY_MON_LOAD_1)): _TEXT,
            vol.Optional(CONF_ENTITY_GRID_PRIORITY, default=defaults.get(CONF_ENTITY_GRID_PRIORITY, "")): _TEXT,
        }
    )


# ── Initial config flow ───────────────────────────────────────────────────────


class GridCoordinatorFlowHandler(ConfigFlow, domain=DOMAIN):
    """Two-step setup: parameters (+ testing mode), then entity IDs."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 1 — controller parameters and testing mode toggle."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            self._data = dict(user_input)

            if user_input.get(CONF_TEST_MODE):
                # Testing mode: auto-assign simulated entity IDs, skip entity step.
                self._data.update(SIM_ENTITY_IDS)
                return self.async_create_entry(title="Grid Coordinator", data=self._data)

            return await self.async_step_entities()

        return self.async_show_form(step_id="user", data_schema=_params_schema({}))

    async def async_step_entities(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 2 — entity ID mapping (non-testing mode only)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_solax()

        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(ENTITY_ID_DEFAULTS),
        )

    async def async_step_solax(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Grid Coordinator", data=self._data)

        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(ENTITY_ID_DEFAULTS),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):  # type: ignore[override]
        return GridCoordinatorOptionsFlowHandler(config_entry)


# ── Options flow ──────────────────────────────────────────────────────────────


class GridCoordinatorOptionsFlowHandler(OptionsFlow):
    """Two-step reconfiguration: parameters (+ testing mode), then entity IDs.

    Entity IDs are always shown so the user can override individual entities
    even when in testing mode.
    """

    def __init__(self, config_entry) -> None:  # type: ignore[override]
        self._entry = config_entry
        self._data: dict = {}

    def _current(self, key: str, default):
        """Read from options first, then data, then supplied default."""
        if key in self._entry.options:
            return self._entry.options[key]
        return self._entry.data.get(key, default)

    async def async_step_init(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 1 — controller parameters and testing mode."""
        if user_input is not None:
            self._data = dict(user_input)
            return await self.async_step_entities()

        current_params = {
            CONF_TEST_MODE: self._current(CONF_TEST_MODE, False),
            CONF_IMPORT_LIMIT: self._current(CONF_IMPORT_LIMIT, DEFAULT_IMPORT_LIMIT),
            CONF_EXPORT_LIMIT: self._current(CONF_EXPORT_LIMIT, DEFAULT_EXPORT_LIMIT),
            CONF_RAMP_STEP: self._current(CONF_RAMP_STEP, DEFAULT_RAMP_STEP),
            CONF_PLAN_STALE_MINUTES: self._current(CONF_PLAN_STALE_MINUTES, DEFAULT_PLAN_STALE_MINUTES),
            CONF_MPC_SIGN_INVERTED: self._current(CONF_MPC_SIGN_INVERTED, DEFAULT_MPC_SIGN_INVERTED),
            CONF_MPC_BATT_SIGN_INVERTED: self._current(CONF_MPC_BATT_SIGN_INVERTED, DEFAULT_MPC_BATT_SIGN_INVERTED),
            CONF_SELF_CONSUMPTION_MODE: self._current(CONF_SELF_CONSUMPTION_MODE, DEFAULT_SELF_CONSUMPTION_MODE),
            CONF_SELF_CONSUMPTION_DEADBAND: self._current(CONF_SELF_CONSUMPTION_DEADBAND, DEFAULT_SELF_CONSUMPTION_DEADBAND),
            CONF_TRACKING_DEADBAND: self._current(CONF_TRACKING_DEADBAND, DEFAULT_TRACKING_DEADBAND),
            CONF_TIER2_GAIN: self._current(CONF_TIER2_GAIN, DEFAULT_TIER2_GAIN),
            CONF_GRID_PRIORITY_BAND: self._current(CONF_GRID_PRIORITY_BAND, DEFAULT_GRID_PRIORITY_BAND),
            CONF_TRANSIENT_VARIANCE_THRESHOLD: self._current(CONF_TRANSIENT_VARIANCE_THRESHOLD, DEFAULT_TRANSIENT_VARIANCE_THRESHOLD),
            CONF_TRANSIENT_VARIANCE_WINDOW: self._current(CONF_TRANSIENT_VARIANCE_WINDOW, DEFAULT_TRANSIENT_VARIANCE_WINDOW),
            CONF_TRANSIENT_EMA_ALPHA: self._current(CONF_TRANSIENT_EMA_ALPHA, DEFAULT_TRANSIENT_EMA_ALPHA),
            CONF_TRANSIENT_DISCHARGE_RAMP_STEP: self._current(CONF_TRANSIENT_DISCHARGE_RAMP_STEP, DEFAULT_TRANSIENT_DISCHARGE_RAMP_STEP),
            CONF_EV_CHARGER_THRESHOLD: self._current(CONF_EV_CHARGER_THRESHOLD, DEFAULT_EV_CHARGER_THRESHOLD),
            CONF_EV_HEADROOM: self._current(CONF_EV_HEADROOM, DEFAULT_EV_HEADROOM),
            CONF_EV_EMERGENCY_THROTTLE: self._current(CONF_EV_EMERGENCY_THROTTLE, DEFAULT_EV_EMERGENCY_THROTTLE),
            CONF_EV_WATTS_PER_AMP: self._current(CONF_EV_WATTS_PER_AMP, DEFAULT_EV_WATTS_PER_AMP),
            CONF_EV_MIN_CHARGE_CURRENT: self._current(CONF_EV_MIN_CHARGE_CURRENT, DEFAULT_EV_MIN_CHARGE_CURRENT),
            CONF_EV_MAX_CHARGE_CURRENT: self._current(CONF_EV_MAX_CHARGE_CURRENT, DEFAULT_EV_MAX_CHARGE_CURRENT),
            CONF_EV_RELEASE_RAMP_STEP: self._current(CONF_EV_RELEASE_RAMP_STEP, DEFAULT_EV_RELEASE_RAMP_STEP),
            CONF_EV_RELEASE_HOLDOFF_MINUTES: self._current(CONF_EV_RELEASE_HOLDOFF_MINUTES, DEFAULT_EV_RELEASE_HOLDOFF_MINUTES),
            CONF_MON_LOAD_1_THRESHOLD: self._current(CONF_MON_LOAD_1_THRESHOLD, DEFAULT_MON_LOAD_1_THRESHOLD),
            CONF_MON_LOAD_1_HEADROOM: self._current(CONF_MON_LOAD_1_HEADROOM, DEFAULT_MON_LOAD_1_HEADROOM),
            CONF_MON_LOAD_1_HOLDOFF_MINUTES: self._current(CONF_MON_LOAD_1_HOLDOFF_MINUTES, DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES),
            CONF_SOLAX_MAX_CHARGE: self._current(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE),
            CONF_SOLAX_MAX_DISCHARGE: self._current(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE),
            CONF_SOLAX_CMD_DEADBAND: self._current(CONF_SOLAX_CMD_DEADBAND, DEFAULT_SOLAX_CMD_DEADBAND),
            CONF_SOLAX_ZERO_DEADBAND: self._current(CONF_SOLAX_ZERO_DEADBAND, DEFAULT_SOLAX_ZERO_DEADBAND),
            CONF_SOLAX_TIER1_SHARE: self._current(CONF_SOLAX_TIER1_SHARE, DEFAULT_SOLAX_TIER1_SHARE),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=_params_schema(current_params),
        )

    async def async_step_entities(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 2 — entity ID mapping."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_solax()

        # If testing mode was just enabled, default to simulated entity IDs.
        # Otherwise keep the current entity IDs (which may already be overrides).
        test_mode = self._data.get(CONF_TEST_MODE, False)
        if test_mode:
            entity_defaults = {k: self._current(k, sim_id) for k, sim_id in SIM_ENTITY_IDS.items()}
        else:
            entity_defaults = {k: self._current(k, prod_id) for k, prod_id in ENTITY_ID_DEFAULTS.items()}
        # Optional feature entities (not in ENTITY_ID_DEFAULTS; production defaults used)
        entity_defaults[CONF_ENTITY_EV_CHARGER] = self._current(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER)
        entity_defaults[CONF_ENTITY_EV_CHARGE_CURRENT] = self._current(CONF_ENTITY_EV_CHARGE_CURRENT, ENTITY_EV_CHARGE_CURRENT)
        entity_defaults[CONF_ENTITY_MON_LOAD_1] = self._current(CONF_ENTITY_MON_LOAD_1, ENTITY_MON_LOAD_1)
        entity_defaults[CONF_ENTITY_GRID_PRIORITY] = self._current(CONF_ENTITY_GRID_PRIORITY, "")

        return self.async_show_form(
            step_id="entities",
            data_schema=_entities_schema(entity_defaults),
        )

    async def async_step_solax(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)

        solax_keys = (
            CONF_ENTITY_SOLAX_SOC,
            CONF_ENTITY_SOLAX_SOC_MIN,
            CONF_ENTITY_SOLAX_SOC_MAX,
            CONF_ENTITY_SOLAX_RC_POWER_CONTROL,
            CONF_ENTITY_SOLAX_RC_ACTIVE_POWER,
            CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
            CONF_ENTITY_SOLAX_RC_TRIGGER,
        )
        test_mode = self._data.get(CONF_TEST_MODE, False)
        solax_defaults = {
            k: self._current(k, SIM_ENTITY_IDS[k] if test_mode else ENTITY_ID_DEFAULTS[k])
            for k in solax_keys
        }

        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(solax_defaults),
        )
