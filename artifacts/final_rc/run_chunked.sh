#!/bin/bash
# Final RC 分块全量回归驱动 v3（STOP A baseline 用）
# v1 教训：Windows 上 GNU timeout 的 KILL 信号不生效，挂死块无人管。
# v2 教训：驱动 stdout 管道断裂/summary 写入 Permission denied 会杀死整个循环
#         ——启动时必须把 stdout/stderr 指向 /dev/null，summary 写入全部容错。
# v3：后台启动 + 停滞检测（log 8 分钟无增长判挂）+ 硬上限 25 分钟，
#     判挂后 PowerShell 按 cmdline 杀整树（含 -u -c 孤儿 worker），重试一次。
set -u
cd "/d/Program Files/workplace/agent"
OUT=artifacts/final_rc/chunks
mkdir -p "$OUT"
PY=./.venv/Scripts/python.exe
STALL_LIMIT=480     # 8 分钟无输出判挂
HARD_LIMIT=1500     # 单块硬上限 25 分钟
FROM="${1:-}"

note() { { echo "$*" >> "$OUT/summary.txt"; } 2>/dev/null || true; }

kill_pytest_tree() {
  powershell -NoProfile -Command "
    Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
    Where-Object { \$_.CommandLine -match 'pytest|exec.eval' } |
    ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }" 2>/dev/null
  return 0
}

CHUNKS=(
"cs:backend/tests/customer_service"
"rag:backend/tests/rag"
"orch:backend/tests/orchestration"
"travel:backend/tests/travel backend/tests/domain_runtime"
"api:backend/tests/api"
"eval:backend/tests/evaluation backend/tests/eval backend/tests/prompts"
"sql:backend/tests/sql backend/tests/test_sql_user_context.py"
"infra:backend/tests/infra backend/tests/config backend/tests/observability backend/tests/audit backend/tests/skills"
"tools:backend/tests/tools backend/tests/tool_runtime backend/tests/services backend/tests/workers backend/tests/messaging backend/tests/agents backend/tests/inventory"
"platform:backend/tests/context_budget backend/tests/security backend/tests/selection_funnel backend/tests/selection_decision backend/tests/memory"
"root-a:backend/tests/test_a*.py backend/tests/test_b*.py backend/tests/test_c*.py backend/tests/test_e*.py"
"root-b:backend/tests/test_f*.py backend/tests/test_g*.py backend/tests/test_h*.py backend/tests/test_i*.py backend/tests/test_l*.py backend/tests/test_m*.py"
"root-c:backend/tests/test_p*.py backend/tests/test_r*.py backend/tests/test_s*.py"
"root-d:backend/tests/test_t*.py backend/tests/test_u*.py backend/tests/test_w*.py tests"
)

skipping=$([ -n "$FROM" ] && echo 1 || echo 0)
for entry in "${CHUNKS[@]}"; do
  name="${entry%%:*}"
  paths="${entry#*:}"
  if [ "$skipping" = 1 ]; then
    [ "$name" = "$FROM" ] && skipping=0 || { continue; }
  fi
  note "=== CHUNK $name START $(date +%T) ==="
  final=""
  for attempt in 1 2; do
    env PYTHONPATH=backend PGPORT=5433 PYTHONUTF8=1 \
      "$PY" -m pytest -q --tb=short -n 4 --continue-on-collection-errors \
      $paths > "$OUT/$name.log" 2>&1 &
    pid=$!
    last=-1; stall=0; start=$(date +%s)
    while kill -0 $pid 2>/dev/null; do
      sleep 20
      now=$(date +%s)
      size=$(stat -c %s "$OUT/$name.log" 2>/dev/null || echo 0)
      if [ "$size" = "$last" ]; then
        stall=$((stall+20))
      else
        stall=0; last=$size
      fi
      if [ $stall -ge $STALL_LIMIT ] || [ $((now-start)) -ge $HARD_LIMIT ]; then
        kill_pytest_tree
        { kill -9 $pid; } 2>/dev/null
        sleep 2
        kill_pytest_tree
        break
      fi
    done
    { wait $pid; } 2>/dev/null
    rc=$?
    kill_pytest_tree
    tail_line=$(grep -oE "[0-9]+ failed(, [0-9]+ passed.*)?(|[0-9]+ passed[^=]*) in [0-9.]+s" "$OUT/$name.log" | tail -1)
    [ -z "$tail_line" ] && tail_line=$(grep -E "passed|no tests ran" "$OUT/$name.log" | tail -1 | head -c 120)
    if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then
      note "CHUNK $name attempt$attempt RC=$rc | $tail_line"
      final="done"
      break
    else
      note "CHUNK $name attempt$attempt RC=$rc (STALL/TIMEOUT/KILLED)"
      final="hang"
      sleep 3
    fi
  done
  [ "$final" = "hang" ] && note "CHUNK $name FINAL=HANG"
done
note "=== ALL CHUNKS DONE $(date +%T) ==="
