Service calls for coordinator control loop overrides

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
Service call shape

  service: grid_coordinator.set_mode
  data:
    mode: force_charge        # required
    power_w: 3000             # optional — force_charge/force_export only
    duration_minutes: 30      # optional — auto-expires; max limit if omitted


  ---
Override state

 Stored in-memory. Overrides are operational, transient commands — like telling someone to hold a
  position for 30 minutes. They shouldn't survive a restart. If someone wants a persistent mode change they should use
  the options flow to set the deadband or disable the integration.
  
  ---
Override visibility

Select entity on the device card for the simple auto / self_consume / force_charge / force_export
  / disabled switch, plus the service call for when you need power_w or duration_minutes. The select gives instant UI
  visibility and control; the service covers the automation/scripting use case.

  ---
 SOC Limits

Force_charge and force_export will normally respect SOC limits unless the bypass_soc: true parameter is set for cases like "charge to 100% regardless."
  