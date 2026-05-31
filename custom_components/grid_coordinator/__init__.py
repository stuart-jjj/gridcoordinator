"""Grid Coordinator — centralised battery/grid power management for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import GridCoordinator
from .data import GridCoordinatorConfigEntry

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> bool:
    """Set up grid_coordinator from a config entry."""
    coordinator = GridCoordinator(hass, entry)
    entry.runtime_data = coordinator

    # First refresh: if a critical entity is unavailable this raises
    # ConfigEntryNotReady and HA will retry after a backoff delay.
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(
    hass: HomeAssistant,
    entry: GridCoordinatorConfigEntry,
) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
