@echo off
REM ===================================================================
REM  Does pricing the capacity constraint remove the non-monotonicity?
REM
REM  Run from the project root in cmd.exe.
REM
REM  WHY THIS RUN EXISTS
REM  The paper currently reports a defect and proposes untested fixes.
REM  A reviewer will ask for one of them to be evaluated, and the
REM  evaluation is also the strongest available confirmation of the
REM  mechanism claim: if pricing contention removes the effect, then
REM  the effect was contention.
REM
REM  WHAT IS BEING TESTED
REM  Two online mitigations, both drop-in replacements for space+time
REM  that use only ledger state available at decision time.
REM
REM    space+time+price    minimises mean CI PLUS lambda times a
REM                        congestion term, so a slot that is nearly
REM                        full costs more than an empty one of the
REM                        same cleanliness. lambda is dimensionless:
REM                        at 0.5 a saturated window is charged as
REM                        though its grid were half the average
REM                        intensity dirtier than it is.
REM
REM    space+time+spread   admits only slots within a tolerance of the
REM                        cleanest, then takes the least loaded among
REM                        them. It never accepts a materially dirtier
REM                        hour, so if it works the defect is caused by
REM                        arbitrary tie-breaking inside a band of
REM                        near-equal hours and no carbon has to be
REM                        surrendered to fix it.
REM
REM  TWO CONTROLS ARE IN THE SWEEP ON PURPOSE
REM    price lambda=0   must be bit-identical to space+time. Verified
REM                     already in a standalone harness over 11,000
REM                     placements; the analysis re-checks it here
REM                     against the real trace and the real simulator.
REM    spread tol=0     reproduces space+time up to tie-breaking only.
REM                     Expect a small difference, not zero. It is the
REM                     reference against which the 5 and 20 percent
REM                     bands are read.
REM
REM  WHAT TO EXPECT, stated before the run
REM  On a synthetic diurnal trace the defect shrank from +1.66 to +0.09
REM  at lambda 0.5, modal start-hour share fell from 0.296 to 0.104,
REM  and realised carbon intensity FELL rather than rose at lambda 0.25
REM  and 0.5, turning up only at lambda 1 and above. If that pattern
REM  holds on the real trace, the finding is that a small congestion
REM  price is free: it removes the non-monotonicity and lowers
REM  emissions at the same time. If instead realised CI rises at every
REM  lambda that fixes the defect, the finding is a genuine trade-off
REM  and the paper reports the curve rather than a recommendation.
REM  The synthetic defect was only +1.66 against +28.85 on real data,
REM  so treat the above as a direction, not a prediction of magnitude.
REM
REM  Margins, capacity, season, seeds and trace window are identical to
REM  run_peak.bat, so the new policy curves drop straight onto Table I
REM  and Figure 1 without re-running anything.
REM
REM  Thirteen policies against the four in run_peak.bat, perfect
REM  foresight only. Roughly 80 minutes.
REM ===================================================================

setlocal enabledelayedexpansion
set PREP=python scripts\prep_traces.py
set CC=data\carboncast
set BASE=--out data --hours 720 --deadline-margin 48 --start-hour 13128 --forecast --lead-hour 24
set VM=data\AzureVMTraces\vmtable_with_header.csv
set AXES=-Dsweep.regionSets=us -Dsweep.caps=350 -Dsweep.margins=12,18,24,25,26,27,28,29,30
set ARMS=-Dsweep.foresight=perfect
set MITIG=-Dsweep.price=0,0.1,0.25,0.5,1.0,2.0 -Dsweep.spread=0,5,20

if not exist results\mitigation mkdir results\mitigation

echo.
echo ######## congestion-priced and spread planners, July, cap 350 ########
for %%s in (1 2 3 4 5) do (
  echo.
  echo ---- seed %%s ----
  %PREP% --mode real --carbon-src %CC% --region-set us --vm-src %VM% --min-duration-h 2 ^
    --requests 1000 %BASE% --seed %%s
  if errorlevel 1 goto :failed
  call mvn -q exec:java %AXES% %ARMS% %MITIG% "-Dexec.args=data results/mitigation/jul_seed%%s.csv"
  if errorlevel 1 goto :failed
)

echo.
echo ===================== mitigation pass complete =====================
echo.
echo     python scripts\analyze_mitigation.py results\mitigation
echo.
echo The script checks the lambda=0 control first and stops if it fails,
echo because a control that moved means the planner is reading state the
echo baseline does not and every other number in the run is suspect.
echo.
echo Then read three things, in order:
echo   1. whether max violation falls to within seed noise for some lambda
echo   2. what realised carbon intensity does at that lambda, which says
echo      whether the fix is free or a trade
echo   3. whether modal start-hour share and peak-to-mean occupancy fall
echo      with it, which says the fix worked through the claimed channel
echo      rather than by accident
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
