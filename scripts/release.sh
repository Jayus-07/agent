#!/usr/bin/env bash
# release.sh — 平台发布统一入口（Platform Readiness STOP E13/G1）
# 默认流程：身份注入 → 迁移 preflight → 全量构建（stop-the-world）→ 滚动 up。
# --skip-build：复用已固定摘要的镜像，先执行数据库迁移，再做迁移 preflight。
# 两种流程随后都会等待健康状态并运行 12 门 Release Gate 冒烟。
# 用法：cd 仓库根 && bash scripts/release.sh [--skip-build]
# 纪律：生产发布只允许从干净且已提交的 Git 工作树构建。
set -uo pipefail
cd "$(dirname "$0")/.."

SKIP_BUILD=false
if [ "${1:-}" = "--skip-build" ]; then
  SKIP_BUILD=true
fi

HEAD_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [ "$HEAD_COMMIT" = "unknown" ]; then
  echo "[release][FATAL] 无法确定构建 commit（非 git 仓库？）"; exit 1
fi
if [ -z "${GIT_COMMIT:-}" ] || [ "$GIT_COMMIT" = "unknown" ]; then
  GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
  case "$GIT_COMMIT" in
    *[!0-9a-fA-F]*|"")
      echo "[release][FATAL] 注入的 GIT_COMMIT 不是有效十六进制提交号"; exit 1
      ;;
  esac
  if [ "${#GIT_COMMIT}" -lt 7 ] || [ "${#GIT_COMMIT}" -gt "${#HEAD_COMMIT}" ]; then
    echo "[release][FATAL] 注入的 GIT_COMMIT 长度无效"; exit 1
  fi
  case "$HEAD_COMMIT" in
    "$GIT_COMMIT"*) ;;
    *)
      echo "[release][FATAL] 注入的 GIT_COMMIT 与当前工作树 HEAD 不一致"; exit 1
      ;;
  esac
fi
if ! GIT_STATUS="$(git status --porcelain --untracked-files=normal 2>/dev/null)"; then
  echo "[release][FATAL] 无法读取 Git 工作树状态，拒绝发布"; exit 1
fi
if [ -n "$GIT_STATUS" ]; then
  echo "[release][FATAL] 发布工作树存在未提交或未跟踪文件；请从干净提交构建"; exit 1
fi
export GIT_COMMIT
export GIT_DIRTY=false
export BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[release] commit=$GIT_COMMIT build_time=$BUILD_TIME dirty=$GIT_DIRTY"

echo "== 0. Tool 契约 lock 漂移检测（治理 M1/M8）=="
if [ "$SKIP_BUILD" = "true" ]; then
  docker compose run --rm --no-deps \
    -e PYTHONPATH=/app \
    --entrypoint python db-migrate \
    -m backend.scripts.gen_tool_contract_lock --check \
    || { echo "[release][FATAL] 契约 lock 与代码漂移：先重新生成 lock 并随变更提交"; exit 1; }
else
  (cd backend && PYTHONPATH=".." python -m backend.scripts.gen_tool_contract_lock --check) \
    || { echo "[release][FATAL] 契约 lock 与代码漂移：先重新生成 lock 并随变更提交"; exit 1; }
fi

echo "== 1. Migration preflight =="
MIGRATION_IMAGE="${RELEASE_DB_MIGRATE_IMAGE:-agent-db-migrate}"
if [ "$SKIP_BUILD" = "true" ]; then
  echo "== 1.1 使用已固定摘要的镜像执行数据库迁移 =="
  docker compose run --rm db-migrate \
    || { echo "[release][FATAL] 数据库迁移执行失败，拒绝启动新版本服务"; exit 1; }

  docker compose run --rm --no-deps \
    -e PYTHONPATH=/app \
    -e PG_AUTHORITY_HOST=postgres \
    -e PG_AUTHORITY_PORT=5432 \
    --entrypoint python db-migrate \
    backend/scripts/verify_migration_state.py \
    || { echo "[release][FATAL] 迁移三层不一致，先核对当前镜像与数据库登记"; exit 1; }
else
  (cd backend && PYTHONPATH=".." python scripts/verify_migration_state.py --image "$MIGRATION_IMAGE") \
    || { echo "[release][FATAL] 迁移三层不一致，先重建 db-migrate 并核对登记"; exit 1; }
fi

if [ "$SKIP_BUILD" != "true" ]; then
  echo "== 2.1 解析构建镜像标签（不执行构建）=="
  bake_definition="$(docker compose build --print app agent-worker db-migrate)" || {
    echo "[release][FATAL] 无法预览构建目标；请升级 Docker Compose 以支持 build --print"
    exit 1
  }
  resolve_service_image() {
    local service="$1"
    printf '%s' "$bake_definition" |
      python -c '
import json
import sys

service = sys.argv[1]
try:
    tags = json.load(sys.stdin)["target"][service]["tags"]
except (KeyError, json.JSONDecodeError, TypeError) as exc:
    print(f"无法从 Bake 定义解析 {service} 镜像标签: {exc}", file=sys.stderr)
    raise SystemExit(1)
if len(tags) != 1 or not tags[0]:
    print(f"{service} 预期一个镜像标签，实际为 {tags!r}", file=sys.stderr)
    raise SystemExit(1)
print(tags[0])
' "$service"
  }
  for svc in app agent-worker db-migrate; do
    resolve_service_image "$svc" >/dev/null || {
      echo "[release][FATAL] 无法从构建定义解析 $svc 的镜像标签"; exit 1;
    }
  done

  echo "== 2. 全量构建（stop-the-world 发布形态，B10）=="
  docker compose build app rag-service mcp-service business-mock \
    agent-worker rag-index-worker maintenance-worker report-worker \
    metadata-shadow-worker beat cs-dispatcher db-migrate || exit 1
  # 构建身份硬校验（STOP G 教训：构建吞没 args 必须当场暴露）
  for svc in app agent-worker db-migrate; do
    image="$(resolve_service_image "$svc")" || {
      echo "[release][FATAL] 无法从构建定义解析 $svc 的镜像标签"; exit 1;
    }
    identity="$(docker image inspect \
      --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" 2>/dev/null)" || {
      echo "[release][FATAL] 无法读取刚构建的 $svc 镜像身份（$image）"; exit 1;
    }
    got="$(printf '%s\n' "$identity" | grep -m1 '^GIT_COMMIT=' || true)"
    got_dirty="$(printf '%s\n' "$identity" | grep -m1 '^GIT_DIRTY=' || true)"
    got_time="$(printf '%s\n' "$identity" | grep -m1 '^BUILD_TIME=' || true)"
    echo "[release] $svc image=$image identity: ${got:-MISSING} ${got_time:-MISSING} ${got_dirty:-MISSING}"
    if [ "$got" != "GIT_COMMIT=$GIT_COMMIT" ]; then
      echo "[release][FATAL] $svc 构建身份与 GIT_COMMIT=$GIT_COMMIT 不一致（构建吞没 args）"
      exit 1
    fi
    if [ "$got_dirty" != "GIT_DIRTY=false" ] || [ "$got_time" != "BUILD_TIME=$BUILD_TIME" ]; then
      echo "[release][FATAL] $svc 构建身份缺少正确的 BUILD_TIME 或 dirty=false（构建吞没 args）"
      exit 1
    fi
  done
fi

echo "== 3. 滚动 up（依赖序由 compose depends_on 保证）=="
if [ "$SKIP_BUILD" = "true" ]; then
  docker compose up -d --no-build || exit 1
else
  docker compose up -d || exit 1
fi

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
