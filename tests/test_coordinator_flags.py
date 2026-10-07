"""Reading of the self-consumption handoff option.

Entry options override entry data, matching `GridCoordinator._opt`.  An absent key means
the default.  The charging-shortfall tolerance, the discharge-plan flag and the power
smoothing option were removed in 2026.10.2 (see the 2026-10-07 drop-shortfall spec).
"""

from types import MethodType, SimpleNamespace

import custom_components.grid_coordinator.const as const
from custom_components.grid_coordinator.const import (
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_SC_MIN_DWELL_SECONDS,
    DEFAULT_SC_MIN_DWELL_SECONDS,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator


def _dwell(*, options: dict | None = None, data: dict | None = None) -> float:
    coordinator = SimpleNamespace(
        _entry=SimpleNamespace(options=options or {}, data=data or {})
    )
    coordinator._opt = MethodType(GridCoordinator._opt, coordinator)
    return GridCoordinator._sc_min_dwell_seconds.fget(coordinator)


def test_dwell_default():
    assert DEFAULT_SC_MIN_DWELL_SECONDS == 120
    assert _dwell() == 120.0


def test_dwell_option_overrides_default():
    assert _dwell(options={CONF_SC_MIN_DWELL_SECONDS: 30}) == 30.0


def test_options_override_entry_data():
    assert _dwell(options={CONF_SC_MIN_DWELL_SECONDS: 30}, data={CONF_SC_MIN_DWELL_SECONDS: 300}) == 30.0


def test_falls_back_to_entry_data_when_no_option():
    assert _dwell(data={CONF_SC_MIN_DWELL_SECONDS: 300}) == 300.0


def test_voltx_battery_power_entity_default():
    assert ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER] == "sensor.voltx_battery_battery_power"


def test_removed_options_are_not_exposed():
    for name in (
        "CONF_SC_BATTERY_TOLERANCE",
        "CONF_SC_DISCHARGE_HANDOFF",
        "CONF_SC_POWER_SMOOTHING_SECONDS",
        "DEFAULT_SC_BATTERY_TOLERANCE",
        "DEFAULT_SC_DISCHARGE_HANDOFF",
        "DEFAULT_SC_POWER_SMOOTHING_SECONDS",
    ):
        assert not hasattr(const, name), name
