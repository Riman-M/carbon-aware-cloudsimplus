#!/usr/bin/env python3
"""
Summarise the perfect-foresight vs forecast-foresight comparison.

Reads a results CSV produced with -Dsweep.foresight=both (i.e. carrying a
`foresight` column with both "perfect" and "forecast" rows) and answers the
question Paper B exists to answer: how much of the carbon saving reported
under perfect foresight survives when the scheduler can only plan against a
real forecast?

Emissions are metered against the ground truth in both arms, so a paired
(policy, cell) difference is attributable to the planning input alone.

The framing throughout is *retention*: the fraction of a policy's saving over
the carbon-agnostic baseline that is left once foresight is imperfect. Raw
degradation in absolute emissions is reported too, but on its own it
understates the question -- a 3% rise in emissions matters very differently
depending on whether the policy was saving 5% or 50% to begin with.

Usage:
    python scripts/analyze_forecast.py [results/results_us_real_h13128.csv]
"""
import sys
from pathlib import Path

import pandas as pd

BASELINE = "round-robin"

# The accounting basis carrying the signal. Total is policy-invariant by
# construction (same fleet, same regions, regardless of placement), so a
# foresight effect measured on it would be noise; see CarbonMeter.
BASIS = "attributed_gco2"


def load(path):
    df = pd.read_csv(path)
    if "foresight" not in df.columns:
        raise SystemExit(
            f"{path} has no 'foresight' column -- it predates the forecast arm. "
            f"Re-run ExperimentRunner with a carbon_intensity_forecast.csv present.")
    arms = set(df.foresight.unique())
    if not {"perfect", "forecast"}.issubset(arms):
        raise SystemExit(
            f"{path} carries only {sorted(arms)}; this comparison needs both arms. "
            f"Re-run without -Dsweep.foresight, or with -Dsweep.foresight=both.")
    invalid = df[~df.placement_valid]
    if len(invalid):
        print(f"WARNING: {len(invalid)} of {len(df)} rows failed the placement check "
              f"and are excluded. Those cells did not run the schedule under test.")
        df = df[df.placement_valid]
    if df.empty:
        raise SystemExit(
            "every row failed the placement check -- nothing to compare. Diagnose the "
            "run before analysing it (see ExperimentRunner's placement-check output).")
    return df


def paired(df):
    """
    One row per (policy, cell) with both arms side by side.

    Pairing is on the full cell key, not just policy: comparing arms across
    different capacities or margins would mix the foresight effect with the
    capacity and deadline effects Paper A already characterised.
    """
    keys = ["policy", "region_set", "total_capacity", "deadline_margin_h"]
    p = df[df.foresight == "perfect"].set_index(keys)[BASIS]
    f = df[df.foresight == "forecast"].set_index(keys)[BASIS]
    out = pd.DataFrame({"perfect": p, "forecast": f}).dropna()
    if out.empty:
        raise SystemExit("no (policy, cell) pairs present in both arms.")
    out["degrade_pct"] = 100 * (out.forecast / out.perfect - 1)
    return out.reset_index()


def with_retention(df, pair):
    """
    Add each policy's saving over the baseline, per arm, and what fraction of
    it survives. Retention is undefined where the baseline saves nothing, so
    those cells are dropped rather than reported as spurious percentages.
    """
    keys = ["region_set", "total_capacity", "deadline_margin_h"]
    base = (df[df.policy == BASELINE]
            .set_index(keys + ["foresight"])[BASIS].unstack("foresight"))

    rows = []
    for _, r in pair[pair.policy != BASELINE].iterrows():
        k = (r.region_set, r.total_capacity, r.deadline_margin_h)
        if k not in base.index:
            continue
        bp, bf = base.loc[k, "perfect"], base.loc[k, "forecast"]
        save_p = 100 * (1 - r.perfect / bp)
        save_f = 100 * (1 - r.forecast / bf)
        rows.append({**r.to_dict(), "save_perfect": save_p, "save_forecast": save_f,
                     "retained_pct": (save_f / save_p * 100) if save_p > 0 else float("nan")})
    return pd.DataFrame(rows)


