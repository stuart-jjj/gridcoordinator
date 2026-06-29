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
CONF_TIER2_GAIN = "tier2_gain"
CONF_GRID_PRIORITY_BAND = "grid_priority_band"
CONF_ENTITY_GRID_PRIORITY = "entity_grid_priority"

# Transient (high grid-variance) load damping
CONF_TRANSIENT_VARIANCE_THRESHOLD = "transient_variance_threshold"
CONF_TRANSIENT_VARIANCE_WINDOW = "transient_variance_window"
CONF_TRANSIENT_EMA_ALPHA = "transient_ema_alpha"
CONF_TRANSIENT_DISCHARGE_RAMP_STEP = "transient_discharge_ramp_step"

# EV charge awareness
CONF_ENTITY_EV_CHARGER = "entity_ev_charger"
CONF_EV_CHARGER_THRESHOLD = "ev_charger_threshold"
CONF_EV_HEADROOM = "ev_headroom"

# EV emergency charge-current throttle (layer-3 backstop; usurps external EV control)
CONF_ENTITY_EV_CHARGE_CURRENT = "entity_ev_charge_current"
CONF_EV_EMERGENCY_THROTTLE = "ev_emergency_throttle"
CONF_EV_WATTS_PER_AMP = "ev_watts_per_amp"
CONF_EV_MIN_CHARGE_CURRENT = "ev_min_charge_current"
CONF_EV_MAX_CHARGE_CURRENT = "ev_max_charge_current"
CONF_EV_RELEASE_HOLDOFF_MINUTES = "ev_release_holdoff_minutes"
CONF_EV_RELEASE_RAMP_STEP = "ev_release_ramp_step"

# Monitored load 1 headroom
CONF_ENTITY_MON_LOAD_1 = "entity_monitored_load_1"
CONF_MON_LOAD_1_THRESHOLD = "monitored_load_1_threshold"
CONF_MON_LOAD_1_HEADROOM = "monitored_load_1_headroom"
CONF_MON_LOAD_1_HOLDOFF_MINUTES = "monitored_load_1_holdoff_minutes"

# Entity ID config keys
CONF_ENTITY_GRID_POWER = "entity_grid_power"
CONF_ENTITY_MPC_GRID_POWER = "entity_mpc_grid_power"
CONF_ENTITY_VOLTX_CAPACITY = "entity_voltx_capacity"
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
DEFAULT_TIER2_GAIN = 0.5                # fraction — damps tier-2 correction to prevent oscillation
DEFAULT_GRID_PRIORITY_BAND = 0          # W — |grid_target| ≤ this → deadbeat grid tracking; 0 disables auto-trigger
DEFAULT_TRANSIENT_VARIANCE_THRESHOLD = 300  # W — rolling grid stdev above this engages transient damping; 0 disables
DEFAULT_TRANSIENT_VARIANCE_WINDOW = 6        # ticks (×10 s) in the rolling stdev window
DEFAULT_TRANSIENT_EMA_ALPHA = 0.3            # EMA smoothing of grid for the tracking error during a transient
DEFAULT_TRANSIENT_DISCHARGE_RAMP_STEP = 150  # W/tick — slow ramp limit for discharge increases during a transient
DEFAULT_OVERRIDE_DURATION_MINUTES = 60  # minutes before a manual override auto-expires
DEFAULT_EV_CHARGER_THRESHOLD = 500      # W — above this the EV is considered charging
DEFAULT_EV_HEADROOM = 3000              # W — import headroom reserved below the ceiling while the EV charges
DEFAULT_EV_EMERGENCY_THROTTLE = False   # off by default — opt-in, it usurps the external EV controller
DEFAULT_EV_WATTS_PER_AMP = 230          # W/A — single-phase 230 V (use ~690 for 3-phase)
DEFAULT_EV_MIN_CHARGE_CURRENT = 5       # A — Tesla API minimum
DEFAULT_EV_MAX_CHARGE_CURRENT = 16      # A — restored on release; set to the charger/circuit maximum
DEFAULT_EV_RELEASE_HOLDOFF_MINUTES = 2  # minutes grid must stay below the ceiling before releasing
DEFAULT_EV_RELEASE_RAMP_STEP = 1        # A/tick — gradual restore toward max on release
EV_RELEASE_MARGIN = 500                 # W below the ceiling before the release timer starts (hysteresis)
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

ENTITY_MPC_BATT_POWER = "sensor.mpc_batt_power"
# EMHASS output — positive = discharging (same as voltx_command convention).

ENTITY_VOLTX_CAPACITY = "input_number.voltx_plant_rated_energy_capacity"        # kWh (rated, static)
ENTITY_VOLTX_SOC = "sensor.battery_state_of_charge"                            # %
ENTITY_VOLTX_MAX_CHARGE = "input_number.voltx_battery_max_charging_limit"      # W
ENTITY_VOLTX_MAX_DISCHARGE = "input_number.voltx_battery_max_discharging_limit"  # W
ENTITY_SOC_MIN = "number.soc_min"                                               # %
ENTITY_SOC_MAX = "number.soc_max"                                               # %

# Enable/disable gate — reuses the existing EMHASS control helper
ENTITY_ENABLED = "input_boolean.emhass_control_active"

# Optional feature entities (production defaults; leave blank to disable the feature)
ENTITY_EV_CHARGER = "sensor.iammeter_power_c"
ENTITY_EV_CHARGE_CURRENT = "number.ziggy_charge_current"  # EV charge-current setpoint (A), written only during emergency throttle
ENTITY_MON_LOAD_1 = "sensor.oven_energy_monitor_power"

