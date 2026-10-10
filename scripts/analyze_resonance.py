#!/usr/bin/env python3
"""
Measure the deadline-margin resonance in the perfect-foresight planner.

Reads results/resonance/{jul,dec}_seed*.csv (run_resonance.bat) and asks four
questions in order, each of which can kill the finding:

1. Is the planner non-monotonic in deadline margin at all?
2. Does the damage PEAK near margin=24, rather than simply growing with margin?
3. Does the peak weaken when the diurnal cycle flattens?
4. Does fleet synchronisation peak at the same margin as emissions?

The headline statistic is the monotonicity VIOLATION: how much worse a cell
is than the best result achieved at any SHORTER margin. Zero means more
deadline freedom never hurt.
"""
import glob
import os
import re
import sys
from pathlib import Path

import pandas as pd

BASIS = "attributed_gco2"
SEASON_LABEL = {"jul": "July 2021 (sharp diurnal)", "dec": "December 2021 (flat diurnal)"}


def FLEX(p):
    return p == "space+time" or p.startswith("time@")


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
                f"or dec_seedN.csv as written by run_resonance.bat")
        d = pd.read_csv(p)
        d["season"], d["seed"] = m.group(1), int(m.group(2))
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    print(f"loaded {len(df)} rows from {len(paths)} files under {target}")

    bad = df[~df.placement_valid]
    if len(bad):
        print(f"\nWARNING: {len(bad)} of {len(df)} rows failed the placement check.")
        print("  These are usually VMs deferred past the end of the 720h horizon, which")
        print("  gets MORE likely at long margins -- exactly the axis under study. Their")
        print("  distribution across margins is reported below, because dropping them")
        print("  silently would bias the very comparison this script makes.")
        t = bad.groupby(["season", "deadline_margin_h"]).size()
        for k, n in t.items():
            print(f"    {k[0]} margin={k[1]:<3d}: {n} rows")
        df = df[df.placement_valid]
    if df.empty:
        raise SystemExit("every row failed the placement check.")
    return df


def violations(df, arm):
    d = df[(df.foresight == arm) & (df.policy.map(FLEX))]
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
                         "share": r.max_region_share,
                         "deferral": r.mean_deferral_h,
                         "violation_pct": float("nan") if best is None
                                          else 100 * (v / best - 1)})
            best = v if best is None else min(best, v)
    return pd.DataFrame(rows)


def q1_monotonic(viol, arm):
    print("\n" + "=" * 70)
    print(f"Q1  is the {arm}-foresight planner non-monotonic in deadline margin?")
    print("=" * 70)
    v = viol.dropna(subset=["violation_pct"])
    if v.empty:
        print("\n  not enough margins per series to tell.")
        return
    series = v.groupby(["season", "seed", "policy", "total_capacity"]).violation_pct.max()
    hurt = series[series > 0.1]
    print(f"\n  {len(hurt)} of {len(series)} series have a margin that is WORSE than a "
          f"shorter one")
    print(f"  largest violation anywhere: {series.max():+.2f}%")
    if len(hurt) == 0:
        print("  -> monotonic throughout; there is no planner defect to explain.")
    else:
        print("  -> more deadline freedom made the planner worse. With perfect")
        print("     information that cannot be a foresight effect; it is the planner.")


def q2_peak(viol, arm):
    print("\n" + "=" * 70)
    print(f"Q2  does the damage PEAK near margin=24, or just grow? ({arm} arm)")
    print("=" * 70)
    for season, g in viol.groupby("season"):
        print(f"\n  {SEASON_LABEL.get(season, season)}")
        t = g.pivot_table(index="margin", columns="total_capacity",
                          values="violation_pct", aggfunc="mean")
        caps = list(t.columns)
        print("    margin  " + "".join(f"{f'cap={c}':>12s}" for c in caps)
              + f"{'share':>9s}{'defer_h':>9s}")
        shares = g.groupby("margin").share.mean()
        defer = g.groupby("margin").deferral.mean()
        for m in t.index:
            cells = "".join(
                "         n/a" if pd.isna(t.loc[m, c]) else f"{t.loc[m, c]:12.2f}"
                for c in caps)
            print(f"    {m:<6d}" + cells + f"{shares[m]:9.3f}{defer[m]:9.2f}")

        series = g.dropna(subset=["violation_pct"])
        if series.empty:
            continue
        by_m = series.groupby("margin").violation_pct.mean()
        peak_m = by_m.idxmax()
        interior = peak_m not in (by_m.index.min(), by_m.index.max())
        print(f"\n    worst margin: {peak_m} ({by_m.max():+.2f}%)"
              + ("  -- an interior point, i.e. a genuine peak" if interior
                 else "  -- an endpoint, NOT a peak"))
        if not interior:
            print("    -> a monotone rise, not a resonance. The diurnal account predicts")
            print("       a peak; this does not show one.")


