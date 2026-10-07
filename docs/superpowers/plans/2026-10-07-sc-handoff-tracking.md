# Self-consumption handoff: battery tracking, flap suppression, Solax follow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hand off to the Voltx native self-consumption mode whenever `grid_target` is near 0 and the battery is not falling short of a charging plan, without flapping, and keep Solax (whose native mode does not work) following Voltx during the handoff.

**Architecture:** The decision becomes one pure function with explicit state (`decide_self_consumption`, `ScState`) plus a pure Solax-follow function, both in `budget.py` and shared by `coordinator.py` and a new closed-loop simulator in `tests/sim/`. The coordinator gains a smoothed Voltx battery power input, three options and one entity, and a rewritten handoff branch.

**Tech Stack:** Python 3.14, Home Assistant custom integration, pytest. Pure logic runs on a bare interpreter (`python3 -m pytest tests/`; `tests/conftest.py` stubs HA).

**Spec:** `docs/superpowers/specs/2026-10-07-sc-handoff-tracking-design.md` (read it first; it contains the replay evidence behind the design).

## Global Constraints

- Sign conventions are unchanged: battery power / `voltx_command` / `mpc_batt_cmd` positive = discharging; grid positive = importing; `sensor.voltx_battery_battery_power` uses the same convention (verified live: `-2069` while charging).
- `budget.py` stays free of Home Assistant imports.
- `tolerance == 0` or an unreadable Voltx power sensor must reproduce today's behaviour (legacy clause, including `sc_discharge_handoff`).
- Solax never opposes Voltx during the handoff (`share >= 0`) and must not be commanded from a stale Voltx power reading.
- The handoff never delays: manual override, disabled gate, Voltx control helper off, or an import/export limit breach.
- New config defaults: `sc_battery_tolerance` 300 W, `sc_min_dwell_seconds` 120, `sc_power_smoothing_seconds` 60, `entity_voltx_battery_power` = `sensor.voltx_battery_battery_power`.
- Python changes only take effect after a **full HA restart** in the devcontainer (CLAUDE.md); lint/format only inside the devcontainer. `main` already has ~890 ruff findings and 11 unformatted files — match surrounding style, never run repo-wide `ruff --fix` / `ruff format`.
- Do not commit `CLAUDE.md` (it is git-ignored); update it locally only.
- No secrets in tool calls. Do not file an ADO issue as part of this plan (see "Follow-ups").
- Commits end with the attribution lines from the session's system reminder.

## Review Focus

Failure modes the spec implies that most need an explicit test, most likely first:

1. Voltx power sensor `unavailable` / non-numeric: decision falls back to the legacy clause, EMA resets, Solax is released not commanded (Task 1 `test_unavailable_power_uses_legacy_clause`; Task 4 `test_read_voltx_power_*`, `test_follow_releases_*`).
2. Persistent charging shortfall (plan −1247 W, only ~300 W surplus): must not cycle every 2 × dwell (Task 2 `test_persistent_charging_shortfall_backs_off_instead_of_flapping`; Task 1 back-off tests).
3. Battery discharging far above an idle/discharge plan: must stay in the handoff (Task 1 parametrized test; Task 2 `test_idle_or_discharge_plan_never_exits_for_discharging_above_plan`).
4. Solax at its SOC ceiling/floor, or capacities missing (share 0): Solax is not commanded and the real constraint is reported (Task 1 follow tests; Task 2 ceiling scenario; Task 4 `test_solax_shares_*`).
5. Import/export limit breach or Voltx control off while in the handoff: exits immediately, inside the dwell (Task 1 `test_force_exit_*`; coordinator ordering checked in Task 4 step 9 and Task 5 step 3).

---

## File Structure

- Modify `custom_components/grid_coordinator/models.py` — add `SolaxMode.FOLLOW_VOLTX`.
- Modify `custom_components/grid_coordinator/budget.py` — add `ScState`, `ema_update`, `decide_self_consumption`, `compute_solax_follow` and constants. Keep `should_hold_self_consumption` (legacy fallback; its tests stay).
- Modify `custom_components/grid_coordinator/const.py`, `config_flow.py`, `translations/en.json` — new options and entity.
- Modify `custom_components/grid_coordinator/coordinator.py` — option readers, Voltx power read + EMA, `_solax_shares` extraction, `_async_solax_follow_voltx`, rewritten handoff branch.
- Create `tests/test_sc_handoff.py`, `tests/test_coordinator_sc.py`, `tests/test_translations.py`; extend `tests/test_coordinator_flags.py`.
- Create `tests/sim/{__init__,plant,driver,test_replay,test_cloud}.py` — closed-loop simulator and scenarios.
- Modify `docs/configuration.md`.

---

### Task 1: Pure decision, EMA and Solax-follow logic

**Files:**
- Modify: `custom_components/grid_coordinator/models.py` (SolaxMode)
- Modify: `custom_components/grid_coordinator/budget.py` (imports at top; new block immediately before `def compute_voltx_command(`)
- Test: `tests/test_sc_handoff.py` (create)

**Interfaces:**
- Produces (used by Tasks 2 and 4):
  - `ScState(active: bool = False, last_transition_at: float | None = None, lockout_s: float = 0.0)` frozen dataclass
  - `ema_update(prev: float | None, sample: float, dt_s: float, tau_s: float) -> float`
  - `decide_self_consumption(*, state, now, effective_target, voltx_setpoint, smoothed_voltx_power, deadband, tolerance, min_dwell_s, allow_discharge_plan=False, force_exit=False) -> ScState`
  - `compute_solax_follow(*, voltx_power, share, solax_soc, solax_soc_min, solax_soc_max, solax_max_charge, solax_max_discharge, grid_actual, import_limit, export_limit, prev_solax_cmd) -> tuple[float, SolaxMode]`
  - constants `SHORTFALL_REENTRY_MULT = 5`, `MAX_FOLLOW_SHARE = 0.95`; `SolaxMode.FOLLOW_VOLTX == "follow_voltx"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sc_handoff.py` with exactly:

```python
"""Pure-logic tests for the self-consumption handoff decision, EMA, and Solax follow."""

import pytest

from custom_components.grid_coordinator.budget import (
    MAX_FOLLOW_SHARE,
    SELF_CONSUMPTION_EXIT_MARGIN,
    SHORTFALL_REENTRY_MULT,
    ScState,
    compute_solax_follow,
    decide_self_consumption,
    ema_update,
)
from custom_components.grid_coordinator.models import SolaxMode

BASE = dict(
    deadband=50.0,
    tolerance=300.0,
    min_dwell_s=120.0,
)


def decide(state=None, *, now=1000.0, target=0.0, setpoint=-1000.0, power=-900.0, **kw):
    args = {**BASE, **kw}
    return decide_self_consumption(
        state=state or ScState(),
        now=now,
        effective_target=target,
        voltx_setpoint=setpoint,
        smoothed_voltx_power=power,
        **args,
    )


# ── EMA ──────────────────────────────────────────────────────────────────────

def test_ema_seeds_on_first_sample():
    assert ema_update(None, 500.0, 10.0, 60.0) == 500.0


def test_ema_tau_zero_is_raw_sample():
    assert ema_update(100.0, 500.0, 10.0, 0.0) == 500.0


def test_ema_moves_toward_sample_by_time_constant():
    # one time-constant closes ~63.2 % of the gap
    assert ema_update(0.0, 100.0, 60.0, 60.0) == pytest.approx(63.212, abs=0.01)


def test_ema_zero_dt_holds():
    assert ema_update(10.0, 500.0, 0.0, 60.0) == 10.0


# ── decision: target clause ───────────────────────────────────────────────────

def test_enters_when_target_in_deadband_and_on_plan():
    assert decide().active is True


def test_does_not_enter_when_target_outside_deadband():
    assert decide(target=80.0).active is False


def test_exit_margin_applies_to_target_once_active():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    inside = 50.0 + SELF_CONSUMPTION_EXIT_MARGIN
    assert decide(active, target=inside).active is True
    assert decide(active, target=inside + 1).active is False


def test_deadband_zero_has_no_margin():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    assert decide(active, deadband=0.0, target=1.0).active is False


# ── decision: battery clause (charging plan only) ─────────────────────────────

def test_charging_plan_battery_on_plan_enters():
    assert decide(setpoint=-1247.0, power=-1200.0).active is True


def test_charging_plan_absorbing_more_than_plan_enters_and_stays():
    st = decide(setpoint=-1247.0, power=-3000.0)
    assert st.active is True
    assert decide(st, now=2000.0, setpoint=-1247.0, power=-3000.0).active is True


def test_charging_plan_shortfall_beyond_tolerance_does_not_enter():
    assert decide(setpoint=-1247.0, power=-900.0).active is False  # d = 347 > 300


def test_charging_plan_shortfall_exit_has_margin_once_active():
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    # d = 320: above tolerance 300 but inside 300 + 25 margin -> stays
    assert decide(active, setpoint=-1000.0, power=-680.0).active is True
    # d = 330 -> exits
    assert decide(active, setpoint=-1000.0, power=-670.0).active is False


@pytest.mark.parametrize("setpoint", [0.0, 400.0])
def test_idle_or_discharge_plan_ignores_actual_discharge(setpoint):
    # Battery covering house load far above plan must not block or end the handoff.
    assert decide(setpoint=setpoint, power=1500.0).active is True
    active = ScState(active=True, last_transition_at=0.0, lockout_s=120.0)
    assert decide(active, setpoint=setpoint, power=1500.0).active is True


# ── decision: fallback ────────────────────────────────────────────────────────

def test_tolerance_zero_uses_legacy_clause():
    # legacy: a charge setpoint beyond the deadband blocks the handoff
    assert decide(tolerance=0.0, setpoint=-1000.0, power=-1000.0).active is False
    assert decide(tolerance=0.0, setpoint=10.0, power=0.0).active is True


def test_tolerance_zero_discharge_flag_lets_discharge_plan_through():
    assert decide(tolerance=0.0, setpoint=800.0, power=0.0).active is False
    assert decide(tolerance=0.0, setpoint=800.0, power=0.0, allow_discharge_plan=True).active is True


def test_unavailable_power_uses_legacy_clause():
    assert decide(setpoint=-1000.0, power=None).active is False
    assert decide(setpoint=10.0, power=None).active is True


# ── decision: dwell, back-off and bypass ──────────────────────────────────────

def test_dwell_blocks_exit_then_allows_it():
    entered = decide(now=1000.0)
    assert entered.active is True
    assert decide(entered, now=1000.0 + 119.0, target=500.0).active is True   # locked
    assert decide(entered, now=1000.0 + 120.0, target=500.0).active is False  # released


def test_dwell_blocks_reentry():
    exited = ScState(active=False, last_transition_at=1000.0, lockout_s=120.0)
    assert decide(exited, now=1100.0).active is False
    assert decide(exited, now=1120.0).active is True


def test_min_dwell_zero_disables_lock():
    entered = decide(now=1000.0, min_dwell_s=0.0)
    assert decide(entered, now=1000.0, target=500.0, min_dwell_s=0.0).active is False


def test_shortfall_exit_sets_longer_reentry_lockout():
    entered = decide(now=1000.0, setpoint=-1000.0, power=-1000.0)
    out = decide(entered, now=2000.0, setpoint=-1000.0, power=-100.0)
    assert out.active is False
    assert out.lockout_s == 120.0 * SHORTFALL_REENTRY_MULT


def test_target_exit_keeps_normal_lockout():
    entered = decide(now=1000.0)
    out = decide(entered, now=2000.0, target=500.0)
    assert out.active is False
    assert out.lockout_s == 120.0


def test_force_exit_bypasses_dwell():
    entered = decide(now=1000.0)
    out = decide(entered, now=1001.0, force_exit=True)
    assert out.active is False


def test_force_exit_blocks_entry():
    assert decide(force_exit=True).active is False


# ── Solax follow ─────────────────────────────────────────────────────────────

FOLLOW = dict(
    solax_soc=50.0, solax_soc_min=20.0, solax_soc_max=95.0,
    solax_max_charge=3000.0, solax_max_discharge=3000.0,
    grid_actual=0.0, import_limit=12000.0, export_limit=10000.0, prev_solax_cmd=0.0,
)


def test_follow_scales_voltx_power_by_share_ratio():
    cmd, mode = compute_solax_follow(voltx_power=-1500.0, share=0.4, **FOLLOW)
    assert cmd == pytest.approx(-1000.0)  # 1500 * 0.4 / 0.6
    assert mode == SolaxMode.FOLLOW_VOLTX


def test_follow_has_voltx_sign_discharge():
    cmd, mode = compute_solax_follow(voltx_power=900.0, share=0.5, **FOLLOW)
    assert cmd == pytest.approx(900.0)
    assert mode == SolaxMode.FOLLOW_VOLTX


def test_follow_zero_when_voltx_idle():
    assert compute_solax_follow(voltx_power=0.0, share=0.4, **FOLLOW) == (0.0, SolaxMode.SELF_CONSUMPTION)


def test_follow_share_capped_so_ratio_stays_finite():
    cmd, _ = compute_solax_follow(voltx_power=-100.0, share=1.0, **FOLLOW)
    assert cmd == pytest.approx(-100.0 * MAX_FOLLOW_SHARE / (1 - MAX_FOLLOW_SHARE))


def test_follow_respects_inverter_charge_limit():
    cmd, _ = compute_solax_follow(voltx_power=-5000.0, share=0.5, **FOLLOW)
    assert cmd == -3000.0


def test_follow_reports_soc_ceiling_and_does_not_charge():
    cmd, mode = compute_solax_follow(voltx_power=-1500.0, share=0.4, **{**FOLLOW, "solax_soc": 96.0})
    assert (cmd, mode) == (0.0, SolaxMode.SOC_CEILING)


def test_follow_reports_soc_floor_and_does_not_discharge():
    cmd, mode = compute_solax_follow(voltx_power=1500.0, share=0.4, **{**FOLLOW, "solax_soc": 19.0})
    assert (cmd, mode) == (0.0, SolaxMode.SOC_FLOOR)


def test_follow_grid_clamp_stops_charging_past_import_ceiling():
    # Grid already at the import limit: Solax must not add charging load.
    cmd, _ = compute_solax_follow(
        voltx_power=-1500.0, share=0.4, **{**FOLLOW, "grid_actual": 12000.0}
    )
    assert cmd >= 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_sc_handoff.py -q`
