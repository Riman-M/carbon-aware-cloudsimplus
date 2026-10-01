#!/usr/bin/env python3
"""
Generate input traces for the carbon-aware CloudSim Plus harness.

Two modes:

  synthetic  Produces plausible carbon-intensity and VM-request traces so the
             harness runs immediately. NOT for publication -- results from
             synthetic CI are not evidence about real grids.

  real       Converts CarbonCast per-region lifecycle-emission CSVs into the
             harness format. See --help for the expected inputs.

CarbonCast (Maji et al., BuildSys 2022; github.com/carbonfirst/CarbonCast) ships
hourly carbon intensity for 13 grid regions across the US, Europe and Australia,
covering 2020-2021 with no missing hours. It is preferred over the Electricity
Maps portal here for three reasons: the data is openly redistributable, so the
experiment is reproducible from a public source; it is not capped at five zone
downloads; and it also publishes paired actual/forecast series, which is what the
uncertainty-aware follow-up needs and which the free Electricity Maps tier does
not provide.

The synthetic CI model uses a per-region base level, a diurnal cycle (solar
depresses midday CI in sunny regions), a weekly component, and AR(1) noise. The
important property for benchmarking is that regions differ both in *level* and
in *phase*, because space-shifting gains come from level spread and time-shifting
gains come from within-region amplitude. A generator that got either wrong would
make one of the two policy families look artificially good.
"""

import argparse
import csv
import math
import random
from pathlib import Path

# CarbonCast region codes. Values are (mean_ci, mean_daily_swing, swing_pct)
# measured over 2020-2021 from the lifecycle-emission files, recorded here so a
# region set can be chosen deliberately rather than by name recognition.
#
# Cross-region spread is 17.8x (SE 38 -> PL 674), which is what space-shifting
# exploits. Within-region daily swing ranges from 10.6% (SE) to 63.6% (CISO),
# which is what time-shifting exploits -- and unlike the synthetic generator,
# where every region got a similar amplitude, that variation is real and large
# enough to be an experimental axis in its own right.
CARBONCAST_REGIONS = {
    "SE":      (38,   4, 10.6),   # hydro/nuclear: cleanest, flattest
    "BPAT":    (65,  18, 27.1),   # Bonneville, hydro-heavy
    "ES":      (170, 53, 31.4),   # strong solar
    "NYISO":   (250, 57, 23.0),
    "CISO":    (273, 174, 63.6),  # largest diurnal swing: solar duck curve
    "ISNE":    (313, 57, 18.2),
    "DE":      (326, 138, 42.3),  # coal + high renewables
    "ERCO":    (366, 129, 35.2),  # gas + wind + solar
    "PJM":     (368, 54, 14.7),
    "FPL":     (383, 66, 17.3),
    "NL":      (492, 83, 16.9),
    "AUS_QLD": (667, 250, 37.5),  # coal, but large swing and a high floor
    "PL":      (674, 94, 14.0),   # coal-dominated: dirtiest, nearly flat
}

# Suggested region sets for --mode real, replacing the synthetic-era sets. The
# Electricity Maps zone codes used previously (FR, GB, IT-NO, JP-KN, KR, AU-NSW)
# have no CarbonCast equivalent, so the sets are rebuilt around what exists.
# Note the loss of FR: the synthetic study used it as the clean, flat, nuclear
# anchor. SE substitutes on level but is hydro, so it behaves differently.
REAL_REGION_SETS = {
    "eu":    ["SE", "ES", "DE", "NL", "PL"],
    "us":    ["BPAT", "CISO", "ERCO", "ISNE", "NYISO", "PJM", "FPL"],
    "world": ["SE", "ES", "DE", "NL", "PL", "CISO", "ERCO", "PJM", "AUS_QLD"],
}

