@echo off
REM ===================================================================
REM  Region-set composition experiment (revision, Reviewer 1 pt 3,
REM  Reviewer 3 pt 1, editor concern 1). Run from the project root in cmd.exe.
REM
REM  Replaces the two-set contrast of the submitted manuscript with 30
REM  five-region subsets of the 13 CarbonCast regions, chosen by
REM  scripts\make_subsets.py. Region count, workload, hardware and capacity
REM  utilisation are identical across subsets; only composition varies.
REM
REM  Phase 1 (core):   round-robin, space, space+time
REM                    4 capacities x 48 h margin x 30 subsets x 3 seeds
REM  Phase 2 (origin): WaitAwhile at every origin of every subset
REM                    2 capacities x 48 h margin x 30 subsets x 3 seeds
REM
REM  Usage:  run_subsets.bat          both phases, roughly 3.5 hours
REM          run_subsets.bat core     phase 1 only, roughly 2 hours
REM
REM  The fleet is sized from 350 total slots (-Dsweep.fleetTotal=350), the
REM  largest cap run, so that no region is asked to hold more concurrent
REM  VMs than its hosts were provisioned for. See run_fidelity.ps1.
REM
REM  Capacities 55,85,140,350 split evenly over five regions (11,17,28,70
REM  per region) give the same utilisations as the EU set: 77, 50, 30, 12 %.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set AZURE=--vm-src data\AzureVMTraces\vmtable_with_header.csv --min-duration-h 2
set BASE=--hours 720 --requests 1000 --deadline-margin 48

if not exist data\subsets\subsets.csv (
  python scripts\make_subsets.py --carbon-src %CC%
  if errorlevel 1 goto :failed
)
if not exist results\subsets mkdir results\subsets

for %%s in (1 2 3) do (
  echo.
  echo ############ seed %%s : sampling workload ############
  %PREP% --mode real --carbon-src %CC% --region-set eu %AZURE% %BASE% --seed %%s --out data\wl_seed%%s
  if errorlevel 1 goto :failed

  for /d %%d in (data\subsets\sub*) do (
    copy /y data\wl_seed%%s\vm_requests.csv %%d\vm_requests.csv >nul
    echo.
    echo ---- seed %%s : %%~nxd : core ----
    call mvn -q exec:java "-Dsweep.regionSets=%%~nxd" "-Dsweep.caps=55,85,140,350" ^
      "-Dsweep.margins=48" "-Dsweep.fleetTotal=350" "-Dsweep.policies=round-robin,space,space+time" ^
      "-Dexec.args=%%d results/subsets/%%~nxd_core_seed%%s.csv"
    if errorlevel 1 goto :failed

    if /i not "%1"=="core" (
      echo ---- seed %%s : %%~nxd : origin ----
      call mvn -q exec:java "-Dsweep.regionSets=%%~nxd" "-Dsweep.caps=85,350" ^
        "-Dsweep.margins=48" "-Dsweep.fleetTotal=350" "-Dsweep.policies=time" "-Dsweep.homeRegion=all" ^
        "-Dexec.args=%%d results/subsets/%%~nxd_origin_seed%%s.csv"
      if errorlevel 1 goto :failed
    )
  )
)

echo.
echo ================= region-subset sweep complete =================
dir /b results\subsets\*.csv | find /c ".csv"
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
