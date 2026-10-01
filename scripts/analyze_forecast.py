#!/usr/bin/env python3
"""
Summarise the perfect-foresight vs forecast-foresight comparison.

Accepts either a single results CSV or a directory of per-seed CSVs
(results/forecast/us_fc_seed*.csv, as written by run_forecast_robust.bat).

The question Paper B exists to answer: how much of the carbon saving reported
under perfect foresight survives when the scheduler can only plan against a
real forecast? Emissions are metered against the ground truth in both arms, so
a paired (policy, cell) difference is attributable to the planning input alone.

The framing is *retention*: the fraction of a policy's saving over the
carbon-agnostic baseline that is left once foresight is imperfect. Raw
degradation on its own understates the question -- a 3% rise in emissions
means something different for a policy saving 5% than for one saving 50%.

WITH MULTIPLE SEEDS, every effect is reported against its across-seed spread.
The single-seed pass produced cell-level effects between -4.5% and +3.2% with
a non-monotonic shape across capacity; nothing that small survives as a
finding unless it clears sampling noise. A one-seed run is therefore reported
as provisional throughout, and the script says so rather than printing
figures that look publishable.

Usage:
    python scripts/analyze_forecast.py results/forecast
    python scripts/analyze_forecast.py results/results_us_real_h13128.csv
"""
import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd

BASELINE = "round-robin"

# The accounting basis carrying the signal. Total is policy-invariant by
# construction (same fleet, same regions, regardless of placement), so a
# foresight effect measured on it would be noise; see CarbonMeter.
BASIS = "attributed_gco2"


def time_flexible(pol):
    """
    A policy that never time-shifts returns an identical result at every
    deadline margin, so pooling its rows across margins would count one
    measurement four times and understate every spread computed from it.
    """
    return pol == "space+time" or pol.startswith("time@")


def load(target):
    """Load one CSV, or every per-seed CSV under a directory."""
    target = Path(target)
    if target.is_dir():
        paths = sorted(glob.glob(str(target / "*seed*.csv")))
        if not paths:
            raise SystemExit(f"no *seed*.csv files under {target}")
        frames = []
        for p in paths:
            m = re.search(r"seed(\d+)", os.path.basename(p))
            if not m:
                raise SystemExit(f"cannot read a seed number from {os.path.basename(p)}")
            d = pd.read_csv(p)
            d["seed"] = int(m.group(1))
            frames.append(d)
        df = pd.concat(frames, ignore_index=True)
        print(f"loaded {len(df)} rows from {len(paths)} seed files under {target}")
    else:
        if not target.exists():
            raise SystemExit(f"no results file or directory at {target}\n"
                             f"Usage: python scripts/analyze_forecast.py <csv or dir>")
        df = pd.read_csv(target)
        df["seed"] = 0
        print(f"loaded {len(df)} rows from {target}")

    if "foresight" not in df.columns:
        raise SystemExit(
            "no 'foresight' column -- these results predate the forecast arm. "
            "Re-run ExperimentRunner with a carbon_intensity_forecast.csv present.")
    arms = set(df.foresight.unique())
    if not {"perfect", "forecast"}.issubset(arms):
        raise SystemExit(
            f"only {sorted(arms)} present; this comparison needs both arms. "
            "Re-run without -Dsweep.foresight, or with -Dsweep.foresight=both.")

    invalid = df[~df.placement_valid]
    if len(invalid):
        cells = (invalid.groupby(["total_capacity", "per_region_cap"])
                 .size().reset_index(name="rows"))
        print(f"\nWARNING: {len(invalid)} of {len(df)} rows failed the placement check "
              f"and are excluded.")
        for _, r in cells.iterrows():
            print(f"  cap={r.total_capacity} (per-region {r.per_region_cap}): {r.rows} rows")
        print("  A whole capacity level failing usually means it sits below the "
              "workload's\n  mean concurrency and cannot be scheduled at all -- that "
              "cell is infeasible,\n  not merely tight, and removing it may remove the "
              "pressure the study is about.")
        df = df[df.placement_valid]
    if df.empty:
        raise SystemExit("every row failed the placement check -- nothing to compare.")
    return df


