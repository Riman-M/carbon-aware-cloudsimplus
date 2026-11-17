#!/usr/bin/env python3
"""
Summarise the schedule-fidelity diagnostic (run_fidelity.ps1).

For each fleet sizing, reports how many VMs finished more than one hour after
their planned finish, the largest lag, and how many rows fail the placement
check, broken down by capacity, margin and policy family. It then compares
attributed emissions between the two fleet sizings, cell by cell, and
recomputes the headline origin spread at 350 slots and a 48 h margin under
each.

Reading the output:
  * If late VMs appear at 350 slots under fleet200 and vanish under fleet350,
    the published 350-slot rows ran a schedule other than the planned one,
    and the fix is to size the fleet from the largest cap and rerun.
  * If emissions barely move between fleets, the published numbers stand
    and only the exclusions need re-handling.
  * If late VMs persist under fleet350, the cause is not fleet size and
    needs further diagnosis before any table is rebuilt.

Usage:
    python scripts/analyze_fidelity.py [results/fidelity]
"""
import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd


def load(d):
    frames = []
    for p in sorted(glob.glob(str(d / "us_home_seed*_fleet*.csv"))):
        m = re.search(r"seed(\d+)_fleet(\d+)", os.path.basename(p))
        df = pd.read_csv(p)
        df["seed"], df["fleet"] = int(m.group(1)), int(m.group(2))
        frames.append(df)
    if not frames:
        raise SystemExit(f"no us_home_seed*_fleet*.csv under {d}")
    df = pd.concat(frames, ignore_index=True)
    df["family"] = df.policy.where(~df.policy.str.startswith("time@"), "time@*")
    return df


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "results/fidelity")
    df = load(d)
    print(f"rows: {len(df)}, seeds {sorted(df.seed.unique())}, "
          f"fleets {sorted(df.fleet.unique())}, hosts per region "
          f"{df.groupby('fleet').hosts_per_region.first().to_dict()}")

    print("\n1. Late VMs (finish > 1 h after plan) and failed rows")
    g = df.groupby(["fleet", "total_capacity", "deadline_margin_h", "family"]).agg(
        rows=("policy", "size"),
        rows_with_late=("late_vms", lambda s: int((s > 0).sum())),
        late_vms_total=("late_vms", "sum"),
        max_lag_h=("max_lag_h", "max"),
        mean_lag_h=("mean_lag_h", "mean"),
        placement_fail=("placement_valid", lambda s: int((~s).sum())),
    )
    print(g.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\n2. Unfinished VMs: latest planned finish (h). Inside 768 h means")
    print("   the work was scheduled to complete and execution fell behind.")
    u = df[df.unfinished_planned_end_h >= 0]
    if len(u):
        print(u[["fleet", "seed", "policy", "total_capacity", "deadline_margin_h",
                 "unfinished_planned_end_h", "max_lag_h"]].to_string(index=False))
    else:
        print("   none")

    print("\n3. Attributed emissions, fleet350 against fleet200 (same seed and cell)")
    keys = ["seed", "policy", "total_capacity", "deadline_margin_h"]
    a = df[df.fleet == 200].set_index(keys).attributed_gco2
    b = df[df.fleet == 350].set_index(keys).attributed_gco2
    rel = (100 * (b / a - 1)).rename("change_pct").reset_index()
    rel["family"] = rel.policy.where(~rel.policy.str.startswith("time@"), "time@*")
    print(rel.groupby(["total_capacity", "deadline_margin_h", "family"]).change_pct
          .agg(["mean", "min", "max"]).to_string(float_format=lambda v: f"{v:+.2f}%"))

    print("\n4. Origin spread at 350 slots, 48 h margin (all rows kept, per seed)")
    t = df[df.policy.str.startswith("time@") & (df.total_capacity == 350)
           & (df.deadline_margin_h == 48)]
    for (fleet, seed), gg in t.groupby(["fleet", "seed"]):
        best, worst = gg.attributed_gco2.min(), gg.attributed_gco2.max()
        print(f"   fleet{fleet} seed{seed}: best {best/1000:6.0f}  worst {worst/1000:6.0f}  "
              f"ratio {worst/best:.2f}   late VMs {int(gg.late_vms.sum())}")


if __name__ == "__main__":
    main()
