# Price Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During forecast price spikes, gridcoordinator decides every 10 s tick whether to export at full power, hold, or charge, using both batteries, keeps enough energy to run the house, and reports the reason for every decision.

**Architecture:** A new pure module, `price_window.py`, parses Amber Express's `detailedForecast` and decides `export` / `hold` / `charge` with a reason. The coordinator evaluates it at the top of every tick and reports it; when it is active and not in shadow mode, its grid target and combined battery setpoint replace the EMHASS ones and the existing tracking path executes them. No new inverter write paths.

**Tech Stack:** Python 3.14, Home Assistant custom integration (`DataUpdateCoordinator`), pytest. Bare-interpreter unit tests (`python3 -m pytest tests/`) run against the stubs in `tests/conftest.py`; real-Home-Assistant smoke tests run in Docker (`scripts/ha_smoke/run`).

**Spec:** `docs/superpowers/specs/2026-10-12-price-window-design.md` (read it first; this plan argues from it).

## Global Constraints

- Repo: `~/Projects/gridcoordinator`, branch `feat/price-window` (already exists, holds the spec). Work reaches `main` by pull request. Do not push, open the PR, merge or tag without asking Stuart first.
- Amber Express is the only price source. Prices are $/kWh and positive on both channels (feed-in is a positive earning).
- `price_window.py` has no Home Assistant imports. `parse_amber_express` is the only code that knows Amber Express field names.
- Tick order: disabled gate, manual override, price window, EMHASS tracking. The manual override and the disabled gate always win.
- No new inverter write paths: a price window decision only replaces the grid target and battery setpoint that the existing tracking path executes.
- Every price window setting is stored as text and accepts a literal or a helper entity ID. A blank or unreadable setting falls back to its default.
- Defaults, verbatim from the spec: spike threshold 1.00 $/kWh, lookahead 4 h, reserve margin 2 h, reserve-until unset, minimum sell price 0.30 $/kWh, charge ratio 1.5, maximum charge buy price unset, round-trip efficiency 0.85, stale cut-off 10 minutes.
- The feature is off unless the enable entity is on and both Amber Express entities and the house load entity are configured. Shadow is on unless the shadow entity reads exactly `off`.
- Reason codes, verbatim: `disabled`, `no_window`, `data_stale`, `export_allocated`, `export_unconfirmed`, `hold_below_min_price`, `hold_outranked`, `hold_no_surplus`, `hold_charge_too_dear`, `hold_soc_ceiling`, `charge_shortfall`.
- Sign conventions (unchanged): grid power + = import; battery command + = discharge.
- Commit messages end with the two trailer lines shown in each commit step.
- Tests use a private event loop (`asyncio.new_event_loop()`), never `asyncio.run()`: see the comment in `tests/test_coordinator_override.py`.
- Every code block in this plan was run in a throwaway copy of the repo on 2026-10-13: 235 passed on the bare interpreter and 30 smoke tests on real Home Assistant 2026.3.2. If a step's expected result differs, stop and find out why before going on.

## Review Focus

Conditions the spec implies that are most likely to bite in use. Each has a test in the task that owns the code.

1. **Amber Express starts every interval one second late** (`02:00:01` to `02:05:00`). At `02:00:00` no interval would cover now and the price window would report stale data for a second every five minutes. Expected: covered. Test: `test_parser_covers_the_first_second_of_an_interval` (Task 1).
2. **A confirmed spike price outranked by intervals that merely could be higher.** With energy tight, $10.48 in hand must not be held for intervals predicted at 60c whose range tops out at $12. Expected: sold. Tests: `test_a_confirmed_spike_is_not_held_back_for_one_that_merely_could_be_higher`, `test_replay_with_a_small_battery_never_sells_cheap_while_spikes_are_possible` (Task 2).
3. **Charging on an unconfirmed buy price.** For the first 20 to 40 s of an interval the buy price is an estimate that could turn out to be a spike. Expected: no charging until it is confirmed or its high bound passes the rule. Test: `test_unconfirmed_buy_price_is_judged_on_its_high_bound` (Task 2).
4. **A spike arriving straight after a hold.** `hold` uses the self-consumption handoff, which has a 120 s minimum dwell; a 5-minute spike cannot wait for it, nor for the 1500 W-per-tick ramp. Expected: full export on the first tick. Test: `test_spike_after_hold_exports_at_once_despite_the_handoff_dwell` (Task 6).
5. **Hold while the EV is charging.** The existing EV rule turns tier 2 off and blocks the handoff, which would leave the whole house on the grid at spike prices. Expected: the batteries carry the house; only the car stays on the grid. Test: `test_hold_while_the_ev_charges_keeps_the_house_off_the_grid` (Task 6).

Also pinned: a setting's helper entity being unavailable (Task 5), a field cleared on the options form (Task 7), and garbage in `detailedForecast` (Tasks 1 and 5).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `custom_components/grid_coordinator/price_window.py` | create | Parse Amber Express; decide export / hold / charge with a reason; turn an action into setpoints. Pure. |
| `custom_components/grid_coordinator/const.py` | modify | Price window config keys, defaults, `PRICE_WINDOW_FIELDS`. |
| `custom_components/grid_coordinator/models.py` | modify | Three `CoordinatorMode` values; `CoordinatorData.price_window`. |
| `custom_components/grid_coordinator/coordinator.py` | modify | Read settings and battery state, evaluate each tick, apply the decision in the tick, report it. |
| `custom_components/grid_coordinator/sensor.py` | modify | Four sensors; attributes on the reason sensor. |
| `custom_components/grid_coordinator/config_flow.py` | modify | Fourth step, "Price Window", in the config and options flows. |
| `custom_components/grid_coordinator/translations/en.json` | modify | Labels and descriptions for that step. |
| `tests/test_price_window.py` | create | Parser, decision, reason, replay of 2026-10-12, setpoints. |
| `tests/test_coordinator_price_window.py` | create | The tick with a real `GridCoordinator` on fake states. |
| `tests/test_translations.py` | modify | Every price window field is labelled. |
| `scripts/ha_smoke/test_smoke_ha.py` | modify | Price window on real Home Assistant: sensors, shadow, options flow. |
| `README.md`, `CLAUDE.md`, `scripts/ha_smoke/README.md` | modify | Document the feature. |

All commands run from `~/Projects/gridcoordinator`.

---

### Task 1: Amber Express parser

**Files:**
- Create: `custom_components/grid_coordinator/price_window.py`
- Test: `tests/test_price_window.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PriceInterval(start, end, sell, sell_high, buy, buy_high, confirmed, flagged)`; `PriceWindowSettings`, `PriceWindowEnergy(usable_kwh, house_load_w, at_soc_ceiling)`, `PriceWindowLimits(export_w, charge_w)`, `IntervalRow`, `PriceWindowDecision`; `inactive(code, sentence) -> PriceWindowDecision`; `parse_amber_express(feed_in_attrs, general_attrs, now) -> list[PriceInterval]`; the `ACTION_*` and `CODE_*` constants.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_price_window.py`:

```python
"""Price window: Amber Express parsing, the decision and its reason, the setpoints."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.grid_coordinator.price_window import (
    PriceWindowLimits,
    PriceWindowSettings,
    parse_amber_express,
)

SYD = ZoneInfo("Australia/Sydney")
NOW = datetime(2026, 10, 12, 15, 30, 40, tzinfo=SYD)
LIMITS = PriceWindowLimits(export_w=10000.0, charge_w=12000.0)
SETTINGS = PriceWindowSettings()


# ── parser ────────────────────────────────────────────────────────────────────


def _entry(start, minutes, per_kwh, *, estimate=True, high=None, spike="none"):
    """One detailedForecast entry the way Amber Express writes it (UTC, start +1 s)."""
    begin = start.astimezone(ZoneInfo("UTC"))
    entry = {
        "per_kwh": per_kwh,
        "duration": minutes,
        "start_time": (begin + timedelta(seconds=1)).isoformat(),
        "end_time": (begin + timedelta(minutes=minutes)).isoformat(),
        "spike_status": spike,
        "descriptor": "neutral",
        "estimate": estimate,
    }
    if estimate:
        entry["advanced_price_predicted"] = {
            "low": per_kwh / 2,
            "predicted": per_kwh,
            "high": per_kwh * 2 if high is None else high,
        }
    return entry


def _attrs(*entries):
    return {"detailedForecast": list(entries)}


T0 = datetime(2026, 10, 12, 15, 30, tzinfo=SYD)


def test_parser_joins_channels_and_reads_both_interval_lengths():
    feed = _attrs(
        _entry(T0, 5, 10.48, estimate=False),
        _entry(T0 + timedelta(minutes=5), 5, 0.60, high=12.0, spike="potential"),
        _entry(T0 + timedelta(minutes=10), 30, 0.25),
    )
    general = _attrs(
        _entry(T0, 5, 11.60, estimate=False),
        _entry(T0 + timedelta(minutes=5), 5, 0.71, high=13.2),
        _entry(T0 + timedelta(minutes=10), 30, 0.36),
    )
    intervals = parse_amber_express(feed, general, NOW)
    assert [(i.end - i.start).total_seconds() / 60 for i in intervals] == [5, 5, 30]
    first, second, third = intervals
    assert first.start == T0  # the +1 s is rounded away
    assert (first.sell, first.sell_high, first.buy, first.buy_high) == (10.48, 10.48, 11.60, 11.60)
    assert first.confirmed and not first.flagged
    assert (second.sell, second.sell_high, second.buy_high) == (0.60, 12.0, 13.2)
    assert second.flagged and not second.confirmed
    assert third.sell_high == 0.50


def test_parser_covers_the_first_second_of_an_interval():
    # Amber Express writes 15:30:01 as the start; 15:30:00 must still be covered.
    feed = _attrs(_entry(T0, 5, 0.30, estimate=False))
    general = _attrs(_entry(T0, 5, 0.41, estimate=False))
    assert len(parse_amber_express(feed, general, T0)) == 1


def test_parser_without_advanced_prediction_uses_the_price_as_its_high():
    entry = _entry(T0, 5, 0.30)
    del entry["advanced_price_predicted"]
    general = _entry(T0, 5, 0.41)
    del general["advanced_price_predicted"]
    (interval,) = parse_amber_express(_attrs(entry), _attrs(general), NOW)
    assert (interval.sell_high, interval.buy_high) == (0.30, 0.41)


def test_parser_flag_on_the_general_channel_counts():
    feed = _attrs(_entry(T0, 5, 0.30))
    general = _attrs(_entry(T0, 5, 0.41, spike="spike"))
    assert parse_amber_express(feed, general, NOW)[0].flagged


def test_parser_skips_unparseable_and_unmatched_entries():
    bad_time = _entry(T0 + timedelta(minutes=5), 5, 0.30)
    bad_time["start_time"] = "not a time"
    bad_price = _entry(T0 + timedelta(minutes=10), 5, 0.30)
    bad_price["per_kwh"] = None
    lonely = _entry(T0 + timedelta(minutes=15), 5, 0.30)  # no general entry
    naive = _entry(T0 + timedelta(minutes=20), 5, 0.30)
    naive["start_time"] = "2026-10-12T15:50:01"  # no timezone
    feed = _attrs(_entry(T0, 5, 0.30), bad_time, bad_price, lonely, naive, "junk", None)
    general = _attrs(
        _entry(T0, 5, 0.41),
        _entry(T0 + timedelta(minutes=5), 5, 0.41),
        _entry(T0 + timedelta(minutes=10), 5, 0.41),
        _entry(T0 + timedelta(minutes=20), 5, 0.41),
    )
    assert [i.start for i in parse_amber_express(feed, general, NOW)] == [T0]


def test_parser_drops_ended_intervals():
    feed = _attrs(_entry(T0 - timedelta(minutes=5), 5, 9.0), _entry(T0, 5, 0.30))
    general = _attrs(_entry(T0 - timedelta(minutes=5), 5, 9.9), _entry(T0, 5, 0.41))
    assert [i.start for i in parse_amber_express(feed, general, NOW)] == [T0]


@pytest.mark.parametrize(
    "feed, general",
    [
        ({}, {}),
        (None, None),
        ({"detailedForecast": "oops"}, {"detailedForecast": None}),
        (  # the forecast starts in the future: nothing covers now
            _attrs(_entry(T0 + timedelta(minutes=5), 5, 0.30)),
            _attrs(_entry(T0 + timedelta(minutes=5), 5, 0.41)),
        ),
    ],
)
def test_parser_returns_empty_when_nothing_covers_now(feed, general):
    assert parse_amber_express(feed, general, NOW) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'custom_components.grid_coordinator.price_window'`.

- [ ] **Step 3: Write the module**

Create `custom_components/grid_coordinator/price_window.py`. The dataclasses and constants that Tasks 2 and 3 use are defined here so the types live in one place.

```python
"""Price window: what to do during forecast price spikes.

Pure functions, no Home Assistant imports.  `parse_amber_express` is the only code
that knows Amber Express field names; `decide_price_window` turns the intervals plus
the batteries' energy into export / hold / charge with a reason; `window_setpoints`
turns that action into the grid target and combined battery setpoint the existing
tracking path executes.

Prices are $/kWh, positive (Amber Express reports feed-in as a positive earning).
Energy is kWh at the battery; power is W.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time

ACTION_EXPORT = "export"
ACTION_HOLD = "hold"
ACTION_CHARGE = "charge"

CODE_DISABLED = "disabled"
CODE_NO_WINDOW = "no_window"
CODE_DATA_STALE = "data_stale"
CODE_EXPORT_ALLOCATED = "export_allocated"
CODE_EXPORT_UNCONFIRMED = "export_unconfirmed"
CODE_HOLD_BELOW_MIN_PRICE = "hold_below_min_price"
CODE_HOLD_OUTRANKED = "hold_outranked"
CODE_HOLD_NO_SURPLUS = "hold_no_surplus"
CODE_HOLD_CHARGE_TOO_DEAR = "hold_charge_too_dear"
CODE_HOLD_SOC_CEILING = "hold_soc_ceiling"
CODE_CHARGE_SHORTFALL = "charge_shortfall"

_FLAGGED_STATUSES = ("potential", "spike")
_EPS_KWH = 0.001


@dataclass(frozen=True)
class PriceInterval:
    """One Amber Express interval, both channels joined."""

    start: datetime
    end: datetime
    sell: float        # feed-in price: actual once confirmed, predicted before
    sell_high: float   # top of the feed-in forecast range (== sell once confirmed)
    buy: float         # general price: actual once confirmed, predicted before
    buy_high: float    # top of the general forecast range (== buy once confirmed)
    confirmed: bool
    flagged: bool      # Amber marks the interval as a potential or actual spike


@dataclass(frozen=True)
class PriceWindowSettings:
    spike_threshold: float = 1.00          # $/kWh, on sell_high
    lookahead_h: float = 4.0
    reserve_margin_h: float = 2.0
    reserve_until: time | None = None      # local clock time; None = use the margin
    min_sell_price: float = 0.30           # $/kWh
    charge_ratio: float = 1.5
    max_charge_buy_price: float | None = None  # $/kWh; None = formula only
    round_trip_efficiency: float = 0.85


@dataclass(frozen=True)
class PriceWindowEnergy:
    usable_kwh: float      # above the SOC floors, every battery under control
    house_load_w: float    # expected average house load to the reserve horizon
    at_soc_ceiling: bool   # True when no battery under control can take charge


@dataclass(frozen=True)
class PriceWindowLimits:
    export_w: float   # maximum export power at the meter
    charge_w: float   # maximum charge power (reported as would_command_w)


@dataclass(frozen=True)
class IntervalRow:
    """One row of the working table recorded with every decision."""

    start: datetime
    end: datetime
    price: float          # the ranking price
    basis: str            # "confirmed", "high" or "predicted"
    qualifies: bool
    allocated_kwh: float


