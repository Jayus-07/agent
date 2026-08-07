#!/usr/bin/env bash
# ============================================================================
# rebuild_pg.sh — 一键重建 PostgreSQL 两库（删 + 建 + 表 + 模拟数据 + 回归）
# ============================================================================
# Wrapper for scripts/rebuild_pg.py（用 psycopg2 直接连 PG，不依赖 psql 客户端）。
#
# 用法：
#   bash scripts/rebuild_pg.sh                  # 完整重建
#   bash scripts/rebuild_pg.sh --keep-data      # 保留数据，只跑 migration（修复）
#   bash scripts/rebuild_pg.sh --skip-regress  # 跳过 pytest
# ============================================================================
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python "$SCRIPT_DIR/rebuild_pg.py" "$@"
