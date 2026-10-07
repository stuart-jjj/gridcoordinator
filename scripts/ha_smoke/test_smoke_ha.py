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
    ENTITY_EV_CHARGER,
    ENTITY_ID_DEFAULTS as D,
)
from custom_components.grid_coordinator.models import CoordinatorMode, SolaxMode

CONTROL_HELPER = "input_boolean.voltx_control_smoke"


@pytest.fixture(autouse=True)
def _enable_custom(enable_custom_integrations):
    yield


def _seed(hass: HomeAssistant, *, grid=0.0, mpc_grid=0.0, mpc_batt=-1247.0, power="-1200",
          voltx_soc=50.0, solax=False, ev_power=None):
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
    if ev_power is not None:
        s(ENTITY_EV_CHARGER, str(ev_power))
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


async def _setup(hass, *, solax=False, extra_data=None, extra_options=None, **seed):
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
    entry = MockConfigEntry(domain=DOMAIN, data=data, options=extra_options or {}, unique_id=DOMAIN)
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


async def test_charging_shortfall_stays_in_the_handoff(hass):
    # Plan -1247 W but only -300 W is happening (a cloud): tracking would buy the shortfall
    # from the grid, native self-consumption never imports to charge (live 2026-10-07 13:36).
    _, coordinator, _ = await _setup(hass, power="-300")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert coordinator._sc_state.active is True


async def test_handoff_still_reports_the_emhass_battery_setpoint(hass):
    # Voltx is not commanded in the handoff, but the diagnostic sensor must still show the
    # EMHASS setpoint in force (it used to read 0 W, making the plan look ignored).
    _, coordinator, _ = await _setup(hass, mpc_batt=-2884.0, power="-506")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert data.voltx_command == 0.0
    assert data.mpc_batt_power == -2884.0
    assert float(hass.states.get("sensor.grid_coordinator_mpc_battery_power").state) == -2884.0


@pytest.mark.parametrize("plan", [-1247.0, 0.0, 800.0])
async def test_any_battery_plan_hands_off_at_zero_target(hass, plan):
    _, coordinator, _ = await _setup(hass, mpc_batt=plan, power="1500")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION


@pytest.mark.parametrize(
    ("mpc_grid", "mpc_batt", "direction"),
    [
        (-4000.0, 5000.0, 1),   # price-driven discharge to the grid: export target
        (3000.0, -5000.0, -1),  # sustained charge beyond the PV surplus: import target
    ],
)
async def test_plan_that_moves_the_grid_overrides_the_handoff(hass, mpc_grid, mpc_batt, direction):
    # The handoff is keyed off the grid target alone.  A plan that exports (discharge for a
    # price spike) or imports (charge beyond the PV surplus) has a target outside the
    # deadband, so Voltx is commanded along the plan instead of being left to native
    # self-consumption.  The first-tick value is ramp-limited, so assert direction only.
    _, coordinator, _ = await _setup(hass, mpc_grid=mpc_grid, mpc_batt=mpc_batt, power="0")
    data = await _tick(hass, coordinator)
    assert coordinator._sc_state.active is False
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION
    assert data.grid_target == mpc_grid
    assert data.mpc_batt_power == mpc_batt
    assert data.voltx_command * direction > 0


@pytest.mark.parametrize("bad", ["unavailable", "nan", "inf"])
async def test_unreadable_power_does_not_affect_the_handoff(hass, bad):
    # The decision no longer needs the Voltx power; only the Solax follow does, and it
    # falls back to releasing Solax.  Must not raise.
    _, coordinator, _ = await _setup(hass, power=bad)
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert coordinator._read_voltx_power() is None


async def test_ev_charging_keeps_the_battery_out_of_the_handoff(hass):
    # EMHASS does not know about the (Amber-managed) EV.  Native self-consumption would
    # drain the battery into the car; tracking with tier 2 off serves the EV from the grid.
    _, coordinator, _ = await _setup(hass, ev_power="3500")
    data = await _tick(hass, coordinator)
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION
    assert coordinator._sc_state.active is False


async def test_idle_ev_charger_does_not_block_the_handoff(hass):
    _, coordinator, _ = await _setup(hass, ev_power="20")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION


async def test_ev_starting_inside_the_handoff_exits_immediately(hass):
    _, coordinator, _ = await _setup(hass, ev_power="20")
    assert (await _tick(hass, coordinator)).mode == CoordinatorMode.SELF_CONSUMPTION
    hass.states.async_set(ENTITY_EV_CHARGER, "3500")
    data = await _tick(hass, coordinator)  # inside the 120 s dwell: the EV bypasses it
    assert data.mode != CoordinatorMode.SELF_CONSUMPTION


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

    _, coordinator, _ = await _setup(hass, mpc_grid=-3000.0)  # planned export -> tracking
    await _tick(hass, coordinator)
    assert coordinator._sc_state.active is False
    # pretend the handoff just ended: the 120 s dwell lockout is in force
    coordinator._sc_state = ScState(active=False, last_transition_at=_time.monotonic())
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
    assert "sc_min_dwell_seconds" in names
    # options removed in 2026.10.2 must not be offered any more
    assert not ({"sc_battery_tolerance", "sc_discharge_handoff", "sc_power_smoothing_seconds"} & names)
    defaults = {f["name"]: f.get("default") for f in fields}
    assert defaults["sc_min_dwell_seconds"] == 120

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sc_min_dwell_seconds": 60}
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
    assert entry.options["sc_min_dwell_seconds"] == 60
    assert not ({"sc_battery_tolerance", "sc_discharge_handoff", "sc_power_smoothing_seconds"} & set(entry.options))
    assert entry.options["entity_voltx_battery_power"] == "sensor.voltx_battery_battery_power"


async def test_entry_carrying_removed_option_keys_still_works(hass):
    # A real 2026.10.1 -> 2026.10.2 upgrade keeps the keys in entry.options (saved by the old
    # options flow); they are ignored, and re-saving the options flow drops them.
    stale = {"sc_battery_tolerance": 450, "sc_discharge_handoff": True, "sc_power_smoothing_seconds": 30}
    entry, coordinator, _ = await _setup(hass, extra_options=stale)
    assert entry.state.name == "LOADED"
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "init"
    for _ in range(3):  # init -> entities -> solax -> create
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert not (set(stale) & set(entry.options))
