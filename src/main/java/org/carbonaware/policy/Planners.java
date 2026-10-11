package org.carbonaware.policy;

import org.carbonaware.core.Model.Placement;
import org.carbonaware.core.Model.VmRequest;
import org.carbonaware.core.RegionLedger;
import org.carbonaware.trace.CarbonTrace;

import java.util.List;

/**
 * Scheduling policies under comparison.
 *
 * <p>All policies see the same {@link CarbonTrace} and the same {@link RegionLedger},
 * so differences in outcome come from the decision rule alone.
 */
public interface Planners {

    interface Planner {
        String name();
        Placement plan(VmRequest req, List<String> eligibleRegions, RegionLedger ledger);

        /**
         * The region this policy refuses to leave while the deadline is still
         * reachable, or {@code null} if it is free to place anywhere. Reported so
         * the results can state which policies were spatially constrained.
         */
        default String pinnedRegion() { return null; }
    }

    /**
     * Shared fallback: the earliest feasible slot anywhere, ignoring the deadline.
     *
     * <p><b>Changed:</b> this is now always searched over the full eligible region
     * set, including for policies that pin themselves to a home region while the
     * deadline is reachable. Restricting the fallback to the home region made
     * {@code WaitAwhile} structurally infeasible -- with one region's share of
     * capacity it could absorb only a fraction of the workload, so the run threw
     * rather than producing a result. The correct reading of "run to completion
     * once the allowed delay is over" (Wiesner et al.) is run <em>now, wherever</em>;
     * staying home past the deadline is not a defensible interpretation. The cost
     * of leaving still shows up, as deadline overrun and as regional concentration.
     */
    private static Placement fallback(final VmRequest req, final List<String> regions,
                                      final RegionLedger ledger, final CarbonTrace trace) {
        for (int h = req.arrivalH(); h < trace.horizonHours() + RegionLedger.OVERRUN_SLACK_H; h++) {
            String best = null;
            double bestCi = Double.MAX_VALUE;
            for (final String r : regions) {
                if (!ledger.canPlace(r, h, req.durationH())) continue;
                final double ci = trace.meanCiOverWindow(r, h, req.durationH());
                if (ci < bestCi) { bestCi = ci; best = r; }
            }
            if (best != null) {
                final int overrun = Math.max(0, (h + req.durationH()) - req.deadlineH());
                return new Placement(best, h, overrun);
            }
        }
        throw new IllegalStateException(
            "No feasible slot for request " + req.id() + " anywhere, even beyond the deadline. "
            + "Total capacity " + ledger.totalCapacity() + " concurrent VMs is too small for this "
            + "workload; raise the total-capacity sweep or reduce --requests.");
    }

    /**
     * Carbon-agnostic round robin: the baseline used by Zanotto et al. It is a
     * weak comparator by construction, and is included so that the share of any
     * headline percentage attributable to the baseline's weakness is visible.
     */
    final class RoundRobin implements Planner {
        private final CarbonTrace trace;
        private int cursor = 0;

        public RoundRobin(final CarbonTrace trace) { this.trace = trace; }

