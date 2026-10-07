# Drop the charging-shortfall clause from the self-consumption handoff — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Hand off to Voltx native self-consumption whenever `|grid_target|` is within the deadband, regardless of battery setpoint or actual power; remove the now-dead tolerance / discharge-handoff / smoothing options and code.

**Architecture:** `decide_self_consumption` loses its battery inputs and the shortfall back-off; the coordinator stops computing a Voltx setpoint and a smoothed Voltx power for the decision (the raw reading is still read for the Solax follow). Three options and one EMA disappear from config, flow, translations and docs.

**Tech Stack:** Python 3.14, Home Assistant custom integration, pytest; real-HA smoke tests in Docker (`scripts/ha_smoke/run`).

**Spec:** `docs/superpowers/specs/2026-10-07-sc-handoff-drop-shortfall-design.md`

## Global Constraints

- Signs unchanged (battery + = discharge, grid + = import). `budget.py` stays HA-free.
- Kept: `sc_min_dwell_seconds`, `force_exit`, `bypass_lockout`, Solax follow (raw Voltx power) and its fallbacks, `entity_voltx_battery_power`, `should_hold_self_consumption` (sim baseline + its tests).
- Removed: options `sc_battery_tolerance`, `sc_discharge_handoff`, `sc_power_smoothing_seconds`; `ema_update`; `SHORTFALL_REENTRY_MULT`; `ScState.lockout_s`; the coordinator EMA state/methods/properties.
- Bare suite: `python3 -m pytest tests -q`. Real-HA: `scripts/ha_smoke/run`. Do not run repo-wide `ruff --fix/format`. `CLAUDE.md` is git-ignored: update locally only.
- Commits end with the session attribution lines.

## Review Focus

1. Charging plan at target ~0 with a persistent shortfall stays in the handoff (no import excursion, no cycling): Task 1 unit test, Task 2 sim test, Task 4 smoke test.
2. Safety exits still bypass the dwell (limit breach, Voltx control off); stale plan still bypasses the lockout: Task 1, Task 4.
3. An existing config entry that still carries the removed option keys loads and works (keys ignored): Task 3 test + smoke.
4. Solax follow unchanged and still released when the Voltx power is unreadable/non-finite: existing tests must stay green.

---

### Task 1: Decision function without battery inputs

**Files:** Modify `custom_components/grid_coordinator/budget.py`; modify `tests/test_sc_handoff.py`

**Interfaces — produces (Tasks 2-3 consume):** `ScState(active: bool = False, last_transition_at: float | None = None)`; `decide_self_consumption(*, state, now, effective_target, deadband, min_dwell_s, force_exit=False, bypass_lockout=False) -> ScState`. Removed: `ema_update`, `SHORTFALL_REENTRY_MULT`, `ScState.lockout_s`.

- [ ] **Step 1: Rewrite the decision/EMA tests (RED).** In `tests/test_sc_handoff.py` replace everything from the imports down to (not including) the `# ── Solax follow` section with:

