Grid Coordinator — Roadmap

  Vision (end state)

  A single Python coordinator running every 10 seconds that is the only thing writing to the Voltx inverter, Solax inverter, EV charger current, and Enphase curtailment level.
  EMHASS continues to run independently and publish mpc_grid_power. The coordinator reads that setpoint and tracks it using a PI control loop, but always enforces hard grid limits
  as the first constraint. Every decision is visible as a HA diagnostic sensor.

  ---
  Phase 1 — MVP: Voltx-only safety net  [COMPLETE]

  Goal: Replace all the competing EMHASS apply automations with one coordinator that controls the Voltx, enforces the 12kW import / 10kW export limits, and exposes its decisions
  as sensors. Nothing else changes. Solax, EV, and Enphase remain on their existing automations.

  What it does:
  - Reads the grid power sensor (actual grid power, positive = import)
  - Reads the EMHASS mpc_grid_power sensor, checks freshness against a configurable stale threshold
  - Sign-corrects the EMHASS value (configurable toggle) so both are on the "positive = import" basis
  - Computes a Voltx battery command using a pure-I controller with hard grid limits enforced
    analytically (not reactively), ramp limiting, SOC floor/ceiling constraints, and a final
    re-clamp to inverter physical limits after the grid safety clamp
  - Writes the battery charge/discharge power setpoint
  - Exposes 5 diagnostic sensors

  Diagnostic sensors exposed:

  ┌─────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                 Sensor                  │                                           Example value                                                           │
  ├─────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_mode            │ emhass_tracking / self_consumption / stale_plan / import_ceiling / export_ceiling / soc_floor / soc_ceiling / disabled │
  ├─────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_grid_target     │ 2450 W                                                                                                           │
  ├─────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_voltx_command   │ −1800 W                                                                                                          │
  ├─────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_import_headroom │ 4200 W                                                                                                           │
  ├─────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_export_headroom │ 6800 W                                                                                                           │
  └─────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  Config (two-step UI on install; all fields editable via Configure button afterward):

  Step 1 — Controller parameters:
  - Testing mode (bool) — creates simulated entities; see below
  - Import limit (W) — default 12000
  - Export limit (W) — default 10000
  - Ramp step per tick (W) — default 1500
  - EMHASS plan stale threshold (minutes) — default 20
  - EMHASS sign inverted (bool) — default True (standard EMHASS injection convention)
  - Self-consumption work mode name — default "Self-consumption" (Voltx Modbus); "Self-consumption mode" for Solplanet
  - Self-consumption deadband (W) — default 50; |grid_target| within this band → self-consumption mode

  Step 2 — Entity IDs (skipped in testing mode; auto-populated with simulated entity IDs):
  - All 10 entity IDs the coordinator reads from or writes to, individually configurable

  Additionally, the EMHASS sign convention toggle is exposed as a switch in the device
  Controls card (switch.grid_coordinator_emhass_sign_inverted) for quick toggling without
  entering the config flow.

  Testing mode:
  Enabling testing mode at setup creates a full set of simulated HA entities (number, select,
  switch) so the closed-loop controller can be exercised in a devcontainer without any real
  hardware. Any individual entity can be overridden with a real entity ID via the options flow.

  Simulated entities created in testing mode:
  - number.grid_coordinator_sim_grid_power       (set grid import/export manually)
  - number.grid_coordinator_sim_mpc_grid_power   (set EMHASS setpoint manually)
  - number.grid_coordinator_sim_battery_soc      (set SOC %)
  - number.grid_coordinator_sim_max_charge       (max charging limit W)
  - number.grid_coordinator_sim_max_discharge    (max discharging limit W)
  - number.grid_coordinator_sim_soc_min          (SOC floor %)
  - number.grid_coordinator_sim_soc_max          (SOC ceiling %)
  - number.grid_coordinator_sim_battery_cmd      (output — written by coordinator)
  - select.grid_coordinator_sim_work_mode        (output — written by coordinator)
  - switch.grid_coordinator_sim_enabled          (enable/disable gate)

  Files:
  - const.py        — domain, entity IDs, defaults, configurable entity ID lookup dicts
  - models.py       — CoordinatorMode (StrEnum), CoordinatorData (frozen dataclass)
  - budget.py       — pure functions: compute_voltx_command(), build_coordinator_data()
  - coordinator.py  — GridCoordinator(DataUpdateCoordinator), 10s loop, calls budget, writes Voltx
  - config_flow.py  — two-step config flow + options flow
  - data.py         — GridCoordinatorConfigEntry TypeAlias
  - entity.py       — GridCoordinatorEntity base class (shared device info)
  - __init__.py     — entry setup; SENSOR + SWITCH always; NUMBER + SELECT in testing mode
  - sensor.py       — 5 diagnostic sensors backed by CoordinatorData fields
  - switch.py       — MpcSignInvertedSwitch (always) + SimEnabledSwitch (testing mode)
  - number.py       — simulated number entities (testing mode only)
  - select.py       — simulated work mode select (testing mode only)
  - simulated.py    — SimNumberEntity, SimWorkModeSelect, SimEnabledSwitch classes
  - translations/en.json — UI strings for config flow, options flow, data descriptions

  You disable (one-time, before enabling the coordinator):
  - EMHASS Master Strategy – 15s Heartbeat
  - EMHASS - Voltx Solplanet Enphase control loop
  - All EMHASS Battery Management 2026.xx-vN versions
  - The automation.trigger call at the end of generate_emhass_energy_plan

  ---
  Phase 2 — Self-consumption hybrid + hardening  [COMPLETE]

  Goal: Make the coordinator hand off gracefully to the inverter's native self-consumption mode
  when no active grid-exchange target is in effect, and add operational hardening.

  ✓ Self-consumption hybrid mode
      When |grid_target| is within the configurable deadband (default ±50 W), the coordinator
      switches the inverter to self-consumption mode rather than commanding Custom mode with a
      0 W setpoint. The inverter's firmware reacts at hardware speed. The coordinator switches
      back to Custom mode on the first tick where a non-zero target arrives.
      On transition to self-consumption the battery command register is zeroed (transition only,
      not every tick, to avoid unnecessary Modbus traffic).

  ✓ Self-consumption on disable
      When the enabled gate is turned off, the coordinator switches the inverter to
      self-consumption mode and resets prev_cmd to 0, so re-enabling always starts the ramp
      cleanly. Safe even if the work mode entity does not yet exist (no-ops silently).

  ✓ Self-consumption on stale plan
      A stale plan forces effective_target to 0 W, which falls within the deadband, so the
      inverter enters self-consumption mode automatically. Mode sensor reports stale_plan
      (not self_consumption) so the cause is visible. Threshold is 20 minutes = 4 missed
      EMHASS cycles (EMHASS runs every 5 minutes).

  ✓ Manual override service call
      grid_coordinator.set_mode service call with fields:
        mode (required): auto | self_consume | force_charge | force_export | disabled
        power_w (optional): target power in W for force_charge / force_export (default: max_charge/max_discharge)
        duration_minutes (optional, default 60): override auto-expires after this many minutes
        bypass_soc (optional, default false): ignore SOC floor/ceiling in force modes
      Override state is in-memory only — lost on HA restart or integration reload.
      Gate entity (enabled switch) takes priority: if gate is off, override has no effect.
      Grid safety clamp always applies regardless of override mode.
      OverrideModeSelect entity (select.grid_coordinator_override_mode) on the device card
      provides a quick UI control for mode switching (uses default 60-min duration).
      Four new CoordinatorMode values: override_self_consume, override_force_charge,
      override_force_export, override_disabled — visible in the mode sensor.
      Also fixed: _async_enter_self_consumption was calling itself recursively (stack overflow
      bug that would have triggered on any stale-plan or deadband transition to self-consumption).

  ---
  Phase 3 — Solax coordination

  Candidate enhancements (deferred from Phase 2):
  - Persistent notification: fire a HA persistent notification when the coordinator has been
    in import_ceiling, export_ceiling, soc_floor, or soc_ceiling for more than N consecutive
    minutes (configurable; default 10 min). Signals a misconfiguration or hardware limit.

  Add Solax to the coordinator's awareness. Solax runs in self-consumption mode by default;
  the coordinator overrides it in two cases only:
  - Export headroom is exhausted and Voltx is already at minimum discharge → reduce Solax export
  - Import is so high that Voltx alone can't absorb it → tell Solax to charge too

  The existing Solax Amber-triggered automations (Solax force export turn on/off, Solax force
  import turn on/off) write to a new input_select.solax_requested_mode helper. The coordinator
  reads this as an advisory, but overrides it if the grid limits don't allow it.

  New sensor: sensor.grid_coordinator_solax_mode showing self_consumption / force_charge /
  force_export / limit_overridden.

  ---
  Phase 4 — EV charge current management

  The coordinator reads sensor.iammeter_power_c (EV charger actual power) and computes
  available import headroom after accounting for EV load. It writes a recommended current to
  number.ziggy_charge_current every tick, replacing the inline calculation in the existing EV
  automation. The existing EV automations control whether charging is enabled; the coordinator
  controls how much.

  New sensor: sensor.grid_coordinator_ev_headroom (W available for EV above current draw).

  ---
  Phase 5 — Enphase curtailment

  The coordinator owns input_select.solar_production_level (the Shelly relay steps). The
  existing curtailment automation is disabled. The coordinator computes required curtailment
  from export headroom after Voltx and Solax have been accounted for. Curtailment is only
  applied when export cannot be controlled by battery alone.

  New sensor: sensor.grid_coordinator_enphase_curtailment (%).

  ---
  Module responsibilities summary

  budget.py       Pure arithmetic. No HA imports. Fully unit-testable.
                  compute_voltx_command(grid_actual, grid_target, prev_cmd,
                                         import_limit, export_limit,
                                         soc, soc_min, soc_max,
                                         max_charge, max_discharge,
                                         ramp_step, plan_is_stale) → (cmd, mode)
                  build_coordinator_data(...) → CoordinatorData

  models.py       CoordinatorMode StrEnum + CoordinatorData frozen dataclass.

  coordinator.py  Reads HA state → self-consumption deadband check → calls budget →
                  writes Voltx → returns CoordinatorData. Owns prev_cmd as instance state.

  sensor.py       Reads CoordinatorData fields. No logic.

  config_flow.py  Two-step config + options flow. No credentials.

  const.py        Entity IDs, domain, defaults, entity lookup dicts.

  simulated.py    All simulated entity classes used in testing mode.

  ---
  To install

  Copy (or symlink) the integration into your live HA config:
  cp -r ~/Projects/gridcoordinator/custom_components/grid_coordinator \
        ~/path/to/ha/config/custom_components/
  Then restart HA and add the integration via Settings → Integrations → Add → Grid Coordinator.

  One thing to verify before enabling

  The mpc_sign_inverted toggle in the config flow (and on the device card) is the critical one.
  Check it by looking at the mpc_grid_power entity in Developer Tools when EMHASS has just run:
  - If the value is positive when your battery is discharging to export → leave toggle ON
    (standard EMHASS injection convention — coordinator negates the value)
  - If it's positive when you're importing → set toggle OFF

  Before turning on the enabled gate

  Disable these automations first (they all write to the same Voltx entities):
  - EMHASS Master Strategy – 15s Heartbeat
  - EMHASS - Voltx Solplanet Enphase control loop
  - All EMHASS Battery Management 2026.xx-vN versions
  - The automation.trigger call at the end of generate_emhass_energy_plan

  Watch sensor.grid_coordinator_mode — it should show emhass_tracking or self_consumption
  under normal operation and switch to import_ceiling / export_ceiling when the hard limits
  are active.
