"""Stub out HA/voluptuous so budget.py (zero HA deps) can be unit-tested without
a full Home Assistant install. Importing `custom_components.grid_coordinator.budget`
runs the package's __init__.py as a side effect, which pulls in coordinator.py and
real homeassistant/voluptuous modules; those aren't needed to exercise the pure
arithmetic in budget.py, so we install lightweight stand-ins before collection.

This does not replace running the real test suite inside the devcontainer (see
CLAUDE.md) — it only unblocks pure-logic unit tests on a bare Python interpreter.
"""

import sys
import types


def _stub(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


if "voluptuous" not in sys.modules:
    vol = _stub("voluptuous")

    class _Marker:
        def __init__(self, *a, **k):
            pass

    class Schema:
        def __init__(self, *a, **k):
            pass

    vol.Schema = Schema
    vol.Required = _Marker
    vol.Optional = _Marker
    vol.In = lambda *a, **k: None
    vol.All = lambda *a, **k: None
    vol.Coerce = lambda *a, **k: None
    vol.Range = lambda *a, **k: None

if "homeassistant" not in sys.modules:
    _stub("homeassistant")

    ha_const = _stub("homeassistant.const")

    class Platform:
        SENSOR = "sensor"
        SWITCH = "switch"
        SELECT = "select"
        NUMBER = "number"
        BUTTON = "button"

    ha_const.Platform = Platform

    ha_core = _stub("homeassistant.core")

    class HomeAssistant:
        pass

    class ServiceCall:
        pass

    ha_core.HomeAssistant = HomeAssistant
    ha_core.ServiceCall = ServiceCall

    ha_helpers = _stub("homeassistant.helpers")

    ha_cv = _stub("homeassistant.helpers.config_validation")
    ha_cv.boolean = lambda v: bool(v)

    ha_uc = _stub("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __init__(self, *a, **k):
            pass

        def __class_getitem__(cls, item):
            return cls

    class UpdateFailed(Exception):
        pass

    ha_uc.DataUpdateCoordinator = DataUpdateCoordinator
    ha_uc.UpdateFailed = UpdateFailed

    ha_helpers.config_validation = ha_cv
    ha_helpers.update_coordinator = ha_uc
