package org.carbonaware;

import org.carbonaware.core.Metrics;
import org.carbonaware.core.Model.VmRequest;
import org.carbonaware.policy.Planners;
import org.carbonaware.policy.Planners.Planner;
import org.carbonaware.trace.CarbonTrace;
import org.carbonaware.trace.RequestTrace;

import ch.qos.logback.classic.Level;
import org.cloudsimplus.util.Log;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.*;
import java.util.function.Function;

/**
 * Sweeps (policy x region-set x total capacity x deadline margin) and writes
 * one
 * CSV row per cell.
 *
 * <p>
 * <b>Changed:</b> the capacity axis is now <em>total</em> concurrent slots,
 * divided evenly across the eligible regions. Sweeping a per-region cap made
 * the
 * total capacity depend on the size of the region set, so a policy confined to
 * one
 * region competed against policies with |J| times more hardware and the
 * comparison
 * measured fleet size as much as scheduling.
 */
public final class ExperimentRunner {

    /**
     * Region sets after Zanotto et al.'s policy scenarios.
     *
     * <p>
     * Built with explicit puts: {@code Map.of} does not preserve insertion
     * order, so wrapping it in a LinkedHashMap gave a JVM-arbitrary iteration
     * order and a non-reproducible row ordering in the output CSV.
     */
    private static final Map<String, List<String>> REGION_SETS = new LinkedHashMap<>();
    static {
        // CarbonCast region codes, matching scripts/prep_traces.py REAL_REGION_SETS.
        // These are NOT the Electricity Maps codes used during synthetic-trace
        // development: "SE-SE3" does not match CarbonCast's "SE", and FR, GB,
        // IT-NO, JP-KN, KR and AU-NSW have no CarbonCast equivalent at all.
        REGION_SETS.put("eu", List.of("SE", "ES", "DE", "NL", "PL"));
        REGION_SETS.put("us", List.of("BPAT", "CISO", "ERCO", "ISNE", "NYISO", "PJM", "FPL"));
        REGION_SETS.put("world", List.of("SE", "ES", "DE", "NL", "PL",
                "CISO", "ERCO", "PJM", "AUS_QLD"));
    }
    /**
     * Total concurrent VM slots across the whole region set.
     *
     * <p>
     * Demand for the shipped trace is ~15,100 VM-hours over 720 h, i.e. a mean
     * of 21 concurrent VMs. The sweep therefore spans genuinely tight (24) to
     * comfortably slack (200); below about 21 no policy can keep up and every row
     * degenerates into deadline overrun.
     */
    private static final int[] DEFAULT_TOTAL_CAPACITY = { 24, 40, 80, 200 };
    private static final int[] DEFAULT_MARGINS = { 6, 12, 24, 48 };

    /**
     * Sweep axes, overridable so a small instance can be run first.
     *
     * <p>
     * A full sweep is 128 cells of 1000 VMs and takes ~16 minutes. Nearly every
     * fault found so far (VM creation, region mapping, the accounting basis) shows
     * up identically in a single cell, so the sweep should not be the thing under
     * test while the harness still is. Override with system properties, e.g.
     * {@code -Dsweep.caps=80 -Dsweep.margins=24 -Dsweep.regionSets=eu}.
     */
    private static int[] ints(final String prop, final int[] fallback) {
        final String v = System.getProperty(prop);
        if (v == null || v.isBlank())
            return fallback;
        return Arrays.stream(v.split(",")).map(String::trim).filter(x -> !x.isEmpty())
                .mapToInt(Integer::parseInt).toArray();
    }

    private static final int[] TOTAL_CAPACITY = ints("sweep.caps", DEFAULT_TOTAL_CAPACITY);
    private static final int[] MARGINS = ints("sweep.margins", DEFAULT_MARGINS);

