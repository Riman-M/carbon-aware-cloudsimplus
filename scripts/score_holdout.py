#!/usr/bin/env python3
"""
Score the hold-out run against the pre-registered predictions.

Reads predictions/holdout-predictions.csv (committed before the run) and
results/holdout/*.csv, and applies the criteria in
predictions/holdout-criteria.txt on both accounting bases. The criteria are
reported whatever the outcome; nothing here is tuned to the observations.

Before scoring, the predictions file is checked against the SHA-256 recorded
in the criteria file, so a prediction edited after the run cannot be scored
as if it were the original.

Each seed's observed mean load (VM-hours / 720) must equal the load written
into the criteria file, so that a run on a different workload sample is not
scored against predictions computed for another.

Usage:
    python scripts/score_holdout.py
    python scripts/score_holdout.py --pred predictions/holdout-b-azure-predictions.csv \
        --results results/holdout-b/azure
"""
import argparse
import glob
import hashlib
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, default=Path("predictions/holdout-predictions.csv"))
    ap.add_argument("--criteria", type=Path)
    ap.add_argument("--results", type=Path, default=Path("results/holdout"))
    args = ap.parse_args()
    PRED, RESULTS = args.pred, args.results
    CRIT = args.criteria or PRED.with_name(
        PRED.name.replace("predictions", "criteria").replace(".csv", ".txt"))
    recorded = re.search(r"sha256: ([0-9a-f]{64})", CRIT.read_text()).group(1)
    # Line endings are normalised, as in predict_holdout.py.
    actual = hashlib.sha256(PRED.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if recorded != actual:
        raise SystemExit("predictions file does not match the hash recorded before the run")
    pred = pd.read_csv(PRED)

    frames = []
    for p in sorted(glob.glob(str(RESULTS / "h*-seed*.csv"))):
        m = re.match(r"(h[0-9a-z]+-\d+)-seed(\d+)\.csv", os.path.basename(p))
        df = pd.read_csv(p)
        df["set"], df["seed"] = m.group(1), int(m.group(2))
        frames.append(df)
    if not frames:
        raise SystemExit(f"no hold-out results under {RESULTS}")
    obs = pd.concat(frames, ignore_index=True)
    print(f"result files: {len(frames)} (expected 60), sets {obs.set.nunique()}, "
          f"seeds {sorted(obs.seed.unique())}")

    line = re.search(r"workload seeds and mean loads: (.+)", CRIT.read_text()).group(1)
    expected = {int(k): float(v) for k, v in (kv.split("=") for kv in line.split(", "))}
    # Every result file is checked, not one per seed, so that a single file run
    # on a different sample cannot pass unnoticed.
    per_file = obs.groupby(["set", "seed"]).vm_hours.first() / 720
    for (set_name, seed), load in per_file.items():
        if seed not in expected or abs(load - expected[seed]) > 0.006:
            raise SystemExit(f"{set_name} seed {seed}: observed load {load:.3f} differs "
                             f"from the pre-registered {expected.get(seed)}; this run "
                             f"used a different workload sample")
    observed = per_file.groupby(level="seed").first().to_dict()
    print("workload loads match the pre-registration: "
          + ", ".join(f"{k}={v:.2f}" for k, v in sorted(observed.items())))

    bad = obs[~obs.placement_valid | (obs.late_vms > 0)]
    if len(bad):
        print(f"WARNING: {len(bad)} rows fail the placement or timing check; listed and excluded:")
        print(bad[["set", "seed", "policy", "total_capacity", "cloudlets_finished",
                   "late_vms"]].to_string(index=False))
        obs = obs.drop(bad.index)

    k = ["set", "seed", "total_capacity"]
    rr = obs[obs.policy == "round-robin"].set_index(k)
    sp = obs[obs.policy == "space"].set_index(k)
    red = pd.DataFrame({
        "dyn": 100 * (1 - sp.dynamic_gco2 / rr.dynamic_gco2),
        "attr": 100 * (1 - sp.attributed_gco2 / rr.attributed_gco2),
    }).dropna().reset_index()
    seeds_per_cell = red.groupby(["set", "total_capacity"]).seed.nunique()
    if (seeds_per_cell < 3).any():
        print("WARNING: cells with fewer than 3 seeds:")
        print(seeds_per_cell[seeds_per_cell < 3].to_string())
    cell = red.groupby(["set", "total_capacity"])[["dyn", "attr"]].mean().reset_index()
    m = pred.merge(cell, on=["set", "total_capacity"], how="inner")
    print(f"cells scored: {len(m)} of {len(pred)} predicted")

    for basis in ("dyn", "attr"):
        err = m[basis] - m[f"pred_{basis}"]
        mae = err.abs().mean()
        r = stats.pearsonr(m[f"pred_{basis}"], m[basis]).statistic
        lr = stats.linregress(m.pred_raw, m[basis])
        q = stats.t.ppf(0.975, len(m) - 2)
        lo, hi = lr.slope - q * lr.stderr, lr.slope + q * lr.stderr
        tag = "PRIMARY" if basis == "dyn" else "secondary"
        print(f"\n== {basis} basis ({tag})")
        print(f"  C1  MAE {mae:.2f} pp (criterion <= 5.0)            "
              f"{'PASS' if mae <= 5.0 else 'FAIL'}")
        print(f"  C2  r   {r:.3f} (criterion >= 0.90)                "
              f"{'PASS' if r >= 0.90 else 'FAIL'}")
        print(f"  C3  slope on raw H_c {lr.slope:.3f} [{lo:.3f}, {hi:.3f}] "
              f"(criterion: contains 1.0)  {'PASS' if lo <= 1.0 <= hi else 'FAIL'}")
        print(f"      intercept {lr.intercept:.2f}, R2 {lr.rvalue**2:.3f}")
        by = m.assign(abs_err=err.abs()).groupby(["size", "util_rank"]).abs_err.agg(["mean", "max"])
        print("  absolute error by set size and capacity level (pp):")
        print(by.to_string(float_format=lambda v: f"{v:6.2f}"))

    out = RESULTS / "score"
    out.mkdir(exist_ok=True)
    m.to_csv(out / "holdout-scored.csv", index=False)
    print(f"\ncell-level table written to {out / 'holdout-scored.csv'}")


if __name__ == "__main__":
    main()
