"""Pure-logic tests for the self-consumption handoff decision and Solax follow."""

import pytest

from custom_components.grid_coordinator.budget import (
    SELF_CONSUMPTION_EXIT_MARGIN,
    ScState,
    compute_solax_follow,
    decide_self_consumption,
)
from custom_components.grid_coordinator.models import SolaxMode

BASE = dict(deadband=50.0, min_dwell_s=120.0)


def decide(state=None, *, now=1000.0, target=0.0, **kw):
    return decide_self_consumption(
        state=state or ScState(), now=now, effective_target=target, **{**BASE, **kw}
    )


# ── target clause ─────────────────────────────────────────────────────────────

def test_enters_when_target_in_deadband():
    assert decide().active is True


def test_does_not_enter_when_target_outside_deadband():
    assert decide(target=80.0).active is False


def test_exit_margin_applies_to_target_once_active():
    active = ScState(active=True, last_transition_at=0.0)
    inside = 50.0 + SELF_CONSUMPTION_EXIT_MARGIN
    assert decide(active, target=inside).active is True
    assert decide(active, target=inside + 1).active is False


def test_deadband_zero_has_no_margin():
    active = ScState(active=True, last_transition_at=0.0)
    assert decide(active, deadband=0.0, target=1.0).active is False


# ── dwell, bypass ─────────────────────────────────────────────────────────────

def test_dwell_blocks_exit_then_allows_it():
    entered = decide(now=1000.0)
    assert entered.active is True
    assert decide(entered, now=1119.0, target=500.0).active is True   # locked
    assert decide(entered, now=1120.0, target=500.0).active is False  # released


def test_dwell_blocks_reentry():
    exited = ScState(active=False, last_transition_at=1000.0)
    assert decide(exited, now=1100.0).active is False
    assert decide(exited, now=1120.0).active is True


def test_min_dwell_zero_disables_lock():
    entered = decide(now=1000.0, min_dwell_s=0.0)
    assert decide(entered, now=1000.0, target=500.0, min_dwell_s=0.0).active is False


def test_force_exit_bypasses_dwell():
    entered = decide(now=1000.0)
    assert decide(entered, now=1001.0, force_exit=True).active is False


def test_force_exit_blocks_entry():
    assert decide(force_exit=True).active is False


def test_bypass_lockout_enters_inside_a_lockout():
    locked = ScState(active=False, last_transition_at=1000.0)
    assert decide(locked, now=1001.0).active is False
    assert decide(locked, now=1001.0, bypass_lockout=True).active is True


def test_bypass_lockout_does_not_force_an_entry_the_target_rejects():
    locked = ScState(active=False, last_transition_at=1000.0)
    assert decide(locked, now=1001.0, target=500.0, bypass_lockout=True).active is False


def test_decision_takes_no_battery_inputs():
    # A charging, idle or discharging plan — and any actual battery power — is irrelevant
    # to the handoff at target ~0 (shortfall clause removed, spec 2026-10-07).
    import inspect

    params = inspect.signature(decide_self_consumption).parameters
    for name in ("voltx_setpoint", "smoothed_voltx_power", "tolerance", "allow_discharge_plan"):
        assert name not in params


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
