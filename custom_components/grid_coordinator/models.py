"""Data models for grid_coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CoordinatorMode(StrEnum):
    """Operating mode / active constraint reported by the coordinator."""

    DISABLED = "disabled"               # emhass_control_active is off
    SELF_CONSUMPTION = "self_consumption"  # grid_target within deadband; inverter in self-consumption mode
    READ_ERROR = "read_error"           # a critical entity was unavailable
    EMHASS_TRACKING = "emhass_tracking"  # normal: following mpc_grid_power
    STALE_PLAN = "stale_plan"           # EMHASS plan too old; holding zero grid target
    IMPORT_CEILING = "import_ceiling"   # import limit is the binding constraint
    EXPORT_CEILING = "export_ceiling"   # export limit is the binding constraint
    SOC_FLOOR = "soc_floor"             # battery at min SOC, charging suppressed
    SOC_CEILING = "soc_ceiling"         # battery at max SOC, discharging suppressed
    # Manual override modes (set via service call or override select entity)
    OVERRIDE_SELF_CONSUME = "override_self_consume"
    OVERRIDE_HOLD_SOC = "override_hold_soc"
    OVERRIDE_FORCE_CHARGE = "override_force_charge"
    OVERRIDE_FORCE_EXPORT = "override_force_export"
    OVERRIDE_DISABLED = "override_disabled"


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