```python
"""Pure-logic tests for the self-consumption handoff decision and Solax follow."""

import pytest

from custom_components.grid_coordinator.budget import (
    SELF_CONSUMPTION_EXIT_MARGIN,
    ScState,
    compute_solax_follow,
    decide_self_consumption,
)
from custom_components.grid_coordinator.models import SolaxMode

BASE = dict(deadband=50.0, min_dwell_s=120.0)


def decide(state=None, *, now=1000.0, target=0.0, **kw):
    return decide_self_consumption(
        state=state or ScState(), now=now, effective_target=target, **{**BASE, **kw}
    )


# ── target clause ─────────────────────────────────────────────────────────────

def test_enters_when_target_in_deadband():
    assert decide().active is True


def test_does_not_enter_when_target_outside_deadband():
    assert decide(target=80.0).active is False


def test_exit_margin_applies_to_target_once_active():
    active = ScState(active=True, last_transition_at=0.0)
    inside = 50.0 + SELF_CONSUMPTION_EXIT_MARGIN
    assert decide(active, target=inside).active is True
    assert decide(active, target=inside + 1).active is False


def test_deadband_zero_has_no_margin():
    active = ScState(active=True, last_transition_at=0.0)
    assert decide(active, deadband=0.0, target=1.0).active is False


# ── dwell, bypass ─────────────────────────────────────────────────────────────

def test_dwell_blocks_exit_then_allows_it():
    entered = decide(now=1000.0)
    assert entered.active is True
    assert decide(entered, now=1119.0, target=500.0).active is True   # locked
    assert decide(entered, now=1120.0, target=500.0).active is False  # released


def test_dwell_blocks_reentry():
    exited = ScState(active=False, last_transition_at=1000.0)
    assert decide(exited, now=1100.0).active is False
    assert decide(exited, now=1120.0).active is True


def test_min_dwell_zero_disables_lock():
    entered = decide(now=1000.0, min_dwell_s=0.0)
    assert decide(entered, now=1000.0, target=500.0, min_dwell_s=0.0).active is False


def test_force_exit_bypasses_dwell():
    entered = decide(now=1000.0)
    assert decide(entered, now=1001.0, force_exit=True).active is False


def test_force_exit_blocks_entry():
    assert decide(force_exit=True).active is False


def test_bypass_lockout_enters_inside_a_lockout():
    locked = ScState(active=False, last_transition_at=1000.0)
    assert decide(locked, now=1001.0).active is False
    assert decide(locked, now=1001.0, bypass_lockout=True).active is True


def test_bypass_lockout_does_not_force_an_entry_the_target_rejects():
    locked = ScState(active=False, last_transition_at=1000.0)
    assert decide(locked, now=1001.0, target=500.0, bypass_lockout=True).active is False


def test_decision_takes_no_battery_inputs():
    # A charging, idle or discharging plan — and any actual battery power — is irrelevant
    # to the handoff at target ~0 (shortfall clause removed, spec 2026-10-07).
    import inspect

    params = inspect.signature(decide_self_consumption).parameters
    for name in ("voltx_setpoint", "smoothed_voltx_power", "tolerance", "allow_discharge_plan"):
        assert name not in params


```

  Run: `python3 -m pytest tests/test_sc_handoff.py -q` — Expected: FAIL/ERROR (`ScState(... lockout_s)` usage gone but `decide_self_consumption` still requires `voltx_setpoint`; `test_decision_takes_no_battery_inputs` fails).

- [ ] **Step 2: Implement (GREEN).** In `budget.py`: delete `ema_update`, `SHORTFALL_REENTRY_MULT` (and its comment block), the `lockout_s` field; keep `import math` only if still used elsewhere (remove it otherwise); replace `decide_self_consumption` with:

```python
def decide_self_consumption(
    *,
    state: ScState,
    now: float,
    effective_target: float,
    deadband: float,
    min_dwell_s: float,
    force_exit: bool = False,
    bypass_lockout: bool = False,
) -> ScState:
    """Decide whether the Voltx native self-consumption handoff is active this tick.

    Holds the handoff whenever |effective_target| is within the (hysteresis-widened)
    deadband, regardless of the battery setpoint or actual battery power.  At a ~0 W grid
    target native self-consumption covers the load, absorbs any solar surplus and never
    imports to fill the battery, so the earlier charging-shortfall clause could only make
    things worse (live import excursion 2026-10-07 13:36).

    After any transition the state is locked for `min_dwell_s`; `force_exit` (safety
    conditions: limit breach, control off) bypasses the lock and forces the handoff off.
    `bypass_lockout` (a stale plan, which zeroes the target) lets the handoff start or
    stop immediately whenever the target calls for it, without forcing it.
    """
    threshold = deadband
    if state.active and deadband > 0:
        threshold += SELF_CONSUMPTION_EXIT_MARGIN
    want = abs(effective_target) <= threshold and not force_exit
    if want == state.active:
        return state
    locked = (
        state.last_transition_at is not None
        and (now - state.last_transition_at) < min_dwell_s
    )
    if locked and not (force_exit or bypass_lockout):
        return state
    return ScState(active=want, last_transition_at=now)
```

  Run: `python3 -m pytest tests/test_sc_handoff.py -q` — Expected: the decision/dwell tests PASS; the Solax-follow tests in the same file PASS. (Other test files and the coordinator will fail to import removed names until Tasks 2-3; do not run the full suite yet.)
- [ ] **Step 3: Commit** `git add custom_components/grid_coordinator/budget.py tests/test_sc_handoff.py && git commit -m "feat: handoff decision ignores battery setpoint and power"` (with attribution).

### Task 2: Simulator and scenarios

**Files:** Modify `tests/sim/driver.py`, `tests/sim/test_cloud.py`, `tests/sim/test_replay.py` (only if it fails)

