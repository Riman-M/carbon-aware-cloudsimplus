@echo off
REM ===================================================================
REM  Deadline-margin resonance experiment.
REM
REM  Run from the project root in cmd.exe.
REM
REM  WHAT THIS TESTS
REM  The 5-seed foresight pass turned up a defect in the PERFECT-
REM  foresight planner, not in forecast quality: 33 of 40 (seed, policy,
REM  capacity) series were non-monotonic in deadline margin, almost
REM  always at m=12 -> m=24, reaching +10-12% at cap=350. A planner with
REM  perfect information got WORSE when given a longer deadline, which
REM  cannot be a foresight effect.
REM
REM  THE HYPOTHESIS
REM  A VM may start anywhere in [arrival, arrival+margin] (see
REM  ExperimentRunner.withMargin), so the margin IS the shift freedom.
REM  At margin=24 that window spans exactly one diurnal cycle, so every
REM  VM can reach the same daily CI minimum regardless of when it
REM  arrived, and the greedy planner sends them all to the same hours.
REM  Below 24 the reachable hours depend on arrival time, so the fleet
REM  desynchronises; above 24 several daily minima are reachable and the
REM  load splits between them. Emissions should therefore peak near
REM  margin=24, together with regional concentration.
REM
REM  HOW IT COULD BE FALSIFIED
REM  Resonance needs a sharp, repeatable daily minimum. In the trace,
REM  BPAT swings 35% of its mean across the day in July 2021 but only
REM  10% in December 2021. If the mechanism is what we think, the m=24
REM  peak must be much weaker in December. If it is equally strong in
REM  both, the diurnal explanation is wrong and something else (the
REM  planner's tie-breaking, the horizon edge) is responsible.
REM
REM  DESIGN
REM  * Margins 12..48 sampled finely around 24 (18, 21, 24, 27, 30), so
REM    a peak can be distinguished from a step change.
REM  * cap=350 carries the effect most strongly, cap=140 is the milder
REM    comparison; both run so the peak can be reported against capacity.
REM  * Both foresight arms run. The pathology lives in the perfect arm;
REM    the forecast arm is the contrast that shows noise suppressing it.
REM  * July at 5 seeds (primary), December at 3 (the seasonal control,
REM    which needs less evidence because the predicted contrast is large).
REM  * Both windows sit inside CarbonCast's Jul-Dec 2021 forecast
REM    coverage, so the seasonal control keeps BOTH arms. 13128 is
REM    2021-07-01, 16800 is 2021-12-01.
REM
REM  Roughly 3 hours.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set AZURE=--vm-src data\AzureVMTraces\vmtable_with_header.csv --min-duration-h 2
REM --deadline-margin must be at least the largest swept margin; the sweep
REM rebuilds each request's deadline per cell, so this only sizes the trace.
set BASE=--out data --hours 720 --requests 1000 --deadline-margin 48
set FC=--forecast --lead-hour 24
set CELLS=-Dsweep.regionSets=us -Dsweep.caps=140,350 -Dsweep.margins=12,18,21,24,27,30,36,48

if not exist results\resonance mkdir results\resonance

for %%s in (1 2 3 4 5) do (
  echo.
  echo ######## seed %%s : July 2021 (sharp diurnal cycle) ########
  %PREP% --mode real --carbon-src %CC% --region-set us %AZURE% %BASE% %FC% --start-hour 13128 --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/resonance/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

for %%s in (1 2 3) do (
  echo.
  echo ######## seed %%s : December 2021 (flat diurnal cycle) ########
  %PREP% --mode real --carbon-src %CC% --region-set us %AZURE% %BASE% %FC% --start-hour 16800 --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/resonance/dec_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ================= resonance pass complete =================
dir /b results\resonance\*.csv
echo.
echo Now summarise:
echo     python scripts\analyze_resonance.py results\resonance
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
