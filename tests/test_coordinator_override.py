"""Manual-override tick: what the coordinator reports when the SOC check blocks it.

2026-10-09 incident: an automation set force_export while Voltx SOC (18%) sat at the
EMHASS floor (18%).  The tick wrote 0 W but still reported ``override_force_export``,
so nothing in the diagnostic sensors said the override was doing nothing.
"""

import asyncio
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator
from custom_components.grid_coordinator.models import CoordinatorMode


class _States:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        return None if value is None else SimpleNamespace(state=value)


def _tick(*, mode, soc, soc_min=20, soc_max=95, bypass=False, power=9000.0, grid=650.0):
    """Run one override tick on a real GridCoordinator method; return (data, write_mock)."""
    states = {
        ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_SOC]: str(soc),
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOC_MIN]: str(soc_min),
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOC_MAX]: str(soc_max),
        ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_MAX_CHARGE]: "9600",
        ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_MAX_DISCHARGE]: "9600",
    }
    c = SimpleNamespace(
        hass=SimpleNamespace(states=_States(states)),
        _entry=SimpleNamespace(options={}, data={}),
        _override_mode=mode,
        _override_power=power,
        _override_bypass_soc=bypass,
        _prev_cmd=0.0,
        _import_limit=14000.0,
        _export_limit=10000.0,
        _solax_active=False,
        _async_write_voltx=AsyncMock(),
    )
    c._opt = MethodType(GridCoordinator._opt, c)
    c._eid = MethodType(GridCoordinator._eid, c)
    c._solax_enabled = MethodType(GridCoordinator._solax_enabled, c)
    handle = MethodType(GridCoordinator._async_handle_override, c)
    data = asyncio.run(handle(grid_actual=grid, plan_age=0.0))
    return data, c._async_write_voltx


def test_force_export_at_soc_floor_reports_soc_floor():
    # The incident: SOC 18 == floor 18, no bypass.  Blocked, and it must say so.
    data, write = _tick(mode="force_export", soc=18, soc_min=18)
    assert data.mode == CoordinatorMode.SOC_FLOOR
    assert data.voltx_command == 0
    write.assert_awaited_once_with(0)


def test_force_export_blocked_still_reports_the_requested_override():
    # The requested override stays visible so the mode alone doesn't hide the intent.
    data, _ = _tick(mode="force_export", soc=18, soc_min=18)
    assert data.override_mode == "force_export"


def test_force_export_with_bypass_ignores_the_floor():
    data, write = _tick(mode="force_export", soc=18, soc_min=18, bypass=True)
    assert data.mode == CoordinatorMode.OVERRIDE_FORCE_EXPORT
    assert data.voltx_command == 9000
    write.assert_awaited_once_with(9000)


def test_force_export_above_floor_exports():
    data, write = _tick(mode="force_export", soc=25, soc_min=20)
    assert data.mode == CoordinatorMode.OVERRIDE_FORCE_EXPORT
    assert data.voltx_command == 9000
    write.assert_awaited_once_with(9000)


def test_force_charge_at_soc_ceiling_reports_soc_ceiling():
    data, write = _tick(mode="force_charge", soc=95, soc_max=95)
    assert data.mode == CoordinatorMode.SOC_CEILING
    assert data.voltx_command == 0
    assert data.override_mode == "force_charge"
    write.assert_awaited_once_with(0)


def test_force_charge_with_bypass_ignores_the_ceiling():
    data, write = _tick(mode="force_charge", soc=95, soc_max=95, bypass=True)
    assert data.mode == CoordinatorMode.OVERRIDE_FORCE_CHARGE
    assert data.voltx_command == -9000
    write.assert_awaited_once_with(-9000)


@pytest.mark.parametrize("soc", [19, 20])
def test_force_export_blocked_just_below_and_at_floor(soc):
    # Floor is 20: SOC 20 is blocked (<=), and SOC 19 is blocked too.
    data, _ = _tick(mode="force_export", soc=soc, soc_min=20)
    assert data.mode == CoordinatorMode.SOC_FLOOR