    public static void main(final String[] args) throws IOException {
        Log.setLevel(Level.OFF);

        final Path dataDir = Path.of(args.length > 0 ? args[0] : "data");

        // Trace provenance, written by scripts/prep_traces.py alongside the trace.
        // Carried into both the results filename and every results row: nothing in
        // carbon_intensity.csv distinguishes a January window from a July one, or
        // real data from synthetic, so without this two result sets from different
        // windows are indistinguishable once they leave the terminal.
        final Properties meta = new Properties();
        final Path metaPath = dataDir.resolve("trace_meta.properties");
        if (Files.exists(metaPath)) {
            try (var in = Files.newInputStream(metaPath)) {
                meta.load(in);
            }
        } else {
            System.out.printf("no %s found; results will be labelled 'unknown'%n", metaPath);
        }
        final String traceMode = meta.getProperty("trace_mode", "unknown");
        final String startHour = meta.getProperty("start_hour", "0");
        final String traceRegionSet = meta.getProperty("region_set", "unknown");

        final String only0 = System.getProperty("sweep.regionSets", "");

        // -Dsweep.homeRegion=all runs WaitAwhile once per region in the set.
        // Adds |J|-1 cells per configuration, so it is off by default.
        final boolean sweepHome = "all".equalsIgnoreCase(
                System.getProperty("sweep.homeRegion", ""));
        final Path outCsv = args.length > 1 ? Path.of(args[1])
                : Path.of(String.format("results/results_%s_%s_h%s.csv",
                        only0.isBlank() ? traceRegionSet : only0.replace(",", "-"),
                        traceMode, startHour));

        final CarbonTrace ci = CarbonTrace.fromCsv(dataDir.resolve("carbon_intensity.csv"));
        final List<VmRequest> base = RequestTrace.fromCsv(dataDir.resolve("vm_requests.csv"));

        // Paper B's realistic-foresight arm: planning against CarbonCast's own
        // forecast instead of the ground truth. Optional -- Paper A's pipeline
        // (and any run without scripts/prep_traces.py --forecast) has no use
        // for it and must keep working without the file present.
        final Path forecastPath = dataDir.resolve("carbon_intensity_forecast.csv");
        final CarbonTrace forecast = Files.exists(forecastPath)
                ? CarbonTrace.fromCsv(forecastPath)
                : null;
        if (forecast != null && forecast.horizonHours() != ci.horizonHours()) {
            throw new IllegalStateException(String.format(
                    "carbon_intensity_forecast.csv horizon (%dh) does not match "
                            + "carbon_intensity.csv (%dh); they must describe the same "
                            + "wall-clock window. Regenerate both together with "
                            + "scripts/prep_traces.py --forecast.",
                    forecast.horizonHours(), ci.horizonHours()));
        }
        // -Dsweep.foresight=perfect|forecast|both restricts which arm(s) run.
        // "both" (or omitted, when a forecast trace is present) runs the
        // perfect-foresight arm Paper A already used plus the forecast arm.
        // Without a forecast trace only "perfect" is possible, regardless of
        // this property.
        final String foresightArg = System.getProperty("sweep.foresight", "both");
        final List<String> foresightArms = new ArrayList<>();
        if (forecast == null) {
            if ("forecast".equalsIgnoreCase(foresightArg)) {
                throw new IllegalStateException(
                        "sweep.foresight=forecast requested but " + forecastPath
                                + " does not exist. Generate it with "
                                + "scripts/prep_traces.py --forecast.");
            }
            foresightArms.add("perfect");
        } else if ("perfect".equalsIgnoreCase(foresightArg)) {
            foresightArms.add("perfect");
        } else if ("forecast".equalsIgnoreCase(foresightArg)) {
            foresightArms.add("forecast");
        } else {
            foresightArms.add("perfect");
            foresightArms.add("forecast");
        }

        final double demandVmHours = base.stream().mapToInt(VmRequest::durationH).sum();
        System.out.printf("regions=%d  horizon=%dh  requests=%d  CI basis=%s%n",
                ci.regions().size(), ci.horizonHours(), base.size(),
                ci.isMarginal() ? "marginal" : "average");
        System.out.printf("demand=%.0f VM-hours  mean concurrency=%.1f%n",
                demandVmHours, demandVmHours / ci.horizonHours());

        final List<Metrics> results = new ArrayList<>();

        // Restrict the region sets when running a small instance.
        final String only = only0;
        final Set<String> wanted = only.isBlank() ? Set.of()
                : new LinkedHashSet<>(Arrays.asList(only.split(",")));

        System.out.printf("sweep: caps=%s margins=%s regionSets=%s foresight=%s -> %d cells%n",
                Arrays.toString(TOTAL_CAPACITY), Arrays.toString(MARGINS),
                wanted.isEmpty() ? "all" : wanted, foresightArms,
                TOTAL_CAPACITY.length * MARGINS.length * 4
                        * (wanted.isEmpty() ? REGION_SETS.size() : wanted.size())
                        * foresightArms.size());

        for (final var rs : REGION_SETS.entrySet()) {
            if (!wanted.isEmpty() && !wanted.contains(rs.getKey()))
                continue;
            final List<String> regions = new ArrayList<>(rs.getValue());
            regions.retainAll(ci.regions());
            // Silent shrinkage is worse than failure: a set that quietly loses
            // its cleanest region still runs and still reports savings, just
            // smaller ones, with nothing in the output to say why.
            if (regions.size() < rs.getValue().size()) {
                final List<String> missing = new ArrayList<>(rs.getValue());
                missing.removeAll(regions);
                throw new IllegalStateException(String.format(
                        "region set '%s' expects %d regions but the trace is missing %s. "
                                + "Regenerate the trace with a matching --region-set, or fix REGION_SETS.",
                        rs.getKey(), rs.getValue().size(), missing));
            }
            final String home = regions.get(0);

            // Fleet size is held constant across the whole sweep, derived from the
            // largest cap that will be run. Sizing per cell made total emissions a
            // function of the capacity axis rather than of scheduling.
            // Deliberately the full default sweep, not the (possibly reduced)
            // selection: a smoke run must provision the same fleet as the full run
            // or its emissions are not comparable with it.
            final int maxTotal = Arrays.stream(DEFAULT_TOTAL_CAPACITY).max().orElse(0);
            final int fleetCapPerRegion = Math.max(1, maxTotal / regions.size());

            for (final int margin : MARGINS) {
                final List<VmRequest> reqs = withMargin(base, margin, ci.horizonHours());

                for (final int totalCap : TOTAL_CAPACITY) {
                    // Even split. Every policy therefore sees the same total number
                    // of concurrent slots; a policy that declines to move between
                    // regions pays for that in deferral and deadline overrun, not
                    // by being handed less hardware.
                    final int perRegion = Math.max(1, totalCap / regions.size());
                    final Map<String, Integer> capByRegion = new LinkedHashMap<>();
                    regions.forEach(r -> capByRegion.put(r, perRegion));

                    // WaitAwhile is pinned to a home region, so its result is a
                    // statement about that region as much as about deferral. With
                    // home fixed to regions.get(0) it drew SE for the eu set and
                    // BPAT for us -- in both cases the cleanest grid available,
                    // which handed the time-shifting policy the optimum for free
                    // and made every space-vs-time comparison uninterpretable.
                    // Sweeping the home region reports the whole distribution
                    // instead of one arbitrary draw.
                    final List<Function<CarbonTrace, Planner>> factories = new ArrayList<>(List.of(
                            Planners.RoundRobin::new,
                            Planners.GreedySpace::new,
                            Planners.SpaceTime::new));
                    if (sweepHome) {
                        for (final String h : regions) {
                            factories.add(t -> new Planners.WaitAwhile(t, h));
                        }
                    } else {
                        factories.add(t -> new Planners.WaitAwhile(t, home));
                    }

                    for (final var f : factories) {
                        for (final String arm : foresightArms) {
                            // The trace a planner sees is what makes this
                            // perfect-foresight vs forecast: "perfect" plans
                            // against the ground truth (Paper A's original
                            // setup, capacity/deadline effects not confounded
                            // with prediction quality); "forecast" plans
                            // against CarbonCast's own forecast trace instead.
                            // Emissions are always measured against the ground
                            // truth in Simulator.run regardless of this choice --
                            // only the planning input changes.
                            final CarbonTrace planningTrace = "forecast".equals(arm) ? forecast : ci;
                            final Planner planner = f.apply(planningTrace);
                            final Metrics m = Simulator.run(
                                    ci, reqs, planner, arm, regions, rs.getKey(), capByRegion,
                                    fleetCapPerRegion, margin);
                            results.add(m);
                            System.out.printf(
                                    "  %-12s %-9s %-7s cap=%-3d/%-2d margin=%-3d  tot=%8.2f  attr=%8.2f  dyn=%7.2f kgCO2"
                                            + "  viol=%3.0f%%  conc=%3.0f%%  peak=%-3d hosts=%-3d%s%n",
                                    m.policy(), m.foresight(), m.regionSet(), m.totalCapacity(), m.perRegionCap(),
                                    margin,
                                    m.totalGrams() / 1000.0, m.attributedGrams() / 1000.0, m.dynamicGrams() / 1000.0,
                                    100 * m.violationRate(), 100 * m.maxRegionShare(),
                                    m.peakConcurrency(), m.hostsPerRegion(),
                                    m.placementValid() ? ""
                                            : String.format("  !! %d misplaced, %d uncreated, %d/%d finished",
                                                    m.misplacedVms(), m.uncreatedVms(),
                                                    m.cloudletsFinished(), m.requests()));
                        }
                    }
                }
            }
        }

        Files.createDirectories(outCsv.getParent());
        final List<String> lines = new ArrayList<>();
        lines.add("trace_mode,start_hour," + Metrics.csvHeader());
        final String provenance = traceMode + "," + startHour + ",";
        results.forEach(m -> lines.add(provenance + m.toCsvRow()));
        Files.write(outCsv, lines);
        System.out.printf("%nwrote %d rows -> %s%n", results.size(), outCsv);

        // A single invalid row invalidates the comparison it belongs to.
        final long bad = results.stream().filter(m -> !m.placementValid()).count();
        if (bad > 0) {
            System.out.printf("%nWARNING: %d of %d rows failed the placement check.%n", bad, results.size());
            System.out.println("Those rows do not describe the schedule under test. Diagnose before use:");
            System.out.println("  uncreated > 0        -> fleet too small; raise HOST_HEADROOM in Simulator");
            System.out.println("  misplaced > 0        -> CloudSim Plus rerouted to a fallback datacenter");
            System.out.println("  finished < requests  -> work ran past the measurement window");
        } else {
            System.out.println("placement check passed: every VM ran in its planned region and completed");
        }

        // The total basis is policy-invariant by construction -- the same fleet is
        // powered in the same regions regardless of placement -- so if the spread
        // across policies is near zero, that is expected rather than a bug, and
        // the attributed basis is the one carrying the signal.
        reportSpread(results, "total", Metrics::gramsPerVmHour);
        reportSpread(results, "attributed", Metrics::attributedGramsPerVmHour);
        reportSpread(results, "dynamic", Metrics::dynamicGramsPerVmHour);
    }