- [ ] **Step 1: Driver.** In `driver.py` remove the `ema_update` import and `power_ema`; drop `tolerance`/`smoothing_s` from `Policy` (keep `deadband`, `min_dwell_s`, `solax_follow`, `legacy`); the new-policy branch becomes `decide_self_consumption(state=state, now=t, effective_target=target, deadband=policy.deadband, min_dwell_s=policy.min_dwell_s, force_exit=breach)`. Keep `s1`, `s2`, `s_follow`, `voltx_setpoint` only where still used (legacy branch uses `mpc`; `voltx_setpoint` is still passed to `compute_voltx_command`).
- [ ] **Step 2: Tests (RED then GREEN).** In `tests/sim/test_cloud.py` replace `test_persistent_charging_shortfall_backs_off_instead_of_flapping` (and its `_active_runs` helper if unused) and `test_charging_shortfall_exit_*` expectations with:

```python
def test_persistent_charging_shortfall_stays_in_the_handoff():
    # Plan wants 1247 W of charging but only a 300 W surplus exists, for 30 minutes.
    # Native self-consumption takes what solar gives and never imports to fill the
    # battery; tracking would buy the shortfall from the grid (live 2026-10-07 13:36).
    def inputs(t):
        return LOAD_W, 800.0, 0.0, -1247.0, None

    base = run(LEGACY, inputs, 1800)
    res = run(NEW, inputs, 1800)
    assert res.transitions == 0
    assert res.active_ticks >= res.ticks - 2
    assert res.import_wh < 0.25 * base.import_wh
```

  Also delete `test_idle_or_discharge_plan_never_exits...`'s dependence on tolerance (it should still pass unchanged). Run `python3 -m pytest tests/sim -q` — expected PASS including the replay (0 transitions) and the imbalanced-SOC Solax tests.
- [ ] **Step 3: Commit** `test: sim covers a persistent charging shortfall staying in the handoff`.

### Task 3: Coordinator, config surface, options removal

**Files:** `custom_components/grid_coordinator/{coordinator.py,const.py,config_flow.py,translations/en.json}`, `tests/{test_coordinator_flags.py,test_coordinator_sc.py,test_translations.py}`

- [ ] **Step 1: Tests first (RED).** `tests/test_coordinator_flags.py`: delete all `sc_discharge_handoff` tests and the `_sc_battery_tolerance`/`_sc_power_smoothing_seconds` tuning tests; keep `_sc_min_dwell_seconds` default/override and the `entity_voltx_battery_power` default test; add:

```python
def test_removed_options_are_not_exposed():
    import custom_components.grid_coordinator.const as c

    for name in ("CONF_SC_BATTERY_TOLERANCE", "CONF_SC_DISCHARGE_HANDOFF", "CONF_SC_POWER_SMOOTHING_SECONDS"):
        assert not hasattr(c, name)
```

  `tests/test_coordinator_sc.py`: delete `test_ema_*`; in `_coord` drop the EMA attributes and `_update_voltx_power_ema` binding. `tests/test_translations.py`: `PARAM_KEYS = ("sc_min_dwell_seconds",)` and add an assertion that the removed keys are absent from both flows' `data`. Run the three files — Expected FAIL.
- [ ] **Step 2: const.py.** Delete `CONF_SC_BATTERY_TOLERANCE`, `CONF_SC_DISCHARGE_HANDOFF`, `CONF_SC_POWER_SMOOTHING_SECONDS` and the matching three `DEFAULT_*` lines.
- [ ] **Step 3: config_flow.py.** Remove the three names from the imports, their three `vol.Required(...)` fields in `_params_schema`, and their `current_params` entries (keep `sc_min_dwell_seconds`).
- [ ] **Step 4: translations.** Remove the three keys' labels and descriptions from `config.step.user` and `options.step.init` (`data` and `data_description`) using the same JSON round-trip script approach as before (assert the round-trip is byte-identical first); update the `sc_min_dwell_seconds` description to drop the "5x" sentence.
- [ ] **Step 5: coordinator.py.** (a) imports: remove `ema_update`, the three removed `CONF_*`/`DEFAULT_*` names; (b) `__init__`: delete `self._voltx_power_ema` and `self._voltx_power_ema_at` (keep `self._sc_state = ScState()`); (c) delete `_update_voltx_power_ema` and the properties `_sc_discharge_handoff`, `_sc_battery_tolerance`, `_sc_power_smoothing_seconds`; (d) in `_async_update_data` replace the block from `voltx_power_raw = self._read_voltx_power()` through the `decide_self_consumption(...)` call with:

