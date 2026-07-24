"""Unit tests for budget.py — pure arithmetic, no HA dependencies."""

import pytest

from custom_components.grid_coordinator.budget import (
    SOLAX_RESIDUAL_MODES,
    build_coordinator_data,
    cap_combined_charge,
    compute_ev_current_limit,
    compute_solax_command,
    compute_solax_share,
    compute_solax_tier1,
    compute_voltx_command,
)
from custom_components.grid_coordinator.models import CoordinatorData, CoordinatorMode, SolaxMode

# ── helpers ────────────────────────────────────────────────────────────────────

_DEFAULTS = dict(
    grid_actual=0.0,
    grid_target=0.0,
    mpc_batt_cmd=0.0,
    prev_cmd=0.0,
    soc=50.0,
    soc_min=20.0,
    soc_max=95.0,
    max_charge=5000.0,
    max_discharge=5000.0,
    import_limit=12000.0,
    export_limit=10000.0,
    ramp_step=1500.0,
    plan_is_stale=False,
    tracking_deadband=0.0,
)


def cmd(**overrides) -> tuple[float, CoordinatorMode]:
    command, mode, _diag = compute_voltx_command(**{**_DEFAULTS, **overrides})
    return command, mode


# ── normal tracking ────────────────────────────────────────────────────────────


def test_closes_error_in_one_step():
    """2-tier controller closes grid error when mpc_batt_cmd is zero."""
    command, mode = cmd(grid_actual=2000.0, grid_target=500.0)
    # mpc_batt_cmd=0 + correction=(2000-500)=1500 → 1500 W discharge
    assert command == 1500
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_negative_error_charges():
    """When grid is below target (e.g. exporting too much), command goes negative."""
    command, mode = cmd(grid_actual=-500.0, grid_target=0.0, prev_cmd=0.0)
    # mpc_batt_cmd=0 + correction=(-500-0)=-500 → -500 W (charge)
    assert command == -500
    assert mode == CoordinatorMode.EMHASS_TRACKING


# ── 2-tier: mpc_batt_cmd as primary signal ────────────────────────────────────


def test_executes_mpc_batt_cmd_directly():
    """When forecast is accurate (grid_actual == grid_target), correction is zero."""
    command, mode = cmd(
        mpc_batt_cmd=2000.0, grid_actual=1000.0, grid_target=1000.0,
        ramp_step=5000.0,
    )
    # correction = 1000 - 1000 = 0 → raw_cmd = 2000 + 0 = 2000
    assert command == 2000
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_correction_added_to_mpc_batt_cmd():
    """When actual grid deviates from forecast, correction is added to mpc_batt_cmd."""
    command, mode = cmd(
        mpc_batt_cmd=1000.0, grid_actual=1500.0, grid_target=1000.0,
        ramp_step=5000.0,
    )
    # raw_cmd = 1000 + (1500 - 1000) = 1500
    assert command == 1500
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_mpc_batt_cmd_negative_charges():
    """Negative mpc_batt_cmd (EMHASS wants to charge) with no grid error."""
    command, mode = cmd(
        mpc_batt_cmd=-2000.0, grid_actual=500.0, grid_target=500.0,
        ramp_step=5000.0,
    )
    # raw_cmd = -2000 + 0 = -2000 (charge)
    assert command == -2000
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_correction_opposes_mpc_batt_cmd():
    """Correction term can partially cancel mpc_batt_cmd when actual < target."""
    command, mode = cmd(
        mpc_batt_cmd=2000.0, grid_actual=500.0, grid_target=1000.0,
        ramp_step=5000.0,
    )
    # raw_cmd = 2000 + (500 - 1000) = 2000 - 500 = 1500
    assert command == 1500
    assert mode == CoordinatorMode.EMHASS_TRACKING


# ── tracking deadband ─────────────────────────────────────────────────────────


def test_deadband_holds_command():
    """Command is held unchanged when grid error is within the deadband."""
    # mpc_batt_cmd matches prev_cmd so the "new plan" guard does not break the hold.
    command, mode = cmd(
        grid_actual=100.0, grid_target=0.0, prev_cmd=1000.0, mpc_batt_cmd=1000.0,
        tracking_deadband=200.0,
    )
    assert command == 1000
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_deadband_exact_boundary_holds():
    assert cmd(
        grid_actual=200.0, grid_target=0.0, prev_cmd=500.0, mpc_batt_cmd=500.0,
        tracking_deadband=200.0,
    )[0] == 500


def test_outside_deadband_reacts():
    command, _ = cmd(
        grid_actual=201.0, grid_target=0.0, prev_cmd=0.0, tracking_deadband=200.0
    )
    assert command != 0


# ── ramp limiting ─────────────────────────────────────────────────────────────


