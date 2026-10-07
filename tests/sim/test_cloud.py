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


# At a 160 s period the 80 s cloud phase is long enough for the charging-shortfall exit (and
# its 5 x dwell re-entry back-off) to apply, so the benefit is smaller by design.
@pytest.mark.parametrize(("period_s", "max_export_ratio"), [(40, 0.5), (80, 0.5), (160, 0.75)])
def test_handoff_cuts_export_and_import_under_cloud_cycling(period_s, max_export_ratio):
    base = run(LEGACY, _cloud(period_s), DURATION_S)
    new = run(NEW, _cloud(period_s), DURATION_S)
    assert new.export_wh < max_export_ratio * base.export_wh
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


def _active_runs(flags):
    runs, count = [], 1
    for prev, cur in zip(flags, flags[1:]):
        if prev == cur:
            count += 1
        else:
            runs.append((prev, count))
            count = 1
    runs.append((flags[-1], count))
    return runs


def test_persistent_charging_shortfall_backs_off_instead_of_flapping():
    # Plan wants 1247 W of charging but only a 300 W surplus exists, for 30 minutes.
    # Tracking pins actual to the setpoint (so entry is easy); native mode then shows the
    # shortfall and exits.  The re-entry back-off must stop that cycling every 2 x dwell.
    def inputs(t):
        return LOAD_W, 800.0, 0.0, -1247.0, None

    base = run(LEGACY, inputs, 1800)
    res = run(NEW, inputs, 1800)
    runs = _active_runs(res.active)
    # Never exits before the 120 s dwell (12 ticks) ...
    assert all(n >= 12 for active, n in runs if active)
    # ... and after a shortfall exit tracking is held >= 5 x dwell (60 ticks) before
    # re-entering (the trailing run may be cut short by the end of the simulation).
    assert all(n >= 60 for active, n in runs[1:-1] if not active)
    assert res.transitions <= 7
    assert res.import_wh < base.import_wh  # still better than staying in tracking


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
