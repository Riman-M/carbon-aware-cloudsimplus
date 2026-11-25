#!/usr/bin/env python3
"""
Pre-registered predictions for the hold-out region sets.

Status of the predictor. Capacity-filled headroom was identified as a
predictor of space-shifting savings AFTER the 30-subset results were seen,
so on those results it is exploratory. This script turns it into a test: it
computes predictions for 20 region sets that have never been simulated (ten
of four regions, ten of six) and writes them, with the criteria for success,
before any of those sets is run. The predictions file and this script are
committed and pushed before run_holdout.ps1 is started, so the repository
history records that the predictions came first.

The predictor. For a region set J with per-region slot limit s = C / |J| and
mean concurrent load L, fill the regions in order of increasing mean carbon
intensity, each up to s, until L is placed. Capacity-filled headroom is

    H_c = 1 - (sum_i f_i * CI_i) / (L * mean_j CI_j)

where f_i is the load placed in region i. It is the reduction a policy would
obtain by holding the mean load in the cleanest regions that can carry it,
against a baseline that spreads load evenly. It needs only the regional
means, the slot limit and the mean load; no simulation.

Two predictions are written for every set and capacity:

  raw         100 * H_c, with no fitted parameters
  calibrated  a + b * 100 * H_c, with a and b fitted per capacity level on
              the 30 five-region subsets, separately for each accounting basis

Criteria, fixed here before the hold-out sets are run (dynamic basis primary):

  C1  mean absolute error of the calibrated prediction, over the 80 hold-out
      set and capacity cells (seed means), is at most 5.0 percentage points
  C2  Pearson correlation between calibrated prediction and observation over
      the same 80 cells is at least 0.90
  C3  regressing observation on raw 100 * H_c over the 80 cells, the 95 %
      confidence interval of the slope contains 1.0

All three are reported whatever the outcome, on both bases.

Usage:
    python scripts/predict_holdout.py
"""
import argparse
import glob
import hashlib
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Total slot levels per set size, chosen so that the slots divide evenly over
# the regions and the utilisations stay close to 77, 50, 30 and 12 %. Levels
# are matched across sizes by rank.
CAPS = {4: [56, 84, 140, 352], 5: [55, 85, 140, 350], 6: [54, 84, 138, 348]}

CRITERIA = {"C1_mae_max_pp": 5.0, "C2_r_min": 0.90, "C3_slope_ci_contains": 1.0}