    private static void reportSpread(final List<Metrics> results, final String basis,
            final Function<Metrics, Double> value) {
        final Map<String, List<Double>> byCell = new LinkedHashMap<>();
        for (final Metrics m : results) {
            // foresight included in the key: pooling a perfect-foresight cell
            // with its forecast-foresight counterpart would read the cost of
            // imperfect foresight as policy spread within one comparison.
            final String cell = m.foresight() + "|" + m.regionSet() + "|"
                    + m.totalCapacity() + "|" + m.deadlineMarginH();
            byCell.computeIfAbsent(cell, k -> new ArrayList<>()).add(value.apply(m));
        }
        double worst = 0;
        for (final List<Double> v : byCell.values()) {
            final double lo = v.stream().mapToDouble(Double::doubleValue).min().orElse(0);
            final double hi = v.stream().mapToDouble(Double::doubleValue).max().orElse(0);
            if (lo > 0)
                worst = Math.max(worst, (hi - lo) / lo);
        }
        System.out.printf("max spread across policies within a cell, %s basis: %.1f%%%n",
                basis, 100 * worst);
    }

    /** Rebuilds requests with a given deadline margin beyond duration. */
    private static List<VmRequest> withMargin(final List<VmRequest> base, final int marginH, final int horizonH) {
        final List<VmRequest> out = new ArrayList<>(base.size());
        for (final VmRequest r : base) {
            final int deadline = Math.min(horizonH, r.arrivalH() + r.durationH() + marginH);
            // Requests too close to the horizon end cannot express the full margin;
            // they are kept with a truncated one rather than dropped, so the
            // request population stays identical across margins.
            out.add(new VmRequest(r.id(), r.pes(), r.ramMb(), r.durationH(),
                    r.arrivalH(), Math.max(deadline, r.arrivalH() + r.durationH())));
        }
        return out;
    }
}