Expected: collection error `ImportError: cannot import name 'MAX_FOLLOW_SHARE' from 'custom_components.grid_coordinator.budget'`.

- [ ] **Step 3: Add the enum member**

In `custom_components/grid_coordinator/models.py`, inside `class SolaxMode`, after the `SOC_CEILING` line add:

```python
    FOLLOW_VOLTX = "follow_voltx"         # commanded as a share of Voltx's actual power during its native self-consumption handoff
```

- [ ] **Step 4: Implement the pure functions**

In `budget.py`, change the import block at the top to:

```python
from __future__ import annotations

import math
from dataclasses import dataclass

from .models import CoordinatorData, CoordinatorMode, SolaxMode, VoltxDiag
```

Then insert this block immediately above `def compute_voltx_command(` (after `should_hold_self_consumption`):

```python
@dataclass(frozen=True)
class ScState:
    """Self-consumption handoff state carried between ticks.

    `last_transition_at` is a monotonic timestamp in seconds (None until the first
    transition) so the minimum-dwell lockout survives across ticks.
    """

    active: bool = False
    last_transition_at: float | None = None
    lockout_s: float = 0.0  # lockout length measured from last_transition_at


# After leaving the handoff because the battery fell short of a charging plan, hold
# tracking this many times longer than the normal dwell before re-entering.  Tracking
# pins actual power to the setpoint, so the entry test is trivially satisfied there and
# native mode then re-reveals the shortfall — without a back-off that cycles every
# 2 x dwell, toggling the inverter work mode (see the shortfall scenario in tests/sim).
SHORTFALL_REENTRY_MULT = 5


def ema_update(prev: float | None, sample: float, dt_s: float, tau_s: float) -> float:
    """Time-constant EMA: alpha = 1 - exp(-dt/tau). Seeds on the first sample.

    tau_s <= 0 disables smoothing (returns the raw sample), so a 0 option means
    "off" rather than a divide-by-zero.
    """
    if prev is None or tau_s <= 0:
        return sample
    alpha = 1.0 - math.exp(-max(dt_s, 0.0) / tau_s)
    return prev + alpha * (sample - prev)


def decide_self_consumption(
    *,
    state: ScState,
    now: float,
    effective_target: float,
    voltx_setpoint: float,
    smoothed_voltx_power: float | None,
    deadband: float,
    tolerance: float,
    min_dwell_s: float,
    allow_discharge_plan: bool = False,
    force_exit: bool = False,
) -> ScState:
    """Decide whether the Voltx native self-consumption handoff is active this tick.

    Holds the handoff when |effective_target| is within the (hysteresis-widened)
    deadband AND the battery clause passes.  The battery clause (tolerance > 0 and a
    smoothed actual power available) applies only to a charging plan
    (voltx_setpoint < 0): d = smoothed_voltx_power - voltx_setpoint must not exceed
    `tolerance` (+ SELF_CONSUMPTION_EXIT_MARGIN once active), so a battery absorbing
    MORE than planned never leaves the handoff.  For an idle/discharge plan the clause
    always passes — at a ~0 W target native self-consumption covers the load, which is
    what the plan wants.  With tolerance == 0, or no actual power, the legacy clause
    from should_hold_self_consumption applies.

    After any transition the state is locked for `min_dwell_s`; `force_exit` (safety
    conditions: limit breach, control off) bypasses the lock and forces the handoff off.
    """
    threshold = deadband
    if state.active and deadband > 0:
        threshold += SELF_CONSUMPTION_EXIT_MARGIN
    target_ok = abs(effective_target) <= threshold
    if tolerance > 0 and smoothed_voltx_power is not None:
        if voltx_setpoint >= 0:
            battery_ok = True
        else:
            tol_eff = tolerance + (SELF_CONSUMPTION_EXIT_MARGIN if state.active else 0.0)
            battery_ok = (smoothed_voltx_power - voltx_setpoint) <= tol_eff
    elif allow_discharge_plan:
        battery_ok = voltx_setpoint >= -threshold
    else:
        battery_ok = abs(voltx_setpoint) <= threshold
    want = target_ok and battery_ok and not force_exit
    if want == state.active:
        return state
    locked = (
        state.last_transition_at is not None
        and (now - state.last_transition_at) < state.lockout_s
    )
    if locked and not force_exit:
        return state
    shortfall_exit = state.active and not want and target_ok and not battery_ok and not force_exit
    lockout = min_dwell_s * (SHORTFALL_REENTRY_MULT if shortfall_exit else 1)
    return ScState(active=want, last_transition_at=now, lockout_s=lockout)


# Cap on Solax's share while following Voltx so s/(1-s) stays finite.
MAX_FOLLOW_SHARE = 0.95


def compute_solax_follow(
    *,
    voltx_power: float,
    share: float,
    solax_soc: float,
    solax_soc_min: float,
    solax_soc_max: float,
    solax_max_charge: float,
    solax_max_discharge: float,
    grid_actual: float,
    import_limit: float,
    export_limit: float,
    prev_solax_cmd: float,
) -> tuple[float, SolaxMode]:
    """Solax command while Voltx is in native self-consumption: follow Voltx's power.

    Voltx moves (1 - share) of the combined battery power, so Solax moves
    voltx_power * share / (1 - share), always with Voltx's sign (share >= 0) — it can
    never oppose Voltx, and being a function of Voltx's power rather than of the grid
    error it cannot repeat the 2026-07-09 grid_priority freeze.  The existing tier-1
    clamps (grid safety, SOC floor/ceiling, inverter limits) are reused unchanged.
    grid_actual already contains Voltx's native response; prev_solax_cmd is added back
    inside compute_solax_tier1 to get the Solax-free baseline.
    """
    s = max(0.0, min(share, MAX_FOLLOW_SHARE))
    target = voltx_power * s / (1.0 - s)
    cmd, mode = compute_solax_tier1(
        mpc_batt_cmd=target,
        share=1.0,
        solax_soc=solax_soc,
        solax_soc_min=solax_soc_min,
        solax_soc_max=solax_soc_max,
        solax_max_charge=solax_max_charge,
        solax_max_discharge=solax_max_discharge,
        grid_after_voltx=grid_actual,
        import_limit=import_limit,
        export_limit=export_limit,
        prev_solax_cmd=prev_solax_cmd,
    )
    if mode in (SolaxMode.SOC_FLOOR, SolaxMode.SOC_CEILING):
        return cmd, mode
    if cmd != 0.0:
        return cmd, SolaxMode.FOLLOW_VOLTX
    return 0.0, SolaxMode.SELF_CONSUMPTION
```

- [ ] **Step 5: Run to verify it passes**

