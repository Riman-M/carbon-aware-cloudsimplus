# ===================================================================
#  Schedule-fidelity diagnostic. Run from the project root in the
#  VS Code terminal:   .\run_fidelity.ps1        (roughly 15 minutes)
#
#  Question: do VMs run at the hours their planner chose? The placement
#  check confirms region and completion but not timing. The published
#  fleet was sized for 200 total slots while the sweep ran up to 350, so
#  at 350 a region can be asked to hold more concurrent VMs than its
#  hosts were sized for, and CloudSim Plus then creates the overflow late.
#
#  Runs the US origin sweep (all policies, every WaitAwhile origin) at
#  84 and 350 slots, margins 6 and 48 h, seeds 1 and 3, twice:
#     fleet200  the fleet as published (sized from 200 slots)
#     fleet350  the fleet sized from the largest cap actually run
#  84 slots is the control: no lag is expected there under either fleet.
# ===================================================================
$ErrorActionPreference = "Stop"
$CC    = "data\carboncast"
$Azure = @("--vm-src", "data\AzureVMTraces\vmtable_with_header.csv", "--min-duration-h", "2")
$Base  = @("--out", "data", "--hours", "720", "--requests", "1000", "--deadline-margin", "48")

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

New-Item -ItemType Directory -Force -Path "results\fidelity" | Out-Null

foreach ($seed in 1, 3) {
    Write-Host "`n############ seed $seed ############"
    python scripts\prep_traces.py --mode real --carbon-src $CC --region-set us @Azure @Base --seed $seed
    Check "prep_traces.py seed $seed"

    foreach ($fleet in 200, 350) {
        Write-Host "---- seed $seed : fleet sized for $fleet slots ----"
        mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=84,350" "-Dsweep.margins=6,48" `
            "-Dsweep.homeRegion=all" "-Dsweep.fleetTotal=$fleet" `
            "-Dexec.args=data results/fidelity/us_home_seed${seed}_fleet$fleet.csv"
        Check "seed $seed fleet $fleet"
    }
}
Write-Host "`nDone. Run:  python scripts\analyze_fidelity.py"
