#!/usr/bin/env python3
"""
Rebuild every table and headline number of Paper A from the revision runs.

Reads results/r1/ (written by run_robust_r1.ps1) and produces, for each
accounting basis (dynamic primary, attributed secondary):

  A  capacity and deadline margin: space+time emissions per cell, the margin
     effect at each capacity and the capacity effect, each measured per seed
     and reported with a 95 % confidence interval and a one-sample t-test
  B  origin region: correlation of WaitAwhile emissions with the origin's mean
     carbon intensity and with its diurnal amplitude, with Fisher intervals
  C  origin spread: best, median and worst origin against space shifting
  D  region set: reduction of space against round-robin for US and EU at
     matched utilisation, with Welch tests and the EU to US ratio
  E  robustness: the July window, the delay-insensitive class and the linear
     power model, each set against the main result

One estimator is used throughout. Wherever a quantity is summarised over
origins, each origin is first averaged over seeds, and the summary (minimum,
median, maximum, correlation) is taken over those origin means. Effects that
compare two cells (a margin effect, a reduction against round-robin) are
computed within each seed and then averaged, so that the interval reflects
workload sampling. Rows failing the placement or timing check are listed and
excluded, and any cell left with fewer seeds than the others is reported.

Every number the revised manuscript quotes is written to
results/r1/tables/headline-numbers.csv, so that no figure in the text lacks a
file behind it. LaTeX tables are written beside it.

If results/robust/ (the published pass) is present, the headline numbers are
also computed from it on the attributed basis and printed beside the new
ones, for the response to reviewers.

Usage:
    python scripts/analyze_r1.py [results/r1]
"""
import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_traces as pt  # noqa: E402

COL = {"dyn": "dynamic_gco2", "attr": "attributed_gco2"}
BASES = ("dyn", "attr")
US = pt.REAL_REGION_SETS["us"]
EU = pt.REAL_REGION_SETS["eu"]
JAN, JULY = 0, 4320
HEADLINE = []


# ---------------------------------------------------------------- helpers

def headline(name, basis, value, lo=np.nan, hi=np.nan, p=np.nan, n=np.nan, note=""):
    HEADLINE.append({"name": name, "basis": basis, "value": value, "ci_lo": lo,
                     "ci_hi": hi, "p": p, "n": n, "note": note})


