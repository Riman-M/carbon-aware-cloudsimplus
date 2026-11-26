# ===================================================================
#  Top-bucket sensitivity (Reviewer 1). Run from the project root in the
#  VS Code terminal:   .\run_bucket.ps1      (roughly 1 hour 40 minutes)
#
#  The Azure V2 trace records its largest VMs only as ">24" cores and
#  ">64" GB. The main runs substitute 30 cores and 70 GB. This repeats the
#  US full sweep and the US origin sweep, five seeds each, under two
#  bracketing substitutions:
#     low   25 cores, 65 GB    the smallest values the buckets allow
#     high  32 cores, 128 GB   the largest VM one simulated host can hold
#  Everything else matches run_robust_r1.ps1, including the fleet sized
#  from 350 slots. Results go to results\bucket\<variant>\, each with the
#  sampled workload kept beside it so the affected VMs can be counted.
#
#  Afterwards:  python scripts\analyze_bucket.py
# ===================================================================
$ErrorActionPreference = "Stop"
$CC    = "data\carboncast"
$Azure = @("--vm-src", "data\AzureVMTraces\vmtable_with_header.csv", "--min-duration-h", "2")
$Base  = @("--out", "data", "--hours", "720", "--requests", "1000", "--deadline-margin", "48")
$Variants = [ordered]@{ low = @("25", "65"); high = @("32", "128") }

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

foreach ($v in $Variants.Keys) {
    $core, $mem = $Variants[$v]
    $out = "results\bucket\$v"
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    foreach ($seed in 1, 2, 3, 4, 5) {
        Write-Host "`n############ $v ($core cores, $mem GB) : seed $seed ############"
        python scripts\prep_traces.py --mode real --carbon-src $CC --region-set us @Azure @Base `
            --seed $seed --top-core-bucket $core --top-memory-bucket $mem
        Check "prep_traces.py $v seed $seed"
        Copy-Item "data\vm_requests.csv" "$out\vm-requests-seed$seed.csv" -Force

        mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=56,84,140,350" "-Dsweep.fleetTotal=350" `
            "-Dexec.args=data results/bucket/$v/us_full_seed$seed.csv"
        Check "$v us_full seed $seed"
        mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=84,350" "-Dsweep.margins=6,48" `
            "-Dsweep.homeRegion=all" "-Dsweep.fleetTotal=350" `
            "-Dexec.args=data results/bucket/$v/us_home_seed$seed.csv"
        Check "$v us_home seed $seed"
    }
}
Write-Host "`nDone. Run:  python scripts\analyze_bucket.py"
