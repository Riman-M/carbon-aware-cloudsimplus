#!/usr/bin/env python3
"""
Figures for the revised Paper A, built only from committed result files.

  fig-margin-by-utilisation.pdf   value of deadline flexibility against load
                                  (results/r1/us_full)
  fig-origin-region.pdf           WaitAwhile emissions against the origin's
                                  mean intensity and diurnal amplitude
                                  (results/r1/us_home)
  fig-headroom-prediction.pdf     region-set composition: dominance against
                                  capacity-filled headroom on the 30 subsets,
                                  and the pre-registered hold-out test
                                  (results/subsets, results/holdout/score)

Every plotted value is computed here from the result files, with the same
estimators as analyze_r1.py and predict_holdout.py, whose functions are
imported rather than copied. The dynamic basis is primary throughout.

Colours are the Okabe-Ito blue, vermillion and bluish green, checked for
colour-vision deficiency, and every series is also distinguished by marker
shape so that the figures survive greyscale printing.

Usage (from the project root):
    python scripts/make_figures.py [--out figures]
"""
import argparse
import glob
import os
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_r1 as ar  # noqa: E402
import predict_holdout as ph  # noqa: E402

BLUE, VERM, GREEN = "#0072B2", "#D55E00", "#009E73"
GREY, INK = "#7A7A7A", "#222222"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["STIXGeneral", "Times New Roman", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 8,
    "axes.labelsize": 8,
    "axes.titlesize": 8,
    "legend.fontsize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "axes.linewidth": 0.6,
    "axes.edgecolor": "#444444",
    "axes.labelcolor": INK,
    "xtick.color": "#444444",
    "ytick.color": "#444444",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": "#E6E6E6",
    "grid.linewidth": 0.5,
    "legend.frameon": False,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.02,
})

SINGLE, DOUBLE = 3.5, 7.2  # inches, elsarticle column and text widths


# ---------------------------------------------------------------- figure 1

def fig_margin(out):
    full = ar.load(Path("results/r1"), "us_full")
    fig, ax = plt.subplots(figsize=(SINGLE, 2.3))
    specs = [("dyn", "Dynamic basis (primary)", BLUE, "o", "full", -0.9),
             ("attr", "Attributed basis", VERM, "s", "none", 0.9)]
    for basis, label, col, mk, fill, dx in specs:
        _, effects, _, _, _ = ar.section_a(full, basis, quiet=True)
        u = np.array([e[1] for e in effects])
        m = np.array([e[2] for e in effects])
        lo = np.array([e[3] for e in effects])
        hi = np.array([e[4] for e in effects])
        ax.errorbar(u + dx, m, yerr=[m - lo, hi - m], fmt=mk, ms=5, color=col,
                    mfc=col if fill == "full" else "white", mew=1.0, lw=1.0,
                    capsize=2, label=label, zorder=3)
    ax.axhline(0, color="#444444", lw=0.6, zorder=1)
    ax.set_xlabel("Capacity utilisation (%)")
    ax.set_ylabel("Reduction from 6 h to 48 h margin (%)")
    ax.set_xlim(0, 85)
    ax.legend(loc="upper right")
    fig.savefig(out / "fig-margin-by-utilisation.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 2

def fig_origin(out, cap=350, margin=48):
    home = ar.load(Path("results/r1"), "us_home")
    mean, diurnal = ar.region_profiles(ar.US, ar.JAN)
    t = home[(home.family == "time") & (home.total_capacity == cap)
             & (home.deadline_margin_h == margin)]
    g = t.groupby("home").dynamic_gco2.agg(["mean", "std", "count"])
    g["mean"] /= 1000
    g["std"] /= 1000
    g["half"] = [stats.t.ppf(0.975, n - 1) * s / np.sqrt(n) if n > 1 else 0
                 for s, n in zip(g["std"], g["count"])]
    space = home[(home.policy == "space") & (home.total_capacity == cap)
                 & (home.deadline_margin_h == margin)].dynamic_gco2.mean() / 1000

    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 2.4), sharey=True)
    for ax, xs, xlabel, letter in [
            (axes[0], mean, "Origin mean carbon intensity (gCO$_2$eq/kWh)", "a"),
            (axes[1], diurnal, "Origin diurnal amplitude (% of mean)", "b")]:
        x = g.index.map(xs).to_numpy()
        ax.errorbar(x, g["mean"], yerr=g["half"], fmt="o", ms=5, color=BLUE,
                    mew=0, lw=1.0, capsize=2, zorder=3, label="WaitAwhile, one origin each")
        # Label offsets in points, chosen so that neighbouring origins do not
        # collide; panel (a) crowds the four dirtiest grids together.
        nudge = ({"PJM": (-20, 2), "FPL": (5, 3), "ERCO": (5, -9), "ISNE": (5, 3),
                  "CISO": (5, -9)} if letter == "a" else {})
        for region, xv, yv in zip(g.index, x, g["mean"]):
            ax.annotate(region, (xv, yv), xytext=nudge.get(region, (4, 3)),
                        textcoords="offset points", fontsize=6.5, color=INK)
        ax.axhline(space, color=VERM, lw=1.0, ls="--", zorder=2, label="Space shifting")
        r, lo, hi, p, n = ar.pearson_fisher(x, g["mean"])
        ax.text(0.03, 0.95, f"({letter})  $r$ = {r:+.3f} [{lo:+.2f}, {hi:+.2f}]",
                transform=ax.transAxes, va="top", fontsize=7, color=INK)
        ax.set_xlabel(xlabel)
    axes[0].set_ylabel("Dynamic emissions (kgCO$_2$eq)")
    top = (g["mean"] + g["half"]).max()
    axes[0].set_ylim(0, top * 1.18)
    axes[0].legend(loc="upper left", bbox_to_anchor=(0.0, 0.89))
    fig.savefig(out / "fig-origin-region.pdf")
    plt.close(fig)


