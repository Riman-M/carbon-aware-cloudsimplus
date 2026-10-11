#!/usr/bin/env python3
"""
Does pricing the capacity constraint remove the deadline-margin non-monotonicity,
and what does it cost?

Reads results/mitigation/jul_seed*.csv (run_mitigation.bat) and answers four
questions in order. The first can invalidate the whole run, so it is checked
before anything else is printed.

  C.  Do the degenerate controls hold?
      price lambda=0 must reproduce space+time exactly. If it does not, the
      new planner is reading state the baseline does not, and every other
      number here is measuring that bug rather than the mitigation.

  1.  Does the non-monotonicity go away?
      Same violation statistic as analyze_resonance.py, so these numbers drop
      straight onto Table I: V(m) is how much worse a cell is than the best
      result the SAME series achieved at any SHORTER margin.

  2.  What does it cost?
      A fix that removes the defect by emitting more at every margin has
      solved nothing. Realised carbon intensity and attributed emissions per
      VM-hour are reported next to the violation, and deadline overrun with
      them, since the other way to fake a fix is to miss deadlines.

  3.  Did it work through the claimed channel?
      The mechanism claim is temporal crowding. If the violation falls while
      modal start-hour share and peak-to-mean occupancy do not, the fix works
      for some other reason and the mechanism section is still unproven.
"""
import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd

BASIS = "attributed_gco2"
BASELINE = "space+time"
CONTROL = "space+time+price(0.00)"
TIE_CONTROL = "space+time+spread(0%)"

# The margin at which the unmitigated planner peaks, from run_peak.bat. Every
# policy is also reported here, so the comparison sits at one fixed point on
# the axis rather than at each policy's own worst case.
PEAK_MARGIN = 27


def is_flex(p):
    """Time-flexible policies: the baseline, the mitigations, and WaitAwhile."""
    return p.startswith("space+time") or p.startswith("time@")


def is_mitigation(p):
    return p.startswith("space+time+")


def load(target):
    target = Path(target)
    paths = sorted(glob.glob(str(target / "*seed*.csv"))) if target.is_dir() else []
    if not paths:
        raise SystemExit(f"no *seed*.csv files under {target}")
    frames = []
    for p in paths:
        name = os.path.basename(p)
        m = re.match(r"(jul|dec)_seed(\d+)\.csv", name)
        if not m:
            raise SystemExit(
                f"cannot read season and seed from {name}; expected jul_seedN.csv "
                f"as written by run_mitigation.bat")
        d = pd.read_csv(p)
        d["season"], d["seed"] = m.group(1), int(m.group(2))
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    print(f"loaded {len(df)} rows from {len(paths)} files under {target}")

    bad = df[~df.placement_valid]
    if len(bad):
        print(f"\nWARNING: {len(bad)} of {len(df)} rows failed the placement check.")
        print("  Excluded below. Counted per policy rather than per cell, because the")
        print("  question is whether a mitigation fails MORE often than the baseline:")
        print("  if it does, it is deferring work past the horizon, not scheduling it")
        print("  better, and its emissions advantage is partly unrun work.")
        n_base = int((bad.policy == BASELINE).sum())
        print(f"\n  {'policy':<26s} {'failed':>7s} {'vs baseline':>12s}   margins")
        for p, g in bad.groupby("policy"):
            ms = ",".join(str(m) for m in sorted(g.deadline_margin_h.unique()))
            print(f"  {p:<26s} {len(g):>7d} {len(g) - n_base:>+12d}   {ms}")
        worse = {p: len(g) for p, g in bad.groupby("policy")
                 if is_mitigation(p) and len(g) > n_base}
        if worse:
            print(f"\n  *** these mitigations fail more than the baseline: {worse}")
            print("  Treat their emissions numbers as upper bounds on the real benefit.")
        df = df[df.placement_valid]
    if df.empty:
        raise SystemExit("every row failed the placement check.")

    pols = sorted(df.policy.unique())
    print(f"policies present: {len(pols)}")
    for p in pols:
        print(f"    {p}")
    return df


# ---------------------------------------------------------------- C. controls

