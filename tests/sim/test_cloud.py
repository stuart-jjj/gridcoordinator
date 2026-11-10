"""Issue 2 + Solax piggy-back: closed loop against the plant under cloud cycling.

Plan: grid target 0 W with the battery setpoint charging at 1247 W (the live plan seen
2026-10-07).  Solar alternates between a 3 kW and a 300 W surplus.  Solax has no
working native mode, so it must be commanded to follow Voltx during the handoff.
"""

import pytest

from custom_components.grid_coordinator.models import SolaxMode
from tests.sim.driver import LEGACY, NEW, Policy, make_plant, run

LOAD_W = 500.0
DURATION_S = 1200


def _cloud(period_s: float, high: float = 3500.0, low: float = 800.0):
    def inputs(t):
        solar = high if (t % period_s) < period_s / 2 else low
        return LOAD_W, solar, 0.0, -1247.0, None

    return inputs


@pytest.mark.parametrize("period_s", [40, 80, 160])
def test_handoff_cuts_export_and_import_under_cloud_cycling(period_s):
    base = run(LEGACY, _cloud(period_s), DURATION_S)
    new = run(NEW, _cloud(period_s), DURATION_S)
    assert new.export_wh < 0.5 * base.export_wh
    assert new.import_wh <= base.import_wh  # no new grid import from the change


@pytest.mark.parametrize("period_s", [40, 80, 160])
def test_solax_follows_voltx_without_a_standoff(period_s):
    new = run(NEW, _cloud(period_s), DURATION_S)
    assert SolaxMode.FOLLOW_VOLTX in new.solax_modes
    # Up to two ticks of opposition at a step change in solar are expected (Solax
    # reacts a tick late); a sustained standoff like 2026-07-09 would be unbounded.
    assert new.max_opposing_run <= 3
    # Solax carries roughly its capacity share (7 / 17 = 0.41) of the throughput.
    share = new.solax_wh / (new.solax_wh + new.voltx_wh)
    assert 0.33 <= share <= 0.47


def test_without_follow_solax_stays_idle_in_the_handoff():
    res = run(Policy(legacy=False, solax_follow=False), _cloud(80), DURATION_S)
    assert res.solax_handoff_wh == 0.0


def test_solax_at_ceiling_is_reported_and_voltx_absorbs_alone():
    plant = make_plant(solax_soc=96.0)
    res = run(NEW, _cloud(80), DURATION_S, plant=plant)
    assert SolaxMode.SOC_CEILING in res.solax_modes
    assert res.solax_handoff_wh == 0.0
    assert res.max_opposing_run == 0


def test_persistent_charging_shortfall_stays_in_the_handoff():
    # Plan wants 1247 W of charging but only a 300 W surplus exists, for 30 minutes.
    # Native self-consumption takes what solar gives and never imports to fill the
    # battery; tracking would buy the shortfall from the grid (live 2026-10-07 13:36).
    def inputs(t):
        return LOAD_W, 800.0, 0.0, -1247.0, None

    base = run(LEGACY, inputs, 1800)
    res = run(NEW, inputs, 1800)
    assert res.transitions == 0
    assert res.active_ticks >= res.ticks - 2
    assert res.import_wh < 5.0  # native self-consumption never buys the shortfall (legacy: ~318 Wh)
    assert res.import_wh < 0.05 * base.import_wh


def test_idle_or_discharge_plan_never_exits_for_discharging_above_plan():
    # Plan idle (0 W) but the house load forces the battery to discharge ~1.5 kW.
    def inputs(t):
        return 2000.0, 500.0, 0.0, 0.0, None

    res = run(NEW, inputs, 600)
    assert res.transitions <= 1  # at most the initial entry
    assert res.active_ticks >= res.ticks - 2


