import org.carbonaware.core.Model.Placement;
import org.carbonaware.core.Model.VmRequest;
import org.carbonaware.core.RegionLedger;
import org.carbonaware.policy.Planners;
import org.carbonaware.policy.Planners.Planner;
import org.carbonaware.trace.CarbonTrace;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.function.Function;

/**
 * Standalone check of the two mitigation planners, with no CloudSim dependency.
 *
 * Compile and run from the project root, in about three seconds:
 *
 *   javac -d target/harness ^
 *     src/main/java/org/carbonaware/core/Model.java ^
 *     src/main/java/org/carbonaware/core/RegionLedger.java ^
 *     src/main/java/org/carbonaware/trace/CarbonTrace.java ^
 *     src/main/java/org/carbonaware/policy/Planners.java
 *   javac -cp target/harness -d target/harness tools/MitigationHarness.java
 *   java -cp target/harness MitigationHarness
 *
 * Worth running after any change to Planners or RegionLedger, because it
 * catches a broken degenerate control in seconds rather than after the
 * 80-minute sweep in run_mitigation.bat.
 *
 * It does three things, in order of how much they are worth:
 *   1. asserts the degenerate controls (price lambda=0 must equal space+time);
 *   2. reproduces the deadline-margin non-monotonicity on a synthetic diurnal
 *      trace, so that the fix is tested against the defect rather than against
 *      nothing;
 *   3. sweeps the mitigation parameters and reports, for each, whether the
 *      non-monotonicity survives and what it cost in realised carbon intensity.
 *
 * The emissions figure here is a proxy, the sum over placed VMs of mean CI
 * times duration, rather than the simulator's attributed basis. It is adequate to detect the
 * phenomenon and must not be quoted as a result.
 */
public final class MitigationHarness {

    static final int HORIZON = 720;
    static final String[] REGIONS = {"R0","R1","R2","R3","R4","R5","R6"};
    static final int TOTAL_CAP = 350;
    static final int N_REQ = 1000;
    static final int[] MARGINS = {12,18,24,25,26,27,28,29,30,36,48};

    public static void main(String[] a) throws IOException {
        final Path csv = Files.createTempFile("ci", ".csv");
        writeTrace(csv);
        final CarbonTrace trace = CarbonTrace.fromCsv(csv);
        final List<String> regions = Arrays.asList(REGIONS);
        final List<VmRequest> base = makeRequests(new Random(1));

        final double demand = base.stream().mapToInt(VmRequest::durationH).sum();
        System.out.printf("synthetic: %d VMs, %.0f VM-hours, mean concurrency %.1f, "
            + "utilisation %.1f%% at cap %d%n%n",
            base.size(), demand, demand / HORIZON, 100 * demand / HORIZON / TOTAL_CAP, TOTAL_CAP);

        final Map<String, Function<CarbonTrace, Planner>> policies = new LinkedHashMap<>();
        policies.put("space+time",   Planners.SpaceTime::new);
        for (double lam : new double[]{0, 0.1, 0.25, 0.5, 1.0, 2.0})
            policies.put(String.format(Locale.ROOT,"price(%.2f)", lam),
                         t -> new Planners.CongestionPriced(t, lam));
        for (double pct : new double[]{0, 5, 20})
            policies.put(String.format(Locale.ROOT,"spread(%.0f%%)", pct),
                         t -> new Planners.NearCleanLeastLoaded(t, pct / 100.0));

        // ---- run everything -------------------------------------------------
        final Map<String, Map<Integer, Run>> all = new LinkedHashMap<>();
        for (var p : policies.entrySet()) {
            final Map<Integer, Run> byMargin = new LinkedHashMap<>();
            for (int m : MARGINS) byMargin.put(m, run(trace, regions, base, m, p.getValue()));
            all.put(p.getKey(), byMargin);
        }

        // ---- 1. degenerate controls ----------------------------------------
        System.out.println("CONTROL CHECKS");
        checkIdentical(all, "space+time", "price(0.00)");
        reportDrift(all, "space+time", "spread(0%)");
        System.out.println();

        // ---- 2 and 3. the sweep --------------------------------------------
        System.out.printf("%-14s %8s %8s %9s %8s %8s%n",
            "policy", "maxV%", "argmax", "realCI", "modal", "pk/mean");
        System.out.println("-".repeat(62));
        for (var e : all.entrySet()) {
            final Map<Integer, Run> r = e.getValue();
            double maxV = Double.NEGATIVE_INFINITY; int argmax = -1;
            double best = Double.MAX_VALUE;
            for (int m : MARGINS) {
                final double v = 100 * (r.get(m).ci() / best - 1);
                if (best != Double.MAX_VALUE && v > maxV) { maxV = v; argmax = m; }
                best = Math.min(best, r.get(m).ci());
            }
            // Report at the margin where the unmitigated policy peaks, so the
            // comparison is at a fixed point on the axis, not each policy's own.
            final Run atPeak = r.get(27);
            System.out.printf("%-14s %+8.2f %8d %9.1f %8.3f %8.3f%n",
                e.getKey(), maxV, argmax, atPeak.ci(), atPeak.modal, atPeak.pkMean);
        }
        System.out.println("\nmaxV is the largest excess over the best SHORTER margin, as in the paper.");
        System.out.println("realCI, modal and pk/mean are read at margin 27 for every policy.");
        Files.deleteIfExists(csv);
    }

