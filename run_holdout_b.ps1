# ===================================================================
#  Second pre-registered hold-out test of capacity-filled headroom, on
#  workloads from the corrected sampler. Run from the project root in the
#  VS Code terminal:   .\run_holdout_b.ps1      (roughly 2 hours)
#
#  BEFORE RUNNING: commit and push the four files in predictions\ named
#  holdout-b-*, together with data\holdout-b-4\subsets.csv and
#  data\holdout-b-6\subsets.csv. The push records that the predictions
#  were fixed before these sets were simulated.
#
#  Twenty region sets never simulated before (ten of four regions, ten of
#  six; none repeats a set of the first hold-out round or of the 30
#  subsets) are run under round-robin and space for workload seeds 1 to 3,
#  once with the Azure workload and once with the Huawei-East-1 workload.
#  The Azure predictions are calibrated on the 30 Azure subsets; the Huawei
#  predictions use the same calibration and only the Huawei mean loads, so
#  the Huawei half tests whether the predictor transfers across providers.
#
#  Needs data\huawei\Huawei-East-1.csv:
#     New-Item -ItemType Directory -Force data\huawei | Out-Null
#     Invoke-WebRequest https://github.com/huaweicloud/vm-placement-dataset/raw/main/Huawei-East-1/data/Huawei-East-1.csv -OutFile data\huawei\Huawei-East-1.csv
#
#  Afterwards:
#     python scripts\score_holdout.py --pred predictions\holdout-b-azure-predictions.csv --results results\holdout-b\azure
#     python scripts\score_holdout.py --pred predictions\holdout-b-huawei-predictions.csv --results results\holdout-b\huawei
#  Do not open the result files before scoring.
# ===================================================================
$ErrorActionPreference = "Stop"
$CC     = "data\carboncast"
$Huawei = "data\huawei\Huawei-East-1.csv"
$HuaweiSha256 = "6AF6517CEE18A185B00096D43387E7A0EF847366AECD6AEF70A04F56CC6809C8"
$Caps   = @{ 4 = "56,84,140,352"; 6 = "54,84,138,348" }
$Fleet  = @{ 4 = 352; 6 = 348 }

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

# The load each seed's workload must have, as written into the criteria files.
function ExpectedLoads($criteria) {
    $line = (Select-String -Path $criteria -Pattern "workload seeds and mean loads: (.+)").Matches[0].Groups[1].Value
    $map = @{}
    foreach ($kv in $line.Split(", ", [System.StringSplitOptions]::RemoveEmptyEntries)) {
        $k, $v = $kv.Split("=")
        $map[[int]$k] = [double]$v
    }
    return $map
}

function CheckLoad($vmFile, $expected, $label) {
    $load = python -c "import pandas as pd; print(pd.read_csv(r'$vmFile').duration_h.sum() / 720)"
    Check "load of $vmFile"
    if ([math]::Abs([double]$load - $expected) -gt 0.006) {
        throw "$label workload $vmFile has mean load $load, not the pre-registered $expected"
    }
}

foreach ($w in "azure", "huawei") {
    foreach ($f in "predictions\holdout-b-$w-predictions.csv", "predictions\holdout-b-$w-criteria.txt") {
        if (-not (Test-Path $f)) { throw "$f is missing; the predictions must be committed first" }
    }
}
if (-not (Test-Path $Huawei)) { throw "$Huawei is missing; see the download line at the top" }
$h = (Get-FileHash $Huawei -Algorithm SHA256).Hash
if ($h -ne $HuaweiSha256) { throw "$Huawei differs from the file the predictions were made with (sha256 $h)" }

# Regenerate the set folders and confirm the selection is the committed one.
foreach ($size in 4, 6) {
    $dir = "data\holdout-b-$size"
    $index = "$dir\subsets.csv"
    if (-not (Test-Path $index)) { throw "$index is missing; it is part of the commit" }
    $committed = (Get-Content $index) -join "`n"
    python scripts\make_subsets.py --carbon-src $CC --size $size --count 10 --seed 2028 `
        --prefix "h${size}b-" --out $dir --exclude data\holdout-4\subsets.csv data\holdout-6\subsets.csv
    Check "make_subsets.py size $size"
    if ($committed -ne ((Get-Content $index) -join "`n")) {
        throw "regenerated $index differs from the committed one; stop and investigate"
    }
}

$loads = @{ azure  = ExpectedLoads "predictions\holdout-b-azure-criteria.txt"
            huawei = ExpectedLoads "predictions\holdout-b-huawei-criteria.txt" }

foreach ($seed in 1, 2, 3) {
    # Azure: the per-seed workloads written by run_subsets.ps1, which the
    # calibration was fitted on.
    $az = "data\wl_seed$seed\vm_requests.csv"
    if (-not (Test-Path $az)) { throw "$az is missing; it comes from run_subsets.ps1" }
    CheckLoad $az $loads.azure[$seed] "Azure"

    $hwDir = "data\wl_huawei_seed$seed"
    if (-not (Test-Path "$hwDir\vm_requests.csv")) {
        python scripts\prep_traces.py --mode real --carbon-src $CC --region-set us `
            --vm-src $Huawei --vm-format huawei --min-duration-h 2 --hours 720 `
            --requests 2000 --deadline-margin 48 --seed $seed --out $hwDir
        Check "prep_traces.py huawei seed $seed"
    }
    CheckLoad "$hwDir\vm_requests.csv" $loads.huawei[$seed] "Huawei"
}

foreach ($w in "azure", "huawei") {
    New-Item -ItemType Directory -Force -Path "results\holdout-b\$w" | Out-Null
    foreach ($seed in 1, 2, 3) {
        $vm = if ($w -eq "azure") { "data\wl_seed$seed\vm_requests.csv" } else { "data\wl_huawei_seed$seed\vm_requests.csv" }
        foreach ($size in 4, 6) {
            $sets = @(Get-ChildItem -Directory "data\holdout-b-$size" -Filter "h${size}b-*" | Sort-Object Name)
            if ($sets.Count -ne 10) { throw "expected 10 sets under data\holdout-b-$size, found $($sets.Count)" }
            foreach ($d in $sets) {
                $name = $d.Name
                Copy-Item $vm "$($d.FullName)\vm_requests.csv" -Force
                Write-Host "`n---- $w : seed $seed : $name ----"
                mvn -q exec:java "-Dsweep.regionSets=$name" "-Dsweep.caps=$($Caps[$size])" `
                    "-Dsweep.margins=48" "-Dsweep.fleetTotal=$($Fleet[$size])" `
                    "-Dsweep.policies=round-robin,space" `
                    "-Dexec.args=data/holdout-b-$size/$name results/holdout-b/$w/$name-seed$seed.csv"
                Check "$w $name seed $seed"
            }
        }
    }
}

$n = (Get-ChildItem "results\holdout-b\*\*.csv").Count
Write-Host "`n================= second hold-out run complete: $n result files (expected 120) ================="
