Workshop with me how the manual override service call would work.

What each mode does

  ┌──────────────┬────────────────────────────────────────────────────────────────────────────────────────────────┐
  │     Mode     │                                           Behaviour                                            │
  ├──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ auto         │ Normal operation — clears any override, follows EMHASS                                         │
  ├──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ self_consume │ Force self-consumption regardless of EMHASS target (like the deadband, but manually asserted)  │
  ├──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ force_charge │ Charge battery at a specified power (or max_charge if not given), subject to grid limits       │
  ├──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ force_export │ Discharge battery at a specified power (or max_discharge if not given), subject to grid limits │
  ├──────────────┼────────────────────────────────────────────────────────────────────────────────────────────────┤
  │ disabled     │ Coordinator goes dormant, inverter returns to self-consumption                                 │
  └──────────────┴────────────────────────────────────────────────────────────────────────────────────────────────┘

  Grid safety clamp always applies regardless of mode — force_charge can't pull more than import_limit, force_export
  can't push more than export_limit. That's non-negotiable.

  ---
  Design decision 1 — Service call shape

  service: grid_coordinator.set_mode
  data:
    mode: force_charge        # required
    power_w: 3000             # optional — force_charge/force_export only
    duration_minutes: 30      # optional — auto-expires; omit for indefinite

  My recommendation: include power_w and duration_minutes as optional parameters. The common case (no duration, max
  power) works without them; advanced cases like "cheap tariff window" or "EV handoff" need them.

  ---
  Design decision 2 — Where to store override state

  Three options:

  - In-memory on coordinator — lost on HA restart or integration reload. Simple, zero side effects.
  - hass.data[DOMAIN] — survives integration reload but not restart.
  - Config entry options — fully persistent, but every change triggers a reload W(disruptive for a transient
  operational control).

  My recommendation: in-memory. Overrides are operational, transient commands — like telling someone to hold a
  position for 30 minutes. They shouldn't survive a restart. If someone wants a persistent mode change they should use
  the options flow to set the deadband or disable the integration.
  
  ---
  Design decision 3 — Override visibility

  Options:
  - The existing mode sensor already shows what the coordinator is doing — force_charge would just show as a new mode
  value
  - Add an override_active boolean sensor so automations can see at a glance whether a manual override is in effect
  - Expose a select entity on the device card showing the current override mode (auto when none active) — this doubles
  as a UI control, replacing the service call for simple cases

  My recommendation: select entity on the device card for the simple auto / self_consume / force_charge / force_export
  / disabled switch, plus the service call for when you need power_w or duration_minutes. The select gives instant UI
  visibility and control; the service covers the automation/scripting use case.

  ---
  Decision points I need your input on:

  1. Does disabled via the service call replace the existing enabled gate, or should they stay separate? The gate is
  currently a physical toggle (switch entity or input_boolean). If the service call can also disable, you have two
  paths to the same state — potentially confusing. Alternative: the service call's disabled mode just sets an
  in-memory flag; the gate entity remains the persistent "is this integration allowed to run" control.
  2. Should force_charge and force_export respect SOC limits? I'd say yes — silently clamp to soc_max / soc_min — but
  you might want a bypass_soc: true parameter for cases like "charge to 100% regardless."
  3. Do you want the select entity, or just the service call? The select is more visible but adds another entity to
  the device card, which is already quite full with sim entities.
  
  