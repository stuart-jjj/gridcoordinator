"""Constants for grid_coordinator."""

from __future__ import annotations

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)
DOMAIN = "grid_coordinator"

# ── Config entry keys ─────────────────────────────────────────────────────────
CONF_IMPORT_LIMIT = "import_limit"
CONF_EXPORT_LIMIT = "export_limit"
CONF_RAMP_STEP = "ramp_step"
CONF_PLAN_STALE_MINUTES = "plan_stale_minutes"
# EMHASS mpc_grid_power uses injection convention (positive = export to grid),
# opposite to IAMMeter (positive = import from grid). Set True to negate it.
CONF_MPC_SIGN_INVERTED = "mpc_sign_inverted"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_IMPORT_LIMIT = 12000        # W — safe single-phase limit
DEFAULT_EXPORT_LIMIT = 10000        # W — network export rule
DEFAULT_RAMP_STEP = 1500            # W per 10 s tick
DEFAULT_PLAN_STALE_MINUTES = 20     # minutes before plan treated as stale
DEFAULT_MPC_SIGN_INVERTED = True    # EMHASS default: positive = export to grid

# ── Update interval ───────────────────────────────────────────────────────────
UPDATE_INTERVAL_SECONDS = 10

# ── HA entity IDs (hardcoded MVP — will become config options in Phase 3) ─────

# Inputs
ENTITY_GRID_POWER = "sensor.iammeter_power_a"
# Convention: positive = import from grid, negative = export to grid

ENTITY_MPC_GRID_POWER = "sensor.mpc_grid_power"
# EMHASS output. Default: positive = export to grid (injection convention).
# Negated internally when CONF_MPC_SIGN_INVERTED is True so both are
# on the same "positive = import" basis for the PI controller.

ENTITY_VOLTX_SOC = "sensor.battery_state_of_charge"                            # %
ENTITY_VOLTX_MAX_CHARGE = "input_number.voltx_battery_max_charging_limit"      # W
ENTITY_VOLTX_MAX_DISCHARGE = "input_number.voltx_battery_max_discharging_limit"  # W
ENTITY_SOC_MIN = "number.soc_min"                                               # %
ENTITY_SOC_MAX = "number.soc_max"                                               # %

# Enable/disable gate — reuses the existing EMHASS control helper
ENTITY_ENABLED = "input_boolean.emhass_control_active"

# Outputs — Voltx via direct Modbus integration
ENTITY_VOLTX_CMD = "number.voltx_battery_battery_charge_discharge_power"
# Convention: positive = discharge (battery → AC), negative = charge (AC → battery)

ENTITY_VOLTX_WORK_MODE = "select.voltx_inverter_work_mode"
VOLTX_WORK_MODE_CUSTOM = "Custom"