# Region profiles loosely calibrated to published annual averages, in gCO2eq/kWh.
# (base_level, diurnal_amplitude, solar_phase_shift_hours, utc_offset)
REGION_PROFILES = {
    "FR":            (58,  12,  0,  1),   # nuclear-heavy, flat
    "SE-SE3":        (35,   8,  0,  1),   # hydro/nuclear, very clean
    "IT-NO":         (315,  60, -3,  1),  # gas + solar
    "DE":            (380, 110, -3,  1),  # coal + high renewables, big swings
    "GB":            (240,  55, -3,  0),  # gas + wind
    "ES":            (185,  75, -3,  1),  # strong solar
    "PL":            (650,  45, -3,  1),  # coal-dominated, flat and dirty
    "NL":            (330,  60, -3,  1),
    "US-TEX-ERCO":   (395,  70, -3, -6),  # gas + wind + solar
    "US-CAL-CISO":   (240, 105, -3, -8),  # very large solar swing
    "US-MIDA-PJM":   (350,  40, -3, -5),
    "AU-NSW":        (620,  80, -3, 10),
    "JP-KN":         (455,  45, -3,  9),
    "KR":            (430,  35, -3,  9),
    "IE":            (290,  60, -3,  0),
}


def synth_carbon(regions, hours, seed):
    rng = random.Random(seed)
    rows = []
    for region in regions:
        base, amp, phase, utc = REGION_PROFILES[region]
        noise = 0.0
        for h in range(hours):
            local_h = (h + utc) % 24
            # Diurnal: CI dips around local midday when solar is on.
            diurnal = -amp * math.sin(math.pi * (local_h + phase) / 12.0)
            # Weekly: lower demand at weekends -> slightly cleaner.
            weekly = -0.05 * base * math.sin(2 * math.pi * h / (24 * 7))
            # AR(1) noise keeps consecutive hours correlated, which is what makes
            # forecasting non-trivial. White noise would make persistence useless
            # and flatter the ML baselines.
            noise = 0.75 * noise + rng.gauss(0, 0.06 * base)
            ci = max(5.0, base + diurnal + weekly + noise)
            rows.append((h, region, round(ci, 2)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def synth_requests(n, hours, seed, deadline_margin_h):
    rng = random.Random(seed + 1)
    rows = []
    for i in range(n):
        pes = rng.choice([1, 2, 4, 8, 16])
        ram_mb = pes * rng.choice([2048, 4096, 8192])
        duration = rng.randint(6, 24)                 # Zanotto: 6-24h VM lifetime
        latest_arrival = hours - duration - deadline_margin_h - 1
        if latest_arrival < 0:
            raise SystemExit(
                f"horizon {hours}h too short for duration {duration}h "
                f"+ margin {deadline_margin_h}h")
        arrival = rng.randint(0, latest_arrival)
        deadline = arrival + duration + deadline_margin_h
        rows.append((i, pes, ram_mb, duration, arrival, deadline))
    return rows


def load_carboncast(src, regions, hours, start_hour):
    """
    Read CarbonCast <REGION>_lifecycle_emissions.csv files into harness rows.

    Each file carries an unnamed index column, a "UTC time" column, a
    "carbon_intensity" column in gCO2eq/kWh, and one column per generation
    source. Only the timestamp and carbon intensity are used.

    Timestamps are NOT in a single format across regions: some files use
    %m/%d/%Y %H:%M:%S, some ISO 8601, and DE carries an explicit +00:00 offset.
    They are parsed permissively and normalised to UTC, then checked for
    completeness rather than trusted -- a silently short region would shift every
    later hour and corrupt the cross-region alignment the whole experiment rests
    on.
    """
    import datetime as _dt

    def parse_ts(raw):
        raw = raw.strip()
        for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                ts = _dt.datetime.strptime(raw, fmt)
                return ts.replace(tzinfo=None) if ts.tzinfo is None \
                    else ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
            except ValueError:
                continue
        raise SystemExit(f"unparseable timestamp {raw!r} in {src}")

    series = {}
    for region in regions:
        path = src / f"{region}_lifecycle_emissions.csv"
        if not path.exists():
            raise SystemExit(
                f"missing {path}\n"
                f"Expected CarbonCast lifecycle files named <REGION>_lifecycle_emissions.csv "
                f"in {src}. Available regions: {sorted(CARBONCAST_REGIONS)}")
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            if "carbon_intensity" not in reader.fieldnames or "UTC time" not in reader.fieldnames:
                raise SystemExit(f"{path} lacks 'UTC time'/'carbon_intensity' columns")
            pairs = [(parse_ts(row["UTC time"]), float(row["carbon_intensity"]))
                     for row in reader if row["carbon_intensity"] not in ("", None)]
        pairs.sort()
        series[region] = pairs

    # Align on a common window. Regions are only comparable hour-for-hour if they
    # are indexed off the same wall-clock instant; indexing each file from its own
    # first row would silently offset regions against each other.
    common_start = max(p[0][0] for p in series.values())
    common_end = min(p[-1][0] for p in series.values())
    span = int((common_end - common_start).total_seconds() // 3600) + 1
    need = start_hour + hours
    if need > span:
        raise SystemExit(
            f"requested --start-hour {start_hour} + --hours {hours} = {need}h "
            f"but only {span}h are common to all selected regions "
            f"({common_start} .. {common_end}).")

    base = common_start + _dt.timedelta(hours=start_hour)
    rows = []
    for region, pairs in series.items():
        lookup = dict(pairs)
        for h in range(hours):
            ts = base + _dt.timedelta(hours=h)
            ci = lookup.get(ts)
            if ci is None:
                raise SystemExit(
                    f"{region} has no observation at {ts}. CarbonCast 2020-2021 "
                    f"files are gap-free, so this indicates a truncated or edited "
                    f"download; re-fetch rather than interpolating.")
            if ci <= 0:
                raise SystemExit(f"{region} has non-positive CI {ci} at {ts}")
            rows.append((h, region, round(ci, 2)))
    rows.sort(key=lambda r: (r[0], r[1]))
    print(f"carbon window: {base} + {hours}h "
          f"(common span {span}h from {common_start})")
    # base is returned so a paired forecast trace (see load_carboncast_forecast)
    # can be indexed to the identical wall-clock hours -- the two traces are
    # only a valid "perfect vs forecast" comparison if hour h means the same
    # instant in both.
    return rows, base


def load_carboncast_forecast(src, regions, hours, base, lead_hour=24):
    """
    Read CarbonCast's paired actual/forecast lifecycle files into the
    planning-trace rows used for Paper B's realistic-foresight arm.

    Expected files: <REGION>_carbon_from_src_forecasts_lifecycle_Jul_Dec_2021.csv,
    or for some regions <REGION>_carbon_from_src_prod_forecasts_lifecycle_Jul_Dec_2021.csv
    (CarbonCast's own naming is inconsistent across regions; both are tried).
    These cover only Jul-Dec 2021, a subset of the 2020-2021 window the ground-
    truth lifecycle_emissions.csv files span -- --start-hour/--hours must land
    inside Jul-Dec 2021 or this will fail loudly below.

    Each file is NOT one row per target hour. It is CarbonCast's 96-hour-ahead
    forecast reissued daily, so a given target hour is covered by up to four
    overlapping forecasts at different lead times (1..96h, from four different
    issue runs). Mixing lead times within one trace would make "the forecast"
    an undefined blend of prediction horizons, so only the forecast issued
    exactly `lead_hour` hours ahead of each target hour is kept -- default 24h,
    i.e. the standard day-ahead forecast.

    Lead hour is derived from each row's position within its 96-row issue
    block, and blocks are detected from the UTC time deltas (a new block
    starts wherever consecutive rows are not 1h apart) rather than assumed
    from row position -- a resorted or edited file then fails loudly instead
    of silently mis-attributing a lead time to the wrong forecast.

    Because reissue is daily (every 24h) rather than hourly, an *exact* lead
    of `lead_hour` only exists for one hour-of-day; every other target hour's
    available leads sit at `lead_hour` +/- a multiple of 24h (e.g. for
    lead_hour=24: some hours only ever see {24, 48, 72, 96}, others only
    {1, 25, 49, 73}). Requiring an exact match would therefore produce a
    trace with real values at 1-in-24 hours and gaps everywhere else. Instead
    the lead *closest* to `lead_hour` is kept per target hour, which stays
    within +/-12h of the request at every hour and is how this reissue-daily
    product is actually meant to be read: the freshest available forecast for
    that hour, not a single fixed horizon repeated hourly.
    """
    import datetime as _dt

    def parse_ts(raw):
        raw = raw.strip()
        for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
            try:
                ts = _dt.datetime.strptime(raw, fmt)
                return ts.replace(tzinfo=None) if ts.tzinfo is None \
                    else ts.astimezone(_dt.timezone.utc).replace(tzinfo=None)
            except ValueError:
                continue
        raise SystemExit(f"unparseable timestamp {raw!r} in a forecast file under {src}")

    suffixes = (
        "_carbon_from_src_forecasts_lifecycle_Jul_Dec_2021.csv",
        "_carbon_from_src_prod_forecasts_lifecycle_Jul_Dec_2021.csv",
    )

    lookup_by_region = {}
    for region in regions:
        path = next((src / f"{region}{suf}" for suf in suffixes
                     if (src / f"{region}{suf}").exists()), None)
        if path is None:
            raise SystemExit(
                f"missing forecast file for {region} in {src}\n"
                f"Expected one of: " + ", ".join(f"{region}{s}" for s in suffixes) + "\n"
                f"Download CarbonCast's data/ directory (carbonfirst/CarbonCast on GitHub) "
                f"into {src}.")

        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            need = {"UTC time", "forecasted_carbon_intensity_lifecycle"}
            if not need.issubset(reader.fieldnames or ()):
                raise SystemExit(f"{path} lacks {need} columns")
            raw_rows = [(parse_ts(r["UTC time"]), r["forecasted_carbon_intensity_lifecycle"])
                        for r in reader
                        if r["forecasted_carbon_intensity_lifecycle"] not in ("", None)]

        by_target_ts = {}   # target timestamp -> {lead_hour: forecast value}
        block_start = None
        prev_ts = None
        pos_in_block = 0
        for ts, val in raw_rows:
            if prev_ts is None or (ts - prev_ts).total_seconds() != 3600:
                if block_start is not None and pos_in_block != 96:
                    raise SystemExit(
                        f"{path}: issue block starting {block_start} has "
                        f"{pos_in_block} rows, expected 96 -- this file does not "
                        f"match CarbonCast's daily 96h-ahead forecast format.")
                block_start = ts
                pos_in_block = 0
            pos_in_block += 1
            by_target_ts.setdefault(ts, {})[pos_in_block] = float(val)
            prev_ts = ts

        # Per target hour, keep the forecast whose lead is nearest lead_hour
        # (ties broken toward the shorter lead). See the docstring note above
        # on why an exact match is not available at every hour.
        lookup_by_region[region] = {
            ts: min(leads.items(), key=lambda kv: (abs(kv[0] - lead_hour), kv[0]))[1]
            for ts, leads in by_target_ts.items()}

    out = []
    for region in regions:
        lookup = lookup_by_region[region]
        for h in range(hours):
            ts = base + _dt.timedelta(hours=h)
            ci = lookup.get(ts)
            if ci is None:
                raise SystemExit(
                    f"{region} has no forecast of any lead at {ts}. CarbonCast's "
                    f"forecast files only cover Jul-Dec 2021 -- narrow "
                    f"--start-hour/--hours to fall inside that window.")
            out.append((h, region, round(ci, 2)))
    out.sort(key=lambda r: (r[0], r[1]))
    print(f"forecast window: {base} + {hours}h, lead_hour={lead_hour}")
    return out


# Top-bucket substitutes for the Azure V2 trace. The dataset encodes its
# highest core and memory buckets as ">24" and ">64"; these are the values used
# in the dataset's own published analysis. Both are lower bounds on the true
# size of the machines in those buckets.
TOP_CORE_BUCKET = 30
TOP_MEMORY_BUCKET = 70


def parse_bucket(raw, top):
    """
    Read a bucketed core or memory field.

    Accepts a plain number, or the ">N" form the top bucket uses, for which the
    substitute value is returned. Anything else raises, so that an unexpected
    column order surfaces as a reported count rather than as silently missing
    rows.
    """
    raw = raw.strip()
    if raw.startswith(">"):
        return top
    return float(raw)


def load_azure(path, count, hours, margin, seed, min_duration_h, category):
    """
    Sample VM requests from the Azure Resource Central vmtable.

    Columns (V2, 2019 trace, 2,695,549 rows): vmid, subscriptionid, deploymentid,
    vmcreated, vmdeleted, maxcpu, avgcpu, p95maxcpu, vmcategory,
    vmcorecountbucket, vmmemorybucket. Timestamps are seconds from trace start
    and run to 2,591,400 = 720h, so the trace spans exactly the same 30-day
    window the harness simulates.

    The core and memory fields are BUCKETS, not exact values. The top buckets
    are encoded as the strings ">24" and ">64". They are mapped to 30 and 70
    respectively, following the convention in the dataset's own analysis
    notebook. Any virtual machine in a top bucket is therefore at least that
    large, so fleet sizing derived from these values is a lower bound.

    Reservoir sampling, not head -n: the file is not shuffled, so taking the
    first N rows would sample whatever ordering Azure wrote it in.

    Right-censored VMs -- those still alive at the end of the trace -- are
    dropped, because their duration is unknown and treating the truncation point
    as a completion would silently shorten exactly the long-running VMs that
    carbon-aware scheduling is supposed to target.
    """
    import random as _random

    rng = _random.Random(seed)
    horizon_s = hours * 3600
    trace_end = 2_591_400

    kept, seen, censored, too_short, out_of_window, wrong_cat = [], 0, 0, 0, 0, 0
    unparsed = 0
    cats = {}

    with path.open(newline="") as fh:
        # Header detection by content, not by name. The raw V2 download has no
        # header row at all, while a file to which one has been added may use
        # either the dataset's own field names or renamed equivalents. Testing
        # whether the timestamp column parses as an integer works for all three.
        first = fh.readline()
        parts = first.split(",")
        is_header = True
        if len(parts) >= 5:
            try:
                int(parts[3]); int(parts[4])
                is_header = False
            except ValueError:
                is_header = True
        if not is_header:
            fh.seek(0)
        for row in csv.reader(fh):
            if len(row) < 11:
                continue
            seen += 1
            try:
                created, deleted = int(row[3]), int(row[4])
                cores = parse_bucket(row[9], TOP_CORE_BUCKET)
                mem_gb = parse_bucket(row[10], TOP_MEMORY_BUCKET)
            except ValueError:
                # Counted rather than skipped silently. An earlier version of
                # this loader swallowed these, which discarded every virtual
                # machine in a top bucket without recording that it had done so.
                unparsed += 1
                continue
            cat = row[8]
            cats[cat] = cats.get(cat, 0) + 1

            if deleted >= trace_end:
                censored += 1
                continue
            if category and cat != category:
                wrong_cat += 1
                continue
            duration_h = max(1, round((deleted - created) / 3600))
            if duration_h < min_duration_h:
                too_short += 1
                continue
            arrival_h = created // 3600
            if arrival_h + duration_h > hours:
                out_of_window += 1
                continue

            rec = (arrival_h, duration_h, max(1, int(round(cores))),
                   int(round(mem_gb * 1024)))
            # Reservoir sampling keeps memory bounded regardless of file size.
            if len(kept) < count:
                kept.append(rec)
            else:
                j = rng.randrange(len(kept) + 1)
                if j < count:
                    kept[j] = rec

    if len(kept) < count:
        raise SystemExit(
            f"only {len(kept)} of {count} requested VMs survived filtering "
            f"(censored={censored}, too_short={too_short}, "
            f"outside_window={out_of_window}, wrong_category={wrong_cat}). "
            f"Relax --min-duration-h or --vm-category, or lower --requests.")

    kept.sort()
    rows = []
    for i, (arrival, duration, pes, ram) in enumerate(kept):
        deadline = min(hours, arrival + duration + margin)
        rows.append((i, pes, ram, duration, arrival, max(deadline, arrival + duration)))

    print(f"azure: scanned {seen} rows, sampled {len(rows)}")
    print(f"  dropped: censored={censored} too_short={too_short} "
          f"outside_window={out_of_window} wrong_category={wrong_cat} "
          f"unparsable={unparsed}")
    if unparsed:
        print(f"  WARNING: {unparsed} rows had unreadable core or memory fields "
              f"and were dropped. Check the column order against the dataset "
              f"schema before trusting this sample.")
    print(f"  categories in trace: "
          + ", ".join(f"{k}={v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))
    return rows


def write_meta(path, meta):
    """
    Record how the trace was generated, next to the trace itself.

    Nothing inside carbon_intensity.csv distinguishes a January window from a
    July one, or real data from synthetic. The harness reads this file and copies
    the fields into every results row and into the results filename, so that two
    result sets from different windows cannot be confused once they are out of
    the terminal.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        for k in sorted(meta):
            fh.write(f"{k}={meta[k]}\n")
    print(f"wrote trace metadata -> {path}")


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    print(f"wrote {len(rows):>6} rows -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["synthetic", "real"], default="synthetic")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--hours", type=int, default=24 * 30,
                    help="simulated horizon (default: 30 days)")
    ap.add_argument("--requests", type=int, default=1000)
    ap.add_argument("--deadline-margin", type=int, default=24,
                    help="slack hours beyond duration (Zanotto sweeps 6/12/24/48)")
    ap.add_argument("--regions", nargs="+",
                    default=["IT-NO", "GB", "DE", "FR", "US-TEX-ERCO",
                             "US-CAL-CISO", "US-MIDA-PJM", "AU-NSW", "JP-KN", "KR"],
                    help="default is Zanotto et al.'s 'subset' policy region set")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--carbon-src", type=Path, default=Path("data/carboncast"),
                    help="directory of CarbonCast <REGION>_lifecycle_emissions.csv files")
    ap.add_argument("--start-hour", type=int, default=0,
                    help="offset into the CarbonCast window. 2020-2021 gives 17544h, "
                         "so a 720h experiment has ~24 non-overlapping start points: "
                         "re-running across them is a seasonal robustness check")
    ap.add_argument("--vm-src", type=Path,
                    help="Azure Resource Central vmtable.csv; if given, VM requests "
                         "are sampled from it instead of generated")
    ap.add_argument("--min-duration-h", type=int, default=1,
                    help="drop VMs shorter than this. Most Azure VMs live minutes; "
                         "deferral is only meaningful for longer-lived ones")
    ap.add_argument("--vm-category", choices=["Delay-insensitive", "Interactive", "Unknown"],
                    help="restrict to one Azure workload class")
    ap.add_argument("--region-set", choices=sorted(REAL_REGION_SETS),
                    help="named CarbonCast region set (overrides --regions)")
    ap.add_argument("--forecast", action="store_true",
                    help="also write carbon_intensity_forecast.csv (Paper B's "
                         "realistic-foresight arm), from CarbonCast's paired "
                         "actual/forecast files. Requires --mode real; the "
                         "requested window must fall inside Jul-Dec 2021.")
    ap.add_argument("--lead-hour", type=int, default=24,
                    help="forecast lead time to use with --forecast, in hours "
                         "(1-96; CarbonCast reissues a 96h-ahead forecast "
                         "daily). Default 24h matches the deadline-margin "
                         "framing used elsewhere in this harness.")
    args = ap.parse_args()

    if args.region_set:
        args.regions = REAL_REGION_SETS[args.region_set]

    if args.mode == "real":
        unknown = set(args.regions) - set(CARBONCAST_REGIONS)
        if unknown:
            raise SystemExit(
                f"Not CarbonCast regions: {sorted(unknown)}\n"
                f"Available: {sorted(CARBONCAST_REGIONS)}\n"
                f"Named sets: {sorted(REAL_REGION_SETS)}")
        ci_rows, ci_base = load_carboncast(args.carbon_src, args.regions,
                                           args.hours, args.start_hour)
    else:
        if args.forecast:
            raise SystemExit("--forecast requires --mode real")
        unknown = set(args.regions) - set(REGION_PROFILES)
        if unknown:
            raise SystemExit(f"No profile for region(s): {sorted(unknown)}")
        ci_rows = synth_carbon(args.regions, args.hours, args.seed)
        ci_base = None
    write_csv(args.out / "carbon_intensity.csv",
              ["hour", "region", "ci_gco2_per_kwh"], ci_rows)

    # Paper B's realistic-foresight arm: a second trace, indexed to the
    # identical wall-clock hours as carbon_intensity.csv (ci_base), carrying
    # CarbonCast's own lead_hour-ahead forecast instead of the observed value.
    # Written only on request -- Paper A's pipeline neither needs nor should
    # depend on these files being present.
    if args.forecast:
        fc_rows = load_carboncast_forecast(args.carbon_src, args.regions,
                                           args.hours, ci_base, args.lead_hour)
        write_csv(args.out / "carbon_intensity_forecast.csv",
                  ["hour", "region", "ci_gco2_per_kwh"], fc_rows)

    # VM requests remain synthetic even in --mode real: the Azure Resource
    # Central conversion is a separate step. Any run mixing real CI with these
    # requests must say so -- the workload shape (6-24h uniform durations, sizes
    # drawn independently of arrival time) is an assumption, not an observation.
    if args.vm_src:
        req_rows = load_azure(args.vm_src, args.requests, args.hours,
                              args.deadline_margin, args.seed,
                              args.min_duration_h, args.vm_category)
    else:
        if args.mode == "real":
            print("NOTE: carbon intensity is real; VM requests are still synthetic.")
        req_rows = synth_requests(args.requests, args.hours, args.seed, args.deadline_margin)
    write_csv(args.out / "vm_requests.csv",
              ["id", "pes", "ram_mb", "duration_h", "arrival_h", "deadline_h"], req_rows)

    write_meta(args.out / "trace_meta.properties", {
        "trace_mode": args.mode,
        "start_hour": args.start_hour if args.mode == "real" else 0,
        "region_set": args.region_set or "custom",
        "regions": "|".join(args.regions),
        "hours": args.hours,
        "requests": args.requests,
        "gen_deadline_margin_h": args.deadline_margin,
        "seed": args.seed,
        "vm_source": str(args.vm_src) if args.vm_src else "synthetic",
        "vm_category": args.vm_category or "all",
        "min_duration_h": args.min_duration_h,
        "has_forecast": args.forecast,
        "forecast_lead_h": args.lead_hour if args.forecast else "n/a",
    })

    # Sanity summary: if the spread across regions is small, no space-shifting
    # policy can win, and the experiment is uninformative by construction.
    by_region = {}
    for _, region, ci in ci_rows:
        by_region.setdefault(region, []).append(ci)
    means = {r: sum(v) / len(v) for r, v in by_region.items()}
    lo, hi = min(means.values()), max(means.values())
    print(f"\nmean CI spread across regions: {lo:.0f} - {hi:.0f} gCO2eq/kWh "
          f"({hi / lo:.1f}x)")
    amp = {r: (max(v) - min(v)) for r, v in by_region.items()}
    print(f"within-region swing: {min(amp.values()):.0f} - {max(amp.values()):.0f} gCO2eq/kWh")

    # Repeatable diurnal amplitude: the spread of hour-of-day means.
    #
    # Window max-min is a poor proxy for what a deadline-bounded deferral can
    # exploit, because it is dominated by one-off noise excursions -- it scores
    # synthetic and real DE as near-identical (110% vs 116%) when their
    # exploitable structure differs by 4x. Averaging over hour-of-day cancels the
    # noise and leaves only the cycle a time-shifting policy can actually plan
    # against. Real grids sit at 2-15%; the synthetic generator produces 40-85%,
    # which is why synthetic traces flatter time-shifting.
    hod = {}
    for hour, region, ci in ci_rows:
        hod.setdefault(region, {}).setdefault(hour % 24, []).append(ci)
    diurnal = {}
    for region, buckets in hod.items():
        prof = [sum(v) / len(v) for v in buckets.values()]
        diurnal[region] = max(prof) - min(prof)

    # Per-region detail. Space-shifting gains come from the spread of the *mean*
    # column; time-shifting gains come from the *swing* column. Printing both
    # makes it visible which family of policy the chosen region set can favour
    # before any simulation is run.
    print(f"\n{'region':10s} {'mean':>7s} {'swing':>7s} {'swing%':>7s} {'diurnal%':>9s}")
    for r in sorted(means, key=means.get):
        print(f"{r:10s} {means[r]:7.0f} {amp[r]:7.0f} "
              f"{100 * amp[r] / means[r]:7.1f} {100 * diurnal[r] / means[r]:9.1f}")
    print("\nspace-shifting exploits the spread of the mean column; "
          "time-shifting exploits diurnal%.")


if __name__ == "__main__":
    main()