def q3_season(viol):
    print("\n" + "=" * 70)
    print("Q3  does the peak weaken when the diurnal cycle flattens?")
    print("=" * 70)
    v = viol.dropna(subset=["violation_pct"])
    if set(v.season.unique()) != {"jul", "dec"}:
        print(f"\n  need both seasons; have {sorted(v.season.unique())}. "
              "Run the December half of run_resonance.bat.")
        return

    print("\n  each season's own worst margin (context):")
    for season, g in v.groupby("season"):
        by_m = g.groupby("margin").violation_pct.mean()
        print(f"    {season:<5s} worst at margin {by_m.idxmax():<3d} ({by_m.max():+.2f}%)")

    jul = v[v.season == "jul"]
    test_m = jul.groupby("margin").violation_pct.mean().idxmax()
    print(f"\n  seasonal test at margin {test_m} (July's peak) in both seasons:")
    print(f"\n  {'season':<10s} {'violation':>12s} {'across-seed sd':>16s} {'seeds':>7s}")
    stat = {}
    for season in ("jul", "dec"):
        g = v[(v.season == season) & (v.margin == test_m)]
        per_seed = g.groupby("seed").violation_pct.mean()
        sd = per_seed.std() if len(per_seed) > 1 else 0.0
        stat[season] = (per_seed.mean(), 0.0 if pd.isna(sd) else sd, len(per_seed))
        print(f"  {season:<10s} {stat[season][0]:11.2f}% {stat[season][1]:15.2f} "
              f"{stat[season][2]:7d}")

    jv, js, _ = stat["jul"]
    dv, ds, _ = stat["dec"]
    gap, combined = jv - dv, js + ds
    print(f"\n  July exceeds December by {gap:+.2f}pp at margin {test_m}; "
          f"combined across-seed spread {combined:.2f}pp")
    if gap > combined and jv > 0:
        print("  -> CONSISTENT with the diurnal-resonance account: the peak tracks the")
        print("     depth of the daily cycle, as predicted before running this.")
    elif abs(gap) <= combined:
        print("  -> NOT consistent: the peak is statistically indistinguishable between")
        print("     a sharp and a flat diurnal cycle. The non-monotonicity is real but")
        print("     the diurnal explanation is not supported -- look at the planner's")
        print("     tie-breaking and the horizon edge instead, and do not publish the")
        print("     resonance account on this evidence.")
    else:
        print("  -> CONTRADICTED: the peak is LARGER where the diurnal cycle is flatter,")
        print("     which is the opposite of the prediction. The mechanism is something")
        print("     else; investigate before reporting.")