def test_ramp_limits_large_step():
    """A large grid error is ramped at most ramp_step per tick."""
    command, mode = cmd(
        grid_actual=5000.0, grid_target=0.0, prev_cmd=0.0, ramp_step=1500.0
    )
    assert command == 1500
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_ramp_down_limits_negative_step():
    command, _ = cmd(
        grid_actual=-5000.0, grid_target=0.0, prev_cmd=0.0, ramp_step=1500.0
    )
    assert command == -1500


# ── transient (high grid-variance) damping ─────────────────────────────────────


def test_transient_tracks_smoothed_grid_not_raw():
    """When damping is engaged the correction uses grid_smoothed, not the raw spike."""
    command, mode = cmd(
        grid_actual=2000.0,        # raw spike (oven element on)
        grid_smoothed=500.0,       # EMA of the average load
        grid_target=0.0,
        transient_active=True,
        ramp_step=5000.0,
        discharge_ramp_step=5000.0,  # large so the asymmetric cap does not bind here
    )
    # raw_cmd = 0 + 1.0 * (500 - 0) = 500, tracking the average not the 2000 W spike
    assert command == 500
    assert mode == CoordinatorMode.EMHASS_TRACKING


def test_transient_ignored_when_inactive():
    """grid_smoothed is ignored unless transient_active is set (backward compatible)."""
    command, _ = cmd(
        grid_actual=2000.0,
        grid_smoothed=500.0,
        grid_target=0.0,
        transient_active=False,
        ramp_step=5000.0,
    )
    # Uses raw grid → 2000 W discharge
    assert command == 2000


def test_transient_allows_fast_discharge_increase():
    """Discharge increases keep the normal (fast) ramp so a new spike is covered quickly."""
    command, _ = cmd(
        grid_actual=3000.0,
        grid_smoothed=3000.0,
        grid_target=0.0,
        prev_cmd=0.0,
        transient_active=True,
        ramp_step=1500.0,
        discharge_ramp_step=150.0,
    )
    # raw_cmd = 3000, limited by the fast ramp_step (1500), not the 150 cap
    assert command == 1500


def test_transient_caps_discharge_decrease():
    """Discharge decreases are limited to discharge_ramp_step during a transient.

    Holds the battery near the cycle's peak so the next on-phase is already
    mostly covered, at the cost of exporting during the load's off-phase.
    """
    command, _ = cmd(
        grid_actual=0.0,
        grid_smoothed=0.0,        # element switched off
        grid_target=0.0,
        prev_cmd=2000.0,          # battery parked high from the peak
        transient_active=True,
        ramp_step=1500.0,
        discharge_ramp_step=150.0,
    )
    # delta = 0 - 2000 = -2000, but discharge ramp-down is capped at 150 W/tick
    assert command == 1850


def test_transient_diag_flag_set():
    """The transient flag is surfaced in diagnostics for logging."""
    _, _, diag = compute_voltx_command(
        **{**_DEFAULTS, "grid_actual": 1000.0, "transient_active": True, "discharge_ramp_step": 150.0}
    )
    assert diag.transient_active is True


def test_transient_deadband_still_enforces_grid_limit():
    """A raw grid spike inside the smoothed deadband must not bypass the import clamp.

    With damping the deadband is tested on grid_smoothed, so a raw spike can sit
    within the deadband while the raw grid exceeds the import limit. The held
    command must still be clamped to the hard grid-safety limit (raw grid based).
    """
    command, mode = cmd(
        grid_actual=15000.0,       # raw spike well above the 12000 W import limit
        grid_smoothed=0.0,         # smoothed value in deadband → would normally hold
        grid_target=0.0,
        prev_cmd=0.0,
        mpc_batt_cmd=0.0,
        tracking_deadband=200.0,
        transient_active=True,
    )
    # cmd_floor = uncontrolled - import_limit = 15000 - 12000 = 3000; prev_cmd=0 is clamped up.
    assert command == 3000
    assert mode == CoordinatorMode.IMPORT_CEILING


# ── SOC constraints ───────────────────────────────────────────────────────────


def test_soc_floor_suppresses_discharge():
    """At minimum SOC, a discharge command (cmd > 0) must be zeroed."""
    command, mode = cmd(
        grid_actual=3000.0, grid_target=0.0,  # raw_cmd would be +3000 (discharge)
        soc=15.0, soc_min=20.0,
    )
    assert command == 0
    assert mode == CoordinatorMode.SOC_FLOOR


def test_soc_floor_allows_charge():
    """At minimum SOC, a charge command (cmd < 0) must be allowed."""
    command, mode = cmd(
        grid_actual=-2000.0, grid_target=0.0,  # raw_cmd would be -2000 (charge)
        soc=15.0, soc_min=20.0,
    )
    # Should be clamped by ramp (1500 W/tick) → -1500, not zeroed
    assert command < 0
    assert mode != CoordinatorMode.SOC_FLOOR


