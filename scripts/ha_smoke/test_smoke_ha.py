"""Smoke test of the self-consumption handoff against a REAL Home Assistant core.

NOT part of the bare-interpreter suite (`python3 -m pytest tests/`): it needs real
homeassistant + pytest-homeassistant-custom-component, so it lives here and is run by
`scripts/ha_smoke/run` inside Docker (see README.md in this directory).

Drives the real config entry setup, the real coordinator tick (_async_update_data) and
the real options flow.  Other integrations (iammeter, Voltx/Solax modbus, EMHASS) are
NOT loaded: their entities are seeded as plain states and hardware writes (number /
select / button services) are captured with mocked services.
"""

import pytest
import voluptuous_serialize
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_ENABLED,
    CONF_ENTITY_GRID_POWER,
    CONF_ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_EXPORT_DURATION,
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_TRIGGER,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_ENTITY_VOLTX_CAPACITY,
    CONF_ENTITY_VOLTX_CMD,
    CONF_ENTITY_VOLTX_CONTROL_ENABLE,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    CONF_ENTITY_VOLTX_WORK_MODE,
    CONF_SELF_CONSUMPTION_DEADBAND,
    DOMAIN,
    ENTITY_ID_DEFAULTS as D,
)
from custom_components.grid_coordinator.models import CoordinatorMode, SolaxMode

CONTROL_HELPER = "input_boolean.voltx_control_smoke"


@pytest.fixture(autouse=True)
def _enable_custom(enable_custom_integrations):
    yield


def _seed(hass: HomeAssistant, *, grid=0.0, mpc_grid=0.0, mpc_batt=-1247.0, power="-1200",
          voltx_soc=50.0, solax=False):
    s = hass.states.async_set
    s(D[CONF_ENTITY_GRID_POWER], str(grid))
    s(D[CONF_ENTITY_ENABLED], "on")
    s(D[CONF_ENTITY_MPC_GRID_POWER], str(mpc_grid))
    s(D[CONF_ENTITY_MPC_BATT_POWER], str(mpc_batt))
    s(D[CONF_ENTITY_VOLTX_SOC], str(voltx_soc))
    s(D[CONF_ENTITY_VOLTX_MAX_CHARGE], "5000")
    s(D[CONF_ENTITY_VOLTX_MAX_DISCHARGE], "5000")
    s(D[CONF_ENTITY_SOC_MIN], "20")
    s(D[CONF_ENTITY_SOC_MAX], "95")
    s(D[CONF_ENTITY_VOLTX_CMD], "0")
    s(D[CONF_ENTITY_VOLTX_WORK_MODE], "Custom")
    s(D[CONF_ENTITY_VOLTX_BATTERY_POWER], power)
    s(D[CONF_ENTITY_VOLTX_CAPACITY], "10")
    if solax:
        s(D[CONF_ENTITY_SOLAX_SOC], "50")
        s(D[CONF_ENTITY_SOLAX_CAPACITY], "7")
        s(D[CONF_ENTITY_SOLAX_SOC_MIN], "20")
        s(D[CONF_ENTITY_SOLAX_SOC_MAX], "95")
        s(D[CONF_ENTITY_SOLAX_RC_POWER_CONTROL], "Disabled")
        s(D[CONF_ENTITY_SOLAX_RC_ACTIVE_POWER], "0")
        s(D[CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION], "0")
        s(D[CONF_ENTITY_SOLAX_RC_TRIGGER], "unknown")
        s(D[CONF_ENTITY_SOLAX_EXPORT_DURATION], "Safe")


async def _setup(hass, *, solax=False, extra_data=None, **seed):
    _seed(hass, solax=solax, **seed)
    calls = {
        "number": async_mock_service(hass, "number", "set_value"),
        "select": async_mock_service(hass, "select", "select_option"),
        "button": async_mock_service(hass, "button", "press"),
    }
    data = {CONF_SELF_CONSUMPTION_DEADBAND: 200}
    if solax:
        data[CONF_ENTITY_SOLAX_SOC] = D[CONF_ENTITY_SOLAX_SOC]
    data.update(extra_data or {})
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entry.runtime_data, calls


async def _tick(hass, coordinator):
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    return coordinator.data


# ── setup / sensors ───────────────────────────────────────────────────────────

async def test_setup_loads_entry_sensors_and_service(hass):
    entry, coordinator, _ = await _setup(hass)
    assert entry.state.name == "LOADED"
    assert hass.states.get("sensor.grid_coordinator_mode") is not None
    assert hass.services.has_service(DOMAIN, "set_mode")


# ── the handoff ───────────────────────────────────────────────────────────────

async def test_charging_plan_on_plan_hands_off_to_native_self_consumption(hass):
    _, coordinator, calls = await _setup(hass)
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert coordinator._sc_state.active is True
    assert hass.states.get("sensor.grid_coordinator_mode").state == "self_consumption"
    options = [c.data["option"] for c in calls["select"] if c.data["entity_id"] == D[CONF_ENTITY_VOLTX_WORK_MODE]]
    assert "Self-consumption" in options


async def test_charging_shortfall_beyond_tolerance_stays_in_tracking(hass):
    _, coordinator, _ = await _setup(hass, power="-300")  # plan -1247, only -300 happening
    data = await _tick(hass, coordinator)
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION
    assert coordinator._sc_state.active is False