def controls(df):
    print("\n" + "=" * 72)
    print("C   degenerate controls")
    print("=" * 72)

    keys = ["season", "seed", "deadline_margin_h", "total_capacity"]
    have = set(df.policy.unique())

    if CONTROL not in have:
        print(f"  {CONTROL} not in this run; identity control SKIPPED.")
        print("  Re-run with 0 in -Dsweep.price to enable it.")
    else:
        a = df[df.policy == BASELINE].set_index(keys).sort_index()
        b = df[df.policy == CONTROL].set_index(keys).sort_index()
        common = a.index.intersection(b.index)
        cols = [BASIS, "attributed_kwh", "modal_start_hour_share",
                "peak_to_mean_occupancy", "mean_deferral_h", "deadline_violations"]
        delta = (a.loc[common, cols] - b.loc[common, cols]).abs().max()
        worst = float(delta.max())
        print(f"  {BASELINE} vs {CONTROL}")
        print(f"    cells compared: {len(common)}")
        for c in cols:
            print(f"    max |difference| in {c:<26s} {delta[c]:.6g}")
        if worst > 1e-6:
            print("\n  *** CONTROL FAILED ***")
            print("  A zero congestion price must leave the objective unchanged, so these")
            print("  must be identical. A non-zero difference means the two planners are")
            print("  not enumerating the same candidate set, or the penalty term is being")
            print("  applied when lambda is zero. Fix that before reading anything below.")
            sys.exit(2)
        print("    PASSES: lambda=0 reproduces the baseline exactly.")

    if TIE_CONTROL in have:
        a = df[df.policy == BASELINE].set_index(keys).sort_index()
        b = df[df.policy == TIE_CONTROL].set_index(keys).sort_index()
        common = a.index.intersection(b.index)
        d = (a.loc[common, BASIS] - b.loc[common, BASIS]).abs()
        rel = 100 * (d / a.loc[common, BASIS]).max()
        print(f"\n  {BASELINE} vs {TIE_CONTROL}")
        print(f"    max |difference| in {BASIS}: {rel:.3f}% of the baseline")
        print("    Exact identity is NOT expected here: a zero tolerance band still")
        print("    re-breaks ties by occupancy. This number is how much the defect")
        print("    owes to tie-breaking alone, before any carbon is traded away.")


# ------------------------------------------------------------- 1. does it go?

def violations(df):
    d = df[df.policy.map(is_flex)]
    rows = []
    keys = ["season", "seed", "policy", "total_capacity"]
    for k, g in d.groupby(keys):
        g = g.sort_values("deadline_margin_h")
        best = None
        for _, r in g.iterrows():
            v = r[BASIS]
            rows.append({**dict(zip(keys, k)),
                         "margin": r.deadline_margin_h,
                         BASIS: v,
                         "violation_pct": float("nan") if best is None
                                          else 100 * (v / best - 1)})
            best = v if best is None else min(best, v)
    return pd.DataFrame(rows)


def q1_removed(viol):
    print("\n" + "=" * 72)
    print("Q1  does the non-monotonicity survive the mitigation?")
    print("=" * 72)
    v = viol.dropna(subset=["violation_pct"])

    # Per policy: the worst violation in each seed's own series, then summarised
    # across seeds. Taking the max of a mean would hide a defect that moves
    # margin between seeds.
    per_seed = v.groupby(["policy", "seed"]).violation_pct.max()
    s = per_seed.groupby("policy").agg(["mean", "std", "count"])
    s = s.sort_values("mean", ascending=False)

    print(f"\n  {'policy':<26s} {'worst V per seed':>18s} {'seeds':>7s} {'argmax':>8s}")
    print("  " + "-" * 62)
    argmax = v.loc[v.groupby(["policy", "seed"]).violation_pct.idxmax()]
    for p, r in s.iterrows():
        am = argmax[argmax.policy == p].margin
        am_s = f"{am.median():.0f}" if len(am) else "-"
        if len(am) and am.nunique() > 1:
            am_s += "*"
        print(f"  {p:<26s} {r['mean']:>+10.2f} +/- {r['std']:>5.2f} "
              f"{int(r['count']):>7d} {am_s:>8s}")
    print("\n  argmax is the median margin of the worst cell; * means it moved")
    print("  between seeds, which is what a violation inside noise looks like.")

    base = s.loc[BASELINE, "mean"] if BASELINE in s.index else float("nan")
    noise = s["std"].median()
    print(f"\n  baseline worst violation: {base:+.2f}%")
    print(f"  median across-seed sd:    {noise:.2f} percentage points")
    ok = s[(s.index.map(is_mitigation)) & (s["mean"] < noise)]
    if len(ok):
        print(f"\n  within seed noise of zero: {', '.join(ok.index)}")
    else:
        print("\n  no mitigation setting brought the violation inside seed noise.")
    return s


# ------------------------------------------------------------ 2. what it cost

