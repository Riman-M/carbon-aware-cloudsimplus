# ===================================================================
#  Second workload trace (editor concern 1, Reviewer 1). Run from the
#  project root in the VS Code terminal:   .\run_huawei.ps1   (about 1 hour)
#
#  Repeats the US full sweep and the US origin sweep of run_robust_r1.ps1
#  for five seeds with VM requests sampled from the Huawei Cloud
#  Huawei-East-1 trace instead of Azure. 2000 requests per sample give a
#  mean concurrency of 37 to 44 VMs, matching the Azure samples, because
#  Huawei VMs live shorter on average. The fleet is sized from 350 slots.
#
#  Needs data\huawei\Huawei-East-1.csv (see run_holdout_b.ps1).
#  Afterwards:  python scripts\analyze_r1.py results\huawei > results\huawei\analysis.txt
# ===================================================================
$ErrorActionPreference = "Stop"
$CC     = "data\carboncast"
$Huawei = "data\huawei\Huawei-East-1.csv"
$Fleet  = "-Dsweep.fleetTotal=350"

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

if (-not (Test-Path $Huawei)) { throw "$Huawei is missing" }
New-Item -ItemType Directory -Force -Path "results\huawei" | Out-Null

foreach ($seed in 1, 2, 3, 4, 5) {
    Write-Host "`n############ huawei : seed $seed ############"
    python scripts\prep_traces.py --mode real --carbon-src $CC --region-set us --out data `
        --vm-src $Huawei --vm-format huawei --min-duration-h 2 --hours 720 `
        --requests 2000 --deadline-margin 48 --seed $seed
    Check "prep_traces.py huawei seed $seed"
    Copy-Item "data\vm_requests.csv" "results\huawei\vm-requests-seed$seed.csv" -Force

    mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=56,84,140,350" $Fleet `
        "-Dexec.args=data results/huawei/us_full_seed$seed.csv"
    Check "huawei us_full seed $seed"
    mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=84,350" "-Dsweep.margins=6,48" `
        "-Dsweep.homeRegion=all" $Fleet "-Dexec.args=data results/huawei/us_home_seed$seed.csv"
    Check "huawei us_home seed $seed"
}
Write-Host "`nDone. Run:  python scripts\analyze_r1.py results\huawei > results\huawei\analysis.txt"
