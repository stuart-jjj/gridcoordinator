# Grid Coordinator — Configuration Reference

## Setup overview

Configuration is split across three steps in the UI (Settings → Integrations → Add Integration → Grid Coordinator):

1. **Controller parameters** — tunable values that control how the loop behaves
2. **Entity IDs** — which HA entities the coordinator reads and writes (skipped in testing mode)
3. **Solax entity IDs** — Solax-specific entities; leave `entity_solax_soc` blank to disable Solax entirely

All parameters are editable after setup via the integration's **Configure** button (options flow). Python source changes require a full HA restart; options-only changes only need an integration reload.

---

## Sign conventions

Before configuring, understand the sign conventions used throughout:

| Signal | Positive means |
|---|---|
| Grid power sensor | Importing from grid |
| `mpc_grid_power` (EMHASS) | Importing from grid (default) |
| `mpc_battery_power` (EMHASS) | Discharging battery (default) |
| Voltx command | Discharging (battery → AC) |
| Solax `remotecontrol_active_power` | **Charging** (opposite of coordinator convention — negated automatically on write) |

If your EMHASS instance uses the opposite convention (positive = export / positive = charge), enable the relevant sign-invert flags below.

---

## Controller parameters

### Grid limits

**`import_limit`** (default: 12000 W)
Hard ceiling on grid import. The coordinator will never write a Voltx command that would cause projected import to exceed this value. Also bounds Solax commands in priority-2 mode.

**`export_limit`** (default: 10000 W)
Hard ceiling on grid export. Same role as `import_limit` on the export side.

Both limits act as a final safety clamp that overrides all other logic — they are non-negotiable and always applied last.

---

### EMHASS plan

**`plan_stale_minutes`** (default: 20 min)
How long without an update from the EMHASS MPC sensor before the plan is treated as stale. When stale, the grid target and battery setpoint are both zeroed and the coordinator reports `stale_plan` mode, which falls through to self-consumption if both are within `self_consumption_deadband`. 20 minutes covers 4 missed 5-minute EMHASS solve cycles.

**`mpc_sign_inverted`** (default: false)
Set true if your `mpc_grid_power` sensor uses injection convention (positive = export). Negates the value before use.

**`mpc_batt_sign_inverted`** (default: false)
Set true if your `mpc_battery_power` sensor uses charging convention (positive = charge). Negates the value before use.

---

### Two-tier control

The coordinator runs a two-tier control loop every 10 seconds:

```
raw_cmd = mpc_batt_cmd + tier2_gain × (grid_actual − grid_target)
```

**Tier 1** (`mpc_batt_cmd`) executes the EMHASS optimiser's battery decision variable directly. **Tier 2** adds a proportional correction for the gap between the EMHASS forecast and actual grid conditions this tick.

**`tier2_gain`** (default: 0.5)
Scales the tier-2 grid-error correction. At 1.0, the full instantaneous error is applied each tick, which can cause a 2-tick oscillation when the ramp step is the binding constraint. At 0.5 (default), the error halves each tick and converges smoothly in 3–4 ticks (~30 s). Lower values converge more slowly but are more stable. Raise it if grid tracking feels sluggish; lower it if you see hunting.

**`ramp_step`** (default: 1500 W/tick)
Maximum change in the Voltx command between ticks (10 s). Smooths large step changes in the EMHASS plan or tier-2 correction. The grid-safety clamp can override the ramp if needed to stay within `import_limit` / `export_limit`.

---

### Deadbands

There are four distinct deadbands, each serving a different purpose:

#### `self_consumption_deadband` (default: 50 W)

**Purpose:** Whole-coordinator off-switch when nothing meaningful needs to happen.

When both `|grid_target|` and `|mpc_batt_cmd|` are within this threshold of zero, the coordinator hands control back to the Voltx inverter's native self-consumption firmware and reports `self_consumption` mode. This also fires when the plan is stale (both targets are forced to zero first).

Setting this to 0 W means the coordinator always tries to track even tiny targets. Raising it reduces unnecessary Modbus activity during periods when EMHASS is effectively saying "do nothing."

