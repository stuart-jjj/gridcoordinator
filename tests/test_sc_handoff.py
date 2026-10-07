"""Pure-logic tests for the self-consumption handoff decision, EMA, and Solax follow."""

import pytest

from custom_components.grid_coordinator.budget import (
    SELF_CONSUMPTION_EXIT_MARGIN,
    SHORTFALL_REENTRY_MULT,
    ScState,
    compute_solax_follow,
    decide_self_consumption,
    ema_update,
)
from custom_components.grid_coordinator.models import SolaxMode

BASE = dict(
    deadband=50.0,
    tolerance=300.0,
    min_dwell_s=120.0,
)


def decide(state=None, *, now=1000.0, target=0.0, setpoint=-1000.0, power=-900.0, **kw):
    args = {**BASE, **kw}
    return decide_self_consumption(
        state=state or ScState(),
        now=now,
        effective_target=target,
        voltx_setpoint=setpoint,
        smoothed_voltx_power=power,
        **args,
    )


# ── EMA ──────────────────────────────────────────────────────────────────────

def test_ema_seeds_on_first_sample():
    assert ema_update(None, 500.0, 10.0, 60.0) == 500.0


def test_ema_tau_zero_is_raw_sample():
    assert ema_update(100.0, 500.0, 10.0, 0.0) == 500.0


def test_ema_moves_toward_sample_by_time_constant():
    # one time-constant closes ~63.2 % of the gap
    assert ema_update(0.0, 100.0, 60.0, 60.0) == pytest.approx(63.212, abs=0.01)


def test_ema_zero_dt_holds():
    assert ema_update(10.0, 500.0, 0.0, 60.0) == 10.0


# ── decision: target clause ───────────────────────────────────────────────────

def test_enters_when_target_in_deadband_and_on_plan():
    assert decide().active is True


def test_does_not_enter_when_target_outside_deadband():
    assert decide(target=80.0).active is False


def test_exit_margin_applies_to_target_once_active():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    inside = 50.0 + SELF_CONSUMPTION_EXIT_MARGIN
    assert decide(active, target=inside).active is True
    assert decide(active, target=inside + 1).active is False


def test_deadband_zero_has_no_margin():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    assert decide(active, deadband=0.0, target=1.0).active is False


# ── decision: battery clause (charging plan only) ─────────────────────────────

def test_charging_plan_battery_on_plan_enters():
    assert decide(setpoint=-1247.0, power=-1200.0).active is True


def test_charging_plan_absorbing_more_than_plan_enters_and_stays():
    st = decide(setpoint=-1247.0, power=-3000.0)
    assert st.active is True
    assert decide(st, now=2000.0, setpoint=-1247.0, power=-3000.0).active is True


def test_charging_plan_shortfall_beyond_tolerance_does_not_enter():
    assert decide(setpoint=-1247.0, power=-900.0).active is False  # d = 347 > 300


def test_charging_plan_shortfall_exit_has_margin_once_active():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    # d = 320: above tolerance 300 but inside 300 + 25 margin -> stays
    assert decide(active, setpoint=-1000.0, power=-680.0).active is True
    # d = 330 -> exits
    assert decide(active, setpoint=-1000.0, power=-670.0).active is False


@pytest.mark.parametrize("setpoint", [0.0, 400.0])
def test_idle_or_discharge_plan_ignores_actual_discharge(setpoint):
    # Battery covering house load far above plan must not block or end the handoff.
    assert decide(setpoint=setpoint, power=1500.0).active is True
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    assert decide(active, setpoint=setpoint, power=1500.0).active is True


# ── decision: fallback ────────────────────────────────────────────────────────

def test_tolerance_zero_uses_legacy_clause():
    # legacy: a charge setpoint beyond the deadband blocks the handoff
    assert decide(tolerance=0.0, setpoint=-1000.0, power=-1000.0).active is False
    assert decide(tolerance=0.0, setpoint=10.0, power=0.0).active is True


def test_tolerance_zero_discharge_flag_lets_discharge_plan_through():
    assert decide(tolerance=0.0, setpoint=800.0, power=0.0).active is False
    assert decide(tolerance=0.0, setpoint=800.0, power=0.0, allow_discharge_plan=True).active is True


def test_unavailable_power_uses_legacy_clause():
    assert decide(setpoint=-1000.0, power=None).active is False
    assert decide(setpoint=10.0, power=None).active is True


# ── decision: dwell, back-off and bypass ──────────────────────────────────────

def test_dwell_blocks_exit_then_allows_it():
    entered = decide(now=1000.0)
    assert entered.active is True
    assert decide(entered, now=1000.0 + 119.0, target=500.0).active is True   # locked
    assert decide(entered, now=1000.0 + 120.0, target=500.0).active is False  # released


def test_dwell_blocks_reentry():
    exited = ScState(active=False, last_transition_at=1000.0, lockout_s=120.0)
    assert decide(exited, now=1100.0).active is False
    assert decide(exited, now=1120.0).active is True