# ---------------------------------------------------------------- figure 3

def subset_cells():
    """Per set and capacity, the across-seed mean dynamic reduction of space
    against round-robin, with capacity-filled headroom computed exactly as
    in predict_holdout.py."""
    desc = pd.read_csv("data/subsets/subsets.csv")
    regs = dict(zip(desc.set, desc.regions.str.split("|")))
    dirs = [d for d in sorted(Path("data/subsets").iterdir()) if d.is_dir()]
    if not dirs:
        raise SystemExit("data/subsets/sub*/ is missing; run make_subsets.py first")
    mean = ph.region_means(dirs)
    loads = ph.seed_loads(Path("results/subsets"))
    frames = []
    for p in glob.glob("results/subsets/sub*_core_seed*.csv"):
        m = re.match(r"(sub\d+)_core_seed(\d+)", os.path.basename(p))
        df = pd.read_csv(p)
        df["set"], df["seed"] = m.group(1), int(m.group(2))
        frames.append(df)
    c = pd.concat(frames, ignore_index=True)
    k = ["set", "seed", "total_capacity"]
    rr = c[c.policy == "round-robin"].set_index(k)
    sp = c[c.policy == "space"].set_index(k)
    r = (100 * (1 - sp.dynamic_gco2 / rr.dynamic_gco2)).rename("red").reset_index()
    r["hc"] = [100 * ph.filled_headroom([mean[x] for x in regs[s]], cap, loads[seed])
               for s, seed, cap in zip(r.set, r.seed, r.total_capacity)]
    g = r.groupby(["set", "total_capacity"])[["red", "hc"]].mean().reset_index()
    return g.merge(desc[["set", "dominance", "headroom"]], on="set")


def fig_headroom(out, cap=85):
    g = subset_cells()
    s = g[g.total_capacity == cap]
    held = pd.read_csv("results/holdout/score/holdout-scored.csv")

    fig, axes = plt.subplots(1, 3, figsize=(DOUBLE, 2.45))

    ax = axes[0]
    ax.plot(s.dominance, s.red, "o", ms=4.5, color=GREY, mew=0)
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 5, 10])
    ax.set_xticklabels(["1", "2", "5", "10"])
    r = stats.pearsonr(np.log(s.dominance), s.red)[0]
    ax.text(0.03, 0.95, f"(a)  $R^2$ = {r**2:.2f}", transform=ax.transAxes, va="top")
    ax.set_xlabel("Dominance ratio (log scale)")
    ax.set_ylabel(f"Observed reduction, {cap} slots (%)")

    ax = axes[1]
    ax.plot(s.hc, s.red, "o", ms=4.5, color=BLUE, mew=0)
    lr = stats.linregress(s.hc, s.red)
    xx = np.linspace(s.hc.min(), s.hc.max(), 2)
    ax.plot(xx, lr.intercept + lr.slope * xx, color=BLUE, lw=0.8)
    ax.text(0.03, 0.95, f"(b)  $R^2$ = {lr.rvalue**2:.2f}", transform=ax.transAxes, va="top")
    ax.set_xlabel("Capacity-filled headroom (%)")
    ax.sharey(axes[0])
    ax.tick_params(labelleft=False)

    ax = axes[2]
    lim = [min(held.pred_dyn.min(), held.dyn.min()) - 3, max(held.pred_dyn.max(), held.dyn.max()) + 3]
    ax.plot(lim, lim, color="#444444", lw=0.6, zorder=1)
    for size, col, mk in [(4, BLUE, "o"), (6, VERM, "^")]:
        h = held[held["size"] == size]
        ax.plot(h.pred_dyn, h.dyn, mk, ms=4.5, color=col, mew=0, zorder=3,
                label=f"{size}-region sets")
    mae = (held.dyn - held.pred_dyn).abs().mean()
    rr_ = stats.pearsonr(held.pred_dyn, held.dyn)[0]
    ax.text(0.03, 0.95, f"(c)  MAE {mae:.2f} pp, $r$ = {rr_:.3f}",
            transform=ax.transAxes, va="top")
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Predicted reduction (%)")
    ax.set_ylabel("Observed reduction (%)")
    ax.legend(loc="lower right")
    fig.tight_layout(w_pad=1.2)
    fig.savefig(out / "fig-headroom-prediction.pdf")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("figures"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    fig_margin(args.out)
    fig_origin(args.out)
    fig_headroom(args.out)
    for f in sorted(args.out.glob("fig-*.pdf")):
        print(f"wrote {f}")


if __name__ == "__main__":
    main()
