"""Minimal house + two-battery plant for closed-loop simulation of the controller.

Sign conventions match the coordinator: battery power positive = discharging,
grid positive = importing.  grid = load - solar - voltx - solax.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fraction of the gap to its target the Voltx inverter closes per 10 s tick while in
# native self-consumption (firmware reacts fast but not instantly).
NATIVE_LAG = 0.7


@dataclass
class Battery:
    soc: float          # %
    capacity_kwh: float
    max_charge: float   # W (positive number)
    max_discharge: float  # W (positive number)
    power: float = 0.0  # W actual, + = discharge

    def clamp(self, power: float) -> float:
        """Clamp a requested power to inverter limits and the SOC window [0, 100]."""
        power = max(-self.max_charge, min(self.max_discharge, power))
        if self.soc >= 100.0 and power < 0:
            return 0.0
        if self.soc <= 0.0 and power > 0:
            return 0.0
        return power

    def integrate(self, dt_s: float) -> None:
        self.soc -= self.power * dt_s / 3600.0 / (self.capacity_kwh * 1000.0) * 100.0


class Plant:
    """Advances the house one tick given the controller's commands."""

    def __init__(self, voltx: Battery, solax: Battery) -> None:
        self.voltx = voltx
        self.solax = solax
        self.grid = 0.0

    def step(
        self,
        *,
        dt_s: float,
        load_w: float,
        solar_w: float,
        voltx_native: bool,
        voltx_cmd: float,
        solax_cmd: float,
    ) -> float:
        """Apply commands, integrate SOC, return the grid power (W, + = import).

        Solax has no working native mode, so with solax_cmd == 0 it is simply idle.
        """
        self.solax.power = self.solax.clamp(solax_cmd)
        if voltx_native:
            want = load_w - solar_w - self.solax.power
            want = self.voltx.clamp(want)
            self.voltx.power += NATIVE_LAG * (want - self.voltx.power)
            self.voltx.power = self.voltx.clamp(self.voltx.power)
        else:
            self.voltx.power = self.voltx.clamp(voltx_cmd)
        self.voltx.integrate(dt_s)
        self.solax.integrate(dt_s)
        self.grid = load_w - solar_w - self.voltx.power - self.solax.power
        return self.grid
