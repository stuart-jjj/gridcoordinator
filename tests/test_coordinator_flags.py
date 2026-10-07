"""Reading of the discharge-plan self-consumption handoff experiment option.

It is a plain boolean config option (not an entity/helper) so the coordinator can
never be installed without it: absent means the default, which is OFF and keeps the
legacy behaviour. Options override entry data, matching `_opt`.
"""

from types import MethodType, SimpleNamespace

from custom_components.grid_coordinator.const import (
    CONF_SC_DISCHARGE_HANDOFF,
    DEFAULT_SC_DISCHARGE_HANDOFF,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator


def _handoff(*, options: dict | None = None, data: dict | None = None) -> bool:
    coordinator = SimpleNamespace(
        _entry=SimpleNamespace(options=options or {}, data=data or {})
    )
    coordinator._opt = MethodType(GridCoordinator._opt, coordinator)
    return GridCoordinator._sc_discharge_handoff.fget(coordinator)


def test_default_is_off():
    assert DEFAULT_SC_DISCHARGE_HANDOFF is False
    assert _handoff() is False


def test_option_on():
    assert _handoff(options={CONF_SC_DISCHARGE_HANDOFF: True}) is True


def test_option_off():
    assert _handoff(options={CONF_SC_DISCHARGE_HANDOFF: False}) is False


def test_options_override_entry_data():
    assert (
        _handoff(
            options={CONF_SC_DISCHARGE_HANDOFF: False},
            data={CONF_SC_DISCHARGE_HANDOFF: True},
        )
        is False
    )
    assert (
        _handoff(
            options={CONF_SC_DISCHARGE_HANDOFF: True},
            data={CONF_SC_DISCHARGE_HANDOFF: False},
        )
        is True
    )


def test_falls_back_to_entry_data_when_no_option():
    assert _handoff(data={CONF_SC_DISCHARGE_HANDOFF: True}) is True



# ── self-consumption handoff tuning options ──────────────────────────────────

from custom_components.grid_coordinator.const import (  # noqa: E402
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_SC_BATTERY_TOLERANCE,
    CONF_SC_MIN_DWELL_SECONDS,
    CONF_SC_POWER_SMOOTHING_SECONDS,
    DEFAULT_SC_BATTERY_TOLERANCE,
    DEFAULT_SC_MIN_DWELL_SECONDS,
    DEFAULT_SC_POWER_SMOOTHING_SECONDS,
    ENTITY_ID_DEFAULTS,
)


def _prop(name: str, *, options: dict | None = None, data: dict | None = None):
    coordinator = SimpleNamespace(
        _entry=SimpleNamespace(options=options or {}, data=data or {})
    )
    coordinator._opt = MethodType(GridCoordinator._opt, coordinator)
    return getattr(GridCoordinator, name).fget(coordinator)


def test_sc_tuning_defaults():
    assert (
        DEFAULT_SC_BATTERY_TOLERANCE,
        DEFAULT_SC_MIN_DWELL_SECONDS,
        DEFAULT_SC_POWER_SMOOTHING_SECONDS,
    ) == (300, 120, 60)
    assert _prop("_sc_battery_tolerance") == 300.0
    assert _prop("_sc_min_dwell_seconds") == 120.0
    assert _prop("_sc_power_smoothing_seconds") == 60.0


def test_sc_tuning_options_override_defaults():
    options = {
        CONF_SC_BATTERY_TOLERANCE: 450,
        CONF_SC_MIN_DWELL_SECONDS: 30,
        CONF_SC_POWER_SMOOTHING_SECONDS: 0,
    }
    assert _prop("_sc_battery_tolerance", options=options) == 450.0
    assert _prop("_sc_min_dwell_seconds", options=options) == 30.0
    assert _prop("_sc_power_smoothing_seconds", options=options) == 0.0


def test_voltx_battery_power_entity_default():
    assert ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER] == "sensor.voltx_battery_battery_power"
