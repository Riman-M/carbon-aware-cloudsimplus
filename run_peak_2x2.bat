@echo off
REM ===================================================================
REM  Separating VM duration from VM count as the driver of the peak.
REM
REM  Run from the project root in cmd.exe.
REM
REM  WHY THIS RUN EXISTS
REM  run_peak.bat refuted the duration prediction, but not cleanly. It
REM  moved three things at once: median duration (8h -> mean 63h), VM
REM  count (1000 -> 540, to hold demand) and demand itself (+11.4%).
REM  The mechanism is crowding, that is, how many planners compete for the
REM  same hours, so VM count is a plausible driver on its own, and
REM  that run cannot tell the two apart.
REM
REM  THE 2x2
REM  Two cells already exist:
REM    A  short duration, 1000 VMs  -> peak +21%, a real effect
REM    B  long  duration,  540 VMs  -> no resolvable peak at all
REM  This run adds the two missing corners:
REM    C  long  duration, 1000 VMs  (count held at A, duration as B)
REM    D  short duration,  540 VMs  (duration as A, count held at B)
REM
REM  HOW TO READ IT
REM    C flat and D peaked  -> DURATION is the driver. The effect needs
REM                            VMs short against the 24h diurnal period;
REM                            a VM averaging 63h spans ~2.6 cycles, so
REM                            the diurnal structure averages out over
REM                            its run and no start hour is much better
REM                            than another.
REM    C peaked and D flat  -> VM COUNT is the driver, i.e. the effect is
REM                            about contention among many simultaneous
REM                            planners, not about duration at all.
REM    both flat            -> count is necessary; duration may be too.
REM    both peaked          -> neither alone explains it; look elsewhere.
REM
REM  Demand cannot be held fixed while both duration and count are
REM  controlled, because the three are arithmetically linked. This design holds
REM  count and lets demand float, which is the right trade for a
REM  contention mechanism. Cell C runs about 63,000 VM-hours, i.e. 25%
REM  utilisation at cap=350 against A's 12%; still slack, but note it
REM  when reading C, and if C comes out flat check that its violation is
REM  flat rather than merely smaller.
REM
REM  Margins, capacity, season and seeds are identical to run_peak.bat so
REM  all four cells are directly comparable.
REM
REM  Roughly 50 minutes.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set BASE=--out data --hours 720 --deadline-margin 48 --start-hour 13128 --forecast --lead-hour 24
set VM=data\AzureVMTraces\vmtable_with_header.csv
set CELLS=-Dsweep.regionSets=us -Dsweep.caps=350 -Dsweep.margins=12,18,24,25,26,27,28,29,30

if not exist results\peak_longcount mkdir results\peak_longcount
if not exist results\peak_shortfew mkdir results\peak_shortfew

echo.
echo ######## cell C: long duration, count held at 1000 ########
for %%s in (1 2 3 4 5) do (
  echo.
  echo ---- seed %%s ----
  %PREP% --mode real --carbon-src %CC% --region-set us --vm-src %VM% --min-duration-h 8 ^
    --requests 1000 %BASE% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/peak_longcount/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ######## cell D: short duration, count held at 540 ########
for %%s in (1 2 3 4 5) do (
  echo.
  echo ---- seed %%s ----
  %PREP% --mode real --carbon-src %CC% --region-set us --vm-src %VM% --min-duration-h 2 ^
    --requests 540 %BASE% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %CELLS% "-Dexec.args=data results/peak_shortfew/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ===================== 2x2 pass complete =====================
echo.
echo     python scripts\analyze_resonance.py results\peak_longcount
echo     python scripts\analyze_resonance.py results\peak_shortfew
echo.
echo In each, read the worst-margin line under Q2. Compare against the two
echo cells you already have: peak_short was +21.04 at margin 28, peak_long
echo had no resolvable peak. A cell counts as peaked only if its best
echo margin clears the neighbouring margins by more than their spread.
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1