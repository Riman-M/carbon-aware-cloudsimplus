package org.carbonaware.core;

import java.util.List;

/**
 * Measured host power as a function of CPU utilisation.
 *
 * <p>Dell Inc., PowerEdge XR8620T (Intel Xeon Gold 6433N, 2.00 GHz), 32 cores,
 * 256 GB. SPECpower_ssj2008, tested 15 Jul 2024, published 13 Aug 2024,
 * 6,564 overall ssj_ops/watt.
 * spec.org/power_ssj2008/results/res2024q3/power_ssj2008-20240716-01419.html
 *
 * <p>The simulated host in {@code Simulator} matches the benchmarked one
 * exactly (same core count, clock and memory), so the curve is used as
 * published, with no rescaling.
 *
 * <p><b>Why this lives here rather than in a CloudSim power model.</b>
 * {@code CarbonMeter} is the only consumer of power values in this study, and
 * CloudSim Plus's {@code PowerModelHostSpec} constructor signature varies
 * between releases. Keeping the curve in plain Java removes a version
 * dependency from the one number the paper reports. Hosts are still given a
 * {@code PowerModelHostSimple} so the simulator's own bookkeeping is
 * well-formed, but nothing in the results is read from it.
 *
 * <p><b>Why it replaced a linear placeholder, and why that matters.</b>
 * The earlier model assumed 120 W idle against a 480 W peak, an idle fraction
 * of 25%. The real machine idles at 230 W against 420 W, a fraction of 55%. The
 * dynamic range a scheduler can influence is therefore 190 W, not 360 W, so
 * every host in use carries a much larger fixed cost and the gap between
 * concentrating and spreading load narrows. Savings computed under the
 * placeholder were systematically overstated.
 *
 * <p>The curve is also strongly non-linear: nearly flat from 0-30% load
 * (230 to 273 W), steep from 30-60% (273 to 372 W), flat again above 70%
 * (385 to 420 W). A straight line between idle and peak misprices both ends,
 * which is exactly the range the capacity sweep traverses.
 */
public final class SpecPower {

    /** Watts at 0%, 10%, ... 100% load. */
    public static final List<Double> WATTS = List.of(
        230.0,  // active idle
        244.0,  // 10%
        260.0,  // 20%
        273.0,  // 30%
        301.0,  // 40%
        340.0,  // 50%
        372.0,  // 60%
        385.0,  // 70%
        400.0,  // 80%
        416.0,  // 90%
        420.0); // 100%

    /**
     * Linear comparison model for the power-model robustness arm: 120 W idle,
     * 480 W peak, an idle fraction of 25 % against the 55 % of the measured
     * curve. Selected with {@code -Dpower.model=linear}; the measured curve is
     * the default. The switch exists so that the robustness arm is reproduced
     * by the same code as the main sweep rather than by an earlier version.
     */
    public static final double LINEAR_IDLE_W = 120.0;
    public static final double LINEAR_PEAK_W = 480.0;
    private static final boolean LINEAR =
        "linear".equalsIgnoreCase(System.getProperty("power.model", "spec"));

    private SpecPower() { }

    /** Name of the active model, for logging and file provenance. */
    public static String modelName() { return LINEAR ? "linear-120-480" : "specpower-xr8620t"; }

    public static double idleWatts() { return LINEAR ? LINEAR_IDLE_W : WATTS.get(0); }

    public static double maxWatts() { return LINEAR ? LINEAR_PEAK_W : WATTS.get(WATTS.size() - 1); }

    /**
     * Power draw at a given CPU utilisation in [0,1], linearly interpolated
     * between the measured decile points.
     *
     * <p>Interpolating within deciles rather than across the whole range keeps
     * the shape of the measured curve; SPEC publishes no finer resolution, so
     * a straight line between adjacent measured points is the most that can be
     * claimed from the data.
     */
    public static double watts(final double utilization) {
        final double u = Math.max(0.0, Math.min(1.0, utilization));
        if (LINEAR) return LINEAR_IDLE_W + u * (LINEAR_PEAK_W - LINEAR_IDLE_W);
        final double pos = u * (WATTS.size() - 1);
        final int lo = (int) Math.floor(pos);
        if (lo >= WATTS.size() - 1) return WATTS.get(WATTS.size() - 1);
        final double frac = pos - lo;
        return WATTS.get(lo) + frac * (WATTS.get(lo + 1) - WATTS.get(lo));
    }
}
