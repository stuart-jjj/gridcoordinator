# Self-consumption handoff: drop the charging-shortfall clause

Date: 2026-10-07
Status: approved in chat (option 2); supersedes the battery clause in
`2026-10-07-sc-handoff-tracking-design.md`

## Problem

Release 2026.10.1 hands off to Voltx native self-consumption when the grid target is
near 0 **unless a charging plan is being undershot** by more than `sc_battery_tolerance`
(actual battery power vs the Voltx setpoint). Live data after the EMHASS plan was
stabilised (EMHASS `battery_stress_cost` 0.02 -> 0) shows the cost of that clause:

- 2026-10-07 13:35:57-13:38:02: plan target 0, battery setpoint -5.9 kW; a cloud cut PV
  so the battery could only charge ~3.4 kW. The shortfall exceeded the tolerance, the
  handoff did not engage, and tracking kept commanding the planned charge, importing
  **2.2-3.7 kW from the grid for ~2 minutes** to charge the battery.
- 13:39:21-13:43:11: once the handoff engaged, the grid stayed within about +/-0.5 kW.

At a ~0 W grid target native self-consumption never imports to fill a battery; tracking
can only reach the same grid result by also importing. The shortfall clause therefore
only ever made things worse at target ~0. In the sim it also caused a ~12-minute
enter/exit cycle under a persistent shortfall (hence the 5x re-entry back-off).

## Decision (option 2 of the 2026-10-07 discussion)

Hand off whenever `|grid_target|` is within the deadband (with the existing 25 W exit
margin once active), **regardless of the battery setpoint or actual battery power**.
Idle, discharge, charging and under-delivered charging plans all hand off.

Kept: the minimum dwell (`sc_min_dwell_seconds`), `force_exit` (limit breach / Voltx
control off) and `bypass_lockout` (stale plan), the Solax follow-Voltx behaviour and its
fallbacks, `SolaxMode.FOLLOW_VOLTX`.

## Removed (all superseded; stored values in existing entries are simply ignored)

- Option `sc_battery_tolerance` and the shortfall logic, including the 5x re-entry
  back-off (`SHORTFALL_REENTRY_MULT`, `ScState.lockout_s`).
- Option `sc_discharge_handoff` (a discharge plan now always hands off at target ~0, a
  superset of what the flag did).
- Option `sc_power_smoothing_seconds`, `ema_update`, and the coordinator's smoothed
  Voltx power. The smoothed value existed only to feed the shortfall clause; the Solax
  follow already uses the raw reading.

Kept for reference: `should_hold_self_consumption` (legacy rule) stays in `budget.py`
solely as the sim's baseline policy and for its existing tests; the coordinator no
longer calls it.

Consequence: there is no per-option way back to the pre-2026.10.1 rule any more
(previously `sc_battery_tolerance = 0`). Reverting means installing 2026.10.1 or earlier.

## Behaviour

`decide_self_consumption(state, now, effective_target, deadband, min_dwell_s,
force_exit=False, bypass_lockout=False) -> ScState`:

- `target_ok = |effective_target| <= deadband (+ SELF_CONSUMPTION_EXIT_MARGIN once active
  and deadband > 0)`; `want = target_ok and not force_exit`.
- After any transition the state is locked for `min_dwell_s` (single lockout length);
  `force_exit` and `bypass_lockout` bypass it as before.
- `ScState(active, last_transition_at)`.

The coordinator no longer reads the Voltx power or computes a Voltx setpoint to decide.
It still reads the **raw** Voltx power for the Solax follow (unchanged fallback: unreadable
or non-finite -> Solax released). The entity `entity_voltx_battery_power` stays.

## Accepted trade-off

With the clause gone nothing hands control back to tracking because the battery is far
from its plan while the target stays ~0 (e.g. the battery discharging hard against an idle
plan, or sitting idle against a charging plan). SOC can therefore drift from the EMHASS
trajectory; EMHASS re-plans every 2 minutes and is expected to correct it. If drift is
observed, raise an ADO issue in the `home-assistant` project for an SOC-deviation guard
(same trigger as in the 2026.10.1 spec; not built now).

## Verification

- Unit: decision truth table (target clause, margin, dwell, force_exit, bypass_lockout);
  no battery inputs.
- Sim (`tests/sim`): replay of 2026-10-07 07:10-07:34 still 0 transitions after the first
  tick; cloud cycling keeps its export/import reductions; **persistent charging
  shortfall (plan -1247 W, surplus 300 W) now stays in the handoff**, with no
  enter/exit cycling and less import than legacy; imbalanced-SOC Solax follow tests
  unchanged.
- Real-HA smoke (`scripts/ha_smoke/run`): the shortfall scenario now expects the handoff;
  the options flow no longer offers the removed options and still offers the dwell.
- Live: watch `sensor.grid_coordinator_mode` and grid power through a cloud dip with a
  charging plan at target ~0 (no multi-kW import excursion).

## Rulings

- Removing the three options (rather than leaving them as dead UI) is part of this change;
  it is a small breaking cleanup of options introduced hours earlier in 2026.10.1.
- `sc_min_dwell_seconds` stays: plan blips to target ~0 for a single republish still need
  the lockout.