@dataclass(frozen=True)
class PriceWindowDecision:
    action: str | None    # ACTION_* while a window is active, otherwise None
    code: str
    sentence: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    reserve_horizon: datetime | None = None
    reserve_basis: str | None = None   # "forecast_margin" or "clock_time"
    usable_kwh: float = 0.0
    house_reserve_kwh: float = 0.0
    surplus_kwh: float = 0.0
    shortfall_kwh: float = 0.0
    current_sell: float | None = None
    current_buy: float | None = None
    current_confirmed: bool | None = None
    current_rank: int | None = None
    intervals: tuple[IntervalRow, ...] = ()
    shadow: bool = False
    would_command_w: float | None = None   # grid W, + = import; None when inactive


def inactive(code: str, sentence: str) -> PriceWindowDecision:
    """A decision that leaves control with EMHASS tracking."""
    return PriceWindowDecision(action=None, code=code, sentence=sentence)


# ── parsing ───────────────────────────────────────────────────────────────────


def _when(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else None


def _price(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _high(entry: dict, fallback: float) -> float:
    advanced = entry.get("advanced_price_predicted")
    if isinstance(advanced, dict):
        high = _price(advanced.get("high"))
        if high is not None:
            return max(high, fallback)
    return fallback


def _entries(attrs) -> list[dict]:
    forecast = attrs.get("detailedForecast") if isinstance(attrs, dict) else None
    if not isinstance(forecast, (list, tuple)):
        return []
    return [entry for entry in forecast if isinstance(entry, dict)]


def parse_amber_express(feed_in_attrs, general_attrs, now: datetime) -> list[PriceInterval]:
    """Join the two channels' `detailedForecast` lists into price intervals.

    Amber Express starts each interval one second late (`02:00:01`) and ends it on the
    boundary (`02:05:00`), so starts are rounded down to the minute; otherwise no
    interval would cover the first second of each one.  Intervals that have ended and
    entries that cannot be parsed are dropped.  Returns an empty list when no interval
    covers `now`, which the caller treats as stale data.
    """
    general_by_start: dict[datetime, dict] = {}
    for entry in _entries(general_attrs):
        start = _when(entry.get("start_time"))
        if start is not None:
            general_by_start[start.replace(second=0, microsecond=0)] = entry

    intervals: list[PriceInterval] = []
    for entry in _entries(feed_in_attrs):
        start = _when(entry.get("start_time"))
        end = _when(entry.get("end_time"))
        sell = _price(entry.get("per_kwh"))
        if start is None or end is None or sell is None:
            continue
        start = start.replace(second=0, microsecond=0)
        if end <= start or end <= now:
            continue
        general = general_by_start.get(start)
        if general is None:
            continue
        buy = _price(general.get("per_kwh"))
        if buy is None:
            continue
        confirmed = entry.get("estimate") is False and general.get("estimate") is False
        intervals.append(
            PriceInterval(
                start=start,
                end=end,
                sell=sell,
                sell_high=sell if confirmed else _high(entry, sell),
                buy=buy,
                buy_high=buy if confirmed else _high(general, buy),
                confirmed=confirmed,
                flagged=(
                    entry.get("spike_status") in _FLAGGED_STATUSES
                    or general.get("spike_status") in _FLAGGED_STATUSES
                ),
            )
        )
    intervals.sort(key=lambda interval: interval.start)
    if not intervals or intervals[0].start > now:
        return []
    return intervals
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/grid_coordinator/price_window.py tests/test_price_window.py
git commit -m "feat: parse Amber Express forecasts into price intervals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 2: The decision and its reason

**Files:**
- Modify: `custom_components/grid_coordinator/price_window.py` (append)
- Test: `tests/test_price_window.py` (append)

**Interfaces:**
- Consumes: Task 1's dataclasses and constants.
- Produces: `decide_price_window(intervals, now, energy, limits, settings) -> PriceWindowDecision` (`now` timezone-aware, local); `decision_attributes(decision) -> dict`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_price_window.py`, replace the `from custom_components.grid_coordinator.price_window import (...)` block with:

```python
from custom_components.grid_coordinator.price_window import (
    ACTION_CHARGE,
    ACTION_EXPORT,
    ACTION_HOLD,
    PriceInterval,
    PriceWindowEnergy,
    PriceWindowLimits,
    PriceWindowSettings,
    decide_price_window,
    decision_attributes,
    parse_amber_express,
)
```

Then append to the file:

```python
# ── decision ──────────────────────────────────────────────────────────────────


def _iv(offset_min, sell, *, minutes=5, high=None, buy=None, buy_high=None, confirmed=False, flagged=False):
    start = T0 + timedelta(minutes=offset_min)
    buy = sell + 0.11 if buy is None else buy
    return PriceInterval(
        start=start,
        end=start + timedelta(minutes=minutes),
        sell=sell,
        sell_high=sell if (confirmed or high is None) else high,
        buy=buy,
        buy_high=buy if (confirmed or buy_high is None) else buy_high,
        confirmed=confirmed,
        flagged=flagged,
    )


def _energy(usable_kwh, *, load_w=1000.0, full=False):
    return PriceWindowEnergy(usable_kwh=usable_kwh, house_load_w=load_w, at_soc_ceiling=full)


def _decide(intervals, energy, settings=SETTINGS, now=NOW):
    return decide_price_window(intervals, now, energy, LIMITS, settings)


# Now: a confirmed $10.48 interval; then 64c; then two more that could spike.
SPIKY = [
    _iv(0, 10.48, confirmed=True),
    _iv(5, 0.64, high=0.90),
    _iv(10, 0.70, high=12.0),
    _iv(15, 0.70, high=12.0),
]


def test_no_interval_covering_now_is_stale():
    assert _decide([], _energy(40)).code == "data_stale"
    assert _decide([_iv(5, 10.0)], _energy(40)).code == "data_stale"


def test_no_window_when_nothing_could_spike():
    d = _decide([_iv(0, 0.33, confirmed=True), _iv(5, 0.31, high=0.70)], _energy(40))
    assert (d.action, d.code) == (None, "no_window")
    assert d.would_command_w is None


def test_flagged_interval_opens_a_window_below_the_threshold():
    d = _decide([_iv(0, 0.33, confirmed=True), _iv(5, 0.31, high=0.70, flagged=True)], _energy(40))
    assert d.action is not None


def test_qualifying_interval_beyond_the_lookahead_is_ignored():
    far = _iv(241, 10.0, high=15.0)  # starts 4 h 0 min 20 s from NOW
    assert _decide([_iv(0, 0.33, confirmed=True), far], _energy(40)).code == "no_window"
    near = _iv(239, 10.0, high=15.0)
    assert _decide([_iv(0, 0.33, confirmed=True), near], _energy(40)).action is not None


def test_plenty_of_energy_serves_every_sellable_interval():
    d = _decide(SPIKY, _energy(40))
    assert (d.action, d.code) == (ACTION_EXPORT, "export_allocated")
    assert d.would_command_w == -10000.0
    assert d.window_end == T0 + timedelta(minutes=20)
    assert d.reserve_basis == "forecast_margin"
    assert d.reserve_horizon == T0 + timedelta(minutes=140)
    # 1 kW for 139 m 20 s at sqrt(0.85) discharge efficiency
    assert d.house_reserve_kwh == pytest.approx(2.52, abs=0.01)
    assert d.surplus_kwh == pytest.approx(37.48, abs=0.01)
    assert d.shortfall_kwh == 0
    assert all(row.allocated_kwh > 0 for row in d.intervals)
    # A confirmed spike price is ranked against what later intervals are predicted
    # to pay (70c), not what they could (12.00), so it comes first.
    assert d.current_rank == 1
    assert [row.basis for row in d.intervals] == ["confirmed", "predicted", "predicted", "predicted"]
    assert "$10.48" in d.sentence and "ranks 1 of 4" in d.sentence


def test_current_interval_is_charged_only_for_its_remaining_time():
    d = _decide(SPIKY, _energy(40))
    # 4 m 20 s left at 10 kW, at the battery
    assert d.intervals[0].allocated_kwh == pytest.approx(10 * (260 / 3600) / 0.85**0.5, abs=0.001)


def test_tight_energy_keeps_it_for_intervals_that_could_spike():
    # 64c now; two intervals ahead could reach $12 and need 1.8 kWh; surplus is ~1.5.
    mid = [_iv(0, 0.64, confirmed=True)] + SPIKY[2:]
    d = _decide(mid, _energy(4.0))
    assert d.action == ACTION_HOLD
    assert d.intervals[0].allocated_kwh == 0
    assert d.intervals[1].allocated_kwh > 0
    assert d.intervals[1].basis == "high"
    assert d.current_rank == 3
    # A qualifying interval is short, so the code reports why it is not charging,
    # and the sentence still says what the energy is being kept for.
    assert d.code == "hold_charge_too_dear"
    assert "reserved for 2 intervals at $12.00 or more" in d.sentence
    assert "Not charging: buying at 75c" in d.sentence


def test_a_confirmed_spike_is_not_held_back_for_one_that_merely_could_be_higher():
    # $10.48 in hand; later intervals could reach $12 but are predicted at 70c.
    d = _decide([SPIKY[0]] + SPIKY[2:], _energy(4.0))
    assert (d.action, d.code) == (ACTION_EXPORT, "export_allocated")
    assert d.current_rank == 1


def test_a_confirmed_spike_is_held_back_for_one_predicted_to_be_higher():
    intervals = [_iv(0, 1.05, confirmed=True), _iv(5, 10.0, high=15.0), _iv(10, 10.0, high=15.0)]
    d = _decide(intervals, _energy(4.0))
    assert d.action == ACTION_HOLD
    assert d.current_rank == 3 and d.intervals[1].basis == "predicted"


def test_outranked_by_a_better_interval_that_is_not_a_spike():
    # 64c now; a 30-minute interval that could reach 95c takes what the possible
    # $12 interval leaves.  Every qualifying interval is served: plain outranked.
    intervals = [
        _iv(0, 0.64, confirmed=True),
        _iv(5, 0.70, minutes=30, high=0.95),
        _iv(35, 0.70, high=12.0),
    ]
    d = _decide(intervals, _energy(6.0))
    assert (d.action, d.code) == (ACTION_HOLD, "hold_outranked")
    assert d.shortfall_kwh == 0
    assert "reserved for 2 intervals at 95c or more" in d.sentence


def test_below_minimum_sell_price_holds_even_with_energy_to_spare():
    d = _decide([_iv(0, 0.17, confirmed=True)] + SPIKY[2:], _energy(40))
    assert (d.action, d.code) == (ACTION_HOLD, "hold_below_min_price")
    assert d.would_command_w == 0.0
    assert "17c" in d.sentence and "30c" in d.sentence


def test_no_surplus_holds_and_says_the_reserve_took_it():
    # 2 kWh usable; the house needs 2.4 kWh to the end of the window plus 2 h.
    d = _decide([_iv(0, 0.64, confirmed=True)] + SPIKY[2:], _energy(2.0))
    assert d.action == ACTION_HOLD and d.surplus_kwh == 0
    assert d.sentence.startswith("Holding: the house reserve to 17:50 (2.5 kWh) takes all 2.0 kWh usable.")
    assert d.code == "hold_charge_too_dear"  # spikes are unserved and 75c is too dear to buy


def test_no_surplus_with_nothing_unserved_reports_no_surplus():
    # The only qualifying interval is the current one, at a price below the minimum
    # sell price... so make export impossible instead: zero export limit.
    d = decide_price_window(
        [_iv(0, 0.64, confirmed=True, flagged=True)], NOW, _energy(0.5),
        PriceWindowLimits(export_w=0.0, charge_w=12000.0), SETTINGS,
    )
    assert (d.action, d.code) == (ACTION_HOLD, "hold_no_surplus")
    assert "takes all 0.5 kWh" in d.sentence


def test_unconfirmed_current_interval_is_served_on_its_high_bound():
    intervals = [_iv(0, 0.60, high=11.0), _iv(5, 0.70, high=12.0)]
    d = _decide(intervals, _energy(40))
    assert (d.action, d.code) == (ACTION_EXPORT, "export_unconfirmed")
    assert d.current_confirmed is False
    assert d.intervals[0].basis == "high" and d.intervals[0].price == 11.0
    assert "could reach $11.00" in d.sentence


# Shortfall cases: confirmed cheap price now, two spike intervals ahead, no surplus.
def _shortfall(buy, *, predicted=9.80, full=False, settings=SETTINGS, confirmed=True, buy_high=None):
    intervals = [
        _iv(0, 0.17, buy=buy, buy_high=buy_high, confirmed=confirmed),
        _iv(5, predicted, high=12.0),
        _iv(10, 10.20, high=12.0),
    ]
    return _decide(intervals, _energy(1.0, full=full), settings)


def test_shortfall_with_a_cheap_buy_price_charges():
    d = _shortfall(0.28)
    assert (d.action, d.code) == (ACTION_CHARGE, "charge_shortfall")
    assert d.would_command_w == 12000.0
    assert d.shortfall_kwh == pytest.approx(2 * 10 * (5 / 60) / 0.85**0.5, abs=0.01)
    assert "buying at 28c" in d.sentence and "cheapest predicted $9.80" in d.sentence


def test_shortfall_with_a_dear_buy_price_holds():
    # 0.60 / 0.85 * 1.5 = 1.06 > 1.00 predicted
    d = _shortfall(0.60, predicted=1.00)
    assert (d.action, d.code) == (ACTION_HOLD, "hold_charge_too_dear")
    # 0.56 / 0.85 * 1.5 = 0.988 <= 1.00
    assert _shortfall(0.56, predicted=1.00).action == ACTION_CHARGE


def test_charge_rule_uses_the_predicted_price_not_the_high_bound():
    # Predicted 40c with a $12 high bound: buying at 28c (49c effective) is not worth it.
    assert _shortfall(0.28, predicted=0.40).code == "hold_charge_too_dear"


def test_maximum_charge_buy_price_overrides_the_formula():
    capped = PriceWindowSettings(max_charge_buy_price=1.00)
    d = _shortfall(1.20, settings=capped)  # formula passes: 2.12 <= 9.80
    assert (d.action, d.code) == (ACTION_HOLD, "hold_charge_too_dear")
    assert "over the $1.00 maximum charge buy price" in d.sentence
    assert _shortfall(1.00, settings=capped).action == ACTION_CHARGE  # at the cap is allowed
    assert _shortfall(1.20).action == ACTION_CHARGE  # unset: formula only


def test_full_batteries_hold_instead_of_charging():
    d = _shortfall(0.28, full=True)
    assert (d.action, d.code) == (ACTION_HOLD, "hold_soc_ceiling")


def test_unconfirmed_buy_price_is_judged_on_its_high_bound():
    # Predicted 28c but it could be $13: do not start charging until it is confirmed.
    assert _shortfall(0.28, confirmed=False, buy_high=13.0).code == "hold_charge_too_dear"
    assert _shortfall(0.28, confirmed=False, buy_high=0.30).action == ACTION_CHARGE


def test_reserve_until_clock_time_later_today():
    d = _decide(SPIKY, _energy(40), PriceWindowSettings(reserve_until=time(21, 0)))
    assert d.reserve_basis == "clock_time"
    assert d.reserve_horizon == datetime(2026, 10, 12, 21, 0, tzinfo=SYD)


def test_reserve_until_clock_time_rolls_to_tomorrow():
    d = _decide(SPIKY, _energy(40), PriceWindowSettings(reserve_until=time(6, 30)))
    assert d.reserve_horizon == datetime(2026, 10, 13, 6, 30, tzinfo=SYD)
    # 15 h of 1 kW: the reserve now takes a large share of the 40 kWh
    assert d.house_reserve_kwh == pytest.approx(14.99 / 0.85**0.5, abs=0.02)


def test_reserve_until_exactly_now_means_tomorrow():
    now = datetime(2026, 10, 12, 15, 30, tzinfo=SYD)
    d = _decide(SPIKY, _energy(40), PriceWindowSettings(reserve_until=time(15, 30)), now=now)
    assert d.reserve_horizon == datetime(2026, 10, 13, 15, 30, tzinfo=SYD)


def test_nonsense_inputs_hold_rather_than_raise():
    # Negative energy and load, zero efficiency: treated as nothing to sell.
    d = _decide(SPIKY, _energy(-5.0, load_w=-200.0), PriceWindowSettings(round_trip_efficiency=0.0))
    assert d.usable_kwh == 0 and d.house_reserve_kwh == 0
    assert d.action == ACTION_HOLD


def test_attributes_carry_the_sentence_and_the_working():
    d = _decide(SPIKY, _energy(40))
    attrs = decision_attributes(d)
    assert attrs["reason"] == d.sentence
    assert attrs["window_end"] == (T0 + timedelta(minutes=20)).isoformat()
    assert attrs["reserve_basis"] == "forecast_margin"
    assert attrs["current_rank"] == 1 and attrs["current_confirmed"] is True
    assert attrs["intervals"][0] == {
        "start": T0.isoformat(),
        "price": 10.48,
        "basis": "confirmed",
        "qualifies": True,
        "allocated_kwh": d.intervals[0].allocated_kwh,
    }
    assert [row["qualifies"] for row in attrs["intervals"]] == [True, False, True, True]
    assert attrs["shadow"] is False and attrs["would_command_w"] == -10000.0


def test_attributes_of_an_inactive_decision():
    attrs = decision_attributes(_decide([], _energy(40)))
    assert attrs["action"] is None and attrs["intervals"] == [] and attrs["window_end"] is None


# ── replay of 2026-10-12 ──────────────────────────────────────────────────────
# Confirmed feed-in prices ($/kWh) per 5-minute interval from 15:05 to 17:05 AEDT.
# The forecasts shown on the day were not recorded, so each step sees the current
# price confirmed and every later interval up to 17:00 as "could reach $12".

REPLAY = [
    1.41, 0.80, 24.23, 0.71, 24.23, 10.48, 10.48, 10.48, 15.73, 10.48, 10.48,
    0.64, 10.48, 0.64, 10.37, 0.17, 0.32, 10.48, 0.17, 0.66, 1.04, 0.31, 0.63, 1.04, 0.15,
]
REPLAY_START = datetime(2026, 10, 12, 15, 5, tzinfo=SYD)
SPIKE_STEPS = [i for i, price in enumerate(REPLAY) if price >= 10]


def _replay(usable_kwh):
    """Step through the afternoon; return [(price, action, code)] and energy sold."""
    out = []
    for step, price in enumerate(REPLAY):
        now = REPLAY_START + timedelta(minutes=5 * step, seconds=40)
        intervals = [
            PriceInterval(
                start=REPLAY_START + timedelta(minutes=5 * k),
                end=REPLAY_START + timedelta(minutes=5 * (k + 1)),
                sell=REPLAY[k] if k == step else 0.60,
                sell_high=REPLAY[k] if k == step else 12.0,
                buy=(REPLAY[k] if k == step else 0.60) * 1.1 + 0.10,
                buy_high=(REPLAY[k] if k == step else 12.0) * 1.1 + 0.10,
                confirmed=k == step,
                flagged=False,
            )
            for k in range(step, len(REPLAY))
        ]
        d = decide_price_window(intervals, now, _energy(usable_kwh), LIMITS, SETTINGS)
        out.append((price, d.action, d.code, d.shortfall_kwh))
        if d.action == ACTION_EXPORT:
            usable_kwh -= 10 * (260 / 3600) / 0.85**0.5
        usable_kwh -= 1.0 * (5 / 60)  # the house
    return out


def test_replay_with_the_real_battery_exports_through_every_spike():
    result = _replay(45.6)  # 60 kWh at 96%, floor 20%
    for step in SPIKE_STEPS:
        assert result[step][1] == ACTION_EXPORT, (step, result[step])
    # Good-but-not-spike prices are sold too, because energy was never short...
    assert all(row[1] == ACTION_EXPORT for row in result if row[0] >= 0.30)
    # ...and the 17c dips are not.  By the last interval nothing ahead can spike.
    assert [row[2] for row in result if row[0] < 0.30] == [
        "hold_below_min_price", "hold_below_min_price", "no_window",
    ]


def test_replay_with_a_small_battery_never_sells_cheap_while_spikes_are_possible():
    result = _replay(9.0)
    # Below the $1.00 spike threshold it sells only when nothing ahead is left short.
    sold_short = [row for row in result if row[1] == ACTION_EXPORT and row[0] < 1.0 and row[3] > 0]
    assert sold_short == []
    # Every confirmed spike it has energy for is sold, none held back for a possible one.
    assert all(result[step][1] == ACTION_EXPORT for step in SPIKE_STEPS[:4])
    # At the 17c dips, with spikes still possible and the battery short, it buys.
    assert [row[1] for row in result if row[0] == 0.17] == [ACTION_CHARGE, ACTION_CHARGE]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: collection error, `ImportError: cannot import name 'decide_price_window'`.

- [ ] **Step 3: Write the decision**

In `custom_components/grid_coordinator/price_window.py`, change the import line `from datetime import datetime, time` to `from datetime import datetime, time, timedelta`, then append:

```python
# ── decision ──────────────────────────────────────────────────────────────────


def _money(value: float) -> str:
    return f"${value:.2f}" if abs(value) >= 1.0 else f"{value * 100:.0f}c"


def _clock(value: datetime) -> str:
    return value.strftime("%H:%M")


def _next_occurrence(now: datetime, at: time) -> datetime:
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def decide_price_window(
    intervals: list[PriceInterval],
    now: datetime,
    energy: PriceWindowEnergy,
    limits: PriceWindowLimits,
    settings: PriceWindowSettings,
) -> PriceWindowDecision:
    """Decide export / hold / charge for this tick, with the reason.

    `now` must be timezone-aware in the home's local zone: a reserve-until clock time
    is read against it.  `intervals` comes from `parse_amber_express`, so the first
    one covers `now`.
    """
    if not intervals or not (intervals[0].start <= now < intervals[0].end):
        return inactive(CODE_DATA_STALE, "Amber Express has no price interval covering now.")
    current = intervals[0]

    # 1. Find the window.
    lookahead_end = now + timedelta(hours=settings.lookahead_h)
    qualifying = [
        interval
        for interval in intervals
        if interval.start < lookahead_end
        and (interval.sell_high >= settings.spike_threshold or interval.flagged)
    ]
    if not qualifying:
        return inactive(
            CODE_NO_WINDOW,
            f"No interval within {settings.lookahead_h:g} h could reach "
            f"{_money(settings.spike_threshold)}.",
        )
    window_end = max(interval.end for interval in qualifying)
    in_window = [interval for interval in intervals if interval.start < window_end]

    # 2. House reserve.
    if settings.reserve_until is not None:
        horizon = _next_occurrence(now, settings.reserve_until)
        basis = "clock_time"
    else:
        horizon = window_end + timedelta(hours=settings.reserve_margin_h)
        basis = "forecast_margin"
    rte = min(1.0, max(0.05, settings.round_trip_efficiency))
    discharge_eff = math.sqrt(rte)
    hours_to_horizon = max(0.0, (horizon - now).total_seconds() / 3600)
    house_reserve = max(0.0, energy.house_load_w) / 1000 * hours_to_horizon / discharge_eff

    # 3. Surplus.
    usable = max(0.0, energy.usable_kwh)
    surplus = max(0.0, usable - house_reserve)

    # 4. Allocate the surplus to the best-priced intervals.
    # Later intervals are normally ranked on the top of their forecast range, so one
    # that might spike keeps energy back.  A confirmed price at or above the spike
    # threshold is a spike in hand: then only intervals *predicted* to beat it rank
    # above it, so it is never held back for one that merely could.
    spike_in_hand = current.confirmed and current.sell >= settings.spike_threshold

    def ranking_price(interval: PriceInterval) -> float:
        if interval.confirmed or spike_in_hand:
            return interval.sell
        return interval.sell_high

    def basis_of(interval: PriceInterval) -> str:
        if interval.confirmed:
            return "confirmed"
        return "predicted" if spike_in_hand else "high"

    def need_kwh(interval: PriceInterval) -> float:
        hours = (interval.end - max(interval.start, now)).total_seconds() / 3600
        return max(0.0, limits.export_w) / 1000 * max(0.0, hours) / discharge_eff

    sellable = sorted(
        [i for i in in_window if i.sell_high >= settings.min_sell_price],
        key=lambda i: (-ranking_price(i), i.start),
    )
    allocated: dict[datetime, float] = {}
    remaining = surplus
    for interval in sellable:
        share = min(need_kwh(interval), remaining)
        allocated[interval.start] = share
        remaining -= share

    qualifying_starts = {interval.start for interval in qualifying}
    unserved = [
        interval
        for interval in qualifying
        if need_kwh(interval) - allocated.get(interval.start, 0.0) > _EPS_KWH
    ]
    shortfall = sum(need_kwh(i) - allocated.get(i.start, 0.0) for i in unserved)
    rank = sellable.index(current) + 1 if current in sellable else None
    current_price = ranking_price(current)

    rows = tuple(
        IntervalRow(
            start=interval.start,
            end=interval.end,
            price=ranking_price(interval),
            basis=basis_of(interval),
            qualifies=interval.start in qualifying_starts,
            allocated_kwh=round(allocated.get(interval.start, 0.0), 3),
        )
        for interval in in_window
    )

    def decision(action: str, code: str, sentence: str) -> PriceWindowDecision:
        command = {
            ACTION_EXPORT: -limits.export_w,
            ACTION_CHARGE: limits.charge_w,
            ACTION_HOLD: 0.0,
        }[action]
        return PriceWindowDecision(
            action=action,
            code=code,
            sentence=sentence,
            window_start=now,
            window_end=window_end,
            reserve_horizon=horizon,
            reserve_basis=basis,
            usable_kwh=round(usable, 2),
            house_reserve_kwh=round(house_reserve, 2),
            surplus_kwh=round(surplus, 2),
            shortfall_kwh=round(shortfall, 2),
            current_sell=current.sell,
            current_buy=current.buy,
            current_confirmed=current.confirmed,
            current_rank=rank,
            intervals=rows,
            would_command_w=command,
        )

    # 5. Decide.
    if allocated.get(current.start, 0.0) > _EPS_KWH:
        served = [i for i in sellable if allocated.get(i.start, 0.0) > _EPS_KWH]
        needed = sum(need_kwh(i) for i in sellable)
        detail = (
            f"ranks {rank} of {len(sellable)} sellable intervals; surplus "
            f"{surplus:.1f} kWh covers {len(served)} of them (all need {needed:.1f} kWh)."
        )
        if current.confirmed:
            return decision(
                ACTION_EXPORT, CODE_EXPORT_ALLOCATED, f"Exporting: {_money(current.sell)} now {detail}"
            )
        return decision(
            ACTION_EXPORT,
            CODE_EXPORT_UNCONFIRMED,
            f"Exporting: price not confirmed yet, could reach {_money(current.sell_high)}; {detail}",
        )

    if surplus <= _EPS_KWH:
        code = CODE_HOLD_NO_SURPLUS
        sentence = (
            f"Holding: the house reserve to {_clock(horizon)} ({house_reserve:.1f} kWh) "
            f"takes all {usable:.1f} kWh usable."
        )
    elif current_price < settings.min_sell_price:
        code = CODE_HOLD_BELOW_MIN_PRICE
        sentence = (
            f"Holding: {_money(current_price)} now is under the "
            f"{_money(settings.min_sell_price)} minimum sell price."
        )
    else:
        code = CODE_HOLD_OUTRANKED
        better = [i for i in sellable if allocated.get(i.start, 0.0) > _EPS_KWH]
        floor_price = min([ranking_price(i) for i in better], default=current_price)
        sentence = (
            f"Holding: {_money(current_price)} now, but the {surplus:.1f} kWh surplus is "
            f"reserved for {len(better)} intervals at {_money(floor_price)} or more, "
            f"through {_clock(window_end)}."
        )

    if unserved:
        cheapest = min(unserved, key=lambda i: i.sell)
        buy = current.buy if current.confirmed else current.buy_high
        effective = buy / rte * settings.charge_ratio
        capped = settings.max_charge_buy_price is not None and buy > settings.max_charge_buy_price
        situation = (
            f"{len(unserved)} spike intervals unserved, cheapest predicted "
            f"{_money(cheapest.sell)}; shortfall {shortfall:.1f} kWh"
        )
        if capped:
            code = CODE_HOLD_CHARGE_TOO_DEAR
            sentence += (
                f" Not charging: buying at {_money(buy)} is over the "
                f"{_money(settings.max_charge_buy_price)} maximum charge buy price; {situation}."
            )
        elif effective > cheapest.sell:
            code = CODE_HOLD_CHARGE_TOO_DEAR
            sentence += (
                f" Not charging: buying at {_money(buy)} costs {_money(effective)} after "
                f"losses and margin; {situation}."
            )
        elif energy.at_soc_ceiling:
            code = CODE_HOLD_SOC_CEILING
            sentence += f" Not charging: batteries are full; {situation}."
        else:
            return decision(
                ACTION_CHARGE,
                CODE_CHARGE_SHORTFALL,
                f"Charging: buying at {_money(buy)}; {situation}.",
            )

    return decision(ACTION_HOLD, code, sentence)


def decision_attributes(decision: PriceWindowDecision) -> dict:
    """The reason sensor's attributes: the sentence plus the working behind it."""

    def stamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "reason": decision.sentence,
        "action": decision.action,
        "shadow": decision.shadow,
        "would_command_w": decision.would_command_w,
        "window_start": stamp(decision.window_start),
        "window_end": stamp(decision.window_end),
        "reserve_horizon": stamp(decision.reserve_horizon),
        "reserve_basis": decision.reserve_basis,
        "usable_kwh": decision.usable_kwh,
        "house_reserve_kwh": decision.house_reserve_kwh,
        "surplus_kwh": decision.surplus_kwh,
        "shortfall_kwh": decision.shortfall_kwh,
        "current_sell": decision.current_sell,
        "current_buy": decision.current_buy,
        "current_confirmed": decision.current_confirmed,
        "current_rank": decision.current_rank,
        "intervals": [
            {
                "start": row.start.isoformat(),
                "price": round(row.price, 4),
                "basis": row.basis,
                "qualifies": row.qualifies,
                "allocated_kwh": row.allocated_kwh,
            }
            for row in decision.intervals
        ],
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: `38 passed`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/grid_coordinator/price_window.py tests/test_price_window.py
git commit -m "feat: price window decision with reason code, sentence and working

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 3: Setpoints for an action

**Files:**
- Modify: `custom_components/grid_coordinator/price_window.py` (append)
- Test: `tests/test_price_window.py` (append)

**Interfaces:**
- Consumes: `ACTION_EXPORT`, `ACTION_CHARGE`, `ACTION_HOLD`.
- Produces: `window_setpoints(action, *, grid_uncontrolled, export_limit, import_limit, headroom_reserve, max_discharge, max_charge, ev_power=0.0) -> tuple[float, float]` returning `(grid_target, battery_setpoint)` in W, battery + = discharge, for the batteries combined.

- [ ] **Step 1: Write the failing tests**

In `tests/test_price_window.py`, add `window_setpoints` to the price_window import block (after `parse_amber_express`), then append:

```python
# ── setpoints ─────────────────────────────────────────────────────────────────

_SP = dict(export_limit=10000.0, import_limit=12000.0, headroom_reserve=0.0,
           max_discharge=13000.0, max_charge=12600.0)


def test_export_setpoint_makes_up_the_export_limit():
    # House drawing 800 W with the batteries idle: 10.8 kW of discharge exports 10 kW.
    assert window_setpoints(ACTION_EXPORT, grid_uncontrolled=800.0, **_SP) == (-10000.0, 10800.0)


def test_export_setpoint_is_capped_by_what_the_batteries_can_deliver():
    assert window_setpoints(ACTION_EXPORT, grid_uncontrolled=4000.0, **_SP) == (-10000.0, 13000.0)


def test_export_setpoint_with_solar_already_over_the_limit_charges_the_excess():
    assert window_setpoints(ACTION_EXPORT, grid_uncontrolled=-11000.0, **_SP) == (-10000.0, -1000.0)


def test_charge_setpoint_fills_the_import_limit():
    # 800 W of house leaves 11.2 kW of import for charging.
    assert window_setpoints(ACTION_CHARGE, grid_uncontrolled=800.0, **_SP) == (12000.0, -11200.0)


def test_charge_setpoint_leaves_reserved_headroom_free():
    sp = dict(_SP, headroom_reserve=3000.0)
    assert window_setpoints(ACTION_CHARGE, grid_uncontrolled=800.0, **sp) == (9000.0, -8200.0)


def test_charge_setpoint_is_capped_by_what_the_batteries_can_take():
    assert window_setpoints(ACTION_CHARGE, grid_uncontrolled=-2000.0, **_SP) == (10600.0, -12600.0)


def test_hold_setpoint_carries_the_house_at_zero_grid():
    assert window_setpoints(ACTION_HOLD, grid_uncontrolled=800.0, **_SP) == (0.0, 800.0)
    assert window_setpoints(ACTION_HOLD, grid_uncontrolled=-3000.0, **_SP) == (0.0, -3000.0)


def test_hold_setpoint_leaves_the_ev_on_the_grid():
    # 7.8 kW at the meter of which 7 kW is the car: the batteries carry the other 800 W.
    assert window_setpoints(ACTION_HOLD, grid_uncontrolled=7800.0, ev_power=7000.0, **_SP) == (7000.0, 800.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: collection error, `ImportError: cannot import name 'window_setpoints'`.

- [ ] **Step 3: Write the function**

Append to `custom_components/grid_coordinator/price_window.py`:

```python
# ── setpoints ─────────────────────────────────────────────────────────────────


def window_setpoints(
    action: str,
    *,
    grid_uncontrolled: float,
    export_limit: float,
    import_limit: float,
    headroom_reserve: float,
    max_discharge: float,
    max_charge: float,
    ev_power: float = 0.0,
) -> tuple[float, float]:
    """Return (grid_target, battery_setpoint) in W for a price window action.

    `grid_uncontrolled` is the grid power with every commanded battery's output added
    back.  The battery setpoint is for the batteries combined (+ = discharge) and is
    anchored on that figure, so the tracking path reaches the target in one step and
    the grid target it is given is one it can actually hold.

    export: the batteries make up whatever the meter needs to sit at the export limit.
    charge: the batteries take everything the import limit (less any reserved
            headroom) allows.
    hold:   the batteries carry the house and nothing else.  `ev_power` (the EV
            charger's draw, when it is charging) stays on the grid.
    """
    if action == ACTION_EXPORT:
        battery = min(max_discharge, grid_uncontrolled + export_limit)
        battery = max(-max_charge, battery)
        return -export_limit, battery
    if action == ACTION_CHARGE:
        battery = max(-max_charge, grid_uncontrolled - (import_limit - headroom_reserve))
        battery = min(max_discharge, battery)
        return grid_uncontrolled - battery, battery
    battery = max(-max_charge, min(max_discharge, grid_uncontrolled - ev_power))
    return grid_uncontrolled - battery, battery
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_price_window.py -q`
Expected: `46 passed`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/grid_coordinator/price_window.py tests/test_price_window.py
git commit -m "feat: grid target and battery setpoint for a price window action

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 4: Config keys, modes and the data field

**Files:**
- Modify: `custom_components/grid_coordinator/const.py`
- Modify: `custom_components/grid_coordinator/models.py`

**Interfaces:**
- Consumes: `PriceWindowDecision` from Task 1.
- Produces: `CONF_ENTITY_PW_ENABLE`, `CONF_ENTITY_PW_SHADOW`, `CONF_ENTITY_PW_FEED_IN`, `CONF_ENTITY_PW_GENERAL`, `CONF_ENTITY_PW_HOUSE_LOAD`, `CONF_PW_SPIKE_THRESHOLD`, `CONF_PW_LOOKAHEAD_HOURS`, `CONF_PW_RESERVE_MARGIN_HOURS`, `CONF_PW_RESERVE_UNTIL`, `CONF_PW_MIN_SELL_PRICE`, `CONF_PW_CHARGE_RATIO`, `CONF_PW_MAX_CHARGE_BUY_PRICE`, `CONF_PW_ROUND_TRIP_EFFICIENCY`, the matching `DEFAULT_PW_*`, `PRICE_WINDOW_STALE_MINUTES`, `PRICE_WINDOW_FIELDS: dict[str, str]`; `CoordinatorMode.PRICE_WINDOW_EXPORT / _HOLD / _CHARGE`; `CoordinatorData.price_window: PriceWindowDecision | None = None`.

These are declarations with no behaviour of their own; Tasks 5 to 7 test them in use. The check here is that nothing existing breaks.

- [ ] **Step 1: Add the constants**

Edit 1 of 1 in `custom_components/grid_coordinator/const.py`. Find (occurs exactly once):

```python
# ── Defaults ──────────────────────────────────────────────────────────────────
```

Replace with:

```python
# ── Price window (forecast price spikes; Amber Express is the price source) ───
# Entities.  All optional: the feature is off until the enable entity, both Amber
# Express entities and the house load entity are set.
CONF_ENTITY_PW_ENABLE = "entity_price_window_enable"
CONF_ENTITY_PW_SHADOW = "entity_price_window_shadow"
CONF_ENTITY_PW_FEED_IN = "entity_price_window_feed_in"
CONF_ENTITY_PW_GENERAL = "entity_price_window_general"
CONF_ENTITY_PW_HOUSE_LOAD = "entity_price_window_house_load"
# Settings.  Each is stored as text and holds either a literal or a helper entity ID.
CONF_PW_SPIKE_THRESHOLD = "price_window_spike_threshold"
CONF_PW_LOOKAHEAD_HOURS = "price_window_lookahead_hours"
CONF_PW_RESERVE_MARGIN_HOURS = "price_window_reserve_margin_hours"
CONF_PW_RESERVE_UNTIL = "price_window_reserve_until"
CONF_PW_MIN_SELL_PRICE = "price_window_min_sell_price"
CONF_PW_CHARGE_RATIO = "price_window_charge_ratio"
CONF_PW_MAX_CHARGE_BUY_PRICE = "price_window_max_charge_buy_price"
CONF_PW_ROUND_TRIP_EFFICIENCY = "price_window_round_trip_efficiency"

DEFAULT_PW_SPIKE_THRESHOLD = 1.00        # $/kWh — feed-in high bound that marks a possible spike
DEFAULT_PW_LOOKAHEAD_HOURS = 4.0         # h — how far ahead a possible spike opens the window
DEFAULT_PW_RESERVE_MARGIN_HOURS = 2.0    # h — house reserve runs this long past the last possible spike
DEFAULT_PW_MIN_SELL_PRICE = 0.30         # $/kWh — never export below this inside a window
DEFAULT_PW_CHARGE_RATIO = 1.5            # required margin of predicted sell over effective buy cost
DEFAULT_PW_ROUND_TRIP_EFFICIENCY = 0.85  # fraction — matches the EMHASS add-on's ~85%
PRICE_WINDOW_STALE_MINUTES = 10          # Amber Express data older than this → stand down

# Options-flow fields for the price window step, in display order, with their defaults
# as text.  Blank means "not set".
PRICE_WINDOW_FIELDS: dict[str, str] = {
    CONF_ENTITY_PW_ENABLE: "",
    CONF_ENTITY_PW_SHADOW: "",
    CONF_ENTITY_PW_FEED_IN: "",
    CONF_ENTITY_PW_GENERAL: "",
    CONF_ENTITY_PW_HOUSE_LOAD: "",
    CONF_PW_SPIKE_THRESHOLD: str(DEFAULT_PW_SPIKE_THRESHOLD),
    CONF_PW_LOOKAHEAD_HOURS: str(DEFAULT_PW_LOOKAHEAD_HOURS),
    CONF_PW_RESERVE_MARGIN_HOURS: str(DEFAULT_PW_RESERVE_MARGIN_HOURS),
    CONF_PW_RESERVE_UNTIL: "",
    CONF_PW_MIN_SELL_PRICE: str(DEFAULT_PW_MIN_SELL_PRICE),
    CONF_PW_CHARGE_RATIO: str(DEFAULT_PW_CHARGE_RATIO),
    CONF_PW_MAX_CHARGE_BUY_PRICE: "",
    CONF_PW_ROUND_TRIP_EFFICIENCY: str(DEFAULT_PW_ROUND_TRIP_EFFICIENCY),
}

# ── Defaults ──────────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Add the modes and the data field**

Edit 1 of 3 in `custom_components/grid_coordinator/models.py`. Find (occurs exactly once):

```python
from enum import StrEnum
```

Replace with:

```python
from enum import StrEnum

from .price_window import PriceWindowDecision
```

Edit 2 of 3 in `custom_components/grid_coordinator/models.py`. Find (occurs exactly once):

```python
    # Manual override modes (set via service call or override select entity)
```

Replace with:

```python
    # Price window in control and no constraint binding (see price_window.py)
    PRICE_WINDOW_EXPORT = "price_window_export"
    PRICE_WINDOW_HOLD = "price_window_hold"
    PRICE_WINDOW_CHARGE = "price_window_charge"
    # Manual override modes (set via service call or override select entity)
```

Edit 3 of 3 in `custom_components/grid_coordinator/models.py`. Find (occurs exactly once):

```python
    ev_throttle_active: bool = False        # True while the coordinator is capping EV charge current
```

Replace with:

```python
    ev_throttle_active: bool = False        # True while the coordinator is capping EV charge current
    price_window: PriceWindowDecision | None = None  # this tick's price window decision and reason
```

- [ ] **Step 3: Run the whole suite**

Run: `python3 -m pytest tests -q`
Expected: `198 passed` (the 152 existing tests plus Tasks 1 to 3).

- [ ] **Step 4: Commit**

```bash
git add custom_components/grid_coordinator/const.py custom_components/grid_coordinator/models.py
git commit -m "feat: price window config keys, modes and coordinator data field

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 5: Evaluate and report every tick (no control yet)

**Files:**
- Modify: `custom_components/grid_coordinator/coordinator.py`
- Test: `tests/test_coordinator_price_window.py` (create)

**Interfaces:**
- Consumes: Tasks 1, 2 and 4.
- Produces: `GridCoordinator._evaluate_price_window(now: datetime) -> PriceWindowDecision`; `GridCoordinator._pw_batteries() -> tuple[PriceWindowEnergy, float, float]` (energy, combined max discharge W, combined max charge W); `self._pw_decision`, `self._pw_control`, `self._pw_last_action`; the old `_async_update_data` body becomes `_async_tick()`, and `_async_update_data` wraps it so every returned `CoordinatorData` carries `price_window`. Module-level `_PRICE_WINDOW_MODES` and `_UNBOUND_MODES`.

After this task the price window is worked out and reported on every tick but never changes a command. Task 6 gives it control.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coordinator_price_window.py`. The `Rig` builds a real `GridCoordinator` on a dict of fake states with the inverter writes mocked; Task 6 reuses it.

```python
"""Price window wiring in the coordinator tick (no running HA required).

A real GridCoordinator runs against a dict of fake states; the inverter writes are
mocks.  Each test sets up prices and batteries, runs ticks, and checks what was
commanded and reported.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.grid_coordinator.const import (
    CONF_ENTITY_ENABLED,
    CONF_ENTITY_EV_CHARGER,
    CONF_ENTITY_GRID_POWER,
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_MPC_BATT_POWER,
    CONF_ENTITY_MPC_GRID_POWER,
    CONF_ENTITY_PW_ENABLE,
    CONF_ENTITY_PW_FEED_IN,
    CONF_ENTITY_PW_GENERAL,
    CONF_ENTITY_PW_HOUSE_LOAD,
    CONF_ENTITY_PW_SHADOW,
    CONF_ENTITY_SOC_MAX,
    CONF_ENTITY_SOC_MIN,
    CONF_ENTITY_SOLAX_CAPACITY,
    CONF_ENTITY_SOLAX_SOC,
    CONF_ENTITY_SOLAX_SOC_MAX,
    CONF_ENTITY_SOLAX_SOC_MIN,
    CONF_ENTITY_VOLTX_BATTERY_POWER,
    CONF_ENTITY_VOLTX_CAPACITY,
    CONF_ENTITY_VOLTX_MAX_CHARGE,
    CONF_ENTITY_VOLTX_MAX_DISCHARGE,
    CONF_ENTITY_VOLTX_SOC,
    CONF_PW_MAX_CHARGE_BUY_PRICE,
    CONF_PW_RESERVE_UNTIL,
    CONF_PW_SPIKE_THRESHOLD,
    ENTITY_ID_DEFAULTS,
)
from custom_components.grid_coordinator.coordinator import GridCoordinator
from custom_components.grid_coordinator.models import CoordinatorMode, SolaxMode

E = ENTITY_ID_DEFAULTS
FEED = "sensor.amber_express_home_feed_in_price"
GENERAL = "sensor.amber_express_home_general_price"
LOAD = "sensor.house_load_expected"
ENABLE = "input_boolean.price_window_enabled"
SHADOW = "input_boolean.price_window_shadow"
EV = "sensor.ev_charger_power"


class _States:
    def __init__(self) -> None:
        self.values: dict[str, SimpleNamespace] = {}

    def set(self, entity_id, state, attributes=None, age_s=0.0):
        self.values[entity_id] = SimpleNamespace(
            state=str(state),
            attributes=attributes or {},
            last_updated=datetime.now(UTC) - timedelta(seconds=age_s),
        )

    def get(self, entity_id):
        return self.values.get(entity_id)


def _forecast(prices, *, confirmed_first=True, high=None):
    """detailedForecast for 5-minute intervals starting at the current one."""
    now = datetime.now(UTC)
    start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
    entries = []
    for index, price in enumerate(prices):
        begin = start + timedelta(minutes=5 * index)
        estimate = not (confirmed_first and index == 0)
        entry = {
            "per_kwh": price,
            "start_time": (begin + timedelta(seconds=1)).isoformat(),
            "end_time": (begin + timedelta(minutes=5)).isoformat(),
            "spike_status": "none",
            "estimate": estimate,
        }
        if estimate:
            entry["advanced_price_predicted"] = {
                "low": price / 2, "predicted": price, "high": price if high is None else high,
            }
        entries.append(entry)
    return {"detailedForecast": entries}


class Rig:
    """A GridCoordinator on fake states, with the inverter writes mocked."""

    def __init__(self, *, options=None, sell=(10.48, 0.64, 10.0), buy=None, high=None,
                 confirmed_first=True, shadow="off", solax=True):
        self.states = _States()
        s = self.states.set
        s(E[CONF_ENTITY_ENABLED], "on")
        s(E[CONF_ENTITY_GRID_POWER], 800)
        s(E[CONF_ENTITY_MPC_GRID_POWER], 0)
        s(E[CONF_ENTITY_MPC_BATT_POWER], 800)
        s(E[CONF_ENTITY_VOLTX_SOC], 80)
        s(E[CONF_ENTITY_SOC_MIN], 20)
        s(E[CONF_ENTITY_SOC_MAX], 95)
        s(E[CONF_ENTITY_VOLTX_CAPACITY], 50)
        s(E[CONF_ENTITY_VOLTX_MAX_CHARGE], 9600)
        s(E[CONF_ENTITY_VOLTX_MAX_DISCHARGE], 10000)
        s(E[CONF_ENTITY_VOLTX_BATTERY_POWER], 0)
        s(E[CONF_ENTITY_SOLAX_SOC], 80)
        s(E[CONF_ENTITY_SOLAX_SOC_MIN], 20)
        s(E[CONF_ENTITY_SOLAX_SOC_MAX], 95)
        s(E[CONF_ENTITY_SOLAX_CAPACITY], 10)
        s(LOAD, 1000)
        s(ENABLE, "on")
        s(SHADOW, shadow)
        s(EV, 0)
        self.prices(sell, buy=buy, high=high, confirmed_first=confirmed_first)
        opts = {
            CONF_ENTITY_PW_ENABLE: ENABLE,
            CONF_ENTITY_PW_SHADOW: SHADOW,
            CONF_ENTITY_PW_FEED_IN: FEED,
            CONF_ENTITY_PW_GENERAL: GENERAL,
            CONF_ENTITY_PW_HOUSE_LOAD: LOAD,
            CONF_ENTITY_EV_CHARGER: EV,
            CONF_ENTITY_MON_LOAD_1: "",
            CONF_ENTITY_SOLAX_SOC: E[CONF_ENTITY_SOLAX_SOC] if solax else "",
        }
        opts.update(options or {})
        hass = SimpleNamespace(
            states=self.states,
            config=SimpleNamespace(time_zone="Australia/Sydney"),
            services=SimpleNamespace(async_call=AsyncMock()),
        )
        # The real DataUpdateCoordinator needs a running Home Assistant; none of what
        # it sets up is used by a tick, so skip it (the bare-interpreter stub is a no-op).
        with patch.object(DataUpdateCoordinator, "__init__", lambda self, *a, **k: None):
            c = GridCoordinator(hass, SimpleNamespace(options=opts, data={}))
        c.hass = hass
        self.voltx = c._async_write_voltx = AsyncMock()
        self.native = c._async_enter_self_consumption = AsyncMock()
        c._async_set_work_mode = AsyncMock(return_value=False)
        c._async_enter_solax_self_consumption = AsyncMock()
        c._async_release_ev_throttle = AsyncMock()
        c._async_ev_emergency_throttle = AsyncMock(return_value=(None, False))

        async def write_solax(command):
            c._solax_last_written_cmd = command
            c._solax_active = command != 0.0

        self.solax = c._async_write_solax = AsyncMock(side_effect=write_solax)
        self.c = c
        self._loop = asyncio.new_event_loop()

    def prices(self, sell, *, buy=None, high=None, confirmed_first=True, age_s=0.0):
        buy = [p + 0.11 for p in sell] if buy is None else buy
        self.states.set(FEED, sell[0], _forecast(sell, confirmed_first=confirmed_first, high=high), age_s)
        self.states.set(GENERAL, buy[0], _forecast(buy, confirmed_first=confirmed_first, high=high), age_s)

    def tick(self):
        # A private loop: asyncio.run() leaves the thread with no current event loop on
        # Python 3.14, which breaks later tests when HA's pytest plugins are installed.
        return self._loop.run_until_complete(self.c._async_update_data())

    def settle(self, data):
        """Pretend the inverters did what they were told: move the grid reading."""
        house = 800.0
        self.states.set(E[CONF_ENTITY_GRID_POWER], house - data.voltx_command - data.solax_command)
        self.states.set(E[CONF_ENTITY_VOLTX_BATTERY_POWER], data.voltx_command)


# ── who is in control ─────────────────────────────────────────────────────────


def test_no_window_follows_emhass():
    rig = Rig(sell=(0.33, 0.31, 0.30))
    data = rig.tick()
    assert data.price_window.code == "no_window"
    assert data.grid_target == 0
    assert data.mode in (CoordinatorMode.SELF_CONSUMPTION, CoordinatorMode.EMHASS_TRACKING)


def test_shadow_reports_but_leaves_the_grid_target_alone():
    rig = Rig(shadow="on")
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert data.price_window.shadow is True
    assert data.price_window.would_command_w == -10000
    assert data.grid_target == 0  # the EMHASS target
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    rig.voltx.assert_not_awaited()


def test_shadow_is_on_when_no_shadow_entity_is_configured():
    rig = Rig(options={CONF_ENTITY_PW_SHADOW: ""})
    assert rig.tick().price_window.shadow is True


def test_shadow_is_on_when_the_shadow_entity_is_unavailable():
    rig = Rig(shadow="unavailable")
    assert rig.tick().price_window.shadow is True


def test_manual_override_still_wins():
    rig = Rig()
    rig.c.set_override("hold_soc")
    data = rig.tick()
    assert data.mode == CoordinatorMode.OVERRIDE_HOLD_SOC
    assert data.override_mode == "hold_soc"
    assert data.price_window.code == "export_allocated"  # still reported


def test_disabled_gate_still_wins():
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_ENABLED], "off")
    data = rig.tick()
    assert data.mode == CoordinatorMode.DISABLED
    rig.voltx.assert_not_awaited()


def test_switched_off_reports_disabled():
    rig = Rig()
    rig.states.set(ENABLE, "off")
    data = rig.tick()
    assert data.price_window.code == "disabled"
    assert data.grid_target == 0


@pytest.mark.parametrize("missing", [CONF_ENTITY_PW_ENABLE, CONF_ENTITY_PW_FEED_IN,
                                     CONF_ENTITY_PW_GENERAL, CONF_ENTITY_PW_HOUSE_LOAD])
def test_unconfigured_reports_disabled(missing):
    assert Rig(options={missing: ""}).tick().price_window.code == "disabled"


# ── stale data stands down to EMHASS ──────────────────────────────────────────


def _stale_cases():
    def unavailable(rig):
        rig.states.set(FEED, "unavailable")

    def missing_attribute(rig):
        rig.states.set(GENERAL, 0.75, {})

    def old(rig):
        rig.prices((10.48, 0.64, 10.0), age_s=11 * 60)

    def gone(rig):
        del rig.states.values[FEED]

    def no_load(rig):
        rig.states.set(LOAD, "unknown")

    def garbage(rig):
        rig.states.set(FEED, 10.48, {"detailedForecast": [{"start_time": 5}, "x", None]})

    return [unavailable, missing_attribute, old, gone, no_load, garbage]


@pytest.mark.parametrize("breakage", _stale_cases(), ids=lambda f: f.__name__)
def test_stale_amber_express_data_falls_through_to_tracking(breakage):
    rig = Rig()
    breakage(rig)
    data = rig.tick()
    assert data.price_window.code == "data_stale"
    assert data.price_window.action is None
    assert data.grid_target == 0  # the EMHASS target, not the price window's


def test_data_ten_minutes_old_is_still_used():
    rig = Rig()
    rig.prices((10.48, 0.64, 10.0), age_s=9 * 60)
    assert rig.tick().price_window.code == "export_allocated"


# ── settings ──────────────────────────────────────────────────────────────────


def test_settings_accept_a_literal_or_a_helper_entity():
    # 64c now with nothing above $1 ahead: no window at the default threshold...
    rig = Rig(sell=(0.64, 0.70, 0.70))
    assert rig.tick().price_window.code == "no_window"
    # ...a literal 0.5 opens one...
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "0.5"})
    assert rig.tick().price_window.action == "export"
    # ...and so does a helper entity holding 0.5.
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "input_number.spike"})
    rig.states.set("input_number.spike", 0.5)
    assert rig.tick().price_window.action == "export"


def test_unreadable_setting_helper_falls_back_to_the_default():
    rig = Rig(sell=(0.64, 0.70, 0.70), options={CONF_PW_SPIKE_THRESHOLD: "input_number.spike"})
    rig.states.set("input_number.spike", "unavailable")
    assert rig.tick().price_window.code == "no_window"


@pytest.mark.parametrize("setting, state", [
    ("06:30", None),
    ("input_datetime.reserve_until", "06:30:00"),
    ("input_datetime.reserve_until", "2026-10-14 06:30:00"),
])
def test_reserve_until_accepts_a_time_or_an_input_datetime(setting, state):
    rig = Rig(options={CONF_PW_RESERVE_UNTIL: setting})
    if state is not None:
        rig.states.set(setting, state)
    decision = rig.tick().price_window
    assert decision.reserve_basis == "clock_time"
    assert (decision.reserve_horizon.hour, decision.reserve_horizon.minute) == (6, 30)
    assert str(decision.reserve_horizon.tzinfo) == "Australia/Sydney"


def test_unreadable_reserve_until_falls_back_to_the_margin():
    rig = Rig(options={CONF_PW_RESERVE_UNTIL: "input_datetime.missing"})
    assert rig.tick().price_window.reserve_basis == "forecast_margin"


# ── logging ───────────────────────────────────────────────────────────────────


def test_each_change_of_reason_code_is_logged_once(caplog):
    rig = Rig()
    with caplog.at_level("INFO", logger="custom_components.grid_coordinator"):
        rig.tick()
        rig.tick()
        rig.prices((0.17, 10.0, 10.0), high=12.0)
        rig.tick()
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("price window:")]
    assert len(lines) == 2
    assert lines[0].startswith("price window: export_allocated — Exporting: $10.48 now")
    assert lines[1].startswith("price window: hold_below_min_price")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_coordinator_price_window.py -q`
Expected: most tests fail with `AttributeError: 'NoneType' object has no attribute 'code'` (or `'shadow'`, `'reserve_basis'`), because `data.price_window` is still `None`. Tests that only check existing behaviour (for example `test_disabled_gate_still_wins`) may already pass.

- [ ] **Step 3: Wire the evaluation into the coordinator**

Edit 1 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
from collections import deque
from datetime import UTC, datetime, timedelta
```

Replace with:

```python
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime, time as clock_time, timedelta
```

Edit 2 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
from typing import TYPE_CHECKING
```

Replace with:

```python
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo
```

Edit 3 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_SOC_MAX,
```

Replace with:

```python
    CONF_ENTITY_MON_LOAD_1,
    CONF_ENTITY_PW_ENABLE,
    CONF_ENTITY_PW_FEED_IN,
    CONF_ENTITY_PW_GENERAL,
    CONF_ENTITY_PW_HOUSE_LOAD,
    CONF_ENTITY_PW_SHADOW,
    CONF_ENTITY_SOC_MAX,
```

Edit 4 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    CONF_PLAN_STALE_MINUTES,
    CONF_RAMP_STEP,
```

Replace with:

```python
    CONF_PLAN_STALE_MINUTES,
    CONF_PW_CHARGE_RATIO,
    CONF_PW_LOOKAHEAD_HOURS,
    CONF_PW_MAX_CHARGE_BUY_PRICE,
    CONF_PW_MIN_SELL_PRICE,
    CONF_PW_RESERVE_MARGIN_HOURS,
    CONF_PW_RESERVE_UNTIL,
    CONF_PW_ROUND_TRIP_EFFICIENCY,
    CONF_PW_SPIKE_THRESHOLD,
    CONF_RAMP_STEP,
```

Edit 5 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_RAMP_STEP,
```

Replace with:

```python
    DEFAULT_PLAN_STALE_MINUTES,
    DEFAULT_PW_CHARGE_RATIO,
    DEFAULT_PW_LOOKAHEAD_HOURS,
    DEFAULT_PW_MIN_SELL_PRICE,
    DEFAULT_PW_RESERVE_MARGIN_HOURS,
    DEFAULT_PW_ROUND_TRIP_EFFICIENCY,
    DEFAULT_PW_SPIKE_THRESHOLD,
    DEFAULT_RAMP_STEP,
```

Edit 6 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    LOGGER,
    SOLAX_EXPORT_DURATION_SAFE,
```

Replace with:

```python
    LOGGER,
    PRICE_WINDOW_STALE_MINUTES,
    SOLAX_EXPORT_DURATION_SAFE,
```

Edit 7 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
from .models import CoordinatorData, CoordinatorMode, SolaxMode
```

Replace with:

```python
from .models import CoordinatorData, CoordinatorMode, SolaxMode
from .price_window import (
    ACTION_CHARGE,
    ACTION_EXPORT,
    ACTION_HOLD,
    CODE_DATA_STALE,
    CODE_DISABLED,
    PriceWindowDecision,
    PriceWindowEnergy,
    PriceWindowLimits,
    PriceWindowSettings,
    decide_price_window,
    inactive,
    parse_amber_express,
)
```

Edit 8 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
def _plan_age_minutes(hass: HomeAssistant, entity_id: str) -> float:
```

Replace with:

```python
_TRUTHY = ("on", "true", "yes", "1")

_PRICE_WINDOW_MODES = {
    ACTION_EXPORT: CoordinatorMode.PRICE_WINDOW_EXPORT,
    ACTION_HOLD: CoordinatorMode.PRICE_WINDOW_HOLD,
    ACTION_CHARGE: CoordinatorMode.PRICE_WINDOW_CHARGE,
}
# Modes that mean "nothing bound": while the price window is in control they are
# reported as the price window's own mode instead.
_UNBOUND_MODES = (
    CoordinatorMode.EMHASS_TRACKING,
    CoordinatorMode.SELF_CONSUMPTION,
    CoordinatorMode.STALE_PLAN,
)


def _parse_clock(text: str) -> clock_time | None:
    """Parse `HH:MM`, `HH:MM:SS` or `YYYY-MM-DD HH:MM:SS` (an input_datetime state)."""
    text = text.strip()
    if " " in text:
        text = text.rsplit(" ", 1)[1]
    try:
        return clock_time.fromisoformat(text)
    except ValueError:
        return None


def _plan_age_minutes(hass: HomeAssistant, entity_id: str) -> float:
```

Edit 9 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
        # Entities already warned about, so a missing control input logs once not 6×/min
```

Replace with:

```python
        # Price window: this tick's decision (None until the first tick), whether it is
        # in control, and the last action/code so a change is acted on and logged once.
        self._pw_decision: PriceWindowDecision | None = None
        self._pw_control: bool = False
        self._pw_last_action: str | None = None
        self._pw_last_code: str | None = None
        # Entities already warned about, so a missing control input logs once not 6×/min
```

Edit 10 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    # ── override control ──────────────────────────────────────────────────────
```

Replace with:

```python
    # ── price window ──────────────────────────────────────────────────────────

    def _pw_number(self, key: str, default: float) -> float:
        """A price window setting: a literal, or the state of a helper entity."""
        value = _float_or_entity(self.hass, str(self._opt(key, default)).strip(), default)
        return value if math.isfinite(value) else default

    def _pw_optional_number(self, key: str) -> float | None:
        """Like _pw_number, but blank or unreadable means "not set"."""
        raw = str(self._opt(key, "")).strip()
        if not raw:
            return None
        value = _float_or_entity(self.hass, raw, float("nan"))
        return value if math.isfinite(value) else None

    def _pw_clock(self, key: str) -> clock_time | None:
        """A clock-time setting: `HH:MM`, or an input_datetime entity; blank = not set."""
        raw = str(self._opt(key, "")).strip()
        if not raw:
            return None
        literal = _parse_clock(raw)
        if literal is not None:
            return literal
        return _parse_clock(_str(self.hass, raw, ""))

    def _pw_settings(self) -> PriceWindowSettings:
        return PriceWindowSettings(
            spike_threshold=self._pw_number(CONF_PW_SPIKE_THRESHOLD, DEFAULT_PW_SPIKE_THRESHOLD),
            lookahead_h=self._pw_number(CONF_PW_LOOKAHEAD_HOURS, DEFAULT_PW_LOOKAHEAD_HOURS),
            reserve_margin_h=self._pw_number(CONF_PW_RESERVE_MARGIN_HOURS, DEFAULT_PW_RESERVE_MARGIN_HOURS),
            reserve_until=self._pw_clock(CONF_PW_RESERVE_UNTIL),
            min_sell_price=self._pw_number(CONF_PW_MIN_SELL_PRICE, DEFAULT_PW_MIN_SELL_PRICE),
            charge_ratio=self._pw_number(CONF_PW_CHARGE_RATIO, DEFAULT_PW_CHARGE_RATIO),
            max_charge_buy_price=self._pw_optional_number(CONF_PW_MAX_CHARGE_BUY_PRICE),
            round_trip_efficiency=self._pw_number(
                CONF_PW_ROUND_TRIP_EFFICIENCY, DEFAULT_PW_ROUND_TRIP_EFFICIENCY
            ),
        )

    def _pw_batteries(self) -> tuple[PriceWindowEnergy, float, float]:
        """Energy above the SOC floors and the combined power limits of the batteries
        the coordinator may command.  Returns (energy, max_discharge_w, max_charge_w);
        the energy's house load is filled in by the caller.
        """
        hass = self.hass
        usable = 0.0
        max_discharge = 0.0
        max_charge = 0.0
        full: list[bool] = []
        if self._control_enabled(hass, CONF_ENTITY_VOLTX_CONTROL_ENABLE):
            soc = _float(hass, self._eid(CONF_ENTITY_VOLTX_SOC), 0.0)
            soc_min = _float(hass, self._eid(CONF_ENTITY_SOC_MIN), 20.0)
            soc_max = _float(hass, self._eid(CONF_ENTITY_SOC_MAX), 95.0)
            capacity = _float(hass, self._eid(CONF_ENTITY_VOLTX_CAPACITY), 0.0)
            usable += capacity * max(0.0, soc - soc_min) / 100
            max_discharge += _float(hass, self._eid(CONF_ENTITY_VOLTX_MAX_DISCHARGE), 5000.0)
            max_charge += _float(hass, self._eid(CONF_ENTITY_VOLTX_MAX_CHARGE), 5000.0)
            full.append(soc >= soc_max)
        if self._solax_enabled() and self._control_enabled(hass, CONF_ENTITY_SOLAX_CONTROL_ENABLE):
            soc = _float(hass, self._eid(CONF_ENTITY_SOLAX_SOC), 0.0)
            soc_min = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MIN), 20.0)
            soc_max = _float_or_entity(hass, self._eid(CONF_ENTITY_SOLAX_SOC_MAX), 95.0)
            capacity = _float(hass, self._eid(CONF_ENTITY_SOLAX_CAPACITY), 0.0)
            usable += capacity * max(0.0, soc - soc_min) / 100
            max_discharge += float(self._opt(CONF_SOLAX_MAX_DISCHARGE, DEFAULT_SOLAX_MAX_DISCHARGE))
            max_charge += float(self._opt(CONF_SOLAX_MAX_CHARGE, DEFAULT_SOLAX_MAX_CHARGE))
            full.append(soc >= soc_max)
        energy = PriceWindowEnergy(
            usable_kwh=usable, house_load_w=0.0, at_soc_ceiling=bool(full) and all(full)
        )
        return energy, max_discharge, max_charge

    def _evaluate_price_window(self, now: datetime) -> PriceWindowDecision:
        """Read Amber Express and the batteries, and decide.  Never raises: anything
        missing or unreadable returns an inactive decision that says why, and EMHASS
        tracking carries on.
        """
        hass = self.hass
        enable = str(self._opt(CONF_ENTITY_PW_ENABLE, "")).strip()
        feed_in = str(self._opt(CONF_ENTITY_PW_FEED_IN, "")).strip()
        general = str(self._opt(CONF_ENTITY_PW_GENERAL, "")).strip()
        load = str(self._opt(CONF_ENTITY_PW_HOUSE_LOAD, "")).strip()
        if not (enable and feed_in and general and load):
            return inactive(CODE_DISABLED, "Price window is not configured.")
        if _str(hass, enable, "off").strip().lower() not in _TRUTHY:
            return inactive(CODE_DISABLED, "Price window is switched off.")

        attrs = []
        for entity_id in (feed_in, general):
            state = hass.states.get(entity_id)
            if state is None or state.state in ("unavailable", "unknown", ""):
                return inactive(CODE_DATA_STALE, f"{entity_id} is unavailable.")
            age_min = (now - state.last_updated).total_seconds() / 60
            if age_min > PRICE_WINDOW_STALE_MINUTES:
                return inactive(
                    CODE_DATA_STALE, f"{entity_id} last updated {age_min:.0f} min ago."
                )
            attrs.append(state.attributes)
        house_load = _float(hass, load, float("nan"))
        if not math.isfinite(house_load):
            return inactive(CODE_DATA_STALE, f"Expected house load {load} is unavailable.")

        try:
            local_now = now.astimezone(ZoneInfo(str(hass.config.time_zone)))
        except Exception:  # noqa: BLE001 — an unknown zone must not stop the control loop
            local_now = now
        intervals = parse_amber_express(attrs[0], attrs[1], local_now)
        energy, _max_discharge, max_charge = self._pw_batteries()
        decision = decide_price_window(
            intervals,
            local_now,
            replace(energy, house_load_w=house_load),
            PriceWindowLimits(
                export_w=self._export_limit, charge_w=min(self._import_limit, max_charge)
            ),
            self._pw_settings(),
        )
        # Shadow is on unless the shadow entity exists and reads off: a new install
        # (or a missing helper) reports what it would do without doing it.
        shadow_entity = str(self._opt(CONF_ENTITY_PW_SHADOW, "")).strip()
        shadow_state = _str(hass, shadow_entity, "on").strip().lower() if shadow_entity else "on"
        return replace(decision, shadow=shadow_state != "off")

    # ── override control ──────────────────────────────────────────────────────
```

Edit 11 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    async def _async_update_data(self) -> CoordinatorData:
        """Called every UPDATE_INTERVAL_SECONDS by the DataUpdateCoordinator."""
        hass = self.hass
```

Replace with:

```python
    async def _async_update_data(self) -> CoordinatorData:
        """Called every UPDATE_INTERVAL_SECONDS by the DataUpdateCoordinator.

        Evaluates the price window first, so its decision and reason are reported on
        every tick — including disabled, override and shadow ticks, where it does not
        control — then runs the tick.
        """
        try:
            decision = self._evaluate_price_window(datetime.now(UTC))
        except Exception:  # noqa: BLE001 — reporting must never take the control loop down
            LOGGER.exception("price window evaluation failed; following EMHASS")
            decision = inactive(CODE_DATA_STALE, "Price window evaluation failed; see the log.")
        if decision.code != self._pw_last_code:
            LOGGER.info(
                "price window: %s%s — %s",
                decision.code,
                " (shadow)" if decision.shadow and decision.action else "",
                decision.sentence,
            )
            self._pw_last_code = decision.code
        self._pw_decision = decision
        self._pw_control = False
        data = await self._async_tick()
        if not self._pw_control:
            # Not in control this tick (inactive, shadow, disabled gate or a manual
            # override): the next tick that is counts as a change of decision.
            self._pw_last_action = None
        elif data.mode in _UNBOUND_MODES:
            data = replace(data, mode=_PRICE_WINDOW_MODES[decision.action])
        return replace(data, price_window=decision)

    async def _async_tick(self) -> CoordinatorData:
        """One control tick: disabled gate, manual override, price window, EMHASS."""
        hass = self.hass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests -q`
Expected: `223 passed`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/grid_coordinator/coordinator.py tests/test_coordinator_price_window.py
git commit -m "feat: evaluate and report the price window every tick

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 6: Let the decision control the tick

**Files:**
- Modify: `custom_components/grid_coordinator/coordinator.py`
- Test: `tests/test_coordinator_price_window.py` (append)

**Interfaces:**
- Consumes: Task 3's `window_setpoints`; Task 5's `self._pw_decision`, `self._pw_control`, `self._pw_last_action`, `_pw_batteries()`.
- Produces: inside `_async_tick`, when the decision has an action and is not shadow, the grid target and battery setpoint come from `window_setpoints`; `_async_voltx_disabled_tick` gains keyword arguments `ramp_step: float`, `tier2_gain: float`, `allow_grid_priority: bool`.

What the edits do, in order: (1) import `window_setpoints`; (2) the control block, placed after the EV reserve is read and before the self-consumption decision; (3) a price window decision bypasses the handoff's minimum dwell; (4) to (6) and (8) to (10) pass the ramp bypass, tier 2 off and grid-priority off into both the normal path and the Voltx-control-off path; (7) the signature for that.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator_price_window.py`:

```python
# ── export ────────────────────────────────────────────────────────────────────


def test_export_commands_both_batteries_on_the_first_tick():
    rig = Rig()
    data = rig.tick()
    assert data.price_window.code == "export_allocated"
    assert data.grid_target == -10000
    # 800 W of house plus 10 kW of export, split by capacity: no ramp from zero.
    assert data.voltx_command == 9000
    assert data.solax_command == 1800
    assert data.solax_mode == SolaxMode.FORCE_DISCHARGE
    assert data.mode == CoordinatorMode.PRICE_WINDOW_EXPORT
    rig.voltx.assert_awaited_once_with(9000)
    rig.solax.assert_awaited_once_with(1800)


def test_export_holds_the_limit_once_there():
    rig = Rig()
    rig.settle(rig.tick())
    data = rig.tick()
    assert (data.voltx_command, data.solax_command) == (9000, 1800)
    assert data.grid_actual == -10000


def test_export_without_solax_uses_voltx_alone():
    rig = Rig(solax=False)
    data = rig.tick()
    assert data.voltx_command == 10000  # all it has; 10.8 kW would reach the limit
    assert data.solax_command == 0


def test_binding_constraint_is_still_the_reported_mode():
    rig = Rig()
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 20)  # Voltx at its floor
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 90)
    data = rig.tick()
    assert data.price_window.action == "export"
    assert data.mode == CoordinatorMode.SOC_FLOOR
    assert data.voltx_command == 0
    assert data.solax_command == 3000  # Solax takes what it can


# ── hold ──────────────────────────────────────────────────────────────────────


def test_hold_hands_the_house_to_native_self_consumption():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    data = rig.tick()
    assert data.price_window.code == "hold_below_min_price"
    assert data.grid_target == 0
    assert data.mode == CoordinatorMode.PRICE_WINDOW_HOLD
    rig.native.assert_awaited()
    rig.voltx.assert_not_awaited()


def test_spike_after_hold_exports_at_once_despite_the_handoff_dwell():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    rig.tick()  # hold: handoff starts, 120 s dwell begins
    rig.states.set(E[CONF_ENTITY_GRID_POWER], 0)
    rig.states.set(E[CONF_ENTITY_VOLTX_BATTERY_POWER], 800)  # Voltx carrying the house
    rig.prices((10.48, 0.64, 10.0))
    data = rig.tick()
    assert data.price_window.action == "export"
    assert data.voltx_command == 9000 and data.solax_command == 1800


def test_hold_while_the_ev_charges_keeps_the_house_off_the_grid():
    rig = Rig(sell=(0.17, 10.0, 10.0), high=12.0)
    rig.states.set(EV, 7000)
    rig.states.set(E[CONF_ENTITY_GRID_POWER], 7800)  # 7 kW car + 800 W house
    data = rig.tick()
    assert data.price_window.action == "hold"
    assert data.grid_target == 7000
    assert data.voltx_command + data.solax_command == 800
    assert data.mode == CoordinatorMode.PRICE_WINDOW_HOLD


# ── charge ────────────────────────────────────────────────────────────────────


def _short_rig(**kwargs):
    """Cheap now, spikes predicted ahead, batteries just above their floors."""
    rig = Rig(sell=(0.17, 9.80, 10.20), **kwargs)
    rig.states.set(E[CONF_ENTITY_VOLTX_SOC], 21)
    rig.states.set(E[CONF_ENTITY_SOLAX_SOC], 21)
    return rig


def test_charge_fills_the_import_limit_on_the_first_tick():
    rig = _short_rig()
    data = rig.tick()
    assert data.price_window.code == "charge_shortfall"
    assert data.grid_target == 12000
    # 12 kW import limit less the 800 W house, split by capacity.
    assert data.voltx_command + data.solax_command == pytest.approx(-11200, abs=2)
    assert data.voltx_command == pytest.approx(-9333, abs=2)
    assert data.mode == CoordinatorMode.PRICE_WINDOW_CHARGE


def test_charge_is_not_clamped_to_zero_by_grid_priority():
    rig = _short_rig(options={"grid_priority_band": 20000})
    assert rig.tick().voltx_command < -9000


def test_maximum_charge_buy_price_from_a_helper_entity_blocks_charging():
    rig = _short_rig(options={CONF_PW_MAX_CHARGE_BUY_PRICE: "input_number.max_buy"})
    rig.states.set("input_number.max_buy", 0.20)  # buying at 28c is over it
    data = rig.tick()
    assert data.price_window.code == "hold_charge_too_dear"
    assert data.grid_target == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_coordinator_price_window.py -q`
Expected: the 10 new tests fail (for example `assert 0 == -10000` on `grid_target`); the Task 5 tests still pass.

- [ ] **Step 3: Apply the decision in the tick**

Edit 1 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
    parse_amber_express,
)
```

Replace with:

```python
    parse_amber_express,
    window_setpoints,
)
```

Edit 2 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
        ev_reserve, ev_active = self._ev_headroom_reserve(hass)
        # Safety conditions bypass the dwell: a limit breach, Voltx control switched off or an
        # EV charging must leave the handoff immediately (the branch has no grid clamp).
```

Replace with:

```python
        ev_reserve, ev_active = self._ev_headroom_reserve(hass)

        # ── price window ───────────────────────────────────────────────────
        # During forecast price spikes the price window's decision replaces the EMHASS
        # setpoints, and everything below runs as it does for a plan: the same Voltx /
        # Solax split, grid limits, headroom and SOC limits.  The battery setpoint is
        # anchored on the uncontrolled grid power, which already reaches the target in
        # one step, so tier 2 and transient damping are switched off for the tick (the
        # price window's own steps are not a cycling load), and plan staleness does not
        # apply because the plan is not being followed.
        pw = self._pw_decision
        pw_action = pw.action if (pw is not None and not pw.shadow) else None
        pw_changed = False
        if pw_action is not None:
            self._pw_control = True
            pw_changed = pw_action != self._pw_last_action
            self._pw_last_action = pw_action
            _, pw_max_discharge, pw_max_charge = self._pw_batteries()
            # In the native handoff prev_cmd is 0 while Voltx is in fact working.  Seed it
            # from the measured power so the uncontrolled grid estimate (here and in
            # compute_voltx_command) is right on the tick the handoff ends; if the
            # handoff continues, its branch below zeroes prev_cmd again.
            if self._sc_state.active and voltx_power_raw is not None:
                self._prev_cmd = voltx_power_raw
            ev_power = 0.0
            if ev_active:
                ev_power = max(0.0, _float(hass, str(self._opt(CONF_ENTITY_EV_CHARGER, ENTITY_EV_CHARGER)), 0.0))
            grid_target, mpc_batt_cmd = window_setpoints(
                pw_action,
                grid_uncontrolled=grid_actual + self._prev_cmd + self._solax_last_written_cmd,
                export_limit=self._export_limit,
                import_limit=self._import_limit,
                headroom_reserve=ev_reserve,
                max_discharge=pw_max_discharge,
                max_charge=pw_max_charge,
                ev_power=ev_power,
            )
            plan_is_stale = False
            transient_active = False
            effective_target = grid_target
            effective_mpc_batt = mpc_batt_cmd

        # Safety conditions bypass the dwell: a limit breach, Voltx control switched off or an
        # EV charging must leave the handoff immediately (the branch has no grid clamp).
```

Edit 3 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
            bypass_lockout=plan_is_stale,
        )
```

Replace with:

```python
            # A price window decision takes effect at once: the dwell exists to stop an
            # EMHASS target hovering at the deadband from flapping, which this cannot do.
            bypass_lockout=plan_is_stale or pw_action is not None,
        )
```

Edit 4 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
                transient_active=transient_active,
                solax_on=solax_on,
            )
```

Replace with:

```python
                transient_active=transient_active,
                solax_on=solax_on,
                ramp_step=float("inf") if pw_changed else self._ramp_step,
                tier2_gain=0.0 if pw_action is not None else self._tier2_gain,
                allow_grid_priority=pw_action is None,
            )
```

Edit 5 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
        grid_priority = self._grid_priority_active(hass, effective_target)
        if grid_priority:
            effective_target = min(effective_target, 0.0)

        # ── compute Solax share
```

Replace with:

```python
        # (Not during a price window: its setpoint is already deadbeat, and the clamp
        # below would turn a charge target into zero.)
        grid_priority = pw_action is None and self._grid_priority_active(hass, effective_target)
        if grid_priority:
            effective_target = min(effective_target, 0.0)

        # ── compute Solax share
```

Edit 6 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
        effective_tier2_gain = 0.0 if ev_active else self._tier2_gain
        # Voltx's own tier-2 gain shrinks
```

Replace with:

```python
        effective_tier2_gain = 0.0 if (ev_active or pw_action is not None) else self._tier2_gain
        # On the tick a price window decision changes, the new setpoint is commanded
        # directly: a 5-minute spike cannot wait 30 s for the ramp.
        ramp_step = float("inf") if pw_changed else self._ramp_step
        # Voltx's own tier-2 gain shrinks
```

Edit 7 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
            export_limit=self._export_limit,
            ramp_step=self._ramp_step,
            plan_is_stale=plan_is_stale,
            tracking_deadband=self._tracking_deadband,
            headroom_reserve=headroom_reserve,
            tier2_gain=voltx_tier2_gain,
```

Replace with:

```python
            export_limit=self._export_limit,
            ramp_step=ramp_step,
            plan_is_stale=plan_is_stale,
            tracking_deadband=self._tracking_deadband,
            headroom_reserve=headroom_reserve,
            tier2_gain=voltx_tier2_gain,
```

Edit 8 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
        transient_active: bool,
        solax_on: bool,
    ) -> CoordinatorData:
        """One tick when Voltx (primary) control is switched off.
```

Replace with:

```python
        transient_active: bool,
        solax_on: bool,
        ramp_step: float,
        tier2_gain: float,
        allow_grid_priority: bool,
    ) -> CoordinatorData:
        """One tick when Voltx (primary) control is switched off.
```

Edit 9 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
            grid_priority = self._grid_priority_active(hass, effective_target)
            # See the equivalent clamp in the Voltx-enabled path (async_update_data):
```

Replace with:

```python
            grid_priority = allow_grid_priority and self._grid_priority_active(hass, effective_target)
            # See the equivalent clamp in the Voltx-enabled path (async_update_data):
```

Edit 10 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
            effective_tier2_gain = 0.0 if ev_active else self._tier2_gain
            solax_cmd, s_mode, _diag = compute_voltx_command(
```

Replace with:

```python
            effective_tier2_gain = 0.0 if ev_active else tier2_gain
            solax_cmd, s_mode, _diag = compute_voltx_command(
```

Edit 11 of 11 in `custom_components/grid_coordinator/coordinator.py`. Find (occurs exactly once):

```python
                export_limit=self._export_limit,
                ramp_step=self._ramp_step,
                plan_is_stale=plan_is_stale,
                tracking_deadband=self._tracking_deadband,
                headroom_reserve=headroom_reserve,
                tier2_gain=effective_tier2_gain,
```

Replace with:

```python
                export_limit=self._export_limit,
                ramp_step=ramp_step,
                plan_is_stale=plan_is_stale,
                tracking_deadband=self._tracking_deadband,
                headroom_reserve=headroom_reserve,
                tier2_gain=effective_tier2_gain,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests -q`
Expected: `233 passed`.

- [ ] **Step 5: Commit**

```bash
git add custom_components/grid_coordinator/coordinator.py tests/test_coordinator_price_window.py
git commit -m "feat: price window decision replaces the EMHASS setpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 7: Sensors, options step and translations

**Files:**
- Modify: `custom_components/grid_coordinator/sensor.py`
- Modify: `custom_components/grid_coordinator/config_flow.py`
- Modify: `custom_components/grid_coordinator/translations/en.json`
- Test: `tests/test_translations.py` (append)
- Test: `scripts/ha_smoke/test_smoke_ha.py` (append, and two small edits)

**Interfaces:**
- Consumes: `CoordinatorData.price_window`, `decision_attributes`, `PRICE_WINDOW_FIELDS`.
- Produces: sensors `sensor.grid_coordinator_price_window_reason` (state = reason code; attributes = `decision_attributes`), `..._price_window_end` (timestamp), `..._price_window_house_reserve` (kWh), `..._price_window_surplus` (kWh); step id `price_window` after `solax` in both flows.

`sensor.py` and `config_flow.py` import real Home Assistant modules, so the bare-interpreter suite cannot load them. The translation test runs there; the sensors and the flow are tested by the smoke suite in Docker.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_translations.py`:

```python
# ── price window step ─────────────────────────────────────────────────────────

def test_every_price_window_field_is_labelled_in_both_flows():
    from custom_components.grid_coordinator.const import PRICE_WINDOW_FIELDS

    for flow in ("config", "options"):
        step = _step(flow, "price_window")
        assert step["title"] and step["description"]
        assert set(step["data"]) == set(PRICE_WINDOW_FIELDS), flow
        assert set(step["data_description"]) == set(PRICE_WINDOW_FIELDS), flow
        assert all(step["data"].values()) and all(step["data_description"].values())


def test_price_window_field_defaults():
    from custom_components.grid_coordinator.const import PRICE_WINDOW_FIELDS as F

    assert F["price_window_spike_threshold"] == "1.0"
    assert F["price_window_lookahead_hours"] == "4.0"
    assert F["price_window_reserve_margin_hours"] == "2.0"
    assert F["price_window_min_sell_price"] == "0.3"
    assert F["price_window_charge_ratio"] == "1.5"
    assert F["price_window_round_trip_efficiency"] == "0.85"
    # Unset by default: the feature is off, shadow is on, no clock time, no buy cap.
    for key in (
        "entity_price_window_enable", "entity_price_window_shadow",
        "entity_price_window_feed_in", "entity_price_window_general",
        "entity_price_window_house_load", "price_window_reserve_until",
        "price_window_max_charge_buy_price",
    ):
        assert F[key] == "", key
```

Append to `scripts/ha_smoke/test_smoke_ha.py`:

```python
# ── price window on real HA ───────────────────────────────────────────────────

PW_FEED = "sensor.amber_express_home_feed_in_price"
PW_GENERAL = "sensor.amber_express_home_general_price"
PW_OPTIONS = {
    "entity_price_window_enable": "input_boolean.pw_enable",
    "entity_price_window_shadow": "input_boolean.pw_shadow",
    "entity_price_window_feed_in": PW_FEED,
    "entity_price_window_general": PW_GENERAL,
    "entity_price_window_house_load": "sensor.pw_house_load",
}


def _pw_seed(hass, sell, *, shadow="off"):
    """Seed Amber Express the way it really reports: UTC strings, start one second late."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    now = dt_util.utcnow()
    start = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)

    def forecast(prices):
        entries = []
        for index, price in enumerate(prices):
            begin = start + timedelta(minutes=5 * index)
            entry = {
                "per_kwh": price,
                "start_time": (begin + timedelta(seconds=1)).isoformat(),
                "end_time": (begin + timedelta(minutes=5)).isoformat(),
                "spike_status": "none",
                "estimate": index > 0,
            }
            if index > 0:
                entry["advanced_price_predicted"] = {"low": price / 2, "predicted": price, "high": price}
            entries.append(entry)
        return {"detailedForecast": entries}

    hass.states.async_set(PW_FEED, str(sell[0]), forecast(sell))
    hass.states.async_set(PW_GENERAL, str(sell[0] + 0.11), forecast([p + 0.11 for p in sell]))
    hass.states.async_set("input_boolean.pw_enable", "on")
    hass.states.async_set("input_boolean.pw_shadow", shadow)
    hass.states.async_set("sensor.pw_house_load", "1000")


async def test_price_window_exports_and_reports_its_reason(hass):
    entry, coordinator, calls = await _setup(hass, grid=800.0, extra_options=PW_OPTIONS, voltx_soc=80.0)
    _pw_seed(hass, (10.48, 0.64, 10.0))
    data = await _tick(hass, coordinator)
    assert data.price_window.code == "export_allocated"
    assert data.grid_target == -10000
    assert data.voltx_command == 5000  # the seeded Voltx limit, reached in one tick
    assert [c.data["value"] for c in calls["number"]][-1] == "5000"

    reason = hass.states.get("sensor.grid_coordinator_price_window_reason")
    assert reason.state == "export_allocated"
    assert reason.attributes["reason"].startswith("Exporting: $10.48 now")
    assert reason.attributes["intervals"][0]["basis"] == "confirmed"
    assert reason.attributes["shadow"] is False
    assert hass.states.get("sensor.grid_coordinator_price_window_end").state not in ("unknown", "unavailable")
    assert float(hass.states.get("sensor.grid_coordinator_price_window_surplus").state) > 0
    assert float(hass.states.get("sensor.grid_coordinator_price_window_house_reserve").state) > 0


async def test_price_window_shadow_does_not_control(hass):
    entry, coordinator, calls = await _setup(hass, extra_options=PW_OPTIONS, voltx_soc=80.0)
    _pw_seed(hass, (10.48, 0.64, 10.0), shadow="on")
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert data.grid_target == 0
    reason = hass.states.get("sensor.grid_coordinator_price_window_reason")
    assert reason.state == "export_allocated"
    assert reason.attributes["shadow"] is True
    assert reason.attributes["would_command_w"] == -10000


async def test_price_window_unconfigured_changes_nothing(hass):
    entry, coordinator, _ = await _setup(hass)
    data = await _tick(hass, coordinator)
    assert data.mode == CoordinatorMode.SELF_CONSUMPTION
    assert hass.states.get("sensor.grid_coordinator_price_window_reason").state == "disabled"
    assert hass.states.get("sensor.grid_coordinator_price_window_end").state == "unknown"
    assert hass.states.get("sensor.grid_coordinator_price_window_surplus").state == "unknown"


async def test_options_flow_price_window_step_saves_literals_and_entities(hass):
    entry, coordinator, _ = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for _ in range(3):  # init -> entities -> solax -> price_window
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "price_window"
    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    shown = {f["name"]: f["description"]["suggested_value"] for f in fields}
    assert shown["price_window_spike_threshold"] == "1.0"
    assert shown["entity_price_window_enable"] == ""
    assert len(fields) == 13

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        # the frontend submits every prefilled field, plus what the user typed
        {**shown, **PW_OPTIONS, "price_window_spike_threshold": "input_number.pw_spike",
         "price_window_reserve_until": "06:30", "price_window_max_charge_buy_price": "1.00"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["price_window_spike_threshold"] == "input_number.pw_spike"
    assert entry.options["price_window_reserve_until"] == "06:30"
    assert entry.options["price_window_lookahead_hours"] == "4.0"

    # Re-open: the saved values come back as the defaults, and clearing one unsets it.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    for _ in range(3):
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    fields = voluptuous_serialize.convert(result["data_schema"], custom_serializer=cv.custom_serializer)
    shown = {f["name"]: f["description"]["suggested_value"] for f in fields}
    assert shown["price_window_reserve_until"] == "06:30"
    # The frontend leaves a cleared text field out of what it submits.
    saved = {name: value for name, value in shown.items() if name != "price_window_reserve_until"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], saved)
    await hass.async_block_till_done()
    assert entry.options["price_window_reserve_until"] == ""
```

The options flow gains a step, so two existing smoke tests need it. In `test_options_flow_exposes_and_saves_the_new_options`, find:

```python
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "solax"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
```

Replace with:

```python
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "solax"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "price_window"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
```

In `test_entry_carrying_removed_option_keys_still_works`, change `for _ in range(3):  # init -> entities -> solax -> create` to `for _ in range(4):  # init -> entities -> solax -> price_window -> create`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_translations.py -q`
Expected: `1 failed, 4 passed`. `test_every_price_window_field_is_labelled_in_both_flows` fails with `KeyError: 'price_window'`; `test_price_window_field_defaults` already passes because it reads `const.py` only.

Run: `scripts/ha_smoke/run`
Expected: `smoke rc=1`; the four `test_price_window_*` / `test_options_flow_price_window_*` tests and the two edited ones fail.

- [ ] **Step 3: Add the sensors**

Edit 1 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
from collections.abc import Callable
```

Replace with:

```python
from collections.abc import Callable
from datetime import datetime
```

Edit 2 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower
```

Replace with:

```python
from homeassistant.const import UnitOfElectricCurrent, UnitOfEnergy, UnitOfPower
```

Edit 3 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
from .models import CoordinatorData
```

Replace with:

```python
from .models import CoordinatorData
from .price_window import decision_attributes
```

Edit 4 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
    value_fn: Callable[[CoordinatorData], str | float | None]
```

Replace with:

```python
    value_fn: Callable[[CoordinatorData], str | float | datetime | None]
    attrs_fn: Callable[[CoordinatorData], dict | None] | None = None
```

Edit 5 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
        value_fn=lambda d: d.ev_current_limit,
    ),
)
```

Replace with:

```python
        value_fn=lambda d: d.ev_current_limit,
    ),
    # Price window.  The reason sensor's attributes hold the sentence and the working
    # (see price_window.decision_attributes); they are recorded, so a decision can be
    # checked afterwards against the forecast Amber Express showed at the time.
    GridSensorDescription(
        key="price_window_reason",
        name="Price Window Reason",
        icon="mdi:comment-question-outline",
        value_fn=lambda d: d.price_window.code if d.price_window else None,
        attrs_fn=lambda d: decision_attributes(d.price_window) if d.price_window else None,
    ),
    GridSensorDescription(
        key="price_window_end",
        name="Price Window End",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.price_window.window_end if d.price_window else None,
    ),
    GridSensorDescription(
        key="price_window_house_reserve",
        name="Price Window House Reserve",
        icon="mdi:home-battery",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: d.price_window.house_reserve_kwh if d.price_window and d.price_window.action else None,
    ),
    GridSensorDescription(
        key="price_window_surplus",
        name="Price Window Surplus",
        icon="mdi:battery-arrow-up-outline",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: d.price_window.surplus_kwh if d.price_window and d.price_window.action else None,
    ),
)
```

Edit 6 of 6 in `custom_components/grid_coordinator/sensor.py`. Find (occurs exactly once):

```python
    def native_value(self) -> str | float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
