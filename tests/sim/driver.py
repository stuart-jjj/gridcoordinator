"""Closed-loop tick driver: runs the pure controller functions the way
coordinator.py's _async_update_data orders them, against sim/plant.py.

Only the parts the self-consumption handoff touches are reproduced (EMA, Solax share
split, handoff decision, Voltx tracking command, Solax tier-1 / follow command).  The
decision and command maths are the real functions from budget.py, so a change there
is exercised here; the *ordering* below mirrors the coordinator and must be kept in
step with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from custom_components.grid_coordinator.budget import (
    ScState,
    compute_solax_follow,
    compute_solax_share,
    compute_solax_tier1,
    compute_voltx_command,
    decide_self_consumption,
    ema_update,
    should_hold_self_consumption,
)
from custom_components.grid_coordinator.const import (
    DEFAULT_IMPORT_LIMIT,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_RAMP_STEP,
    DEFAULT_SOC_BALANCE_DEADBAND,
    DEFAULT_SOC_BALANCE_SENSITIVITY,
    DEFAULT_TIER2_GAIN,
    DEFAULT_TRACKING_DEADBAND,
)
from custom_components.grid_coordinator.models import SolaxMode

from .plant import Battery, Plant

TICK_S = 10.0


@dataclass(frozen=True)
class Policy:
    """Which handoff behaviour the run uses.  `legacy` is today's main branch."""

    legacy: bool
    deadband: float = 200.0
    tolerance: float = 300.0
    min_dwell_s: float = 120.0
    smoothing_s: float = 60.0
    solax_follow: bool = True


LEGACY = Policy(legacy=True, solax_follow=False)
NEW = Policy(legacy=False)


@dataclass
class Result:
    ticks: int = 0
    transitions: int = 0
    export_wh: float = 0.0
    import_wh: float = 0.0
    max_opposing_run: int = 0  # longest run of consecutive ticks with Voltx/Solax opposed
    solax_wh: float = 0.0      # |Solax| throughput
    voltx_wh: float = 0.0      # |Voltx| throughput
    solax_handoff_wh: float = 0.0  # |Solax| throughput on ticks where the handoff was active
    active_ticks: int = 0
    solax_modes: set = field(default_factory=set)
    grid: list = field(default_factory=list)
    active: list = field(default_factory=list)
    voltx: list = field(default_factory=list)
    solax: list = field(default_factory=list)


Inputs = Callable[[float], tuple[float, float, float, float, float | None]]
# t_s -> (load_w, solar_w, grid_target, mpc_batt, voltx_power_override_or_None)


def make_plant(voltx_soc: float = 50.0, solax_soc: float = 50.0) -> Plant:
    return Plant(
        Battery(soc=voltx_soc, capacity_kwh=10.0, max_charge=5000.0, max_discharge=5000.0),
        Battery(soc=solax_soc, capacity_kwh=7.0, max_charge=3000.0, max_discharge=3000.0),
    )


