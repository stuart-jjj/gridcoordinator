# Self-consumption handoff: battery-tracking rule and flap suppression

Date: 2026-10-07
Status: draft for review

## Problems

1. **Mode flapping.** The coordinator flips between `emhass_tracking` and
   `self_consumption` every few minutes at any time of day. Live history for
   2026-10-07 07:10-07:50 shows entries into `self_consumption` at 07:16, 07:26
   and 07:44. Each coincides with `sensor.grid_coordinator_mpc_battery_power`
   reading exactly `0.0` between neighbouring values of about 400-600 W
   (570 -> 0 -> 399, 366 -> 0 -> 449). `grid_target` also hovers around the
   deadband (0, -83, -178, -60, 0, 706, 554, 0, -292). The existing 25 W exit
   margin (`SELF_CONSUMPTION_EXIT_MARGIN`) cannot absorb swings of hundreds of
   watts between ~2-minute EMHASS republishes.
   *This diagnosis is inferred from sensor history; no debug tick logs were
   available for the window.*

2. **Export while charging should absorb.** In `emhass_tracking` with
   `grid_target = 0` and a charge setpoint (observed: battery setpoint -1247 W),
   the coordinator commands that fixed charge power. When cloud cover lifts, the
   2-minute plan cannot follow the solar surplus, so the excess exports to the
   grid during low feed-in prices. Native Voltx self-consumption reacts at
   firmware speed and would absorb it.

## Goal

Hand off to the Voltx native self-consumption mode whenever
`grid_target` is within the deadband of 0 **and** the battery is not doing
materially less than its setpoint. Make the decision stable enough that it does
not flap on noisy EMHASS republishes or on the noisy actual-power signal.

Success criteria (measured in the simulation harness, section "Verification"):

- Replaying the 2026-10-07 07:10-07:34 inputs, baseline `main` shows 4 mode
  transitions (reproducing the flapping first) and the new rule shows none after
  the first tick.
- In the cloud-cycling scenario, export energy during low feed-in falls clearly
  versus baseline, with no new grid import caused by the change.

## Non-goals

- No change to tier-1/tier-2 control maths, ramp limiting, or the existing Solax
  share/clamp functions (section 6 reuses them; it does not alter them).
- No parity work for test mode / simulated entities (integration is productionised).
- No removal of `sc_discharge_handoff`; it stays as the fallback (below).

## Sign conventions

Unchanged. Positive = discharging, negative = charging for `mpc_batt_cmd`,
`voltx_command`, and `sensor.voltx_battery_battery_power` (verified live:
state `-2069` while charging). Positive `grid` = importing.

Define `d = smoothed_batt_power - mpc_batt_cmd`.
`d > 0` means the battery is charging less, or discharging more, than planned
(a shortfall). `d < 0` means it is absorbing more than planned.

## Design

### 1. Decision rule (`budget.py`, pure)

Replace `should_hold_self_consumption` with a function that takes a small state
object and returns the new state. It is the single implementation used by both
the coordinator and the simulator.

Inputs: `effective_target`, `effective_mpc_batt`, `smoothed_batt_power`
(`None` if the sensor is unavailable), `deadband`, `tolerance`, `min_dwell_s`,
`now`, and the previous state (`active`, `last_transition_at`).

Condition to be in self-consumption (`want_sc`):

- `|effective_target| <= threshold` where `threshold = deadband`, plus
  `SELF_CONSUMPTION_EXIT_MARGIN` once active (existing behaviour; margin still
  skipped when `deadband == 0`).
- Battery clause, when `tolerance > 0` and `smoothed_batt_power` is available:
  it applies **only to a charging plan** (`effective_mpc_batt < 0`):
  `d <= tol_eff` where `tol_eff = tolerance`, plus the exit margin once active.
  `d < 0` (absorbing more than planned) always satisfies the clause. For a
  non-charging plan (`effective_mpc_batt >= 0`, i.e. idle or discharge) the clause
  is always satisfied: at a ~0 W grid target native self-consumption covers the
  house load, which is what the plan wants, and a battery discharging more than
  planned is not a reason to leave it (design decision 2026-10-07, see
  "Replay finding" below). This supersedes `sc_discharge_handoff` whenever the
  clause is active.
