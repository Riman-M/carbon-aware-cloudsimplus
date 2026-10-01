package org.carbonaware.core;

/**
 * All quantities reported for a single (policy x capacity x deadline x
 * region-set) cell.
 *
 * <p>
 * Emissions appear on three accounting bases (see {@link CarbonMeter}) and both
 * absolutely and normalised per admitted VM-hour. The normalised form is the
 * one
 * comparable across configurations; absolute values are kept because percentage
 * savings on a tiny base are easy to overstate.
 */
public record Metrics(
        String policy,
        String foresight, // "perfect" (planned against groundTruth) or "forecast"
        String regionSet,
        int perRegionCap,
        int totalCapacity,
        int deadlineMarginH,
        double totalGrams,
        double dynamicGrams,
        double attributedGrams,
        double totalKwh,
        double dynamicKwh,
        double attributedKwh,
        int requests,
        double vmHours,
        int deadlineViolations,
        double meanOverrunH,
        double meanDeferralH,
        double maxRegionShare,
        int peakConcurrency,
        int hostsPerRegion,
        long peakActiveHosts,
        double planMillis,
        int misplacedVms,
        int uncreatedVms,
        int cloudletsFinished) {
    public double gramsPerVmHour() {
        return vmHours == 0 ? 0 : totalGrams / vmHours;
    }

    public double dynamicGramsPerVmHour() {
        return vmHours == 0 ? 0 : dynamicGrams / vmHours;
    }

    public double attributedGramsPerVmHour() {
        return vmHours == 0 ? 0 : attributedGrams / vmHours;
    }

    public double violationRate() {
        return requests == 0 ? 0 : deadlineViolations / (double) requests;
    }

    /**
     * True when every VM ran in the region its planner selected and every cloudlet
     * completed.
     *
     * <p>
     * {@code misplacedVms} and {@code uncreatedVms} are now derived from an
     * allocation listener fired at creation time, not from {@code Vm.isCreated()}
     * after {@code sim.start()}. The latter reads false for every VM that ran and
     * was then destroyed, so it reported total failure and total success
     * identically and could not be used to validate a run.
     */
    public boolean placementValid() {
        return misplacedVms == 0 && uncreatedVms == 0 && cloudletsFinished == requests;
    }

    public static String csvHeader() {
        return String.join(",",
                "policy", "foresight", "region_set", "per_region_cap", "total_capacity", "deadline_margin_h",
                "total_gco2", "dynamic_gco2", "attributed_gco2",
                "total_kwh", "dynamic_kwh", "attributed_kwh",
                "requests", "vm_hours",
                "gco2_per_vm_hour", "dynamic_gco2_per_vm_hour", "attributed_gco2_per_vm_hour",
                "deadline_violations", "violation_rate", "mean_overrun_h",
                "mean_deferral_h", "max_region_share", "peak_concurrency",
                "hosts_per_region", "peak_active_hosts", "plan_ms",
                "misplaced_vms", "uncreated_vms", "cloudlets_finished", "placement_valid");
    }

    public String toCsvRow() {
        return String.format(
                "%s,%s,%s,%d,%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d,%.2f,%.6f,%.6f,%.6f,"
                        + "%d,%.4f,%.3f,%.3f,%.4f,%d,%d,%d,%.2f,%d,%d,%d,%b",
                policy, foresight, regionSet, perRegionCap, totalCapacity, deadlineMarginH,
                totalGrams, dynamicGrams, attributedGrams,
                totalKwh, dynamicKwh, attributedKwh,
                requests, vmHours,
                gramsPerVmHour(), dynamicGramsPerVmHour(), attributedGramsPerVmHour(),
                deadlineViolations, violationRate(), meanOverrunH,
                meanDeferralH, maxRegionShare, peakConcurrency,
                hostsPerRegion, peakActiveHosts, planMillis,
                misplacedVms, uncreatedVms, cloudletsFinished, placementValid());
    }
}