def q4_mechanism(df):
    """
    Direct test of the synchronisation account, using the temporal concentration
    metrics rather than inferring from emissions alone.
    """
    print("\n" + "=" * 70)
    print("Q4  does fleet synchronisation actually peak where emissions do?")
    print("=" * 70)
    need = ["modal_start_hour_share", "start_hour_entropy", "peak_to_mean_occupancy"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        print(f"\n  results predate the temporal-concentration metrics ({', '.join(missing)}).")
        print("  Re-run the sweep after rebuilding; without these, the mechanism cannot")
        print("  be tested and only the phenomenon itself can be reported.")
        return

    # Split by capacity. The violation is an order of magnitude larger at the
    # slack capacity, so pooling capacities averages the cell that carries the
    # effect together with one that barely shows it and can report a spurious
    # mismatch between the emissions peak and the concentration peak.
    #
    # round-robin never time-shifts, so its start-hour distribution IS the raw
    # arrival distribution. A time-shifting policy is only "synchronised" if it
    # exceeds that baseline, so the baseline is printed alongside.
    d = df[(df.foresight == "perfect") & (df.policy.map(FLEX))]
    rr = df[df.policy == "round-robin"].groupby("season").modal_start_hour_share.mean()

    for (season, cap), g in d.groupby(["season", "total_capacity"]):
        base = rr.get(season, float("nan"))
        print(f"\n  {SEASON_LABEL.get(season, season)}, cap={cap}, perfect arm"
              f"   (arrival-pattern baseline modal={base:.3f})")
        t = g.groupby("deadline_margin_h").agg(
            attr=(BASIS, "mean"), modal=("modal_start_hour_share", "mean"),
            crowd=("peak_to_mean_occupancy", "mean"),
            hosts=("peak_active_hosts", "mean"),
            akwh=("attributed_kwh", "mean"), dyn=("dynamic_gco2", "mean"),
            dkwh=("dynamic_kwh", "mean"))
        # Carbon per kWh ACTUALLY consumed. This is the decisive column: it
        # divides out how much energy was drawn and how many hosts were lit,
        # leaving only how dirty the chosen hours were. The attributed basis
        # charges the idle floor of every active host, so a crowding effect
        # could otherwise raise emissions mechanically, with no worse carbon
        # timing at all.
        t["ci"] = t.attr / t.akwh
        t["ci_dyn"] = t.dyn / t.dkwh
        print(f"    {'margin':>7s} {'attr_t':>9s} {'modal':>7s} {'vs_arr':>7s} "
              f"{'crowd':>7s} {'hosts':>6s} {'CI':>7s} {'CI_dyn':>7s}")
        for m, r in t.iterrows():
            print(f"    {m:7d} {r.attr / 1000:9.0f} {r.modal:7.3f} "
                  f"{r.modal - base:+7.3f} {r.crowd:7.3f} {r.hosts:6.1f} "
                  f"{r.ci:7.1f} {r.ci_dyn:7.1f}")
        if len(t) < 3:
            continue
        am, mm, cm, him = (t.attr.idxmax(), t.modal.idxmax(),
                           t.crowd.idxmax(), t.ci.idxmax())
        print(f"\n    peaks -> emissions:{am}  clock-hour agreement:{mm}  "
              f"crowding:{cm}  realised CI:{him}")
        print(f"    corr(emissions, modal)={t.attr.corr(t.modal):+.3f}  "
              f"corr(emissions, crowding)={t.attr.corr(t.crowd):+.3f}  "
              f"corr(crowding, CI)={t.crowd.corr(t.ci):+.3f}")
        if him == am and t.hosts.idxmax() != am:
            print("    -> realised CI peaks WITH emissions while the active-host count")
            print("       does not, so the planner genuinely chose dirtier hours. The")
            print("       effect is carbon timing, not the attributed basis lighting")
            print("       more machines.")
        elif him != am:
            print("    -> realised CI does NOT peak with emissions: the rise is coming")
            print("       from energy drawn or hosts lit, not from dirtier hours. Do not")
            print("       describe this as a carbon-timing effect.")
        if mm == am and (t.modal.max() - base) > 0:
            print("    -> clock-hour agreement also peaks here and exceeds the arrival")
            print("       baseline, so fleet synchronisation is a credible driver.")
        else:
            print("    -> clock-hour agreement does not line up or stays below the")
            print("       arrival baseline, so describe the mechanism as temporal")
            print("       crowding rather than clock-hour resonance in this cell.")


def arms_contrast(viol_p, viol_f):
    print("\n" + "=" * 70)
    print("CONTRAST  does forecast noise suppress the peak?")
    print("=" * 70)
    print("\n  If the defect is fleet synchronisation, planning against a noisier")
    print("  signal should desynchronise the fleet and reduce the violation.")
    print(f"\n  {'season':<10s} {'margin':>7s} {'perfect':>10s} {'forecast':>10s} {'diff':>9s}")
    for season in sorted(set(viol_p.season.unique()) | set(viol_f.season.unique())):
        p = viol_p[viol_p.season == season].groupby("margin").violation_pct.mean()
        f = viol_f[viol_f.season == season].groupby("margin").violation_pct.mean()
        for m in sorted(set(p.index) & set(f.index)):
            if pd.isna(p[m]) and pd.isna(f[m]):
                continue
            print(f"  {season:<10s} {m:7d} {p[m]:9.2f}% {f[m]:9.2f}% {f[m] - p[m]:+8.2f}pp")
    print("\n  -> a consistently negative diff at the peak margin supports the")
    print("     synchronisation account. It does NOT mean forecasting is beneficial:")
    print("     the right fix for a planner that herds is a better planner, not a")
    print("     worse input.")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "results/resonance"
    df = load(target)
    print(f"  seasons: {sorted(df.season.unique())}  seeds: {sorted(df.seed.unique())}")
    print(f"  caps: {sorted(df.total_capacity.unique())}  "
          f"margins: {sorted(df.deadline_margin_h.unique())}")
    if len(df.deadline_margin_h.unique()) < 4:
        print("\n  NOTE: a peak cannot be distinguished from a step with so few margins.")

    vp = violations(df, "perfect")
    if vp.empty:
        raise SystemExit("no time-flexible policies in the perfect arm.")
    q1_monotonic(vp, "perfect")
    q2_peak(vp, "perfect")
    q3_season(vp)
    q4_mechanism(df)

    vf = violations(df, "forecast")
    if not vf.empty:
        arms_contrast(vp, vf)
    print()


if __name__ == "__main__":
    main()