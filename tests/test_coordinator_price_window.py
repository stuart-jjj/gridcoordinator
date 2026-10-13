"""Price window wiring in the coordinator tick (no running HA required).

A real GridCoordinator runs against a dict of fake states; the inverter writes are
mocks.  Each test sets up prices and batteries, runs ticks, and checks what was
commanded and reported.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_ENABLED,
    CONF_ENTITY_EV_CHARGER,
    CONF_ENTITY_GRID_POWER,
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_PW_ENABLE,
    CONF_ENTITY_PW_FEED_IN,
    CONF_ENTITY_PW_GENERAL,
    CONF_ENTITY_PW_HOUSE_LOAD,
    CONF_ENTITY_PW_SHADOW,
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_ENTITY_VOLTX_CAPACITY,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    CONF_PW_MAX_CHARGE_BUY_PRICE,
    CONF_PW_RESERVE_UNTIL,
    CONF_PW_SPIKE_THRESHOLD,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator
from custom_components.grid_coordinator.models import CoordinatorMode, SolaxMode

E = ENTITY_ID_DEFAULTS
FEED = "sensor.amber_express_home_feed_in_price"
GENERAL = "sensor.amber_express_home_general_price"
LOAD = "sensor.house_load_expected"
ENABLE = "input_boolean.price_window_enabled"
SHADOW = "input_boolean.price_window_shadow"
EV = "sensor.ev_charger_power"


class _States:
    def __init__(self) -> None:
        self.values: dict[str, SimpleNamespace] = {}

    def set(self, entity_id, state, attributes=None, age_s=0.0):
        self.values[entity_id] = SimpleNamespace(
            state=str(state),
            attributes=attributes or {},
            last_updated=datetime.now(UTC) - timedelta(seconds=age_s),
        )

    def get(self, entity_id):
        return self.values.get(entity_id)


def _forecast(prices, *, confirmed_first=True, high=None):
    """detailedForecast for 5-minute intervals starting at the current one."""
    now = datetime.now(UTC)
    start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    entries = []
    for index, price in enumerate(prices):
        begin = start + timedelta(minutes=5 * index)
        estimate = not (confirmed_first and index == 0)
        entry = {
            "per_kwh": price,
            "start_time": (begin + timedelta(seconds=1)).isoformat(),
            "end_time": (begin + timedelta(minutes=5)).isoformat(),
            "spike_status": "none",
            "estimate": estimate,
        }
        if estimate:
            entry["advanced_price_predicted"] = {
                "low": price / 2, "predicted": price, "high": price if high is None else high,
            }
        entries.append(entry)
    return {"detailedForecast": entries}


class Rig:
    """A GridCoordinator on fake states, with the inverter writes mocked."""

    def __init__(self, *, options=None, sell=(10.48, 0.64, 10.0), buy=None, high=None,
                 confirmed_first=True, shadow="off", solax=True):
        self.states = _States()
        s = self.states.set
        s(E[CONF_ENTITY_ENABLED], "on")
        s(E[CONF_ENTITY_GRID_POWER], 800)
        s(E[CONF_ENTITY_MPC_GRID_POWER], 0)
        s(E[CONF_ENTITY_MPC_BATT_POWER], 800)
        s(E[CONF_ENTITY_VOLTX_SOC], 80)
        s(E[CONF_ENTITY_SOC_MIN], 20)
        s(E[CONF_ENTITY_SOC_MAX], 95)
        s(E[CONF_ENTITY_VOLTX_CAPACITY], 50)
        s(E[CONF_ENTITY_VOLTX_MAX_CHARGE], 9600)
        s(E[CONF_ENTITY_VOLTX_MAX_DISCHARGE], 10000)
        s(E[CONF_ENTITY_VOLTX_BATTERY_POWER], 0)
        s(E[CONF_ENTITY_SOLAX_SOC], 80)
        s(E[CONF_ENTITY_SOLAX_SOC_MIN], 20)
        s(E[CONF_ENTITY_SOLAX_SOC_MAX], 95)
        s(E[CONF_ENTITY_SOLAX_CAPACITY], 10)
        s(LOAD, 1000)
        s(ENABLE, "on")
        s(SHADOW, shadow)
        s(EV, 0)
        self.prices(sell, buy=buy, high=high, confirmed_first=confirmed_first)
        opts = {
            CONF_ENTITY_PW_ENABLE: ENABLE,
            CONF_ENTITY_PW_SHADOW: SHADOW,
            CONF_ENTITY_PW_FEED_IN: FEED,
            CONF_ENTITY_PW_GENERAL: GENERAL,
            CONF_ENTITY_PW_HOUSE_LOAD: LOAD,
            CONF_ENTITY_EV_CHARGER: EV,
            CONF_ENTITY_MON_LOAD_1: "",
            CONF_ENTITY_SOLAX_SOC: E[CONF_ENTITY_SOLAX_SOC] if solax else "",
        }
        opts.update(options or {})
        hass = SimpleNamespace(
            states=self.states,
            config=SimpleNamespace(time_zone="Australia/Sydney"),
            services=SimpleNamespace(async_call=AsyncMock()),
        )
        # The real DataUpdateCoordinator needs a running Home Assistant; none of what
        # it sets up is used by a tick, so skip it (the bare-interpreter stub is a no-op).
        with patch.object(DataUpdateCoordinator, "__init__", lambda self, *a, **k: None):
            c = GridCoordinator(hass, SimpleNamespace(options=opts, data={}))
        c.hass = hass
        self.voltx = c._async_write_voltx = AsyncMock()
        self.native = c._async_enter_self_consumption = AsyncMock()
        c._async_set_work_mode = AsyncMock(return_value=False)
        c._async_enter_solax_self_consumption = AsyncMock()
        c._async_release_ev_throttle = AsyncMock()
        c._async_ev_emergency_throttle = AsyncMock(return_value=(None, False))

        async def write_solax(command):
            c._solax_last_written_cmd = command
            c._solax_active = command != 0.0

        self.solax = c._async_write_solax = AsyncMock(side_effect=write_solax)
        self.c = c
        self._loop = asyncio.new_event_loop()

    def prices(self, sell, *, buy=None, high=None, confirmed_first=True, age_s=0.0):
        buy = [p + 0.11 for p in sell] if buy is None else buy
        self.states.set(FEED, sell[0], _forecast(sell, confirmed_first=confirmed_first, high=high), age_s)
        self.states.set(GENERAL, buy[0], _forecast(buy, confirmed_first=confirmed_first, high=high), age_s)

    def tick(self):
        # A private loop: asyncio.run() leaves the thread with no current event loop on
        # Python 3.14, which breaks later tests when HA's pytest plugins are installed.
        return self._loop.run_until_complete(self.c._async_update_data())

    def settle(self, data):
        """Pretend the inverters did what they were told: move the grid reading."""
        house = 800.0
        self.states.set(E[CONF_ENTITY_GRID_POWER], house - data.voltx_command - data.solax_command)
        self.states.set(E[CONF_ENTITY_VOLTX_BATTERY_POWER], data.voltx_command)


# ── who is in control ─────────────────────────────────────────────────────────


def test_no_window_follows_emhass():
    rig = Rig(sell=(0.33, 0.31, 0.30))
    data = rig.tick()
    assert data.price_window.code == "no_window"
    assert data.grid_target == 0
    assert data.mode in (CoordinatorMode.SELF_CONSUMPTION, CoordinatorMode.EMHASS_TRACKING)


def test_shadow_reports_but_leaves_the_grid_target_alone():
    rig = Rig(shadow="on")
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert data.price_window.shadow is True
    assert data.price_window.would_command_w == -10000
    assert data.grid_target == 0  # the EMHASS target
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    rig.voltx.assert_not_awaited()


def test_shadow_is_on_when_no_shadow_entity_is_configured():
    rig = Rig(options={CONF_ENTITY_PW_SHADOW: ""})
    assert rig.tick().price_window.shadow is True


def test_shadow_is_on_when_the_shadow_entity_is_unavailable():
    rig = Rig(shadow="unavailable")
    assert rig.tick().price_window.shadow is True


def test_manual_override_still_wins():
    rig = Rig()
    rig.c.set_override("hold_soc")
    data = rig.tick()
    assert data.mode == CoordinatorMode.OVERRIDE_HOLD_SOC
    assert data.override_mode == "hold_soc"
    assert data.price_window.code == "export_allocated"  # still reported


def test_disabled_gate_still_wins():
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_ENABLED], "off")
    data = rig.tick()
    assert data.mode == CoordinatorMode.DISABLED
    rig.voltx.assert_not_awaited()


def test_switched_off_reports_disabled():
    rig = Rig()
    rig.states.set(ENABLE, "off")
    data = rig.tick()
    assert data.price_window.code == "disabled"
    assert data.grid_target == 0


@pytest.mark.parametrize("missing", [CONF_ENTITY_PW_ENABLE, CONF_ENTITY_PW_FEED_IN,
                                     CONF_ENTITY_PW_GENERAL, CONF_ENTITY_PW_HOUSE_LOAD])
def test_unconfigured_reports_disabled(missing):
    assert Rig(options={missing: ""}).tick().price_window.code == "disabled"


# ── stale data stands down to EMHASS ──────────────────────────────────────────


def _stale_cases():
    def unavailable(rig):
        rig.states.set(FEED, "unavailable")

    def missing_attribute(rig):
        rig.states.set(GENERAL, 0.75, {})

    def old(rig):
        rig.prices((10.48, 0.64, 10.0), age_s=11 * 60)

    def gone(rig):
        del rig.states.values[FEED]

    def no_load(rig):
        rig.states.set(LOAD, "unknown")

    def garbage(rig):
        rig.states.set(FEED, 10.48, {"detailedForecast": [{"start_time": 5}, "x", None]})

    return [unavailable, missing_attribute, old, gone, no_load, garbage]


@pytest.mark.parametrize("breakage", _stale_cases(), ids=lambda f: f.__name__)
def test_stale_amber_express_data_falls_through_to_tracking(breakage):
    rig = Rig()
    breakage(rig)
    data = rig.tick()
    assert data.price_window.code == "data_stale"
    assert data.price_window.action is None
    assert data.grid_target == 0  # the EMHASS target, not the price window's


def test_data_ten_minutes_old_is_still_used():
    rig = Rig()
    rig.prices((10.48, 0.64, 10.0), age_s=9 * 60)
    assert rig.tick().price_window.code == "export_allocated"


# ── settings ──────────────────────────────────────────────────────────────────


def test_settings_accept_a_literal_or_a_helper_entity():
    # 64c now with nothing above $1 ahead: no window at the default threshold...
    rig = Rig(sell=(0.64, 0.70, 0.70))
    assert rig.tick().price_window.code == "no_window"
    # ...a literal 0.5 opens one...
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "0.5"})
    assert rig.tick().price_window.action == "export"
    # ...and so does a helper entity holding 0.5.
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "input_number.spike"})
    rig.states.set("input_number.spike", 0.5)
    assert rig.tick().price_window.action == "export"


def test_unreadable_setting_helper_falls_back_to_the_default():
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "input_number.spike"})
    rig.states.set("input_number.spike", "unavailable")
    assert rig.tick().price_window.code == "no_window"


@pytest.mark.parametrize("setting, state", [
    ("06:30", None),
    ("input_datetime.reserve_until", "06:30:00"),
    ("input_datetime.reserve_until", "2026-10-14 06:30:00"),
])
def test_reserve_until_accepts_a_time_or_an_input_datetime(setting, state):
    rig = Rig(options={CONF_PW_RESERVE_UNTIL: setting})
    if state is not None:
        rig.states.set(setting, state)
    decision = rig.tick().price_window
    assert decision.reserve_basis == "clock_time"
    assert (decision.reserve_horizon.hour, decision.reserve_horizon.minute) == (6, 30)
    assert str(decision.reserve_horizon.tzinfo) == "Australia/Sydney"


def test_unreadable_reserve_until_falls_back_to_the_margin():
    rig = Rig(options={CONF_PW_RESERVE_UNTIL: "input_datetime.missing"})
    assert rig.tick().price_window.reserve_basis == "forecast_margin"


# ── logging ───────────────────────────────────────────────────────────────────


def test_each_change_of_reason_code_is_logged_once(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="custom_components.grid_coordinator"):
        rig.tick()
        rig.tick()
        rig.prices((0.17, 10.0, 10.0), high=12.0)
        rig.tick()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("price window:")]
    assert len(lines) == 2
    assert lines[0].startswith("price window: export_allocated — Exporting: $10.48 now")
    assert lines[1].startswith("price window: hold_below_min_price")


# ── export ────────────────────────────────────────────────────────────────────


def test_export_commands_both_batteries_on_the_first_tick():
    rig = Rig()
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert data.grid_target == -10000
    # 800 W of house plus 10 kW of export, split by capacity: no ramp from zero.
    assert data.voltx_command == 9000
    assert data.solax_command == 1800
    assert data.solax_mode == SolaxMode.FORCE_DISCHARGE
    assert data.mode == CoordinatorMode.PRICE_WINDOW_EXPORT
    rig.voltx.assert_awaited_once_with(9000)
    rig.solax.assert_awaited_once_with(1800)


def test_export_holds_the_limit_once_there():
    rig = Rig()
    rig.settle(rig.tick())
    data = rig.tick()
    assert (data.voltx_command, data.solax_command) == (9000, 1800)
    assert data.grid_actual == -10000


def test_export_without_solax_uses_voltx_alone():
    rig = Rig(solax=False)
    data = rig.tick()
    assert data.voltx_command == 10000  # all it has; 10.8 kW would reach the limit
    assert data.solax_command == 0


def test_binding_constraint_is_still_the_reported_mode():
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 20)  # Voltx at its floor
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 90)
    data = rig.tick()
    assert data.price_window.action == "export"
    assert data.mode == CoordinatorMode.SOC_FLOOR
    assert data.voltx_command == 0
    assert data.solax_command == 3000  # Solax takes what it can


# ── hold ──────────────────────────────────────────────────────────────────────


def test_hold_hands_the_house_to_native_self_consumption():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    data = rig.tick()
    assert data.price_window.code == "hold_below_min_price"
    assert data.grid_target == 0
    assert data.mode == CoordinatorMode.PRICE_WINDOW_HOLD
    rig.native.assert_awaited()
    rig.voltx.assert_not_awaited()


def test_spike_after_hold_exports_at_once_despite_the_handoff_dwell():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    rig.tick()  # hold: handoff starts, 120 s dwell begins
    rig.states.set(E[CONF_ENTITY_GRID_POWER], 0)
    rig.states.set(E[CONF_ENTITY_VOLTX_BATTERY_POWER], 800)  # Voltx carrying the house
    rig.prices((10.48, 0.64, 10.0))
    data = rig.tick()
    assert data.price_window.action == "export"
    assert data.voltx_command == 9000 and data.solax_command == 1800


def test_hold_while_the_ev_charges_keeps_the_house_off_the_grid():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    rig.states.set(EV, 7000)
    rig.states.set(E[CONF_ENTITY_GRID_POWER], 7800)  # 7 kW car + 800 W house
    data = rig.tick()
    assert data.price_window.action == "hold"
    assert data.grid_target == 7000
    assert data.voltx_command + data.solax_command == 800
    assert data.mode == CoordinatorMode.PRICE_WINDOW_HOLD


# ── charge ────────────────────────────────────────────────────────────────────


def _short_rig(**kwargs):
    """Cheap now, spikes predicted ahead, batteries just above their floors."""
    rig = Rig(sell=(0.17, 9.80, 10.20), **kwargs)
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 21)
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 21)
    return rig


def test_charge_fills_the_import_limit_on_the_first_tick():
    rig = _short_rig()
    data = rig.tick()
    assert data.price_window.code == "charge_shortfall"
    assert data.grid_target == 12000
    # 12 kW import limit less the 800 W house, split by capacity.
    assert data.voltx_command + data.solax_command == pytest.approx(-11200, abs=2)
    assert data.voltx_command == pytest.approx(-9333, abs=2)
    assert data.mode == CoordinatorMode.PRICE_WINDOW_CHARGE


def test_charge_is_not_clamped_to_zero_by_grid_priority():
    rig = _short_rig(options={"grid_priority_band": 20000})
    assert rig.tick().voltx_command < -9000


def test_maximum_charge_buy_price_from_a_helper_entity_blocks_charging():
    rig = _short_rig(options={CONF_PW_MAX_CHARGE_BUY_PRICE: "input_number.max_buy"})
    rig.states.set("input_number.max_buy", 0.20)  # buying at 28c is over it
    data = rig.tick()
    assert data.price_window.code == "hold_charge_too_dear"
    assert data.grid_target == 0


# ── review fixes ──────────────────────────────────────────────────────────────


def test_export_gives_voltx_what_solax_cannot_deliver():
    # Solax is the fuller battery, so SOC balancing asks it for more than its 3 kW.
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 60)
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 85)
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert (data.voltx_command, data.solax_command) == (7800, 3000)


def test_export_with_solax_at_its_floor_runs_voltx_flat_out():
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 33)
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 30)
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC_MIN], 30)
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert (data.voltx_command, data.solax_command) == (10000, 0)


def test_charge_gives_voltx_what_solax_cannot_take():
    rig = _short_rig()
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 60)  # Solax is much the emptier battery
    rig.states.set(LOAD, 20000)  # so the house reserve still leaves a shortfall
    data = rig.tick()
    assert data.price_window.code == "charge_shortfall"
    assert data.solax_command == -3000
    assert data.voltx_command + data.solax_command == pytest.approx(-11200, abs=2)


def test_unreadable_maximum_charge_buy_price_helper_blocks_charging():
    rig = _short_rig(options={CONF_PW_MAX_CHARGE_BUY_PRICE: "input_number.max_buy"})
    rig.states.set("input_number.max_buy", "unavailable")
    data = rig.tick()
    assert data.price_window.code == "hold_charge_too_dear"
    assert "cannot be read" in data.price_window.sentence
    assert data.grid_target == 0