```python
        voltx_power_raw = self._read_voltx_power()
        # Safety conditions bypass the dwell: a limit breach or Voltx control switched off
        # must leave the handoff immediately (the handoff branch has no grid clamp).
        limit_breach = grid_actual > self._import_limit or grid_actual < -self._export_limit
        was_active = self._sc_state.active
        self._sc_state = decide_self_consumption(
            state=self._sc_state,
            now=time.monotonic(),
            effective_target=effective_target,
            deadband=self._self_consumption_deadband,
            min_dwell_s=self._sc_min_dwell_seconds,
            force_exit=limit_breach or not voltx_control,
            bypass_lockout=plan_is_stale,
        )
```

  and simplify the two debug logs to `target=%.0fW power=%s` (no setpoint/smoothed/d fields; keep the Solax cmd/mode fields). The `soc`, `voltx_control`, `solax_on` reads stay (the follow uses them).
- [ ] **Step 6: Run** `python3 -m pytest tests -q` — Expected: all pass (the legacy `should_hold_self_consumption` tests in `tests/test_budget.py` are unchanged); `python3 -m py_compile` the touched modules; `ruff check --select F` on the touched source files — no undefined/unused names.
- [ ] **Step 7: Commit** `refactor: remove the superseded handoff options and smoothed-power code`.

### Task 4: Real-HA smoke tests and docs

**Files:** `scripts/ha_smoke/test_smoke_ha.py`, `scripts/ha_smoke/README.md`, `docs/configuration.md`, local `CLAUDE.md`

- [ ] **Step 1: Smoke tests.** In `test_smoke_ha.py`: `test_charging_shortfall_beyond_tolerance_stays_in_tracking` becomes `test_charging_shortfall_stays_in_the_handoff` asserting `SELF_CONSUMPTION`; `test_unreadable_power_falls_back_to_legacy_clause` becomes `test_unreadable_power_does_not_affect_the_handoff` (power `unavailable`, plan -1247 -> still `SELF_CONSUMPTION`, and `coordinator._solax_active` stays False with Solax off); the `nan`/`inf` test keeps asserting the update does not raise and the handoff is unaffected; `test_stale_plan_hands_off_even_with_a_pending_lockout` sets `ScState(active=False, last_transition_at=time.monotonic())` (no `lockout_s`) with a pending dwell; the options-flow test asserts the removed names are absent from the init-step schema and that `sc_min_dwell_seconds` is present with default 120 and saves. Add `test_entry_carrying_removed_option_keys_still_works`: entry data includes `sc_battery_tolerance`, `sc_discharge_handoff`, `sc_power_smoothing_seconds`; setup succeeds and the handoff still engages.
- [ ] **Step 2: Run** `scripts/ha_smoke/run` — Expected: unit suite and smoke all pass (rc 0). Update the README coverage bullets accordingly.
- [ ] **Step 3: Docs.** `docs/configuration.md`: rewrite the "Self-consumption handoff tuning" subsection — handoff whenever `|grid_target|` is within the deadband (plus the 25 W exit margin once active) for any plan; `sc_min_dwell_seconds` (120 s) with the safety bypasses; Solax follow unchanged; add a one-line note that `sc_battery_tolerance`, `sc_discharge_handoff` and `sc_power_smoothing_seconds` were removed in 2026.10.2 and stored values are ignored. Local `CLAUDE.md`: update the "Self-consumption handoff" paragraph the same way (not committed).
- [ ] **Step 4: Commit** `test: smoke tests and docs for the simplified handoff`.

### Task 5: Release 2026.10.2

- [ ] Run the full bare suite and `scripts/ha_smoke/run` once more; push the branch; open the PR (body: problem with the live evidence, the change and removals, verification, the accepted SOC-drift trade-off); wait for CI green; merge with a merge commit; check out `main`, pull; run `scripts/release 2026.10.2 "Hand off at zero grid target regardless of battery shortfall" --notes-file <notes>` using the same notes structure as 2026.10.1 (What's new / Maintenance / What it does footer), stating: removed options and that the behaviour on upgrade needs no input; the live evidence; the requirement to restart HA fully; the EMHASS `battery_stress_cost` finding as a recommendation (not part of this release). Verify the tag points at `main` and `manifest.json` reads 2026.10.2.

## Self-Review

- **Spec coverage:** decision without battery inputs, dwell/bypass kept -> Task 1; removals of options, EMA, back-off -> Tasks 1 and 3; sim persistent-shortfall behaviour -> Task 2; smoke + docs -> Task 4; release -> Task 5; accepted SOC-drift trade-off recorded in the spec and PR/notes (ADO issue only if drift is observed).
- **Placeholder scan:** none; code steps carry their code, mechanical removals name the exact symbols.
- **Type consistency:** `decide_self_consumption` signature and `ScState` fields are identical in Task 1 tests, the Task 2 driver and the Task 3 coordinator call.
