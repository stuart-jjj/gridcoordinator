"""Coordinator helpers for the self-consumption handoff (no running HA required)."""

import asyncio
import math
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_ENTITY_VOLTX_CAPACITY,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator
from custom_components.grid_coordinator.models import SolaxMode

POWER_ENTITY = ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER]


class _States:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        return None if value is None else SimpleNamespace(state=value)


def _coord(values: dict[str, str] | None = None, **attrs):
    c = SimpleNamespace(
        hass=SimpleNamespace(states=_States(values or {})),
        _entry=SimpleNamespace(options={}, data={}),
        _voltx_power_ema=None,
        _voltx_power_ema_at=None,
        _sc_power_smoothing_seconds=60.0,
        **attrs,
    )
    c._opt = MethodType(GridCoordinator._opt, c)
    c._eid = MethodType(GridCoordinator._eid, c)
    c._read_voltx_power = MethodType(GridCoordinator._read_voltx_power, c)
    c._update_voltx_power_ema = MethodType(GridCoordinator._update_voltx_power_ema, c)
    return c


# ── Voltx power read + EMA ────────────────────────────────────────────────────

def test_read_voltx_power_numeric():
    assert _coord({POWER_ENTITY: "-2069"})._read_voltx_power() == -2069.0


@pytest.mark.parametrize("state", ["unavailable", "unknown", "", "not-a-number"])
def test_read_voltx_power_unreadable_is_none(state):
    assert _coord({POWER_ENTITY: state})._read_voltx_power() is None


def test_read_voltx_power_missing_entity_is_none():
    assert _coord({})._read_voltx_power() is None


def test_ema_seeds_then_smooths():
    c = _coord()
    assert c._update_voltx_power_ema(1000.0) == 1000.0
    second = c._update_voltx_power_ema(0.0)
    assert 0.0 < second <= 1000.0  # moved toward 0 but not all the way (tiny dt)


def test_ema_resets_when_sensor_unavailable():
    c = _coord()
    c._update_voltx_power_ema(500.0)
    assert c._update_voltx_power_ema(None) is None
    assert c._voltx_power_ema is None and c._voltx_power_ema_at is None
    # the next good reading re-seeds instead of blending with the stale value
    assert c._update_voltx_power_ema(-300.0) == -300.0


# ── Solax share extraction (regression: behaviour moved out of the tick) ──────

def _share_coord(values):
    c = _coord(values, _soc_balance_sensitivity=0.01, _soc_balance_deadband=5.0)
    c._solax_shares = MethodType(GridCoordinator._solax_shares, c)
    return c


def test_solax_shares_all_zero_when_solax_off():
    soc, s1, s2, e1, e2 = _share_coord({})._solax_shares(
        solax_on=False, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert math.isnan(soc)
    assert (s1, s2, e1, e2) == (0.0, 0.0, 0.0, 0.0)


def test_solax_shares_capacity_ratio_when_socs_balanced():
    values = {
        ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_CAPACITY]: "10",
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_CAPACITY]: "7",
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC]: "50",
    }
    soc, s1, s2, e1, e2 = _share_coord(values)._solax_shares(
        solax_on=True, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert soc == 50.0
    assert s1 == pytest.approx(7 / 17)
    assert s2 == pytest.approx(7 / 17)
    # far below the SOC-ceiling taper band, so effective == raw
    assert (e1, e2) == (pytest.approx(s1), pytest.approx(s2))


def test_solax_shares_zero_when_capacities_missing():
    values = {ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC]: "50"}
    _soc, s1, s2, e1, e2 = _share_coord(values)._solax_shares(
        solax_on=True, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert (s1, s2, e1, e2) == (0.0, 0.0, 0.0, 0.0)


# ── follow: fallback paths release Solax instead of commanding it ─────────────

def _follow(solax_enabled, solax_active, **kwargs):
    release = AsyncMock()
    c = SimpleNamespace(
        _solax_enabled=lambda: solax_enabled,
        _solax_active=solax_active,
        _async_enter_solax_self_consumption=release,
    )
    result = asyncio.run(GridCoordinator._async_solax_follow_voltx(c, **kwargs))
    return result, release


def test_follow_releases_solax_when_voltx_power_unreadable():
    result, release = _follow(True, True, solax_on=True, voltx_power=None, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_awaited_once()


def test_follow_releases_solax_when_its_control_is_off():
    result, release = _follow(True, True, solax_on=False, voltx_power=-1500.0, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_awaited_once()


def test_follow_does_not_touch_an_inactive_solax_when_unavailable():
    result, release = _follow(True, False, solax_on=True, voltx_power=None, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_not_awaited()
