"""Diagnostic sensor platform for grid_coordinator."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant

from .coordinator import GridCoordinator
from .entity import GridCoordinatorEntity
from .models import CoordinatorData
from .price_window import decision_attributes

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import GridCoordinatorConfigEntry


@dataclass(frozen=True, kw_only=True)
class GridSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with a typed data accessor."""

    value_fn: Callable[[CoordinatorData], str | float | datetime | None]
    attrs_fn: Callable[[CoordinatorData], dict | None] | None = None


SENSOR_DESCRIPTIONS: tuple[GridSensorDescription, ...] = (
    GridSensorDescription(
        key="mode",
        name="Mode",
        icon="mdi:state-machine",
        value_fn=lambda d: str(d.mode),
    ),
    GridSensorDescription(
        key="mpc_batt_power",
        name="MPC Battery Power",
        icon="mdi:battery-arrow-up",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.mpc_batt_power,
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
    GridSensorDescription(
        key="solax_command",
        name="Solax Command",
        icon="mdi:battery-arrow-up",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda d: d.solax_command,
    ),
    GridSensorDescription(
        key="solax_mode",
        name="Solax Mode",
        icon="mdi:state-machine",
        value_fn=lambda d: str(d.solax_mode),
    ),
    GridSensorDescription(
        key="ev_current_limit",
        name="EV Current Limit",
        icon="mdi:ev-station",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=lambda d: d.ev_current_limit,
    ),
    # Price window.  The reason sensor's attributes hold the sentence and the working
    # (see price_window.decision_attributes); they are recorded, so a decision can be
    # checked afterwards against the forecast Amber Express showed at the time.
    GridSensorDescription(
        key="price_window_reason",
        name="Price Window Reason",
        icon="mdi:comment-question-outline",
        value_fn=lambda d: d.price_window.code if d.price_window else None,
        attrs_fn=lambda d: decision_attributes(d.price_window) if d.price_window else None,
    ),
    GridSensorDescription(
        key="price_window_end",
        name="Price Window End",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.price_window.window_end if d.price_window else None,
    ),
    GridSensorDescription(
        key="price_window_house_reserve",
        name="Price Window House Reserve",
        icon="mdi:home-battery",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: d.price_window.house_reserve_kwh if d.price_window and d.price_window.action else None,
    ),
    GridSensorDescription(
        key="price_window_surplus",
        name="Price Window Surplus",
        icon="mdi:battery-arrow-up-outline",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: d.price_window.surplus_kwh if d.price_window and d.price_window.action else None,
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
    def native_value(self) -> str | float | datetime | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is None or self.coordinator.data is None:
            return None
        return attrs_fn(self.coordinator.data)
