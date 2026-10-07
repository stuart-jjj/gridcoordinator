"""Every new handoff option/entity must have a label and description in both flows."""

import json
from pathlib import Path

EN = json.loads(
    (Path(__file__).parents[1] / "custom_components/grid_coordinator/translations/en.json").read_text()
)

PARAM_KEYS = ("sc_min_dwell_seconds",)
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
