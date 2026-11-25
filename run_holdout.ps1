# ===================================================================
#  Hold-out test of capacity-filled headroom. Run from the project root
#  in the VS Code terminal:   .\run_holdout.ps1      (roughly 1 hour)
#
#  BEFORE RUNNING: commit and push predictions\holdout-predictions.csv
#  and predictions\holdout-criteria.txt. The push timestamp is the record
#  that the predictions were fixed before these sets were simulated.
#
#  Runs 20 region sets that have never been simulated (ten of four regions
#  in data\holdout-4, ten of six in data\holdout-6) under round-robin and
#  space, at four capacity levels, for workload seeds 1 to 3. The fleet is
#  sized from the largest capacity of each set size.
#
#  Afterwards:  python scripts\score_holdout.py
#  Do not open the result files before scoring: the criteria are applied
#  to the full run as a whole.
# ===================================================================
$ErrorActionPreference = "Stop"
$CC    = "data\carboncast"
$Azure = @("--vm-src", "data\AzureVMTraces\vmtable_with_header.csv", "--min-duration-h", "2")
$Base  = @("--hours", "720", "--requests", "1000", "--deadline-margin", "48")
$Caps  = @{ 4 = "56,84,140,352"; 6 = "54,84,138,348" }
$Fleet = @{ 4 = 352; 6 = 348 }

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

if (-not (Test-Path "predictions\holdout-predictions.csv")) {
    throw "predictions\holdout-predictions.csv is missing; the hold-out run needs its predictions first"
}

# Regenerate the set folders and confirm the selection is the committed one.
foreach ($size in 4, 6) {
    $dir = "data\holdout-$size"
    $index = "$dir\subsets.csv"
    if (-not (Test-Path $index)) { throw "$index is missing; it is part of the commit" }
    # Compared line by line, so a checkout that converts line endings does
    # not count as a difference.
    $committed = (Get-Content $index) -join "`n"
    python scripts\make_subsets.py --carbon-src $CC --size $size --count 10 --seed 2027 `
        --prefix "h$size-" --out $dir
    Check "make_subsets.py size $size"
    $regenerated = (Get-Content $index) -join "`n"
    if ($committed -ne $regenerated) {
        throw "regenerated $index differs from the committed one; stop and investigate"
    }
}

New-Item -ItemType Directory -Force -Path "results\holdout" | Out-Null

foreach ($seed in 1, 2, 3) {
    $wl = "data\wl_seed$seed"
    if (-not (Test-Path "$wl\vm_requests.csv")) {
        python scripts\prep_traces.py --mode real --carbon-src $CC --region-set eu @Azure @Base `
            --seed $seed --out $wl
        Check "prep_traces.py seed $seed"
    }
    foreach ($size in 4, 6) {
        $sets = @(Get-ChildItem -Directory "data\holdout-$size" -Filter "h$size-*" | Sort-Object Name)
        if ($sets.Count -ne 10) { throw "expected 10 sets under data\holdout-$size, found $($sets.Count)" }
        foreach ($d in $sets) {
            $name = $d.Name
            Copy-Item "$wl\vm_requests.csv" "$($d.FullName)\vm_requests.csv" -Force
            Write-Host "`n---- seed $seed : $name ----"
            mvn -q exec:java "-Dsweep.regionSets=$name" "-Dsweep.caps=$($Caps[$size])" `
                "-Dsweep.margins=48" "-Dsweep.fleetTotal=$($Fleet[$size])" `
                "-Dsweep.policies=round-robin,space" `
                "-Dexec.args=data/holdout-$size/$name results/holdout/$name-seed$seed.csv"
            Check "$name seed $seed"
        }
    }
}

$n = (Get-ChildItem "results\holdout\*.csv").Count
Write-Host "`n================= hold-out run complete: $n result files (expected 60) ================="
Write-Host "Next:  python scripts\score_holdout.py"
