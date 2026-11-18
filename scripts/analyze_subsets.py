#!/usr/bin/env python3
"""
Analyse the region-set composition experiment.

Reads results/subsets/*_core_seed*.csv and *_origin_seed*.csv together with
data/subsets/subsets.csv, and reports:

  1. For each capacity, the reduction that space shifting (and space+time)
     achieves against round-robin in each of the subsets, averaged over
     workload seeds.
  2. Simple regressions of that reduction on each composition descriptor
     (log dominance, log spread, headroom) with R^2, slope, 95 % CI and p.
  3. A two-predictor regression on log dominance and headroom, the pair the
     design separates best, to establish which descriptor carries the effect.
  4. The concentration mechanism as a multivariate model: reduction against
     the busiest-region share with capacity, policy and headroom as
     covariates, so that the share's coefficient is not a proxy for them.
  5. The origin-region finding repeated in every subset: per subset, the
     correlation of WaitAwhile emissions with origin mean CI and with origin
     diurnal amplitude, and the share of work that stayed in the origin.

Only numpy, scipy and pandas are required.

Usage:
    python scripts/analyze_subsets.py [results/subsets] [data/subsets] [attr|dyn]
"""
import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


# Accounting basis for every reduction reported: "attr" (attributed, full draw
# of active hosts) or "dyn" (draw above idle only). The fidelity diagnostic
# showed the attributed basis depends on how many hosts are provisioned,
# through the allocator's spreading of VMs, while the dynamic basis does not,
# so both should be read.
BASIS = "attr"

# ---------------------------------------------------------------- loading

def load(results_dir, kind):
    frames = []
    for path in sorted(glob.glob(str(results_dir / f"sub*_{kind}_seed*.csv"))):
        m = re.match(r"(sub\d+)_" + kind + r"_seed(\d+)\.csv", os.path.basename(path))
        df = pd.read_csv(path)
        df["set"] = m.group(1)
        df["seed"] = int(m.group(2))
        frames.append(df)
    if not frames:
        return None
    out = pd.concat(frames, ignore_index=True)
    bad = out[~out.placement_valid]
    if len(bad):
        print(f"WARNING: {len(bad)} {kind} rows failed the placement check and are excluded:")
        print(bad.groupby(["set", "policy", "total_capacity"]).size().to_string())
    if "late_vms" in out.columns:
        late = out[out.late_vms > 0]
        if len(late):
            print(f"WARNING: {len(late)} {kind} rows contain VMs finishing more than 1 h "
                  f"after plan (max lag {out.max_lag_h.max():.1f} h); their emissions "
                  f"do not describe the planned schedule:")
            print(late.groupby(["policy", "total_capacity"]).size().to_string())
    return out[out.placement_valid].copy()


def region_profiles(subset_dir):
    """Mean CI and repeatable diurnal amplitude per region, from the traces the
    simulator actually read, so that the descriptors cannot drift from the input."""
    mean, diurnal = {}, {}
    for d in sorted(x for x in subset_dir.glob("sub*") if x.is_dir()):
        ci = pd.read_csv(d / "carbon_intensity.csv")
        for region, g in ci.groupby("region"):
            if region in mean:
                continue
            m = g.ci_gco2_per_kwh.mean()
            hod = g.groupby(g.hour % 24).ci_gco2_per_kwh.mean()
            mean[region] = m
            diurnal[region] = 100 * (hod.max() - hod.min()) / m
    return mean, diurnal


# ---------------------------------------------------------------- statistics

def ols(y, X, names):
    """Ordinary least squares with an intercept. Returns a table of
    coefficient, standard error, 95 % CI and two-sided p, and R^2."""
    X = np.column_stack([np.ones(len(y)), X])
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = n - k
    s2 = resid @ resid / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    t = beta / se
    p = 2 * stats.t.sf(np.abs(t), dof)
    q = stats.t.ppf(0.975, dof)
    r2 = 1 - (resid @ resid) / ((y - y.mean()) @ (y - y.mean()))
    tab = pd.DataFrame({"coef": beta, "se": se, "lo95": beta - q * se,
                        "hi95": beta + q * se, "p": p},
                       index=["intercept"] + names)
    return tab, r2, n


def corr_ci(r, n):
    """Fisher-z 95 % interval for a Pearson coefficient."""
    if n <= 3 or abs(r) >= 1:
        return (np.nan, np.nan)
    z = np.arctanh(r)
    h = 1.96 / np.sqrt(n - 3)
    return (np.tanh(z - h), np.tanh(z + h))


