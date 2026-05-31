"""Custom types for grid_coordinator."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import GridCoordinator

type GridCoordinatorConfigEntry = ConfigEntry[GridCoordinator]
