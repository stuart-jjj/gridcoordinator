"""Diagnostic sensor platform for grid_coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant

from .coordinator import GridCoordinator
from .entity import GridCoordinatorEntity
from .models import CoordinatorData

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import GridCoordinatorConfigEntry


@dataclass(frozen=True, kw_only=True)
class GridSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a typed data accessor."""

    value_fn: Callable[[CoordinatorData], str | float | None]


SENSOR_DESCRIPTIONS: tuple[GridSensorDescription, ...] = (
    GridSensorDescription(
        key="mode",
        name="Mode",
        icon="mdi:state-machine",
        value_fn=lambda d: str(d.mode),
    ),
    GridSensorDescription(
        key="grid_target",
        name="Grid Target",
        icon="mdi:transmission-tower",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.grid_target,
    ),
    GridSensorDescription(
        key="voltx_command",
        name="Voltx Command",
        icon="mdi:battery-arrow-up",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.voltx_command,
    ),
    GridSensorDescription(
        key="import_headroom",
        name="Import Headroom",
        icon="mdi:transmission-tower-import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.import_headroom,
    ),
    GridSensorDescription(
        key="export_headroom",
        name="Export Headroom",
        icon="mdi:transmission-tower-export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.export_headroom,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: GridCoordinatorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for the coordinator."""
    coordinator: GridCoordinator = entry.runtime_data
    async_add_entities(
        GridCoordinatorSensor(coordinator=coordinator, description=desc)
        for desc in SENSOR_DESCRIPTIONS
    )


class GridCoordinatorSensor(GridCoordinatorEntity, SensorEntity):
    """One diagnostic sensor backed by a single CoordinatorData field."""

    entity_description: GridSensorDescription

    def __init__(
        self,
        coordinator: GridCoordinator,
        description: GridSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> str | float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