def fmt_p(p):
    return f"{p:.1e}" if p < 1e-3 else f"{p:.3f}"


# ---------------------------------------------------------------- analyses

def reductions(core, desc):
    """Per-seed reduction against round-robin, then mean over seeds per set."""
    keys = ["set", "seed", "total_capacity", "deadline_margin_h"]
    base = core[core.policy == "round-robin"].set_index(keys)
    rows = []
    for pol in ["space", "space+time"]:
        p = core[core.policy == pol].set_index(keys)
        joined = p.join(base[["attributed_gco2", "dynamic_gco2"]], rsuffix="_rr", how="inner")
        joined["red_attr"] = 100 * (1 - joined.attributed_gco2 / joined.attributed_gco2_rr)
        joined["red_dyn"] = 100 * (1 - joined.dynamic_gco2 / joined.dynamic_gco2_rr)
        joined["red"] = joined["red_" + BASIS]
        joined["policy"] = pol
        rows.append(joined.reset_index())
    r = pd.concat(rows, ignore_index=True)
    r = r.merge(desc, on="set")
    return r


def report_by_capacity(r):
    print("\n" + "=" * 78)
    print("1. Reduction against round-robin, mean over seeds, by capacity")
    print("=" * 78)
    for pol, g in r.groupby("policy"):
        per = g.groupby(["total_capacity", "set"]).red.mean().unstack(0)
        print(f"\n  {pol}: {BASIS} reduction (%), min / median / max across subsets")
        for cap in per.columns:
            col = per[cap]
            print(f"    cap {cap:>4d}  {col.min():6.1f}  {col.median():6.1f}  {col.max():6.1f}"
                  f"   (sd across seeds, median {g[g.total_capacity == cap].groupby('set').red.std().median():.1f})")


def report_simple(r):
    print("\n" + "=" * 78)
    print("2. Which composition descriptor predicts the reduction? (space, per capacity)")
    print("=" * 78)
    s = r[r.policy == "space"].groupby(["total_capacity", "set"]).agg(
        red=("red", "mean"), dominance=("dominance", "first"),
        spread=("spread", "first"), headroom=("headroom", "first")).reset_index()
    s["log_dominance"] = np.log(s.dominance)
    s["log_spread"] = np.log(s.spread)
    print(f"\n  {'cap':>5}  {'descriptor':<14} {'R2':>6} {'slope':>9} {'95% CI':>20} {'p':>9}")
    for cap, g in s.groupby("total_capacity"):
        for d in ["log_dominance", "log_spread", "headroom"]:
            tab, r2, n = ols(g.red.values, g[[d]].values, [d])
            row = tab.loc[d]
            print(f"  {cap:>5d}  {d:<14} {r2:6.3f} {row.coef:9.2f} "
                  f"[{row.lo95:8.2f}, {row.hi95:8.2f}] {fmt_p(row.p):>9}")
        print()
    return s


def report_joint(s):
    print("=" * 78)
    print("3. Joint model: reduction ~ log dominance + headroom (space, per capacity)")
    print("=" * 78)
    print("  If dominance carries the effect, its coefficient survives with headroom")
    print("  in the model; if headroom does, dominance's coefficient collapses.\n")
    for cap, g in s.groupby("total_capacity"):
        tab, r2, n = ols(g.red.values, g[["log_dominance", "headroom"]].values,
                         ["log_dominance", "headroom"])
        print(f"  cap {cap}:  R2 = {r2:.3f}, n = {n}")
        for name in ["log_dominance", "headroom"]:
            row = tab.loc[name]
            print(f"     {name:<14} {row.coef:8.2f}  [{row.lo95:7.2f}, {row.hi95:7.2f}]  p = {fmt_p(row.p)}")
        print()