@pytest.mark.parametrize("native_lag", [0.7, 1.0])
@pytest.mark.parametrize(("voltx_soc", "solax_soc"), [(60.0, 30.0), (60.0, 40.0), (70.0, 25.0)])
def test_solax_follow_is_stable_when_socs_are_imbalanced(monkeypatch, native_lag, voltx_soc, solax_soc):
    # An SOC imbalance pushes Solax's share above 0.5; a ratio-of-Voltx follow formula then
    # has loop gain > 1 against native Voltx and the two batteries alternate in opposite
    # directions indefinitely (final review, Critical 1).
    monkeypatch.setattr("tests.sim.plant.NATIVE_LAG", native_lag)

    def inputs(t):
        return LOAD_W, 2500.0, 0.0, -1247.0, None  # steady 2 kW surplus

    res = run(NEW, inputs, 600, plant=make_plant(voltx_soc, solax_soc))
    assert res.active_ticks > res.ticks * 0.9
    assert res.max_opposing_run <= 3
    assert all(abs(g) < 400 for g in res.grid[-10:])


def _voltx_full_surplus(t):
    # 2026-11-10 16:20: EV stopped, plan target 0 W, ~2.8 kW spare solar, Voltx full.
    return 2200.0, 5000.0, 0.0, -2147.0, None


def test_voltx_full_handoff_exports_the_surplus_without_the_limit_exit():
    # Reproduces the incident: Solax follows Voltx's 0 W, so the surplus is exported.
    plant = make_plant(voltx_soc=100.0, solax_soc=79.0)
    res = run(Policy(legacy=False, exit_at_voltx_limit=False), _voltx_full_surplus, 600, plant=plant)
    assert res.active_ticks >= res.ticks - 1
    assert res.export_wh > 400.0  # ~2.8 kW for 10 minutes


def test_voltx_full_leaves_handoff_and_solax_absorbs_the_surplus():
    plant = make_plant(voltx_soc=100.0, solax_soc=50.0)  # room for 30 min of surplus
    res = run(NEW, _voltx_full_surplus, 1800, plant=plant)
    assert res.solax[-1] < -2500.0  # Solax charging
    assert res.export_wh < 60.0  # only the first few ticks
    # No cycle of re-entering the handoff once Solax has the grid at target.
    assert res.transitions <= 2
    assert sum(res.active[-150:]) == 0


def test_voltx_full_with_house_in_deficit_stays_in_the_handoff():
    # Evening: Voltx full, load above solar.  Native Voltx discharges; Solax is not
    # made to carry the deficit while Voltx idles (Copilot review, PR #50).
    def inputs(t):
        return 3000.0, 1000.0, 0.0, 0.0, None

    plant = make_plant(voltx_soc=100.0, solax_soc=79.0)
    res = run(NEW, inputs, 600, plant=plant)
    assert res.active_ticks >= res.ticks - 1
    assert res.voltx[-1] > 1500.0  # Voltx discharging
    assert res.import_wh < 30.0


def test_voltx_full_surplus_with_opposing_discharge_plan_does_not_discharge_voltx():
    # Plan wants a discharge at a 0 W target while there is a real surplus: tracking
    # follows the grid target, so Voltx is not discharged into the export.
    def inputs(t):
        return 2200.0, 5000.0, 0.0, 1500.0, None

    plant = make_plant(voltx_soc=100.0, solax_soc=79.0)
    res = run(NEW, inputs, 600, plant=plant)
    assert max(res.voltx[3:]) <= 0.0
    assert res.solax[-1] < -2500.0
    assert res.export_wh < 60.0


def test_voltx_empty_deficit_with_charge_plan_does_not_import_to_charge():
    # Voltx at its floor, plan wants a charge, but load exceeds solar: Solax covers the
    # deficit and Voltx does not import to follow the plan.
    def inputs(t):
        return 2500.0, 800.0, 0.0, -1500.0, None

    plant = make_plant(voltx_soc=20.0, solax_soc=60.0)
    res = run(NEW, inputs, 600, plant=plant)
    assert min(res.voltx[3:]) >= 0.0
    assert res.solax[-1] > 1500.0
    assert res.import_wh < 30.0


def test_voltx_full_planned_export_still_discharges_voltx():
    # A deliberate export from a full Voltx is outside the handoff band: the plan stands.
    def inputs(t):
        return 1000.0, 1000.0, -3000.0, 3000.0, None

    plant = make_plant(voltx_soc=100.0, solax_soc=79.0)
    res = run(NEW, inputs, 300, plant=plant)
    assert res.voltx[-1] > 1500.0
