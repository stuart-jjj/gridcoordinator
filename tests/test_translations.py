"""Every new handoff option/entity must have a label and description in both flows."""

import json
from pathlib import Path

EN = json.loads(
    (Path(__file__).parents[1] / "custom_components/grid_coordinator/translations/en.json").read_text()
)

PARAM_KEYS = ("sc_min_dwell_seconds", "sc_exit_margin")
REMOVED_KEYS = ("sc_battery_tolerance", "sc_discharge_handoff", "sc_power_smoothing_seconds")
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


def test_removed_options_are_gone_from_both_flows():
    for flow, step in (("config", "user"), ("options", "init")):
        for section in ("data", "data_description"):
            keys = _step(flow, step)[section]
            for key in REMOVED_KEYS:
                assert key not in keys, (flow, section, key)


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
