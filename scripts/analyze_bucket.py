#!/usr/bin/env python3
"""
Top-bucket sensitivity: do the findings depend on the values substituted for
the Azure trace's ">24" core and ">64" GB buckets?

Compares results/bucket/low and results/bucket/high (run_bucket.ps1) with the
baseline substitution in results/r1, on both accounting bases, for the
quantities the manuscript reports from the US runs:

  margin effect (6 h to 48 h, space+time) at each capacity
  capacity effect (56 to 350 slots at 48 h)
  correlation of WaitAwhile emissions with origin mean CI and diurnal amplitude
  worst to best origin ratio
  reduction of space against round-robin at each capacity

It also counts, from the workloads kept beside each variant, how many of the
sampled VMs fall in a top bucket, since only those are changed.

Estimators are imported from analyze_r1.py, so the baseline column reproduces
the r1 figures exactly.

Usage:
    python scripts/analyze_bucket.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_r1 as ar  # noqa: E402

VARIANTS = [("baseline 30/70", Path("results/r1")),
            ("low 25/65", Path("results/bucket/low")),
            ("high 32/128", Path("results/bucket/high"))]


def top_bucket_counts():
    print("VMs in a top bucket, per sampled workload (changed by the substitution):")
    for name, d in VARIANTS[1:]:
        files = sorted(d.glob("vm-requests-seed*.csv"))
        if not files:
            raise SystemExit(f"no vm-requests-seed*.csv under {d}")
        core, mem = (25, 65 * 1024) if "low" in name else (32, 128 * 1024)
        for f in files:
            w = pd.read_csv(f)
            n_core = int((w.pes == core).sum())
            n_mem = int((w.ram_mb == mem).sum())
            hours = w.duration_h[(w.pes == core) | (w.ram_mb == mem)].sum()
            print(f"  {name:<12} {f.stem}: {n_core} by cores, {n_mem} by memory, "
                  f"{100 * hours / w.duration_h.sum():.1f}% of VM-hours")


def summarise(d, basis):
    full, home = ar.load(d, "us_full"), ar.load(d, "us_home")
    if full is None or home is None:
        raise SystemExit(f"us_full or us_home missing under {d}")
    _, effects, ce, _, _ = ar.section_a(full, basis, quiet=True)
    b = ar.section_b(home, basis, *ar.region_profiles(ar.US, ar.JAN), quiet=True)
    c = ar.section_c(home, basis, quiet=True)
    red = ar.reduction(full, basis)
    out = {}
    for cap, u, m, lo, hi, p, n in effects:
        out[f"margin effect, cap {cap} (%)"] = (m, lo, hi)
    out["capacity effect (%)"] = (ce[0], ce[1], ce[2])
    for cap, m, rc, rd, ps, share in b:
        out[f"r origin CI, cap {cap} m {m}"] = (rc[0], rc[1], rc[2])
        out[f"r origin diurnal, cap {cap} m {m}"] = (rd[0], rd[1], rd[2])
    for row in c:
        out[f"worst/best origin, cap {row[0]} m {row[1]}"] = (row[5], np.nan, np.nan)
    for cap in sorted(red.total_capacity.unique()):
        m, lo, hi, n = ar.mean_ci(red[red.total_capacity == cap].red)
        out[f"space vs RR, cap {cap} (%)"] = (m, lo, hi)
    return out


def main():
    top_bucket_counts()
    for basis in ar.BASES:
        print("\n" + "=" * 100)
        print(f"{basis} basis" + (" (primary)" if basis == "dyn" else ""))
        print("=" * 100)
        cols = [(name, summarise(d, basis)) for name, d in VARIANTS]
        keys = list(cols[0][1])
        print(f"{'quantity':<36}" + "".join(f"{name:>22}" for name, _ in cols))
        for k in keys:
            cells = []
            for _, res in cols:
                m, lo, hi = res[k]
                cells.append(f"{m:8.3f}" if np.isnan(lo) else f"{m:7.2f} [{lo:6.2f},{hi:6.2f}]")
            print(f"{k:<36}" + "".join(f"{c:>22}" for c in cells))


if __name__ == "__main__":
    main()
