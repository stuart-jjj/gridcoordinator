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
CONF_MPC_SIGN_INVERTED = "mpc_sign_inverted"
CONF_MPC_BATT_SIGN_INVERTED = "mpc_batt_sign_inverted"
CONF_ENTITY_MPC_BATT_POWER = "entity_mpc_batt_power"
CONF_TEST_MODE = "test_mode"
CONF_SELF_CONSUMPTION_MODE = "self_consumption_mode"
CONF_SELF_CONSUMPTION_DEADBAND = "self_consumption_deadband"
CONF_TRACKING_DEADBAND = "tracking_deadband"

# EV charge awareness
CONF_ENTITY_EV_CHARGER = "entity_ev_charger"
CONF_EV_CHARGER_THRESHOLD = "ev_charger_threshold"

# Monitored load 1 headroom
CONF_ENTITY_MON_LOAD_1 = "entity_monitored_load_1"
CONF_MON_LOAD_1_THRESHOLD = "monitored_load_1_threshold"
CONF_MON_LOAD_1_HEADROOM = "monitored_load_1_headroom"
CONF_MON_LOAD_1_HOLDOFF_MINUTES = "monitored_load_1_holdoff_minutes"

# Entity ID config keys
CONF_ENTITY_GRID_POWER = "entity_grid_power"
CONF_ENTITY_MPC_GRID_POWER = "entity_mpc_grid_power"
CONF_ENTITY_VOLTX_SOC = "entity_voltx_soc"
CONF_ENTITY_VOLTX_MAX_CHARGE = "entity_voltx_max_charge"
CONF_ENTITY_VOLTX_MAX_DISCHARGE = "entity_voltx_max_discharge"
CONF_ENTITY_SOC_MIN = "entity_soc_min"
CONF_ENTITY_SOC_MAX = "entity_soc_max"
CONF_ENTITY_ENABLED = "entity_enabled"
CONF_ENTITY_VOLTX_CMD = "entity_voltx_cmd"
CONF_ENTITY_VOLTX_WORK_MODE = "entity_voltx_work_mode"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_IMPORT_LIMIT = 12000        # W — safe single-phase limit
DEFAULT_EXPORT_LIMIT = 10000        # W — network export rule
DEFAULT_RAMP_STEP = 1500            # W per 10 s tick
DEFAULT_PLAN_STALE_MINUTES = 20     # minutes before plan treated as stale
DEFAULT_MPC_SIGN_INVERTED = False   # positive = import (matches grid sensor convention)
DEFAULT_MPC_BATT_SIGN_INVERTED = False  # positive = discharge (matches coordinator convention)
DEFAULT_SELF_CONSUMPTION_MODE = "Self-consumption"  # Voltx Modbus work-mode name
DEFAULT_SELF_CONSUMPTION_DEADBAND = 50  # W — |grid_target| below this → self-consumption
DEFAULT_TRACKING_DEADBAND = 200         # W — hold command if grid error is within this band
DEFAULT_OVERRIDE_DURATION_MINUTES = 60  # minutes before a manual override auto-expires
DEFAULT_EV_CHARGER_THRESHOLD = 500      # W — above this the EV is considered charging
DEFAULT_MON_LOAD_1_THRESHOLD = 10       # W — above this the monitored load is considered on
DEFAULT_MON_LOAD_1_HEADROOM = 6000      # W — import headroom to reserve when load is on
DEFAULT_MON_LOAD_1_HOLDOFF_MINUTES = 5  # minutes load must be off before headroom is released

# ── Update interval ───────────────────────────────────────────────────────────
UPDATE_INTERVAL_SECONDS = 10

# ── Production entity IDs ─────────────────────────────────────────────────────

ENTITY_GRID_POWER = "sensor.iammeter_power_a"
# Convention: positive = import from grid, negative = export to grid

ENTITY_MPC_GRID_POWER = "sensor.mpc_grid_power"
# EMHASS output — positive = import from grid (same as grid sensor).
# Set CONF_MPC_SIGN_INVERTED=True if your EMHASS uses injection convention (positive = export).

ENTITY_MPC_BATT_POWER = "sensor.mpc_battery_power"
# EMHASS output — positive = discharging (same as voltx_command convention).