async def test_unreadable_power_falls_back_to_legacy_clause(hass):
    _, coordinator, _ = await _setup(hass, power="unavailable")
    data = await _tick(hass, coordinator)
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION  # legacy: a charge setpoint blocks it
    assert coordinator._voltx_power_ema is None
    hass.states.async_set(D[CONF_ENTITY_MPC_BATT_POWER], "0")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION  # legacy: idle plan is fine


@pytest.mark.parametrize("bad", ["nan", "inf"])
async def test_non_finite_power_is_treated_as_unreadable(hass, bad):
    _, coordinator, _ = await _setup(hass, power=bad)
    await _tick(hass, coordinator)  # must not raise
    assert coordinator._voltx_power_ema is None


async def test_import_limit_breach_exits_the_handoff_inside_the_dwell(hass):
    _, coordinator, _ = await _setup(hass)
    assert (await _tick(hass, coordinator)).mode == CoordinatorMode.SELF_CONSUMPTION
    hass.states.async_set(D[CONF_ENTITY_GRID_POWER], "13000")  # > 12000 W default import limit
    data = await _tick(hass, coordinator)
    assert coordinator._sc_state.active is False
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION


async def test_voltx_control_helper_off_exits_the_handoff(hass):
    hass.states.async_set(CONTROL_HELPER, "on")
    _, coordinator, _ = await _setup(
        hass, extra_data={CONF_ENTITY_VOLTX_CONTROL_ENABLE: CONTROL_HELPER}
    )
    assert (await _tick(hass, coordinator)).mode == CoordinatorMode.SELF_CONSUMPTION
    hass.states.async_set(CONTROL_HELPER, "off")
    await _tick(hass, coordinator)
    assert coordinator._sc_state.active is False


# ── Solax follows Voltx ───────────────────────────────────────────────────────

async def test_solax_follows_voltx_during_the_handoff(hass):
    share = 7 / 17  # capacity share at balanced SOCs
    _, coordinator, calls = await _setup(hass, solax=True, power="-2000")
    # setup ran tick 1: Solax idle before it, so it took share * Voltx alone
    first = -2000 * share
    assert coordinator.data.solax_mode == SolaxMode.FOLLOW_VOLTX
    assert coordinator.data.solax_command == pytest.approx(first, abs=2)
    # tick 2: Solax follows share of the COMBINED power (Voltx -2000 + Solax's last setpoint)
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert data.solax_mode == SolaxMode.FOLLOW_VOLTX
    assert data.solax_command == pytest.approx(share * (-2000 + first), abs=2)
    assert hass.states.get("sensor.grid_coordinator_solax_mode").state == "follow_voltx"
    writes = [c for c in calls["number"] if c.data["entity_id"] == D[CONF_ENTITY_SOLAX_RC_ACTIVE_POWER]]
    assert writes, "no Solax active-power write"
    # Solax's register sign is inverted (charge is +)
    assert int(writes[-1].data["value"]) == pytest.approx(-data.solax_command, abs=2)
    assert any(c.data["entity_id"] == D[CONF_ENTITY_SOLAX_RC_TRIGGER] for c in calls["button"])


async def test_solax_released_when_voltx_power_unreadable(hass):
    _, coordinator, _ = await _setup(hass, solax=True, mpc_batt=0.0, power="-2000")
    await _tick(hass, coordinator)  # handoff + follow
    assert coordinator._solax_active is True
    hass.states.async_set(D[CONF_ENTITY_VOLTX_BATTERY_POWER], "unavailable")
    data = await _tick(hass, coordinator)
    assert data.solax_mode != SolaxMode.FOLLOW_VOLTX
    assert coordinator._solax_active is False


# ── stale plan ────────────────────────────────────────────────────────────────

async def test_stale_plan_hands_off_even_with_a_pending_lockout(hass):
    import time as _time
    from unittest.mock import patch

    from custom_components.grid_coordinator.budget import ScState

    _, coordinator, _ = await _setup(hass, power="-300")  # shortfall -> tracking
    await _tick(hass, coordinator)
    assert coordinator._sc_state.active is False
    # pretend a shortfall exit just happened: 600 s re-entry lockout in force
    coordinator._sc_state = ScState(active=False, last_transition_at=_time.monotonic(), lockout_s=600.0)
    with patch("custom_components.grid_coordinator.coordinator._plan_age_minutes", return_value=45.0):
        data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.STALE_PLAN
    assert coordinator._sc_state.active is True


# ── config / options flow with the real schema + selectors ────────────────────

async def test_options_flow_exposes_and_saves_the_new_options(hass):
    entry, _, _ = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "init"
    # the frontend serialises the schema; this fails if a selector/default is malformed
    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    names = {f["name"] for f in fields}
    assert {"sc_battery_tolerance", "sc_min_dwell_seconds", "sc_power_smoothing_seconds"} <= names
    defaults = {f["name"]: f.get("default") for f in fields}
    assert (defaults["sc_battery_tolerance"], defaults["sc_min_dwell_seconds"],
            defaults["sc_power_smoothing_seconds"]) == (300, 120, 60)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sc_battery_tolerance": 450, "sc_min_dwell_seconds": 60, "sc_power_smoothing_seconds": 30}
    )
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "entities"
    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    ent_defaults = {f["name"]: f.get("default") for f in fields}
    assert ent_defaults["entity_voltx_battery_power"] == "sensor.voltx_battery_battery_power"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "solax"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["sc_battery_tolerance"] == 450
    assert entry.options["sc_min_dwell_seconds"] == 60
    assert entry.options["sc_power_smoothing_seconds"] == 30
    assert entry.options["entity_voltx_battery_power"] == "sensor.voltx_battery_battery_power"
