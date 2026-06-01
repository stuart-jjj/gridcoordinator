"""Simulated HA entities used in testing mode.

Each entity sets self.entity_id explicitly so the coordinator can reference
them by the SIM_ENTITY_* constants defined in const.py.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    SIM_ENTITY_ENABLED,
    SIM_ENTITY_GRID_POWER,
    SIM_ENTITY_MPC_GRID_POWER,
    SIM_ENTITY_SOC_MAX,
    SIM_ENTITY_SOC_MIN,
    SIM_ENTITY_VOLTX_CMD,
    SIM_ENTITY_VOLTX_MAX_CHARGE,
    SIM_ENTITY_VOLTX_MAX_DISCHARGE,
    SIM_ENTITY_VOLTX_SOC,
    SIM_ENTITY_VOLTX_WORK_MODE,
    VOLTX_WORK_MODE_CUSTOM,
)

_DEVICE = DeviceInfo(
    identifiers={(DOMAIN, DOMAIN)},
    name="Grid Coordinator",
    manufacturer="Custom",
    model="Phase 1 MVP",
)


class SimNumberEntity(NumberEntity):
    """Generic simulated number entity settable from the HA UI."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.BOX
    _attr_device_info = _DEVICE

    def __init__(
        self,
        entry_id: str,
        name: str,
        fixed_entity_id: str,
        min_val: float,
        max_val: float,
        step: float,
        unit: str,
        default: float,
        icon: str,
    ) -> None:
        self.entity_id = fixed_entity_id
        self._attr_unique_id = f"{entry_id}_{fixed_entity_id}"
        self._attr_name = name
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value = default
        self._attr_icon = icon

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()


class SimWorkModeSelect(SelectEntity):
    """Simulated Voltx work-mode select — accepts select.select_option service calls."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Sim Work Mode"
    _attr_icon = "mdi:cog-outline"
    _attr_options = [VOLTX_WORK_MODE_CUSTOM, "Self-consumption", "Auto", "Forced Charge", "Forced Discharge"]
    _attr_device_info = _DEVICE

    def __init__(self, entry_id: str) -> None:
        self.entity_id = SIM_ENTITY_VOLTX_WORK_MODE
        self._attr_unique_id = f"{entry_id}_{SIM_ENTITY_VOLTX_WORK_MODE}"
        self._attr_current_option = "Auto"

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()


class SimEnabledSwitch(SwitchEntity):
    """Simulated enable/disable gate — starts off so the coordinator is dormant until switched on."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Sim Enabled"
    _attr_icon = "mdi:power"
    _attr_device_info = _DEVICE

    def __init__(self, entry_id: str) -> None:
        self.entity_id = SIM_ENTITY_ENABLED
        self._attr_unique_id = f"{entry_id}_{SIM_ENTITY_ENABLED}"
        self._attr_is_on = False

    async def async_turn_on(self, **kwargs: object) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()


# ── Factory helpers ───────────────────────────────────────────────────────────


def build_sim_number_entities(entry_id: str) -> list[SimNumberEntity]:
    """Return all simulated number entities for the given config entry."""
    return [
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim Grid Power",
            fixed_entity_id=SIM_ENTITY_GRID_POWER,
            min_val=-20000, max_val=20000, step=10,
            unit="W", default=0.0,
            icon="mdi:transmission-tower",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim MPC Grid Power",
            fixed_entity_id=SIM_ENTITY_MPC_GRID_POWER,
            min_val=-20000, max_val=20000, step=10,
            unit="W", default=0.0,
            icon="mdi:chart-timeline-variant",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim Battery SOC",
            fixed_entity_id=SIM_ENTITY_VOLTX_SOC,
            min_val=0, max_val=100, step=1,
            unit="%", default=50.0,
            icon="mdi:battery-50",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim Max Charge",
            fixed_entity_id=SIM_ENTITY_VOLTX_MAX_CHARGE,
            min_val=0, max_val=10000, step=100,
            unit="W", default=5000.0,
            icon="mdi:battery-arrow-down",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim Max Discharge",
            fixed_entity_id=SIM_ENTITY_VOLTX_MAX_DISCHARGE,
            min_val=0, max_val=10000, step=100,
            unit="W", default=5000.0,
            icon="mdi:battery-arrow-up",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim SOC Min",
            fixed_entity_id=SIM_ENTITY_SOC_MIN,
            min_val=0, max_val=100, step=1,
            unit="%", default=20.0,
            icon="mdi:battery-low",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim SOC Max",
            fixed_entity_id=SIM_ENTITY_SOC_MAX,
            min_val=0, max_val=100, step=1,
            unit="%", default=95.0,
            icon="mdi:battery-high",
        ),
        SimNumberEntity(
            entry_id=entry_id,
            name="Sim Battery Command",
            fixed_entity_id=SIM_ENTITY_VOLTX_CMD,
            min_val=-10000, max_val=10000, step=1,
            unit="W", default=0.0,
            icon="mdi:battery-charging",
        ),
    ]
