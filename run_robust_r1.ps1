# ===================================================================
#  Robustness pass for the revision. Run from the project root in the
#  VS Code terminal, after run_subsets.ps1 has finished:
#
#     .\run_robust_r1.ps1                      every arm, roughly 4 hours
#     .\run_robust_r1.ps1 -Arms us,eu          only the named arms
#
#  Arms: us, eu, july, delayins, linear
#
#  Differences from run_robust.bat, the pass behind the submitted paper:
#
#  * The fleet is sized from 350 total slots, the largest cap run
#    (-Dsweep.fleetTotal=350). The published pass sized it from 200. The
#    fidelity diagnostic showed that at 350 slots this changes attributed
#    emissions by 6 to 32 % for every policy that concentrates load, and
#    leaves one VM per affected row unfinished; the dynamic basis moves
#    by about 1 %. At 84 slots nothing changes.
#  * Every arm uses five workload seeds. The EU control, the July window
#    and the delay-insensitive class previously used three.
#  * The linear power arm is produced by this code (-Dpower.model=linear)
#    rather than by an earlier version of it.
#
#  Results go to results\r1\ so the published files stay untouched.
# ===================================================================
param([string[]]$Arms = @("us", "eu", "july", "delayins", "linear"))

$ErrorActionPreference = "Stop"
$CC    = "data\carboncast"
$Azure = @("--vm-src", "data\AzureVMTraces\vmtable_with_header.csv", "--min-duration-h", "2")
$Base  = @("--out", "data", "--hours", "720", "--deadline-margin", "48")
$Fleet = "-Dsweep.fleetTotal=350"
$Seeds = 1, 2, 3, 4, 5
$Out   = "results/r1"

function Check($step) { if ($LASTEXITCODE -ne 0) { throw "FAILED at: $step" } }

function Prep($set, $seed, [string[]]$extra) {
    python scripts\prep_traces.py --mode real --carbon-src $CC --region-set $set @Azure @Base `
        --seed $seed @extra
    Check "prep_traces.py $set seed $seed $extra"
}

function Full($set, $caps, $file, [string[]]$extra) {
    mvn -q exec:java "-Dsweep.regionSets=$set" "-Dsweep.caps=$caps" $Fleet @extra `
        "-Dexec.args=data $Out/$file"
    Check $file
}

function Home($set, $file, [string[]]$extra) {
    mvn -q exec:java "-Dsweep.regionSets=$set" "-Dsweep.caps=84,350" "-Dsweep.margins=6,48" `
        "-Dsweep.homeRegion=all" $Fleet @extra "-Dexec.args=data $Out/$file"
    Check $file
}

New-Item -ItemType Directory -Force -Path "results\r1" | Out-Null

foreach ($seed in $Seeds) {
    if ($Arms -contains "us" -or $Arms -contains "linear") {
        Write-Host "`n############ seed $seed : us, January ############"
        Prep "us" $seed @("--requests", "1000")
        if ($Arms -contains "us") {
            Full "us" "56,84,140,350" "us_full_seed$seed.csv" @()
            Home "us" "us_home_seed$seed.csv" @()
        }
        if ($Arms -contains "linear") {
            Write-Host "---- seed $seed : linear power model ----"
            Full "us" "56,84,140,350" "us_linear_full_seed$seed.csv" @("-Dpower.model=linear")
            Home "us" "us_linear_home_seed$seed.csv" @("-Dpower.model=linear")
        }
    }
    if ($Arms -contains "eu") {
        Write-Host "`n############ seed $seed : eu, January ############"
        Prep "eu" $seed @("--requests", "1000")
        Full "eu" "55,85,140,350" "eu_full_seed$seed.csv" @()
    }
    if ($Arms -contains "july") {
        Write-Host "`n############ seed $seed : us, July ############"
        Prep "us" $seed @("--requests", "1000", "--start-hour", "4320")
        Home "us" "us_july_home_seed$seed.csv" @()
    }
    if ($Arms -contains "delayins") {
        # 90 requests holds total demand, and so utilisation, close to the
        # main sweep: this class lives about ten times longer on average.
        Write-Host "`n############ seed $seed : us, delay-insensitive ############"
        Prep "us" $seed @("--requests", "90", "--vm-category", "Delay-insensitive")
        Home "us" "us_delayins_home_seed$seed.csv" @()
    }
}

$n = (Get-ChildItem "results\r1\*.csv").Count
Write-Host "`n================= revision robustness pass complete: $n result files ================="
