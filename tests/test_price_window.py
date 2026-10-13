"""Price window: Amber Express parsing, the decision and its reason, the setpoints."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

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
    window_setpoints,
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