def test_min_dwell_zero_disables_lock():
    entered = decide(now=1000.0, min_dwell_s=0.0)
    assert decide(entered, now=1000.0, target=500.0, min_dwell_s=0.0).active is False


def test_shortfall_exit_sets_longer_reentry_lockout():
    entered = decide(now=1000.0, setpoint=-1000.0, power=-1000.0)
    out = decide(entered, now=2000.0, setpoint=-1000.0, power=-100.0)
    assert out.active is False
    assert out.lockout_s == 120.0 * SHORTFALL_REENTRY_MULT


def test_legacy_clause_exit_keeps_normal_lockout_when_tolerance_is_zero():
    # tolerance 0 must reproduce legacy behaviour apart from the plain dwell (review Important 2).
    entered = decide(now=1000.0, tolerance=0.0, setpoint=0.0, power=0.0)
    out = decide(entered, now=2000.0, tolerance=0.0, setpoint=-800.0, power=-800.0)
    assert out.active is False
    assert out.lockout_s == 120.0


def test_legacy_clause_exit_keeps_normal_lockout_when_power_unavailable():
    entered = decide(now=1000.0, setpoint=10.0, power=None)
    out = decide(entered, now=2000.0, setpoint=600.0, power=None)
    assert out.active is False
    assert out.lockout_s == 120.0


def test_target_exit_keeps_normal_lockout():
    entered = decide(now=1000.0)
    out = decide(entered, now=2000.0, target=500.0)
    assert out.active is False
    assert out.lockout_s == 120.0


def test_force_exit_bypasses_dwell():
    entered = decide(now=1000.0)
    out = decide(entered, now=1001.0, force_exit=True)
    assert out.active is False


def test_force_exit_blocks_entry():
    assert decide(force_exit=True).active is False


# ── Solax follow ─────────────────────────────────────────────────────────────

FOLLOW = dict(
    solax_soc=50.0, solax_soc_min=20.0, solax_soc_max=95.0,
    solax_max_charge=3000.0, solax_max_discharge=3000.0,
    grid_actual=0.0, import_limit=12000.0, export_limit=10000.0, prev_solax_cmd=0.0,
)


def test_follow_takes_share_of_combined_battery_power():
    # Voltx at -900 plus Solax already at -600 is -1500 combined; Solax's share is 0.4.
    cmd, mode = compute_solax_follow(voltx_power=-900.0, share=0.4, **{**FOLLOW, "prev_solax_cmd": -600.0})
    assert cmd == pytest.approx(-600.0)
    assert mode == SolaxMode.FOLLOW_VOLTX


def test_follow_first_tick_takes_share_of_voltx_alone():
    cmd, _ = compute_solax_follow(voltx_power=-1500.0, share=0.4, **FOLLOW)
    assert cmd == pytest.approx(-600.0)


def test_follow_share_above_half_is_still_a_fraction_of_the_total():
    # share 0.7 must never produce more than the combined power (ratio s/(1-s) would be 2.33x).
    cmd, _ = compute_solax_follow(voltx_power=-1000.0, share=0.7, **{**FOLLOW, "prev_solax_cmd": -1000.0})
    assert cmd == pytest.approx(-1400.0)


def test_follow_has_voltx_sign_discharge():
    cmd, mode = compute_solax_follow(voltx_power=900.0, share=0.5, **FOLLOW)
    assert cmd == pytest.approx(450.0)
    assert mode == SolaxMode.FOLLOW_VOLTX


def test_follow_zero_when_voltx_idle():
    assert compute_solax_follow(voltx_power=0.0, share=0.4, **FOLLOW) == (0.0, SolaxMode.SELF_CONSUMPTION)


def test_follow_respects_inverter_charge_limit():
    cmd, _ = compute_solax_follow(voltx_power=-5000.0, share=0.8, **FOLLOW)  # raw -4000 W
    assert cmd == -3000.0


def test_follow_reports_soc_ceiling_and_does_not_charge():
    cmd, mode = compute_solax_follow(voltx_power=-1500.0, share=0.4, **{**FOLLOW, "solax_soc": 96.0})
    assert (cmd, mode) == (0.0, SolaxMode.SOC_CEILING)


def test_follow_reports_soc_floor_and_does_not_discharge():
    cmd, mode = compute_solax_follow(voltx_power=1500.0, share=0.4, **{**FOLLOW, "solax_soc": 19.0})
    assert (cmd, mode) == (0.0, SolaxMode.SOC_FLOOR)


def test_follow_grid_clamp_stops_charging_past_import_ceiling():
    # Grid already at the import limit: Solax must not add charging load.
    cmd, _ = compute_solax_follow(
        voltx_power=-1500.0, share=0.4, **{**FOLLOW, "grid_actual": 12000.0}
    )
    assert cmd >= 0.0
