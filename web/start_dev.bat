@echo off
REM ==========================================
REM  前端开发服务器启动脚本
REM  自动清理 .next 缓存，避免生产构建污染
REM ==========================================
echo [1/3] 清理 .next 缓存...
if exist ".next" rd /s /q ".next"

echo [2/3] 类型检查 (tsc --noEmit)...
call npx tsc --noEmit 2>nul
if %errorlevel% neq 0 (
    echo ⚠️  类型检查有警告，但继续启动...
)

echo [3/3] 启动 Next.js Dev Server...
npx next dev -p 3000
