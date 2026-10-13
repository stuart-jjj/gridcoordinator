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
from datetime import datetime, time, timedelta

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
    # The cap is set but could not be read (its helper is unavailable): no charging,
    # rather than charging without the one absolute guard against buying dear.
    max_charge_buy_price_unreadable: bool = False
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
        if interval.sell_high >= settings.min_sell_price  # else it is never sold into
        and need_kwh(interval) - allocated.get(interval.start, 0.0) > _EPS_KWH
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
        if settings.max_charge_buy_price_unreadable:
            code = CODE_HOLD_CHARGE_TOO_DEAR
            sentence += (
                f" Not charging: the maximum charge buy price setting cannot be read; "
                f"{situation}."
            )
        elif capped:
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


def split_setpoint(
    total: float,
    share: float,
    *,
    a_max_discharge: float,
    a_max_charge: float,
    b_max_discharge: float,
    b_max_charge: float,
) -> tuple[float, float]:
    """Split a combined battery setpoint (W, + = discharge) between two batteries.

    Battery B is asked for `share` of the total; whatever either battery cannot
    deliver (a power limit, or a limit of 0 because it is at an SOC bound) goes to
    the other.  Returns (a, b).  The parts sum to `total` unless both are at a limit.
    """
    share = min(1.0, max(0.0, share))
    b = max(-b_max_charge, min(b_max_discharge, total * share))
    a = max(-a_max_charge, min(a_max_discharge, total - b))
    b = max(-b_max_charge, min(b_max_discharge, total - a))
    return a, b


def track_setpoint(
    action: str,
    prev_setpoint: float,
    *,
    grid_actual: float,
    grid_target: float,
    readings_current: bool,
    pending_discharge: float,
    pending_charge: float,
    ramp_step: float,
    max_discharge: float,
    max_charge: float,
) -> float:
    """The combined battery setpoint for a tick on which the action has not changed.

    The first tick of an action steps straight to `window_setpoints`.  After that the
    setpoint moves only on what the meter reads, never on what was commanded: while
    the batteries or the meter are still catching up with a step, "grid + commanded"
    overstates the load and would drive the batteries past the grid limit.

    - Past the limit the action is working at (over the export limit when exporting,
      over the import limit when charging): cut by the full excess at once.
    - Short of the target: close the gap by at most `ramp_step`, less whatever has
      been commanded in that direction and not yet seen (`pending_discharge` /
      `pending_charge`, W), and only when `readings_current` (the meter has had time
      to show the last command).
    """
    error = grid_actual - grid_target  # + = more discharge wanted
    urgent = (action == ACTION_EXPORT and error < 0) or (action == ACTION_CHARGE and error > 0)
    if urgent:
        step = error
    elif not readings_current:
        step = 0.0
    elif error > 0:
        step = min(ramp_step, max(0.0, error - max(0.0, pending_discharge)))
    else:
        step = -min(ramp_step, max(0.0, -error - max(0.0, pending_charge)))
    return max(-max_charge, min(max_discharge, prev_setpoint + step))
