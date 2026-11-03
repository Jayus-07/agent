"""audit-remediation-r1 P3 批次 B 回归测试。

覆盖：
- B1 xml_parser：feed/close 形态改动后合法 XML 仍完整解析（DTD 拒绝已有
  test_enterprise_document_structure 覆盖，此处防回归）；
- B2 分页收口：三个列表端点的 limit/offset 参数契约；
- B3 /admin/email/channel 挂 require_admin_user（结构断言防回退）；
- B4 tenant fail-closed：plan_revisions._tenant 双分支 + travel /plan 路由守卫。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[2]


# ═══════════════ B1 xml_parser ═══════════════

def test_xml_parser_valid_document_still_parses_complete(tmp_path):
    from backend.rag.preprocessing.parser.xml_parser import XmlParser

    p = tmp_path / "ok.xml"
    p.write_text(
        "<root><item id='1'><name>杭州</name></item>"
        "<item id='2'><name>苏州</name></item></root>",
        encoding="utf-8",
    )
    ast = XmlParser().parse(str(p))
    assert ast.parse_status == "complete"
    assert "杭州" in ast.raw_text


def test_xml_parser_no_fromstring_source_guard():
    """B1：解析入口不允许再出现裸 fromstring（对齐 _parse_ooxml_xml 形态）。"""
    src = (BACKEND / "rag/preprocessing/parser/xml_parser.py").read_text(
        encoding="utf-8")
    assert "ET.fromstring" not in src, "解析入口不得使用裸 fromstring"
    assert "parser.feed(" in src


# ═══════════════ B2 分页收口 ═══════════════

def _route_params(func) -> dict:
    return {
        name: param
        for name, param in inspect.signature(func).parameters.items()
    }


def _query_bounds(query_default) -> dict:
    """pydantic v2 下 FastAPI Query 的 ge/le 存于 FieldInfo.metadata。"""
    out: dict = {}
    for meta in getattr(query_default, "metadata", []):
        ge = getattr(meta, "ge", None)
        le = getattr(meta, "le", None)
        if ge is not None:
            out["ge"] = ge
        if le is not None:
            out["le"] = le
    return out


def test_tasks_list_has_offset_param():
    from backend.app.api.routes.tasks import list_tasks

    params = _route_params(list_tasks)
    assert "offset" in params, "tasks 列表缺 offset 分页参数"
    assert _query_bounds(params["limit"].default) == {"ge": 1, "le": 100}
    assert _query_bounds(params["offset"].default) == {"ge": 0}


def test_task_service_offset_clamped_non_negative():
    """service 层 offset 负数必须钳 0（防 OFFSET -1 直接 SQL 报错）。"""
    src = (BACKEND / "services/task_service.py").read_text(encoding="utf-8")
    assert "OFFSET %s" in src
    assert "max(0, int(offset))" in src


def test_agent_task_list_limit_query_clamped():
    from backend.app.api.routes.agent_chat_tasks import list_agent_tasks

    params = _route_params(list_agent_tasks)
    assert _query_bounds(params["limit"].default) == {"ge": 1, "le": 100}


def test_memory_extractions_limit_query_clamped():
    from backend.app.api.routes.memory import list_extraction_jobs

    params = _route_params(list_extraction_jobs)
    assert _query_bounds(params["limit"].default) == {"ge": 1, "le": 200}


# ═══════════════ B3 admin 闸 ═══════════════

def test_admin_email_router_requires_admin():
    from backend.app.api.routes.admin_email import router
    from backend.app.api.deps import require_admin_user

    assert router.dependencies, "admin_email 路由未挂角色闸"
    deps = [d.dependency for d in router.dependencies]
    assert require_admin_user in deps, (
        "admin_email 必须挂 require_admin_user（SMTP host/账号存在性属敏感面）")


# ═══════════════ B4 tenant fail-closed ═══════════════

class _Ident:
    def __init__(self, source: str, tenant_id: str):
        self.source = source
        self.tenant_id = tenant_id


class _Req:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


def test_plan_revisions_tenant_header_empty_fails_closed():
    from backend.app.api.routes.plan_revisions import _tenant

    with pytest.raises(HTTPException) as ei:
        _tenant(_Req(), _Ident("header", ""))
    assert ei.value.status_code == 403


def test_plan_revisions_tenant_legacy_empty_fails_closed():
    from backend.app.api.routes.plan_revisions import _tenant

    with pytest.raises(HTTPException) as ei:
        _tenant(_Req({"X-Tenant-Id": ""}), _Ident("legacy", ""))
    assert ei.value.status_code == 403


def test_plan_revisions_tenant_valid_passthrough():
    from backend.app.api.routes.plan_revisions import _tenant

    assert _tenant(_Req(), _Ident("header", "acme")) == "acme"
    assert _tenant(_Req({"X-Tenant-Id": "acme"}), _Ident("legacy", "")) == "acme"


def test_travel_plan_route_has_tenant_guard():
    """travel /plan 路由层必须有空租户 403 守卫（worker 异常会被吞）。"""
    src = (BACKEND / "app/api/routes/travel.py").read_text(encoding="utf-8")
    guard_pos = src.find('raise HTTPException(403, detail="缺少可信租户身份")')
    worker_pos = src.find("def worker(control):")
    assert guard_pos != -1 and worker_pos != -1 and guard_pos < worker_pos, (
        "travel /plan 需在 worker 启动前 fail-closed 校验租户")
    # worker 内（/plan 路由）不再回退 default（audit 原 :909；其余路由的
    # 同款回退属遗留项另行批次，不在本断言范围）
    worker_slice = src[worker_pos:src.find("except RunStopped", worker_pos)]
    assert 'getattr(identity, "tenant_id", "") or "default"' not in worker_slice
