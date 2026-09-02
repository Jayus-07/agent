# 🧪 P2 优化测试执行脚本 (PowerShell)

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "🔍 P2 优化自动化测试执行" -ForegroundColor Yellow
Write-Host "========================================`n" -ForegroundColor Cyan

# 设置环境变量
$env:PYTHONPATH = "d:\Program Files\workplace\agent"

# 测试配置
$TEST_FILE = "tests\rag\test_optimizations_p2.py"
$VERIFY_SCRIPT = "scripts\verify_faq_classification.py"

# 检查 Python 是否存在
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Python not found. Please install Python first." -ForegroundColor Red
    exit 1
}

# 测试 1: FAQ 分类准确性
Write-Host "`n【测试 1】FAQ 分类准确性验证..." -ForegroundColor Green
Write-Host "运行脚本：$VERIFY_SCRIPT" -ForegroundColor White

try {
    $result = python $VERIFY_SCRIPT 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 分类准确性测试通过!" -ForegroundColor Green
        Write-Host $result -ForegroundColor Gray
    } else {
        Write-Host "⚠️  测试执行遇到问题（可能是环境问题）" -ForegroundColor Yellow
        Write-Host "建议：先运行 .\activate_python.bat 激活虚拟环境后再试`n"
    }
} catch {
    Write-Host "[ERROR] $_" -ForegroundColor Red
}

# 测试 2: MinHash 缓存功能
Write-Host "`n【测试 2】MinHash 缓存机制验证..." -ForegroundColor Green
pytest_cmd = "pytest tests/rag/test_optimizations_p2.py::TestMinHashCache -v --tb=short"

Write-Host "运行命令：$pytest_cmd" -ForegroundColor White

try {
    $output = Invoke-Expression $pytest_cmd 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ MinHash 缓存测试通过!" -ForegroundColor Green
    } else {
        Write-Host "⚠️  MinHash 测试未完全通过" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERROR] pytest not found. Run: pip install pytest pytest-asyncio" -ForegroundColor Red
}

# 测试 3: LLM 并行性能
Write-Host "`n【测试 3】LLM 并行执行性能测试..." -ForegroundColor Green
pytest_cmd = "pytest tests/rag/test_optimizations_p2.py::TestLLMParallelPerformance -v --tb=short"

Write-Host "运行命令：$pytest_cmd" -ForegroundColor White

try {
    $output = Invoke-Expression $pytest_cmd 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ LLM 并行性能测试通过!" -ForegroundColor Green
    } else {
        Write-Host "⚠️  LLM 性能测试未完全通过" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[ERROR] pytest not found" -ForegroundColor Red
}

# 总结
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "📊 测试结果汇总" -ForegroundColor Yellow
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "✅ 所有脚本已准备就绪！" -ForegroundColor Green
Write-Host "接下来请手动验证：`n" -ForegroundColor White

Write-Host "步骤 1: 运行 .\activate_python.bat 激活虚拟环境" -ForegroundColor Cyan
Write-Host "步骤 2: 执行 python scripts/verify_faq_classification.py" -ForegroundColor Cyan  
Write-Host "步骤 3: 查看日志确认 FAQ 分类为 faq 而非 financial" -ForegroundColor Cyan
Write-Host "`n预期输出示例:" -ForegroundColor Gray
Write-Host "   分类结果：faq" -ForegroundColor Green
Write-Host "   置信度：75.00%" -ForegroundColor Green
Write-Host "   ✅ 正确识别为 faa（而非 financial）`n" -ForegroundColor Green
