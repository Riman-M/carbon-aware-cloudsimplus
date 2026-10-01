package org.carbonaware;

import org.carbonaware.core.CarbonMeter;
import org.carbonaware.core.Metrics;
import org.carbonaware.core.Model.Placement;
import org.carbonaware.core.Model.VmRequest;
import org.carbonaware.core.RegionLedger;
import org.carbonaware.core.SpecPower;
import org.carbonaware.policy.Planners.Planner;
import org.carbonaware.trace.CarbonTrace;

import org.cloudsimplus.brokers.DatacenterBroker;
import org.cloudsimplus.brokers.DatacenterBrokerSimple;
import org.cloudsimplus.cloudlets.Cloudlet;
import org.cloudsimplus.cloudlets.CloudletSimple;
import org.cloudsimplus.core.CloudSimPlus;
import org.cloudsimplus.datacenters.Datacenter;
import org.cloudsimplus.datacenters.DatacenterSimple;
import org.cloudsimplus.hosts.Host;
import org.cloudsimplus.hosts.HostSimple;
import org.cloudsimplus.power.models.PowerModelHostSimple;
import org.cloudsimplus.resources.Pe;
import org.cloudsimplus.resources.PeSimple;
import org.cloudsimplus.utilizationmodels.UtilizationModelDynamic;
import org.cloudsimplus.vms.Vm;
import org.cloudsimplus.vms.VmSimple;

import java.util.*;

/** Runs one experiment cell and returns its {@link Metrics}. */
public final class Simulator {

    // Host spec after Zanotto et al.: Dell PowerEdge XR8620T, Xeon Gold 6433N,
    // 32 cores @ 2.00 GHz, 256 GB. The simulated host matches the benchmarked
    // one exactly, so the power curve below needs no rescaling.
    public static final int HOST_PES = 32;
    public static final double HOST_MIPS = 2000;
    public static final long HOST_RAM_MB = 256L * 1024;
    public static final long HOST_BW_MBPS = 10_000;
    public static final long HOST_STORAGE = 1_000_000;

    private static final double SCHEDULING_INTERVAL_SEC = 300;

    /** Spare capacity above the sizing basis, absorbing packing fragmentation. */
    private static final double HOST_HEADROOM = 1.25;

    /** Slack hours added to the measurement window beyond the trace horizon. */
    private static final int WINDOW_SLACK_H = 48;

    /**
     * Seconds an idle VM is kept before destruction.
     *
     * <p>
     * CloudSim Plus's default is -1, meaning a VM is torn down the instant it
     * goes idle. A VM created at its submission delay is momentarily idle before
     * the broker dispatches its bound cloudlet, which can destroy it before it
     * ever runs -- the run then reports near-zero dynamic power with no error.
     * A small positive delay closes that window while still releasing capacity
     * promptly once work finishes.
     */
    private static final double VM_DESTRUCTION_DELAY_SEC = 60.0;

    /**
     * Hosts provisioned per region.
     *
     * <p>
     * <b>Changed:</b> the sizing basis is now fixed across the whole sweep --
     * it is driven by the <em>largest</em> capacity cap the experiment will run,
     * not by the cap of the current cell. Sizing per cell coupled fleet size to
     * the capacity axis, so total emissions rose with the cap simply because more
     * hardware had been provisioned (918 to 3826 kgCO2 for the eu set on identical
     * work). That reads as carbon-aware scheduling making things worse. With a
     * fixed fleet the cap constrains scheduling only, which is what it is meant to
     * represent. The attributed and dynamic bases are unaffected either way, since
     * they already discount hosts that are not running anything.
     *
     * <p>
     * Sizing is driven by the largest VM in the workload,
     * not the mean. The cap bounds the <em>number</em> of concurrent VMs, not
     * their size, and CloudSim Plus places per host rather than from a pool -- a
     * 128 GB VM needs one host with 128 GB free. With this trace the mean VM is
     * 6.3 vCPU / 29.7 GB but the largest is 16 vCPU / 128 GB, so mean-based
     * sizing under-provisioned by roughly 3x and creation failed. When the mapped
     * datacenter cannot fit a VM, CloudSim Plus silently reroutes it to a
     * fallback datacenter and the simulation stops honouring the planner's region
     * choice, so this must be sized conservatively.
     */
    static int hostsFor(final int maxConcurrent, final int maxPesPerVm, final long maxRamMb) {
        final int byPes = (int) Math.ceil((maxConcurrent * (double) maxPesPerVm) / HOST_PES);
        final int byRam = (int) Math.ceil((maxConcurrent * (double) maxRamMb) / HOST_RAM_MB);
        return Math.max(1, (int) Math.ceil(Math.max(byPes, byRam) * HOST_HEADROOM));
    }