def content_hash(path):
    """SHA-256 of the file with line endings normalised, so that a checkout
    which converts LF and CRLF does not register as an edit."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def region_means(set_dirs):
    mean = {}
    for d in set_dirs:
        ci = pd.read_csv(d / "carbon_intensity.csv")
        for region, g in ci.groupby("region"):
            mean.setdefault(region, g.ci_gco2_per_kwh.mean())
    return mean


def filled_headroom(cis, total_slots, load):
    """H_c for one set: fill the cleanest regions up to their slot limit."""
    cis = sorted(cis)
    per_region = total_slots / len(cis)
    remaining, weighted = load, 0.0
    for ci in cis:
        f = min(per_region, remaining)
        weighted += f * ci
        remaining -= f
        if remaining <= 0:
            break
    if remaining > 1e-9:
        raise SystemExit(f"load {load:.1f} exceeds total slots {total_slots}; "
                         "H_c is undefined for an infeasible cell")
    return 1.0 - weighted / (load * float(np.mean(cis)))


def seed_loads(results_dir):
    """Mean concurrent load per workload seed, from the subset results. Every
    subset shares the same workload per seed, so any file gives the value."""
    loads = {}
    for p in glob.glob(str(results_dir / "sub*_core_seed*.csv")):
        seed = int(re.search(r"seed(\d+)", os.path.basename(p)).group(1))
        if seed not in loads:
            loads[seed] = pd.read_csv(p).vm_hours.iloc[0] / 720.0
    if not loads:
        raise SystemExit(f"no subset results under {results_dir}")
    return loads


def calibration(results_dir, subset_dir, loads, mean):
    desc = pd.read_csv(subset_dir / "subsets.csv")
    regs = dict(zip(desc.set, desc.regions.str.split("|")))
    frames = []
    for p in glob.glob(str(results_dir / "sub*_core_seed*.csv")):
        m = re.match(r"(sub\d+)_core_seed(\d+)", os.path.basename(p))
        df = pd.read_csv(p)
        df["set"], df["seed"] = m.group(1), int(m.group(2))
        frames.append(df)
    c = pd.concat(frames, ignore_index=True)
    if (~c.placement_valid).any():
        raise SystemExit("subset results contain rows failing the placement check")
    k = ["set", "seed", "total_capacity"]
    rr = c[c.policy == "round-robin"].set_index(k)
    sp = c[c.policy == "space"].set_index(k)
    r = pd.DataFrame({
        "dyn": 100 * (1 - sp.dynamic_gco2 / rr.dynamic_gco2),
        "attr": 100 * (1 - sp.attributed_gco2 / rr.attributed_gco2),
    }).reset_index()
    r["hc"] = [100 * filled_headroom([mean[x] for x in regs[s]], cap, loads[seed])
               for s, seed, cap in zip(r.set, r.seed, r.total_capacity)]
    g = r.groupby(["set", "total_capacity"]).mean(numeric_only=True).reset_index()
    fits = {}
    for rank, cap in enumerate(CAPS[5]):
        gg = g[g.total_capacity == cap]
        for basis in ("dyn", "attr"):
            b, a = np.polyfit(gg.hc, gg[basis], 1)
            fits[(rank, basis)] = (a, b)
    return fits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=Path("results/subsets"))
    ap.add_argument("--subsets", type=Path, default=Path("data/subsets"))
    ap.add_argument("--holdout", type=Path, nargs="+",
                    default=[Path("data/holdout-4"), Path("data/holdout-6")])
    ap.add_argument("--out", type=Path, default=Path("predictions/holdout-predictions.csv"))
    args = ap.parse_args()

    set_dirs = [d for root in [args.subsets, *args.holdout]
                for d in sorted(root.iterdir()) if d.is_dir()]
    mean = region_means(set_dirs)
    loads = seed_loads(args.results)
    fits = calibration(args.results, args.subsets, loads, mean)

    rows = []
    for root in args.holdout:
        desc = pd.read_csv(root / "subsets.csv")
        for _, s in desc.iterrows():
            regions = s.regions.split("|")
            size = len(regions)
            for rank, cap in enumerate(CAPS[size]):
                hc = np.mean([100 * filled_headroom([mean[x] for x in regions], cap, L)
                              for L in loads.values()])
                row = {"set": s.set, "size": size, "regions": s.regions,
                       "total_capacity": cap, "util_rank": rank,
                       "dominance": s.dominance, "headroom": s.headroom,
                       "hc_pct": round(hc, 3), "pred_raw": round(hc, 3)}
                for basis in ("dyn", "attr"):
                    a, b = fits[(rank, basis)]
                    row[f"pred_{basis}"] = round(a + b * hc, 3)
                    row[f"cal_a_{basis}"] = round(a, 4)
                    row[f"cal_b_{basis}"] = round(b, 4)
                rows.append(row)

    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    digest = content_hash(args.out)

    crit = args.out.with_name("holdout-criteria.txt")
    crit.write_text(
        "Pre-registered criteria for the hold-out region sets\n"
        "Written by scripts/predict_holdout.py before any hold-out set was simulated.\n\n"
        f"predictions file: {args.out.as_posix()}\n"
        f"sha256: {digest}\n"
        f"workload seeds and mean loads: "
        + ", ".join(f"{k}={v:.2f}" for k, v in sorted(loads.items())) + "\n"
        f"cells: {len(out)} ({out.set.nunique()} sets x 4 capacity levels)\n\n"
        "Primary basis: dynamic. Policy: space against round-robin.\n"
        f"C1  MAE of calibrated prediction <= {CRITERIA['C1_mae_max_pp']} percentage points\n"
        f"C2  Pearson r(calibrated prediction, observed) >= {CRITERIA['C2_r_min']}\n"
        f"C3  95 % CI of slope (observed on raw H_c) contains {CRITERIA['C3_slope_ci_contains']}\n"
        "All criteria are reported on both bases whatever the outcome.\n")

    print(f"wrote {len(out)} predictions -> {args.out}")
    print(f"sha256 {digest}")
    print(f"wrote criteria -> {crit}")
    print("\ncalibration fitted on the 30 five-region subsets (prediction = a + b * H_c):")
    for (rank, basis), (a, b) in sorted(fits.items()):
        print(f"  level {rank} (cap {CAPS[5][rank]:>3})  {basis:<4}  a = {a:7.2f}  b = {b:.3f}")


if __name__ == "__main__":
    main()