```

Replace with:

```python
    def native_value(self) -> str | float | datetime | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is None or self.coordinator.data is None:
            return None
        return attrs_fn(self.coordinator.data)
```

- [ ] **Step 4: Add the flow step**

Edit 1 of 5 in `custom_components/grid_coordinator/config_flow.py`. Find (occurs exactly once):

```python
    ENTITY_MON_LOAD_1,
    SIM_ENTITY_IDS,
)
```

Replace with:

```python
    ENTITY_MON_LOAD_1,
    PRICE_WINDOW_FIELDS,
    SIM_ENTITY_IDS,
)
```

Edit 2 of 5 in `custom_components/grid_coordinator/config_flow.py`. Find (occurs exactly once):

```python
def _entities_schema(defaults: dict) -> vol.Schema:
```

Replace with:

```python
def _price_window_schema(defaults: dict) -> vol.Schema:
    """Schema for the price window step (step 4).

    Every field is free text: an entity ID, or for the numeric and time settings
    either a literal or the ID of a helper entity.  Blank means "not set".
    """
    return vol.Schema(
        {
            # suggested_value, not default: a field cleared on the form is then left
            # out of the input (and stored blank) instead of being refilled.
            vol.Optional(key, description={"suggested_value": defaults.get(key, default)}): _TEXT
            for key, default in PRICE_WINDOW_FIELDS.items()
        }
    )


