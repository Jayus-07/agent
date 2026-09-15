#!/usr/bin/env bash
# run_gateway_test_stack.sh — 启动/重建 9081 网关测试台架（echo 桩 + APISIX 测试实例）
#
# 背景：B2~B3 时代的 9081 启动脚本历史会话未留存（docs/team-prompts/01-member-gateway.md
# 有明确记录），本脚本按 apisix-test/ 配置与 B2 任务日志重建，行为对齐：
#   - echo 桩(8099)：scripts/gateway_echo_stub.py，支持 ?delay=N（limit-conn 测试用）
#   - APISIX 测试实例(9081)：enforce 模式，issuer=hongmeng-oa（12 场景矩阵的合同值），
#     黑名单前缀与生产一致（auth:blacklist:），matrix 场景 9 直接在共享 redis 种子
#   - 测试容器接入 agent_agent-net（与生产同一网络），上游写 host.docker.internal
#
# 用法：
#   bash scripts/run_gateway_test_stack.sh          # 前台启动（Ctrl+C 清理）
#   RUN_MATRIX=1 bash scripts/run_gateway_test_stack.sh   # 起台架后直接跑 12 场景矩阵
#
# 注意（踩坑约定，同 smoke_gateway_auth.sh）：
# 1. 交给 docker 的宿主路径必须先 cygpath -w，否则 Git Bash 产出畸形路径
# 2. 密钥从 .env 读取，不落盘、不进日志
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEST_CONTAINER="agent-apisix-test"
TEST_PORT=9081
STUB_PORT=8099
STUB_PID=""

to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
env_val() { grep "^$1=" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }

cleanup() {
  [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
  docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1
}
trap cleanup EXIT

# ── 前置检查 ────────────────────────────────────────────────
SECRET="$(env_val JWT_SECRET)"
[ -z "$SECRET" ] && { echo "FATAL: .env 缺 JWT_SECRET"; exit 1; }
[ ${#SECRET} -lt 32 ] && { echo "FATAL: JWT_SECRET 长度不足 32 字节"; exit 1; }
# 轮换密钥：.env 有就用（对齐生产双密钥配置），没有则用固定测试值——
# 必须确定性：矩阵 rotation 场景要用同一个 previous 签名（随机值会导致场景 8 假红）
PREVIOUS="$(env_val JWT_SECRET_PREVIOUS)"
[ -z "$PREVIOUS" ] && PREVIOUS="test-previous-secret-0123456789abcdef-0123456789abcdef"

# ── echo 桩（宿主机 8099，网关经 host.docker.internal 访问）──
D:/Python/python.exe "$ROOT/scripts/gateway_echo_stub.py" "$STUB_PORT" &
STUB_PID=$!
sleep 1
kill -0 "$STUB_PID" 2>/dev/null || { echo "FATAL: echo 桩启动失败（8099 被占用？）"; exit 1; }
echo "[stack] echo stub up on :$STUB_PORT (pid $STUB_PID)"

# ── APISIX 测试实例 ─────────────────────────────────────────
docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1
docker run -d --name "$TEST_CONTAINER" \
  --network agent_agent-net \
  -p "127.0.0.1:${TEST_PORT}:9080" \
  --env-file "$(to_win "$ROOT/.env")" \
  -e GATEWAY_AUTH_MODE=enforce \
  -e JWT_ISSUER=hongmeng-oa \
  -e JWT_SECRET="$SECRET" \
  -e JWT_SECRET_PREVIOUS="$PREVIOUS" \
  -v "$(to_win "$ROOT/apisix-test/apisix.yaml"):/usr/local/apisix/conf/apisix.yaml:ro" \
  -v "$(to_win "$ROOT/apisix-test/config.yaml"):/usr/local/apisix/conf/config.yaml:ro" \
  -v "$(to_win "$ROOT/apisix/plugins"):/usr/local/apisix/custom_plugins/apisix/plugins:ro" \
  apache/apisix:3.13.0-debian >/dev/null || { echo "FATAL: 测试容器启动失败"; exit 1; }

# 就绪轮询：无凭据打 /echo 应得到网关 401（说明数据面 + gateway-auth 已就绪）
echo -n "[stack] waiting for :$TEST_PORT "
READY=0
for _ in $(seq 1 30); do
  CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:${TEST_PORT}/echo" 2>/dev/null || echo 000)"
  if [ "$CODE" = "401" ]; then READY=1; break; fi
  echo -n "."
  sleep 2
done
echo ""
if [ "$READY" != "1" ]; then
  echo "FATAL: 测试实例未就绪（坏配置会加载失败），容器日志："
  docker logs --tail 30 "$TEST_CONTAINER" 2>&1
  exit 1
fi
echo "[stack] apisix-test ready on :$TEST_PORT (enforce)"

# ── 可选：直接跑认证矩阵 ────────────────────────────────────
if [ "${RUN_MATRIX:-0}" = "1" ]; then
  PY="D:/Python/python.exe"; command -v "$PY" >/dev/null 2>&1 || PY=python
  "$PY" "$ROOT/scripts/gateway_auth_matrix.py" \
    --base "http://127.0.0.1:${TEST_PORT}" \
    --secret "$SECRET" --previous "$PREVIOUS" \
    --redis-host 127.0.0.1 --redis-port 6379
fi

if [ "${RUN_MATRIX:-0}" != "1" ]; then
  echo "[stack] 台架就绪。跑矩阵："
  echo "  D:/Python/python.exe scripts/gateway_auth_matrix.py --base http://127.0.0.1:${TEST_PORT} \\"
  echo "      --secret <JWT_SECRET> --previous <JWT_SECRET_PREVIOUS> --redis-host 127.0.0.1 --redis-port 6379"
  echo "  或限流回归：D:/Python/python.exe scripts/gateway_rate_limit_check.py --secret <JWT_SECRET>"
  echo "  Ctrl+C 停止并清理。"
  wait "$STUB_PID"
fi
