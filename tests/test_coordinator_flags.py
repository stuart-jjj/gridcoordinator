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
    coordinator = SimpleNamespace(_entry=SimpleNamespace(options=options or {}, data=data or {}))
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
    assert _handoff(options={CONF_SC_DISCHARGE_HANDOFF: False}, data={CONF_SC_DISCHARGE_HANDOFF: True}) is False
    assert _handoff(options={CONF_SC_DISCHARGE_HANDOFF: True}, data={CONF_SC_DISCHARGE_HANDOFF: False}) is True


def test_falls_back_to_entry_data_when_no_option():
    assert _handoff(data={CONF_SC_DISCHARGE_HANDOFF: True}) is True