def _price_window_input(user_input: dict) -> dict:
    """Every price window field, with one left blank on the form stored as ""."""
    return {key: str(user_input.get(key, "")).strip() for key in PRICE_WINDOW_FIELDS}


def _entities_schema(defaults: dict) -> vol.Schema:
```

Edit 3 of 5 in `custom_components/grid_coordinator/config_flow.py`. Find (occurs exactly once):

```python
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Grid Coordinator", data=self._data)

        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(ENTITY_ID_DEFAULTS),
        )
```

Replace with:

```python
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_price_window()

        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(ENTITY_ID_DEFAULTS),
        )

    async def async_step_price_window(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 4 — price window (optional; leave the entities blank to skip)."""
        if user_input is not None:
            self._data.update(_price_window_input(user_input))
            return self.async_create_entry(title="Grid Coordinator", data=self._data)

        return self.async_show_form(
            step_id="price_window",
            data_schema=_price_window_schema(PRICE_WINDOW_FIELDS),
        )
```

Edit 4 of 5 in `custom_components/grid_coordinator/config_flow.py`. Find (occurs exactly once):

```python
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="", data=self._data)
```

Replace with:

```python
        """Step 3 — Solax entity IDs."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_price_window()
```

Edit 5 of 5 in `custom_components/grid_coordinator/config_flow.py`. Find (occurs exactly once):

```python
        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(solax_defaults),
        )
