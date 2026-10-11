@echo off
REM ===================================================================
REM  Fast correctness check for the mitigation planners.
REM
REM  Run from the project root in cmd.exe. Takes about three seconds.
REM
REM  WHAT IT CHECKS
REM  The degenerate control: a congestion price of zero must leave the
REM  objective untouched, so space+time+price(0.00) has to place every
REM  virtual machine exactly where space+time places it. If it does
REM  not, the two planners are not enumerating the same candidate set,
REM  or the penalty term is being applied when lambda is zero, and the
REM  80-minute sweep in run_mitigation.bat would measure that fault
REM  rather than the mitigation.
REM
REM  It also reproduces the deadline-margin non-monotonicity on a
REM  synthetic diurnal trace, so the mitigations are exercised against
REM  the defect rather than against nothing. Those numbers are a sanity
REM  check and not a result: the synthetic defect is about +1.7 percent
REM  against +28.9 on the real CarbonCast trace.
REM
REM  WHAT YOU SHOULD SEE
REM    space+time vs price 0.00 : IDENTICAL, 0 of 11000 placements differ
REM    space+time vs spread 0   : about 3930 of 11000 differ
REM
REM  The second is expected, not a failure. A zero tolerance band still
REM  re-breaks ties by occupancy, so the placements legitimately move.
REM
REM  This compiles only the four classes the planners depend on, none
REM  of which needs CloudSim Plus, so it works even when the full build
REM  cannot resolve its dependencies.
REM ===================================================================

setlocal
set OUT=target\harness
set SRC=src\main\java\org\carbonaware

if not exist %OUT% mkdir %OUT%

echo.
echo [1/3] compiling the planner subsystem
javac -d %OUT% ^
  %SRC%\core\Model.java ^
  %SRC%\core\RegionLedger.java ^
  %SRC%\trace\CarbonTrace.java ^
  %SRC%\policy\Planners.java
if errorlevel 1 goto :failed

echo [2/3] compiling the harness
javac -cp %OUT% -d %OUT% tools\MitigationHarness.java
if errorlevel 1 goto :failed

echo [3/3] running
echo.
java -cp %OUT% MitigationHarness
if errorlevel 1 goto :failed

echo.
echo ===================== harness complete =====================
echo.
echo Check the control line first. If it reports IDENTICAL, the
echo planners are sound and run_mitigation.bat is safe to start.
goto :eof

:failed
echo.
echo *** FAILED at the step above. ***
exit /b 1