def paired(df):
    """
    One row per (seed, policy, cell) with both arms side by side.

    Pairing is on the full cell key: comparing arms across different
    capacities, margins or seeds would mix the foresight effect with the
    capacity, deadline and sampling effects.
    """
    keys = ["seed", "policy", "region_set", "total_capacity", "deadline_margin_h"]
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
    keys = ["seed", "region_set", "total_capacity", "deadline_margin_h"]
    base = (df[df.policy == BASELINE]
            .set_index(keys + ["foresight"])[BASIS].unstack("foresight"))

    rows = []
    for _, r in pair[pair.policy != BASELINE].iterrows():
        k = (r.seed, r.region_set, r.total_capacity, r.deadline_margin_h)
        if k not in base.index:
            continue
        bp, bf = base.loc[k, "perfect"], base.loc[k, "forecast"]
        save_p = 100 * (1 - r.perfect / bp)
        save_f = 100 * (1 - r.forecast / bf)
        rows.append({**r.to_dict(), "save_perfect": save_p, "save_forecast": save_f,
                     "retained_pct": (save_f / save_p * 100) if save_p > 0 else float("nan")})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Margin-invariant policies would otherwise contribute one measurement
    # once per margin; keep a single representative row for them.
    flex = out[out.policy.map(time_flexible)]
    rigid = (out[~out.policy.map(time_flexible)]
             .drop_duplicates(subset=["seed", "policy", "region_set", "total_capacity"]))
    return pd.concat([flex, rigid], ignore_index=True)


def seed_note(n_seeds):
    if n_seeds >= 3:
        return None
    return ("  NOTE: %d seed%s. Every figure below is provisional -- with no\n"
            "  across-seed spread to compare against, a 1-3%% effect cannot be\n"
            "  distinguished from workload sampling noise. Run\n"
            "  run_forecast_robust.bat before treating any of this as a result."
            % (n_seeds, "" if n_seeds == 1 else "s"))


def control_check(pair):
    """
    round-robin never consults the carbon trace while capacity is slack, so its
    two arms should be identical. Movement means the forecast trace is reaching
    a decision path it should not.
    """
    print("\n" + "=" * 70)
    print("CONTROL  the carbon-agnostic baseline must not move between arms")
    print("=" * 70)
    rr = pair[pair.policy == BASELINE]
    if rr.empty:
        print(f"\n  no '{BASELINE}' rows found; control check skipped.")
        return
    moved = rr[rr.degrade_pct.abs() > 1e-9]
    print(f"\n  {len(rr)} baseline cells, largest |change| between arms: "
          f"{rr.degrade_pct.abs().max():.6f}%")
    if len(moved) == 0:
        print("  -> identical in every cell, as expected.")
    else:
        print(f"  -> WARNING: {len(moved)} cells differ. Under capacity pressure the "
              "baseline\n     does consult the trace via the shared fallback, so small "
              "moves in tight\n     cells are legitimate; movement in slack cells is not.")
        for _, r in moved.nlargest(min(5, len(moved)), "degrade_pct").iterrows():
            print(f"     seed={r.seed} cap={r.total_capacity:<4d} "
                  f"m={r.deadline_margin_h:<3d} {r.degrade_pct:+.4f}%")


def direction_check(ret, n_seeds):
    """
    A forecast arm beating its perfect-foresight twin is a signal, not a win.
    Isolated cells can invert through packing luck. A cell that inverts at
    EVERY seed cannot be luck, and points at a mechanism -- most plausibly
    greedy herding, where perfect foresight sends every VM at the same true
    CI minimum, they collide against the per-region cap, and the overflow
    spills into dirtier regions that forecast noise would have avoided.
    """
    print("\n" + "=" * 70)
    print("SANITY  forecast should not systematically beat perfect foresight")
    print("=" * 70)
    inv = ret[ret.degrade_pct < 0]
    print(f"\n  {len(inv)} of {len(ret)} carbon-aware (seed, cell) pairs invert")
    if not len(inv):
        print("  -> none; every carbon-aware policy pays for imperfect foresight.")
        return

    print(f"  largest inversion {inv.degrade_pct.min():.2f}%, "
          f"median across inverted {inv.degrade_pct.median():.2f}%")
    if n_seeds >= 2:
        cell = ["policy", "total_capacity", "deadline_margin_h"]
        g = ret.groupby(cell).degrade_pct.agg(["mean", "std", "min", "max", "count"])
        always = g[g["max"] < 0]
        print(f"\n  cells inverting at EVERY seed: {len(always)} of {len(g)}")
        if len(always):
            print("  these are not sampling luck -- a mechanism is producing them:")
            for k, r in always.sort_values("mean").head(8).iterrows():
                print(f"     {k[0]:<12s} cap={k[1]:<4d} m={k[2]:<3d}  "
                      f"{r['mean']:+6.2f}% +/- {r['std']:.2f}  (n={int(r['count'])})")
            print("\n  -> worth investigating directly (per-region placement under the"
                  "\n     two arms) before reporting any pooled retention figure, since"
                  "\n     pooling cancels these against the positive cells.")
    else:
        print("  -> with one seed these cannot be separated from packing luck.")


