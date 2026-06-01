"""Select platform — override mode selector (always) + sim work mode (testing mode)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant

from .const import CONF_TEST_MODE, DOMAIN
from .entity import GridCoordinatorEntity
from .simulated import SimWorkModeSelect

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import GridCoordinator
    from .data import GridCoordinatorConfigEntry


_OVERRIDE_OPTIONS = ["auto", "self_consume", "hold_soc", "force_charge", "force_export", "disabled"]

# Duration used when changing mode via the select entity.
# Amber uses 30 min for force modes and 2 hr for preserve/consume — split the difference.
_CHARGE_DISPATCH_DURATION = 30   # minutes — force_charge / force_export
_PRESERVE_CONSUME_DURATION = 120  # minutes — hold_soc / self_consume


class OverrideModeSelect(GridCoordinatorEntity, SelectEntity):
    """Select entity to view and change the active manual override mode.

    Shows 'auto' when no override is active.  Selecting any other option
    applies that override for DEFAULT_OVERRIDE_DURATION_MINUTES; selecting
    'auto' cancels any active override immediately.

    For finer control (custom power level, custom duration, bypass_soc) use
    the grid_coordinator.set_mode service call instead.
    """

    _attr_name = "Override Mode"
    _attr_icon = "mdi:gesture-tap"
    _attr_options = _OVERRIDE_OPTIONS

    def __init__(self, coordinator: GridCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_override_mode"
        self.entity_id = f"select.{DOMAIN}_override_mode"

    @property
    def current_option(self) -> str:
        if self.coordinator.data is None:
            return "auto"
        return self.coordinator.data.override_mode or "auto"

    async def async_select_option(self, option: str) -> None:
        duration = (
            _CHARGE_DISPATCH_DURATION
            if option in ("force_charge", "force_export")
            else _PRESERVE_CONSUME_DURATION
        )
        self.coordinator.set_override(option, duration_minutes=duration)
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: GridCoordinatorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create override mode select (always) and sim work mode select (test mode only)."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [OverrideModeSelect(coordinator)]

    test_mode = entry.options.get(CONF_TEST_MODE, entry.data.get(CONF_TEST_MODE, False))
    if test_mode:
        entities.append(SimWorkModeSelect(entry.entry_id))

    async_add_entities(entities)