    record Run(double gco2, double vmHours, double modal, double pkMean, List<Placement> pl) {
        double ci() { return gco2 / vmHours; }
    }

    static Run run(CarbonTrace trace, List<String> regions, List<VmRequest> base,
                   int margin, Function<CarbonTrace, Planner> factory) {
        final Map<String,Integer> cap = new LinkedHashMap<>();
        final int per = TOTAL_CAP / REGIONS.length;
        for (String r : REGIONS) cap.put(r, per);
        final RegionLedger ledger = new RegionLedger(cap, HORIZON);
        final Planner planner = factory.apply(trace);
        double g = 0, vmh = 0;
        final List<Placement> placements = new ArrayList<>();
        for (VmRequest r0 : base) {
            final int dl = Math.min(HORIZON, r0.arrivalH() + r0.durationH() + margin);
            final VmRequest r = new VmRequest(r0.id(), r0.pes(), r0.ramMb(), r0.durationH(),
                r0.arrivalH(), Math.max(dl, r0.arrivalH() + r0.durationH()));
            final Placement p = planner.plan(r, regions, ledger);
            ledger.place(p.region(), p.startHour(), r.durationH());
            placements.add(p);
            g += trace.meanCiOverWindow(p.region(), p.startHour(), r.durationH()) * r.durationH();
            vmh += r.durationH();
        }
        return new Run(g, vmh, ledger.modalStartHourShare(), ledger.peakToMeanOccupancy(), placements);
    }

    static void checkIdentical(Map<String,Map<Integer,Run>> all, String a, String b) {
        int diffs = 0;
        for (int m : MARGINS) {
            final List<Placement> pa = all.get(a).get(m).pl(), pb = all.get(b).get(m).pl();
            for (int i = 0; i < pa.size(); i++) if (!pa.get(i).equals(pb.get(i))) diffs++;
        }
        System.out.printf("  %-14s vs %-14s : %s (%d of %d placements differ)%n",
            a, b, diffs == 0 ? "IDENTICAL: control passes" : "*** MISMATCH ***",
            diffs, MARGINS.length * N_REQ);
        if (diffs != 0) throw new AssertionError("lambda=0 must reproduce space+time exactly");
    }

    static void reportDrift(Map<String,Map<Integer,Run>> all, String a, String b) {
        int diffs = 0;
        for (int m : MARGINS) {
            final List<Placement> pa = all.get(a).get(m).pl(), pb = all.get(b).get(m).pl();
            for (int i = 0; i < pa.size(); i++) if (!pa.get(i).equals(pb.get(i))) diffs++;
        }
        System.out.printf("  %-14s vs %-14s : %d of %d placements differ "
            + "(tie-breaking only; exact identity is not expected)%n",
            a, b, diffs, MARGINS.length * N_REQ);
    }

    /** Diurnal trace: region-specific phase and amplitude, as in a spread of time zones. */
    static void writeTrace(Path p) throws IOException {
        final List<String> out = new ArrayList<>();
        out.add("hour,region,ci_gco2_per_kwh");
        final double[] baseCi = {180, 250, 420, 300, 330, 390, 360};
        final double[] amp    = {0.35, 0.30, 0.22, 0.28, 0.25, 0.18, 0.26};
        final double[] phase  = {3, 6, 9, 1, 11, 7, 4};
        for (int h = 0; h < HORIZON; h++)
            for (int r = 0; r < REGIONS.length; r++)
                out.add(String.format(Locale.ROOT, "%d,%s,%.3f", h, REGIONS[r],
                    baseCi[r] * (1 + amp[r] * Math.sin(2 * Math.PI * (h - phase[r]) / 24.0))));
        Files.write(p, out);
    }

    /** Median about 8 h, mean about 30 h: the Azure duration shape used in the paper. */
    static List<VmRequest> makeRequests(Random rnd) {
        final List<VmRequest> out = new ArrayList<>();
        for (int i = 0; i < N_REQ; i++) {
            final int dur = (int) Math.max(2, Math.min(240,
                Math.round(Math.exp(2.079 + 1.626 * rnd.nextGaussian()))));
            final int arr = rnd.nextInt(Math.max(1, HORIZON - dur - 60));
            out.add(new VmRequest(i, 2, 4096, dur, arr, arr + dur));
        }
        return out;
    }
}