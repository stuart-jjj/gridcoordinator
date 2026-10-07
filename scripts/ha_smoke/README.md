# Real-HA smoke tests (Docker)

Runs the integration against a **real Home Assistant core** (the version pinned in
`requirements.txt`, currently 2026.3.2, Python 3.14) inside Docker. The bare-interpreter suite
(`python3 -m pytest tests/`) uses stubs and cannot exercise config entry setup, the real
`_async_update_data` tick wiring, or the options-flow schema; this can.

## When to run it

- Before merging a change to `coordinator.py`, `config_flow.py`, `const.py` or
  `translations/en.json` — anything the stub suite can't see.
- After bumping the `homeassistant==` pin in `requirements.txt`.
- It is **not** a substitute for a live soak: see "What it does not cover".

## Run it

```bash
scripts/ha_smoke/run
```

Expected tail (12 smoke scenarios plus the repo's unit suite on real HA; counts grow as tests are added):

```
== existing unit suite on real HA
163 passed in 0.6s
== smoke tests
... 12 passed ...
== unit suite rc=0   smoke rc=0
```

A non-zero exit means a failure; scroll up for the `FAILED` / `E ` lines.
First run installs Home Assistant into a Docker volume (a few minutes); later runs reuse it and
take seconds. The venv is rebuilt automatically when the `homeassistant==` pin changes.

## Prerequisites

- Docker running (Docker Desktop on this machine).
- An image with **Python >= 3.14**. The runner auto-picks the VS Code devcontainer image built for this
  repo (`vsc-gridcoordinator-*-features:latest`) and falls back to `python:3.14`. Override with
  `SMOKE_IMAGE=<image>`. The bare `ghcr.io/home-assistant/devcontainer:addons` image has **no Python**
  (VS Code devcontainer features add it) — don't use it directly.
- Docker Desktop only bind-mounts paths under your home directory by default. The runner mounts the
  repo, so keep the checkout under `~`; mounting anything from `/tmp` fails with "mounts denied".

## Safety

The repo is mounted **read-only** and copied to `/work` inside the container. Nothing in the working
tree is written, and the git-ignored `config/` dev instance is excluded from the copy. Do not point a
`hass` process at the repo's `config/` from here. The only persistent artefact is the named volume
`gc-smoke-venv` (override with `SMOKE_VENV_VOLUME`); remove it with `docker volume rm gc-smoke-venv`.

## What it covers

`test_smoke_ha.py` boots real HA core via `pytest-homeassistant-custom-component`, sets up a real
config entry for `grid_coordinator`, and drives the real coordinator:

- Entry loads, sensors and the `set_mode` service register.
- Self-consumption handoff: any battery plan (charging, idle, discharging) hands off at a
  ~0 W target, including a charging plan that is being undershot; unreadable / `nan` / `inf`
  Voltx power does not affect the decision.
- Safety exits inside the dwell: import-limit breach, Voltx control helper switched off, an EV starting
  to charge (the EV is served from the grid, not by draining the battery); an idle EV charger does not block it.
- Solax follows Voltx during the handoff (setpoint maths, register sign inversion, trigger press), and
  is released when the Voltx power sensor drops.
- A stale plan bypasses a pending re-entry lockout.
- The real options flow: the dwell option appears with its default, the options removed in 2026.10.2
  are absent, the schema serialises the way the frontend does it, values save into `entry.options`;
  an entry still carrying the removed option keys loads and works.

It also re-runs the repo's own unit suite against real HA instead of the stubs.

## What it does not cover

- **Other integrations are not loaded.** iammeter, Voltx modbus, Solax modbus and the EMHASS pyscript are
  replaced by seeded plain states plus mocked `number` / `select` / `button` services that record calls.
  Real option names, Modbus latency, entity availability quirks and service semantics need the live
  system (and the soak checklist in the plan).
- It is HA core only: no Supervisor, no frontend, no real recorder history.
- Expect harmless `Referenced entities … are missing` warnings in the verbose log: the mocked services
  have no backing entities.

## Adding a scenario

Everything is in `test_smoke_ha.py`:

- `_seed(hass, ...)` writes the input entity states (`ENTITY_ID_DEFAULTS` ids). Extend it if the
  coordinator starts reading a new entity.
- `_setup(hass, solax=..., extra_data=..., **seed)` seeds, mocks the three services, creates and sets up
  the `MockConfigEntry`, and returns `(entry, coordinator, calls)`. **Setup itself runs one coordinator
  tick**, so state carried across ticks (for example Solax's last written setpoint) already reflects tick 1
  when you call `_tick` — account for that in expected values.
- `_tick(hass, coordinator)` runs one `async_refresh()` and asserts it succeeded.
- `calls["number"|"select"|"button"]` hold the recorded service calls (`c.data["entity_id"]`, `c.data["value"]`).
- To enable Solax pass `solax=True`; `_solax_enabled()` needs `CONF_ENTITY_SOLAX_SOC` in the entry data,
  which `_setup` adds.
- Handoff state is `coordinator._sc_state`; to test a lockout set it directly with
  `ScState(active=..., last_transition_at=time.monotonic())` (the coordinator uses
  `time.monotonic()`, so don't use a frozen clock for these).

## Pitfalls already hit (so you don't repeat them)

- **`tests/conftest.py` stubs `homeassistant` and `voluptuous`** whenever they have not been imported yet,
  *even if the real packages are installed*. That is why `container.sh` deletes it from the copy before
  running the unit suite on real HA, and why the smoke tests live outside `tests/`.
- **Do not use `asyncio.run()` in tests.** On Python 3.14 it leaves the thread with no current event loop,
  and every later test then errors with `RuntimeError: There is no current event loop` once HA's pytest
  plugins are installed. Use `loop = asyncio.new_event_loop(); loop.run_until_complete(...); loop.close()`.
- `pytest-homeassistant-custom-component` is version-locked to a Home Assistant release; the runner
  installs it under a `homeassistant==<pin>` constraint so pip picks the matching plugin version.
  If resolution fails after a pin bump, check which plugin release supports that HA version.
- Python source changes in the repo are picked up on every run because the container copies the tree
  fresh; there is nothing to restart.
