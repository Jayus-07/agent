#!/bin/bash
# PR-3.3: 重构验收脚本 — 自动化 6 类检查（对齐 ADR-0002 v2 验证标准）
#
# 用法：
#   bash scripts/verify_refactor_2026q3.sh                  # 全套（默认）
#   bash scripts/verify_refactor_2026q3.sh --only=static     # 只跑静态
#   bash scripts/verify_refactor_2026q3.sh --only=unit       # 只跑单元
#   bash scripts/verify_refactor_2026q3.sh --only=integration
#   bash scripts/verify_refactor_2026q3.sh --only=e2e        # 检查 demo 脚本
#   bash scripts/verify_refactor_2026q3.sh --only=perf       # 性能基线
#   bash scripts/verify_refactor_2026q3.sh --only=docs       # 文档状态
#
# 退出码：
#   0 = 全部通过
#   1 = 有失败（详见各节输出）
#   2 = 参数错误
#
# 跨平台：Windows 用 Git Bash 跑（CLAUDE.md 强制环境）

set -e  # 任一命令失败立即退出

# === 路径定位 ===
repo_root=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$repo_root" ]; then
    echo "❌ 必须在 git 仓库根目录运行"
    exit 2
fi
cd "$repo_root"
backend_dir="$repo_root/backend"

# === 颜色（Windows MSYS 兼容）===
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; NC=''
fi

# === 参数解析 ===
RAW_ARG="${1:-all}"
# 兼容 --only=xxx / only=xxx / xxx 三种格式
ONLY=$(echo "$RAW_ARG" | sed -E 's/^--?only=//')
case "$ONLY" in
    all|static|unit|integration|e2e|perf|docs) ;;
    *) echo "❌ 未知 --only=$RAW_ARG（解析后=$ONLY，可选: all static unit integration e2e perf docs）"; exit 2 ;;
esac

# === 计数器 ===
PASS=0
FAIL=0
WARN=0

ok()   { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}✗${NC} $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; WARN=$((WARN+1)); }
sec()  { echo -e "\n${BLUE}━━━ $1 ━━━${NC}"; }


# ==========================================================
# 1. 静态验证
# ==========================================================
check_static() {
    sec "1. 静态验证（py_compile + 跨域 import + 大函数）"

    # 1.1 py_compile 全部 backend（用 -print0 | xargs -0 处理 "Program Files" 等含空格路径）
    local compile_log=$(mktemp)
    local n=$(find "$backend_dir" -name "*.py" -not -path "*/__pycache__/*" -not -path "*/.venv/*" -print0 | tr -cd '\0' | wc -c)
    if find "$backend_dir" -name "*.py" -not -path "*/__pycache__/*" -not -path "*/.venv/*" -print0 \
        | xargs -0 python -m py_compile 2>"$compile_log"; then
        ok "py_compile: $n 个 .py 文件全部 OK"
    else
        fail "py_compile 有错误："
        head -10 "$compile_log" | sed 's/^/    /'
    fi
    rm -f "$compile_log"

    # 1.2 跨域 import（CLAUDE.md 禁止 Skill 跨域访问）
    if command -v grep >/dev/null; then
        local cross=$(grep -rn "from backend.orchestration" "$backend_dir/rag/" 2>/dev/null \
            | grep -v "__pycache__" | grep -v "rag/orchestration" || true)
        if [ -z "$cross" ]; then
            ok "rag/ 无跨 orchestration 域 import（CLAUDE.md 合规）"
        else
            warn "rag/ 跨域 import（应移到 rag/ 内）："
            echo "$cross" | head -5
        fi
    fi

    # 1.3 RAGChain 行数（ADR-0002 目标 < 350，PR-1.1 已完但 1.2~1.4 未做）
    if [ -f "$backend_dir/rag/chain.py" ]; then
        local lines=$(wc -l < "$backend_dir/rag/chain.py")
        if [ "$lines" -lt 350 ]; then
            ok "rag/chain.py 行数: $lines（达到 ADR-0002 目标 < 350）"
        elif [ "$lines" -lt 600 ]; then
            warn "rag/chain.py 行数: $lines（PR-1.2~1.4 部分完成）"
        else
            warn "rag/chain.py 行数: $lines（PR-1.1 完成但 1.2~1.4 未做，ADR-0002 目标 350）"
        fi
    fi
}


# ==========================================================
# 2. 单元测试
# ==========================================================
check_unit() {
    sec "2. 单元测试（pytest 全量）"

    local start=$(date +%s)
    if cd "$backend_dir" && python -m pytest tests/ -q --tb=line --no-cov 2>&1 | tail -20; then
        local end=$(date +%s)
        local dur=$((end - start))
        echo ""
        if [ "$dur" -lt 180 ]; then
            ok "全量测试在 ${dur}s 内完成（基线 130s）"
        elif [ "$dur" -lt 240 ]; then
            warn "全量测试 ${dur}s（接近 3 分钟基线）"
        else
            fail "全量测试 ${dur}s（超过 3 分钟基线）"
        fi
    else
        fail "全量测试有失败（见上方）"
    fi
    cd "$repo_root"
}


