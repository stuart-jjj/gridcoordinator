"""Data models for grid_coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SolaxMode(StrEnum):
    """Operating mode of the Solax priority-2 battery."""

    SELF_CONSUMPTION = "self_consumption"  # not commanded; inverter manages itself
    FORCE_CHARGE = "force_charge"          # coordinator charging to absorb excess export
    FORCE_DISCHARGE = "force_discharge"    # coordinator discharging to reduce import
    SOC_FLOOR = "soc_floor"               # discharge needed but Solax also at SOC floor
    SOC_CEILING = "soc_ceiling"           # charge needed but Solax also at SOC ceiling


class CoordinatorMode(StrEnum):
    """Operating mode / active constraint reported by the coordinator."""

    DISABLED = "disabled"               # emhass_control_active is off
    SELF_CONSUMPTION = "self_consumption"  # grid_target within deadband; inverter in self-consumption mode
    READ_ERROR = "read_error"           # a critical entity was unavailable
    EMHASS_TRACKING = "emhass_tracking"  # normal: following mpc_grid_power
    GRID_PRIORITY = "grid_priority"     # deadbeat grid tracking; battery setpoint ignored to hold grid_target
    STALE_PLAN = "stale_plan"           # EMHASS plan too old; holding zero grid target
    IMPORT_CEILING = "import_ceiling"   # import limit is the binding constraint
    EXPORT_CEILING = "export_ceiling"   # export limit is the binding constraint
    CHARGE_LIMIT = "charge_limit"       # inverter max charge power is the binding constraint
    DISCHARGE_LIMIT = "discharge_limit"  # inverter max discharge power is the binding constraint
    SOC_FLOOR = "soc_floor"             # battery at min SOC, discharging suppressed
    SOC_CEILING = "soc_ceiling"         # battery at max SOC, charging suppressed
    EV_CHARGING = "ev_charging"              # EV detected; discharge suppressed to let grid absorb load
    LOAD_HEADROOM = "load_headroom"          # headroom reserved for monitored load; charging reduced
    # Manual override modes (set via service call or override select entity)
    OVERRIDE_SELF_CONSUME = "override_self_consume"
    OVERRIDE_HOLD_SOC = "override_hold_soc"
    OVERRIDE_FORCE_CHARGE = "override_force_charge"
    OVERRIDE_FORCE_EXPORT = "override_force_export"
    OVERRIDE_DISABLED = "override_disabled"


@dataclass(frozen=True)
class VoltxDiag:
    """Intermediate values from compute_voltx_command, surfaced for debug logging.

    These let a debug-log capture reconstruct exactly which constraint shaped the
    written command (or whether the tracking deadband held the previous one).
    """

    deadband_hold: bool   # True = tracking deadband returned prev_cmd unchanged
    uncontrolled: float   # estimated uncontrolled power: grid_actual + prev_cmd (W)
    raw_cmd: float        # tier-1 + tier-2 command before any constraint (W)
    cmd_floor: float      # grid-safety lower bound incl. headroom reserve (W)
    cmd_ceil: float       # grid-safety upper bound (W)
    ramped_cmd: float     # after SOC/physical limits and ramp, before grid clamp (W)
    transient_active: bool = False  # True = high grid-variance damping engaged this tick


@dataclass(frozen=True)
class CoordinatorData:
    """All outputs produced by one coordinator tick."""

    mode: CoordinatorMode
    grid_actual: float       # W, positive = import from grid
    grid_target: float       # W, positive = import desired (sign-corrected from EMHASS)
    voltx_command: float     # W sent to inverter; positive = discharge, negative = charge
    import_headroom: float   # W remaining before import limit is reached
    export_headroom: float   # W remaining before export limit is reached
    plan_age_minutes: float  # minutes since mpc_grid_power was last updated
    override_mode: str | None = None  # active override key; None when following EMHASS normally
    mpc_batt_power: float = 0.0        # W — EMHASS battery setpoint used this tick (positive = discharge)
    solax_command: float = 0.0          # W sent to Solax; positive = discharge, negative = charge
    solax_mode: SolaxMode = SolaxMode.SELF_CONSUMPTION  # Solax operating mode this tick
