@echo off
REM ===================================================================
REM  Locating the resonance peak, and testing what sets its position.
REM
REM  Run from the project root in cmd.exe.
REM
REM  WHAT IS OPEN
REM  The resonance sweep established the defect and its mechanism: in
REM  July at cap=350 the perfect-foresight planner's emissions, temporal
REM  crowding and realised carbon intensity all peak at deadline margin
REM  27, the violation reaches +28.85%, and the peak collapses when the
REM  diurnal cycle flattens in December. What is NOT explained is why 27
REM  rather than 24, the diurnal period itself. A reviewer will ask.
REM
REM  TWO THINGS THIS RUN DOES
REM
REM  1. Resolves the peak. The main sweep sampled 24 then 27 then 30, so
REM     the peak's position is known only to +/-3h and its shape not at
REM     all. Margins 24..29 at 1h steps settle both. 12 and 18 are kept
REM     as anchors so the violation statistic -- which compares each
REM     margin against the best SHORTER margin in its own series -- has
REM     the same baseline as the main sweep and the numbers stay
REM     comparable to it.
REM
REM  2. Tests the duration account. The planner minimises MEAN carbon
REM     intensity over a VM's whole run window, not the CI at its start
REM     instant. So to sit a VM of duration D inside the daily trough,
REM     the reachable start set plausibly needs the diurnal period PLUS
REM     room to fit D -- which would put the peak above 24 and make its
REM     position depend on D.
REM
REM     THE PREDICTION, stated before the run: with a longer-duration
REM     workload the peak must move to a LARGER margin. If it stays at
REM     27, duration is not what sets the offset and the explanation
REM     must be sought elsewhere (the planner's tie-breaking among equal
REM     windows, or the shape of the trough rather than its period).
REM
REM     --min-duration-h 8 raises the median VM from 8h to about 20h.
REM     --requests is cut to 540 to hold total VM-hours near the
REM     baseline's ~30,400, because otherwise the longer workload would
REM     also make the cell tighter and confound duration with capacity
REM     pressure. CHECK THIS: the prep step prints "demand=... VM-hours"
REM     for both variants. They should land within a few percent of each
REM     other. If they do not, adjust --requests on the long variant and
REM     re-run that half -- the comparison is only clean at matched
REM     demand.
REM
REM  Only cap=350 and only July: that is the cell carrying the effect
REM  (+20.8% at the peak against +3.1% at cap=140), and the point here
REM  is to resolve a peak already known to live there, not to re-survey.
REM
REM  Roughly 70 minutes. Writes two directories, each analysed on its
REM  own with the existing script.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set BASE=--out data --hours 720 --deadline-margin 48 --start-hour 13128 --forecast --lead-hour 24
set VM=data\AzureVMTraces\vmtable_with_header.csv
set CELLS=-Dsweep.regionSets=us -Dsweep.caps=350 -Dsweep.margins=12,18,24,25,26,27,28,29,30

if not exist results\peak_short mkdir results\peak_short
if not exist results\peak_long mkdir results\peak_long

echo.
echo ################ baseline duration - median about 8h ################
for %%s in (1 2 3 4 5) do (
  echo.
  echo ---- seed %%s ----
  %PREP% --mode real --carbon-src %CC% --region-set us --vm-src %VM% --min-duration-h 2 ^
    --requests 1000 %BASE% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/peak_short/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ################ long duration - median about 20h ################
for %%s in (1 2 3 4 5) do (
  echo.
  echo ---- seed %%s ----
  %PREP% --mode real --carbon-src %CC% --region-set us --vm-src %VM% --min-duration-h 8 ^
    --requests 540 %BASE% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/peak_long/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ===================== peak-location pass complete =====================
echo.
echo Compare the two peak positions:
echo     python scripts\analyze_resonance.py results\peak_short
echo     python scripts\analyze_resonance.py results\peak_long
echo.
echo In each, read the worst-margin line under Q2 and the peaks line under
echo Q4. Q3 will report that it needs both seasons; that is expected, this
echo run is July only and the seasonal test was settled by run_resonance.
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