def test_soc_ceiling_suppresses_charge():
    """At maximum SOC, a charge command (cmd < 0) must be zeroed."""
    command, mode = cmd(
        grid_actual=-3000.0, grid_target=0.0,  # raw_cmd would be -3000 (charge)
        soc=96.0, soc_max=95.0,
    )
    assert command == 0
    assert mode == CoordinatorMode.SOC_CEILING


def test_soc_ceiling_allows_discharge():
    """At maximum SOC, a discharge command (cmd > 0) must be allowed."""
    command, mode = cmd(
        grid_actual=2000.0, grid_target=0.0,  # raw_cmd would be +2000 (discharge)
        soc=96.0, soc_max=95.0,
    )
    assert command > 0
    assert mode != CoordinatorMode.SOC_CEILING


# ── inverter physical limits ──────────────────────────────────────────────────


def test_discharge_capped_at_max_discharge():
    command, _ = cmd(
        grid_actual=8000.0, grid_target=0.0, ramp_step=10000.0,
        max_discharge=5000.0,
    )
    assert command <= 5000


def test_charge_capped_at_max_charge():
    command, _ = cmd(
        grid_actual=-8000.0, grid_target=0.0, ramp_step=10000.0,
        max_charge=5000.0,
    )
    assert command >= -5000


# ── grid safety clamp ─────────────────────────────────────────────────────────


def test_import_ceiling_clamps_charge():
    """If charging would push grid import over the limit, command is clamped."""
    # uncontrolled = grid_actual + prev_cmd = 11000 + 0 = 11000
    # cmd_floor = 11000 - 12000 = -1000 (can charge at most 1000 W)
    # raw_cmd = prev(0) + (11000 - 0) = 11000 → after ramp/limits → 5000 discharge, not relevant
    # Test with a scenario where charging exceeds import limit:
    # uncontrolled = -1000 (exporting), prev=0
    # raw_cmd = -1000 - 0 = -1000 (wants to charge more)
    # cmd_floor = -1000 - 12000 = -13000 (no floor issue)
    # After ramp: -1000 (within ramp_step=1500)
    # Grid projection: -1000 - (-1000) = 0 W (fine, no clamp needed)
    # Now test import ceiling violation:
    # grid_actual = 11500, target = 0 → raw_cmd = 11500 → ramped = 1500
    # uncontrolled = 11500 + 0 = 11500; cmd_floor = 11500-12000 = -500
    # cmd_ceil = 11500 + 10000 = 21500
    # ramped = 1500; final = 1500 (no clamp)
    # Projected grid = 11500 - 1500 = 10000 ≤ 12000 ✓
    command, mode = cmd(
        grid_actual=11500.0, grid_target=0.0, ramp_step=10000.0,
        max_discharge=5000.0, import_limit=12000.0,
    )
    projected_grid = 11500.0 - command
    assert projected_grid <= 12000.0


def test_export_ceiling_clamps_discharge():
    """If discharging would push grid export over the limit, command is clamped."""
    # grid_actual = -9000 (exporting 9000 W), target = 0
    # uncontrolled = -9000 + 0 = -9000
    # cmd_ceil = -9000 + 10000 = 1000 (can only discharge 1000 W before hitting export limit)
    # raw_cmd = 0 + (-9000 - 0) = -9000 → after ramp: -1500
    # ramped = -1500 → cmd_ceil clamps to max(−9000−12000, min(1000, -1500)) = -1500 (no issue)
    # Let's force export ceiling: grid_actual = -9500, prev_cmd = 1000 (already discharging)
    # uncontrolled = -9500 + 1000 = -8500; cmd_ceil = -8500 + 10000 = 1500
    # raw_cmd = 1000 + (-9500 - 0) = -8500 → after ramp: 1000-1500=-500
    # -500 within [floor, ceil=1500] → no clamp (fine)
    # Force the clamp: grid = -9500, prev=0, target=-11000 (wants more export, raw=-9500-(-11000)=1500)
    # uncontrolled = -9500; cmd_ceil = -9500+10000=500; ramped=1500 > cmd_ceil=500 → clamped to 500
    command, mode = cmd(
        grid_actual=-9500.0, grid_target=-11000.0, prev_cmd=0.0,
        ramp_step=10000.0, export_limit=10000.0,
    )
    projected_grid = -9500.0 - command
    assert projected_grid >= -10000.0
    assert mode == CoordinatorMode.EXPORT_CEILING


# ── stale plan ────────────────────────────────────────────────────────────────


