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

# A peak smaller than this is not the defect under study, whatever its t.
MATERIAL_PP = 2.0


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


def q1_removed(viol, df):
    """Peak height, and why it replaces the maximum violation.

    The maximum of a series is biased upward under a flat null: eight noisy
    margins produce a positive maximum even when nothing is happening, so a
    policy that has genuinely flattened the curve still scores several points
    and looks like a partial fix. Peak height instead contrasts the margins
    where the baseline peaks against the flanks of the same sweep. It is
    centred on zero when the curve is flat, so it can be tested against zero,
    and it answers the question actually being asked: is there still a peak?
    """
    print("\n" + "=" * 72)
    print("Q1  does the non-monotonicity survive the mitigation?")
    print("=" * 72)
    v = viol.dropna(subset=["violation_pct"])

    # The plateau is located from the BASELINE, then every policy is read at
    # the same margins. Locating each policy's own worst margin would compare
    # different experiments, and for a flat series it would just find noise.
    base = v[v.policy == BASELINE].groupby("margin").violation_pct.mean()
    if base.empty:
        raise SystemExit(f"baseline {BASELINE} missing; cannot locate the plateau")
    plateau = sorted(base.nlargest(3).index)
    margins = sorted(v.margin.unique())
    flanks = [margins[0], margins[-1]]
    print(f"\n  baseline plateau at margins {[int(m) for m in plateau]}, "
          f"read against flanks {[int(m) for m in flanks]}")

    rows = []
    for pol, g in v.groupby("policy"):
        per = []
        for _, gs in g.groupby("seed"):
            p = gs[gs.margin.isin(plateau)].violation_pct.mean()
            f = gs[gs.margin.isin(flanks)].violation_pct.mean()
            if pd.notna(p) and pd.notna(f):
                per.append(p - f)
        if len(per) < 2:
            continue
        s = pd.Series(per)
        rows.append({"policy": pol, "height": s.mean(), "sd": s.std(),
                     "se": s.std() / (len(s) ** 0.5), "n": len(s),
                     "maxV": g.groupby("seed").violation_pct.max().mean()})
    h = pd.DataFrame(rows).set_index("policy").sort_values("height", ascending=False)
    h["t"] = h.height / h.se

    print(f"\n  {'policy':<26} {'height':>8} {'se':>6} {'t':>6} {'n':>3}   "
          f"{'maxV':>7}   verdict")
    print("  " + "-" * 76)
    for p, r in h.iterrows():
        # Significance alone is not enough. A setting with very little
        # across-seed spread can clear t=2.5 on a height of well under a
        # point, which is not the defect under study by any reading.
        if abs(r.t) >= 2.5 and r.height >= MATERIAL_PP:
            verdict = "peak present"
        elif abs(r.t) >= 2.5:
            verdict = f"resolvable but under {MATERIAL_PP:.0f} pp"
        else:
            verdict = "no peak at this power"
        print(f"  {p:<26} {r.height:>+8.2f} {r.se:>6.2f} {r.t:>6.1f} {int(r.n):>3}   "
              f"{r.maxV:>+7.2f}   {verdict}")

    # Coverage check. The placement check removes whole cells, and it removes
    # them unevenly: the baseline crowds work into the trough and so defers
    # more of it past the horizon. A height averaged over series with holes in
    # them is therefore not obviously comparable across policies, and the holes
    # sit disproportionately in the rows that were worst. Recomputing over only
    # those seeds whose plateau and both flanks survived settles whether the
    # comparison rests on that unevenness.
    need = set(plateau) | set(flanks)
    crows = []
    for pol, g in v.groupby("policy"):
        per, seeds = [], []
        for s, gs in g.groupby("seed"):
            if not need <= set(gs.margin):
                continue
            per.append(gs[gs.margin.isin(plateau)].violation_pct.mean()
                       - gs[gs.margin.isin(flanks)].violation_pct.mean())
            seeds.append(s)
        if len(per) < 2:
            crows.append({"policy": pol, "height": float("nan"),
                          "se": float("nan"), "n": len(per)})
            continue
        s2 = pd.Series(per)
        crows.append({"policy": pol, "height": s2.mean(),
                      "se": s2.std() / (len(s2) ** 0.5), "n": len(s2)})
    c = pd.DataFrame(crows).set_index("policy")
    print(f"\n  Recomputed over seeds with no missing cell at {sorted(int(x) for x in need)}:")
    print(f"\n  {'policy':<26} {'height':>8} {'se':>6} {'n':>3}   {'shift vs above':>15}")
    print("  " + "-" * 64)
    for p in h.index:
        if p not in c.index:
            continue
        r = c.loc[p]
        if r.n < 2:
            print(f"  {p:<26} {'--':>8} {'--':>6} {int(r.n):>3}   too few complete seeds")
            continue
        print(f"  {p:<26} {r.height:>+8.2f} {r.se:>6.2f} {int(r.n):>3}   "
              f"{r.height - h.loc[p, 'height']:>+15.2f}")
    print("\n  A baseline that holds up here is not an artifact of which cells")
    print("  survived. A baseline that only appears on the full set would be.")

    print("\n  maxV is shown only for continuity with analyze_resonance.py. It is")
    print("  biased upward on a flat series, so it must not be read as a residual")
    print("  defect; height is the statistic to quote.")
    if BASELINE in h.index:
        b = h.loc[BASELINE, "height"]
        print(f"\n  baseline peak height: {b:+.2f} percentage points")
        gone = h[(h.index.map(is_mitigation))
                 & ((h.t.abs() < 2.5) | (h.height < MATERIAL_PP))]
        if len(gone):
            print(f"  peak not detectable for: {', '.join(gone.index)}")
        else:
            print("  every mitigation setting still shows a detectable peak.")
    return h


