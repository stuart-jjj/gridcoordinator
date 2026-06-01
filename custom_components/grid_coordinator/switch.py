"""Switch platform — config switches and simulated entities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    CONF_MPC_SIGN_INVERTED,
    CONF_TEST_MODE,
    DEFAULT_MPC_SIGN_INVERTED,
    DOMAIN,
)
from .simulated import SimEnabledSwitch

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import GridCoordinatorConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: GridCoordinatorConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the MPC sign switch (always) and sim enabled switch (test mode only)."""
    entities: list[SwitchEntity] = [MpcSignInvertedSwitch(entry)]

    test_mode = entry.options.get(CONF_TEST_MODE, entry.data.get(CONF_TEST_MODE, False))
    if test_mode:
        entities.append(SimEnabledSwitch(entry.entry_id))

    async_add_entities(entities)


class MpcSignInvertedSwitch(SwitchEntity):
    """Toggle the EMHASS injection sign convention from the device card.

    ON  — mpc_grid_power positive = exporting to grid (standard EMHASS convention).
          The coordinator negates the value before using it.
    OFF — mpc_grid_power positive = importing from grid (same convention as the
          coordinator). No negation applied.

    Toggling saves to config options and reloads the integration (~2 s).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "EMHASS sign inverted"
    _attr_icon = "mdi:swap-vertical"

    def __init__(self, entry: GridCoordinatorConfigEntry) -> None:
        self.entity_id = "switch.grid_coordinator_emhass_sign_inverted"
        self._attr_unique_id = f"{entry.entry_id}_mpc_sign_inverted"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, DOMAIN)},
            name="Grid Coordinator",
            manufacturer="Custom",
            model="Phase 1 MVP",
        )
        self._entry = entry

    @property
    def is_on(self) -> bool:
        if CONF_MPC_SIGN_INVERTED in self._entry.options:
            return bool(self._entry.options[CONF_MPC_SIGN_INVERTED])
        return bool(self._entry.data.get(CONF_MPC_SIGN_INVERTED, DEFAULT_MPC_SIGN_INVERTED))

    async def async_turn_on(self, **_kwargs: object) -> None:
        self._save(value=True)

    async def async_turn_off(self, **_kwargs: object) -> None:
        self._save(value=False)

    def _save(self, *, value: bool) -> None:
        new_options = {**self._entry.options, CONF_MPC_SIGN_INVERTED: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