def test_stale_plan_mode_reported():
    _, mode = cmd(grid_actual=1000.0, grid_target=0.0, plan_is_stale=True)
    assert mode == CoordinatorMode.STALE_PLAN


def test_stale_plan_in_deadband_still_stale():
    _, mode = cmd(
        grid_actual=50.0, grid_target=0.0, tracking_deadband=200.0, plan_is_stale=True
    )
    assert mode == CoordinatorMode.STALE_PLAN


# ── build_coordinator_data ────────────────────────────────────────────────────


# ── compute_solax_command ─────────────────────────────────────────────────────

_SOLAX = dict(
    grid_after_voltx=0.0,
    grid_target=0.0,
    solax_soc=50.0,
    solax_soc_min=20.0,
    solax_soc_max=95.0,
    solax_max_charge=2400.0,
    solax_max_discharge=2400.0,
    import_limit=12000.0,
    export_limit=10000.0,
)


def solax(**overrides) -> tuple[float, SolaxMode]:
    return compute_solax_command(**{**_SOLAX, **overrides})


def test_solax_inactive_for_normal_tracking():
    cmd, mode = solax(voltx_mode=CoordinatorMode.EMHASS_TRACKING)
    assert cmd == 0.0
    assert mode == SolaxMode.SELF_CONSUMPTION


def test_solax_inactive_for_self_consumption():
    cmd, mode = solax(voltx_mode=CoordinatorMode.SELF_CONSUMPTION)
    assert cmd == 0.0
    assert mode == SolaxMode.SELF_CONSUMPTION


def test_solax_discharges_on_soc_floor_with_residual():
    """Voltx at SOC floor + grid above target → Solax discharges the delta."""
    # Voltx can't discharge (SOC floor); grid_after_voltx = 5000, target = 2000
    # residual = 5000 - 2000 = 3000 → capped at max_discharge=2400
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_FLOOR,
        grid_after_voltx=5000.0,
        grid_target=2000.0,
    )
    assert cmd == 2400.0
    assert mode == SolaxMode.FORCE_DISCHARGE


def test_solax_discharges_exact_residual_within_limit():
    """Residual below max_discharge → exact residual is commanded."""
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_FLOOR,
        grid_after_voltx=3000.0,
        grid_target=2000.0,
    )
    assert cmd == 1000.0
    assert mode == SolaxMode.FORCE_DISCHARGE


def test_solax_charges_on_soc_ceiling_with_residual():
    """Voltx at SOC ceiling + grid below target → Solax charges the delta."""
    # grid_after_voltx = -2000 (exporting), target = 0 → residual = -2000 - 0 = -2000 (charge)
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_CEILING,
        grid_after_voltx=-2000.0,
        grid_target=0.0,
    )
    assert cmd == -2000.0
    assert mode == SolaxMode.FORCE_CHARGE


def test_solax_inactive_when_no_residual():
    """Voltx at SOC floor but grid already at target → Solax stays idle."""
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_FLOOR,
        grid_after_voltx=2000.0,
        grid_target=2000.0,
    )
    assert cmd == 0.0
    assert mode == SolaxMode.SELF_CONSUMPTION


def test_solax_soc_floor_blocks_discharge():
    """Solax SOC at floor → discharge suppressed even though residual exists."""
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_FLOOR,
        grid_after_voltx=5000.0,
        grid_target=2000.0,
        solax_soc=20.0,
        solax_soc_min=20.0,
    )
    assert cmd == 0.0
    assert mode == SolaxMode.SOC_FLOOR


def test_solax_soc_ceiling_blocks_charge():
    """Solax SOC at ceiling → charge suppressed even though residual exists."""
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_CEILING,
        grid_after_voltx=-2000.0,
        grid_target=0.0,
        solax_soc=95.0,
        solax_soc_max=95.0,
    )
    assert cmd == 0.0
    assert mode == SolaxMode.SOC_CEILING


def test_solax_discharge_clamped_by_export_limit():
    """Discharging Solax must not push grid past the export limit."""
    # grid_after_voltx = -9500 (exporting), target = -11000 (wants more export)
    # residual = -9500 - (-11000) = 1500 (discharge) but grid_limit_ceil = -9500 + 10000 = 500
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.SOC_FLOOR,
        grid_after_voltx=-9500.0,
        grid_target=-11000.0,
        export_limit=10000.0,
    )
    projected = -9500.0 - cmd
    assert projected >= -10000.0
    assert mode == SolaxMode.FORCE_DISCHARGE


