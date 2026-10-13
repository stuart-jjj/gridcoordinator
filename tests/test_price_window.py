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