# ------------------------------------------------------------ 2. what it cost

def q2_cost(df, h):
    print("\n" + "=" * 72)
    print("Q2  what did it cost?")
    print("=" * 72)
    d = df[df.policy.map(is_flex)]

    print("\n  Attributed gCO2 per VM-hour against the baseline AT THE SAME MARGIN.")
    print("  Reported per margin rather than pooled, because a congestion price")
    print("  has nothing to relieve where there is no contention: the sign of the")
    print("  effect is expected to differ between the crowded and uncrowded ends")
    print("  of the axis, and a single pooled figure would average that away.")
    e = d.pivot_table(index="deadline_margin_h", columns="policy",
                      values="attributed_gco2_per_vm_hour", aggfunc="mean")
    if BASELINE not in e.columns:
        raise SystemExit(f"baseline {BASELINE} missing")
    rel = 100 * (e.div(e[BASELINE], axis=0) - 1)
    cols = [c for c in rel.columns if c != BASELINE]
    print(f"\n  {'margin':>7}" + "".join(f"{c.replace('space+time', '') or 'base':>14}"
                                         for c in cols))
    print("  " + "-" * (7 + 14 * len(cols)))
    for m, r in rel.iterrows():
        print(f"  {int(m):>7}" + "".join(f"{r[c]:>+14.2f}" for c in cols))

    print("\n  Deadline cost, pooled over margins. A mitigation that improves the")
    print("  carbon columns while these rise has moved the problem, not solved it.")
    q = d.groupby("policy").agg(overrun=("mean_overrun_h", "mean"),
                                missed=("violation_rate", "mean"),
                                defer=("mean_deferral_h", "mean"))
    print(f"\n  {'policy':<26} {'overrun_h':>10} {'missed%':>9} {'defer_h':>9}")
    print("  " + "-" * 56)
    for p, r in q.iterrows():
        print(f"  {p:<26} {r.overrun:>10.2f} {100 * r.missed:>9.2f} {r.defer:>9.2f}")
    return rel


# ------------------------------------------------- 3. through which mechanism

# Below this regional share a policy has stopped concentrating and has become a
# spatial load balancer. Its busiest region then holds a thin, spiky remainder,
# so peak_to_mean_occupancy measured there describes dispersal rather than
# crowding. The baseline sits near 0.98; a setting below this is reported but
# excluded from the correlation, which would otherwise be meaningless.
DEGENERATE_REG_SHARE = 0.85