Run: `python3 -m pytest tests -q`
Expected: all pass (the existing 86 plus the new `tests/test_sc_handoff.py`).

- [ ] **Step 6: Commit**

```bash
git add custom_components/grid_coordinator/models.py custom_components/grid_coordinator/budget.py tests/test_sc_handoff.py
git commit -m "feat: pure self-consumption handoff decision, EMA and Solax follow logic"
```

---

### Task 2: Closed-loop simulation harness and scenarios

Builds the harness the spec requires and uses it to test the logic against the live 2026-10-07 data (issue 1) and cloud cycling (issue 2) **before** any coordinator wiring. Modelled on `~/Projects/homeassistant-config/tests/` (virtual time, replay of recorded inputs) but pure Python and tick-based.

**Files:**
- Create: `tests/sim/__init__.py` (empty), `tests/sim/plant.py`, `tests/sim/driver.py`, `tests/sim/test_replay.py`, `tests/sim/test_cloud.py`

**Interfaces:**
- Consumes: everything Task 1 produces, plus existing `compute_voltx_command`, `compute_solax_tier1`, `compute_solax_share`, `should_hold_self_consumption`, and `const` defaults.
- Produces: `tests.sim.driver.{LEGACY, NEW, Policy, Result, run, make_plant}`. `run(policy, inputs, duration_s, plant=None, *, open_loop_power=False) -> Result`; `inputs(t) -> (load_w, solar_w, grid_target, mpc_batt, recorded_voltx_power_or_None)`.

- [ ] **Step 1: Create the package and plant model**

Create empty `tests/sim/__init__.py`. Create `tests/sim/plant.py`:

```python
"""Minimal house + two-battery plant for closed-loop simulation of the controller.

Sign conventions match the coordinator: battery power positive = discharging,
grid positive = importing.  grid = load - solar - voltx - solax.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fraction of the gap to its target the Voltx inverter closes per 10 s tick while in
# native self-consumption (firmware reacts fast but not instantly).
NATIVE_LAG = 0.7


@dataclass
class Battery:
    soc: float          # %
    capacity_kwh: float
    max_charge: float   # W (positive number)
    max_discharge: float  # W (positive number)
    power: float = 0.0  # W actual, + = discharge

    def clamp(self, power: float) -> float:
        """Clamp a requested power to inverter limits and the SOC window [0, 100]."""
        power = max(-self.max_charge, min(self.max_discharge, power))
        if self.soc >= 100.0 and power < 0:
            return 0.0
        if self.soc <= 0.0 and power > 0:
            return 0.0
        return power

    def integrate(self, dt_s: float) -> None:
        self.soc -= self.power * dt_s / 3600.0 / (self.capacity_kwh * 1000.0) * 100.0


class Plant:
    """Advances the house one tick given the controller's commands."""

    def __init__(self, voltx: Battery, solax: Battery) -> None:
        self.voltx = voltx
        self.solax = solax
        self.grid = 0.0

    def step(
        self,
        *,
        dt_s: float,
        load_w: float,
        solar_w: float,
        voltx_native: bool,
        voltx_cmd: float,
        solax_cmd: float,
    ) -> float:
        """Apply commands, integrate SOC, return the grid power (W, + = import).

        Solax has no working native mode, so with solax_cmd == 0 it is simply idle.
        """
        self.solax.power = self.solax.clamp(solax_cmd)
        if voltx_native:
            want = load_w - solar_w - self.solax.power
            want = self.voltx.clamp(want)
            self.voltx.power += NATIVE_LAG * (want - self.voltx.power)
            self.voltx.power = self.voltx.clamp(self.voltx.power)
        else:
            self.voltx.power = self.voltx.clamp(voltx_cmd)
        self.voltx.integrate(dt_s)
        self.solax.integrate(dt_s)
        self.grid = load_w - solar_w - self.voltx.power - self.solax.power
        return self.grid
```

- [ ] **Step 2: Create the tick driver**

Create `tests/sim/driver.py`. Its tick ordering deliberately mirrors `coordinator._async_update_data`; the decision and command maths are the real `budget.py` functions:

```python
"""Closed-loop tick driver: runs the pure controller functions the way
coordinator.py's _async_update_data orders them, against sim/plant.py.

Only the parts the self-consumption handoff touches are reproduced (EMA, Solax share
split, handoff decision, Voltx tracking command, Solax tier-1 / follow command).  The
decision and command maths are the real functions from budget.py, so a change there
is exercised here; the *ordering* below mirrors the coordinator and must be kept in
step with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from custom_components.grid_coordinator.budget import (
    ScState,
    compute_solax_follow,
    compute_solax_share,
    compute_solax_tier1,
    compute_voltx_command,
    decide_self_consumption,
    ema_update,
    should_hold_self_consumption,
)
from custom_components.grid_coordinator.const import (
    DEFAULT_IMPORT_LIMIT,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_RAMP_STEP,
    DEFAULT_SOC_BALANCE_DEADBAND,
    DEFAULT_SOC_BALANCE_SENSITIVITY,
    DEFAULT_TIER2_GAIN,
    DEFAULT_TRACKING_DEADBAND,
)
from custom_components.grid_coordinator.models import SolaxMode

from .plant import Battery, Plant

TICK_S = 10.0


@dataclass(frozen=True)
class Policy:
    """Which handoff behaviour the run uses.  `legacy` is today's main branch."""

    legacy: bool
    deadband: float = 200.0
    tolerance: float = 300.0
    min_dwell_s: float = 120.0
    smoothing_s: float = 60.0
    solax_follow: bool = True


LEGACY = Policy(legacy=True, solax_follow=False)
NEW = Policy(legacy=False)


@dataclass
class Result:
    ticks: int = 0
    transitions: int = 0
    export_wh: float = 0.0
    import_wh: float = 0.0
    max_opposing_run: int = 0  # longest run of consecutive ticks with Voltx/Solax opposed
    solax_wh: float = 0.0      # |Solax| throughput
    voltx_wh: float = 0.0      # |Voltx| throughput
    solax_handoff_wh: float = 0.0  # |Solax| throughput on ticks where the handoff was active
    active_ticks: int = 0
    solax_modes: set = field(default_factory=set)
    grid: list = field(default_factory=list)
    active: list = field(default_factory=list)
    voltx: list = field(default_factory=list)
    solax: list = field(default_factory=list)


Inputs = Callable[[float], tuple[float, float, float, float, float | None]]
# t_s -> (load_w, solar_w, grid_target, mpc_batt, voltx_power_override_or_None)


def make_plant(voltx_soc: float = 50.0, solax_soc: float = 50.0) -> Plant:
    return Plant(
        Battery(soc=voltx_soc, capacity_kwh=10.0, max_charge=5000.0, max_discharge=5000.0),
        Battery(soc=solax_soc, capacity_kwh=7.0, max_charge=3000.0, max_discharge=3000.0),
    )


def run(
    policy: Policy,
    inputs: Inputs,
    duration_s: float,
    plant: Plant | None = None,
    *,
    open_loop_power: bool = False,
) -> Result:
    """Run `duration_s` of ticks.  With open_loop_power the Voltx power fed to the
    decision comes from inputs()'s 5th element (replaying a recording) instead of the
    plant, and the plant's grid is not meaningful."""
    plant = plant or make_plant()
    res = Result()
    state = ScState()
    power_ema: float | None = None
    prev_cmd = 0.0
    solax_last = 0.0
    grid = 0.0
    last_active: bool | None = None
    opposing_run = 0
    t = 0.0
    while t <= duration_s:
        load, solar, target, mpc, recorded_power = inputs(t)
        voltx_power = recorded_power if open_loop_power else plant.voltx.power
        power_ema = ema_update(power_ema, voltx_power, TICK_S, policy.smoothing_s)

        s1 = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=mpc, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )
        s2 = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=grid - target, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )
        voltx_setpoint = mpc * (1.0 - s1)
        # While following, the share is keyed off the direction Voltx is actually moving.
        s_follow = compute_solax_share(
            voltx_soc=plant.voltx.soc, solax_soc=plant.solax.soc,
            voltx_capacity_kwh=plant.voltx.capacity_kwh, solax_capacity_kwh=plant.solax.capacity_kwh,
            cmd=voltx_power, sensitivity=DEFAULT_SOC_BALANCE_SENSITIVITY, soc_deadband=DEFAULT_SOC_BALANCE_DEADBAND,
        )

        breach = grid > DEFAULT_IMPORT_LIMIT or grid < -DEFAULT_EXPORT_LIMIT
        if policy.legacy:
            want = should_hold_self_consumption(
                target, mpc, policy.deadband, state.active, allow_discharge_plan=False
            )
            if want != state.active:
                state = ScState(active=want, last_transition_at=t)
        else:
            state = decide_self_consumption(
                state=state, now=t, effective_target=target, voltx_setpoint=voltx_setpoint,
                smoothed_voltx_power=power_ema, deadband=policy.deadband,
                tolerance=policy.tolerance, min_dwell_s=policy.min_dwell_s, force_exit=breach,
            )

        if state.active:
            voltx_cmd = 0.0
            if policy.solax_follow:
                solax_cmd, solax_mode = compute_solax_follow(
                    voltx_power=voltx_power, share=s_follow, solax_soc=plant.solax.soc,
                    solax_soc_min=20.0, solax_soc_max=95.0,
                    solax_max_charge=3000.0, solax_max_discharge=3000.0,
                    grid_actual=grid, import_limit=DEFAULT_IMPORT_LIMIT,
                    export_limit=DEFAULT_EXPORT_LIMIT, prev_solax_cmd=solax_last,
                )
            else:
                solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION
            prev_cmd = 0.0
        else:
            voltx_cmd, _mode, _diag = compute_voltx_command(
                grid_actual=grid, grid_target=target, mpc_batt_cmd=voltx_setpoint,
                prev_cmd=prev_cmd, soc=plant.voltx.soc, soc_min=20.0, soc_max=95.0,
                max_charge=5000.0, max_discharge=5000.0, import_limit=DEFAULT_IMPORT_LIMIT,
                export_limit=DEFAULT_EXPORT_LIMIT, ramp_step=DEFAULT_RAMP_STEP,
                plan_is_stale=False, tracking_deadband=DEFAULT_TRACKING_DEADBAND,
                tier2_gain=DEFAULT_TIER2_GAIN * (1.0 - s2),
            )
            grid_after_voltx = grid + prev_cmd - voltx_cmd
            solax_cmd, solax_mode = compute_solax_tier1(
                mpc_batt_cmd=mpc, share=s1, solax_soc=plant.solax.soc,
                solax_soc_min=20.0, solax_soc_max=95.0, solax_max_charge=3000.0,
                solax_max_discharge=3000.0, grid_after_voltx=grid_after_voltx,
                import_limit=DEFAULT_IMPORT_LIMIT, export_limit=DEFAULT_EXPORT_LIMIT,
                prev_solax_cmd=solax_last,
                tier2_term=DEFAULT_TIER2_GAIN * s2 * (grid - target),
            )
            prev_cmd = voltx_cmd
        solax_last = solax_cmd

        grid = plant.step(
            dt_s=TICK_S, load_w=load, solar_w=solar, voltx_native=state.active,
            voltx_cmd=voltx_cmd, solax_cmd=solax_cmd,
        )

        res.ticks += 1
        res.active_ticks += int(state.active)
        if last_active is not None and state.active != last_active:
            res.transitions += 1
        last_active = state.active
        res.export_wh += max(0.0, -grid) * TICK_S / 3600.0
        res.import_wh += max(0.0, grid) * TICK_S / 3600.0
        # Opposed = signs differ with both more than ~50 W from zero.
        if plant.voltx.power * plant.solax.power < -2500.0:
            opposing_run += 1
            res.max_opposing_run = max(res.max_opposing_run, opposing_run)
        else:
            opposing_run = 0
        res.voltx_wh += abs(plant.voltx.power) * TICK_S / 3600.0
        res.solax_wh += abs(plant.solax.power) * TICK_S / 3600.0
        if state.active:
            res.solax_handoff_wh += abs(plant.solax.power) * TICK_S / 3600.0
        res.solax_modes.add(solax_mode)
        res.grid.append(grid)
        res.active.append(state.active)
        res.voltx.append(plant.voltx.power)
        res.solax.append(plant.solax.power)
        t += TICK_S
    return res
```