def test_solax_command_excludes_grid_priority():
    """GRID_PRIORITY must NOT trigger full-residual coverage.

    Regression pin for a 2026-07-09 production incident: routing grid_priority
    through this instant/full-residual formula let Solax zero the grid error the
    moment Voltx's ramp got partway there, removing the error signal Voltx's
    deadbeat loop needs to keep converging — Voltx froze mid-ramp (~9kW discharge)
    while Solax held a permanent opposing charge (~3kW), indefinitely, not a
    self-correcting oscillation. Solax still helps during grid_priority via the
    proportional, damped tier-1/tier-2 share path in coordinator.py instead (see
    test_grid_priority_standoff_resolves_via_proportional_share below).
    """
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.GRID_PRIORITY,
        grid_after_voltx=5000.0,
        grid_target=0.0,
    )
    assert cmd == 0.0
    assert mode == SolaxMode.SELF_CONSUMPTION


def test_solax_headroom_floor_reclamped_to_physical_limit():
    """The headroom-tightened grid floor must not command more discharge than the inverter
    can deliver — the final command stays within solax_max_discharge."""
    # grid_without_solax = 10000; tightened floor = 10000 - (10000 - 3000) = 3000 W,
    # which exceeds the 2400 W inverter limit → must re-clamp to 2400.
    cmd, mode = solax(
        voltx_mode=CoordinatorMode.DISCHARGE_LIMIT,
        grid_after_voltx=10000.0,
        grid_target=0.0,
        import_limit=10000.0,
        headroom_reserve=3000.0,
        solax_max_discharge=2400.0,
    )
    assert cmd == 2400.0
    assert mode == SolaxMode.FORCE_DISCHARGE


# ── compute_solax_share ───────────────────────────────────────────────────────

_SHARE = dict(
    voltx_soc=50.0,
    solax_soc=50.0,
    voltx_capacity_kwh=10.0,
    solax_capacity_kwh=5.0,
    cmd=1000.0,
    sensitivity=0.01,
    soc_deadband=5.0,
)


def share(**overrides) -> float:
    return compute_solax_share(**{**_SHARE, **overrides})


def test_share_base_when_balanced():
    """Within the SOC deadband, share is just the capacity ratio."""
    assert share(voltx_soc=50.0, solax_soc=50.0) == pytest.approx(5.0 / 15.0)


def test_share_zero_cmd_returns_base():
    """cmd == 0 has no direction to key the imbalance nudge off, so base_share is used."""
    assert share(cmd=0.0, voltx_soc=80.0, solax_soc=20.0) == pytest.approx(5.0 / 15.0)


def test_share_increases_for_fuller_battery_discharging():
    """Solax fuller than Voltx + discharge command → share nudges above base (works harder)."""
    base = 5.0 / 15.0
    result = share(cmd=1000.0, voltx_soc=50.0, solax_soc=80.0)
    assert result > base


def test_share_decreases_for_emptier_battery_discharging():
    """Solax emptier than Voltx + discharge command → share nudges below base (works less)."""
    base = 5.0 / 15.0
    result = share(cmd=1000.0, voltx_soc=80.0, solax_soc=50.0)
    assert result < base


def test_share_direction_reverses_for_charge_cmd():
    """The same SOC imbalance nudges the opposite way when the command charges."""
    discharge_share = share(cmd=1000.0, voltx_soc=50.0, solax_soc=80.0)
    charge_share = share(cmd=-1000.0, voltx_soc=50.0, solax_soc=80.0)
    assert charge_share < discharge_share


def test_share_clamped_to_zero_and_one():
    assert share(cmd=1000.0, voltx_soc=0.0, solax_soc=100.0, sensitivity=10.0) == 1.0
    assert share(cmd=1000.0, voltx_soc=100.0, solax_soc=0.0, sensitivity=10.0) == 0.0


def test_share_zero_total_capacity_returns_zero():
    assert share(voltx_capacity_kwh=0.0, solax_capacity_kwh=0.0) == 0.0


# ── compute_solax_tier1 ───────────────────────────────────────────────────────

_SOLAX_T1 = dict(
    mpc_batt_cmd=0.0,
    share=0.33,
    solax_soc=50.0,
    solax_soc_min=20.0,
    solax_soc_max=95.0,
    solax_max_charge=2400.0,
    solax_max_discharge=2400.0,
    grid_after_voltx=0.0,
    import_limit=10000.0,
    export_limit=10000.0,
    headroom_reserve=3000.0,
    suppress_charge=False,
    prev_solax_cmd=0.0,
)


def solax_t1(**overrides) -> tuple[float, SolaxMode]:
    return compute_solax_tier1(**{**_SOLAX_T1, **overrides})


def test_solax_tier1_charges_share_below_ceiling():
    """Below the headroom ceiling Solax executes its full share of the charge plan."""
    # share×mpc = 0.33 × -3000 = -990; grid 2000 is far below the 7000 W ceiling.
    cmd, mode = solax_t1(mpc_batt_cmd=-3000.0, grid_after_voltx=2000.0)
    assert cmd == -990.0
    assert mode == SolaxMode.FORCE_CHARGE


