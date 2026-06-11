"""Grid Coordinator — centralised battery/grid power management for Home Assistant."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import CONF_TEST_MODE, DEFAULT_OVERRIDE_DURATION_MINUTES, DOMAIN, LOGGER
from .coordinator import GridCoordinator
from .data import GridCoordinatorConfigEntry

# SWITCH is always set up (MpcSignInvertedSwitch lives there).
# SELECT is always set up (OverrideModeSelect lives there; SimWorkModeSelect is added in test mode).
# In test mode, NUMBER is added for the simulated input/output number entities.
# SimEnabledSwitch is also in the switch platform and created only in test mode.
_BASE_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT]
_SIM_PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.BUTTON]

_SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(["auto", "self_consume", "hold_soc", "force_charge", "force_export", "disabled"]),
        vol.Optional("power_w"): vol.All(vol.Coerce(float), vol.Range(min=0, max=20000)),
        vol.Optional("duration_minutes", default=DEFAULT_OVERRIDE_DURATION_MINUTES): vol.All(vol.Coerce(float), vol.Range(min=1, max=240)),
        vol.Optional("bypass_soc", default=False): cv.boolean,
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> bool:
    """Set up grid_coordinator from a config entry."""
    coordinator = GridCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # Tolerate a failed first refresh instead of raising ConfigEntryNotReady:
    # at HA startup the grid power sensor (iammeter modbus) often isn't loaded
    # yet. async_refresh() records the failure (entities show unavailable) and
    # the 10 s update cycle recovers on its own once the sensor appears.
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        LOGGER.warning(
            "initial refresh failed (grid sensor not ready?); "
            "entities will be unavailable until the next successful update"
        )

    test_mode = entry.options.get(CONF_TEST_MODE, entry.data.get(CONF_TEST_MODE, False))
    platforms = list(_BASE_PLATFORMS) + (list(_SIM_PLATFORMS) if test_mode else [])
    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    async def _handle_set_mode(call: ServiceCall) -> None:
        mode: str = call.data["mode"]
        power_w: float | None = call.data.get("power_w")
        duration_minutes: float = call.data["duration_minutes"]
        bypass_soc: bool = call.data["bypass_soc"]
        coordinator.set_override(
            mode,
            power_w=power_w,
            duration_minutes=duration_minutes,
            bypass_soc=bypass_soc,
        )
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "set_mode", _handle_set_mode, schema=_SET_MODE_SCHEMA)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> bool:
    """Unload a config entry."""
    hass.services.async_remove(DOMAIN, "set_mode")
    test_mode = entry.options.get(CONF_TEST_MODE, entry.data.get(CONF_TEST_MODE, False))
    platforms = list(_BASE_PLATFORMS) + (list(_SIM_PLATFORMS) if test_mode else [])
    return await hass.config_entries.async_unload_platforms(entry, platforms)


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
