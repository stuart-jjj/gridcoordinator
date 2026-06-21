Grid Coordinator — Roadmap

  Vision (end state)

  A single Python coordinator running every 10 seconds that is the only thing writing to the Voltx inverter, Solax inverter, EV charger current, and Enphase curtailment level.
  EMHASS continues to run independently and publish mpc_battery_power and mpc_grid_power. The coordinator reads mpc_battery_power as its primary dispatch setpoint and uses
  mpc_grid_power as a real-time correction signal, always enforcing hard grid limits as the first constraint. Every decision is visible as a HA diagnostic sensor.

  ---
  Phase 1 — MVP: Voltx-only safety net  [COMPLETE]

  Goal: Replace all the competing EMHASS apply automations with one coordinator that controls the Voltx, enforces the 12kW import / 10kW export limits, and exposes its decisions
  as sensors. Nothing else changes. Solax, EV, and Enphase remain on their existing automations.

  What it does:
  - Reads the grid power sensor (actual grid power, positive = import)
  - Reads the EMHASS mpc_grid_power sensor, checks freshness against a configurable stale threshold
  - Sign-corrects both EMHASS values via configurable toggles so they share the coordinator's basis
  - Computes a Voltx battery command using hard grid limits enforced analytically (not reactively),
    ramp limiting, SOC floor/ceiling constraints, and a final re-clamp to inverter physical limits
  - Writes the battery charge/discharge power setpoint
  - Exposes diagnostic sensors

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

  ✓ EV charge awareness   [⚠ REPLACED by "Phase 2 revision — EV import-headroom reserve" below; SOC-floor mechanism removed]
      When the configured EV charger power sensor exceeds a threshold (default 500 W), the
      battery SOC floor is dynamically raised to current SOC + 1 %, making battery discharge
      impossible while the car is charging. The EV load is served entirely by grid import up
      to the configured import limit. When EV charging stops the SOC floor returns to the
      configured minimum automatically.
      Mode sensor reports ev_charging (distinct from soc_floor so the cause is visible).
      Config: entity_ev_charger (optional power sensor), ev_charger_threshold (W).

  ✓ Monitored load 1 — import headroom reservation
      A configurable power sensor (e.g. oven) is watched each tick. As soon as its reading
      exceeds the threshold (default 10 W), the coordinator reserves a block of import headroom
      (default 6000 W) by tightening the effective import limit used in cmd_floor. This reduces
      battery charging power immediately so that the reserved headroom is available for the load
      to spike without exceeding the import limit. Headroom is held for a configurable holdoff
      period (default 5 min) after the load drops below threshold, preventing rapid cycling on
      spikey loads like an oven cycling its heating element. The tracking deadband early-return
      is skipped when headroom is active, ensuring the clamp applies even when the grid error is
      within the band at the moment the load turns on.
      Mode sensor reports load_headroom when this is the binding constraint.
      Config: entity_monitored_load_1, monitored_load_1_threshold (W),
              monitored_load_1_headroom (W), monitored_load_1_holdoff_minutes.

  ---
  Phase 2 revision — EV import-headroom reserve  [COMPLETE]

  Supersedes the Phase 2 "EV charge awareness" SOC-floor mechanism above.

  Rationale: raising the Voltx SOC floor to soc + 1 % blocked discharge, but via the tier-2 grid
  correction it also parked Voltx idle, while Solax kept charging on its tier-1 share with no
  headroom awareness — so the two batteries behaved inconsistently and Solax could add grid import
  while the EV was drawing. The EV is controlled externally by Amber Electric, which has no
  visibility of household grid power or limits, so there is no control over EV draw. The
  coordinator therefore protects a block of import headroom rather than throttling the car.

  ✓ Behaviour while the EV charger is drawing (> ev_charger_threshold):
  - Both batteries follow the EMHASS plan (tier 1) — charge or discharge — while projected grid
    import stays below (import_limit − ev_headroom). The old "block Voltx discharge" rule is gone.
  - Tier 2 (grid-target correction) is disabled for Voltx while the EV charges (effective
    tier2_gain = 0). The EV is a large unforecast load; without this, tier 2 would drain the
    battery to drive grid back to the EMHASS target (feed the EV). With it off, the battery
    executes the plan and lets grid import absorb the EV up to the headroom ceiling. (Solax has
    no tier-2 term, so only Voltx needed this.)
  - ev_headroom (config, default 3000 W) is reserved beneath the import ceiling via the same
    cmd_floor tightening as the monitored-load reserve. As grid import rises toward
    (import_limit − ev_headroom) both batteries ramp down charging; at/above it Voltx discharges
    to hold the line, Solax yields its charging share (suppress_charge — Voltx owns the ceiling,
    avoiding a battery-to-battery round-trip), and Solax joins the discharge via the residual path
    when Voltx hits its discharge limit.
  - The ceiling is evaluated against the actual grid sensor (net of house load), so household
    consumption is accounted for automatically.
  - When both the EV reserve and the monitored-load reserve are active, the larger is used.

  ✓ Implementation
  - budget.compute_voltx_command: unchanged core; EV path is just headroom_reserve + tier2_gain=0.
  - budget.compute_solax_tier1: added grid-safety/headroom clamp on the Solax-free baseline, a
    suppress_charge flag (yield charge when Voltx owns the ceiling), and SOC handling.
  - budget.compute_solax_command: headroom_reserve tightens its import floor + a final physical
    re-clamp (the tightened floor could otherwise command more discharge than the inverter limit).
  - coordinator: _ev_adjusted_soc_min replaced by _ev_headroom_reserve; reserves combined via max;
    LOAD_HEADROOM remapped to EV_CHARGING when the EV reserve is the binding/dominant one.
  - Mode sensor reports ev_charging when the EV headroom reserve binds.
  - Config: ev_headroom (W, default 3000) added to step 1 + options flow; ev_charger_threshold retained.

  Note: a discharge EMHASS plan is followed during EV charging (battery sells per plan). And if a
  charge plan is active while grid is low, the battery may charge from grid up to the headroom
  ceiling — this is literal plan-following, bounded by the reserve. grid_priority, if explicitly
  enabled, still overrides with deadbeat grid tracking even during EV charging.

  ---
  Phase 3 — Solax priority-2 battery  [COMPLETE]

  Goal: Add the Solax X1 AC G3 as a priority-2 storage battery beneath the Voltx. Solax runs in
  its native self-consumption mode by default; the coordinator overrides it only when the Voltx is
  constrained at a SOC boundary and there is still a residual tracking error Voltx cannot cover.

  ✓ Activation model — residual tracking at SOC boundary
      Solax activates only when voltx_mode is SOC_FLOOR or SOC_CEILING:

        solax_cmd = grid_after_voltx − grid_target

      where grid_after_voltx = (grid_actual + prev_voltx_cmd) − voltx_cmd.

      Any other Voltx mode (emhass_tracking, self_consumption, stale_plan, override …) → Solax
      stays in self-consumption. When the plan is stale the coordinator exits via the self-
      consumption deadband before a voltx_mode is computed, so Solax naturally stays idle.

  ✓ Control mechanism (homeassistant-solax-modbus entities)
      Per tick when active:
        1. select.{hub}_remotecontrol_power_control → "Enabled Power Control"
           (only written on transition; re-checked each tick via current state)
        2. number.{hub}_remotecontrol_active_power → −solax_cmd W
           (Solax convention: negative = discharge — opposite of coordinator)
        3. number.{hub}_remotecontrol_autorepeat_duration → 20 s
           (hardware expires in 4 s; autorepeat keeps it alive between 10 s coordinator ticks)
        4. button.{hub}_remotecontrol_trigger_gen3 → press
      On transition to idle: power control → "Disabled", trigger pressed once.
      Solax errors are caught and logged as warnings; they do not kill Voltx coordination.

  ✓ SOC constraints
      Solax respects its own SOC floor and ceiling (live sensor entities, not config values):
      - raw_cmd > 0 and solax_soc ≤ solax_soc_min → return 0, SolaxMode.SOC_FLOOR
      - raw_cmd < 0 and solax_soc ≥ solax_soc_max → return 0, SolaxMode.SOC_CEILING

  ✓ Physical and grid limits
      solax_cmd clamped to [−max_charge, +max_discharge] (default 2400 W, X1 AC G3 limit).
      Hard grid-safety clamp applied on top: projected grid after both batteries must stay
      within [−export_limit, +import_limit].

  ✓ Optional feature
      Leave entity_solax_soc blank to disable Solax coordination entirely. All entity IDs
      are configurable in the options flow Solax step.

  ✓ New sensors
      sensor.grid_coordinator_solax_command — W sent to Solax (positive = discharge)
      sensor.grid_coordinator_solax_mode    — self_consumption / force_charge /
                                              force_discharge / soc_floor / soc_ceiling

  ✓ Testing mode extended
      Simulated Solax entities added (number, select, button platforms):
      - number.grid_coordinator_sim_solax_soc / soc_min / soc_max  (inputs)
      - number.grid_coordinator_sim_solax_rc_active_power            (output — written by coordinator)
      - number.grid_coordinator_sim_solax_rc_autorepeat_duration     (output)
      - select.grid_coordinator_sim_solax_rc_power_control           (output)
      - button.grid_coordinator_sim_solax_rc_trigger                 (output — press_count attribute)

  ---
  Phase 4 — 2-tier control architecture  [COMPLETE]

  Goal: Replace the pure integrator (anchored to prev_cmd + grid error) with a 2-tier controller
  that executes the EMHASS LP decision variable directly and applies a proportional correction for
  forecast error — eliminating integral wind-up and tracking the right primary signal.

  ✓ Architecture change

    Old (pure-I, grid-anchored):
      raw_cmd = prev_cmd + (grid_actual − grid_target)

    New (2-tier, EMHASS-anchored):
      raw_cmd = mpc_batt_cmd + (grid_actual − grid_target)

    Tier 1 — EMHASS battery setpoint (mpc_batt_cmd): executes sensor.mpc_battery_power directly,
    which is the LP decision variable the optimiser actually solved for.

    Tier 2 — Grid correction: proportional adjustment for the gap between EMHASS forecast and
    actual conditions this tick. When forecast is accurate, correction → 0 and the battery
    executes mpc_batt_cmd exactly. When load or solar deviates from forecast, the correction
    fires immediately without waiting for the next 5-minute EMHASS re-solve.

    prev_cmd is retained for: estimating uncontrolled power (grid_actual + prev_cmd) and ramp
    limiting. It no longer accumulates error and cannot wind up.

  ✓ Stale plan handling
      When plan_is_stale, both effective_target and effective_mpc_batt are zeroed before the
      deadband check, so neither signal leaks into the controller on a stale plan.

  ✓ Sign convention defaults updated
      mpc_grid_power — DEFAULT_MPC_SIGN_INVERTED changed from True to False.
      mpc_battery_power — DEFAULT_MPC_BATT_SIGN_INVERTED = False.
      Both sensors now use positive = import / positive = discharge convention by default,
      matching the IAMMeter grid sensor and coordinator voltx_command convention.

  ✓ New entity and sensor
      Reads sensor.mpc_battery_power each tick (configurable via entity_mpc_batt_power).
      New config param: mpc_batt_sign_inverted (bool, default False).
      New diagnostic sensor: sensor.grid_coordinator_mpc_batt_power — the EMHASS battery
      setpoint used this tick (W, positive = discharge), useful for verifying sign convention
      and observing what EMHASS requested vs. what the grid correction added.
      New sim entity: number.grid_coordinator_sim_mpc_batt_power.

  ---
  Phase 5 — EV charge current management

  Battery protection when the EV is charging is already implemented (Phase 2 — EV charge
  awareness above). This phase covers active current management.

  The coordinator writes a recommended current to number.ziggy_charge_current every tick,
  replacing the inline calculation in the existing EV automation. The existing EV automations
  control whether charging is enabled; the coordinator controls how much. Available import
  headroom after accounting for all other loads drives the current setpoint.

  New sensor: sensor.grid_coordinator_ev_headroom (W available for EV above current draw).

  ✓ Emergency EV current throttle (usurp Amber control)  [COMPLETE]
      EV charging is owned by Amber Electric, which has no visibility of household grid power
      or limits. As a layer-3 backstop beneath both battery layers, the coordinator caps the EV
      charge current (number.ziggy_charge_current via number.set_value) when the batteries cannot
      hold grid import at the headroom ceiling, then restores it when the grid recovers.

      - Target: the import-headroom ceiling (import_limit − ev_headroom), NOT the hard import
        limit — so the reserve stays protected against other uncontrolled house loads too.
      - Engages when projected grid after both batteries exceeds the ceiling (i.e. batteries are
        SOC- or power-constrained). Sheds the overshoot from the EV: new current =
        (ev_power − overshoot) / watts_per_amp, ratcheting DOWN only while over (converges, no hunting).
      - Release: once grid stays EV_RELEASE_MARGIN (500 W) below the ceiling for
        ev_release_holdoff_minutes, ramps the cap back up by ev_release_ramp_step A/tick to max and
        hands control back to Amber. Re-assert + release-to-max model (Amber cannot be edited):
        while engaged the cap is re-written every tick to override Amber; on release the configured
        max is written once and Amber re-asserts its own setpoint next cycle. Also released
        immediately when the EV stops, and on the disabled / self-consumption / override paths.
      - Pure function budget.compute_ev_current_limit (unit-tested). Conversion is single-phase
        230 W/A by default; min current 5 A (Tesla API minimum).
      - Config (opt-in, off by default): ev_emergency_throttle, entity_ev_charge_current,
        ev_watts_per_amp (230), ev_min_charge_current (5), ev_max_charge_current (16 — set to the
        charger/circuit max), ev_release_ramp_step (1), ev_release_holdoff_minutes (2).
      - New sensor: sensor.grid_coordinator_ev_current_limit (A; unknown when not throttling).

      Not yet done (the rest of Phase 5): proactive current management from positive headroom
      (raise/lower current to use spare import), and sensor.grid_coordinator_ev_headroom. The
      emergency throttle only acts in the over-ceiling direction.
      Known limitation: the throttle runs only in the active control path — it is released (not
      enforced) during self-consumption / disabled / manual override.

  The coordinator owns input_select.solar_production_level (the Shelly relay steps). The
  existing curtailment automation is disabled. The coordinator computes required curtailment
  from export headroom after Voltx and Solax have been accounted for. Curtailment is only
  applied when export cannot be controlled by battery alone.

  New sensor: sensor.grid_coordinator_enphase_curtailment (%).

  ---
  Current diagnostic sensors

  ┌────────────────────────────────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                  Sensor                   │                                           Example value                                                             │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_mode              │ emhass_tracking / self_consumption / stale_plan / import_ceiling / export_ceiling / soc_floor / soc_ceiling /       │
  │                                           │ ev_charging / load_headroom / disabled / override_*                                                                 │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_mpc_batt_power    │ 2000 W  (EMHASS battery setpoint used this tick; positive = discharge)                                              │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_grid_target       │ 2450 W                                                                                                              │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_voltx_command     │ −1800 W                                                                                                             │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_import_headroom   │ 4200 W                                                                                                              │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_export_headroom   │ 6800 W                                                                                                              │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_solax_command     │ 2400 W  (positive = discharge; 0 when Solax in self-consumption)                                                    │
  ├────────────────────────────────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ sensor.grid_coordinator_solax_mode        │ force_discharge / force_charge / self_consumption / soc_floor / soc_ceiling                                         │
  └────────────────────────────────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

  ---
  Current config (three-step UI on install; all fields editable via Configure button afterward)

  Step 1 — Controller parameters:
  - Testing mode (bool) — creates simulated entities; see below
  - Import limit (W) — default 12000
  - Export limit (W) — default 10000
  - Ramp step per tick (W) — default 1500
  - EMHASS plan stale threshold (minutes) — default 20
  - Invert mpc_grid_power sign (bool) — default False; enable if positive = export to grid
  - Invert mpc_battery_power sign (bool) — default False; enable if positive = charge
  - Self-consumption work mode name — default "Self-consumption" (Voltx Modbus)
  - Self-consumption deadband (W) — default 50; |grid_target| within this band → self-consumption mode
  - Tracking deadband (W) — default 200; hold command when grid error is within this band
  - EV charger detection threshold (W) — default 500
  - Monitored load 1 active threshold (W) — default 10
  - Monitored load 1 headroom to reserve (W) — default 6000
  - Monitored load 1 holdoff after power drops (min) — default 5
  - Solax max charge power (W) — default 2400
  - Solax max discharge power (W) — default 2400

  Step 2 — Entity IDs:
  - entity_grid_power       — actual grid power (positive = import)
  - entity_mpc_grid_power   — EMHASS MPC grid power setpoint
  - entity_mpc_batt_power   — EMHASS MPC battery power setpoint (primary control signal)
  - entity_voltx_soc        — Voltx battery SOC (%)
  - entity_voltx_max_charge / entity_voltx_max_discharge — inverter power limits
  - entity_soc_min / entity_soc_max — SOC floor/ceiling (%)
  - entity_enabled          — enable/disable gate
  - entity_voltx_cmd        — Voltx power setpoint (written each tick)
  - entity_voltx_work_mode  — Voltx work mode select
  - entity_ev_charger (optional) — EV charger power sensor
  - entity_monitored_load_1 (optional) — monitored load power sensor

  Step 3 — Solax entity IDs (leave entity_solax_soc blank to disable Solax):
  - entity_solax_soc        — Solax battery SOC (%)
  - entity_solax_soc_min / entity_solax_soc_max — Solax SOC floor/ceiling entities
  - entity_solax_rc_power_control — select entity: "Disabled" / "Enabled Power Control"
  - entity_solax_rc_active_power — signed power setpoint (W, Solax: negative = discharge)
  - entity_solax_rc_autorepeat_duration — autorepeat duration (s)
  - entity_solax_rc_trigger — trigger button entity

  ---
  Simulated entities created in testing mode

  Inputs (set manually in HA UI):
  - number.grid_coordinator_sim_grid_power         — actual grid import/export
  - number.grid_coordinator_sim_mpc_grid_power     — EMHASS grid setpoint
  - number.grid_coordinator_sim_mpc_batt_power     — EMHASS battery setpoint (primary signal)
  - number.grid_coordinator_sim_battery_soc        — Voltx SOC (%)
  - number.grid_coordinator_sim_max_charge         — Voltx max charging limit (W)
  - number.grid_coordinator_sim_max_discharge      — Voltx max discharging limit (W)
  - number.grid_coordinator_sim_soc_min            — Voltx SOC floor (%)
  - number.grid_coordinator_sim_soc_max            — Voltx SOC ceiling (%)
  - number.grid_coordinator_sim_solax_soc          — Solax SOC (%)
  - number.grid_coordinator_sim_solax_soc_min      — Solax SOC floor (%)
  - number.grid_coordinator_sim_solax_soc_max      — Solax SOC ceiling (%)
  - switch.grid_coordinator_sim_enabled            — enable/disable gate

  Outputs (written by coordinator each tick):
  - number.grid_coordinator_sim_battery_cmd        — Voltx power command
  - select.grid_coordinator_sim_work_mode          — Voltx work mode
  - number.grid_coordinator_sim_solax_rc_active_power         — Solax power setpoint
  - number.grid_coordinator_sim_solax_rc_autorepeat_duration  — Solax autorepeat (s)
  - select.grid_coordinator_sim_solax_rc_power_control        — Solax RC mode
  - button.grid_coordinator_sim_solax_rc_trigger              — press_count attribute increments each tick

  ---
  Module responsibilities summary

  budget.py       Pure arithmetic. No HA imports. Fully unit-testable.

                  compute_voltx_command(grid_actual, grid_target, mpc_batt_cmd, prev_cmd,
                                         import_limit, export_limit,
                                         soc, soc_min, soc_max,
                                         max_charge, max_discharge,
                                         ramp_step, plan_is_stale,
                                         tracking_deadband, headroom_reserve) → (cmd, mode)

                  compute_solax_command(voltx_mode, grid_after_voltx, grid_target,
                                         solax_soc, solax_soc_min, solax_soc_max,
                                         solax_max_charge, solax_max_discharge,
                                         import_limit, export_limit) → (cmd, solax_mode)

                  build_coordinator_data(...) → CoordinatorData

  models.py       CoordinatorMode StrEnum, SolaxMode StrEnum, CoordinatorData frozen dataclass.

  coordinator.py  Reads HA state → reads & sign-corrects mpc_batt_power + mpc_grid_power →
                  self-consumption deadband check → EV SOC floor adjust → monitored load headroom →
                  calls compute_voltx_command → writes Voltx → calls compute_solax_command →
                  writes Solax → returns CoordinatorData.
                  Instance state: prev_cmd, _solax_active, _mon_load_1_active,
                  _mon_load_1_below_since, override fields.

  sensor.py       Reads CoordinatorData fields. No logic.

  config_flow.py  Three-step config + options flow. No credentials.

  const.py        Entity IDs, domain, defaults, entity lookup dicts.

  simulated.py    All simulated entity classes used in testing mode.

  ---
  To install

  Copy (or symlink) the integration into your live HA config:
  cp -r ~/Projects/gridcoordinator/custom_components/grid_coordinator \
        ~/path/to/ha/config/custom_components/
  Then restart HA and add the integration via Settings → Integrations → Add → Grid Coordinator.

  Sign convention verification

  Both sign toggles default to off (positive = import / positive = discharge). Verify in
  Developer Tools → States after EMHASS has just run:

  mpc_grid_power:
    - Positive when you are importing from the grid → leave mpc_sign_inverted OFF (default)
    - Positive when you are exporting to the grid → turn mpc_sign_inverted ON

  mpc_battery_power:
    - Positive when the battery is discharging → leave mpc_batt_sign_inverted OFF (default)
    - Positive when the battery is charging → turn mpc_batt_sign_inverted ON

  The new sensor.grid_coordinator_mpc_batt_power shows the sign-corrected value used each tick,
  making it easy to verify: when the battery is discharging, both that sensor and
  sensor.grid_coordinator_voltx_command should be positive.

  Before turning on the enabled gate

  Disable these automations first (they all write to the same Voltx entities):
  - EMHASS Master Strategy – 15s Heartbeat
  - EMHASS - Voltx Solplanet Enphase control loop
  - All EMHASS Battery Management 2026.xx-vN versions
  - The automation.trigger call at the end of generate_emhass_energy_plan

  Watch sensor.grid_coordinator_mode — it should show emhass_tracking or self_consumption
  under normal operation and switch to import_ceiling / export_ceiling when the hard limits
  are active.
