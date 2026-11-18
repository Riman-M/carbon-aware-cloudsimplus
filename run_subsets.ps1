# ===================================================================
#  Region-set composition experiment, PowerShell version of
#  run_subsets.bat for the VS Code terminal. Run from the project root:
#
#     .\run_subsets.ps1            both phases, roughly 3.5 hours
#     .\run_subsets.ps1 -CoreOnly  phase 1 only, roughly 2 hours
#
#  The fleet is sized from 350 total slots (-Dsweep.fleetTotal=350), the
#  largest cap run, so that no region is asked to hold more concurrent
#  VMs than its hosts were provisioned for. See run_fidelity.ps1.
#
#  Every -D argument is quoted: unquoted, PowerShell splits
#  -Dsweep.regionSets=eu at the dot and Maven receives a broken property.
#
#  If PowerShell refuses to run scripts, run this once in the terminal:
#     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# ===================================================================
param([switch]$CoreOnly)

$ErrorActionPreference = "Stop"
$CC    = "data\carboncast"
$Azure = @("--vm-src", "data\AzureVMTraces\vmtable_with_header.csv", "--min-duration-h", "2")
$Base  = @("--hours", "720", "--requests", "1000", "--deadline-margin", "48")

function Check($step) {
    if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" }
}

# The per-subset trace folders are generated, not versioned, so test for
# them rather than for subsets.csv, which is versioned and may be present
# without them.
$subsets = @(Get-ChildItem -Directory "data\subsets" -Filter "sub*" -ErrorAction SilentlyContinue)
if ($subsets.Count -eq 0) {
    python scripts\make_subsets.py --carbon-src $CC
    Check "make_subsets.py"
    $subsets = @(Get-ChildItem -Directory "data\subsets" -Filter "sub*")
}
if ($subsets.Count -eq 0) { throw "no subset folders under data\subsets; make_subsets.py produced nothing" }
$subsets = $subsets | Sort-Object Name
Write-Host "subsets to run: $($subsets.Count)"
New-Item -ItemType Directory -Force -Path "results\subsets" | Out-Null

foreach ($seed in 1, 2, 3) {
    Write-Host "`n############ seed $seed : sampling workload ############"
    python scripts\prep_traces.py --mode real --carbon-src $CC --region-set eu @Azure @Base `
        --seed $seed --out "data\wl_seed$seed"
    Check "prep_traces.py seed $seed"

    foreach ($d in $subsets) {
        $name = $d.Name
        Copy-Item "data\wl_seed$seed\vm_requests.csv" "$($d.FullName)\vm_requests.csv" -Force

        Write-Host "`n---- seed $seed : $name : core ----"
        mvn -q exec:java "-Dsweep.regionSets=$name" "-Dsweep.caps=55,85,140,350" `
            "-Dsweep.margins=48" "-Dsweep.fleetTotal=350" "-Dsweep.policies=round-robin,space,space+time" `
            "-Dexec.args=data/subsets/$name results/subsets/${name}_core_seed$seed.csv"
        Check "$name core seed $seed"

        if (-not $CoreOnly) {
            Write-Host "---- seed $seed : $name : origin ----"
            mvn -q exec:java "-Dsweep.regionSets=$name" "-Dsweep.caps=85,350" `
                "-Dsweep.margins=48" "-Dsweep.fleetTotal=350" "-Dsweep.policies=time" "-Dsweep.homeRegion=all" `
                "-Dexec.args=data/subsets/$name results/subsets/${name}_origin_seed$seed.csv"
            Check "$name origin seed $seed"
        }
    }
}

$n = (Get-ChildItem "results\subsets\*.csv").Count
Write-Host "`n================= region-subset sweep complete: $n result files ================="