- [ ] **Step 3: Create the issue-1 replay test**

The series are step-hold values copied from the 2026-10-07 recorder history (Australia/Sydney). The deadband is **assumed 200 W**: the recorded flips only fit a deadband between 178 and 291 W. Confirm against the real option and change the `Policy.deadband` default in `driver.py` if it differs.

Create `tests/sim/test_replay.py`:

```python
"""Issue 1: replay of the live 2026-10-07 07:10-07:34 inputs (Australia/Sydney).

The EMHASS setpoint sat at exactly 0.0 for 4-6 minutes at a time while actual Voltx
power was ~400-600 W, flipping the legacy handoff on and off.  Series are step-hold
values copied from the recorder (sensor.grid_coordinator_grid_target,
sensor.grid_coordinator_mpc_battery_power, sensor.voltx_battery_battery_power).

The replay is open-loop: the recorded Voltx power came from the legacy run, so it is
fed to the decision as-is rather than from the plant.  Deadband is assumed 200 W (the
recorded flips only fit 178-291 W) — confirm against the real options.
"""

from tests.sim.driver import LEGACY, NEW, run


def _t(h: int, m: int, s: int = 0) -> int:
    return (h * 3600 + m * 60 + s) - (7 * 3600 + 10 * 60)


GRID_TARGET = [
    (_t(7, 10), 0.0), (_t(7, 16, 4), -83.1), (_t(7, 18, 3), 0.0), (_t(7, 26, 4), -177.8),
    (_t(7, 28, 3), -176.8), (_t(7, 30, 3), -59.8), (_t(7, 32, 4), 0.0), (_t(7, 34, 4), 706.2),
]
MPC_BATT = [
    (_t(7, 10), 295.22), (_t(7, 10, 4), 866.33), (_t(7, 12, 4), 906.33), (_t(7, 14, 4), 570.78),
    (_t(7, 16, 4), 0.0), (_t(7, 20, 4), 399.89), (_t(7, 22, 3), 613.22), (_t(7, 24, 3), 366.56),
    (_t(7, 26, 4), 0.0), (_t(7, 32, 4), 449.11), (_t(7, 34, 4), 0.0),
]
VOLTX_POWER = [
    (0, 847), (_t(7, 12, 34), 744), (_t(7, 12, 45), 718), (_t(7, 12, 56), 713), (_t(7, 13, 7), 677),
    (_t(7, 13, 18), 687), (_t(7, 13, 28), 677), (_t(7, 13, 40), 687), (_t(7, 13, 51), 668),
    (_t(7, 14, 1), 663), (_t(7, 14, 12), 414), (_t(7, 14, 43), 404), (_t(7, 15, 45), 409),
    (_t(7, 15, 55), 450), (_t(7, 16, 6), 440), (_t(7, 16, 16), 295), (_t(7, 16, 47), 269),
    (_t(7, 17, 18), 352), (_t(7, 17, 49), 1442), (_t(7, 18, 51), 295), (_t(7, 19, 22), 1442),
    (_t(7, 19, 52), 1437), (_t(7, 20, 5), 1463), (_t(7, 20, 16), 367), (_t(7, 20, 27), 506),
    (_t(7, 20, 38), 589), (_t(7, 20, 49), 579), (_t(7, 20, 59), 465), (_t(7, 21, 10), 418),
    (_t(7, 22, 12), 424), (_t(7, 22, 43), 419), (_t(7, 24, 46), 440), (_t(7, 24, 56), 574),
    (_t(7, 25, 7), 714), (_t(7, 25, 18), 538), (_t(7, 25, 49), 533), (_t(7, 26, 16), 595),
    (_t(7, 26, 47), 502), (_t(7, 27, 17), 590), (_t(7, 27, 49), 554), (_t(7, 28, 19), 404),
    (_t(7, 28, 51), 238), (_t(7, 29, 22), 269), (_t(7, 29, 52), 284), (_t(7, 30, 23), 341),
    (_t(7, 30, 55), 1607), (_t(7, 31, 26), 2135), (_t(7, 31, 56), 2243), (_t(7, 32, 5), 2248),
    (_t(7, 32, 16), 434), (_t(7, 32, 27), 651), (_t(7, 32, 37), 806), (_t(7, 32, 49), 734),
    (_t(7, 32, 59), 770), (_t(7, 33, 10), 661), (_t(7, 33, 21), 708), (_t(7, 33, 32), 791),
    (_t(7, 33, 42), 791), (_t(7, 33, 53), 780),
]
DURATION_S = _t(7, 34, 0)


def _hold(series, t):
    value = series[0][1]
    for at, v in series:
        if at <= t:
            value = v
    return value


def _inputs(t):
    return 0.0, 0.0, _hold(GRID_TARGET, t), _hold(MPC_BATT, t), _hold(VOLTX_POWER, t)


def test_legacy_reproduces_the_live_flapping():
    res = run(LEGACY, _inputs, DURATION_S, open_loop_power=True)
    assert res.transitions == 4  # live history: 07:16, 07:20, 07:26, 07:32


def test_new_rule_does_not_flap_on_the_same_inputs():
    res = run(NEW, _inputs, DURATION_S, open_loop_power=True)
    assert res.transitions == 0
    assert res.active_ticks > res.ticks * 0.9
```

- [ ] **Step 4: Create the cloud-cycling / Solax scenarios**

Create `tests/sim/test_cloud.py`:

```python
"""Issue 2 + Solax piggy-back: closed loop against the plant under cloud cycling.

Plan: grid target 0 W with the battery setpoint charging at 1247 W (the live plan seen
2026-10-07).  Solar alternates between a 3 kW and a 300 W surplus.  Solax has no
working native mode, so it must be commanded to follow Voltx during the handoff.
"""

import pytest

from custom_components.grid_coordinator.models import SolaxMode
from tests.sim.driver import LEGACY, NEW, Policy, make_plant, run

LOAD_W = 500.0
DURATION_S = 1200


def _cloud(period_s: float, high: float = 3500.0, low: float = 800.0):
    def inputs(t):
        solar = high if (t % period_s) < period_s / 2 else low
        return LOAD_W, solar, 0.0, -1247.0, None

    return inputs


@pytest.mark.parametrize("period_s", [40, 80, 160])
def test_handoff_cuts_export_and_import_under_cloud_cycling(period_s):
    base = run(LEGACY, _cloud(period_s), DURATION_S)
    new = run(NEW, _cloud(period_s), DURATION_S)
    assert new.export_wh < 0.5 * base.export_wh
    assert new.import_wh <= base.import_wh  # no new grid import from the change


@pytest.mark.parametrize("period_s", [40, 80, 160])
def test_solax_follows_voltx_without_a_standoff(period_s):
    new = run(NEW, _cloud(period_s), DURATION_S)
    assert SolaxMode.FOLLOW_VOLTX in new.solax_modes
    # Up to two ticks of opposition at a step change in solar are expected (Solax
    # reacts a tick late); a sustained standoff like 2026-07-09 would be unbounded.
    assert new.max_opposing_run <= 3
    # Solax carries roughly its capacity share (7 / 17 = 0.41) of the throughput.
    share = new.solax_wh / (new.solax_wh + new.voltx_wh)
    assert 0.33 <= share <= 0.47


def test_without_follow_solax_stays_idle_in_the_handoff():
    res = run(Policy(legacy=False, solax_follow=False), _cloud(80), DURATION_S)
    assert res.solax_handoff_wh == 0.0


def test_solax_at_ceiling_is_reported_and_voltx_absorbs_alone():
    plant = make_plant(solax_soc=96.0)
    res = run(NEW, _cloud(80), DURATION_S, plant=plant)
    assert SolaxMode.SOC_CEILING in res.solax_modes
    assert res.solax_handoff_wh == 0.0
    assert res.max_opposing_run == 0


def _active_runs(flags):
    runs, count = [], 1
    for prev, cur in zip(flags, flags[1:]):
        if prev == cur:
            count += 1
        else:
            runs.append((prev, count))
            count = 1
    runs.append((flags[-1], count))
    return runs


def test_persistent_charging_shortfall_backs_off_instead_of_flapping():
    # Plan wants 1247 W of charging but only a 300 W surplus exists, for 30 minutes.
    # Tracking pins actual to the setpoint (so entry is easy); native mode then shows the
    # shortfall and exits.  The re-entry back-off must stop that cycling every 2 x dwell.
    def inputs(t):
        return LOAD_W, 800.0, 0.0, -1247.0, None

    base = run(LEGACY, inputs, 1800)
    res = run(NEW, inputs, 1800)
    runs = _active_runs(res.active)
    # Never exits before the 120 s dwell (12 ticks) ...
    assert all(n >= 12 for active, n in runs if active)
    # ... and after a shortfall exit tracking is held >= 5 x dwell (60 ticks) before
    # re-entering (the trailing run may be cut short by the end of the simulation).
    assert all(n >= 60 for active, n in runs[1:-1] if not active)
    assert res.transitions <= 7
    assert res.import_wh < base.import_wh  # still better than staying in tracking


def test_idle_or_discharge_plan_never_exits_for_discharging_above_plan():
    # Plan idle (0 W) but the house load forces the battery to discharge ~1.5 kW.
    def inputs(t):
        return 2000.0, 500.0, 0.0, 0.0, None

    res = run(NEW, inputs, 600)
    assert res.transitions <= 1  # at most the initial entry
    assert res.active_ticks >= res.ticks - 2
```

