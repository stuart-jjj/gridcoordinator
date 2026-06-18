"""Pure power budget arithmetic — zero HA dependencies, fully unit-testable."""

from __future__ import annotations

from .models import CoordinatorData, CoordinatorMode, SolaxMode, VoltxDiag


def compute_voltx_command(
    *,
    grid_actual: float,
    grid_target: float,
    mpc_batt_cmd: float,
    prev_cmd: float,
    soc: float,
    soc_min: float,
    soc_max: float,
    max_charge: float,
    max_discharge: float,
    import_limit: float,
    export_limit: float,
    ramp_step: float,
    plan_is_stale: bool,
    tracking_deadband: float = 0.0,
    headroom_reserve: float = 0.0,
    tier2_gain: float = 1.0,
    grid_priority: bool = False,
    grid_smoothed: float | None = None,
    transient_active: bool = False,
    discharge_ramp_step: float | None = None,
) -> tuple[float, CoordinatorMode, VoltxDiag]:
    """Compute the Voltx battery command for one 10 s tick.

    Sign conventions (all Watts):
      grid_actual / grid_target : positive = import from grid, negative = export
      mpc_batt_cmd / voltx cmd  : positive = discharge (reduces import / raises export)
                                  negative = charge   (raises import / reduces export)

    Two-tier control:
      Tier 1 — EMHASS battery setpoint (mpc_batt_cmd): executes the LP decision variable
               directly, exactly what the optimiser solved for.
      Tier 2 — Grid correction: proportional adjustment for the gap between EMHASS
               forecast and actual conditions this tick, scaled by tier2_gain.

      raw_cmd = mpc_batt_cmd + tier2_gain × (grid_actual − grid_target)

    When the forecast is accurate (grid_actual ≈ grid_target), correction → 0 and the
    battery tracks mpc_batt_cmd exactly.  When load or solar deviates from the LP
    forecast, the correction term restores grid balance without waiting for the next
    5-minute EMHASS re-solve.  No integral wind-up: the anchor resets to the EMHASS
    plan each tick rather than accumulating prev_cmd.

    tier2_gain < 1.0 damps the correction so it converges geometrically rather than
    hunting.  At gain=1.0 (legacy) the full error is applied each tick, which can
    produce a 2-tick oscillation when the ramp step bounds the response.

    grid_priority overrides the two-tier blend entirely with deadbeat grid tracking:

      raw_cmd = uncontrolled − grid_target

    This anchors on uncontrolled power instead of the battery setpoint, so the
    command drives grid to grid_target in a single ramp-limited step with zero
    steady-state offset (the +prev_cmd inside `uncontrolled` cancels the −cmd
    feedback term → eigenvalue 0, no hunting).  Use it when holding the grid to
    target matters more than executing the EMHASS battery plan — e.g. high-price
    zero-import periods where the two-tier droop would otherwise leave a residual
    import of (forecast_error / (1 + tier2_gain)).

    prev_cmd is still used for:
      - Estimating uncontrolled power: P_uncontrolled ≈ grid_actual + prev_cmd
      - Ramp limiting: smooth transitions between ticks

    Hard grid limit bounds on the new command:
      projected_grid = P_uncontrolled − new_cmd  must stay in [−export_limit, +import_limit]
      ⟹  cmd_floor = P_uncontrolled − import_limit
          cmd_ceil  = P_uncontrolled + export_limit

    headroom_reserve tightens the effective import limit so that the specified
    number of watts remains available for transient loads (e.g. oven spike).

    transient_active engages high grid-variance damping for a rapidly cycling
    load (oven/cooktop thermostat).  Two effects, both confined to the transient:
      - The tracking error is driven from grid_smoothed (an EMA of the grid)
        instead of the raw reading, so the battery tracks the *average* load
        rather than aliasing the fast on/off cycling.
      - The ramp becomes asymmetric: discharge *increases* are limited to
        discharge_ramp_step (slow) while discharge *decreases* keep the normal
        ramp_step (fast).  This settles the battery toward the load's lower
        envelope and sheds over-discharge within a tick, killing the export
        spike that otherwise occurs when the element switches off.
    Safety quantities (uncontrolled power and the grid-safety clamp) always use
    the raw instantaneous grid_actual — the smoothing only feeds the correction.

    Returns (command_W rounded to int, active_mode, diagnostics).
    """
    uncontrolled = grid_actual + prev_cmd
    # During a high-variance transient, drive the tracking error from a smoothed
    # grid value so the battery tracks the average load instead of chasing fast
    # thermostatic cycling.  Falls back to raw grid when damping is off.
    grid_track = grid_smoothed if (transient_active and grid_smoothed is not None) else grid_actual
    # Tighten the import floor by headroom_reserve so charging is reduced to keep
    # import capacity available for transient loads (e.g. oven spike).
    cmd_floor = uncontrolled - (import_limit - headroom_reserve)
    cmd_ceil = uncontrolled + export_limit

    # Base tracking mode reported when no constraint binds.
    if plan_is_stale:
        tracking_mode = CoordinatorMode.STALE_PLAN
    elif grid_priority:
        tracking_mode = CoordinatorMode.GRID_PRIORITY
    else:
        tracking_mode = CoordinatorMode.EMHASS_TRACKING

    if grid_priority:
        # Deadbeat grid tracking: anchor on uncontrolled power, ignore the battery
        # setpoint. Drives grid to target in one ramp-limited step, no offset.
        raw_cmd = uncontrolled - grid_target
    else:
        # Two-tier: EMHASS battery setpoint + damped proportional grid correction.
        raw_cmd = mpc_batt_cmd + tier2_gain * (grid_track - grid_target)
    unclamped_cmd = raw_cmd  # preserved for diagnostics before constraints rewrite it

    # Tracking deadband — hold the current command when the grid error is small.
    # Prevents command chatter from measurement noise and small load fluctuations.
    # The held command is still clamped to the hard grid-safety limits below, since
    # under transient damping the deadband is tested on the smoothed grid.
    # Skip when headroom is active (oven may have just fired) or when mpc_batt_cmd
    # has moved significantly from prev_cmd — a new EMHASS plan must be acted on
    # even when the grid happens to be near target (e.g. solar-powered charging).
    if (abs(grid_track - grid_target) <= tracking_deadband
            and abs(mpc_batt_cmd - prev_cmd) <= tracking_deadband
            and headroom_reserve == 0):
        # Hold the current command, but still enforce the hard grid-safety limits
        # against the *raw* grid (cmd_floor/cmd_ceil derive from raw uncontrolled).
        # With transient damping the deadband is tested on grid_track (smoothed), so
        # a raw grid spike can sit outside the limits while the smoothed value is
        # within deadband — clamp here so the safety guarantee always holds.
        held_cmd = max(cmd_floor, min(cmd_ceil, prev_cmd))
        # Report the binding constraint even in deadband so Solax knows Voltx is bounded.
        # Without this, Solax successfully brings grid near target → deadband fires →
        # mode=emhass_tracking → Solax released → grid shoots up again (2-tick oscillation).
        # Guard SOC with prev_cmd direction: only propagate SOC_FLOOR when Voltx was already
        # zeroed/discharging (prev_cmd >= 0). If Voltx is actively charging (prev_cmd < 0)
        # the SOC floor is not the binding constraint and Solax should stay idle.
        if held_cmd > prev_cmd + 1:
            mode = CoordinatorMode.IMPORT_CEILING
        elif held_cmd < prev_cmd - 1:
            mode = CoordinatorMode.EXPORT_CEILING
        elif soc <= soc_min and prev_cmd >= 0:
            mode = CoordinatorMode.SOC_FLOOR
        elif soc >= soc_max and prev_cmd <= 0:
            mode = CoordinatorMode.SOC_CEILING
        else:
            mode = tracking_mode
        diag = VoltxDiag(
            deadband_hold=True,
            uncontrolled=uncontrolled,
            raw_cmd=unclamped_cmd,
            cmd_floor=cmd_floor,
            cmd_ceil=cmd_ceil,
            ramped_cmd=held_cmd,
            transient_active=transient_active,
        )
        return round(held_cmd), mode, diag

    mode = tracking_mode

    # SOC constraints (checked before inverter limits so mode is set correctly)
    # cmd > 0 = discharge; cmd < 0 = charge
    if raw_cmd > 0 and soc <= soc_min:
        raw_cmd = 0.0
        mode = CoordinatorMode.SOC_FLOOR
    elif raw_cmd < 0 and soc >= soc_max:
        raw_cmd = 0.0
        mode = CoordinatorMode.SOC_CEILING

    # Inverter physical limits
    if raw_cmd < -max_charge:
        raw_cmd = -max_charge
        mode = CoordinatorMode.CHARGE_LIMIT
    elif raw_cmd > max_discharge:
        raw_cmd = max_discharge
        mode = CoordinatorMode.DISCHARGE_LIMIT

    # Ramp — smooth transitions; grid safety clamp below can override it.
    # During a transient the ramp is asymmetric: discharge increases (delta > 0)
    # are limited to the slower discharge_ramp_step so the battery does not latch
    # to load peaks, while discharge decreases (delta < 0) keep the fast ramp_step
    # so any over-discharge into export is shed within a single tick.
    up_step = discharge_ramp_step if (transient_active and discharge_ramp_step is not None) else ramp_step
    delta = raw_cmd - prev_cmd
    ramped_cmd = prev_cmd + max(-ramp_step, min(up_step, delta))

    # Hard grid limit clamp — overrides ramp if needed to stay within limits
    final_cmd = max(cmd_floor, min(cmd_ceil, ramped_cmd))

    if final_cmd > ramped_cmd + 1:
        mode = CoordinatorMode.LOAD_HEADROOM if headroom_reserve > 0 else CoordinatorMode.IMPORT_CEILING
    elif final_cmd < ramped_cmd - 1:
        mode = CoordinatorMode.EXPORT_CEILING

    # Re-apply inverter physical limits last so the written command is always
    # deliverable even when the grid-safety clamp demands more than the inverter
    # can provide (overloaded scenario: grid_power > import_limit + max_discharge).
    if final_cmd < -max_charge:
        final_cmd = -max_charge
        mode = CoordinatorMode.CHARGE_LIMIT
    elif final_cmd > max_discharge:
        final_cmd = max_discharge
        mode = CoordinatorMode.DISCHARGE_LIMIT

    diag = VoltxDiag(
        deadband_hold=False,
        uncontrolled=uncontrolled,
        raw_cmd=unclamped_cmd,
        cmd_floor=cmd_floor,
        cmd_ceil=cmd_ceil,
        ramped_cmd=ramped_cmd,
        transient_active=transient_active,
    )
    return round(final_cmd), mode, diag


