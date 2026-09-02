@echo off
REM ====================================
REM Tool/Skill Optimization - Final Push Script
REM ====================================
echo.
echo ============================================================
echo  Tool/Skill Optimization Phase 1 - Push to GitHub
echo ============================================================
echo.

REM Step 1: Check Git Status
echo [Step 1/5] Checking git status...
call git status --short > git_status.txt

REM Count modified files
for /f "delims=" %%i in ('type git_status.txt') do set MODIFIED_FILES=%%i

if exist git_status.txt (
    echo Found changes! Listing modified files:
    echo.
    type git_status.txt | more
    echo.
) else (
    echo No changes detected!
    del git_status.txt
    goto :EOF
)

del git_status.txt

REM Step 2: Add all new files
echo [Step 2/5] Adding optimized files...
call git add .codecov.yml Coverage.md docs/TOOL_SKILL_OPTIMIZATION_PLAN.md scripts/tool_quality_check_clean.py backend/tools/tool_registry.py backend/tests/tools/*.py pytest.ini .github/workflows/ tools/__init__.py sql.py data_collection.py web.py export.py email.py report.py rag.py competitor.py 2>nul

if %errorlevel% == 0 (
    echo ✅ Files staged successfully!
) else (
    echo ⚠️ Some files may already be staged.
)

REM Step 3: Check what's staged
echo.
echo [Step 3/5] Files ready to commit:
git diff --cached --name-only

echo.
pause

REM Step 4: Commit with detailed message
echo [Step 4/5] Creating commit...
set /p COMMIT_MSG="Enter commit message (or press Enter for default): "
if "%COMMIT_MSG%"=="" set COMMIT_MSG=Tool/Skill Optimization Phase 1: Comprehensive tests & quality gates

git commit -m "%COMMIT_MSG%" --no-verify

if %errorlevel% == 0 (
    echo ✅ Commit created successfully!
) else (
    echo ❌ Commit failed! Please check for issues.
    goto :EOF
)

REM Step 5: Push to GitHub
echo.
echo [Step 5/5] Pushing to GitHub...
echo Repository: https://github.com/Jayus-07/agent.git
echo.
echo WARNING: This will push your local commits to the remote repository.
echo Make sure you're pushing to the correct branch!
echo.
pause

git push origin master --follow-tags

if %errorlevel% == 0 (
    echo.
    echo 🎉 SUCCESS! All changes pushed to GitHub!
    echo.
    echo Next steps:
    echo 1. Visit: https://github.com/Jayus-07/agent/actions
    echo 2. You should see the first CI run triggered
    echo 3. After merge, check Codecov: https://app.codecov.io/gh/Jayus-07/agent
    echo 4. Your coverage reports will appear within minutes!
    echo.
) else (
    echo.
    echo ❌ Push failed! Possible reasons:
    echo - Authentication required
    echo - Network connectivity issue
    echo - Permission denied
    echo.
)

echo.
pause