- [ ] **Step 5: Confirm the baseline reproduces issue 1, then run everything**

Run: `python3 -m pytest tests/sim/test_replay.py::test_legacy_reproduces_the_live_flapping -v`
Expected: PASS — the legacy `main` logic yields exactly 4 transitions on the live inputs. This is the "baseline shows the flapping first" check; if it fails, the replay data or the deadband assumption is wrong, so stop and investigate rather than editing the assertion.

Run: `python3 -m pytest tests -q`
Expected: all pass. Reference numbers from the planning prototype (for sanity, not assertions): cloud cycling, period 80 s, 20 min — legacy export 222 Wh / import 127 Wh; new export 55 Wh / import 40 Wh; Solax throughput share 0.41; at most 2 consecutive opposing ticks. Persistent shortfall, 30 min — legacy import 318 Wh, new 258 Wh with 6 transitions (cycle ≈ 12 min).

- [ ] **Step 6: Commit**

```bash
git add tests/sim
git commit -m "test: closed-loop simulation harness for the self-consumption handoff"
```

---

### Task 3: Config surface (options, entity, translations, readers)

**Files:**
- Modify: `custom_components/grid_coordinator/const.py`
- Modify: `custom_components/grid_coordinator/config_flow.py`
- Modify: `custom_components/grid_coordinator/translations/en.json`
- Modify: `custom_components/grid_coordinator/coordinator.py` (const imports + three properties only)
- Test: `tests/test_coordinator_flags.py` (append), `tests/test_translations.py` (create)

**Interfaces:**
- Produces: `CONF_SC_BATTERY_TOLERANCE`, `CONF_SC_MIN_DWELL_SECONDS`, `CONF_SC_POWER_SMOOTHING_SECONDS`, `CONF_ENTITY_VOLTX_BATTERY_POWER`, `DEFAULT_SC_BATTERY_TOLERANCE`, `DEFAULT_SC_MIN_DWELL_SECONDS`, `DEFAULT_SC_POWER_SMOOTHING_SECONDS`, `ENTITY_VOLTX_BATTERY_POWER`; `GridCoordinator._sc_battery_tolerance`, `._sc_min_dwell_seconds`, `._sc_power_smoothing_seconds` (float properties).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator_flags.py`:

```python


# ── self-consumption handoff tuning options ──────────────────────────────────

from custom_components.grid_coordinator.const import (  # noqa: E402
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_SC_BATTERY_TOLERANCE,
    CONF_SC_MIN_DWELL_SECONDS,
    CONF_SC_POWER_SMOOTHING_SECONDS,
    DEFAULT_SC_BATTERY_TOLERANCE,
    DEFAULT_SC_MIN_DWELL_SECONDS,
    DEFAULT_SC_POWER_SMOOTHING_SECONDS,
    ENTITY_ID_DEFAULTS,
)


def _prop(name: str, *, options: dict | None = None, data: dict | None = None):
    coordinator = SimpleNamespace(
        _entry=SimpleNamespace(options=options or {}, data=data or {})
    )
    coordinator._opt = MethodType(GridCoordinator._opt, coordinator)
    return getattr(GridCoordinator, name).fget(coordinator)


def test_sc_tuning_defaults():
    assert (
        DEFAULT_SC_BATTERY_TOLERANCE,
        DEFAULT_SC_MIN_DWELL_SECONDS,
        DEFAULT_SC_POWER_SMOOTHING_SECONDS,
    ) == (300, 120, 60)
    assert _prop("_sc_battery_tolerance") == 300.0
    assert _prop("_sc_min_dwell_seconds") == 120.0
    assert _prop("_sc_power_smoothing_seconds") == 60.0


def test_sc_tuning_options_override_defaults():
    options = {
        CONF_SC_BATTERY_TOLERANCE: 450,
        CONF_SC_MIN_DWELL_SECONDS: 30,
        CONF_SC_POWER_SMOOTHING_SECONDS: 0,
    }
    assert _prop("_sc_battery_tolerance", options=options) == 450.0
    assert _prop("_sc_min_dwell_seconds", options=options) == 30.0
    assert _prop("_sc_power_smoothing_seconds", options=options) == 0.0


def test_voltx_battery_power_entity_default():
    assert ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER] == "sensor.voltx_battery_battery_power"
```

Create `tests/test_translations.py`:

```python
"""Every new handoff option/entity must have a label and description in both flows."""

import json
from pathlib import Path

EN = json.loads(
    (Path(__file__).parents[1] / "custom_components/grid_coordinator/translations/en.json").read_text()
)

PARAM_KEYS = ("sc_battery_tolerance", "sc_min_dwell_seconds", "sc_power_smoothing_seconds")
ENTITY_KEY = "entity_voltx_battery_power"


def _step(flow: str, step: str) -> dict:
    return EN[flow]["step"][step]


def test_param_options_labelled_in_config_and_options_flows():
    for flow, step in (("config", "user"), ("options", "init")):
        data = _step(flow, step)["data"]
        desc = _step(flow, step)["data_description"]
        for key in PARAM_KEYS:
            assert data[key], (flow, key)
            assert desc[key], (flow, key)


def test_battery_power_entity_labelled_in_config_and_options_flows():
    for flow in ("config", "options"):
        step = _step(flow, "entities")
        assert step["data"][ENTITY_KEY]
        assert step["data_description"][ENTITY_KEY]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_coordinator_flags.py tests/test_translations.py -q`
Expected: FAIL (`ImportError` for the new `CONF_*` names).

- [ ] **Step 3: Add constants**

In `const.py`: after `CONF_SC_DISCHARGE_HANDOFF = "sc_discharge_handoff"` add

```python
CONF_SC_BATTERY_TOLERANCE = "sc_battery_tolerance"
CONF_SC_MIN_DWELL_SECONDS = "sc_min_dwell_seconds"
CONF_SC_POWER_SMOOTHING_SECONDS = "sc_power_smoothing_seconds"
```

after `CONF_ENTITY_VOLTX_WORK_MODE = "entity_voltx_work_mode"` add

```python
CONF_ENTITY_VOLTX_BATTERY_POWER = "entity_voltx_battery_power"
```

after the `DEFAULT_SC_DISCHARGE_HANDOFF = False ...` line add

```python
DEFAULT_SC_BATTERY_TOLERANCE = 300      # W — max charging shortfall vs plan that still allows the handoff; 0 = off
DEFAULT_SC_MIN_DWELL_SECONDS = 120      # s — lock after any handoff transition (one EMHASS republish)
DEFAULT_SC_POWER_SMOOTHING_SECONDS = 60  # s — EMA time constant for actual Voltx battery power
```

after `ENTITY_VOLTX_WORK_MODE = "select.voltx_inverter_work_mode"` add

```python
ENTITY_VOLTX_BATTERY_POWER = "sensor.voltx_battery_battery_power"      # W, + = discharge
```

and inside the `ENTITY_ID_DEFAULTS` dict (next to `CONF_ENTITY_VOLTX_WORK_MODE: ENTITY_VOLTX_WORK_MODE,`) add `CONF_ENTITY_VOLTX_BATTERY_POWER: ENTITY_VOLTX_BATTERY_POWER,`. Do **not** add it to `SIM_ENTITY_IDS` (test-mode parity is a non-goal; the entities schema falls back to the production default).

- [ ] **Step 4: Add the options to the config flow**

In `config_flow.py`: add the four new `CONF_*` names and three `DEFAULT_*` names to the existing imports from `.const`. In `_params_schema`, immediately after the `CONF_SC_DISCHARGE_HANDOFF` field (the `vol.Required(CONF_SC_DISCHARGE_HANDOFF ...): selector.BooleanSelector(),` entry) insert:

```python
            vol.Required(CONF_SC_BATTERY_TOLERANCE, default=defaults.get(CONF_SC_BATTERY_TOLERANCE, DEFAULT_SC_BATTERY_TOLERANCE)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=2000, step=50, unit_of_measurement="W", mode=_NUM,
                )),
            vol.Required(CONF_SC_MIN_DWELL_SECONDS, default=defaults.get(CONF_SC_MIN_DWELL_SECONDS, DEFAULT_SC_MIN_DWELL_SECONDS)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=900, step=10, unit_of_measurement="s", mode=_NUM,
                )),
            vol.Required(CONF_SC_POWER_SMOOTHING_SECONDS, default=defaults.get(CONF_SC_POWER_SMOOTHING_SECONDS, DEFAULT_SC_POWER_SMOOTHING_SECONDS)):
                selector.NumberSelector(selector.NumberSelectorConfig(
                    min=0, max=300, step=10, unit_of_measurement="s", mode=_NUM,
                )),
```

In `_entities_schema`, right after the `CONF_ENTITY_VOLTX_WORK_MODE` line insert:

```python
            vol.Required(CONF_ENTITY_VOLTX_BATTERY_POWER, default=defaults.get(CONF_ENTITY_VOLTX_BATTERY_POWER, ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER])): _TEXT,
```

In the options flow `current_params` dict (right after the `CONF_SC_DISCHARGE_HANDOFF: self._current(...)` line) insert:

```python
            CONF_SC_BATTERY_TOLERANCE: self._current(CONF_SC_BATTERY_TOLERANCE, DEFAULT_SC_BATTERY_TOLERANCE),
            CONF_SC_MIN_DWELL_SECONDS: self._current(CONF_SC_MIN_DWELL_SECONDS, DEFAULT_SC_MIN_DWELL_SECONDS),
            CONF_SC_POWER_SMOOTHING_SECONDS: self._current(CONF_SC_POWER_SMOOTHING_SECONDS, DEFAULT_SC_POWER_SMOOTHING_SECONDS),