def test_solax_tier1_ramps_down_charge_near_ceiling():
    """As grid nears the ceiling the headroom clamp throttles Solax's charging share."""
    # grid_without_solax = 6500; floor = 6500 - 7000 = -500 → charge limited to -500.
    cmd, mode = solax_t1(mpc_batt_cmd=-3000.0, grid_after_voltx=6500.0)
    assert cmd == -500.0
    assert mode == SolaxMode.FORCE_CHARGE


def test_solax_tier1_suppress_charge_yields_to_voltx():
    """suppress_charge zeroes any charge (Voltx owns the ceiling) but allows discharge."""
    cmd, mode = solax_t1(mpc_batt_cmd=-3000.0, grid_after_voltx=6800.0, suppress_charge=True)
    assert cmd == 0.0
    assert mode == SolaxMode.SELF_CONSUMPTION


def test_solax_tier1_discharges_to_hold_ceiling():
    """Over the ceiling the headroom floor forces discharge even against a charge plan."""
    # grid_without_solax = 7500; floor = 7500 - 7000 = 500 → discharge 500 W.
    cmd, mode = solax_t1(mpc_batt_cmd=-3000.0, grid_after_voltx=7500.0)
    assert cmd == 500.0
    assert mode == SolaxMode.FORCE_DISCHARGE


def test_solax_tier1_tier2_term_acts_when_mpc_batt_zero():
    """tier2_term must move Solax even when mpc_batt_cmd/share contribute nothing.

    This is the case that left Solax idle before the 2026-07-09 tier-2 sharing fix:
    EMHASS plans zero battery use but a real grid deviation still needs correcting.
    """
    cmd, mode = solax_t1(mpc_batt_cmd=0.0, share=0.0, grid_after_voltx=0.0, tier2_term=750.0)
    assert cmd == 750.0
    assert mode == SolaxMode.FORCE_DISCHARGE


def test_solax_tier1_tier2_term_adds_to_share_term():
    """tier2_term and the mpc_batt share sum rather than overriding each other."""
    # share×mpc = 0.33×(-3000) = -990; + tier2_term 200 = -790.
    cmd, mode = solax_t1(mpc_batt_cmd=-3000.0, grid_after_voltx=2000.0, tier2_term=200.0)
    assert cmd == -790.0
    assert mode == SolaxMode.FORCE_CHARGE


# ── cap_combined_charge (EV proportional scale-back) ──────────────────────────

_CAP = dict(
    mpc_batt_cmd=0.0,
    grid_uncontrolled=0.0,
    import_limit=12000.0,
    headroom_reserve=3000.0,
)


def cap(**overrides) -> float:
    return cap_combined_charge(**{**_CAP, **overrides})


def test_cap_combined_charge_passes_through_when_within_headroom():
    """A combined charge that fits under the reduced ceiling is untouched."""
    # reduced ceiling = 12000 - 3000 = 9000; uncontrolled 4000 leaves 5000 W to charge.
    assert cap(mpc_batt_cmd=-4000.0, grid_uncontrolled=4000.0) == -4000.0


def test_cap_combined_charge_reduces_when_over_headroom():
    """A combined charge exceeding the headroom is capped to the reduced-ceiling floor.

    Splitting this reduced total by the SOC-balance share then scales both batteries by
    the same factor, so they stay balanced instead of Voltx grabbing the whole reserve.
    """
    # floor = 7000 - (12000 - 3000) = -2000 → charge limited to 2000 W combined.
    assert cap(mpc_batt_cmd=-5000.0, grid_uncontrolled=7000.0) == -2000.0


def test_cap_combined_charge_does_not_touch_discharge():
    """A discharge plan is never reduced (only charging preserves the reserve)."""
    assert cap(mpc_batt_cmd=2000.0, grid_uncontrolled=4000.0) == 2000.0


def test_cap_combined_charge_forces_discharge_over_ceiling():
    """When the both-batteries-removed grid already exceeds the reduced ceiling the floor
    is positive, forcing a proportional discharge both batteries share to defend it."""
    # floor = 10000 - 9000 = 1000 → even a charge plan is lifted to +1000 W discharge.
    assert cap(mpc_batt_cmd=-3000.0, grid_uncontrolled=10000.0) == 1000.0


# ── compute_ev_current_limit ──────────────────────────────────────────────────

_EV = dict(
    projected_grid=8000.0,
    target_grid=7000.0,
    ev_power=3680.0,
    watts_per_amp=230.0,
    min_current=5.0,
    max_current=16.0,
    prev_limit=16.0,
    release_ramp_step=1.0,
    release_ready=False,
)