#### `tracking_deadband` (default: 200 W)

**Purpose:** Suppress Voltx command chatter from measurement noise.

When the grid error (`|grid_actual − grid_target|`) is within this band **and** `mpc_batt_cmd` hasn't moved significantly from the previous command, the coordinator holds the current Voltx command unchanged. This avoids re-writing the inverter register every 10 seconds due to sensor noise when the grid is already near target.

The deadband is skipped when:
- The monitored load headroom feature is active (a large load may have just fired)
- `mpc_batt_cmd` has changed significantly (a new EMHASS plan must be acted on immediately)

If Voltx is at a SOC boundary while in the deadband, the correct `soc_floor` / `soc_ceiling` mode is still reported so Solax can respond.

#### `solax_cmd_deadband` (default: 50 W)

**Purpose:** Reduce Modbus write traffic to the Solax inverter.

Only updates the `remotecontrol_active_power` register when the new command differs from the last written value by more than this amount. The autorepeat trigger button is still pressed every tick regardless (needed to keep the inverter in remote-control mode). This is purely about reducing unnecessary register writes — it does not affect the commanded power once it has been written.

#### `solax_zero_deadband` (default: 0 W, disabled)

**Purpose:** Suppress small Solax commands and release it to self-consumption.

If the computed Solax command is within ±`solax_zero_deadband` watts of zero, the coordinator treats it as zero and releases Solax to self-consumption mode. This avoids driving the Solax inverter for residual errors too small to be worth acting on.

Example: with `solax_zero_deadband = 50`, any computed command in the range −50 W to +50 W is suppressed to zero.

Default is 0 (disabled) — all non-zero computed commands are written.

---

### Self-consumption mode name

**`self_consumption_mode`** (default: `"Self-consumption"`)
The Voltx work-mode string written to `entity_voltx_work_mode` when the coordinator hands off to the inverter's native mode. Must match exactly what the inverter's select entity reports. Check your inverter integration's select options if the coordinator fails to release correctly.

---

### Solax parameters

**`solax_max_charge`** (default: 2400 W)
Physical charge power limit for the Solax inverter. Clamps the computed Solax command. Set to your hardware's actual maximum.

**`solax_max_discharge`** (default: 2400 W)
Physical discharge power limit. Same role as `solax_max_charge` on the discharge side.

**`solax_tier1_share`** (default: 0.0)
Fraction of the EMHASS battery setpoint (`mpc_batt_cmd`) that Solax executes in parallel with Voltx during normal (non-SOC-bounded) tracking. Voltx executes the complementary `(1 − share)` fraction plus the full tier-2 grid-error correction.

At the default of 0.0, Solax is idle during normal tracking and only activates when Voltx is constrained (SOC boundary or physical inverter limit). Setting it to, say, 0.3 means Solax executes 30% of every charge/discharge command alongside Voltx.

When Voltx hits a constraint (SOC floor/ceiling or charge/discharge limit) regardless of this setting, Solax automatically switches to priority-2 mode and covers the residual tracking error instead.

---

### EV charge awareness

**`entity_ev_charger`** (optional)
Entity ID of a power sensor monitoring the EV charger circuit. Leave blank to disable.

**`ev_charger_threshold`** (default: 500 W)
When the EV charger draws above this level, the effective SOC floor is raised to `soc + 1%`, preventing the battery from discharging to compensate for EV load. Grid import naturally absorbs the EV load up to `import_limit`.

---

### Monitored load headroom

Reserves import capacity for a large transient load (e.g. an oven or heat pump) so the battery doesn't try to compensate for it and cause an import spike when it switches off.

**`entity_monitored_load_1`** (optional)
Power sensor for the load to monitor. Leave blank to disable.

**`monitored_load_1_threshold`** (default: 10 W)
Load is considered "on" when it exceeds this level.

**`monitored_load_1_headroom`** (default: 6000 W)
Watts of import capacity to reserve while the load is on. Tightens the effective `import_limit` seen by the Voltx charging calculation by this amount, so the battery charges less aggressively and leaves room for the load.

