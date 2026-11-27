#!/usr/bin/env python3
"""
Select candidate region sets for the region-set composition experiment.

The submitted manuscript compared two region sets (US, EU). Two points cannot
establish how reported savings depend on region-set composition, and the
reviewers said so. This script replaces the contrast with a sample of
five-region subsets drawn from the thirteen CarbonCast regions, chosen so that
three candidate descriptors of composition vary as independently as the data
allow:

  dominance   ratio of the second-cleanest to the cleanest regional mean
  spread      ratio of the dirtiest to the cleanest regional mean
  headroom    1 - (cleanest mean / mean of the set's regional means), the
              reduction available to a policy that moves all work from an
              even spread into the cleanest region

Region count is fixed at five so that composition is not confounded with set
size. All 1287 five-region subsets are enumerated and the sample is stratified
on (log dominance, log spread), because the two are correlated across subsets
and a simple random draw would leave a regression unable to separate them.
Within each occupied stratum, subsets are drawn uniformly at random with a
fixed seed, so the selection is reproducible.

Means are computed over the same window the simulation uses, not over the
whole of 2020-2021, because that is the signal the planner sees.

Output: data/subsets/subsets.csv, one row per selected subset, and one
directory per subset holding carbon_intensity.csv and trace_meta.properties.
vm_requests.csv is copied in per workload seed by the runner script, so that
every subset is evaluated on an identical workload.

Usage:
    python scripts/make_subsets.py --carbon-src data/carboncast
"""
import argparse
import csv
import itertools
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prep_traces as pt  # noqa: E402


def descriptors(means):
    v = sorted(means)
    set_mean = sum(v) / len(v)
    return {
        "cleanest_ci": v[0],
        "second_ci": v[1],
        "set_mean_ci": set_mean,
        "dominance": v[1] / v[0],
        "spread": v[-1] / v[0],
        "headroom": 1.0 - v[0] / set_mean,
    }


def stratum(d, dom_edges, spr_edges):
    def idx(x, edges):
        for i in range(len(edges) - 1):
            if edges[i] <= x < edges[i + 1]:
                return i
        return len(edges) - 2
    return idx(math.log(d["dominance"]), dom_edges), idx(math.log(d["spread"]), spr_edges)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--carbon-src", type=Path, default=Path("data/carboncast"))
    ap.add_argument("--out", type=Path, default=Path("data/subsets"))
    ap.add_argument("--hours", type=int, default=720)
    ap.add_argument("--start-hour", type=int, default=0)
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--bins", type=int, default=4,
                    help="strata per axis on log dominance and log spread")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--exclude", type=Path, nargs="*", default=[],
                    help="index files (subsets.csv) whose region sets may not be "
                         "drawn again, so that a new hold-out round is unseen")
    ap.add_argument("--prefix", default="sub",
                    help="set name prefix; the hold-out sets use h4- and h6-")
    args = ap.parse_args()

    regions = sorted(pt.CARBONCAST_REGIONS)
    rows = pt.load_carboncast(args.carbon_src, regions, args.hours, args.start_hour)
    series = {}
    for _, r, ci in rows:
        series.setdefault(r, []).append(ci)
    mean = {r: sum(v) / len(v) for r, v in series.items()}

    used = set()
    for idx in args.exclude:
        with idx.open(newline="") as fh:
            for row in csv.DictReader(fh):
                used.add(frozenset(row["regions"].split("|")))

    cands = []
    for combo in itertools.combinations(regions, args.size):
        if frozenset(combo) in used:
            continue
        d = descriptors([mean[r] for r in combo])
        d["regions"] = combo
        cands.append(d)

    lo_d = min(math.log(c["dominance"]) for c in cands)
    hi_d = max(math.log(c["dominance"]) for c in cands)
    lo_s = min(math.log(c["spread"]) for c in cands)
    hi_s = max(math.log(c["spread"]) for c in cands)
    dom_edges = [lo_d + i * (hi_d - lo_d) / args.bins for i in range(args.bins + 1)]
    spr_edges = [lo_s + i * (hi_s - lo_s) / args.bins for i in range(args.bins + 1)]

    strata = {}
    for c in cands:
        strata.setdefault(stratum(c, dom_edges, spr_edges), []).append(c)

    # Round-robin over occupied strata until the target count is reached, so
    # that sparse strata are represented before dense ones are drawn twice.
    rng = random.Random(args.seed)
    pools = {k: rng.sample(v, len(v)) for k, v in sorted(strata.items())}
    chosen = []
    while len(chosen) < args.count and any(pools.values()):
        for k in sorted(pools):
            if pools[k] and len(chosen) < args.count:
                chosen.append((k, pools[k].pop()))

    chosen.sort(key=lambda kc: (kc[1]["dominance"], kc[1]["spread"]))
    args.out.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for i, (k, c) in enumerate(chosen, start=1):
        name = f"{args.prefix}{i:02d}"
        # Region order inside a set fixes WaitAwhile's default origin and the
        # round-robin rotation. Sorting by mean makes that order a stated
        # property of the set rather than an accident of enumeration.
        regs = sorted(c["regions"], key=mean.get)
        sub_dir = args.out / name
        sub_rows = [(h, r, ci) for (h, r, ci) in rows if r in regs]
        pt.write_csv(sub_dir / "carbon_intensity.csv",
                     ["hour", "region", "ci_gco2_per_kwh"], sub_rows)
        pt.write_meta(sub_dir / "trace_meta.properties", {
            "trace_mode": "real",
            "start_hour": args.start_hour,
            "region_set": name,
            "regions": "|".join(regs),
            "hours": args.hours,
        })
        index_rows.append([
            name, "|".join(regs), k[0], k[1],
            f"{c['cleanest_ci']:.1f}", f"{c['second_ci']:.1f}", f"{c['set_mean_ci']:.1f}",
            f"{c['dominance']:.4f}", f"{c['spread']:.4f}", f"{c['headroom']:.4f}",
        ])

    pt.write_csv(args.out / "subsets.csv",
                 ["set", "regions", "dom_stratum", "spread_stratum",
                  "cleanest_ci", "second_ci", "set_mean_ci",
                  "dominance", "spread", "headroom"], index_rows)

    # How well the design separates the descriptors. A regression on these
    # is only interpretable if the pairwise correlations are well below 1.
    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((y - mb) ** 2 for y in b)
        return cov / math.sqrt(va * vb)

    ld = [math.log(c["dominance"]) for _, c in chosen]
    ls = [math.log(c["spread"]) for _, c in chosen]
    hr = [c["headroom"] for _, c in chosen]
    ld_all = [math.log(c["dominance"]) for c in cands]
    ls_all = [math.log(c["spread"]) for c in cands]
    print(f"\n{len(chosen)} subsets from {len(cands)} candidates, "
          f"{len(strata)} occupied strata")
    print(f"corr(log dominance, log spread): selected {corr(ld, ls):+.3f}, "
          f"all subsets {corr(ld_all, ls_all):+.3f}")
    print(f"corr(log dominance, headroom):   selected {corr(ld, hr):+.3f}")
    print(f"corr(log spread, headroom):      selected {corr(ls, hr):+.3f}")
    print(f"dominance range {min(math.exp(x) for x in ld):.2f}-{max(math.exp(x) for x in ld):.2f}, "
          f"spread range {min(math.exp(x) for x in ls):.2f}-{max(math.exp(x) for x in ls):.2f}")


if __name__ == "__main__":
    main()