- Fallback, when `tolerance == 0` or the sensor is unavailable: today's clause,
  i.e. `abs(mpc_batt) <= threshold`, or `mpc_batt >= -threshold` when
  `sc_discharge_handoff` is on.

Dwell: after any transition (enter or exit), `want_sc` is ignored for
`min_dwell_s` seconds, except for the bypass conditions below. The state object
records `last_transition_at` and `lockout_s` (the lockout length for that
transition).

Shortfall back-off: when the handoff is left because a charging plan was
undershot (target in band, battery clause failed), the next lockout is
`5 x min_dwell_s` (`SHORTFALL_REENTRY_MULT`) instead of `min_dwell_s`. Found in
the sim: tracking pins actual power to the setpoint, so the entry test is
trivially satisfied there, and native mode then re-reveals the shortfall; without
the back-off a persistent shortfall cycles every `2 x min_dwell_s`, toggling the
inverter work mode. With it the cycle is about 12 minutes and import over 30
minutes is still ~19 % below legacy.

`tolerance == 0` therefore reproduces current behaviour, other than the dwell.
`min_dwell_s == 0` disables the dwell.

**Replay finding (why the clause is charge-only).** Prototyped against the
recorded 2026-10-07 07:10-07:34 inputs (deadband assumed 200 W, the recorded
flips only fit 178-291 W): legacy gives 4 transitions (07:16, 07:20, 07:26,
07:32, matching the live mode history to within 6 s). A symmetric or
shortfall-both-directions clause with a 120 s dwell still gave 4, because the
EMHASS setpoint sits at exactly `0.0` for 4-6 minutes at a time (longer than the
dwell) while actual power during native self-consumption spikes to ~1400 W as the
battery covers house load. Smoothing the setpoint (60-600 s) gave 3-4. A 600 s
exit debounce gave 1. The charge-only clause gave 0 and needs no extra setting.
The replay is open-loop (the recorded power came from the legacy run), so the
closed-loop sim is the real test.

**Accepted trade-off:** a battery that discharges far beyond a discharge or idle
plan no longer forces a handoff exit, so SOC can drift ahead of plan while
`grid_target` stays near 0. EMHASS re-plans every 2 minutes and is expected to
correct this. If drift is observed in practice, raise an ADO issue in the
`home-assistant` project for an SOC-deviation guard (not built now).

### 2. Actual-power smoothing (`coordinator.py`)

Maintain an EMA of the Voltx battery power sensor with time constant
`sc_power_smoothing_seconds`, updated on every tick (10 s). Reuse the existing
`_grid_ema` pattern. Reset the EMA when the sensor is unavailable so a stale
value never drives the decision. During native self-consumption the sensor is
still read; its swings (observed 295 -> 1442 -> 295 W in two minutes) are the
reason smoothing is required.

### 3. Dwell bypass

These must never be delayed by the dwell. They already short-circuit before the
self-consumption check, or must be made to:

- manual override active
- plan stale (existing STALE_PLAN path: `effective_target` and `effective_mpc_batt` are zeroed)
- global `entity_enabled` gate off; per-battery control-enable helper off
- import/export limit breach

**Open item for the implementation plan:** confirm which of these the current
self-consumption branch already honours (`coordinator.py` ~L520-600), and add a
bypass for any that it does not. An import or export limit breach while held in
self-consumption is the one most likely to be missing.

### 4. Config (options flow, `config_flow.py`, `const.py`, `en.json`)

| Key | Default | Meaning |
|---|---|---|
| `entity_voltx_battery_power` | `sensor.voltx_battery_battery_power` | Voltx battery power, positive = discharge |
| `sc_battery_tolerance` | 300 W | Max charging shortfall tolerated on a charging plan; 0 disables the clause |
| `sc_min_dwell_seconds` | 120 | Lock after any transition; 0 disables |
| `sc_power_smoothing_seconds` | 60 | EMA time constant for actual battery power |

The entity key is added to `ENTITY_ID_DEFAULTS`. Tolerance, dwell and smoothing
live in controller-parameters step 1; the entity lives in step 2 (entities).
No new sim entity is needed (test mode parity is a non-goal).