**`monitored_load_1_holdoff_minutes`** (default: 5 min)
How long after the load drops below threshold before headroom is released. Prevents the battery from ramping back up immediately after a cyclic load (e.g. oven thermostat) briefly switches off.

---

## Entity IDs

Configured in step 2 of the setup flow. All have production defaults; only change what differs in your installation.

| Config key | What it reads/writes | Default |
|---|---|---|
| `entity_grid_power` | Grid import/export sensor (W) | `sensor.iammeter_power_a` |
| `entity_mpc_grid_power` | EMHASS grid target (W) | `sensor.mpc_grid_power` |
| `entity_mpc_batt_power` | EMHASS battery setpoint (W) | `sensor.mpc_batt_power` |
| `entity_voltx_soc` | Voltx battery state of charge (%) | `sensor.battery_state_of_charge` |
| `entity_voltx_max_charge` | Voltx max charge limit (W) | `input_number.voltx_battery_max_charging_limit` |
| `entity_voltx_max_discharge` | Voltx max discharge limit (W) | `input_number.voltx_battery_max_discharging_limit` |
| `entity_soc_min` | SOC floor below which discharging is suppressed (%) | `number.soc_min` |
| `entity_soc_max` | SOC ceiling above which charging is suppressed (%) | `number.soc_max` |
| `entity_enabled` | On/off gate — coordinator is dormant when off | `input_boolean.emhass_control_active` |
| `entity_voltx_cmd` | Written each tick: charge/discharge setpoint (W) | `number.voltx_battery_battery_charge_discharge_power` |
| `entity_voltx_work_mode` | Written to switch between Custom and self-consumption | `select.voltx_inverter_work_mode` |
| `entity_ev_charger` | EV charger power (optional) | `sensor.iammeter_power_c` |
| `entity_monitored_load_1` | Monitored load power (optional) | `sensor.oven_energy_monitor_power` |

`entity_soc_min` and `entity_soc_max` can be set to a numeric literal (e.g. `"20"`) instead of an entity ID if you want a fixed value.

---

## Solax entity IDs

Configured in step 3. Leave `entity_solax_soc` blank to disable Solax entirely — all other Solax fields are then ignored.

| Config key | What it reads/writes | Default |
|---|---|---|
| `entity_solax_soc` | Solax battery state of charge (%) | `sensor.solax_battery_capacity` |
| `entity_solax_soc_min` | Solax SOC floor (%, entity or literal) | `number.solax_selfuse_discharge_min_soc` |
| `entity_solax_soc_max` | Solax SOC ceiling (%, entity or literal) | `number.solax_battery_charge_upper_soc` |
| `entity_solax_rc_power_control` | Remote-control enable/disable select | `select.solax_remotecontrol_power_control` |
| `entity_solax_rc_active_power` | Written each tick: charge/discharge setpoint (W, Solax convention) | `number.solax_remotecontrol_active_power` |
| `entity_solax_rc_autorepeat_duration` | Autorepeat window (s) — keeps inverter in RC mode between ticks | `number.solax_remotecontrol_autorepeat_duration` |
| `entity_solax_rc_trigger` | Button pressed every tick to keep autorepeat alive | `button.solax_remotecontrol_trigger_gen3` |

The Solax `remotecontrol_active_power` sign convention is **inverted** relative to the coordinator: positive = charging. The coordinator negates automatically on write.

---

## Testing mode

Enable `test_mode` in the options flow to switch all entity IDs to simulated virtual entities (`grid_coordinator_sim_*`) that the integration creates itself. This lets you exercise the control loop manually without physical hardware. In testing mode the entity ID step is skipped and all simulated IDs are assigned automatically.

---

## Manual overrides

The coordinator exposes a `grid_coordinator.set_mode` service call and a select entity on the device card for manual overrides (force charge, force export, hold SOC, self-consume, disabled). Overrides are in-memory only and expire after a configurable duration (default 60 min); they are cleared on HA restart or integration reload.

See [service-calls.md](service-calls.md) for the full service call reference.
