"""Pure power budget arithmetic — zero HA dependencies, fully unit-testable."""

from __future__ import annotations

from .models import CoordinatorData, CoordinatorMode, SolaxMode


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
) -> tuple[float, CoordinatorMode]:
    """Compute the Voltx battery command for one 10 s tick.

    Sign conventions (all Watts):
      grid_actual / grid_target : positive = import from grid, negative = export
      mpc_batt_cmd / voltx cmd  : positive = discharge (reduces import / raises export)
                                  negative = charge   (raises import / reduces export)

    Two-tier control:
      Tier 1 — EMHASS battery setpoint (mpc_batt_cmd): executes the LP decision variable
               directly, exactly what the optimiser solved for.
      Tier 2 — Grid correction (grid_actual − grid_target): proportional adjustment for
               the gap between EMHASS forecast and actual conditions this tick.

      raw_cmd = mpc_batt_cmd + (grid_actual − grid_target)

    When the forecast is accurate (grid_actual ≈ grid_target), correction → 0 and the
    battery tracks mpc_batt_cmd exactly.  When load or solar deviates from the LP
    forecast, the correction term restores grid balance without waiting for the next
    5-minute EMHASS re-solve.  No integral wind-up: the anchor resets to the EMHASS
    plan each tick rather than accumulating prev_cmd.

    prev_cmd is still used for:
      - Estimating uncontrolled power: P_uncontrolled ≈ grid_actual + prev_cmd
      - Ramp limiting: smooth transitions between ticks

    Hard grid limit bounds on the new command:
      projected_grid = P_uncontrolled − new_cmd  must stay in [−export_limit, +import_limit]
      ⟹  cmd_floor = P_uncontrolled − import_limit
          cmd_ceil  = P_uncontrolled + export_limit

    headroom_reserve tightens the effective import limit so that the specified
    number of watts remains available for transient loads (e.g. oven spike).

    Returns (command_W rounded to int, active_mode).
    """
    # Tracking deadband — hold the current command when the grid error is small.
    # Prevents command chatter from measurement noise and small load fluctuations.
    # Grid safety limits are not re-evaluated here; prev_cmd was already safe.
    # Skip the early return when headroom is active: the oven may have just turned on
    # and the charging floor must be enforced even if the grid error is within the band.
    if abs(grid_actual - grid_target) <= tracking_deadband and headroom_reserve == 0:
        mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.EMHASS_TRACKING
        return round(prev_cmd), mode

    uncontrolled = grid_actual + prev_cmd
    # Tighten the import floor by headroom_reserve so charging is reduced to keep
    # import capacity available for transient loads (e.g. oven spike).
    cmd_floor = uncontrolled - (import_limit - headroom_reserve)
    cmd_ceil = uncontrolled + export_limit

    # Two-tier: EMHASS battery setpoint + proportional grid correction.
    raw_cmd = mpc_batt_cmd + (grid_actual - grid_target)

    mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.EMHASS_TRACKING

    # SOC constraints (checked before inverter limits so mode is set correctly)
    # cmd > 0 = discharge; cmd < 0 = charge
    if raw_cmd > 0 and soc <= soc_min:
        raw_cmd = 0.0
        mode = CoordinatorMode.SOC_FLOOR
    elif raw_cmd < 0 and soc >= soc_max:
        raw_cmd = 0.0
        mode = CoordinatorMode.SOC_CEILING

    # Inverter physical limits
    raw_cmd = max(-max_charge, min(max_discharge, raw_cmd))

    # Ramp — smooth transitions; grid safety clamp below can override it
    delta = raw_cmd - prev_cmd
    ramped_cmd = prev_cmd + max(-ramp_step, min(ramp_step, delta))

    # Hard grid limit clamp — overrides ramp if needed to stay within limits
    final_cmd = max(cmd_floor, min(cmd_ceil, ramped_cmd))

    if final_cmd > ramped_cmd + 1:
        mode = CoordinatorMode.LOAD_HEADROOM if headroom_reserve > 0 else CoordinatorMode.IMPORT_CEILING
    elif final_cmd < ramped_cmd - 1:
        mode = CoordinatorMode.EXPORT_CEILING

    # Re-apply inverter physical limits last so the written command is always
    # deliverable even when the grid-safety clamp demands more than the inverter
    # can provide (overloaded scenario: grid_power > import_limit + max_discharge).
    final_cmd = max(-max_charge, min(max_discharge, final_cmd))

    return round(final_cmd), mode


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
) -> tuple[float, SolaxMode]:
    """Compute the Solax priority-2 battery command for one 10 s tick.

    Only activates when Voltx is at a SOC boundary (SOC_FLOOR or SOC_CEILING) and
    there is a residual tracking error Voltx cannot cover.

    Sign convention: same as voltx_command — positive = discharge, negative = charge.
    grid_after_voltx: projected grid power after Voltx command is applied,
                      i.e. (grid_actual + prev_voltx_cmd) − voltx_cmd.
    """
    if voltx_mode not in (CoordinatorMode.SOC_FLOOR, CoordinatorMode.SOC_CEILING):
        return 0.0, SolaxMode.SELF_CONSUMPTION

    # Hard grid-safety bounds for Solax, accounting for Voltx already applied.
    # Grid equation: P_grid = grid_after_voltx − solax_cmd
    # → cmd must stay in [grid_after_voltx − import_limit, grid_after_voltx + export_limit]
    grid_limit_floor = grid_after_voltx - import_limit
    grid_limit_ceil = grid_after_voltx + export_limit

    # Residual error: additional discharge (+) or charge (−) needed after Voltx
    raw_cmd = grid_after_voltx - grid_target

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