        @Override public String name() { return "round-robin"; }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            for (int i = 0; i < regions.size(); i++) {
                final String region = regions.get((cursor + i) % regions.size());
                if (ledger.canPlace(region, req.arrivalH(), req.durationH())) {
                    cursor = (cursor + i + 1) % regions.size();
                    return new Placement(region, req.arrivalH());
                }
            }
            return fallback(req, regions, ledger, trace);
        }
    }

    /** Space-shift only: cleanest eligible region at the arrival hour, no deferral. */
    final class GreedySpace implements Planner {
        private final CarbonTrace trace;

        public GreedySpace(final CarbonTrace trace) { this.trace = trace; }

        @Override public String name() { return "space"; }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            String best = null;
            double bestCi = Double.MAX_VALUE;
            for (final String region : regions) {
                if (!ledger.canPlace(region, req.arrivalH(), req.durationH())) continue;
                final double ci = trace.meanCiOverWindow(region, req.arrivalH(), req.durationH());
                if (ci < bestCi) { bestCi = ci; best = region; }
            }
            return best == null ? fallback(req, regions, ledger, trace)
                                : new Placement(best, req.arrivalH());
        }
    }

    /**
     * Time-shift only, after Wiesner et al.'s "Let's Wait Awhile": stay in the
     * origin region, defer to the cleanest feasible start hour within the deadline.
     * Once the deadline is unreachable it releases the region constraint via the
     * shared fallback.
     */
    final class WaitAwhile implements Planner {
        private final CarbonTrace trace;
        private final String homeRegion;

        public WaitAwhile(final CarbonTrace trace, final String homeRegion) {
            this.trace = trace;
            this.homeRegion = homeRegion;
        }

        // The home region is part of the identity of this policy, not a detail:
        // "time" pinned to the cleanest grid and "time" pinned to the dirtiest
        // are different experiments and must not share a row label.
        @Override public String name() { return "time@" + homeRegion; }

        @Override public String pinnedRegion() { return homeRegion; }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            final String region = regions.contains(homeRegion) ? homeRegion : regions.get(0);
            int bestHour = -1;
            double bestCi = Double.MAX_VALUE;
            for (int h = req.arrivalH(); h <= req.latestStartH(); h++) {
                if (!ledger.canPlace(region, h, req.durationH())) continue;
                final double ci = trace.meanCiOverWindow(region, h, req.durationH());
                if (ci < bestCi) { bestCi = ci; bestHour = h; }
            }
            // Full region set on fallback -- see the note on fallback() above.
            return bestHour < 0 ? fallback(req, regions, ledger, trace)
                                : new Placement(region, bestHour);
        }
    }

    /**
     * Combined space and time shift by exhaustive search over (region, start hour).
     *
     * <p>Zanotto et al. solve this as a binary integer program with PuLP/CBC, one
     * VM at a time against a greedily updated occupancy matrix. For a single
     * request the feasible set has |J|x|T| members, so enumeration returns the
     * identical optimum without a solver dependency and in microseconds.
     */
    final class SpaceTime implements Planner {
        private final CarbonTrace trace;

        public SpaceTime(final CarbonTrace trace) { this.trace = trace; }

        @Override public String name() { return "space+time"; }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            String bestRegion = null;
            int bestHour = -1;
            double bestCi = Double.MAX_VALUE;
            for (final String region : regions) {
                for (int h = req.arrivalH(); h <= req.latestStartH(); h++) {
                    if (!ledger.canPlace(region, h, req.durationH())) continue;
                    final double ci = trace.meanCiOverWindow(region, h, req.durationH());
                    if (ci < bestCi) { bestCi = ci; bestRegion = region; bestHour = h; }
                }
            }
            return bestRegion == null ? fallback(req, regions, ledger, trace)
                                      : new Placement(bestRegion, bestHour);
        }
    }

    // =====================================================================
    //  Mitigations for the deadline-margin non-monotonicity
    //
    //  The defect the preceding policies exhibit is an externality. Each
    //  request is placed to minimise its own mean carbon intensity against a
    //  shared occupancy ledger, and the cost it imposes on requests not yet
    //  placed -- consuming scarce capacity in the hours they will also want --
    //  appears nowhere in its objective. Enlarging every request's feasible set
    //  enlarges the overlap between them, so slack increases contention for the
    //  same trough hours rather than relieving it.
    //
    //  Both mitigations below are online: they use only ledger state already
    //  available at decision time and need no knowledge of future arrivals, so
    //  they are drop-in replacements rather than a different problem
    //  formulation. Each has an exact degenerate case that reproduces
    //  SpaceTime, which the analysis uses as a correctness control.
    // =====================================================================

    /**
     * Space and time shift, with the capacity constraint priced instead of
     * merely enforced.
     *
     * <p>Where {@link SpaceTime} minimises mean carbon intensity over the run
     * window, this minimises
     * <pre>
     *   meanCI(r,t,D)  +  lambda * ciRef * meanUtilisation(r,t,D)
     * </pre>
     * where {@code ciRef} is the mean carbon intensity over the eligible regions
     * and the whole horizon. The penalty is <em>additive</em> and expressed in
     * the same units as the thing it competes with, which makes {@code lambda}
     * dimensionless and directly interpretable: at {@code lambda = 0.5}, a
     * fully-occupied window is treated as though its grid were half the average
     * intensity dirtier than it is. A multiplicative penalty was rejected because
     * it would scale the congestion price with how dirty the slot already is,
     * and the cost of denying an hour to later requests does not depend on that.
     *
     * <p>{@code lambda = 0} is bit-identical to {@link SpaceTime}: the penalty
     * term vanishes and the enumeration order is the same. That case is run as a
     * control.
     *
     * <p>Raising {@code lambda} is not free. Past some value the planner begins
     * declining genuinely clean hours in order to spread load, and absolute
     * emissions rise even though the non-monotonicity is gone. Both quantities
     * are therefore reported together; the useful result is the trade-off curve,
     * not a single setting.
     *
     * <p>A convex penalty -- {@code meanUtilisation}<sup>{@code gamma}</sup> with
     * {@code gamma > 1}, so that the last slot in an hour costs far more than the
     * first -- is a one-line change here. It is left out deliberately: a second
     * free parameter would have to be swept jointly with the first, and the
     * linear form is already sufficient to test the mechanism claim.
     */
    final class CongestionPriced implements Planner {
        private final CarbonTrace trace;
        private final double lambda;
        private double ciRef = Double.NaN;   // resolved on first use, see ciRef()

        public CongestionPriced(final CarbonTrace trace, final double lambda) {
            if (lambda < 0 || !Double.isFinite(lambda)) {
                throw new IllegalArgumentException("lambda must be finite and >= 0, got " + lambda);
            }
            this.trace = trace;
            this.lambda = lambda;
        }

        @Override public String name() {
            return String.format(java.util.Locale.ROOT, "space+time+price(%.2f)", lambda);
        }

        /**
         * Mean carbon intensity over the eligible regions and the whole horizon.
         *
         * <p>Computed from the eligible set passed to {@code plan}, not from
         * every region in the trace, so that the reference is the average of the
         * grids this policy can actually reach. Resolved once and cached: the
         * eligible set is fixed for the lifetime of a planner instance, and one
         * instance serves exactly one cell of the sweep.
         */
        private double ciRef(final List<String> regions) {
            if (!Double.isNaN(ciRef)) return ciRef;
            double sum = 0;
            long n = 0;
            for (final String r : regions) {
                for (int h = 0; h < trace.horizonHours(); h++) { sum += trace.ciAt(r, h); n++; }
            }
            ciRef = n == 0 ? 0 : sum / n;
            return ciRef;
        }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            final double ref = ciRef(regions);
            String bestRegion = null;
            int bestHour = -1;
            double bestCost = Double.MAX_VALUE;
            for (final String region : regions) {
                for (int h = req.arrivalH(); h <= req.latestStartH(); h++) {
                    if (!ledger.canPlace(region, h, req.durationH())) continue;
                    final double cost = trace.meanCiOverWindow(region, h, req.durationH())
                        + lambda * ref * ledger.meanUtilisation(region, h, req.durationH());
                    if (cost < bestCost) { bestCost = cost; bestRegion = region; bestHour = h; }
                }
            }
            return bestRegion == null ? fallback(req, regions, ledger, trace)
                                      : new Placement(bestRegion, bestHour);
        }
    }

    /**
     * Space and time shift, choosing the least loaded slot among those whose mean
     * carbon intensity is within a tolerance of the cleanest available.
     *
     * <p>This is the cheaper of the two mitigations and answers a narrower
     * question. {@link CongestionPriced} trades carbon against congestion
     * continuously, and so can be driven to accept a materially dirtier hour.
     * This policy never does: it fixes a tolerance {@code tol}, admits only slots
     * within {@code (1+tol)} of the best mean intensity, and uses occupancy
     * purely to choose among them. If that suffices to remove the
     * non-monotonicity, then the defect is caused by arbitrary tie-breaking
     * within a band of near-equivalent hours rather than by the planner pursuing
     * genuinely cleaner ones, and no carbon needs to be surrendered to fix it.
     *
     * <p>{@code tol = 0} admits only slots tied with the best to floating-point
     * equality, so it reproduces {@link SpaceTime} up to tie-breaking.
     *
     * <p>Ties on occupancy are broken by earliest hour, then by the order of the
     * eligible region list, so the policy is deterministic. A randomised variant
     * would desynchronise more aggressively, but it would also make every cell a
     * draw from a distribution and require its own seed axis to interpret.
     */
    final class NearCleanLeastLoaded implements Planner {
        private final CarbonTrace trace;
        private final double tol;   // fractional, e.g. 0.05 for a 5% band

        public NearCleanLeastLoaded(final CarbonTrace trace, final double tol) {
            if (tol < 0 || !Double.isFinite(tol)) {
                throw new IllegalArgumentException("tolerance must be finite and >= 0, got " + tol);
            }
            this.trace = trace;
            this.tol = tol;
        }

        @Override public String name() {
            return String.format(java.util.Locale.ROOT, "space+time+spread(%.0f%%)", 100 * tol);
        }

        @Override
        public Placement plan(final VmRequest req, final List<String> regions, final RegionLedger ledger) {
            // Pass one: the cleanest feasible mean intensity, which sets the band.
            double bestCi = Double.MAX_VALUE;
            for (final String region : regions) {
                for (int h = req.arrivalH(); h <= req.latestStartH(); h++) {
                    if (!ledger.canPlace(region, h, req.durationH())) continue;
                    final double ci = trace.meanCiOverWindow(region, h, req.durationH());
                    if (ci < bestCi) bestCi = ci;
                }
            }
            if (bestCi == Double.MAX_VALUE) return fallback(req, regions, ledger, trace);

            // Pass two: least loaded within the band. Strict < keeps the first
            // candidate found, and the enumeration order gives earliest hour then
            // region-list order as the tie-break.
            final double ceiling = bestCi * (1 + tol);
            String bestRegion = null;
            int bestHour = -1;
            double bestLoad = Double.MAX_VALUE;
            for (final String region : regions) {
                for (int h = req.arrivalH(); h <= req.latestStartH(); h++) {
                    if (!ledger.canPlace(region, h, req.durationH())) continue;
                    if (trace.meanCiOverWindow(region, h, req.durationH()) > ceiling) continue;
                    final double load = ledger.meanUtilisation(region, h, req.durationH());
                    if (load < bestLoad) { bestLoad = load; bestRegion = region; bestHour = h; }
                }
            }
            return bestRegion == null ? fallback(req, regions, ledger, trace)
                                      : new Placement(bestRegion, bestHour);
        }
    }
}