"""Config flow for grid_coordinator — limits and sign convention only."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

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
)

_NUM = selector.NumberSelectorMode.BOX

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_IMPORT_LIMIT, default=DEFAULT_IMPORT_LIMIT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=3000, max=20000, step=500, unit_of_measurement="W", mode=_NUM
            )
        ),
        vol.Required(CONF_EXPORT_LIMIT, default=DEFAULT_EXPORT_LIMIT): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1000, max=15000, step=500, unit_of_measurement="W", mode=_NUM
            )
        ),
        vol.Required(CONF_RAMP_STEP, default=DEFAULT_RAMP_STEP): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=200, max=5000, step=100, unit_of_measurement="W/tick", mode=_NUM
            )
        ),
        vol.Required(CONF_PLAN_STALE_MINUTES, default=DEFAULT_PLAN_STALE_MINUTES): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=5, max=60, step=5, unit_of_measurement="min", mode=_NUM
            )
        ),
        vol.Required(CONF_MPC_SIGN_INVERTED, default=DEFAULT_MPC_SIGN_INVERTED): selector.BooleanSelector(),
    }
)


class GridCoordinatorFlowHandler(ConfigFlow, domain=DOMAIN):
    """Single-step config flow — no credentials required."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial setup form."""
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Grid Coordinator", data=user_input)

        return self.async_show_form(step_id="user", data_schema=STEP_SCHEMA)