def control_check(pair):
    """
    round-robin never consults the carbon trace while capacity is slack, so its
    two arms should be identical. Any movement means the forecast trace is
    reaching a decision path it should not, or the arms differ by something
    other than the planning input.
    """
    print("\n" + "=" * 70)
    print("CONTROL  the carbon-agnostic baseline must not move between arms")
    print("=" * 70)
    rr = pair[pair.policy == BASELINE]
    if rr.empty:
        print(f"\n  no '{BASELINE}' rows found; control check skipped.")
        return
    worst = rr.degrade_pct.abs().max()
    moved = rr[rr.degrade_pct.abs() > 1e-9]
    print(f"\n  {len(rr)} baseline cells, largest |change| between arms: {worst:.6f}%")
    if len(moved) == 0:
        print("  -> identical in every cell, as expected.")
    else:
        print(f"  -> WARNING: {len(moved)} cells differ. Under capacity pressure the "
              "baseline\n     does consult the trace via the shared fallback, so small "
              "moves in tight\n     cells are legitimate; movement in slack cells is not.")
        for _, r in moved.nlargest(min(5, len(moved)), "degrade_pct").iterrows():
            print(f"     cap={r.total_capacity:<4d} m={r.deadline_margin_h:<3d} "
                  f"{r.degrade_pct:+.4f}%")


def direction_check(ret):
    """
    A forecast arm beating its own perfect-foresight twin is not a win, it is a
    signal. Planning against a noisier version of the same series cannot help
    on average; isolated cells can invert through packing luck, but a
    systematic inversion means the arms are mislabelled or the forecast trace
    is not what it claims to be.
    """
    print("\n" + "=" * 70)
    print("SANITY  forecast should never systematically beat perfect foresight")
    print("=" * 70)
    inverted = ret[ret.degrade_pct < 0]
    print(f"\n  {len(inverted)} of {len(ret)} carbon-aware cells have forecast < perfect")
    if len(inverted):
        print(f"  largest inversion: {inverted.degrade_pct.min():.2f}%  "
              f"(median across inverted cells {inverted.degrade_pct.median():.2f}%)")
        print("  worst offenders:")
        for _, r in inverted.nsmallest(min(5, len(inverted)), "degrade_pct").iterrows():
            print(f"     {r.policy:<12s} cap={r.total_capacity:<4d} "
                  f"m={r.deadline_margin_h:<3d} {r.degrade_pct:+.2f}%")
        if len(inverted) > 0.25 * len(ret):
            print("  -> more than a quarter of cells inverted: investigate before "
                  "reporting anything.")
    else:
        print("  -> none; every carbon-aware policy pays for imperfect foresight.")


def by_policy(ret):
    print("\n" + "=" * 70)
    print("FINDING  cost of imperfect foresight, by policy")
    print("=" * 70)
    print(f"\n  {'policy':<12s} {'cells':>5s} {'degrade%':>18s} {'save perf':>10s} "
          f"{'save fcst':>10s} {'retained':>18s}")
    for pol, g in ret.groupby("policy"):
        print(f"  {pol:<12s} {len(g):5d} "
              f"{g.degrade_pct.mean():8.2f} +/- {g.degrade_pct.std():5.2f} "
              f"{g.save_perfect.mean():9.1f}% {g.save_forecast.mean():9.1f}% "
              f"{g.retained_pct.mean():8.1f}% +/- {g.retained_pct.std():5.1f}")
    print("\n  -> retention near 100% means carbon-aware scheduling is robust to "
          "realistic\n     forecast error; that is a result, but a robustness result, "
          "not a gap result.")