    public static Metrics run(
            final CarbonTrace groundTruth,
            final List<VmRequest> requests,
            final Planner planner,
            final String foresight, // "perfect" or "forecast" -- caller's label, see note below
            final List<String> eligibleRegions,
            final String regionSetName,
            final Map<String, Integer> capByRegion,
            final int fleetSizingCapPerRegion, // largest per-region cap in the sweep
            final int deadlineMarginH) {
        // There used to be a second CarbonTrace parameter here ("planningTrace,
        // pass groundTruth for the perfect-foresight arm") that this method never
        // actually read -- every placement decision comes from the trace baked
        // into `planner` at construction time (see Planners.java), not from an
        // argument to run(). That made the parameter dead code: changing it did
        // nothing. The real foresight/forecast switch lives in ExperimentRunner,
        // where `planner` is built; `foresight` here is purely a label for the
        // results row, kept in sync with that choice by the caller.
        // ---- 1. Plan every request against the capacity ledger ----
        final RegionLedger ledger = new RegionLedger(capByRegion, groundTruth.horizonHours());

        final Map<VmRequest, Placement> plan = new LinkedHashMap<>();
        int violations = 0;
        long overrunSum = 0;
        long deferralSum = 0;
        double vmHours = 0;

        final long planStart = System.nanoTime();
        for (final VmRequest req : requests) {
            final Placement p = planner.plan(req, eligibleRegions, ledger);
            ledger.place(p.region(), p.startHour(), req.durationH());
            plan.put(req, p);
            if (!p.meetsDeadline())
                violations++;
            overrunSum += p.deadlineOverrunH();
            deferralSum += (p.startHour() - req.arrivalH());
            vmHours += req.durationH();
        }
        final double planMillis = (System.nanoTime() - planStart) / 1e6;

        // ---- 2. Build and run the simulation ----
        final CloudSimPlus sim = new CloudSimPlus();

        final int maxPes = requests.stream().mapToInt(VmRequest::pes).max().orElse(4);
        final long maxRam = requests.stream().mapToLong(VmRequest::ramMb).max().orElse(8192);

        final Map<String, Datacenter> dcByRegion = new LinkedHashMap<>();
        final Map<Datacenter, String> regionOf = new LinkedHashMap<>();
        // Identical in every cell of the sweep -- see hostsFor().
        final int hostsPerRegion = hostsFor(fleetSizingCapPerRegion, maxPes, maxRam);
        for (final String region : eligibleRegions) {
            final Datacenter dc = new DatacenterSimple(sim, createHosts(hostsPerRegion));
            dc.setSchedulingInterval(SCHEDULING_INTERVAL_SEC);
            dcByRegion.put(region, dc);
            regionOf.put(dc, region);
        }

        final CarbonMeter meter = new CarbonMeter(groundTruth, regionOf);
        meter.attach(sim);

        // Every policy is measured over the identical window. Without this, a
        // policy that defers work simply runs longer and accrues more idle
        // energy, which reads as higher emissions regardless of its decisions.
        final double windowSec = (groundTruth.horizonHours() + WINDOW_SLACK_H) * 3600.0;
        sim.terminateAt(windowSec);

        // One broker per region.
        //
        // A single broker cannot express per-VM region placement:
        // DatacenterBrokerAbstract invokes the datacenter mapper once per batch of
        // waiting VMs and then asks that one datacenter to create all of them, so
        // the mapper's Vm argument is merely the first VM of the batch. With one
        // broker every VM landed in the same datacenter regardless of the plan --
        // regional concentration varied from 10% to 100% across policies while
        // emissions stayed identical, because no VM ever moved. Giving each region
        // its own broker, each pinned to its own datacenter, makes misplacement
        // impossible by construction rather than merely detectable.
        final Map<String, DatacenterBroker> brokerByRegion = new LinkedHashMap<>();

        // Keyed by VM id, not by Vm.
        //
        // CloudSim Plus derives Vm.equals/hashCode from the id *and the broker*,
        // and the broker is assigned during submitVmList -- after these entries are
        // inserted. A Vm key therefore changes its hash while sitting in the map,
        // every post-simulation lookup misses, and each VM is scored as misplaced
        // even when it ran exactly where planned. Request ids are globally unique,
        // so they are a stable key.
        final Map<Long, Datacenter> targetDc = new HashMap<>();
        final Map<Long, Datacenter> actualDc = new HashMap<>();

        for (final String region : eligibleRegions) {
            final Datacenter dc = dcByRegion.get(region);
            final DatacenterBroker b = new DatacenterBrokerSimple(sim, "broker_" + region);
            b.setVmDestructionDelay(VM_DESTRUCTION_DELAY_SEC);
            b.setDatacenterMapper((lastDc, vm) -> dc);

            brokerByRegion.put(region, b);
        }

        final List<Vm> vms = new ArrayList<>();
        final Map<String, List<Vm>> vmsByRegion = new LinkedHashMap<>();
        final Map<String, List<Cloudlet>> cloudletsByRegion = new LinkedHashMap<>();
        eligibleRegions.forEach(r -> {
            vmsByRegion.put(r, new ArrayList<>());
            cloudletsByRegion.put(r, new ArrayList<>());
        });

        for (final var e : plan.entrySet()) {
            final VmRequest req = e.getKey();
            final Placement pl = e.getValue();

            final Vm vm = new VmSimple(req.id(), HOST_MIPS, req.pes());
            vm.setRam(req.ramMb()).setBw(1000).setSize(10_000);
            // The VM delay alone implements time-shifting. Setting the same delay
            // on the cloudlet as well double-counts it.
            vm.setSubmissionDelay(pl.startHour() * 3600.0);

            targetDc.put(vm.getId(), dcByRegion.get(pl.region()));
            vms.add(vm);
            vmsByRegion.get(pl.region()).add(vm);

            // Sized to run for exactly durationH at full utilisation, matching the
            // uniform-power, non-preemptible, fixed-size VM assumption.
            final long lengthMi = (long) (HOST_MIPS * req.durationH() * 3600.0);
            final Cloudlet cl = new CloudletSimple(req.id(), lengthMi, req.pes());
            cl.setUtilizationModelCpu(new UtilizationModelDynamic(1.0))
                    .setUtilizationModelRam(new UtilizationModelDynamic(0.5))
                    .setUtilizationModelBw(new UtilizationModelDynamic(0.1));
            cl.setVm(vm);
            cloudletsByRegion.get(pl.region()).add(cl);
        }

        for (final String region : eligibleRegions) {
            final DatacenterBroker b = brokerByRegion.get(region);
            b.submitVmList(vmsByRegion.get(region));
            b.submitCloudletList(cloudletsByRegion.get(region));
        }
        sim.start();

        // Placement is read back from each broker's created list rather than from
        // a creation listener.
        //
        // addOnVmsCreatedListener fires only once the broker has created *all* its
        // waiting VMs. Where capacity binds, some VMs are deferred past the
        // measurement window and never created, the condition is never met, and
        // the listener never fires -- so the cells that matter most reported every
        // VM as uncreated while their cloudlets were visibly completing. The
        // created list is cumulative and survives VM destruction, and since each
        // broker is pinned to exactly one datacenter, membership in that list is
        // itself the placement record; no getHost() call is needed.
        for (final var e : brokerByRegion.entrySet()) {
            final Datacenter dc = dcByRegion.get(e.getKey());
            for (final Vm v : e.getValue().getVmCreatedList()) {
                actualDc.putIfAbsent(v.getId(), dc);
            }
        }

        // The invariant the whole experiment rests on: every VM must actually run
        // in the region its planner chose.
        int misplaced = 0;
        int notCreated = 0;
        for (final Vm vm : vms) {
            final Datacenter placed = actualDc.get(vm.getId());
            if (placed == null) {
                notCreated++;
                continue;
            }
            if (placed != targetDc.get(vm.getId()))
                misplaced++;
        }
        final int finished = brokerByRegion.values().stream()
                .mapToInt(b -> b.getCloudletFinishedList().size()).sum();

        return new Metrics(
                planner.name(), foresight, regionSetName,
                capByRegion.values().stream().mapToInt(Integer::intValue).max().orElse(0),
                ledger.totalCapacity(),
                deadlineMarginH,
                meter.totalGrams(), meter.dynamicGrams(), meter.attributedGrams(),
                meter.totalKwh(), meter.dynamicKwh(), meter.attributedKwh(),
                requests.size(), vmHours,
                violations,
                requests.isEmpty() ? 0 : overrunSum / (double) requests.size(),
                requests.isEmpty() ? 0 : deferralSum / (double) requests.size(),
                ledger.maxRegionShare(),
                ledger.peakConcurrency(),
                hostsPerRegion,
                meter.peakActiveHosts(),
                planMillis,
                misplaced,
                notCreated,
                finished);
    }

    private static List<Host> createHosts(final int count) {
        final List<Host> hosts = new ArrayList<>(count);
        for (int i = 0; i < count; i++) {
            final List<Pe> pes = new ArrayList<>(HOST_PES);
            for (int p = 0; p < HOST_PES; p++)
                pes.add(new PeSimple(HOST_MIPS));
            final Host host = new HostSimple(HOST_RAM_MB, HOST_BW_MBPS, HOST_STORAGE, pes);
            // Set so the simulator's own bookkeeping is well-formed. Emissions
            // are computed by CarbonMeter from the measured SpecPower curve, not
            // from this linear approximation -- see SpecPower for why.
            host.setPowerModel(new PowerModelHostSimple(
                    SpecPower.maxWatts(), SpecPower.idleWatts()));
            hosts.add(host);
        }
        return hosts;
    }
}