def report_concentration(r):
    print("=" * 78)
    print("4. Concentration mechanism, multivariate (Reviewer 1)")
    print("=" * 78)
    print("  reduction ~ busiest-region share + capacity (dummies) + policy + headroom")
    print("  cell level: every set x seed x capacity x policy\n")
    d = r.copy()
    caps = sorted(d.total_capacity.unique())
    X = [d.max_region_share.values * 100]
    names = ["share_pct"]
    for c in caps[1:]:
        X.append((d.total_capacity == c).astype(float).values)
        names.append(f"cap_{c}")
    X.append((d.policy == "space+time").astype(float).values)
    names.append("policy_space+time")
    X.append(d.headroom.values)
    names.append("headroom")
    tab, r2, n = ols(d.red.values, np.column_stack(X), names)
    print(f"  R2 = {r2:.3f}, n = {n}")
    shown = tab.copy()
    shown["p"] = shown.p.map(fmt_p)
    print(shown.to_string(float_format=lambda v: f"{v:9.3f}"))

    # The bivariate figure the manuscript reported, for comparison.
    rr = stats.pearsonr(d.max_region_share, d.red)
    print(f"\n  bivariate corr(share, reduction) = {rr.statistic:+.3f}, n = {len(d)}")
    print("  Note: share correlates with emissions negatively and with reduction positively.")


def report_origin(origin, mean_ci, diurnal):
    print("\n" + "=" * 78)
    print("5. Origin-region finding, repeated in every subset (WaitAwhile)")
    print("=" * 78)
    t = origin[origin.policy.str.startswith("time@")].copy()
    t["home"] = t.policy.str.split("@").str[1]
    t["home_ci"] = t.home.map(mean_ci)
    t["home_diurnal"] = t.home.map(diurnal)
    # Coefficients on the across-seed mean per origin: one estimator throughout.
    g = t.groupby(["set", "total_capacity", "home"]).agg(
        attr=("attributed_gco2", "mean"), share=("max_region_share", "mean"),
        ci=("home_ci", "first"), di=("home_diurnal", "first")).reset_index()
    rows = []
    for (s, cap), gg in g.groupby(["set", "total_capacity"]):
        rows.append({"set": s, "cap": cap,
                     "r_ci": stats.pearsonr(gg.ci, gg.attr).statistic,
                     "r_di": stats.pearsonr(gg.di, gg.attr).statistic,
                     "home_share": gg.share.mean(),
                     "spread": gg.attr.max() / gg.attr.min()})
    o = pd.DataFrame(rows)
    for cap, gg in o.groupby("cap"):
        print(f"\n  cap {cap}: across {len(gg)} subsets")
        print(f"    corr(origin mean CI, emissions):  median {gg.r_ci.median():+.3f}, "
              f"range [{gg.r_ci.min():+.3f}, {gg.r_ci.max():+.3f}], "
              f"{(gg.r_ci > 0.8).sum()} of {len(gg)} above +0.8")
        print(f"    corr(origin diurnal, emissions):  median {gg.r_di.median():+.3f}, "
              f"range [{gg.r_di.min():+.3f}, {gg.r_di.max():+.3f}]")
        print(f"    share of work in origin region:   median {gg.home_share.median():.2f}, "
              f"range [{gg.home_share.min():.2f}, {gg.home_share.max():.2f}]")
        print(f"    worst/best origin ratio:          median {gg.spread.median():.2f}, "
              f"range [{gg.spread.min():.2f}, {gg.spread.max():.2f}]")
    return o


def main():
    global BASIS
    results_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "results/subsets")
    subset_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "data/subsets")
    BASIS = sys.argv[3] if len(sys.argv) > 3 else "attr"
    if BASIS not in ("attr", "dyn"):
        raise SystemExit("basis must be attr or dyn")
    print(f"accounting basis: {BASIS}")
    desc = pd.read_csv(subset_dir / "subsets.csv")

    core = load(results_dir, "core")
    if core is None:
        raise SystemExit(f"no sub*_core_seed*.csv under {results_dir}")
    print(f"core: {core.set.nunique()} subsets, {core.seed.nunique()} seeds, "
          f"capacities {sorted(core.total_capacity.unique())}")

    r = reductions(core, desc)
    report_by_capacity(r)
    s = report_simple(r)
    report_joint(s)
    report_concentration(r)

    out = results_dir / f"summary_{BASIS}"
    out.mkdir(exist_ok=True)
    r.to_csv(out / "reductions_by_cell.csv", index=False)
    s.to_csv(out / "space_reduction_by_set.csv", index=False)

    origin = load(results_dir, "origin")
    if origin is not None:
        mean_ci, diurnal = region_profiles(subset_dir)
        o = report_origin(origin, mean_ci, diurnal)
        o.to_csv(out / "origin_by_set.csv", index=False)
    print(f"\nsummary tables written to {out}")


if __name__ == "__main__":
    main()