### 5. Diagnostics

Add the `d` value and the dwell remaining to the existing debug tick log line
so the next flapping incident can be diagnosed from logs rather than inferred
from sensor history.

### 6. Solax piggy-backs on Voltx during the handoff

**Constraint:** Solax's native self-consumption mode is non-functional, so
"release Solax to self-consumption" (`_async_enter_solax_self_consumption`,
called from the handoff branch at `coordinator.py` ~L570) leaves Solax idle.
During a Voltx handoff Solax must therefore keep being commanded, following
Voltx rather than the plan.

Scope: only the deadband handoff branch. The other call sites (disabled gate,
override, Voltx-off tick, etc.) are unchanged.

While the handoff is active and Solax is enabled and its control switch is on:

1. `s = compute_solax_share(...)` with `cmd = raw_voltx_batt_power`
   (the SOC-balance share, keyed off the direction Voltx is actually moving).
   The follow signal is the **raw** Voltx reading, not the smoothed one: in the
   sim the smoothed signal lagged a cloud cycle and left Solax charging against a
   collapsed surplus while Voltx discharged to compensate. Smoothing is used only
   for the handoff decision.
2. Voltx is moving `(1 - s)` of the total battery power, so Solax's
   target is `solax_target = raw_voltx_batt_power * s / (1 - s)`
   (`s` is clamped below 1; if `s >= 1` use the maximum, see limits).