def ev(**overrides) -> tuple[float, bool]:
    return compute_ev_current_limit(**{**_EV, **overrides})


def test_ev_inactive_below_ceiling():
    """Grid below the ceiling and not throttling → stays at max, inactive."""
    limit, active = ev(projected_grid=4000.0, prev_limit=16.0)
    assert limit == 16.0
    assert active is False


def test_ev_engages_and_sheds_overshoot():
    """Over the ceiling, current is cut to shed exactly the overshoot from the EV."""
    # overshoot = 9300 - 7000 = 2300; new = (3680 - 2300) / 230 = 6 A.
    limit, active = ev(projected_grid=9300.0, ev_power=3680.0, prev_limit=16.0)
    assert limit == pytest.approx(6.0)
    assert active is True


def test_ev_ratchets_down_only():
    """While over the ceiling the cap only lowers, never raises, even as the EV obeys."""
    # overshoot = 7460 - 7000 = 460; target = (2760 - 460) / 230 = 10; min(prev=12, 10) = 10.
    limit, active = ev(projected_grid=7460.0, ev_power=2760.0, prev_limit=12.0)
    assert limit == pytest.approx(10.0)
    assert active is True


def test_ev_overshoot_exceeds_ev_power_clamps_to_min():
    """A huge overshoot drives the target below zero; result floors at min_current."""
    limit, active = ev(projected_grid=12000.0, ev_power=1000.0, prev_limit=8.0)
    assert limit == 5.0
    assert active is True


def test_ev_holds_during_release_holdoff():
    """Recovered but holdoff not yet satisfied → hold the cap, still active."""
    limit, active = ev(projected_grid=6500.0, prev_limit=5.0, release_ready=False)
    assert limit == 5.0
    assert active is True


def test_ev_releases_ramps_up_when_ready():
    """Sustained recovery ramps the cap back up by release_ramp_step."""
    limit, active = ev(projected_grid=6500.0, prev_limit=5.0, release_ready=True)
    assert limit == 6.0
    assert active is True


def test_ev_release_reaches_max_deactivates():
    """Once the cap is back at max, the throttle reports inactive (handed back)."""
    limit, active = ev(projected_grid=6500.0, prev_limit=15.0, release_ready=True)
    assert limit == 16.0
    assert active is False


def test_ev_never_exceeds_max():
    """The ramp-up never overshoots max_current."""
    limit, active = ev(projected_grid=6500.0, prev_limit=16.0, release_ready=True)
    assert limit == 16.0
    assert active is False


# ── build_coordinator_data ────────────────────────────────────────────────────


# ── grid_priority + Solax multi-tick regression ────────────────────────────────
#
# Reproduces the 2026-07-09 production incident end-to-end by driving several
# ticks through the SAME dispatch policy coordinator.py uses (mode-based routing
# between compute_solax_command's residual path and the tier1/tier2 share path),
# feeding each tick's commands back into the next tick's grid_actual. Pure
# budget.py functions only — no HA — so this runs anywhere the rest of the suite
# does, and pins the emergent (not just per-call) behavior of the fix.