```

(The non-test-mode entities defaults loop already covers the new entity because it iterates `ENTITY_ID_DEFAULTS`.)

- [ ] **Step 5: Translations**

`en.json` round-trips byte-for-byte through `json.dumps(indent=2, ensure_ascii=False) + "\n"`, so patch it with this script (run from the repo root) rather than hand-editing four places:

```bash
python3 - <<'EOF'
import json
p = "custom_components/grid_coordinator/translations/en.json"
raw = open(p).read()
d = json.loads(raw)
assert json.dumps(d, indent=2, ensure_ascii=False) + "\n" == raw, "round-trip changed the file; edit by hand"

labels = {
    "sc_battery_tolerance": "Self-consumption battery tolerance (W)",
    "sc_min_dwell_seconds": "Self-consumption minimum dwell (s)",
    "sc_power_smoothing_seconds": "Self-consumption power smoothing (s)",
}
descs = {
    "sc_battery_tolerance": "When the grid target is near zero, hand off to native self-consumption unless a CHARGING plan is being undershot by more than this many watts (actual battery power minus the EMHASS setpoint). Absorbing more than planned never leaves the handoff, and idle/discharge plans always pass. 0 disables this rule and keeps the older behaviour. Default 300 W.",
    "sc_min_dwell_seconds": "After the handoff turns on or off it is locked for this long, so one noisy EMHASS republish cannot flip it and flip it straight back. After leaving because a charging plan was undershot, re-entry waits 5x this long. Safety conditions (grid limit breach, control switched off) ignore it. 0 disables. Default 120 s.",
    "sc_power_smoothing_seconds": "Time constant used to smooth the Voltx battery power before comparing it with the plan (raw power swings by over 1 kW within a minute during native self-consumption). 0 disables smoothing. Default 60 s.",
}
for flow, step in (("config", "user"), ("options", "init")):
    s = d[flow]["step"][step]
    s["data"].update(labels)
    s["data_description"].update(descs)
for flow in ("config", "options"):
    s = d[flow]["step"]["entities"]
    s["data"]["entity_voltx_battery_power"] = "Voltx battery power sensor (W, + = discharge)"
    s["data_description"]["entity_voltx_battery_power"] = "Used to see what the battery is actually doing during the self-consumption handoff, and to let Solax follow it. If unavailable the coordinator falls back to the older handoff rule and releases Solax."
open(p, "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
EOF
git diff --stat custom_components/grid_coordinator/translations/en.json
```

Expected: only `en.json` changed, additions only (`git diff` shows no removed lines).

- [ ] **Step 6: Coordinator option readers**

In `coordinator.py`, add to the `from .const import (` list: `CONF_ENTITY_VOLTX_BATTERY_POWER,`, `CONF_SC_BATTERY_TOLERANCE,`, `CONF_SC_MIN_DWELL_SECONDS,`, `CONF_SC_POWER_SMOOTHING_SECONDS,`, `DEFAULT_SC_BATTERY_TOLERANCE,`, `DEFAULT_SC_MIN_DWELL_SECONDS,`, `DEFAULT_SC_POWER_SMOOTHING_SECONDS,` (keep the list's existing ordering style). Immediately after the `_sc_discharge_handoff` property add:

```python
    @property
    def _sc_battery_tolerance(self) -> float:
        return float(self._opt(CONF_SC_BATTERY_TOLERANCE, DEFAULT_SC_BATTERY_TOLERANCE))

    @property
    def _sc_min_dwell_seconds(self) -> float:
        return float(self._opt(CONF_SC_MIN_DWELL_SECONDS, DEFAULT_SC_MIN_DWELL_SECONDS))

    @property
    def _sc_power_smoothing_seconds(self) -> float:
        return float(self._opt(CONF_SC_POWER_SMOOTHING_SECONDS, DEFAULT_SC_POWER_SMOOTHING_SECONDS))
```

(`CONF_ENTITY_VOLTX_BATTERY_POWER` is used in Task 4; importing it now keeps this task's diff self-contained — ruff may flag it unused until Task 4, which is fine.)

- [ ] **Step 7: Run tests and syntax-check**

Run: `python3 -m pytest tests -q && python3 -m py_compile custom_components/grid_coordinator/config_flow.py custom_components/grid_coordinator/coordinator.py`
Expected: all pass, no compile errors.

- [ ] **Step 8: Commit**

```bash
git add custom_components/grid_coordinator/const.py custom_components/grid_coordinator/config_flow.py custom_components/grid_coordinator/translations/en.json custom_components/grid_coordinator/coordinator.py tests/test_coordinator_flags.py tests/test_translations.py
git commit -m "feat: config options and entity for the self-consumption battery tolerance"
```

---

### Task 4: Coordinator integration

**Files:**
- Modify: `custom_components/grid_coordinator/coordinator.py`
- Test: `tests/test_coordinator_sc.py` (create)

**Interfaces:**
- Consumes: all of Task 1 (`ScState`, `ema_update`, `decide_self_consumption`, `compute_solax_follow`) and the Task 3 properties/constants.
- Produces: `GridCoordinator._read_voltx_power() -> float | None`; `._update_voltx_power_ema(raw: float | None) -> float | None`; `._solax_shares(*, solax_on, voltx_soc, mpc_batt, tier2_error) -> tuple[solax_soc, share, tier2_share, effective_share, effective_tier2_share]`; `async ._async_solax_follow_voltx(*, solax_on, voltx_power, voltx_soc, grid_actual) -> tuple[float, SolaxMode]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coordinator_sc.py`:

```python
"""Coordinator helpers for the self-consumption handoff (no running HA required)."""

import asyncio
import math
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_ENTITY_VOLTX_CAPACITY,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator
from custom_components.grid_coordinator.models import SolaxMode

POWER_ENTITY = ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_BATTERY_POWER]


class _States:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, entity_id: str):
        value = self._values.get(entity_id)
        return None if value is None else SimpleNamespace(state=value)


def _coord(values: dict[str, str] | None = None, **attrs):
    c = SimpleNamespace(
        hass=SimpleNamespace(states=_States(values or {})),
        _entry=SimpleNamespace(options={}, data={}),
        _voltx_power_ema=None,
        _voltx_power_ema_at=None,
        _sc_power_smoothing_seconds=60.0,
        **attrs,
    )
    c._opt = MethodType(GridCoordinator._opt, c)
    c._eid = MethodType(GridCoordinator._eid, c)
    c._read_voltx_power = MethodType(GridCoordinator._read_voltx_power, c)
    c._update_voltx_power_ema = MethodType(GridCoordinator._update_voltx_power_ema, c)
    return c


# ── Voltx power read + EMA ────────────────────────────────────────────────────

def test_read_voltx_power_numeric():
    assert _coord({POWER_ENTITY: "-2069"})._read_voltx_power() == -2069.0


@pytest.mark.parametrize("state", ["unavailable", "unknown", "", "not-a-number"])
def test_read_voltx_power_unreadable_is_none(state):
    assert _coord({POWER_ENTITY: state})._read_voltx_power() is None


def test_read_voltx_power_missing_entity_is_none():
    assert _coord({})._read_voltx_power() is None


def test_ema_seeds_then_smooths():
    c = _coord()
    assert c._update_voltx_power_ema(1000.0) == 1000.0
    second = c._update_voltx_power_ema(0.0)
    assert 0.0 < second <= 1000.0  # moved toward 0 but not all the way (tiny dt)


def test_ema_resets_when_sensor_unavailable():
    c = _coord()
    c._update_voltx_power_ema(500.0)
    assert c._update_voltx_power_ema(None) is None
    assert c._voltx_power_ema is None and c._voltx_power_ema_at is None
    # the next good reading re-seeds instead of blending with the stale value
    assert c._update_voltx_power_ema(-300.0) == -300.0


# ── Solax share extraction (regression: behaviour moved out of the tick) ──────

def _share_coord(values):
    c = _coord(values, _soc_balance_sensitivity=0.01, _soc_balance_deadband=5.0)
    c._solax_shares = MethodType(GridCoordinator._solax_shares, c)
    return c