ENTITY_VOLTX_SOC = "sensor.battery_state_of_charge"                            # %
ENTITY_VOLTX_MAX_CHARGE = "input_number.voltx_battery_max_charging_limit"      # W
ENTITY_VOLTX_MAX_DISCHARGE = "input_number.voltx_battery_max_discharging_limit"  # W
ENTITY_SOC_MIN = "number.soc_min"                                               # %
ENTITY_SOC_MAX = "number.soc_max"                                               # %

# Enable/disable gate — reuses the existing EMHASS control helper
ENTITY_ENABLED = "input_boolean.emhass_control_active"

# Optional feature entities (production defaults; leave blank to disable the feature)
ENTITY_EV_CHARGER = "sensor.iammeter_power_c"
ENTITY_MON_LOAD_1 = "sensor.oven_energy_monitor_power"

# Outputs — Voltx via direct Modbus integration
ENTITY_VOLTX_CMD = "number.voltx_battery_battery_charge_discharge_power"
# Convention: positive = discharge (battery → AC), negative = charge (AC → battery)

ENTITY_VOLTX_WORK_MODE = "select.voltx_inverter_work_mode"
VOLTX_WORK_MODE_CUSTOM = "Custom"

# ── Solax entity ID config keys ───────────────────────────────────────────────
CONF_ENTITY_SOLAX_SOC = "entity_solax_soc"
CONF_ENTITY_SOLAX_SOC_MIN = "entity_solax_soc_min"
CONF_ENTITY_SOLAX_SOC_MAX = "entity_solax_soc_max"
CONF_ENTITY_SOLAX_RC_POWER_CONTROL = "entity_solax_rc_power_control"
CONF_ENTITY_SOLAX_RC_ACTIVE_POWER = "entity_solax_rc_active_power"
CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION = "entity_solax_rc_autorepeat_duration"
CONF_ENTITY_SOLAX_RC_TRIGGER = "entity_solax_rc_trigger"

# ── Solax parameter config keys ────────────────────────────────────────────────
CONF_SOLAX_MAX_CHARGE = "solax_max_charge"
CONF_SOLAX_MAX_DISCHARGE = "solax_max_discharge"
CONF_SOLAX_CMD_DEADBAND = "solax_cmd_deadband"

# ── Solax defaults ─────────────────────────────────────────────────────────────
DEFAULT_SOLAX_MAX_CHARGE = 2400        # W — X1 AC G3 hardware limit
DEFAULT_SOLAX_MAX_DISCHARGE = 2400     # W
DEFAULT_SOLAX_AUTOREPEAT_DURATION = 20 # s — 2× the 10 s tick; hardware expires in 4 s
DEFAULT_SOLAX_CMD_DEADBAND = 50        # W — suppress command updates smaller than this

# Remote-control power mode names (as reported by homeassistant-solax-modbus)
SOLAX_RC_MODE_ENABLED = "Enabled Power Control"
SOLAX_RC_MODE_DISABLED = "Disabled"

# ── Solax production entity IDs ────────────────────────────────────────────────
# Hub name "solax" — must match the name configured in the Solax Modbus integration.
ENTITY_SOLAX_SOC = "sensor.solax_battery_capacity"
ENTITY_SOLAX_SOC_MIN = "number.solax_selfuse_discharge_min_soc"
ENTITY_SOLAX_SOC_MAX = "number.solax_battery_charge_upper_soc"
ENTITY_SOLAX_RC_POWER_CONTROL = "select.solax_remotecontrol_power_control"
ENTITY_SOLAX_RC_ACTIVE_POWER = "number.solax_remotecontrol_active_power"
ENTITY_SOLAX_RC_AUTOREPEAT_DURATION = "number.solax_remotecontrol_autorepeat_duration"
ENTITY_SOLAX_RC_TRIGGER = "button.solax_remotecontrol_trigger_gen3"

