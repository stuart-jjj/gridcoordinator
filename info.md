# Grid Coordinator

Grid Coordinator is a Home Assistant custom integration that tracks an EMHASS
grid-power target and controls a Voltx battery inverter in a 10-second control
loop.

## Features

- Tracks EMHASS `mpc_grid_power` with consistent grid sign handling.
- Applies SOC safety bounds, inverter charge/discharge limits, and ramp limits.
- Supports temporary manual override modes through a Home Assistant service.

## Installation

Add this repository to HACS as a custom repository with category
`Integration`, then install Grid Coordinator from HACS.

![Grid Coordinator icon](https://raw.githubusercontent.com/stuartjones/grid_coordinator/main/custom_components/grid_coordinator/brand/icon.png)