def run(
    policy: Policy,
    inputs: Inputs,
    duration_s: float,
    plant: Plant | None = None,
    *,
    open_loop_power: bool = False,
) -> Result:
    """Run `duration_s` of ticks.  With open_loop_power the Voltx power fed to the
    decision comes from inputs()'s 5th element (replaying a recording) instead of the
    plant, and the plant's grid is not meaningful."""
    plant = plant or make_plant()
    res = Result()
    state = ScState()
    power_ema: float | None = None
    prev_cmd = 0.0
    solax_last = 0.0
    grid = 0.0
    last_active: bool | None = None
    opposing_run = 0
    t = 0.0
    while t <= duration_s:
        load, solar, target, mpc, recorded_power = inputs(t)
        voltx_power = recorded_power if open_loop_power else plant.voltx.power
        power_ema = ema_update(power_ema, voltx_power, TICK_S, policy.smoothing_s)

        s1 = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=mpc, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )
        s2 = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=grid - target, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )
        voltx_setpoint = mpc * (1.0 - s1)
        # While following, the share is keyed off the direction Voltx is actually moving.
        s_follow = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=voltx_power, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )

        breach = grid > DEFAULT_IMPORT_LIMIT or grid < -DEFAULT_EXPORT_LIMIT
        if policy.legacy:
            want = should_hold_self_consumption(
                target, mpc, policy.deadband, state.active, allow_discharge_plan=False
            )
            if want != state.active:
                state = ScState(active=want, last_transition_at=t)
        else:
            state = decide_self_consumption(
                state=state, now=t, effective_target=target, voltx_setpoint=voltx_setpoint,
                smoothed_voltx_power=power_ema, deadband=policy.deadband,
                tolerance=policy.tolerance, min_dwell_s=policy.min_dwell_s, force_exit=breach,
            )

        if state.active:
            voltx_cmd = 0.0
            if policy.solax_follow:
                solax_cmd, solax_mode = compute_solax_follow(
                    voltx_power=voltx_power, share=s_follow, solax_soc=plant.solax.soc,
                    solax_soc_min=20.0, solax_soc_max=95.0,
                    solax_max_charge=3000.0, solax_max_discharge=3000.0,
                    grid_actual=grid, import_limit=DEFAULT_IMPORT_LIMIT,
                    export_limit=DEFAULT_EXPORT_LIMIT, prev_solax_cmd=solax_last,
                )
            else:
                solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION
            prev_cmd = 0.0
        else:
            voltx_cmd, _mode, _diag = compute_voltx_command(
                grid_actual=grid, grid_target=target, mpc_batt_cmd=voltx_setpoint,
                prev_cmd=prev_cmd, soc=plant.voltx.soc, soc_min=20.0, soc_max=95.0,
                max_charge=5000.0, max_discharge=5000.0, import_limit=DEFAULT_IMPORT_LIMIT,
                export_limit=DEFAULT_EXPORT_LIMIT, ramp_step=DEFAULT_RAMP_STEP,
                plan_is_stale=False, tracking_deadband=DEFAULT_TRACKING_DEADBAND,
                tier2_gain=DEFAULT_TIER2_GAIN * (1.0 - s2),
            )
            grid_after_voltx = grid + prev_cmd - voltx_cmd
            solax_cmd, solax_mode = compute_solax_tier1(
                mpc_batt_cmd=mpc, share=s1, solax_soc=plant.solax.soc,
                solax_soc_min=20.0, solax_soc_max=95.0, solax_max_charge=3000.0,
                solax_max_discharge=3000.0, grid_after_voltx=grid_after_voltx,
                import_limit=DEFAULT_IMPORT_LIMIT, export_limit=DEFAULT_EXPORT_LIMIT,
                prev_solax_cmd=solax_last,
                tier2_term=DEFAULT_TIER2_GAIN * s2 * (grid - target),
            )
            prev_cmd = voltx_cmd
        solax_last = solax_cmd

        grid = plant.step(
            dt_s=TICK_S, load_w=load, solar_w=solar, voltx_native=state.active,
            voltx_cmd=voltx_cmd, solax_cmd=solax_cmd,
        )

        res.ticks += 1
        res.active_ticks += int(state.active)
        if last_active is not None and state.active != last_active:
            res.transitions += 1
        last_active = state.active
        res.export_wh += max(0.0, -grid) * TICK_S / 3600.0
        res.import_wh += max(0.0, grid) * TICK_S / 3600.0
        # Opposed = signs differ with both more than ~50 W from zero.
        if plant.voltx.power * plant.solax.power < -2500.0:
            opposing_run += 1
            res.max_opposing_run = max(res.max_opposing_run, opposing_run)
        else:
            opposing_run = 0
        res.voltx_wh += abs(plant.voltx.power) * TICK_S / 3600.0
        res.solax_wh += abs(plant.solax.power) * TICK_S / 3600.0
        if state.active:
            res.solax_handoff_wh += abs(plant.solax.power) * TICK_S / 3600.0
        res.solax_modes.add(solax_mode)
        res.grid.append(grid)
        res.active.append(state.active)
        res.voltx.append(plant.voltx.power)
        res.solax.append(plant.solax.power)
        t += TICK_S
    return res
