"""Issue 1: replay of the live 2026-10-07 07:10-07:34 inputs (Australia/Sydney).

The EMHASS setpoint sat at exactly 0.0 for 4-6 minutes at a time while actual Voltx
power was ~400-600 W, flipping the legacy handoff on and off.  Series are step-hold
values copied from the recorder (sensor.grid_coordinator_grid_target,
sensor.grid_coordinator_mpc_battery_power, sensor.voltx_battery_battery_power).

The replay is open-loop: the recorded Voltx power came from the legacy run, so it is
fed to the decision as-is rather than from the plant.  Deadband is assumed 200 W (the
recorded flips only fit 178-291 W) — confirm against the real options.
"""

from tests.sim.driver import LEGACY, NEW, run


def _t(h: int, m: int, s: int = 0) -> int:
    return (h * 3600 + m * 60 + s) - (7 * 3600 + 10 * 60)


GRID_TARGET = [
    (_t(7, 10), 0.0), (_t(7, 16, 4), -83.1), (_t(7, 18, 3), 0.0), (_t(7, 26, 4), -177.8),
    (_t(7, 28, 3), -176.8), (_t(7, 30, 3), -59.8), (_t(7, 32, 4), 0.0), (_t(7, 34, 4), 706.2),
]
MPC_BATT = [
    (_t(7, 10), 295.22), (_t(7, 10, 4), 866.33), (_t(7, 12, 4), 906.33), (_t(7, 14, 4), 570.78),
    (_t(7, 16, 4), 0.0), (_t(7, 20, 4), 399.89), (_t(7, 22, 3), 613.22), (_t(7, 24, 3), 366.56),
    (_t(7, 26, 4), 0.0), (_t(7, 32, 4), 449.11), (_t(7, 34, 4), 0.0),
]
VOLTX_POWER = [
    (0, 847), (_t(7, 12, 34), 744), (_t(7, 12, 45), 718), (_t(7, 12, 56), 713), (_t(7, 13, 7), 677),
    (_t(7, 13, 18), 687), (_t(7, 13, 28), 677), (_t(7, 13, 40), 687), (_t(7, 13, 51), 668),
    (_t(7, 14, 1), 663), (_t(7, 14, 12), 414), (_t(7, 14, 43), 404), (_t(7, 15, 45), 409),
    (_t(7, 15, 55), 450), (_t(7, 16, 6), 440), (_t(7, 16, 16), 295), (_t(7, 16, 47), 269),
    (_t(7, 17, 18), 352), (_t(7, 17, 49), 1442), (_t(7, 18, 51), 295), (_t(7, 19, 22), 1442),
    (_t(7, 19, 52), 1437), (_t(7, 20, 5), 1463), (_t(7, 20, 16), 367), (_t(7, 20, 27), 506),
    (_t(7, 20, 38), 589), (_t(7, 20, 49), 579), (_t(7, 20, 59), 465), (_t(7, 21, 10), 418),
    (_t(7, 22, 12), 424), (_t(7, 22, 43), 419), (_t(7, 24, 46), 440), (_t(7, 24, 56), 574),
    (_t(7, 25, 7), 714), (_t(7, 25, 18), 538), (_t(7, 25, 49), 533), (_t(7, 26, 16), 595),
    (_t(7, 26, 47), 502), (_t(7, 27, 17), 590), (_t(7, 27, 49), 554), (_t(7, 28, 19), 404),
    (_t(7, 28, 51), 238), (_t(7, 29, 22), 269), (_t(7, 29, 52), 284), (_t(7, 30, 23), 341),
    (_t(7, 30, 55), 1607), (_t(7, 31, 26), 2135), (_t(7, 31, 56), 2243), (_t(7, 32, 5), 2248),
    (_t(7, 32, 16), 434), (_t(7, 32, 27), 651), (_t(7, 32, 37), 806), (_t(7, 32, 49), 734),
    (_t(7, 32, 59), 770), (_t(7, 33, 10), 661), (_t(7, 33, 21), 708), (_t(7, 33, 32), 791),
    (_t(7, 33, 42), 791), (_t(7, 33, 53), 780),
]
DURATION_S = _t(7, 34, 0)


def _hold(series, t):
    value = series[0][1]
    for at, v in series:
        if at <= t:
            value = v
    return value


def _inputs(t):
    return 0.0, 0.0, _hold(GRID_TARGET, t), _hold(MPC_BATT, t), _hold(VOLTX_POWER, t)


def test_legacy_reproduces_the_live_flapping():
    res = run(LEGACY, _inputs, DURATION_S, open_loop_power=True)
    assert res.transitions == 4  # live history: 07:16, 07:20, 07:26, 07:32


def test_new_rule_does_not_flap_on_the_same_inputs():
    res = run(NEW, _inputs, DURATION_S, open_loop_power=True)
    assert res.transitions == 0
    assert res.active_ticks > res.ticks * 0.9