def _run_grid_priority_ticks(load_schedule, *, ticks, ramp_step=300.0, tier2_gain=0.5,
                              voltx_cap=10.0, solax_cap=5.0, sensitivity=0.01, soc_deadband=5.0):
    """Drive `ticks` iterations of grid_priority tracking (target=0) against a
    per-tick uncontrolled-load schedule, mirroring coordinator.py's dispatch.

    Returns a list of (voltx_cmd, solax_cmd, grid_actual) per tick.
    """
    prev_voltx = prev_solax = 0.0
    voltx_soc = solax_soc = 75.0
    history = []
    for tick in range(ticks):
        true_load = load_schedule(tick)
        grid_actual = true_load - prev_voltx - prev_solax

        solax_tier2_share = compute_solax_share(
            voltx_soc=voltx_soc, solax_soc=solax_soc,
            voltx_capacity_kwh=voltx_cap, solax_capacity_kwh=solax_cap,
            cmd=grid_actual - 0.0, sensitivity=sensitivity, soc_deadband=soc_deadband,
        )
        solax_share = compute_solax_share(
            voltx_soc=voltx_soc, solax_soc=solax_soc,
            voltx_capacity_kwh=voltx_cap, solax_capacity_kwh=solax_cap,
            cmd=0.0, sensitivity=sensitivity, soc_deadband=soc_deadband,
        )
        voltx_tier2_gain = tier2_gain * (1.0 - solax_tier2_share)
        solax_tier2_term = tier2_gain * solax_tier2_share * (grid_actual - 0.0)

        command, mode, _diag = compute_voltx_command(
            grid_actual=grid_actual, grid_target=0.0, mpc_batt_cmd=0.0,
            prev_cmd=prev_voltx, soc=voltx_soc, soc_min=20.0, soc_max=95.0,
            max_charge=5000.0, max_discharge=9000.0,
            import_limit=5000.0, export_limit=5000.0, ramp_step=ramp_step,
            plan_is_stale=False, tracking_deadband=0.0, headroom_reserve=0.0,
            tier2_gain=voltx_tier2_gain, grid_priority=True,
        )
        grid_after_voltx = grid_actual + prev_voltx - command

        if mode in SOLAX_RESIDUAL_MODES:
            solax_cmd, _solax_mode = compute_solax_command(
                voltx_mode=mode, grid_after_voltx=grid_after_voltx, grid_target=0.0,
                solax_soc=solax_soc, solax_soc_min=20.0, solax_soc_max=95.0,
                solax_max_charge=3000.0, solax_max_discharge=3000.0,
                import_limit=5000.0, export_limit=5000.0, headroom_reserve=0.0,
                prev_solax_cmd=prev_solax,
            )
        elif solax_share > 0 or solax_tier2_share > 0:
            solax_cmd, _solax_mode = compute_solax_tier1(
                mpc_batt_cmd=0.0, share=solax_share,
                solax_soc=solax_soc, solax_soc_min=20.0, solax_soc_max=95.0,
                solax_max_charge=3000.0, solax_max_discharge=3000.0,
                grid_after_voltx=grid_after_voltx, import_limit=5000.0, export_limit=5000.0,
                headroom_reserve=0.0, suppress_charge=False, prev_solax_cmd=prev_solax,
                tier2_term=solax_tier2_term,
            )
        else:
            solax_cmd = 0.0

        history.append((command, solax_cmd, grid_actual))
        prev_voltx, prev_solax = command, solax_cmd
    return history


def test_grid_priority_does_not_route_through_full_residual():
    """Pins the real dispatch table (shared by budget.py's guard and coordinator.py's
    routing) rather than a value hardcoded in the test — so this fails if
    GRID_PRIORITY is ever re-added to SOLAX_RESIDUAL_MODES, regardless of which of
    the two call sites regresses."""
    assert CoordinatorMode.GRID_PRIORITY not in SOLAX_RESIDUAL_MODES
    assert SOLAX_RESIDUAL_MODES == (
        CoordinatorMode.SOC_FLOOR,
        CoordinatorMode.SOC_CEILING,
        CoordinatorMode.CHARGE_LIMIT,
        CoordinatorMode.DISCHARGE_LIMIT,
    )


def test_grid_priority_standoff_resolves_via_proportional_share():
    """A load spike then drop under grid_priority must NOT leave Voltx and Solax
    permanently fighting each other (the 2026-07-09 incident): Voltx discharging
    hard while Solax holds a large opposing charge, indefinitely.

    Reproduces the incident's shape: a load spike (both batteries help hold grid
    to target), then the load drops back off — the transient that triggered the
    freeze, since Voltx's ramp lags the instantaneous drop.
    """
    def load(tick):
        if tick < 5:
            return 500.0
        if tick < 25:
            return 8000.0
        return 500.0

    history = _run_grid_priority_ticks(load, ticks=80)
    final_voltx, final_solax, final_grid = history[-1]

    # Settles on the true load being served by Voltx alone, not a permanent
    # opposite-direction split (e.g. voltx=1700/solax=-1200 in the buggy version).
    assert final_voltx == pytest.approx(500.0, abs=50)
    assert final_solax == pytest.approx(0.0, abs=50)
    assert final_grid == pytest.approx(0.0, abs=50)


def test_grid_priority_solax_still_helps_during_transient():
    """Solax should still assist while Voltx's ramp is catching up to a load spike
    (the original 2026-07-09 SOC-drift motivation) — just not via the frozen
    full-residual path."""
    def load(tick):
        return 500.0 if tick < 5 else 8000.0

    history = _run_grid_priority_ticks(load, ticks=8)
    # Mid-ramp, Solax should be contributing a nonzero discharge assist.
    _voltx, solax_cmd, _grid = history[6]
    assert solax_cmd > 0


def test_build_coordinator_data_headroom():
    data = build_coordinator_data(
        mode=CoordinatorMode.EMHASS_TRACKING,
        grid_actual=2000.0,
        grid_target=500.0,
        voltx_command=1500.0,
        import_limit=12000.0,
        export_limit=10000.0,
        plan_age_minutes=1.0,
    )
    assert isinstance(data, CoordinatorData)
    assert data.import_headroom == pytest.approx(10000.0)  # 12000 - 2000
    assert data.export_headroom == pytest.approx(10000.0)  # 10000 - 0
    assert data.plan_age_minutes == 1.0
    assert data.override_mode is None
