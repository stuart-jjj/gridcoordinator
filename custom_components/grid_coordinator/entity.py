"""Base entity for grid_coordinator."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GridCoordinator


class GridCoordinatorEntity(CoordinatorEntity[GridCoordinator]):
    """Shared base for all grid coordinator entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GridCoordinator) -> None:
        super().__init__(coordinator)
        # All entities share a single logical device in the HA device registry.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name="Grid Coordinator",
            manufacturer="Custom",
            model="Phase 1 MVP",
        )