def compute_solax_tier1(
    *,
    mpc_batt_cmd: float,
    share: float,
    solax_soc: float,
    solax_soc_min: float,
    solax_soc_max: float,
    solax_max_charge: float,
    solax_max_discharge: float,
) -> tuple[float, SolaxMode]:
    """Compute Solax command as a direct share of the EMHASS battery setpoint.

    Used when Voltx is in normal tracking (not SOC-bounded): Solax executes
    (share × mpc_batt_cmd), while Voltx executes the remaining (1−share) fraction
    plus the tier-2 grid correction.  SOC limits and inverter physical limits apply;
    no grid-safety clamp (Voltx's computation already accounts for the full plan).
    """
    raw_cmd = mpc_batt_cmd * share

    if raw_cmd > 0 and solax_soc <= solax_soc_min:
        return 0.0, SolaxMode.SOC_FLOOR
    if raw_cmd < 0 and solax_soc >= solax_soc_max:
        return 0.0, SolaxMode.SOC_CEILING

    raw_cmd = max(-solax_max_charge, min(solax_max_discharge, raw_cmd))

    if raw_cmd > 0:
        return float(round(raw_cmd)), SolaxMode.FORCE_DISCHARGE
    if raw_cmd < 0:
        return float(round(raw_cmd)), SolaxMode.FORCE_CHARGE
    return 0.0, SolaxMode.SELF_CONSUMPTION


