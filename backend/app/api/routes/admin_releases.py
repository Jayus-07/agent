"""发布记录查询 API（M8 / 台账 D8）

GET /admin/releases        — 发布历史分页（result 过滤）
GET /admin/releases/latest — 当前版本卡（最新一条 + 上一条 PASS 的回滚判据）

数据源：ai.release_records（verify_release_gate.py 12 门跑完直写）。
权限：管理员。只读，落库在发布链路侧。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/admin/releases", tags=["Admin-Releases"])


def _query(sql: str, params: tuple) -> list[tuple]:
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _row_to_dict(r: tuple) -> dict[str, Any]:
    gates = r[3] if isinstance(r[3], dict) else json.loads(r[3] or "{}")
    details = r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}")
    return {
        "id": r[0], "git_sha": r[1], "build_time": r[2],
        "gates": gates, "gate_details": details, "result": r[5],
        "operator": r[6], "started_at": str(r[7]) if r[7] else None,
        "finished_at": str(r[8]) if r[8] else None,
        "created_at": str(r[9]),
    }


_SELECT_COLS = ("id, git_sha, build_time, gates, gate_details, result, "
                "operator, started_at, finished_at, created_at")


@router.get("")
async def list_releases(
    request: Request,
    result: str = Query("", description="PASS/FAIL 过滤（空=全部）"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    await require_admin_user(request)
    where = ""
    params: list[Any] = []
    if result:
        where = "WHERE result = %s"
        params.append(result)
    rows = _query(
        f"SELECT {_SELECT_COLS} FROM ai.release_records {where} "
        f"ORDER BY id DESC LIMIT %s OFFSET %s",
        (*params, limit, offset))
    total = _query(
        f"SELECT COUNT(*) FROM ai.release_records {where}", tuple(params))[0][0]
    return {"total": total, "limit": limit, "offset": offset,
            "releases": [_row_to_dict(r) for r in rows]}


@router.get("/latest")
async def latest_release(request: Request):
    """当前版本卡：最新一条 + 最近一条 PASS（回滚目标判据）。"""
    await require_admin_user(request)
    rows = _query(
        f"SELECT {_SELECT_COLS} FROM ai.release_records ORDER BY id DESC LIMIT 1", ())
    if not rows:
        return {"latest": None, "last_pass": None}
    last_pass_rows = _query(
        f"SELECT {_SELECT_COLS} FROM ai.release_records WHERE result='PASS' "
        f"ORDER BY id DESC LIMIT 1", ())
    return {
        "latest": _row_to_dict(rows[0]),
        "last_pass": _row_to_dict(last_pass_rows[0]) if last_pass_rows else None,
    }


__all__ = ["router"]
