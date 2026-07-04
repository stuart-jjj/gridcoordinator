#!/usr/bin/env python3
"""Parse a grid_coordinator HA debug log into tick / solax / ev_throttle tables.

Writes three CSVs and a single multi-sheet .xlsx (no third-party deps), and prints
a summary of the stress-test run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")


def num(seg: str, key: str):
    m = re.search(rf"{re.escape(key)}=(-?\d+(?:\.\d+)?)", seg)
    if not m:
        return None
    v = m.group(1)
    return int(v) if re.fullmatch(r"-?\d+", v) else float(v)


def word(seg: str, key: str):
    m = re.search(rf"{re.escape(key)}=(\S+)", seg)
    return m.group(1) if m else None


def parse(path: Path):
    ticks, solax, throttle = [], [], []
    for raw in path.read_text(errors="replace").splitlines():
        line = ANSI.sub("", raw).strip()
        mts = TS.match(line)
        if not mts:
            continue
        ts = mts.group(1)

        if "tick | grid=" in line:
            msg = line[line.index("tick | "):]
            segs = msg.split(" | ")
            g = segs[1]                     # grid/voltx block
            c = segs[2]                     # constraints block
            sx = next((s for s in segs if s.startswith("solax ")), "")
            et = next((s for s in segs if s.startswith("ev_throttle=")), "")
            ticks.append({
                "timestamp": ts,
                "grid_W": num(g, "grid"),
                "age_s": num(g, "age"),
                "target_W": num(g, "target"),
                "unctrl_W": num(g, "unctrl"),
                "mpc_batt_W": num(g, "mpc_batt"),
                "prev_W": num(g, "prev"),
                "raw_W": num(g, "raw"),
                "ramped_W": num(g, "ramped"),
                "cmd_W": num(g, "cmd"),
                "hold": word(g, "hold"),
                "mode": word(g, "mode"),
                "floor_W": num(c, "floor"),
                "ceil_W": num(c, "ceil"),
                "maxc_W": num(c, "maxc"),
                "maxd_W": num(c, "maxd"),
                "soc_pct": num(c, "soc"),
                "soc_min": int(re.search(r"\[(\d+)\.\.(\d+)\]", c).group(1)) if re.search(r"\[(\d+)\.\.(\d+)\]", c) else None,
                "soc_max": int(re.search(r"\[(\d+)\.\.(\d+)\]", c).group(2)) if re.search(r"\[(\d+)\.\.(\d+)\]", c) else None,
                "plan_age_min": num(c, "plan_age"),
                "stale": word(c, "stale"),
                "ev": word(c, "ev"),
                "t2g": num(c, "t2g"),
                "gp": word(c, "gp"),
                "headroom_W": num(c, "headroom"),
                "transient": word(c, "transient"),
                "gstdev_W": num(c, "gstdev"),
                "gema_W": num(c, "gema"),
                "ev_throttle": word(et, "ev_throttle"),
                "ev_limit_A": (re.search(r"limit=(\S+?)A", et).group(1) if re.search(r"limit=(\S+?)A", et) else None),
                "proj_grid_W": num(et, "proj_grid"),
            })
            solax.append({
                "timestamp": ts,
                "solax_cmd_W": num(sx, "cmd"),
                "solax_mode": word(sx, "mode"),
                "path": word(sx, "path"),
                "supp": word(sx, "supp"),
                "solax_soc_pct": num(sx, "soc"),
                "after_voltx_W": num(sx, "after_voltx"),
                "prev_solax_W": num(sx, "prev"),
            })

        elif "ev throttle | proj=" in line:
            seg = line[line.index("ev throttle | "):]
            rec = re.search(r"recovered=(-?\d+)s/(-?\d+)min", seg)
            throttle.append({
                "timestamp": ts,
                "proj_W": num(seg, "proj"),
                "ceil_W": num(seg, "ceil"),
                "ovr_W": num(seg, "ovr"),
                "evP_W": num(seg, "evP"),
                "limit_A": num(seg, "limit"),
                "active": word(seg, "active"),
                "rel_ready": word(seg, "rel_ready"),
                "recovered_s": int(rec.group(1)) if rec else None,
                "holdoff_min": int(rec.group(2)) if rec else None,
            })
    return ticks, solax, throttle


if __name__ == "__main__":
    import pandas as pd

    log = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else log.parent
    ticks, solax, throttle = parse(log)
    frames = {
        "tick": pd.DataFrame(ticks),
        "solax": pd.DataFrame(solax),
        "ev_throttle": pd.DataFrame(throttle),
    }
    for name, df in frames.items():
        df.to_csv(outdir / f"gc_{name}.csv", index=False)
    xlsx = outdir / "gridcoordinator_stress_2026-06-21.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        for name, df in frames.items():
            df.to_excel(xw, sheet_name=name, index=False)
    print(f"rows: tick={len(ticks)} solax={len(solax)} ev_throttle={len(throttle)}")
    print(f"wrote {xlsx.name} + 3 CSVs to {outdir}")