def by_pressure(ret):
    """
    The hypothesis worth testing: forecast error should bite hardest where
    there is no slack to recover from a bad decision. Slack capacity lets a
    misled scheduler pick another good hour or region almost for free.
    """
    print("\n" + "=" * 70)
    print("FINDING  does the cost grow under capacity and deadline pressure?")
    print("=" * 70)

    print("\n  mean degradation %, by capacity x deadline margin "
          "(carbon-aware policies pooled):")
    tab = ret.pivot_table(index="total_capacity", columns="deadline_margin_h",
                          values="degrade_pct", aggfunc="mean")
    print("          " + "".join(f"{f'm={m}':>10s}" for m in tab.columns))
    for cap in tab.index:
        print(f"  cap={cap:<4d}" + "".join(f"{tab.loc[cap, m]:10.2f}" for m in tab.columns))

    print("\n  mean retention %, same layout:")
    tab_r = ret.pivot_table(index="total_capacity", columns="deadline_margin_h",
                            values="retained_pct", aggfunc="mean")
    print("          " + "".join(f"{f'm={m}':>10s}" for m in tab_r.columns))
    for cap in tab_r.index:
        print(f"  cap={cap:<4d}" + "".join(f"{tab_r.loc[cap, m]:10.1f}" for m in tab_r.columns))

    caps, margins = sorted(ret.total_capacity.unique()), sorted(ret.deadline_margin_h.unique())
    tight = ret[(ret.total_capacity == caps[0]) & (ret.deadline_margin_h == margins[0])]
    slack = ret[(ret.total_capacity == caps[-1]) & (ret.deadline_margin_h == margins[-1])]
    if len(tight) and len(slack):
        print(f"\n  tightest cell (cap={caps[0]}, m={margins[0]}): "
              f"degrade {tight.degrade_pct.mean():5.2f}% +/- {tight.degrade_pct.std():.2f}, "
              f"retained {tight.retained_pct.mean():.1f}%")
        print(f"  slackest cell (cap={caps[-1]}, m={margins[-1]}): "
              f"degrade {slack.degrade_pct.mean():5.2f}% +/- {slack.degrade_pct.std():.2f}, "
              f"retained {slack.retained_pct.mean():.1f}%")
        print("\n  -> the pressure story only holds if the tight figure clears the "
              "slack figure\n     by more than the two spreads combined. If both sit "
              "near zero, the honest\n     headline is that foresight quality does not "
              "much matter here.")


def headline(ret):
    print("\n" + "=" * 70)
    print("HEADLINE")
    print("=" * 70)
    d, r = ret.degrade_pct, ret.retained_pct
    print(f"\n  across {len(ret)} carbon-aware (policy, cell) pairs:")
    print(f"    emissions rise under forecast : {d.mean():.2f}% +/- {d.std():.2f}  "
          f"(worst cell {d.max():.2f}%)")
    print(f"    savings retained              : {r.mean():.1f}% +/- {r.std():.1f}  "
          f"(worst cell {r.min():.1f}%)")
    print()


def main():
    default = "results/results_us_real_h13128.csv"
    path = Path(sys.argv[1] if len(sys.argv) > 1 else default)
    if not path.exists():
        raise SystemExit(f"no results file at {path}\nUsage: "
                         f"python scripts/analyze_forecast.py <results csv>")

    df = load(path)
    print(f"loaded {len(df)} rows from {path}")
    print(f"  policies: {sorted(df.policy.unique())}")
    print(f"  caps: {sorted(df.total_capacity.unique())}  "
          f"margins: {sorted(df.deadline_margin_h.unique())}")

    pair = paired(df)
    ret = with_retention(df, pair)

    control_check(pair)
    if ret.empty:
        raise SystemExit("\nno carbon-aware policies paired against the baseline.")
    direction_check(ret)
    by_policy(ret)
    by_pressure(ret)
    headline(ret)


if __name__ == "__main__":
    main()
