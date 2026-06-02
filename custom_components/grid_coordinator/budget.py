"""Pure power budget arithmetic — zero HA dependencies, fully unit-testable."""

from __future__ import annotations

from .models import CoordinatorData, CoordinatorMode


def compute_voltx_command(
    *,
    grid_actual: float,
    grid_target: float,
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
) -> tuple[float, CoordinatorMode]:
    """Compute the Voltx battery command for one 10 s tick.

    Sign conventions (all Watts):
      grid_actual / grid_target : positive = import from grid, negative = export
      voltx command             : positive = discharge (reduces import / raises export)
                                  negative = charge   (raises import / reduces export)

    The grid relationship is:
      P_grid = P_uncontrolled − P_voltx_cmd
    where P_uncontrolled = house loads − solar (approximately constant between ticks).

    Estimating uncontrolled from the previous tick:
      P_uncontrolled ≈ grid_actual + prev_cmd

    Hard grid limit bounds on the new command:
      projected_grid = P_uncontrolled − new_cmd  must stay in [−export_limit, +import_limit]
      ⟹  cmd_floor = P_uncontrolled − import_limit
          cmd_ceil  = P_uncontrolled + export_limit

    Returns (command_W rounded to int, active_mode).
    """
    # Tracking deadband — hold the current command when the grid error is small.
    # Prevents command chatter from measurement noise and small load fluctuations.
    # Grid safety limits are not re-evaluated here; prev_cmd was already safe.
    if abs(grid_actual - grid_target) <= tracking_deadband:
        mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.EMHASS_TRACKING
        return round(prev_cmd), mode

    uncontrolled = grid_actual + prev_cmd
    cmd_floor = uncontrolled - import_limit
    cmd_ceil = uncontrolled + export_limit

    # Pure-I controller: close the grid error in one step.
    # Positive error (importing more than target) → increase discharge (raise cmd).
    raw_cmd = prev_cmd + (grid_actual - grid_target)

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
        mode = CoordinatorMode.IMPORT_CEILING
    elif final_cmd < ramped_cmd - 1:
        mode = CoordinatorMode.EXPORT_CEILING

    # Re-apply inverter physical limits last so the written command is always
    # deliverable even when the grid-safety clamp demands more than the inverter
    # can provide (overloaded scenario: grid_power > import_limit + max_discharge).
    final_cmd = max(-max_charge, min(max_discharge, final_cmd))

    return round(final_cmd), mode


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
) -> CoordinatorData:
    """Construct CoordinatorData with derived headroom fields."""
    return CoordinatorData(
        mode=mode,
        grid_actual=grid_actual,
        grid_target=grid_target,
        voltx_command=voltx_command,
        import_headroom=import_limit - grid_actual,
        export_headroom=export_limit + grid_actual,
        plan_age_minutes=round(plan_age_minutes, 1),
        override_mode=override_mode,
    )
