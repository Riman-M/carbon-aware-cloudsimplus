@echo off
REM ===================================================================
REM  Seed-robustness pass for the foresight comparison (Paper B).
REM
REM  Run from the project root in cmd.exe.
REM
REM  WHY THIS EXISTS
REM  A single-seed pass put the forecast penalty between -4.5% and
REM  +3.2% depending on the cell, with a non-monotonic shape across
REM  capacity. Those effects are small enough that none of them can be
REM  separated from workload sampling noise at one seed. The rule from
REM  the Paper A robustness pass applies unchanged: an effect that moves
REM  less than its own seed spread is not a finding.
REM
REM  WHAT IS HELD FIXED
REM  --seed drives the reservoir sample from the Azure vmtable, so each
REM  seed is a different draw of 1000 VMs from the same population.
REM  Carbon traces, capacities, margins and the forecast lead time are
REM  identical across seeds, so all spread is sampling noise.
REM
REM  CONFIGURATION NOTES
REM  * Azure workload (--vm-src), matching Paper A. Paper B's claim is
REM    about how much of Paper A's saving survives imperfect foresight,
REM    which only means something if both measure the same workload.
REM  * Caps are multiples of 7 (the us region count) so no slots are
REM    lost to integer division. 56 is ~1.19x the Azure mean concurrency
REM    of 47.2 and is the genuinely tight cell; 350 is ~7.4x and is the
REM    slack cell where greedy herding, if real, should show up.
REM  * --start-hour 13128 is 2021-07-01. CarbonCast publishes forecasts
REM    for Jul-Dec 2021 only, so the window must start at or after this
REM    hour; 13128 + 720 lands well inside it.
REM
REM  Roughly 2 hours. Writes results\forecast\us_fc_seed<N>.csv.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set AZURE=--vm-src data\AzureVMTraces\vmtable_with_header.csv --min-duration-h 2
set BASE=--out data --hours 720 --requests 1000 --deadline-margin 48
set FORECAST=--start-hour 13128 --forecast --lead-hour 24

if not exist results\forecast mkdir results\forecast

for %%s in (1 2 3 4 5) do (
  echo.
  echo ############ seed %%s : us foresight sweep ############
  %PREP% --mode real --carbon-src %CC% --region-set us %AZURE% %BASE% %FORECAST% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java "-Dsweep.regionSets=us" "-Dsweep.caps=56,84,140,350" ^
    "-Dexec.args=data results/forecast/us_fc_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ================= forecast robustness pass complete =================
dir /b results\forecast\*.csv
echo.
echo Now summarise across seeds:
echo     python scripts\analyze_forecast.py results\forecast
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