# Outputs — Voltx via direct Modbus integration
ENTITY_VOLTX_CMD = "number.voltx_battery_battery_charge_discharge_power"
# Convention: positive = discharge (battery → AC), negative = charge (AC → battery)

ENTITY_VOLTX_WORK_MODE = "select.voltx_inverter_work_mode"
VOLTX_WORK_MODE_CUSTOM = "Custom"

# ── Solax entity ID config keys ───────────────────────────────────────────────
CONF_ENTITY_SOLAX_CAPACITY = "entity_solax_capacity"
CONF_ENTITY_SOLAX_SOC = "entity_solax_soc"
CONF_ENTITY_SOLAX_SOC_MIN = "entity_solax_soc_min"
CONF_ENTITY_SOLAX_SOC_MAX = "entity_solax_soc_max"
CONF_ENTITY_SOLAX_RC_POWER_CONTROL = "entity_solax_rc_power_control"
CONF_ENTITY_SOLAX_RC_ACTIVE_POWER = "entity_solax_rc_active_power"
CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION = "entity_solax_rc_autorepeat_duration"
CONF_ENTITY_SOLAX_RC_TRIGGER = "entity_solax_rc_trigger"
CONF_ENTITY_SOLAX_EXPORT_DURATION = "entity_solax_export_duration"

# ── Solax parameter config keys ────────────────────────────────────────────────
CONF_SOLAX_MAX_CHARGE = "solax_max_charge"
CONF_SOLAX_MAX_DISCHARGE = "solax_max_discharge"
CONF_SOLAX_CMD_DEADBAND = "solax_cmd_deadband"
CONF_SOLAX_ZERO_DEADBAND = "solax_zero_deadband"
CONF_SOLAX_TIER1_SHARE = "solax_tier1_share"  # removed — replaced by dynamic SOC-balance
CONF_SOC_BALANCE_SENSITIVITY = "soc_balance_sensitivity"
CONF_SOC_BALANCE_DEADBAND = "soc_balance_deadband"

# ── Solax defaults ─────────────────────────────────────────────────────────────
DEFAULT_SOLAX_MAX_CHARGE = 2400        # W — X1 AC G3 hardware limit
DEFAULT_SOLAX_MAX_DISCHARGE = 2400     # W
DEFAULT_SOLAX_AUTOREPEAT_DURATION = 90  # s — HA software autorepeat window; hardware expiry controlled by export_duration (0x9F)
SOLAX_EXPORT_DURATION_SAFE = "15 Minutes"  # written to select.solax_export_duration (register 0x9F) on first activation
DEFAULT_SOLAX_CMD_DEADBAND = 50        # W — suppress command updates smaller than this
DEFAULT_SOLAX_ZERO_DEADBAND = 0        # W — commands within ±this of zero are suppressed to 0
DEFAULT_SOLAX_TIER1_SHARE = 0.0        # unused — kept for migration compatibility only
DEFAULT_SOC_BALANCE_SENSITIVITY = 0.01  # share units per % SOC difference beyond the deadband
DEFAULT_SOC_BALANCE_DEADBAND = 5.0      # % SOC difference below which no share adjustment is applied
DEFAULT_SOLAX_TIER1_SOC_TAPER_BAND = 10.0  # % below Solax soc_max where tier-1 share tapers to zero

# Remote-control power mode names (as reported by homeassistant-solax-modbus)
SOLAX_RC_MODE_ENABLED = "Enabled Power Control"
SOLAX_RC_MODE_DISABLED = "Disabled"

# ── Solax production entity IDs ────────────────────────────────────────────────
# Hub name "solax" — must match the name configured in the Solax Modbus integration.
ENTITY_SOLAX_CAPACITY = "input_number.solax_plant_rated_energy_capacity"        # kWh (rated, static)
ENTITY_SOLAX_SOC = "sensor.solax_battery_capacity"
ENTITY_SOLAX_SOC_MIN = "number.solax_selfuse_discharge_min_soc"
ENTITY_SOLAX_SOC_MAX = "number.solax_battery_charge_upper_soc"
ENTITY_SOLAX_RC_POWER_CONTROL = "select.solax_remotecontrol_power_control"
ENTITY_SOLAX_RC_ACTIVE_POWER = "number.solax_remotecontrol_active_power"
ENTITY_SOLAX_RC_AUTOREPEAT_DURATION = "number.solax_remotecontrol_autorepeat_duration"
ENTITY_SOLAX_RC_TRIGGER = "button.solax_remotecontrol_trigger_gen3"
ENTITY_SOLAX_EXPORT_DURATION = "select.solax_export_duration"  # register 0x9F — hardware RC command expiry timer

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
    CONF_ENTITY_VOLTX_CAPACITY: ENTITY_VOLTX_CAPACITY,
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
    CONF_ENTITY_SOLAX_CAPACITY: ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_SOC: ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MIN: ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_SOLAX_SOC_MAX: ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_RC_POWER_CONTROL: ENTITY_SOLAX_RC_POWER_CONTROL,
    CONF_ENTITY_SOLAX_RC_ACTIVE_POWER: ENTITY_SOLAX_RC_ACTIVE_POWER,
    CONF_ENTITY_SOLAX_RC_AUTOREPEAT_DURATION: ENTITY_SOLAX_RC_AUTOREPEAT_DURATION,
    CONF_ENTITY_SOLAX_RC_TRIGGER: ENTITY_SOLAX_RC_TRIGGER,
    CONF_ENTITY_SOLAX_EXPORT_DURATION: ENTITY_SOLAX_EXPORT_DURATION,
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
