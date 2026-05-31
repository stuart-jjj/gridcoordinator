# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Home Assistant custom integration (`grid_coordinator`) that runs a 10-second closed-loop controller to command a Voltx battery inverter so the household grid import/export tracks the EMHASS MPC setpoint.

## Development commands

```bash
# Install dependencies (HA + ruff)
scripts/setup

# Run a local HA instance with the integration loaded
scripts/develop          # starts hass on port 8123

# Lint and format (auto-fix)
scripts/lint             # ruff format . && ruff check . --fix

# CI equivalents (check only, no fix)
python3 -m ruff check .
python3 -m ruff format . --check
```

There is no test suite yet. `budget.py` is the natural place to add `pytest` unit tests first since it has zero HA dependencies.

## Architecture

The integration runs as a single `DataUpdateCoordinator` subclass (`GridCoordinator`) that fires every 10 seconds:

```
coordinator.py  →  budget.py  →  inverter (HA service calls)
                      ↓
                 models.py (CoordinatorData)
                      ↓
                 sensor.py (diagnostic sensors)
```

**`coordinator.py`** — reads HA entity states, delegates arithmetic to `budget.py`, then writes the result to the Voltx inverter via `select.select_option` and `number.set_value` service calls. Raises `UpdateFailed` (→ `ConfigEntryNotReady`) only for the grid power sensor; all other inputs fall back to safe defaults.

**`budget.py`** — pure functions, no HA imports. `compute_voltx_command()` implements a proportional-integral controller in a single step, then applies (in order): SOC constraints, inverter physical limits, ramp limiting, and hard grid-safety clamp. `build_coordinator_data()` derives headroom fields.

**`models.py`** — `CoordinatorMode` (StrEnum) and `CoordinatorData` (frozen dataclass). The mode reported is the *binding constraint* that last overrode the raw command.

**`const.py`** — all hardcoded entity IDs (Phase 1 MVP; entity config is planned for Phase 3) and tunable defaults.

## Sign conventions

These are consistent throughout the codebase and must not be broken:

| Signal | Positive means |
|---|---|
| `grid_actual`, `grid_target` | importing from the grid |
| `voltx_command` | discharging (battery → AC) |
| EMHASS `mpc_grid_power` | **exporting** to the grid (injection convention — opposite) |

`CONF_MPC_SIGN_INVERTED` (default `True`) negates the EMHASS value so the controller always works in the "positive = import" basis.

## Key constraints

- Entity IDs in `const.py` are hardcoded Phase 1 stubs — changing them is a common task during development.
- The integration enforces a single config entry (`async_set_unique_id(DOMAIN)` + `_abort_if_unique_id_configured`).
- Config options are import/export limits (W), ramp step (W/tick), plan stale timeout (min), and MPC sign convention.
- The `scripts/develop` script sets `PYTHONPATH` so HA finds `custom_components/grid_coordinator` without symlinking.
