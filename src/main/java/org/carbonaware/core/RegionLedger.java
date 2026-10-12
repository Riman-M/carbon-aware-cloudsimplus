package org.carbonaware.core;

import java.util.*;

/**
 * Tracks concurrent VM occupancy per region and enforces a per-region cap.
 *
 * <p>This is the {@code alloc[j][t]} matrix and constraint (3) from Zanotto et al.
 *
 * <p><b>Changed:</b> the cap is now per region rather than a single global constant.
 * The previous design confounded two axes: at cap {@code C}, a space-shifting policy
 * over {@code |J|} regions had {@code C x |J|} concurrent slots while a time-shifting
 * policy pinned to one region had {@code C}. Any emissions difference between them
 * was therefore partly just "more machines". The experiment now fixes a *total*
 * system capacity and divides it across the eligible regions, so every policy is
 * compared under the same amount of hardware.
 *
 * <p>The occupancy arrays extend past the trace horizon by {@link #OVERRUN_SLACK_H}
 * so that deadline-overrun fallbacks always have somewhere to land.
 */
public final class RegionLedger {

    /** Extra hours beyond the trace horizon reserved for deadline overruns. */
    public static final int OVERRUN_SLACK_H = 336; // two weeks

    private final Map<String, int[]> occupancy = new LinkedHashMap<>();
    private final Map<String, Integer> placedCount = new LinkedHashMap<>();
    private final Map<String, Integer> capByRegion;
    private final int limit;

    /**
     * Placements per clock-hour-of-day of their start, across all regions.
     *
     * <p>Carbon intensity is diurnal, so what a time-shifting policy actually
     * chooses is a clock-hour, not an absolute hour. If every VM converges on
     * the same clock-hour the fleet is synchronised, and they contend for the
     * same slots in the same grid at the same time no matter how far apart
     * they arrived. {@link #maxRegionShare()} cannot see this: a fleet can be
     * wholly inside one region and either spread evenly through its days or
     * piled into one hour of each, and those two give the same regional share
     * and very different emissions.
     */
    private final int[] startsByHourOfDay = new int[24];

    public RegionLedger(final Map<String, Integer> capByRegion, final int horizonHours) {
        this.capByRegion = Map.copyOf(capByRegion);
        this.limit = horizonHours + OVERRUN_SLACK_H;
        for (final String r : capByRegion.keySet()) {
            occupancy.put(r, new int[limit + 1]);
            placedCount.put(r, 0);
        }
    }

    public int capOf(final String region) {
        return capByRegion.getOrDefault(region, 0);
    }

    /** Sum of per-region caps: the total concurrent slots available to any policy. */
    public int totalCapacity() {
        return capByRegion.values().stream().mapToInt(Integer::intValue).sum();
    }

    public boolean canPlace(final String region, final int startHour, final int durationH) {
        final int[] occ = occupancy.get(region);
        final int cap = capOf(region);
        if (occ == null || cap <= 0 || startHour < 0 || startHour + durationH > limit) return false;
        for (int h = startHour; h < startHour + durationH; h++) {
            if (occ[h] >= cap) return false;
        }
        return true;
    }

    public void place(final String region, final int startHour, final int durationH) {
        final int[] occ = occupancy.get(region);
        for (int h = startHour; h < startHour + durationH; h++) occ[h]++;
        placedCount.merge(region, 1, Integer::sum);
        startsByHourOfDay[Math.floorMod(startHour, 24)]++;
    }

    /**
     * Mean occupancy over {@code [startHour, startHour+durationH)} as a fraction
     * of the region's cap, in [0,1].
     *
     * <p>Exposed for the congestion-priced planner. A greedy carbon-aware planner
     * prices the grid but not the queue: it reads {@code canPlace} as a boolean and
     * is therefore indifferent between a slot that is empty and one that is a single
     * VM short of full, even though taking the latter denies that hour to every
     * request still to be placed. This is the quantity that makes the denial
     * visible at decision time, and it is available online, from state the ledger
     * already holds, with no knowledge of future arrivals.
     *
     * <p>Returns 1.0 (maximum congestion) for a window that is out of range or in a
     * region with no capacity, so a caller that skips the {@code canPlace} check
     * still gets a conservative answer rather than a misleadingly cheap one.
     */
    public double meanUtilisation(final String region, final int startHour, final int durationH) {
        final int[] occ = occupancy.get(region);
        final int cap = capOf(region);
        if (occ == null || cap <= 0 || durationH <= 0) return 1.0;
        if (startHour < 0 || startHour + durationH > limit) return 1.0;
        long sum = 0;
        for (int h = startHour; h < startHour + durationH; h++) sum += occ[h];
        return sum / (double) ((long) durationH * cap);
    }

    /** Peak simultaneous VMs across all regions, over the whole ledger. */
    public int peakConcurrency() {
        int peak = 0;
        for (int h = 0; h <= limit; h++) {
            int now = 0;
            for (final int[] occ : occupancy.values()) now += occ[h];
            peak = Math.max(peak, now);
        }
        return peak;
    }

    public int placedIn(final String region) { return placedCount.get(region); }

