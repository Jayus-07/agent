"""observability/health.py — 健康检查端点

提供 /health 用于负载均衡器/监控系统探测服务存活。
"""
import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["系统"])
async def health():
    """健康检查：返回服务状态 + RAG 模块状态 + 会话上下文 backend 状态

    Platform Readiness STOP B1/B4 增补：
    - build：本进程构建身份（commit/build_time，镜像构建注入）——回答
      「现在运行的 app 是哪个 commit」；
    - migrations：迁移水位观测（packaged_max vs applied_max）——status=
      "drifted" 表示镜像内迁移领先于 DB 已登记水位（历史静默跳过场景）。
      观测面而非硬门：fail-closed 责任链 = db-migrate fail-fast（未登记
      rc=2 阻断下游）+ 发布前 verify_migration_state.py preflight。
    """
    from backend.app.api.deps import get_rag_status
    return {
        "status": "ok",
        "build": {
            "commit": os.getenv("GIT_COMMIT", "unknown"),
            "build_time": os.getenv("BUILD_TIME", "unknown"),
        },
        "migrations": _migration_watermark(),
        "rag": get_rag_status(),
        # STOP G5：ConversationContext backend 观测（healthy/degraded/
        # disabled + backend 名称）。软失败：观测缺失不影响存活判定。
        "conversation_context": _context_backend_status(),
        # STOP J §98：Provider health 组件（healthy/degraded/disabled）——
        # 非关键 Provider 降级不拖垮整体 /health（travel 域全部 required=false）
        "travel_providers": _travel_providers_status(),
    }


def _migration_watermark() -> dict:
    """迁移水位观测（软失败，任何异常返回 unknown 不影响存活判定）。"""
    try:
        from pathlib import Path

        import psycopg2

        from backend.config.database import MEMORY_DB_CONFIG

        migrations_dir = Path(__file__).resolve().parents[4] / "sql" / "migrations"
        packaged = sorted(p.name for p in migrations_dir.glob("*.sql")) if migrations_dir.is_dir() else []
        packaged_max = packaged[-1] if packaged else ""
        cfg = MEMORY_DB_CONFIG
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
            user=cfg["user"], password=cfg["password"], connect_timeout=3)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT coalesce(max(filename),''), count(*) "
                    "FROM schema_migrations WHERE status='applied'")
                applied_max, applied_count = cur.fetchone()
        finally:
            conn.close()
        drifted = bool(packaged_max and applied_max and packaged_max > applied_max)
        return {
            "status": "drifted" if drifted else "ok",
            "packaged_max": packaged_max,
            "applied_max": applied_max,
            "applied_count": applied_count,
        }
    except Exception:  # noqa: BLE001
        return {"status": "unknown"}


def _context_backend_status() -> dict:
    try:
        from backend.orchestration.context.context_repository import (
            get_conversation_context_repository,
        )

        return get_conversation_context_repository().status
    except Exception:  # noqa: BLE001
        return {"backend": "unknown", "status": "unknown"}


def _travel_providers_status() -> dict:
    try:
        from backend.providers.travel.live.health import provider_health

        return provider_health()
    except Exception:  # noqa: BLE001
        return {}