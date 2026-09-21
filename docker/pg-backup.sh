#!/bin/bash
# =====================================================
# pg-backup.sh — PG 自动备份（2026-09-21 高并发审查 B3）
#
# 背景：PG 是任务状态/checkpoint/记忆库唯一权威（单机 pg_data 卷），
# 此前全仓零自动备份，磁盘损坏 = 全量丢失。
#
# 行为：
#   1. 启动即备份一轮（不等第一个 sleep）
#   2. 每轮 dump agent_memory + agent_business（pg_dump -Fc 自定义格式，
#      支持并行恢复与选择性恢复）
#   3. 保留最近 PG_BACKUP_RETENTION_DAYS 天（按目录名日期清理）
#   4. 单轮失败不退出容器（下轮重试），但打 ERROR 日志可见
#
# 恢复（演练纳入发布验收，B3 要求）：
#   # 恢复单库（容器内执行，或宿主机装了同版本 psql 客户端）：
#   docker exec -i agent-pg-backup pg_restore \
#     -h postgres -U postgres -d agent_memory --clean --if-exists \
#     /backups/agent_memory/agent_memory-YYYYMMDD-HHMMSS.dump
#   # 仅查看内容（不落库）：
#   pg_restore -l /backups/agent_memory/agent_memory-YYYYMMDD-HHMMSS.dump
#
# 说明：WAL 归档（PITR）需要改 postgres 启动参数（archive_mode/command）
# 并重启 PG，属破坏性变更，本版先落"每日基础备份 + 保留策略"，WAL
# 归档作为后续扩容期任务（见审查报告 P2 清单）。
# =====================================================
set -u

PGHOST="${PGHOST:-postgres}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-postgres}"
# PGPASSWORD 由 compose 注入（pg_dump 自动读取）
BACKUP_DIR="${PG_BACKUP_DIR:-/backups}"
RETENTION_DAYS="${PG_BACKUP_RETENTION_DAYS:-14}"
INTERVAL="${PG_BACKUP_INTERVAL_SECONDS:-86400}"
DATABASES="agent_memory agent_business"

log() { echo "[pg-backup $(date '+%Y-%m-%d %H:%M:%S')] $*"; }

backup_one() {
    local db="$1"
    local dir="${BACKUP_DIR}/${db}"
    local ts
    ts="$(date '+%Y%m%d-%H%M%S')"
    local target="${dir}/${db}-${ts}.dump"
    mkdir -p "$dir"
    if pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -Fc -f "$target" "$db"; then
        local size
        size="$(du -h "$target" | cut -f1)"
        log "OK  ${db} -> ${target} (${size})"
    else
        log "ERROR ${db} dump failed（下一轮重试；保留失败现场排查）"
        rm -f "$target"
        return 1
    fi
}

prune_old() {
    # 按目录名下文件的时间戳清理：早于 RETENTION_DAYS 的 .dump 删除
    local db="$1"
    local dir="${BACKUP_DIR}/${db}"
    [ -d "$dir" ] || return 0
    find "$dir" -name "*.dump" -type f -mtime "+${RETENTION_DAYS}" -print -delete |
        while read -r f; do log "PRUNE expired: $f"; done
}

# 可达性等待：compose depends_on healthy 已保证，这里兜底（首轮立刻备份
# 失败重试间隔 30s，最多 20 次）
for i in $(seq 1 20); do
    if pg_isready -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d agent_memory >/dev/null 2>&1; then
        break
    fi
    log "waiting for postgres... ($i/20)"
    sleep 30
done

while true; do
    log "=== backup round start (retention=${RETENTION_DAYS}d, interval=${INTERVAL}s) ==="
    failed=0
    for db in $DATABASES; do
        backup_one "$db" || failed=1
        prune_old "$db"
    done
    if [ "$failed" -eq 0 ]; then
        log "=== backup round OK ==="
    else
        log "=== backup round had failures（见上方 ERROR）==="
    fi
    sleep "$INTERVAL"
done