def by_policy(ret, n_seeds):
    print("\n" + "=" * 70)
    print("FINDING  cost of imperfect foresight, by policy")
    print("=" * 70)
    note = seed_note(n_seeds)
    if note:
        print("\n" + note)
    print(f"\n  {'policy':<12s} {'cells':>5s} {'degrade%':>17s} {'save perf':>10s} "
          f"{'save fcst':>10s} {'retained%':>17s}")
    for pol, g in ret.groupby("policy"):
        print(f"  {pol:<12s} {len(g):5d} "
              f"{g.degrade_pct.mean():8.2f} +/- {g.degrade_pct.std():4.2f} "
              f"{g.save_perfect.mean():9.1f}% {g.save_forecast.mean():9.1f}% "
              f"{g.retained_pct.mean():8.1f}% +/- {g.retained_pct.std():4.1f}")

    if n_seeds >= 2:
        print("\n  the same effect measured per seed, so the spread is sampling noise:")
        for pol, g in ret.groupby("policy"):
            per_seed = g.groupby("seed").degrade_pct.mean()
            print(f"    {pol:<12s} {per_seed.mean():+6.2f}% +/- {per_seed.std():.2f} "
                  f"across {len(per_seed)} seeds   "
                  f"[{per_seed.min():+.2f}, {per_seed.max():+.2f}]")
        print("\n  -> a policy whose mean sits inside its own across-seed spread has no"
              "\n     demonstrated foresight penalty, whatever the pooled number says.")


