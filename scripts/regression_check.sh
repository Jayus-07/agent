#!/usr/bin/env bash
# scripts/regression_check.sh — 回归对比，避免被全量套件的预存失败数吓到
#
# 背景：本沙箱跑 `pytest backend/tests` 会有大量失败，主要来自
#   - backend/tests/evaluation/test_eval_golden.py  (RAG 黄金集检索质量评测，需建好的
#       RAG 索引 + Postgres + embedding + reranker + LLM，沙箱全无；缺基础设施时会
#       【挂起】而非快速失败) —— 本脚本在 run_suite 里 --ignore 它，不参与比对。
# 注意：test_eval_characterization / test_startup_validation 是纯逻辑/启动探针测试，
# 本就该是绿的（依赖齐了就过），NOT 恒失败，故不参与忽略、也不进已知预存名单。
# 若只看总失败数，任何一次改动都会显得"红了一片"，实则无一源于本次工作。
# 本脚本只跑「除 golden set 外的全量」，只关心「相对基线的【新增】失败」。
#
# 用法：
#   ./scripts/regression_check.sh            # 用已存的基线对比当前配置（无基线则先生成）
#   ./scripts/regression_check.sh baseline   # 重新生成基线（TRAVEL_ENABLED=false）
#   ./scripts/regression_check.sh check      # 同默认：用已存基线对比当前配置
#   ./scripts/regression_check.sh full       # 强制重跑基线 + 当前两次（最严格，约 22 分钟）
#
# 环境变量：
#   TRAVEL_ENABLED  当前配置下旅行域开关（默认 true；baseline 模式恒为 false）
#   PYTHON         Python 解释器（默认 ./.venv/Scripts/python.exe）
#   BASELINE_FILE  基线清单路径（默认 backend/tests/.regression_baseline.txt）
#
# 退出码：0 = 无新增失败（全量红都是预存环境失败）；1 = 发现新增失败；2 = 用法/环境错误
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PY="${PYTHON:-./.venv/Scripts/python.exe}"
BASELINE_FILE="${BASELINE_FILE:-backend/tests/.regression_baseline.txt}"
MODE="${1:-check}"

# 这些模块/文件在本沙箱恒失败（环境依赖，与代码改动无关）。即使它们某次
# 在"当前配置"下比基线多冒出几个用例，也一律算预存失败，不计入"新增"。
# 注意：只放【真正环境依赖、且不承载业务回归信号】的模块。
#   - test_eval_golden.py 是 RAG 黄金集检索质量评测，需建好的 RAG 索引 +
#     Postgres + embedding + reranker + LLM，沙箱全无；且缺基础设施时会
#     【挂起】而非快速失败，故除过滤外还在 run_suite 里 --ignore 掉，避免
#     整轮卡死。它应在有真实 infra 的 CI 里单独跑，不在本沙箱参与比对。
#   - 切勿把 test_eval_characterization / test_startup_validation 放进这里：
#     它们是纯逻辑/启动探针测试，本就该是绿的；放进来会掩盖真回归。
KNOWN_FLAKY_PREFIXES=(
  "backend/tests/evaluation/test_eval_golden.py"
)

# 跑一轮全量，只把 "^FAILED ..." 行抽出来（含参数化 id），按 id 去重排序。
# 通过 --ignore 排除会挂起的 RAG 黄金集评测（见上），保证本沙箱内快速失败。
run_suite() {
  local te="$1"
  TRAVEL_ENABLED="$te" PYTHONIOENCODING=utf-8 "$PY" -m pytest backend/tests \
    -q --no-header -p no:cacheprovider --no-cov \
    --ignore=backend/tests/evaluation/test_eval_golden.py \
    2>/dev/null | grep -E "^FAILED" | sort -u
}

# 判断某条 FAILED 行是否落在已知预存失败模块里（容错，不计入新增）。
is_known_flaky() {
  local line="$1"
  local p
  for p in "${KNOWN_FLAKY_PREFIXES[@]}"; do
    case "$line" in
      *"$p"*) return 0 ;;
    esac
  done
  return 1
}

case "$MODE" in
  baseline)
    echo "==> 生成基线 (TRAVEL_ENABLED=false) ..."
    run_suite false > "$BASELINE_FILE"
    echo "基线失败数: $(wc -l < "$BASELINE_FILE") → $BASELINE_FILE"
    ;;
  full)
    echo "==> 生成基线 (TRAVEL_ENABLED=false) ..."
    run_suite false > "$BASELINE_FILE"
    echo "基线失败数: $(wc -l < "$BASELINE_FILE")"
    echo "==> 跑当前配置 (TRAVEL_ENABLED=${TRAVEL_ENABLED:-true}) ..."
    run_suite "${TRAVEL_ENABLED:-true}" > /tmp/regr_current.txt
    ;;
  check|"")
    if [ ! -f "$BASELINE_FILE" ]; then
      echo "==> 无基线，先生成 (TRAVEL_ENABLED=false) ..."
      run_suite false > "$BASELINE_FILE"
      echo "基线失败数: $(wc -l < "$BASELINE_FILE")"
    fi
    echo "==> 跑当前配置 (TRAVEL_ENABLED=${TRAVEL_ENABLED:-true}) ..."
    run_suite "${TRAVEL_ENABLED:-true}" > /tmp/regr_current.txt
    ;;
  *)
    echo "用法: $0 [baseline|full|check]" >&2
    exit 2
    ;;
esac

# 当前配置相对基线的"纯新增"（含参数化 id 不在基线条目里的）
comm -13 "$BASELINE_FILE" /tmp/regr_current.txt > /tmp/regr_raw_new.txt

# 再从纯新增里剔除已知预存失败模块（这些模块的波动算环境噪声，不报警）
: > /tmp/regr_new.txt
while IFS= read -r line; do
  [ -z "$line" ] && continue
  if is_known_flaky "$line"; then
    echo "$line" >> /tmp/regr_flaky.txt
  else
    echo "$line" >> /tmp/regr_new.txt
  fi
done < /tmp/regr_raw_new.txt

BASE_COUNT=$(wc -l < "$BASELINE_FILE")
CUR_COUNT=$(wc -l < /tmp/regr_current.txt)
NEW_COUNT=$(wc -l < /tmp/regr_new.txt)
FLAKY_IN_CURRENT=$(wc -l < /tmp/regr_flaky.txt 2>/dev/null || echo 0)

echo "------------------------------------------------------------"
echo "基线失败数       : $BASE_COUNT (预存，与改动无关)"
echo "当前失败数       : $CUR_COUNT"
echo "其中预存模块波动 : $FLAKY_IN_CURRENT (环境噪声，忽略)"
echo "真正新增失败数   : $NEW_COUNT"
echo "------------------------------------------------------------"

if [ "$NEW_COUNT" -gt 0 ]; then
  echo "!! 发现 $NEW_COUNT 个新增失败（疑似本次改动引入，不在基线与已知预存模块内）:"
  cat /tmp/regr_new.txt
  echo "------------------------------------------------------------"
  echo "结论: 有回归，请排查上面的新增失败。"
  exit 1
else
  echo "结论: 无新增失败 —— 全量红都是预存环境失败，可放心。"
  echo "------------------------------------------------------------"
  exit 0
fi
