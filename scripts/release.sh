#!/usr/bin/env bash
# release.sh — 平台发布统一入口（Platform Readiness STOP E13/G1）
# 流程：身份注入 → 迁移 preflight → 全量构建（stop-the-world）→ 滚动 up →
#       健康等待 → 12 门 Release Gate 冒烟
# 用法：cd 仓库根 && bash scripts/release.sh [--skip-build]
# 纪律：禁止在 GIT_COMMIT=unknown 下发布（Gate 0 会拦）。
set -uo pipefail
cd "$(dirname "$0")/.."

export GIT_COMMIT="${GIT_COMMIT:-$(git rev-parse --short HEAD 2>/dev/null || echo unknown)}"
export BUILD_TIME="${BUILD_TIME:-$(date +%Y%m%d%H%M)}"
echo "[release] commit=$GIT_COMMIT build_time=$BUILD_TIME"
if [ "$GIT_COMMIT" = "unknown" ]; then
  echo "[release][FATAL] 无法确定构建 commit（非 git 仓库？）"; exit 1
fi

echo "== 1. Migration preflight =="
(cd backend && PYTHONPATH=".." python scripts/verify_migration_state.py --image agent-db-migrate) \
  || { echo "[release][FATAL] 迁移三层不一致，先重建 db-migrate 并核对登记"; exit 1; }

if [ "${1:-}" != "--skip-build" ]; then
  echo "== 2. 全量构建（stop-the-world 发布形态，B10）=="
  docker compose build app rag-service mcp-service business-mock \
    agent-worker rag-index-worker maintenance-worker report-worker \
    metadata-shadow-worker beat cs-dispatcher db-migrate || exit 1
  # 构建身份硬校验（STOP G 教训：构建吞没 args 必须当场暴露）
  for svc in app agent-worker db-migrate; do
    img=$(docker compose config --images 2>/dev/null | head -1)
    got=$(docker inspect -f "{{range .Config.Env}}{{.}} {{end}}" \
          "$(docker compose images -q $svc 2>/dev/null || echo $svc)" 2>/dev/null \
          | tr ' ' '\n' | grep '^GIT_COMMIT=' | head -1)
    echo "[release] $svc identity: ${got:-MISSING}"
    if [ "$got" != "GIT_COMMIT=$GIT_COMMIT" ]; then
      echo "[release][FATAL] $svc 构建身份与 GIT_COMMIT=$GIT_COMMIT 不一致（构建吞没 args）"
      exit 1
    fi
  done
fi

echo "== 3. 滚动 up（依赖序由 compose depends_on 保证）=="
docker compose up -d || exit 1

echo "== 4. 等待 app healthy =="
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health || true)
  [ "$code" = "200" ] && break
  sleep 5
done
[ "$code" = "200" ] || { echo "[release][FATAL] app 未恢复健康"; exit 1; }

echo "== 5. Release Gate（12 门冒烟）=="
(cd backend && PYTHONPATH=".." python scripts/verify_release_gate.py --password "${RELEASE_PASSWORD:?需设置 RELEASE_PASSWORD（e2e_domain 口令）}")
rc=$?
echo "== release rc=$rc =="
exit $rc