# ── Simulated entity IDs (created in testing mode) ────────────────────────────
SIM_ENTITY_SOLAX_SOC = "number.grid_coordinator_sim_solax_soc"
SIM_ENTITY_SOLAX_SOC_MIN = "number.grid_coordinator_sim_solax_soc_min"
SIM_ENTITY_SOLAX_SOC_MAX = "number.grid_coordinator_sim_solax_soc_max"
SIM_ENTITY_SOLAX_RC_POWER_CONTROL = "select.grid_coordinator_sim_solax_rc_power_control"
SIM_ENTITY_SOLAX_RC_ACTIVE_POWER = "number.grid_coordinator_sim_solax_rc_active_power"
SIM_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION = "number.grid_coordinator_sim_solax_rc_autorepeat_duration"
SIM_ENTITY_SOLAX_RC_TRIGGER = "button.grid_coordinator_sim_solax_rc_trigger"

SIM_ENTITY_GRID_POWER = "number.grid_coordinator_sim_grid_power"
SIM_ENTITY_MPC_GRID_POWER = "number.grid_coordinator_sim_mpc_grid_power"
SIM_ENTITY_MPC_BATT_POWER = "number.grid_coordinator_sim_mpc_batt_power"
SIM_ENTITY_VOLTX_SOC = "number.grid_coordinator_sim_battery_soc"
SIM_ENTITY_VOLTX_MAX_CHARGE = "number.grid_coordinator_sim_max_charge"
SIM_ENTITY_VOLTX_MAX_DISCHARGE = "number.grid_coordinator_sim_max_discharge"
SIM_ENTITY_SOC_MIN = "number.grid_coordinator_sim_soc_min"
SIM_ENTITY_SOC_MAX = "number.grid_coordinator_sim_soc_max"
SIM_ENTITY_ENABLED = "switch.grid_coordinator_sim_enabled"
SIM_ENTITY_VOLTX_CMD = "number.grid_coordinator_sim_battery_cmd"
SIM_ENTITY_VOLTX_WORK_MODE = "select.grid_coordinator_sim_work_mode"

# Maps config key → production default entity ID
ENTITY_ID_DEFAULTS: dict[str, str] = {
    # Voltx + grid
    CONF_ENTITY_GRID_POWER: ENTITY_GRID_POWER,
    CONF_ENTITY_MPC_GRID_POWER: ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_MPC_BATT_POWER: ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_VOLTX_SOC: ENTITY_VOLTX_SOC,
    CONF_ENTITY_VOLTX_MAX_CHARGE: ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE: ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_SOC_MIN: ENTITY_SOC_MIN,
    CONF_ENTITY_SOC_MAX: ENTITY_SOC_MAX,
    CONF_ENTITY_ENABLED: ENTITY_ENABLED,
    CONF_ENTITY_VOLTX_CMD: ENTITY_VOLTX_CMD,
    CONF_ENTITY_VOLTX_WORK_MODE: ENTITY_VOLTX_WORK_MODE,
    # Solax
    CONF_ENTITY_SOLAX_SOC: ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MIN: ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_SOLAX_SOC_MAX: ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL: ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER: ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION: ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_TRIGGER: ENTITY_SOLAX_RC_TRIGGER,
}

# Maps config key → simulated entity ID
SIM_ENTITY_IDS: dict[str, str] = {
    # Voltx + grid
    CONF_ENTITY_GRID_POWER: SIM_ENTITY_GRID_POWER,
    CONF_ENTITY_MPC_GRID_POWER: SIM_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_MPC_BATT_POWER: SIM_ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_VOLTX_SOC: SIM_ENTITY_VOLTX_SOC,
    CONF_ENTITY_VOLTX_MAX_CHARGE: SIM_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE: SIM_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_SOC_MIN: SIM_ENTITY_SOC_MIN,
    CONF_ENTITY_SOC_MAX: SIM_ENTITY_SOC_MAX,
    CONF_ENTITY_ENABLED: SIM_ENTITY_ENABLED,
    CONF_ENTITY_VOLTX_CMD: SIM_ENTITY_VOLTX_CMD,
    CONF_ENTITY_VOLTX_WORK_MODE: SIM_ENTITY_VOLTX_WORK_MODE,
    # Solax
    CONF_ENTITY_SOLAX_SOC: SIM_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MIN: SIM_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_SOLAX_SOC_MAX: SIM_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL: SIM_ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER: SIM_ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION: SIM_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_TRIGGER: SIM_ENTITY_SOLAX_RC_TRIGGER,
}