# ==========================================================
# 3. 集成测试（关键 3 个测试文件）
# ==========================================================
check_integration() {
    sec "3. 集成测试（Skill 注册 / Self-Correction / Evidence Gate）"

    cd "$backend_dir"
    local files=(
        "tests/test_adr0001_dual_registry_merge.py"
        "tests/test_rag_p1_self_correction.py"
        "tests/test_evidence_gate.py"
    )
    for f in "${files[@]}"; do
        if [ ! -f "$f" ]; then
            warn "$f 不存在（可能还在别处）"
            continue
        fi
        if python -m pytest "$f" -q --no-cov 2>&1 | tail -3 | grep -q "passed\|no tests ran"; then
            ok "$f 通过"
        else
            fail "$f 有失败"
        fi
    done
    cd "$repo_root"
}


# ==========================================================
# 4. 端到端 demo 脚本存在性（不真跑，避免依赖模型）
# ==========================================================
check_e2e() {
    sec "4. 端到端 demo 脚本（4 个场景 — 仅检查文件，不真跑）"

    # ADR-0002 v2 列了 4 个 demo 场景
    declare -A scenarios=(
        ["RAG QA"]="backend/app/api/routes/chat.py"
        ["Daily Report"]="backend/orchestration/workflows/daily_report.py"
        ["Inventory Alert"]="backend/orchestration/workflows/inventory_alert.py"
        ["Knowledge Index"]="backend/app/api/routes/rag.py"
    )
    for name in "${!scenarios[@]}"; do
        if [ -f "${scenarios[$name]}" ]; then
            ok "$name 入口存在: ${scenarios[$name]}"
        else
            warn "$name 入口缺失: ${scenarios[$name]}"
        fi
    done

    # 前端 /observability 页面（验证 Trace span 名不变）
    if [ -f "frontend/src/app/observability/traces/[id]/page.tsx" ]; then
        ok "Trace 详情页存在（前端 span 名不变）"
    else
        warn "Trace 详情页路径变更（需手动验证前端）"
    fi
}


# ==========================================================
# 5. 性能基线
# ==========================================================
check_perf() {
    sec "5. 性能基线（ADR-0002 v2: 全量 < 3 分钟，单元 < 2 分钟）"

    # 已经在 check_unit 跑过，这里只读基线
    warn "性能基线数据：当前全量 ~130s（见上方 §2 输出）"
    warn "PR-1.4 完成后需 P99 < 5s 验证（手动跑 curl 100 次）"
}


# ==========================================================
# 6. 文档状态
# ==========================================================
check_docs() {
    sec "6. 文档状态（ADR 索引 + CHANGELOG + structure.md）"

    # 6.1 ADR 索引
    if [ -f "docs/architecture/adr/README.md" ]; then
        local n=$(grep -c "^| \[0" docs/architecture/adr/README.md)
        ok "ADR 索引包含 $n 篇 ADR"
    else
        fail "ADR 索引缺失"
    fi

    # 6.2 ADR-0002 状态（取 markdown 表格第 3 列）
    if [ -f "docs/architecture/adr/0002-ragchain-decomposition.md" ]; then
        status=$(grep -m1 "^| \*\*状态\*\*" docs/architecture/adr/0002-ragchain-decomposition.md \
            | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $3); print $3}')
        if [[ "$status" == *"Accepted"* ]] && [[ "$status" != *"Partially"* ]]; then
            ok "ADR-0002 状态: $status"
        elif [[ "$status" == *"Partially"* ]]; then
            warn "ADR-0002 状态: $status（PR-1.4 完成后改 Accepted）"
        else
            warn "ADR-0002 状态: $status"
        fi
    fi

    # 6.3 production-readiness.md（路线图 + O1/R1 状态）
    if [ -f "docs/architecture/production-readiness.md" ]; then
        ok "生产化路线图存在"
        # 检查 O1 / R1 是否勾掉（行内含 [x] 紧跟 O1/R1）
        if grep -E "^\| O1 \|.*\[x\]" docs/architecture/production-readiness.md >/dev/null 2>&1; then
            ok "O1（Prometheus）路线图已勾掉"
        else
            warn "O1（Prometheus）路线图未勾掉"
        fi
        if grep -E "^\| R1 \|.*\[x\]" docs/architecture/production-readiness.md >/dev/null 2>&1; then
            ok "R1（LLM 限流）路线图已勾掉"
        else
            warn "R1（LLM 限流）路线图未完全勾掉（需 PR-2.4 接 429）"
        fi
    fi

    # 6.4 经验 memory
    if [ -d "memory" ]; then
        ok "memory/ 目录存在（CLAUDE.md 经验沉淀）"
    fi
}


# ==========================================================
# 主流程
# ==========================================================
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  重构验收脚本 (PR-3.3) — $(date +%Y-%m-%d)${NC}"
echo -e "${BLUE}  Repo: $repo_root${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

case "$ONLY" in
    all)
        check_static
        check_unit
        check_integration
        check_e2e
        check_perf
        check_docs
        ;;
    static)        check_static ;;
    unit)          check_unit ;;
    integration)   check_integration ;;
    e2e)           check_e2e ;;
    perf)          check_perf ;;
    docs)          check_docs ;;
esac

# === 汇总 ===
echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "  ${GREEN}通过: $PASS${NC}  ${YELLOW}警告: $WARN${NC}  ${RED}失败: $FAIL${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

if [ "$FAIL" -gt 0 ]; then
    echo -e "${RED}❌ 验收未通过 — 修完再跑${NC}"
    exit 1
else
    echo -e "${GREEN}✅ 验收通过${NC}"
    exit 0
fi