3. Pass `solax_target` through the existing `compute_solax_tier1`
   (`mpc_batt_cmd=solax_target, share=1.0, tier2_term=0`) so the existing grid
   safety clamp, SOC floor/ceiling and Solax inverter limits all apply, then the
   existing zero deadband and `_async_write_solax`. `grid_after_voltx` is the
   live `grid_actual` (Voltx's native response is already in the meter reading).

**New Solax mode.** Add `SolaxMode.FOLLOW_VOLTX = "follow_voltx"` to `models.py`
("Solax commanded as a share of Voltx's actual power during the native
self-consumption handoff"). `sensor.grid_coordinator_solax_mode` is a plain
`str(d.solax_mode)`, so no sensor or translation change is needed. Reporting
rules:

- Solax commanded (non-zero after clamps) in this state: `follow_voltx`.
- `compute_solax_tier1` returns `SOC_FLOOR` / `SOC_CEILING`: report those as-is
  (a real constraint is more informative than the generic follow state).
- Command zeroed (zero deadband, Voltx idle) or the fallback release path:
  `self_consumption`, as today.

This keeps `solax_mode` honest during the handoff (it would otherwise read
`self_consumption` while Solax is being actively written) and gives the soak and
the sim a single value to assert on.

Properties this relies on, all to be checked in the sim:

- **Same sign as Voltx.** `s >= 0`, so Solax never opposes Voltx. This is
  deliberately a function of Voltx's power, not of the grid error, so it cannot
  repeat the 2026-07-09 `grid_priority` freeze (Solax zeroing the error and
  freezing Voltx's loop). Native Voltx self-consumption still sees a grid error
  after Solax acts and reduces its own power; the combined response converges
  (`voltx = S/(1+k)`, `solax = k*S/(1+k)` for a surplus `S`, `k = s/(1-s)`). Solax acts a tick after Voltx moves, so a step
  change in solar produces up to two ticks of opposition, then both settle.
- **Fallback.** If the Voltx power sensor is unavailable, fall back to today's
  release-to-idle behaviour. Solax must not be commanded from a stale value.
- **Dwell.** Entering the handoff does not release-then-recommand Solax:
  `_solax_active` stays true across the transition so there is no zeroing gap.
  On exit to tracking, Solax is simply back on the normal tier-1/tier-2 path.
- **Tolerance clause interaction.** `d` is computed on Voltx power only
  (Voltx's own setpoint after the Solax share reduction, `mpc_batt_cmd*(1-s)`,
  versus Voltx actual). Comparing against the undivided plan would read Solax's
  share as a permanent Voltx shortfall.

**Open item for the plan:** confirm that the Voltx setpoint used for `d` equals
the `(1 - solax_share)`-reduced `mpc_batt_cmd` the coordinator already builds
before calling `compute_voltx_command`, and reuse that variable.

## Verification: simulation harness

New `tests/sim/`, modelled on `homeassistant-config/tests/` (`pyscript_harness.py`
virtual clock, `replay.py`). Pure Python, no HA, runs under the existing
`python3 -m pytest tests/`.

Driver: advances a virtual clock in 10 s ticks and calls the same pure
functions the coordinator calls (`should_hold...` replacement,
`compute_voltx_command`, `compute_solax_*` where relevant). To stop the driver
drifting from production, the decision logic is already shared via
`budget.py`; the driver only reimplements the thin tick ordering.

Plant model: house load, solar profile, battery that follows the command with
the configured ramp in tracking mode, and in native self-consumption absorbs
`solar - load` limited by max charge/discharge and SOC bounds. Solax is
modelled with its real limits (3000 W cap) and, matching production, **no
working native mode**: when not commanded it is idle. Grid = load - solar +
Voltx + Solax.

Scenarios:

1. **Replay (issue 1).** Feed the recorded 2026-10-07 07:10-07:34 series for
   `grid_target`, `mpc_batt`, `voltx_battery_power` and mode. Baseline run on
   `main` must show the flapping; the new run must not.
2. **Cloud cycling (issue 2).** Plan `target=0`, `mpc_batt=-1247`, solar
   alternating roughly 300 W / 3 kW surplus on ~60-90 s cycles. Metrics:
   export Wh, import Wh, transitions.
3. **Charging-shortfall exit.** On a charging plan, actual charging short of the
   setpoint by more than tolerance exits, after the dwell and never before it.
   On an idle or discharge plan, a battery discharging far above plan never exits.
   A *persistent* charging shortfall backs off (re-entry after a shortfall exit
   waits `5 x min_dwell_s`) rather than cycling at the dwell rate.
4. **Sensor unavailable.** Falls back to legacy behaviour, EMA reset.
5. **Bypass.** Stale plan and limit breach exit immediately even inside the dwell.
6. **Solax piggy-back (section 6).** Across a held handoff with a cloud profile:
   Solax and Voltx never have opposite signs; combined battery power absorbs the
   surplus; Solax's share of throughput matches `s`; the loop settles (opposition
   lasts at most 3 consecutive ticks, no standoff like 2026-07-09); Solax honours its SOC
   limits and 3000 W cap; falls back to idle when the Voltx power sensor is
   unavailable. Baseline (Solax idle during handoff) is run for comparison.

Plus unit tests for the decision function (truth table incl. asymmetry,
hysteresis margin, dwell boundary, `tolerance == 0`, `deadband == 0`) in
`tests/test_budget.py`.

The recorded replay data is saved as a fixture under `tests/sim/fixtures/`.

## What the harness cannot show

It models the plant. It does not model real Voltx firmware reaction time,
Modbus write latency, or work-mode write behaviour (see the shadow-register
note in project memory). The cloud-cycling result is therefore evidence the
logic is sound, not proof of export reduction on the real inverter. A live
soak with debug logging is required before treating issue 2 as closed.

## Risks

- Tolerance too small makes the handoff rare and the benefit disappears; too
  large lets the battery sit far off plan. 300 W is a starting point to tune.
- A cloud-driven shortfall greater than tolerance still exits to tracking and
  may force brief grid import; during a *sustained* shortfall the handoff cycles
  roughly every 12 minutes (the back-off above), versus never entering at all on
  legacy. Raise `sc_battery_tolerance` or set it to 0 if that is unwanted.
- Solax following Voltx adds a second actor to a loop the native Voltx mode
  closes on its own. The proportional-to-Voltx formulation avoids the known
  `grid_priority` freeze, but this is the highest-risk part of the change and is
  the first thing to examine in the live soak (watch for Voltx/Solax power with
  opposite signs, or Solax swinging at the smoothing period).
- Over-absorption beyond plan is allowed by design. If SOC runs ahead of plan
  at times when charging is not cheap, the tolerance clause will not stop it;
  the SOC ceiling handling is unchanged.