def by_pressure(ret, n_seeds):
    """
    The hypothesis: forecast error should bite hardest where there is no slack
    to recover from a bad decision. The single-seed pass did NOT show that --
    it showed an inverted U, smallest at the tightest capacity and negative at
    the slackest. This table is what decides between the two shapes.
    """
    print("\n" + "=" * 70)
    print("FINDING  does the cost grow under capacity and deadline pressure?")
    print("=" * 70)

    flex = ret[ret.policy.map(time_flexible)]
    if flex.empty:
        print("\n  no time-flexible policies; pressure analysis skipped.")
        return

    print("\n  mean degradation %, by capacity x deadline margin "
          "(time-flexible policies):")
    tab = flex.pivot_table(index="total_capacity", columns="deadline_margin_h",
                           values="degrade_pct", aggfunc="mean")
    print("          " + "".join(f"{f'm={m}':>10s}" for m in tab.columns))
    for cap in tab.index:
        print(f"  cap={cap:<4d}" + "".join(f"{tab.loc[cap, m]:10.2f}" for m in tab.columns))

    if n_seeds >= 2:
        print("\n  across-seed sd of the same cells (compare like for like):")
        sd = (flex.groupby(["total_capacity", "deadline_margin_h", "seed"])
              .degrade_pct.mean().groupby(level=[0, 1]).std().unstack())
        print("          " + "".join(f"{f'm={m}':>10s}" for m in sd.columns))
        for cap in sd.index:
            print(f"  cap={cap:<4d}" + "".join(f"{sd.loc[cap, m]:10.2f}" for m in sd.columns))

    caps = sorted(flex.total_capacity.unique())
    margins = sorted(flex.deadline_margin_h.unique())
    tight = flex[(flex.total_capacity == caps[0]) & (flex.deadline_margin_h == margins[0])]
    slack = flex[(flex.total_capacity == caps[-1]) & (flex.deadline_margin_h == margins[-1])]
    if len(tight) and len(slack):
        td, sdp = tight.degrade_pct, slack.degrade_pct
        print(f"\n  tightest (cap={caps[0]}, m={margins[0]}): "
              f"{td.mean():+6.2f}% +/- {0 if pd.isna(td.std()) else td.std():.2f}")
        print(f"  slackest (cap={caps[-1]}, m={margins[-1]}): "
              f"{sdp.mean():+6.2f}% +/- {0 if pd.isna(sdp.std()) else sdp.std():.2f}")
        gap = td.mean() - sdp.mean()
        combined = (0 if pd.isna(td.std()) else td.std()) + \
                   (0 if pd.isna(sdp.std()) else sdp.std())
        verdict = "CLEARS" if abs(gap) > combined else "does NOT clear"
        print(f"  gap {gap:+.2f}pp vs combined spread {combined:.2f}pp -> {verdict}")

        # Endpoints alone can read as a clean trend while the interior
        # contradicts it, so check the whole capacity column rather than
        # trusting the two ends.
        by_cap = flex.groupby("total_capacity").degrade_pct.mean()
        ordered = by_cap.loc[caps]
        monotonic = (ordered.is_monotonic_decreasing or ordered.is_monotonic_increasing)
        print("\n  degradation by capacity (all margins pooled): "
              + ", ".join(f"cap={c}:{v:+.2f}%" for c, v in ordered.items()))
        if not monotonic:
            worst = ordered.idxmax()
            print(f"  -> NOT monotonic in capacity: the worst capacity is {worst}, an"
                  f"\n     interior point, not an endpoint. The endpoint comparison above"
                  f"\n     therefore describes a trend that does not exist. Slack alone"
                  f"\n     does not explain this shape -- do not report it as if it did.")
        else:
            print("  -> monotonic in capacity, so the endpoint comparison is "
                  "representative.")
        print("\n  -> the pressure story holds only if the gap clears the combined"
              "\n     spread AND runs the expected way (tight worse than slack) AND"
              "\n     the column is monotonic. Report the shape you actually have.")


def headline(ret, n_seeds):
    print("\n" + "=" * 70)
    print("HEADLINE")
    print("=" * 70)
    d, r = ret.degrade_pct, ret.retained_pct
    print(f"\n  across {len(ret)} carbon-aware (seed, cell) pairs, {n_seeds} seed(s):")
    print(f"    emissions rise under forecast : {d.mean():+.2f}% +/- {d.std():.2f}  "
          f"(worst cell {d.max():+.2f}%)")
    print(f"    savings retained              : {r.mean():.1f}% +/- {r.std():.1f}  "
          f"(worst cell {r.min():.1f}%)")
    if n_seeds >= 2:
        per_seed = ret.groupby("seed").degrade_pct.mean()
        print("\n    per-seed means: "
              + ", ".join(f"{v:+.2f}%" for v in per_seed.sort_index())
              + f"  (sd {per_seed.std():.2f})")
        if abs(per_seed.mean()) < per_seed.std():
            print("    -> the pooled mean is smaller than the seed-to-seed spread."
                  "\n       On this evidence there is no demonstrated foresight penalty"
                  "\n       overall, whatever individual cells show.")
    else:
        print("\n" + (seed_note(n_seeds) or ""))
    print()


def main():
    default = "results/forecast"
    target = sys.argv[1] if len(sys.argv) > 1 else default
    df = load(target)
    n_seeds = df.seed.nunique()
    print(f"  policies: {sorted(df.policy.unique())}")
    print(f"  caps: {sorted(df.total_capacity.unique())}  "
          f"margins: {sorted(df.deadline_margin_h.unique())}  seeds: {n_seeds}")

    pair = paired(df)
    ret = with_retention(df, pair)
    if ret.empty:
        raise SystemExit("\nno carbon-aware policies paired against the baseline.")

    control_check(pair)
    direction_check(ret, n_seeds)
    by_policy(ret, n_seeds)
    by_pressure(ret, n_seeds)
    headline(ret, n_seeds)


if __name__ == "__main__":
    main()