```

Replace with:

```python
        return self.async_show_form(
            step_id="solax",
            data_schema=_solax_schema(solax_defaults),
        )

    async def async_step_price_window(
        self,
        user_input: dict | None = None,
    ) -> ConfigFlowResult:
        """Step 4 — price window."""
        if user_input is not None:
            self._data.update(_price_window_input(user_input))
            return self.async_create_entry(title="", data=self._data)

        current = {
            key: str(self._current(key, default)) for key, default in PRICE_WINDOW_FIELDS.items()
        }
        return self.async_show_form(
            step_id="price_window",
            data_schema=_price_window_schema(current),
        )
```

- [ ] **Step 5: Add the translations**

In `custom_components/grid_coordinator/translations/en.json`, add this object as `"price_window"` in **both** `config.step` and `options.step`, after `"solax"` (the file is 2-space-indented JSON; keep it so):

```json
{
  "title": "Price Window",
  "description": "What to do during forecast price spikes: export at full power, hold, or charge, keeping enough energy to run the house. Prices come from **Amber Express**. Leave the enable entity blank to keep this off. Each numeric or time setting takes a value or the entity ID of a helper.",
  "data": {
    "entity_price_window_enable": "Enable (switch-like entity)",
    "entity_price_window_shadow": "Shadow mode (switch-like entity)",
    "entity_price_window_feed_in": "Amber Express feed-in price entity",
    "entity_price_window_general": "Amber Express general price entity",
    "entity_price_window_house_load": "Expected house load entity (W)",
    "price_window_spike_threshold": "Spike threshold ($/kWh)",
    "price_window_lookahead_hours": "Lookahead (h)",
    "price_window_reserve_margin_hours": "Reserve margin (h)",
    "price_window_reserve_until": "Reserve until (clock time)",
    "price_window_min_sell_price": "Minimum sell price ($/kWh)",
    "price_window_charge_ratio": "Charge ratio",
    "price_window_max_charge_buy_price": "Maximum charge buy price ($/kWh)",
    "price_window_round_trip_efficiency": "Round-trip efficiency"
  },
  "data_description": {
    "entity_price_window_enable": "input_boolean, switch or template binary_sensor. The price window runs only while this is 'on'. Blank = off.",
    "entity_price_window_shadow": "While this is 'on', or blank, or unavailable, the decision is worked out and reported but does not control the batteries. Turn it 'off' to go live.",
    "entity_price_window_feed_in": "E.g. sensor.amber_express_home_feed_in_price. Its detailedForecast attribute is read.",
    "entity_price_window_general": "E.g. sensor.amber_express_home_general_price. Its detailedForecast attribute is read.",
    "entity_price_window_house_load": "A sensor in watts giving the average house load to expect over the next few hours. It sizes the energy kept back for the house.",
    "price_window_spike_threshold": "An interval counts as a possible spike when the top of its feed-in forecast range reaches this, or when Amber flags it. A confirmed price at or above this is always sold ahead of intervals that merely could beat it.",
    "price_window_lookahead_hours": "A possible spike this far ahead puts the price window in control. Until it arrives the batteries hold, unless there is energy to spare at a good price.",
    "price_window_reserve_margin_hours": "Energy to run the house is kept until this long after the last possible spike.",
    "price_window_reserve_until": "Optional. HH:MM, or an input_datetime. When set, energy for the house is kept until the next time the clock reads this (today or tomorrow) instead of using the margin.",
    "price_window_min_sell_price": "Never export below this price while a window is active.",
    "price_window_charge_ratio": "Charge from the grid only when the predicted spike price is at least this many times the buy price after round-trip losses.",
    "price_window_max_charge_buy_price": "Optional. Never charge from the grid above this buy price, whatever the formula says. Blank = formula only.",
    "price_window_round_trip_efficiency": "Fraction of bought energy that comes back out (0.85 = 85%). Match the EMHASS efficiencies."
  }
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests -q`
Expected: `235 passed`.

Run: `scripts/ha_smoke/run`
Expected tail: `235 passed` under "existing unit suite on real HA", `30 passed` under "smoke tests", and `== unit suite rc=0   smoke rc=0`.

- [ ] **Step 7: Commit**

```bash
git add custom_components/grid_coordinator/sensor.py custom_components/grid_coordinator/config_flow.py custom_components/grid_coordinator/translations/en.json tests/test_translations.py scripts/ha_smoke/test_smoke_ha.py
git commit -m "feat: price window sensors and options step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `scripts/ha_smoke/README.md`

**Interfaces:**
- Consumes: the finished feature.
- Produces: nothing code depends on.

- [ ] **Step 1: README**

In `README.md`, insert this section immediately before `## Configuration`:

```markdown
## Price window

During forecast price spikes the coordinator stops following the EMHASS plan and
decides for itself, every tick, whether to export at full power, hold, or charge.
Prices come from the Amber Express integration's `detailedForecast` attribute.

- A *window* opens when an interval within the lookahead (default 4 h) could reach
  the spike threshold (default $1.00/kWh on the top of the feed-in forecast range)
  or is flagged by Amber as a potential or actual spike.
- Energy to run the house until the window ends plus a margin (default 2 h), or
  until a set clock time, is kept back. What is left is the surplus.
- The surplus goes to the best-priced intervals first, each at full export power
  from both batteries. If the current interval is one of them, the coordinator
  exports; otherwise it holds.
- If intervals that could spike are left without energy and buying now is cheap
  enough against their predicted price, it charges at full power.

Every decision carries a reason: `sensor.grid_coordinator_price_window_reason`
holds a code (`export_allocated`, `hold_outranked`, `charge_shortfall`, ...) and
its attributes hold a sentence with the figures and the per-interval working.

The feature is off until its enable entity, both Amber Express entities and an
expected house load entity are set in the options flow's Price Window step. It
starts in **shadow mode** (reports, does not control) until the shadow entity is
turned off. The manual override and the enable gate still take precedence.
```

- [ ] **Step 2: CLAUDE.md**

In `CLAUDE.md`, in the Architecture section, insert this paragraph immediately before the paragraph that starts `**`models.py`**`:

```markdown
**`price_window.py`** — pure functions, no HA imports. `parse_amber_express()` is the only code that knows Amber Express field names (it rounds each `start_time` down to the minute: Amber Express writes `02:00:01`). `decide_price_window()` finds the window (intervals within the lookahead whose feed-in high bound reaches the spike threshold, or that Amber flags), keeps a house reserve, allocates the surplus to the best-priced intervals and returns `export` / `hold` / `charge` with a reason code, sentence and working. Later intervals rank on their high bound, except when the current price is confirmed at or above the spike threshold (a spike in hand), when they rank on their predicted price. `window_setpoints()` turns the action into a grid target and a combined battery setpoint anchored on the uncontrolled grid power. `coordinator._evaluate_price_window()` runs at the top of every tick (`_async_update_data` wraps `_async_tick`), so the decision is reported even on disabled, override and shadow ticks; when it controls, the tick switches off tier 2, transient damping, entity-driven grid priority and the handoff dwell, and bypasses the ramp on the tick the action changes. Spec: `docs/superpowers/specs/2026-10-12-price-window-design.md`.
```

Also in `CLAUDE.md`, change `**`config_flow.py`** — three-step config + options flow:` to `**`config_flow.py`** — four-step config + options flow:` and add `, (4) price window settings (free text: a literal or a helper entity ID; `suggested_value`, not `default`, so a cleared field is stored blank)` before the full stop that ends that sentence.

- [ ] **Step 3: Smoke README**

In `scripts/ha_smoke/README.md`, change `Expected tail (12 smoke scenarios` to `Expected tail (30 smoke scenarios`, `163 passed in 0.6s` to `235 passed in 0.9s`, and `... 12 passed ...` to `... 30 passed ...`. Under "What it covers", add the bullet:

```markdown
- Price window: a spike exports through the real tick and writes the Voltx setpoint; the four
  sensors and the reason sensor's attributes; shadow mode reports without controlling; the
  options flow's Price Window step saves literals and entity IDs, and a cleared field is stored blank.
```

- [ ] **Step 4: Check the suite still passes and commit**

Run: `python3 -m pytest tests -q`
Expected: `235 passed`.

```bash
git add README.md CLAUDE.md scripts/ha_smoke/README.md
git commit -m "docs: price window

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

---

### Task 9: Pull request and release (ask Stuart before each outward step)

**Files:**
- Modify: `custom_components/grid_coordinator/manifest.json` (after merge)

**Interfaces:**
- Consumes: Tasks 1 to 8 committed on `feat/price-window`.
- Produces: release `2026.10.5` installed on the Home Assistant host.

- [ ] **Step 1: Final check**

Run: `python3 -m pytest tests -q` and `scripts/ha_smoke/run`
Expected: `235 passed`; `== unit suite rc=0   smoke rc=0`.

- [ ] **Step 2: Push and open the PR** (confirm with Stuart first)

```bash
git push -u origin feat/price-window
gh pr create --title "feat: price window" --body "$(cat <<'BODY'
During forecast price spikes the coordinator decides every tick whether to export at full power, hold, or charge, using both batteries, keeps enough energy to run the house, and reports the reason for every decision. Off by default; starts in shadow mode.

Spec: docs/superpowers/specs/2026-10-12-price-window-design.md
Plan: docs/superpowers/plans/2026-10-13-price-window.md

Tests: bare-interpreter suite and scripts/ha_smoke/run both pass.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB
BODY
)"
```

- [ ] **Step 3: After Stuart merges, bump the version on `main`**

In `custom_components/grid_coordinator/manifest.json` change `"version": "2026.10.4"` to `"version": "2026.10.5"`, then:

```bash
git add custom_components/grid_coordinator/manifest.json
git commit -m "chore: bump manifest version to 2026.10.5

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LDb2LLrmcoYDFbbZVaPSNB"
```

Push and tag as previous releases were (see `git log --oneline` and `git tag` for the pattern), with Stuart's go-ahead. Stuart updates the integration on the Home Assistant host and restarts.

---

### Task 10: Shadow rollout in homeassistant-config (after the release is installed)

This task changes live Home Assistant, not this repo. Do it with Stuart, through the `home-assistant` MCP tools or the HA UI. Nothing here makes the price window control anything: shadow stays on.

- [ ] **Step 1: Create the helpers**

| Helper | Type | Initial value |
|---|---|---|
| `input_boolean.price_window_enabled` | toggle | off |
| `input_boolean.price_window_shadow` | toggle | on |
| `input_number.price_window_spike_threshold` | number, 0 to 30, step 0.05, $/kWh | 1.00 |
| `input_number.price_window_min_sell_price` | number, 0 to 5, step 0.01, $/kWh | 0.30 |
| `input_number.price_window_max_charge_buy_price` | number, 0 to 30, step 0.05, $/kWh | 1.00 |
| `input_datetime.price_window_reserve_until` | time only | leave unset in the options until wanted |

Lookahead, reserve margin, charge ratio and round-trip efficiency stay as literals (4.0, 2.0, 1.5, 0.85) unless Stuart wants helpers for them too.

- [ ] **Step 2: Choose the expected house load entity**

It must be in watts and represent the average load to expect over the next few hours, not the instantaneous reading. Candidates seen on 2026-10-13: `sensor.house_load_emhass_filtered`, `sensor.house_load_non_deferrable_filtered`. Check the unit and a day of history for each with Stuart and pick one. If only a kW sensor suits, create a template sensor that multiplies by 1000.

- [ ] **Step 3: Set the options**

Settings, Devices & services, Grid Coordinator, Configure; step through to "Price Window" and enter: enable `input_boolean.price_window_enabled`, shadow `input_boolean.price_window_shadow`, feed-in `sensor.amber_express_home_feed_in_price`, general `sensor.amber_express_home_general_price`, house load from Step 2, spike threshold `input_number.price_window_spike_threshold`, minimum sell price `input_number.price_window_min_sell_price`, maximum charge buy price `input_number.price_window_max_charge_buy_price`. Leave the rest as shown.

- [ ] **Step 4: Turn it on in shadow and check it reads sensibly**

Turn on `input_boolean.price_window_enabled`. Then check:
- `sensor.grid_coordinator_price_window_reason` is `no_window` on an ordinary day, and its `reason` attribute names the threshold. If it reads `data_stale`, the attribute says which entity.
- `sensor.grid_coordinator_mode` behaves exactly as before (shadow never changes a command).
- Lower `input_number.price_window_spike_threshold` to just under the current top-of-range feed-in price for a few minutes: the reason should move to an `export_*` or `hold_*` code with `shadow: true` and a sensible `intervals` table, and the mode sensor should still not change. Put the threshold back.

- [ ] **Step 5: Record it**

In `homeassistant-config`, add a short section to `CLAUDE.md` naming the helpers, the chosen house load entity and that the three automations ("Voltx auto export start", "Voltx auto-export stop", "Price spike export ceiling") stay in place while in shadow. Commit straight to `main` (that repo's convention).

- [ ] **Step 6: Going live is a separate decision**

After a real price event has been read back in shadow (the reason sensor's history against what the prices did), Stuart decides whether to turn `input_boolean.price_window_shadow` off. On the first live event, compare `sensor.voltx_battery_battery_power` with `sensor.grid_coordinator_voltx_command` at each change of decision to confirm the Voltx follows a direct step (the ramp bypass), as the spec requires. Only then remove the three automations and fix `input_number.emhass_plan_export_limit` at its normal value.