def q2_cost(df, worst):
    print("\n" + "=" * 72)
    print("Q2  what did it cost?")
    print("=" * 72)
    print(f"\n  All columns read at margin {PEAK_MARGIN}, averaged across seeds.")
    print("  realCI is attributed gCO2 divided by attributed kWh: how dirty the")
    print("  hours actually chosen were. d_emit is the change in attributed")
    print("  gCO2 per VM-hour against the baseline, so negative is better.")

    d = df[(df.deadline_margin_h == PEAK_MARGIN) & df.policy.map(is_flex)]
    g = d.groupby("policy").agg(
        attr=("attributed_gco2_per_vm_hour", "mean"),
        gco2=(BASIS, "mean"),
        akwh=("attributed_kwh", "mean"),
        overrun=("mean_overrun_h", "mean"),
        viol_rate=("violation_rate", "mean"),
        defer=("mean_deferral_h", "mean"),
    )
    g["realCI"] = g.gco2 / g.akwh
    if BASELINE not in g.index:
        raise SystemExit(f"baseline {BASELINE} missing at margin {PEAK_MARGIN}")
    b = g.loc[BASELINE]
    g["d_emit"] = 100 * (g.attr / b.attr - 1)

    order = [BASELINE] + [p for p in g.index if p != BASELINE]
    print(f"\n  {'policy':<26s} {'maxV%':>8s} {'realCI':>8s} {'d_emit%':>9s} "
          f"{'overrun_h':>10s} {'missed%':>8s} {'defer_h':>8s}")
    print("  " + "-" * 82)
    for p in order:
        r = g.loc[p]
        w = worst.loc[p, "mean"] if p in worst.index else float("nan")
        print(f"  {p:<26s} {w:>+8.2f} {r.realCI:>8.1f} {r.d_emit:>+9.2f} "
              f"{r.overrun:>10.2f} {100*r.viol_rate:>8.2f} {r.defer:>8.2f}")

    print("\n  Read d_emit and missed% together with maxV. A mitigation is only")
    print("  interesting if it cuts maxV without buying that with emissions or")
    print("  with deadline misses; a row that improves maxV while missed% rises")
    print("  has moved the problem into the QoS column, not solved it.")
    return g


# ------------------------------------------------- 3. through which mechanism

def q3_channel(df, worst):
    print("\n" + "=" * 72)
    print("Q3  did it work through temporal crowding, as claimed?")
    print("=" * 72)
    d = df[(df.deadline_margin_h == PEAK_MARGIN) & df.policy.map(is_flex)]
    g = d.groupby("policy").agg(
        modal=("modal_start_hour_share", "mean"),
        entropy=("start_hour_entropy", "mean"),
        pkmean=("peak_to_mean_occupancy", "mean"),
        share=("max_region_share", "mean"),
        hosts=("peak_active_hosts", "mean"),
    )
    print(f"\n  {'policy':<26s} {'maxV%':>8s} {'modal':>8s} {'entropy':>8s} "
          f"{'pk/mean':>8s} {'regShare':>9s} {'hosts':>8s}")
    print("  " + "-" * 80)
    order = [BASELINE] + [p for p in g.index if p != BASELINE]
    for p in order:
        r = g.loc[p]
        w = worst.loc[p, "mean"] if p in worst.index else float("nan")
        print(f"  {p:<26s} {w:>+8.2f} {r.modal:>8.3f} {r.entropy:>8.3f} "
              f"{r.pkmean:>8.3f} {r.share:>9.3f} {r.hosts:>8.0f}")

    mit = g[g.index.map(is_mitigation)]
    if len(mit) > 1 and BASELINE in worst.index:
        w = worst.loc[mit.index, "mean"]
        for col, label in (("modal", "modal start-hour share"),
                           ("pkmean", "peak-to-mean occupancy")):
            c = w.corr(mit[col])
            print(f"\n  correlation of worst violation with {label}: {c:+.3f}")
        print("\n  Both should be strongly positive: the settings that crowd least")
        print("  should be the settings that violate least. A near-zero or negative")
        print("  correlation means the fix is not acting on crowding and the")
        print("  mechanism section needs a different explanation.")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "results/mitigation"
    df = load(target)
    controls(df)
    viol = violations(df)
    worst = q1_removed(viol)
    q2_cost(df, worst)
    q3_channel(df, worst)
    print("\n" + "=" * 72)
    print("If a setting clears Q1 inside noise, is non-positive on d_emit in Q2,")
    print("and moves modal and pk/mean in Q3, that is the result the paper needs:")
    print("the defect is removable online, and the mechanism is confirmed by the")
    print("fact that addressing crowding is what removes it.")


if __name__ == "__main__":
    main()