def q3_channel(df, h):
    print("\n" + "=" * 72)
    print("Q3  did it work through temporal crowding, as claimed?")
    print("=" * 72)
    d = df[(df.deadline_margin_h == PEAK_MARGIN) & df.policy.map(is_flex)]
    fleet = "fleet_peak_to_mean_occupancy" in df.columns
    agg = dict(modal=("modal_start_hour_share", "mean"),
               entropy=("start_hour_entropy", "mean"),
               pkmean=("peak_to_mean_occupancy", "mean"),
               share=("max_region_share", "mean"),
               hosts=("peak_active_hosts", "mean"))
    if fleet:
        agg["fleetpk"] = ("fleet_peak_to_mean_occupancy", "mean")
    g = d.groupby("policy").agg(**agg)
    if not fleet:
        print("\n  NOTE: no fleet_peak_to_mean_occupancy column in these results.")
        print("  Falling back to the busiest-region measure, which is only valid")
        print("  while every policy compared stays in one region. Re-run against a")
        print("  build that reports the fleet-wide figure to remove the caveat.")

    cols = f"{'fleetpk':>9}" if fleet else ""
    print(f"\n  {'policy':<26} {'height':>8} {'modal':>7} {'entropy':>8} "
          f"{'pk/mean':>8}{cols} {'regShare':>9} {'hosts':>7}")
    print("  " + "-" * (76 + len(cols)))
    for p, r in g.iterrows():
        hh = h.loc[p, "height"] if p in h.index else float("nan")
        flag = "  <- dispersed" if r.share < DEGENERATE_REG_SHARE else ""
        extra = f"{r.fleetpk:>9.3f}" if fleet else ""
        print(f"  {p:<26} {hh:>+8.2f} {r.modal:>7.3f} {r.entropy:>8.3f} "
              f"{r.pkmean:>8.3f}{extra} {r.share:>9.3f} {r.hosts:>7.0f}{flag}")

    mit = g[g.index.map(is_mitigation)]
    keep = mit[mit.share >= DEGENERATE_REG_SHARE]
    drop = mit[mit.share < DEGENERATE_REG_SHARE]
    if len(drop):
        print(f"\n  Excluded from the correlation below, regional share under "
              f"{DEGENERATE_REG_SHARE}:")
        for p, r in drop.iterrows():
            print(f"    {p:<26} regShare {r.share:.3f}")
        print("  These have stopped concentrating, so their busiest-region crowding")
        print("  figure measures dispersal. They are a boundary on the parameter,")
        print("  not a setting to recommend: read their emissions row in Q2.")

    if len(keep) > 1 and len(h):
        w = h.loc[keep.index, "height"]
        pairs = [("modal", "modal start-hour share"), ("pkmean", "peak-to-mean occupancy")]
        if fleet:
            pairs.append(("fleetpk", "fleet-wide peak-to-mean"))
        print(f"\n  Correlation of peak height with, over {len(keep)} concentrating settings:")
        for col, label in pairs:
            print(f"    {label:<30} {w.corr(keep[col]):+.3f}")
        print("\n  These should be strongly positive: the settings that crowd least")
        print("  should be the settings with the smallest peak. A near-zero or")
        print("  negative value means the fix is not acting on crowding and the")
        print("  mechanism section needs a different explanation.")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "results/mitigation"
    df = load(target)
    controls(df)
    viol = violations(df)
    h = q1_removed(viol, df)
    q2_cost(df, h)
    q3_channel(df, h)
    print("\n" + "=" * 72)
    print("What the paper needs from this run is a setting that shows no peak in")
    print("Q1, does not pay for that with emissions or deadlines at the crowded")
    print("margins in Q2, keeps its regional share in Q3, and sits on the right")
    print("side of the crowding correlation. A setting that clears Q1 only after")
    print("its regional share has collapsed has removed the defect by ceasing to")
    print("be carbon-aware, which round-robin also achieves and nobody wants.")


if __name__ == "__main__":
    main()