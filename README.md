# Grid Coordinator

Grid Coordinator is a Home Assistant custom integration that runs a 10-second
control loop to steer a Voltx battery inverter so household grid import/export
tracks an EMHASS MPC setpoint.

## What it does

- Reads live grid power and EMHASS target power.
- Normalizes sign conventions so control logic always uses:
	- positive = importing from grid
	- negative = exporting to grid
- Computes battery charge/discharge command with safety constraints:
	- SOC limits
	- inverter max charge/discharge limits
	- ramp limiting per control tick
	- hard grid import/export limits
- Writes setpoints to Voltx entities in Home Assistant.
- Exposes diagnostic sensors via coordinator data.

## Core behavior

- Update cadence: every 10 seconds.
- If the integration enable entity is off, the coordinator stops active tracking
	and leaves inverter behavior in self-consumption mode.
- If the EMHASS plan is stale, the controller falls back to a safe behavior.
- Around near-zero target, a self-consumption deadband avoids unnecessary
	command chatter.

## Manual override service

The integration provides `grid_coordinator.set_mode` to temporarily override
normal EMHASS tracking.

Supported modes:
- `auto`: cancel override and resume EMHASS tracking
- `self_consume`: force inverter self-consumption behavior
- `hold_soc`: hold battery around 0W charge/discharge
- `force_charge`: force charging (optionally with `power_w`)
- `force_export`: force discharge/export (optionally with `power_w`)
- `disabled`: stop coordinator writes to inverter

Common fields:
- `power_w` (optional for force modes)
- `duration_minutes` (default 60)
- `bypass_soc` (optional, force modes only)

## Configuration

Configured from the UI config flow and options flow.

Main controller options include:
- import limit (W)
- export limit (W)
- ramp step (W per tick)
- plan stale timeout (minutes)
- MPC sign inversion toggle
- self-consumption mode name
- self-consumption deadband (W)
- tracking deadband (W)
- testing mode toggle

Entity IDs are configurable for all required inputs and outputs, including grid
power, MPC target, battery SOC/limits, enable gate, inverter command entity,
and inverter work mode entity.

## Installation

Install as a custom integration in Home Assistant, then add Grid Coordinator
from Settings > Devices & Services.

If you are developing this repo locally, see [CLAUDE.md](CLAUDE.md) for
devcontainer-specific setup and runtime notes.

## Development

Run inside the devcontainer:

```bash
scripts/setup
scripts/develop
scripts/lint
```

Notes:
- Python source changes require a full Home Assistant restart.
- Config/options and translation changes can be picked up with integration
	reload.