def mean_ci(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return np.nan, np.nan, np.nan, 0
    m = x.mean()
    if n < 2:
        return m, np.nan, np.nan, n
    h = stats.t.ppf(0.975, n - 1) * x.std(ddof=1) / np.sqrt(n)
    return m, m - h, m + h, n


def one_sample_p(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    return stats.ttest_1samp(x, 0.0).pvalue if len(x) > 1 else np.nan


def pearson_fisher(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = len(a)
    r, p = stats.pearsonr(a, b)
    if n > 3 and abs(r) < 1:
        z, h = np.arctanh(r), 1.96 / np.sqrt(n - 3)
        return r, np.tanh(z - h), np.tanh(z + h), p, n
    return r, np.nan, np.nan, p, n


def fmt_p(p):
    if np.isnan(p):
        return "n/a"
    return f"{p:.1e}" if p < 1e-3 else f"{p:.3f}"


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------- loading

def load(base, arm):
    frames = []
    for p in sorted(glob.glob(str(base / f"{arm}_seed*.csv"))):
        seed = int(re.search(r"seed(\d+)\.csv$", os.path.basename(p)).group(1))
        df = pd.read_csv(p)
        df["seed"] = seed
        frames.append(df)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    late = df.late_vms > 0 if "late_vms" in df.columns else pd.Series(False, index=df.index)
    bad = df[~df.placement_valid | late]
    if len(bad):
        print(f"  {arm}: {len(bad)} of {len(df)} rows fail the placement or timing check, excluded:")
        print(bad.groupby(["policy", "total_capacity", "deadline_margin_h"]).seed
              .apply(lambda s: sorted(s)).to_string())
    df = df.drop(bad.index)
    df["family"] = df.policy.where(~df.policy.str.startswith("time@"), "time")
    df["home"] = df.policy.str.extract(r"time@(.+)")[0]
    return df


def region_profiles(regions, start_hour):
    rows = pt.load_carboncast(Path("data/carboncast"), regions, 720, start_hour)
    by, hod = {}, {}
    for hour, region, ci in rows:
        by.setdefault(region, []).append(ci)
        hod.setdefault(region, {}).setdefault(hour % 24, []).append(ci)
    mean = {r: float(np.mean(v)) for r, v in by.items()}
    diurnal = {r: 100 * (max(np.mean(v) for v in b.values())
                         - min(np.mean(v) for v in b.values())) / mean[r]
               for r, b in hod.items()}
    return mean, diurnal


def filled_headroom(cis, total_slots, load):
    cis = sorted(cis)
    per, rem, acc = total_slots / len(cis), load, 0.0
    for ci in cis:
        f = min(per, rem)
        acc += f * ci
        rem -= f
        if rem <= 0:
            break
    return 1 - acc / (load * np.mean(cis)) if rem <= 1e-9 else np.nan


# ---------------------------------------------------------------- A

def section_a(full, basis, tag="", quiet=False):
    col = COL[basis]
    st = full[full.policy == "space+time"]
    caps = sorted(st.total_capacity.unique())
    margins = sorted(st.deadline_margin_h.unique())
    util = {c: 100 * full[full.total_capacity == c].vm_hours.mean() / (c * 720) for c in caps}
    piv = st.pivot_table(index=["seed", "total_capacity"], columns="deadline_margin_h",
                         values=col) / 1000

    cells = []
    for c in caps:
        row = {"cap": c, "util": util[c]}
        for m in margins:
            x = piv.xs(c, level="total_capacity")[m]
            mm, lo, hi, n = mean_ci(x)
            row[m] = (mm, (hi - lo) / 2 if n > 1 else np.nan, n)
        cells.append(row)

    effects = []
    for c in caps:
        sub = piv.xs(c, level="total_capacity")
        e = 100 * (1 - sub[margins[-1]] / sub[margins[0]])
        m, lo, hi, n = mean_ci(e)
        effects.append((c, util[c], m, lo, hi, one_sample_p(e), n))
    capeff = 100 * (1 - piv.xs(caps[-1], level="total_capacity")[margins[-1]]
                    / piv.xs(caps[0], level="total_capacity")[margins[-1]])
    ce = mean_ci(capeff)

    if not quiet:
        section(f"A{tag}  Capacity and deadline margin (space+time, {basis} basis)")
        print(f"  emissions kgCO2eq, mean with 95% CI half-width; seeds per cell in brackets")
        print(f"  {'cap':>4} {'util':>6}  " + "  ".join(f"{m:>16}h" for m in margins))
        for r in cells:
            print(f"  {r['cap']:>4} {r['util']:5.1f}%  " + "  ".join(
                f"{r[m][0]:8.1f} +/-{r[m][1]:5.1f} [{r[m][2]}]" for m in margins))
        print(f"\n  margin effect {margins[0]} h -> {margins[-1]} h, per seed then averaged:")
        for c, u, m, lo, hi, p, n in effects:
            print(f"  cap {c:>4} ({u:4.1f}%):  {m:6.1f}%  [{lo:6.1f}, {hi:6.1f}]  p = {fmt_p(p)}  n = {n}")
        print(f"  capacity effect {caps[0]} -> {caps[-1]} at {margins[-1]} h:  "
              f"{ce[0]:.1f}%  [{ce[1]:.1f}, {ce[2]:.1f}]  n = {ce[3]}")

        viol = full.groupby(["total_capacity", "deadline_margin_h", "family"]).deadline_violations
        print("\n  deadline violations (of the requests), mean and max over seeds, by cap and margin:")
        v = viol.agg(["mean", "max"]).unstack("family")
        print(v.to_string(float_format=lambda x: f"{x:6.1f}"))

    return cells, effects, ce, util, margins


# ---------------------------------------------------------------- B, C

def origin_means(home, basis):
    t = home[home.family == "time"]
    return (t.groupby(["total_capacity", "deadline_margin_h", "home"])[COL[basis]]
            .mean().div(1000).rename("e").reset_index())


def section_b(home, basis, mean_ci_map, diurnal_map, tag="", quiet=False):
    g = origin_means(home, basis)
    rows = []
    for (c, m), gg in g.groupby(["total_capacity", "deadline_margin_h"]):
        rc = pearson_fisher(gg.home.map(mean_ci_map), gg.e)
        rd = pearson_fisher(gg.home.map(diurnal_map), gg.e)
        per_seed = []
        t = home[(home.family == "time") & (home.total_capacity == c) & (home.deadline_margin_h == m)]
        for s, ts in t.groupby("seed"):
            if ts.home.nunique() >= 4:
                per_seed.append(stats.pearsonr(ts.home.map(mean_ci_map), ts[COL[basis]])[0])
        share = t.max_region_share.mean()
        rows.append((c, m, rc, rd, (min(per_seed), max(per_seed)) if per_seed else (np.nan,) * 2, share))
    if not quiet:
        section(f"B{tag}  What predicts WaitAwhile's emissions ({basis} basis)")
        print("  Pearson r over origins, each origin averaged over seeds; 95% Fisher CI; p")
        for c, m, rc, rd, ps, share in rows:
            print(f"  cap {c:>3} m {m:>2}:  origin CI  {rc[0]:+.3f} [{rc[1]:+.3f}, {rc[2]:+.3f}] p={fmt_p(rc[3])}"
                  f"   diurnal {rd[0]:+.3f} [{rd[1]:+.3f}, {rd[2]:+.3f}] p={fmt_p(rd[3])}"
                  f"   n={rc[4]}  per-seed CI r [{ps[0]:+.3f}, {ps[1]:+.3f}]  home share {share:.2f}")
    return rows


def section_c(home, basis, tag="", quiet=False):
    g = origin_means(home, basis)
    sp = home[home.policy == "space"].groupby(["total_capacity", "deadline_margin_h"])[COL[basis]].mean() / 1000
    rows = []
    for (c, m), gg in g.groupby(["total_capacity", "deadline_margin_h"]):
        best, worst = gg.e.min(), gg.e.max()
        rows.append((c, m, best, gg.e.median(), worst, worst / best, sp.loc[(c, m)],
                     gg.loc[gg.e.idxmin(), "home"], gg.loc[gg.e.idxmax(), "home"]))
    if not quiet:
        section(f"C{tag}  Spread across origins ({basis} basis, kgCO2eq, origin means over seeds)")
        print(f"  {'cap':>4} {'m':>3} {'best':>9} {'median':>9} {'worst':>9} {'ratio':>6} {'space':>9}")
        for c, m, b, md, w, ratio, s, bn, wn in rows:
            print(f"  {c:>4} {m:>3} {b:9.1f} {md:9.1f} {w:9.1f} {ratio:6.2f} {s:9.1f}   best {bn}, worst {wn}")
    return rows


# ---------------------------------------------------------------- D

def reduction(df, basis, policy="space"):
    k = ["seed", "total_capacity", "deadline_margin_h"]
    p = df[df.policy == policy].set_index(k)[COL[basis]]
    b = df[df.policy == "round-robin"].set_index(k)[COL[basis]]
    r = (100 * (1 - p / b)).rename("red").reset_index()
    # space and round-robin place at arrival, so the margin does not enter;
    # average over margins within a seed so each seed counts once.
    return r.groupby(["seed", "total_capacity"]).red.mean().reset_index()


def section_d(us, eu, basis, us_mean, eu_mean, quiet=False):
    ru, re_ = reduction(us, basis), reduction(eu, basis)
    uc, ec = sorted(ru.total_capacity.unique()), sorted(re_.total_capacity.unique())
    rows = []
    for c_u, c_e in zip(uc, ec):
        u = ru[ru.total_capacity == c_u].red
        e = re_[re_.total_capacity == c_e].red
        mu, mel = mean_ci(u), mean_ci(e)
        p = stats.ttest_ind(e, u, equal_var=False).pvalue
        uu = 100 * us[us.total_capacity == c_u].vm_hours.mean() / (c_u * 720)
        ue = 100 * eu[eu.total_capacity == c_e].vm_hours.mean() / (c_e * 720)
        load_u = us.vm_hours.mean() / 720
        load_e = eu.vm_hours.mean() / 720
        hc_u = 100 * filled_headroom(list(us_mean.values()), c_u, load_u)
        hc_e = 100 * filled_headroom(list(eu_mean.values()), c_e, load_e)
        rows.append((c_u, uu, mu, c_e, ue, mel, p, mel[0] / mu[0], hc_u, hc_e))
    if not quiet:
        section(f"D  Region set: space against round-robin ({basis} basis)")
        print("  per-seed reduction, mean [95% CI]; Welch test EU vs US; H_c = capacity-filled headroom")
        for c_u, uu, mu, c_e, ue, mel, p, ratio, hcu, hce in rows:
            print(f"  US cap {c_u:>3} ({uu:4.1f}%): {mu[0]:5.1f} [{mu[1]:5.1f}, {mu[2]:5.1f}] H_c {hcu:5.1f}"
                  f"   EU cap {c_e:>3} ({ue:4.1f}%): {mel[0]:5.1f} [{mel[1]:5.1f}, {mel[2]:5.1f}] H_c {hce:5.1f}"
                  f"   p={fmt_p(p)}  EU/US {ratio:.2f}")
    return rows


# ---------------------------------------------------------------- LaTeX

def tex_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    print(f"  wrote {path}")


def tex_tables(out, basis, a, b, c, d):
    cells, effects, ce, util, margins = a
    unit = "kgCO\\textsubscript{2}eq"
    lines = ["\\begin{tabular}{rr" + "r" * len(margins) + "r}", "\\toprule",
             "& & \\multicolumn{%d}{c}{Deadline margin (h)} & \\\\" % len(margins),
             "\\cmidrule(lr){3-%d}" % (2 + len(margins)),
             "Slots & Utilisation & " + " & ".join(str(m) for m in margins)
             + f" & Effect of margin (\\%) \\\\", "\\midrule"]
    for r, eff in zip(cells, effects):
        vals = " & ".join(f"{r[m][0]:.1f} ({r[m][1]:.1f})" for m in margins)
        lines.append(f"{r['cap']} & {r['util']:.0f}\\,\\% & {vals} & "
                     f"{eff[2]:.1f} [{eff[3]:.1f}, {eff[4]:.1f}] \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    tex_write(out / f"table-capacity-margin-{basis}.tex",
              f"% space+time emissions ({unit}), mean (95% CI half-width) over seeds; {basis} basis\n"
              + "\n".join(lines) + "\n")

    lines = ["\\begin{tabular}{rrrrr}", "\\toprule",
             "Slots & Margin (h) & Origin mean CI & Origin diurnal amplitude & Share in origin \\\\",
             "\\midrule"]
    for cap, m, rc, rd, ps, share in b:
        lines.append(f"{cap} & {m} & ${rc[0]:+.3f}$ [{rc[1]:+.2f}, {rc[2]:+.2f}] & "
                     f"${rd[0]:+.3f}$ [{rd[1]:+.2f}, {rd[2]:+.2f}] & {share:.2f} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    tex_write(out / f"table-origin-correlation-{basis}.tex",
              f"% Pearson r over origins (origin means over seeds), 95% Fisher CI; {basis} basis\n"
              + "\n".join(lines) + "\n")

    lines = ["\\begin{tabular}{rrrrrrr}", "\\toprule",
             "Slots & Margin (h) & Best & Median & Worst & Worst/best & Space \\\\", "\\midrule"]
    for cap, m, bst, md, w, ratio, s, *_ in c:
        lines.append(f"{cap} & {m} & {bst:.1f} & {md:.1f} & {w:.1f} & {ratio:.2f} & {s:.1f} \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    tex_write(out / f"table-origin-spread-{basis}.tex",
              f"% WaitAwhile emissions ({unit}) over origins, origin means over seeds; {basis} basis\n"
              + "\n".join(lines) + "\n")

    if d:
        lines = ["\\begin{tabular}{rrrr}", "\\toprule",
                 "Utilisation (US / EU) & US set & EU set & EU/US \\\\", "\\midrule"]
        for c_u, uu, mu, c_e, ue, mel, p, ratio, *_ in d:
            lines.append(f"{uu:.0f} / {ue:.0f}\\,\\% & {mu[0]:.1f} [{mu[1]:.1f}, {mu[2]:.1f}] & "
                         f"{mel[0]:.1f} [{mel[1]:.1f}, {mel[2]:.1f}] & {ratio:.2f} \\\\")
        lines += ["\\bottomrule", "\\end{tabular}"]
        tex_write(out / f"table-region-set-{basis}.tex",
                  f"% space vs round-robin reduction (%), mean [95% CI] over seeds; {basis} basis\n"
                  + "\n".join(lines) + "\n")


# ---------------------------------------------------------------- headline

def record(basis, a, b, c, d, prefix=""):
    cells, effects, ce, util, margins = a
    for cap, u, m, lo, hi, p, n in effects:
        headline(f"{prefix}margin_effect_cap{cap}", basis, m, lo, hi, p, n,
                 f"space+time, {margins[0]} to {margins[-1]} h, utilisation {u:.1f}%")
    headline(f"{prefix}capacity_effect", basis, ce[0], ce[1], ce[2], np.nan, ce[3])
    for cap, m, rc, rd, ps, share in b:
        headline(f"{prefix}origin_ci_r_cap{cap}_m{m}", basis, rc[0], rc[1], rc[2], rc[3], rc[4])
        headline(f"{prefix}origin_diurnal_r_cap{cap}_m{m}", basis, rd[0], rd[1], rd[2], rd[3], rd[4])
        headline(f"{prefix}origin_home_share_cap{cap}_m{m}", basis, share)
    for cap, m, bst, md, w, ratio, s, bn, wn in c:
        headline(f"{prefix}origin_ratio_cap{cap}_m{m}", basis, ratio, note=f"best {bn}, worst {wn}")
        headline(f"{prefix}origin_best_cap{cap}_m{m}", basis, bst)
        headline(f"{prefix}origin_median_cap{cap}_m{m}", basis, md)
        headline(f"{prefix}origin_worst_cap{cap}_m{m}", basis, w)
        headline(f"{prefix}space_cap{cap}_m{m}", basis, s)
    for c_u, uu, mu, c_e, ue, mel, p, ratio, hcu, hce in d or []:
        headline(f"{prefix}us_space_reduction_cap{c_u}", basis, mu[0], mu[1], mu[2], np.nan, mu[3])
        headline(f"{prefix}eu_space_reduction_cap{c_e}", basis, mel[0], mel[1], mel[2], np.nan, mel[3])
        headline(f"{prefix}eu_us_ratio_cap{c_u}", basis, ratio, p=p)


# ---------------------------------------------------------------- main

def main():
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "results/r1")
    out = base / "tables"
    print(f"reading {base}")
    arms = {a: load(base, a) for a in ["us_full", "us_home", "eu_full", "us_july_home",
                                       "us_delayins_home", "us_linear_full", "us_linear_home"]}
    for a, df in arms.items():
        if df is None:
            print(f"  {a}: no files")
        else:
            print(f"  {a}: seeds {sorted(df.seed.unique())}, rows {len(df)}, "
                  f"hosts per region {sorted(df.hosts_per_region.unique())}")
    if arms["us_full"] is None or arms["us_home"] is None:
        raise SystemExit("us_full and us_home are required")

    us_mean, us_diu = region_profiles(US, JAN)
    eu_mean, _ = region_profiles(EU, JAN)
    jul_mean, jul_diu = region_profiles(US, JULY)
    print("\n  US regions, January: " + ", ".join(f"{r} {us_mean[r]:.0f} ({us_diu[r]:.1f}%)"
                                                for r in sorted(us_mean, key=us_mean.get)))
    print("  US regions, July:    " + ", ".join(f"{r} {jul_mean[r]:.0f} ({jul_diu[r]:.1f}%)"
                                                for r in sorted(jul_mean, key=jul_mean.get)))

    for basis in BASES:
        print("\n\n" + "#" * 78 + f"\n#  {basis.upper()} BASIS" + (" (PRIMARY)" if basis == "dyn" else "")
              + "\n" + "#" * 78)
        a = section_a(arms["us_full"], basis)
        b = section_b(arms["us_home"], basis, us_mean, us_diu)
        c = section_c(arms["us_home"], basis)
        d = section_d(arms["us_full"], arms["eu_full"], basis, us_mean, eu_mean) \
            if arms["eu_full"] is not None else None
        record(basis, a, b, c, d)
        tex_tables(out, basis, a, b, c, d)

        if arms["us_july_home"] is not None:
            bj = section_b(arms["us_july_home"], basis, jul_mean, jul_diu, tag="-july")
            cj = section_c(arms["us_july_home"], basis, tag="-july")
            for cap, m, rc, rd, ps, share in bj:
                headline(f"july_origin_ci_r_cap{cap}_m{m}", basis, rc[0], rc[1], rc[2], rc[3], rc[4])
                headline(f"july_origin_diurnal_r_cap{cap}_m{m}", basis, rd[0], rd[1], rd[2], rd[3], rd[4])
            for cap, m, bst, md, w, ratio, s, *_ in cj:
                headline(f"july_space_cap{cap}_m{m}", basis, s)
                headline(f"july_origin_best_cap{cap}_m{m}", basis, bst)
                headline(f"july_origin_median_cap{cap}_m{m}", basis, md)
        if arms["us_delayins_home"] is not None:
            di = arms["us_delayins_home"]
            print(f"\n  delay-insensitive: requests per run {sorted(di.requests.unique())}, "
                  f"mean VM lifetime {di.vm_hours.mean() / di.requests.mean():.1f} h")
            bd = section_b(di, basis, us_mean, us_diu, tag="-delay-insensitive")
            for cap, m, rc, rd, ps, share in bd:
                headline(f"delayins_origin_ci_r_cap{cap}_m{m}", basis, rc[0], rc[1], rc[2], rc[3], rc[4])
                headline(f"delayins_origin_diurnal_r_cap{cap}_m{m}", basis, rd[0], rd[1], rd[2], rd[3], rd[4])
        if arms["us_linear_full"] is not None:
            section(f"E-linear  Linear 120/480 W model against the measured curve ({basis} basis)")
            al = section_a(arms["us_linear_full"], basis, quiet=True)
            dl_u = reduction(arms["us_linear_full"], basis)
            ds_u = reduction(arms["us_full"], basis)
            # Loop names here must not reuse a, b, c or d, which hold the
            # section results used below.
            for (cap, u, m, lo, hi, p, n), (_, _, m2, lo2, hi2, p2, n2) in zip(a[1], al[1]):
                print(f"  margin effect cap {cap:>3}: measured {m:6.1f} [{lo:6.1f}, {hi:6.1f}]   "
                      f"linear {m2:6.1f} [{lo2:6.1f}, {hi2:6.1f}]")
                headline(f"linear_margin_effect_cap{cap}", basis, m2, lo2, hi2, p2, n2)
            for cap in sorted(ds_u.total_capacity.unique()):
                s1 = mean_ci(ds_u[ds_u.total_capacity == cap].red)
                s2 = mean_ci(dl_u[dl_u.total_capacity == cap].red)
                print(f"  space vs RR cap {cap:>3}: measured {s1[0]:5.1f} [{s1[1]:5.1f}, {s1[2]:5.1f}]   "
                      f"linear {s2[0]:5.1f} [{s2[1]:5.1f}, {s2[2]:5.1f}]")
                headline(f"linear_us_space_reduction_cap{cap}", basis, s2[0], s2[1], s2[2], np.nan, s2[3])
            if arms["us_linear_home"] is not None:
                cl = section_c(arms["us_linear_home"], basis, quiet=True)
                for row, row_lin in zip(c, cl):
                    cap, m, ratio, ratio2 = row[0], row[1], row[5], row_lin[5]
                    print(f"  origin ratio cap {cap:>3} m {m:>2}: measured {ratio:.2f}   linear {ratio2:.2f}")
                    headline(f"linear_origin_ratio_cap{cap}_m{m}", basis, ratio2)

    published = Path("results/robust")
    if published.exists() and base != published:
        section("Published pass (results/robust, fleet sized from 200 slots) against r1, attributed basis")
        old_full = load(published, "us_full")
        old_home = load(published, "us_home")
        old_eu = load(published, "eu_full")
        if old_full is not None and old_home is not None:
            oa = section_a(old_full, "attr", quiet=True)
            oc = section_c(old_home, "attr", quiet=True)
            na = section_a(arms["us_full"], "attr", quiet=True)
            nc = section_c(arms["us_home"], "attr", quiet=True)
            for (c, u, m, *_), (_, _, m2, *__) in zip(oa[1], na[1]):
                print(f"  margin effect cap {c:>3}: published {m:6.1f}   r1 {m2:6.1f}")
            for o, n in zip(oc, nc):
                print(f"  origin ratio cap {o[0]:>3} m {o[1]:>2}: published {o[5]:.2f}   r1 {n[5]:.2f}")
            if old_eu is not None and arms["eu_full"] is not None:
                od = section_d(old_full, old_eu, "attr", us_mean, eu_mean, quiet=True)
                nd = section_d(arms["us_full"], arms["eu_full"], "attr", us_mean, eu_mean, quiet=True)
                for o, n in zip(od, nd):
                    print(f"  space vs RR US cap {o[0]:>3}: published {o[2][0]:5.1f}   r1 {n[2][0]:5.1f}"
                          f"   EU cap {o[3]:>3}: published {o[5][0]:5.1f}   r1 {n[5][0]:5.1f}")

    out.mkdir(parents=True, exist_ok=True)
    h = pd.DataFrame(HEADLINE)
    h.to_csv(out / "headline-numbers.csv", index=False)
    print(f"\n{len(h)} headline numbers written to {out / 'headline-numbers.csv'}")


if __name__ == "__main__":
    main()
