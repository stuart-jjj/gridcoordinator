"""Button platform — simulated Solax trigger button for testing mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .const import CONF_TEST_MODE
from .simulated import build_sim_solax_button_entities

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import GridCoordinatorConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: GridCoordinatorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create simulated Solax trigger button when testing mode is active."""
    test_mode = entry.options.get(CONF_TEST_MODE, entry.data.get(CONF_TEST_MODE, False))
    if not test_mode:
        return
    async_add_entities(build_sim_solax_button_entities(entry.entry_id))