    /**
     * Share of all placements landing in the single busiest region, in [0,1].
     *
     * <p>This quantifies the "black hole" effect directly: a value near 1 means
     * the policy has concentrated the entire workload into one grid, which is the
     * regime where reported savings look largest and real-world feasibility is
     * lowest. Reporting it alongside emissions keeps that trade-off visible.
     */
    public double maxRegionShare() {
        final int total = placedCount.values().stream().mapToInt(Integer::intValue).sum();
        if (total == 0) return 0;
        return placedCount.values().stream().mapToInt(Integer::intValue).max().orElse(0) / (double) total;
    }

    public Map<String, Integer> placementsByRegion() { return Map.copyOf(placedCount); }

    /**
     * Share of placements starting at the single most common clock-hour, in [0,1].
     *
     * <p>The direct measure of fleet synchronisation. With 24 clock-hours, an
     * unsynchronised fleet sits near 1/24 = 0.042 (higher in practice, because
     * arrivals are themselves diurnal); a fleet that has all converged on one
     * daily carbon minimum approaches 1.
     */
    public double modalStartHourShare() {
        final int total = Arrays.stream(startsByHourOfDay).sum();
        if (total == 0) return 0;
        return Arrays.stream(startsByHourOfDay).max().orElse(0) / (double) total;
    }

    /**
     * Normalised Shannon entropy of the start-clock-hour distribution, in [0,1].
     *
     * <p>1.0 means starts are spread evenly over all 24 clock-hours; 0.0 means
     * every VM starts at the same one. Reported alongside
     * {@link #modalStartHourShare()} because the two fail differently: the
     * modal share is blind to whether the remaining mass is spread evenly or
     * split across a second peak, and entropy is blind to which hour is
     * favoured.
     */
    public double startHourEntropy() {
        final int total = Arrays.stream(startsByHourOfDay).sum();
        if (total == 0) return 0;
        double h = 0;
        for (final int c : startsByHourOfDay) {
            if (c == 0) continue;
            final double p = c / (double) total;
            h -= p * Math.log(p);
        }
        return h / Math.log(24);   // log(24) is the entropy of the uniform case
    }

    /**
     * Peak occupancy divided by mean occupancy across the busiest region's
     * active span (hour 0 to its last occupied hour). 1.0 is perfectly level;
     * higher means the same VM-hours are crowded into fewer hours of that span.
     *
     * <p>Where {@link #modalStartHourShare()} measures agreement on a clock-hour,
     * this measures the resulting crowding in absolute time, which is what
     * actually drives contention against the per-region cap.
     *
     * <p>The mean is deliberately taken over the whole span rather than over
     * occupied hours only. Averaging over occupied hours alone makes a fleet
     * that runs in one tight burst per day look perfectly level, because every
     * hour it does occupy is equally full; the idle hours it crowded out of are
     * exactly the signal, so they have to stay in the denominator.
     *
     * <p><b>Read this with {@link #maxRegionShare()}.</b> Restricting to the
     * busiest region is only equivalent to measuring the fleet while the fleet
     * is in one region, which is the regime every policy studied in Paper A
     * occupies. A policy that disperses spatially leaves the busiest region
     * holding a thin, spiky remainder, and this figure then rises for a reason
     * that has nothing to do with temporal crowding. Use
     * {@link #fleetPeakToMeanOccupancy()} whenever the comparison includes a
     * policy whose regional share is materially below one.
     */
    public double peakToMeanOccupancy() {
        final String busiest = placedCount.entrySet().stream()
            .max(Map.Entry.comparingByValue()).map(Map.Entry::getKey).orElse(null);
        if (busiest == null) return 0;
        final int[] occ = occupancy.get(busiest);
        int peak = 0;
        long sum = 0;
        int lastOccupied = -1;
        for (int h = 0; h < occ.length; h++) {
            peak = Math.max(peak, occ[h]);
            sum += occ[h];
            if (occ[h] > 0) lastOccupied = h;
        }
        if (lastOccupied < 0 || sum == 0) return 0;
        final double mean = sum / (double) (lastOccupied + 1);
        return mean == 0 ? 0 : peak / mean;
    }

    /**
     * Peak divided by mean of the occupancy summed across every region, over
     * the fleet's active span.
     *
     * <p>The region-blind counterpart of {@link #peakToMeanOccupancy()}, and
     * the one to quote when the comparison spans policies with different
     * regional concentration. Spreading the same VM-hours over more regions
     * leaves this figure unchanged, because it asks only whether the fleet as
     * a whole is level in time, so it isolates temporal crowding from spatial
     * dispersal instead of confounding the two.
     *
     * <p>For a fleet confined to one region the two measures agree up to the
     * length of the active span, so adding this does not invalidate any figure
     * already reported for a concentrated policy.
     */
    public double fleetPeakToMeanOccupancy() {
        if (occupancy.isEmpty()) return 0;
        final int span = limit + 1;
        long peak = 0;
        long sum = 0;
        int lastOccupied = -1;
        for (int h = 0; h < span; h++) {
            long now = 0;
            for (final int[] occ : occupancy.values()) now += occ[h];
            peak = Math.max(peak, now);
            sum += now;
            if (now > 0) lastOccupied = h;
        }
        if (lastOccupied < 0 || sum == 0) return 0;
        final double mean = sum / (double) (lastOccupied + 1);
        return mean == 0 ? 0 : peak / mean;
    }
}