def test_solax_shares_all_zero_when_solax_off():
    soc, s1, s2, e1, e2 = _share_coord({})._solax_shares(
        solax_on=False, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert math.isnan(soc)
    assert (s1, s2, e1, e2) == (0.0, 0.0, 0.0, 0.0)


def test_solax_shares_capacity_ratio_when_socs_balanced():
    values = {
        ENTITY_ID_DEFAULTS[CONF_ENTITY_VOLTX_CAPACITY]: "10",
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_CAPACITY]: "7",
        ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC]: "50",
    }
    soc, s1, s2, e1, e2 = _share_coord(values)._solax_shares(
        solax_on=True, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert soc == 50.0
    assert s1 == pytest.approx(7 / 17)
    assert s2 == pytest.approx(7 / 17)
    # far below the SOC-ceiling taper band, so effective == raw
    assert (e1, e2) == (pytest.approx(s1), pytest.approx(s2))


def test_solax_shares_zero_when_capacities_missing():
    values = {ENTITY_ID_DEFAULTS[CONF_ENTITY_SOLAX_SOC]: "50"}
    _soc, s1, s2, e1, e2 = _share_coord(values)._solax_shares(
        solax_on=True, voltx_soc=50.0, mpc_batt=-1000.0, tier2_error=-500.0
    )
    assert (s1, s2, e1, e2) == (0.0, 0.0, 0.0, 0.0)


# ── follow: fallback paths release Solax instead of commanding it ─────────────

def _follow(solax_enabled, solax_active, **kwargs):
    release = AsyncMock()
    c = SimpleNamespace(
        _solax_enabled=lambda: solax_enabled,
        _solax_active=solax_active,
        _async_enter_solax_self_consumption=release,
    )
    result = asyncio.run(GridCoordinator._async_solax_follow_voltx(c, **kwargs))
    return result, release


def test_follow_releases_solax_when_voltx_power_unreadable():
    result, release = _follow(True, True, solax_on=True, voltx_power=None, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_awaited_once()


def test_follow_releases_solax_when_its_control_is_off():
    result, release = _follow(True, True, solax_on=False, voltx_power=-1500.0, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_awaited_once()


def test_follow_does_not_touch_an_inactive_solax_when_unavailable():
    result, release = _follow(True, False, solax_on=True, voltx_power=None, voltx_soc=50.0, grid_actual=0.0)
    assert result == (0.0, SolaxMode.SELF_CONSUMPTION)
    release.assert_not_awaited()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_coordinator_sc.py -q`
Expected: FAIL — `AttributeError: type object 'GridCoordinator' has no attribute '_read_voltx_power'`.

- [ ] **Step 3: Imports and state**

In `coordinator.py`:

1. After `import asyncio` add `import time`.
2. Replace the budget import block with:

```python
from .budget import (
    SOLAX_RESIDUAL_MODES,
    ScState,
    build_coordinator_data,
    cap_combined_charge,
    compute_ev_current_limit,
    compute_solax_command,
    compute_solax_follow,
    compute_solax_share,
    compute_solax_tier1,
    compute_voltx_command,
    decide_self_consumption,
    ema_update,
)
```

(`should_hold_self_consumption` is no longer used by the coordinator; it stays in `budget.py` for the fallback and the tests.)

3. In `__init__`, replace

```python
        # Self-consumption deadband hysteresis state (see should_hold_self_consumption)
        self._self_consumption_active: bool = False
```

with

```python
        # Self-consumption handoff state (see decide_self_consumption): active flag, last
        # transition time and lockout length, plus the smoothed Voltx power it consumes.
        self._sc_state = ScState()
        self._voltx_power_ema: float | None = None
        self._voltx_power_ema_at: float | None = None
```

- [ ] **Step 4: Add the helper methods**

Insert immediately before `    @property` / `    def _import_limit(self) -> float:`:

```python
    def _read_voltx_power(self) -> float | None:
        """Raw Voltx battery power in W (+ = discharge), or None if unreadable."""
        state = self.hass.states.get(self._eid(CONF_ENTITY_VOLTX_BATTERY_POWER))
        if state is None or state.state in ("unavailable", "unknown", ""):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _update_voltx_power_ema(self, raw: float | None) -> float | None:
        """Update and return the smoothed Voltx power; reset (None) when unreadable.

        Resetting on a bad reading means a stale average never drives the handoff
        decision once the sensor recovers.
        """
        if raw is None:
            self._voltx_power_ema = None
            self._voltx_power_ema_at = None
            return None
        now = time.monotonic()
        dt = 0.0 if self._voltx_power_ema_at is None else now - self._voltx_power_ema_at
        self._voltx_power_ema = ema_update(
            self._voltx_power_ema, raw, dt, self._sc_power_smoothing_seconds
        )
        self._voltx_power_ema_at = now
        return self._voltx_power_ema

    def _solax_shares(
        self,
        *,
        solax_on: bool,
        voltx_soc: float,
        mpc_batt: float,
        tier2_error: float,
    ) -> tuple[float, float, float, float, float]:
        """SOC-balance Solax shares for both tiers.

        Returns (solax_soc, solax_share, solax_tier2_share, effective_solax_share,
        effective_solax_tier2_share); the effective values are tapered to zero as
        Solax approaches its SOC ceiling.  `mpc_batt` keys the tier-1 share's
        direction, `tier2_error` the tier-2 share's.  Extracted unchanged from the
        main tick so the handoff branch can size Voltx's setpoint the same way.
        """
        hass = self.hass
        solax_soc = float("nan")
        solax_share = 0.0
        solax_tier2_share = 0.0
        if solax_on:
            solax_soc = _float(hass, self._eid(CONF_ENTITY_SOLAX_SOC), 50.0)
            voltx_cap = _float(hass, self._eid(CONF_ENTITY_VOLTX_CAPACITY), 0.0)
            solax_cap = _float(hass, self._eid(CONF_ENTITY_SOLAX_CAPACITY), 0.0)
            if voltx_cap > 0 and solax_cap > 0:
                solax_share = compute_solax_share(
                    voltx_soc=voltx_soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=mpc_batt,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
                solax_tier2_share = compute_solax_share(
                    voltx_soc=voltx_soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=tier2_error,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
        if solax_share > 0.0 or solax_tier2_share > 0.0:
            _s_soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            _taper = min(1.0, max(0.0, _s_soc_max - solax_soc) / DEFAULT_SOLAX_TIER1_SOC_TAPER_BAND)
        else:
            _taper = 1.0
        return (
            solax_soc,
            solax_share,
            solax_tier2_share,
            solax_share * _taper,
            solax_tier2_share * _taper,
        )

    async def _async_solax_follow_voltx(
        self,
        *,
        solax_on: bool,
        voltx_power: float | None,
        voltx_soc: float,
        grid_actual: float,
    ) -> tuple[float, SolaxMode]:
        """Command Solax as a share of Voltx's actual power during the native handoff.

        Solax's own native self-consumption mode does not work, so during the handoff
        it must keep being commanded.  Uses the RAW Voltx reading (the smoothed one
        lagged a cloud cycle and left Solax charging against a collapsed surplus).
        Falls back to releasing it (the previous behaviour) when Solax control is off
        or the Voltx power reading is unavailable — never command from a stale value.
        """
        if not solax_on or voltx_power is None:
            if self._solax_enabled() and self._solax_active:
                await self._async_enter_solax_self_consumption()
            return 0.0, SolaxMode.SELF_CONSUMPTION
        hass = self.hass
        # Share is keyed off the direction Voltx is actually moving (not the plan).
        solax_soc, _, _, share, _ = self._solax_shares(
            solax_on=True, voltx_soc=voltx_soc, mpc_batt=voltx_power, tier2_error=0.0
        )
        solax_cmd, solax_mode = compute_solax_follow(
            voltx_power=voltx_power,
            share=share,
            solax_soc=solax_soc,
            solax_soc_min=_float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MIN), 20.0),
            solax_soc_max=_float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0),
            solax_max_charge=float(self._opt(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE)),
            solax_max_discharge=float(self._opt(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE)),
            grid_actual=grid_actual,
            import_limit=self._import_limit,
            export_limit=self._export_limit,
            prev_solax_cmd=self._solax_last_written_cmd,
        )
        solax_zero_deadband = float(self._opt(CONF_SOLAX_ZERO_DEADBAND, DEFAULT_SOLAX_ZERO_DEADBAND))
        if solax_zero_deadband > 0 and abs(solax_cmd) <= solax_zero_deadband:
            solax_cmd, solax_mode = 0.0, SolaxMode.SELF_CONSUMPTION
        await self._async_write_solax(solax_cmd)
        return solax_cmd, solax_mode

```

- [ ] **Step 5: Run the unit tests**

Run: `python3 -m pytest tests -q`
Expected: all pass (the new helpers exist; the tick itself is not yet rewired).

- [ ] **Step 6: Use the extracted share helper in the main tick**

Replace this block in `_async_update_data` (from `solax_soc = float("nan")` through the taper lines; keep the long explanatory comment above it):

```python
        solax_soc = float("nan")
        solax_share = 0.0
        solax_tier2_share = 0.0
        if solax_on:
            solax_soc = _float(hass, self._eid(CONF_ENTITY_SOLAX_SOC), 50.0)
            voltx_cap = _float(hass, self._eid(CONF_ENTITY_VOLTX_CAPACITY), 0.0)
            solax_cap = _float(hass, self._eid(CONF_ENTITY_SOLAX_CAPACITY), 0.0)
            if voltx_cap > 0 and solax_cap > 0:
                solax_share = compute_solax_share(
                    voltx_soc=soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=effective_mpc_batt,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
                solax_tier2_share = compute_solax_share(
                    voltx_soc=soc,
                    solax_soc=solax_soc,
                    voltx_capacity_kwh=voltx_cap,
                    solax_capacity_kwh=solax_cap,
                    cmd=grid_track - effective_target,
                    sensitivity=self._soc_balance_sensitivity,
                    soc_deadband=self._soc_balance_deadband,
                )
        if solax_share > 0.0 or solax_tier2_share > 0.0:
            _s_soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            _taper = min(1.0, max(0.0, _s_soc_max - solax_soc) / DEFAULT_SOLAX_TIER1_SOC_TAPER_BAND)
        else:
            _taper = 1.0
        effective_solax_share = solax_share * _taper
        effective_solax_tier2_share = solax_tier2_share * _taper
```

with:

```python
        (
            solax_soc,
            solax_share,
            solax_tier2_share,
            effective_solax_share,
            effective_solax_tier2_share,
        ) = self._solax_shares(
            solax_on=solax_on,
            voltx_soc=soc,
            mpc_batt=effective_mpc_batt,
            tier2_error=grid_track - effective_target,
        )
```

Run `python3 -m pytest tests -q` (still green; `compute_solax_share` is still imported and used inside `_solax_shares`).

- [ ] **Step 7: Remove the late reads that move above the handoff decision**

The handoff branch now needs the Voltx SOC, the per-battery control flags and the Voltx power (Step 8 reads them). In `_async_update_data`:

(a) Delete the later line `        soc = _float(hass, entity_soc, 50.0)` (leave `soc_min` etc. and the `# ── read battery / inverter state` comment).

(b) Replace

```python
        # ── per-battery control switches ───────────────────────────────────
        # Each battery's control can be turned off with an optional binary helper
        # (blank helper → control on by default).  A battery whose control is off is
        # released to native self-consumption and never commanded this tick.
        voltx_control = self._control_enabled(hass, CONF_ENTITY_VOLTX_CONTROL_ENABLE)
        solax_on = self._solax_enabled() and self._control_enabled(
            hass, CONF_ENTITY_SOLAX_CONTROL_ENABLE
        )
        if not voltx_control:
```

with

```python
        # ── per-battery control switches ───────────────────────────────────
        # voltx_control / solax_on are read above, before the self-consumption decision.
        # A battery whose control is off is released to native self-consumption and never
        # commanded this tick.
        if not voltx_control:
```

- [ ] **Step 8: Rewrite the handoff branch**

Replace the block from `        effective_target = grid_target if not plan_is_stale else 0.0` through the end of the `if self._self_consumption_active:` block (its final `)` closing `return build_coordinator_data(...)`) — i.e. everything between the `# ── self-consumption deadband check` comment block (keep that comment block) and `        # ── read battery / inverter state` — with:

```python
        effective_target = grid_target if not plan_is_stale else 0.0
        effective_mpc_batt = mpc_batt_cmd if not plan_is_stale else 0.0

        # Inputs the handoff decision needs, read before it (they used to be read later).
        voltx_control = self._control_enabled(hass, CONF_ENTITY_VOLTX_CONTROL_ENABLE)
        solax_on = self._solax_enabled() and self._control_enabled(
            hass, CONF_ENTITY_SOLAX_CONTROL_ENABLE
        )
        soc = _float(hass, entity_soc, 50.0)
        voltx_power_raw = self._read_voltx_power()
        voltx_power_smoothed = self._update_voltx_power_ema(voltx_power_raw)
        # The battery clause compares Voltx's actual power with Voltx's OWN setpoint,
        # i.e. the plan after Solax's SOC-balance share is taken off.
        _, _, _, sc_solax_share, _ = self._solax_shares(
            solax_on=solax_on,
            voltx_soc=soc,
            mpc_batt=effective_mpc_batt,
            tier2_error=grid_track - effective_target,
        )
        voltx_setpoint = effective_mpc_batt * (1.0 - sc_solax_share)
        # Safety conditions bypass the dwell: a limit breach or Voltx control switched off
        # must leave the handoff immediately (the handoff branch has no grid clamp).
        limit_breach = grid_actual > self._import_limit or grid_actual < -self._export_limit
        was_active = self._sc_state.active
        self._sc_state = decide_self_consumption(
            state=self._sc_state,
            now=time.monotonic(),
            effective_target=effective_target,
            voltx_setpoint=voltx_setpoint,
            smoothed_voltx_power=voltx_power_smoothed,
            deadband=self._self_consumption_deadband,
            tolerance=self._sc_battery_tolerance,
            min_dwell_s=self._sc_min_dwell_seconds,
            allow_discharge_plan=self._sc_discharge_handoff,
            force_exit=limit_breach or not voltx_control,
        )
        if self._sc_state.active != was_active:
            LOGGER.debug(
                "self-consumption handoff %s (target=%.0fW setpoint=%.0fW power=%s smoothed=%s)",
                "ON" if self._sc_state.active else "OFF",
                effective_target,
                voltx_setpoint,
                voltx_power_raw,
                None if voltx_power_smoothed is None else round(voltx_power_smoothed),
            )
        if self._sc_state.active:
            await self._async_enter_self_consumption()
            # Solax's native mode does not work: keep commanding it as a share of Voltx.
            solax_cmd, solax_mode = await self._async_solax_follow_voltx(
                solax_on=solax_on,
                voltx_power=voltx_power_raw,
                voltx_soc=soc,
                grid_actual=grid_actual,
            )
            await self._async_release_ev_throttle()
            self._prev_cmd = 0.0
            sc_mode = CoordinatorMode.STALE_PLAN if plan_is_stale else CoordinatorMode.SELF_CONSUMPTION
            LOGGER.debug(
                "tick | grid=%.0fW (age=%.0fs) target=%.0fW mpc_batt=%.0fW mode=%s "
                "plan_age=%.1fmin (self-consumption) | voltx_setpoint=%.0fW power=%s "
                "smoothed=%s d=%s solax cmd=%.0fW mode=%s",
                grid_actual, grid_age_s, grid_target, mpc_batt_cmd, sc_mode, plan_age,
                voltx_setpoint, voltx_power_raw,
                None if voltx_power_smoothed is None else round(voltx_power_smoothed),
                None if voltx_power_smoothed is None else round(voltx_power_smoothed - voltx_setpoint),
                solax_cmd, solax_mode,
            )
            return build_coordinator_data(
                mode=sc_mode,
                grid_actual=grid_actual,
                grid_target=effective_target,
                voltx_command=0.0,
                import_limit=self._import_limit,
                export_limit=self._export_limit,
                plan_age_minutes=plan_age,
                override_mode=None,
                solax_command=solax_cmd,
                solax_mode=solax_mode,
            )
```

Also delete the two stale comment lines that described the old call (`# The sc_discharge_handoff option lets a discharge setpoint ...` / `# the load at a ~0W target) through...`) if they remain, and in the kept deadband comment block change `should_hold_self_consumption applies hysteresis once active` to `decide_self_consumption applies hysteresis once active`.

- [ ] **Step 9: Verify**

Run: `python3 -m pytest tests -q && python3 -m py_compile custom_components/grid_coordinator/coordinator.py`
Expected: all pass. Then `grep -n "_self_consumption_active\|should_hold_self_consumption" custom_components/grid_coordinator/coordinator.py` — expected: no matches.

Review the diff by eye for these coordinator-ordering risks (they cannot be unit-tested without HA): (1) `voltx_control`/`solax_on` are now defined before the handoff code and the later `if not voltx_control:` branch still sees them; (2) with Voltx control off, `force_exit` keeps the handoff off so `_async_voltx_disabled_tick` runs (previously the handoff could hijack it); (3) `soc` is defined before first use and not read twice; (4) `plan_age`, `grid_track`, `grid_age_s` are all defined above the branch.

- [ ] **Step 10: Commit**

```bash
git add custom_components/grid_coordinator/coordinator.py tests/test_coordinator_sc.py
git commit -m "feat: battery-tracking self-consumption handoff with Solax following Voltx"
```

---

### Task 5: Docs and final verification

**Files:**
- Modify: `docs/configuration.md`
- (Local only, not committed) `CLAUDE.md`

- [ ] **Step 1: Document the options**

In `docs/configuration.md`, in the `#### self_consumption_deadband (default: 50 W)` section, replace the paragraph beginning "When both `|grid_target|` and `|mpc_batt_cmd|` are within this threshold of zero" with the first paragraph below, and add the new subsection after the rest of that section (before `#### tracking_deadband`):

```markdown
When `|grid_target|` is within this threshold of zero, the coordinator hands control to the Voltx inverter's native self-consumption firmware and reports `self_consumption` mode (see the battery tolerance below for the battery condition). This also fires when the plan is stale (both targets are forced to zero first).

#### Self-consumption handoff tuning

**`sc_battery_tolerance`** (default 300 W; 0 = off, keeps the older rule). On a *charging* plan (EMHASS battery setpoint below zero) the handoff is held unless the smoothed actual Voltx battery power falls short of the Voltx setpoint by more than this many watts. Absorbing more than planned never leaves the handoff, and idle or discharge plans always pass (at a ~0 W grid target native self-consumption just covers the load). Needs `entity_voltx_battery_power` (default `sensor.voltx_battery_battery_power`, positive = discharge); if that sensor is unavailable the older rule (and `sc_discharge_handoff`) applies.

**`sc_min_dwell_seconds`** (default 120 s). After the handoff turns on or off it is locked for this long, so one noisy EMHASS republish cannot flip it and flip it straight back. After leaving because a charging plan was undershot, re-entry waits 5x this long. A grid limit breach or Voltx control being switched off bypasses the lock.

**`sc_power_smoothing_seconds`** (default 60 s). Time constant for smoothing the Voltx battery power before it is compared with the plan; raw power swings by over 1 kW within a minute during native self-consumption. 0 disables smoothing.

**Solax during the handoff.** Solax's own native self-consumption mode does not work, so while Voltx is handed off the coordinator keeps commanding Solax as a share of Voltx's actual power (`solax_mode` = `follow_voltx`), using the same SOC-balance share as normal tracking and the existing grid-safety, SOC and inverter limits. It always has the same sign as Voltx. If the Voltx power sensor is unavailable, or Solax control is off, Solax is released as before.
```

- [ ] **Step 2: Final automated checks**

Run: `python3 -m pytest tests -q` — expected: all pass.
Run `python3 -m ruff check` on the touched source files (`budget.py coordinator.py config_flow.py const.py models.py`) before and after your changes (`git stash` to compare) — expected: no *new* finding categories (the baseline already has many); fix any you introduced. Do not run repo-wide `--fix`.

- [ ] **Step 3: Devcontainer smoke test (cannot be done on the bare interpreter)**

Inside the devcontainer: `scripts/develop` (full restart). In the HA UI: Settings → Integrations → Grid Coordinator → Configure; confirm the three new options and the Voltx battery power entity appear with defaults and the integration reloads without errors. Enable debug logging for `custom_components.grid_coordinator` and confirm tick lines in the handoff include `voltx_setpoint=… power=… smoothed=… d=…`, and that transitions log `self-consumption handoff ON/OFF`. With the Voltx control helper switched off while in the handoff, confirm the handoff exits on the next tick and `_async_voltx_disabled_tick` takes over.

- [ ] **Step 4: Update CLAUDE.md locally (not committed — it is git-ignored)**

Add to the Architecture section: the handoff is `decide_self_consumption` (budget.py; charge-plan-only battery clause, dwell with a 5× shortfall back-off, force-exit for safety); Solax follows Voltx via `compute_solax_follow` because its native mode is non-functional; `tests/sim/` is the closed-loop harness (`python3 -m pytest tests/sim`).

- [ ] **Step 5: Commit docs**

```bash
git add docs/configuration.md
git commit -m "docs: self-consumption handoff tuning options and Solax follow"
```

---

## Live soak (after merge; the sim cannot cover this)

The harness models the plant, not real Voltx firmware reaction time, Modbus latency or work-mode write behaviour. Before treating the issues as closed, run with debug logging for at least one sunny/cloudy day and check: (1) `sensor.grid_coordinator_mode` flips per hour versus the 07:10–07:50 baseline; (2) export during low feed-in periods with `grid_target = 0` and a charge plan; (3) Voltx and Solax power never held opposite in sign for more than a few ticks (`solax_mode = follow_voltx` periods); (4) the persistent-shortfall cycle (a ≈12-minute enter/exit period is expected when a cloudy day undershoots a charging plan; raise `sc_battery_tolerance` or set it to 0 if it bothers you). Consider writing this up with the `writing-verification-guides` skill.

## Follow-ups (not in this plan)

- If SOC drift ahead of plan is observed because a discharging battery no longer forces a handoff exit, raise an ADO issue in the `home-assistant` project for an SOC-deviation guard (decision recorded in the spec; deliberately not filed now).
- Confirm the real `self_consumption_deadband` value (the replay assumes 200 W).

## Self-Review

- **Spec coverage:** decision rule, charge-only clause, hysteresis, dwell, bypass and legacy fallback → Task 1 and Task 4 steps 7–8; smoothing → Task 1 (`ema_update`) and Task 4; config table and entity → Task 3; diagnostics (d and handoff transitions in the debug log) → Task 4 step 8; Solax follow, `FOLLOW_VOLTX` and its reporting rules → Task 1 and Task 4; simulation harness, replay, cloud cycling, shortfall back-off, SOC ceiling and the no-follow baseline → Task 2; "what the harness cannot show" → Live soak.
- **Spec details changed by the replay/sim findings** (the shortfall re-entry back-off, raw-power Solax follow, and the 2–3 tick opposition criterion) have been written back to the spec.
- **Placeholder scan:** none; every code step carries its code.
- **Type consistency:** `ScState`, `decide_self_consumption`, `compute_solax_follow` and `ema_update` signatures are identical across the Task 1 tests, the Task 2 driver and the Task 4 coordinator calls; `_solax_shares` returns a 5-tuple consistently in its definition, tests and both call sites.