def compute_solax_command(
    *,
    voltx_mode: CoordinatorMode,
    grid_after_voltx: float,
    grid_target: float,
    solax_soc: float,
    solax_soc_min: float,
    solax_soc_max: float,
    solax_max_charge: float,
    solax_max_discharge: float,
    import_limit: float,
    export_limit: float,
    prev_solax_cmd: float = 0.0,
) -> tuple[float, SolaxMode]:
    """Compute the Solax priority-2 battery command for one 10 s tick.

    Only activates when Voltx is constrained (SOC_FLOOR, SOC_CEILING, CHARGE_LIMIT,
    or DISCHARGE_LIMIT) and there is a residual tracking error Voltx cannot cover.

    Sign convention: same as voltx_command — positive = discharge, negative = charge.
    grid_after_voltx: projected grid power after Voltx command is applied,
                      i.e. (grid_actual + prev_voltx_cmd) − voltx_cmd.
                      The grid sensor is net of all generation including Solax, so
                      prev_solax_cmd is added back to get the Solax-free baseline
                      before computing raw_cmd — analogous to how Voltx uses prev_cmd.
    """
    if voltx_mode not in (
        CoordinatorMode.SOC_FLOOR,
        CoordinatorMode.SOC_CEILING,
        CoordinatorMode.CHARGE_LIMIT,
        CoordinatorMode.DISCHARGE_LIMIT,
    ):
        return 0.0, SolaxMode.SELF_CONSUMPTION

    # Undo the current Solax contribution so raw_cmd represents the full residual.
    # Without this the controller only corrects the margin above the running Solax
    # output and converges to half the needed correction.
    grid_without_solax = grid_after_voltx + prev_solax_cmd

    # Hard grid-safety bounds for Solax.
    # Grid equation: P_grid = grid_without_solax − solax_cmd
    # → cmd must stay in [grid_without_solax − import_limit, grid_without_solax + export_limit]
    grid_limit_floor = grid_without_solax - import_limit
    grid_limit_ceil = grid_without_solax + export_limit

    # Residual error: discharge (+) or charge (−) needed to bring grid to target
    raw_cmd = grid_without_solax - grid_target

    # SOC constraints
    if raw_cmd > 0 and solax_soc <= solax_soc_min:
        return 0.0, SolaxMode.SOC_FLOOR
    if raw_cmd < 0 and solax_soc >= solax_soc_max:
        return 0.0, SolaxMode.SOC_CEILING

    # Physical limits
    raw_cmd = max(-solax_max_charge, min(solax_max_discharge, raw_cmd))

    # Hard grid-safety clamp
    final_cmd = max(grid_limit_floor, min(grid_limit_ceil, raw_cmd))

    if final_cmd > 0:
        mode = SolaxMode.FORCE_DISCHARGE
    elif final_cmd < 0:
        mode = SolaxMode.FORCE_CHARGE
    else:
        mode = SolaxMode.SELF_CONSUMPTION

    return float(round(final_cmd)), mode


def build_coordinator_data(
    *,
    mode: CoordinatorMode,
    grid_actual: float,
    grid_target: float,
    voltx_command: float,
    import_limit: float,
    export_limit: float,
    plan_age_minutes: float,
    override_mode: str | None = None,
    mpc_batt_power: float = 0.0,
    solax_command: float = 0.0,
    solax_mode: SolaxMode = SolaxMode.SELF_CONSUMPTION,
) -> CoordinatorData:
    """Construct CoordinatorData with derived headroom fields."""
    return CoordinatorData(
        mode=mode,
        grid_actual=grid_actual,
        grid_target=grid_target,
        voltx_command=voltx_command,
        import_headroom=import_limit - max(0.0, grid_actual),
        export_headroom=export_limit - max(0.0, -grid_actual),
        plan_age_minutes=round(plan_age_minutes, 1),
        override_mode=override_mode,
        mpc_batt_power=mpc_batt_power,
        solax_command=solax_command,
        solax_mode=solax_mode,
    )
