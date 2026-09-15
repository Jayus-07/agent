"""api/routes/prompts.py — Prompt 管理 API

端点:
  GET    /prompts                                      — 列表（filter: category, risk_level, q, keys）
  GET    /prompts/meta/registry                        — 静态注册表元数据
  GET    /prompts/{key}                                — 详情 + active template
  GET    /prompts/{key}/versions                       — 版本历史
  GET    /prompts/{key}/versions/{version}             — 单版本
  GET    /prompts/{key}/diff                           — 版本间 unified diff
  POST   /prompts/{key}/drafts                         — 创建草稿
  POST   /prompts/{key}/publish                        — 发布版本
  POST   /prompts/{key}/rollback                       — 回滚
  POST   /prompts/{key}/versions/{version}/transition  — 状态流水线转换
  POST   /prompts/{key}/render                         — 干跑渲染（无 LLM）
  POST   /prompts/{key}/playground                     — 渲染 + LLM 调用
  GET    /prompts/{key}/audit                          — 审计日志
  POST   /prompts/seed                                 — 种子默认值

鉴权（2026-09-15 S0-2 起）：
  **全部端点（含只读）** 均需通过 `backend.app.api.deps.resolve_operator_role` ——
  该依赖是运营角色的**唯一解析入口**，当前只认 `X-Internal-Token` 服务凭据
  （浏览器侧本轮不开放，`/prompts` 功能处于预留状态）。
  **本模块禁止读取 `X-Operator-Role` / `X-Operator-Id`**：前者曾是客户端自设头
  （可伪造 → 越权发布高风险 Prompt），后者是审计身份的伪造入口。
"""
import difflib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.api.deps import OperatorIdentity, resolve_operator_role
from backend.prompts.registry import PROMPT_REGISTRY, PromptSpec
from backend.prompts.renderer import PromptRenderer, PromptRenderError
from backend.prompts.service import prompt_service
from backend.shared.logger import logger

router = APIRouter(prefix="/prompts", tags=["Prompt管理"])


# ── Request / Response models ─────────────────────────────────

class DraftRequest(BaseModel):
    template: str
    change_note: str = ""


class PublishRequest(BaseModel):
    version: int


class RollbackRequest(BaseModel):
    version: int


class RenderRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    template: str | None = None


class PlaygroundRequest(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)
    template: str | None = None
    model: str | None = None


class SeedRequest(BaseModel):
    auto_seed: bool = True


class TransitionRequest(BaseModel):
    status: str


# ── Permission helpers ────────────────────────────────────────
#
# 2026-09-15 S0-2：角色来源已收敛到 `backend.app.api.deps.resolve_operator_role`
# （唯一入口）。此处只保留风险等级 × 动作 的权限矩阵。
# 原有的 `_operator_role()` / `_operator_id()` 两个透传桩函数已删除 ——
# 它们是死代码（返回自身入参、从未接线到身份层），留着会诱导后人走客户端头那条路。
# **禁止再从此模块读取 `X-Operator-Role` / `X-Operator-Id`。**


def _check_permission(risk_level: str, action: str, role: str) -> None:
    """Permission matrix:
      critical: read-only (template masked for non-admin)
      high:     viewer=read, editor=draft, admin=publish/rollback
      medium:   viewer=read, editor=draft+publish+rollback
      low:      viewer=read, editor=draft+publish+rollback
    """
    if risk_level == "critical":
        if action != "read":
            raise HTTPException(403, "Critical prompts are code-controlled and read-only")
        return

    matrix = {
        "high":   {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["admin"], "transition": ["admin"], "rollback": ["admin"]},
        "medium": {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["editor", "admin"], "transition": ["editor", "admin"], "rollback": ["editor", "admin"]},
        "low":    {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["editor", "admin"], "transition": ["editor", "admin"], "rollback": ["editor", "admin"]},
    }
    allowed = matrix.get(risk_level, matrix["low"]).get(action, [])
    if role not in allowed:
        raise HTTPException(403, f"Role '{role}' cannot perform '{action}' on {risk_level}-risk prompts")


# ── Serialization ─────────────────────────────────────────────

def _prompt_to_dict(p, *, include_template: bool = False, mask: bool = False) -> dict:
    d = {
        "id": p.id,
        "key": p.key,
        "name": p.name,
        "description": p.description,
        "category": p.category,
        "risk_level": p.risk_level,
        "template_engine": p.template_engine,
        "variables": p.variables or [],
        "variable_count": len(p.variables or []),
        "active_version": p.active_version,
        "is_code_controlled": p.is_code_controlled,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
    if include_template:
        d["template"] = "*** CODE-CONTROLLED ***" if mask else (p.active_template if hasattr(p, "active_template") else "")
    return d


def _version_to_dict(v, *, mask: bool = False) -> dict:
    return {
        "id": v.id,
        "version": v.version,
        "template": "*** CODE-CONTROLLED ***" if mask else v.template,
        "variables": v.variables or [],
        "status": v.status,
        "change_note": v.change_note,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if getattr(v, "updated_at", None) else None,
    }


def _audit_to_dict(a) -> dict:
    return {
        "id": a.id,
        "prompt_key": a.prompt_key,
        "action": a.action,
        "from_version": a.from_version,
        "to_version": a.to_version,
        "actor": a.actor,
        "role": a.role,
        "detail": a.detail,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _spec_to_dict(spec: PromptSpec) -> dict:
    return {
        "key": spec.key,
        "name": spec.name,
        "category": spec.category,
        "risk_level": spec.risk_level,
        "variables": [{"name": v.name, "required": v.required, "description": v.description} for v in spec.variables],
        "code_controlled": spec.code_controlled,
        "required_substrings": list(spec.required_substrings),
        "default_file": spec.default_file,
        "agent": spec.agent,
    }


# ── GET /prompts ──────────────────────────────────────────────

@router.get("")
async def list_prompts(
    category: str | None = None,
    risk_level: str | None = None,
    q: str | None = None,
    keys: str | None = None,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    key_filter = [k.strip() for k in keys.split(",") if k.strip()] if keys else None

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompts = await repo.list_all(category=category, risk_level=risk_level, q=q)

        if key_filter:
            prompts = [p for p in prompts if p.key in key_filter]

        prompt_ids = [p.id for p in prompts]
        latest_versions = await repo.get_latest_versions(prompt_ids)

    items = []
    for p in prompts:
        spec = PROMPT_REGISTRY.get(p.key)
        mask = spec and spec.code_controlled
        d = _prompt_to_dict(p, mask=mask)
        lv = latest_versions.get(p.id)
        d["latest_version_number"] = lv.version if lv else None
        d["latest_version_status"] = lv.status if lv else None
        d["latest_version_updated_at"] = (
            lv.updated_at.isoformat() if lv and getattr(lv, "updated_at", None) else None
        )
        items.append(d)

    return {"items": items, "total": len(items)}


# ── GET /prompts/meta/registry ────────────────────────────────

@router.get("/meta/registry")
async def get_registry(
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    return {
        "specs": [_spec_to_dict(s) for s in PROMPT_REGISTRY.values()],
        "total": len(PROMPT_REGISTRY),
    }


# ── GET /prompts/{key} ────────────────────────────────────────

@router.get("/{key}")
async def get_prompt(
    key: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", operator.role)

    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(key)

    if not prompt:
        defaults = prompt_service._defaults
        if key in defaults:
            return {
                "key": key,
                "name": spec.name,
                "category": spec.category,
                "risk_level": spec.risk_level,
                "variables": [{"name": v.name, "required": v.required, "description": v.description} for v in spec.variables],
                "code_controlled": spec.code_controlled,
                "required_substrings": list(spec.required_substrings),
                "active_version": None,
                "source": "default",
                "template": "*** CODE-CONTROLLED ***" if spec.code_controlled else defaults[key],
            }
        raise HTTPException(404, f"Prompt not found: {key}")

    mask = spec.code_controlled
    result = _prompt_to_dict(prompt, include_template=True, mask=mask)

    if not mask and prompt.active_version:
        async with AsyncSessionLocal() as session:
            repo = PromptRepository(session)
            ver = await repo.get_version(prompt.id, prompt.active_version)
            if ver:
                result["template"] = ver.template

    result["source"] = "db"
    result["code_controlled"] = spec.code_controlled
    result["required_substrings"] = list(spec.required_substrings)
    return result


# ── GET /prompts/{key}/versions ───────────────────────────────

@router.get("/{key}/versions")
async def list_versions(
    key: str,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", operator.role)

    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(key)
        if not prompt:
            raise HTTPException(404, f"Prompt not found in DB: {key}")
        versions = await repo.list_versions(prompt.id)

    mask = spec.code_controlled
    return {"items": [_version_to_dict(v, mask=mask) for v in versions], "total": len(versions)}


# ── GET /prompts/{key}/versions/{version} ─────────────────────

@router.get("/{key}/versions/{version}")
async def get_version(
    key: str,
    version: int,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", operator.role)

    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(key)
        if not prompt:
            raise HTTPException(404, f"Prompt not found in DB: {key}")
        ver = await repo.get_version(prompt.id, version)
        if not ver:
            raise HTTPException(404, f"Version {version} not found for {key}")

    return _version_to_dict(ver, mask=spec.code_controlled)


# ── GET /prompts/{key}/diff ───────────────────────────────────

@router.get("/{key}/diff")
async def diff_versions(
    key: str,
    from_version: int,
    to_version: int,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    if spec.code_controlled:
        raise HTTPException(403, "Cannot diff code-controlled prompt")

    _check_permission(spec.risk_level, "read", operator.role)

    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(key)
        if not prompt:
            raise HTTPException(404, f"Prompt not found in DB: {key}")

        ver_from = await repo.get_version(prompt.id, from_version)
        ver_to = await repo.get_version(prompt.id, to_version)

    if not ver_from or not ver_to:
        raise HTTPException(404, "One or both versions not found")

    from_lines = ver_from.template.splitlines(keepends=True)
    to_lines = ver_to.template.splitlines(keepends=True)

    diff = difflib.unified_diff(
        from_lines, to_lines,
        fromfile=f"{key} v{from_version}",
        tofile=f"{key} v{to_version}",
    )
    return {
        "key": key,
        "from_version": from_version,
        "to_version": to_version,
        "diff": "".join(diff),
    }


# ── POST /prompts/{key}/drafts ────────────────────────────────

@router.post("/{key}/drafts")
async def create_draft(
    key: str,
    body: DraftRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "draft", operator.role)

    try:
        result = await prompt_service.create_draft(
            key, body.template,
            change_note=body.change_note,
            created_by=operator.actor,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(403, str(e))


# ── POST /prompts/{key}/publish ───────────────────────────────

@router.post("/{key}/publish")
async def publish(
    key: str,
    body: PublishRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "publish", operator.role)

    try:
        result = await prompt_service.publish(
            key, body.version,
            actor=operator.actor,
            role=operator.role,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── POST /prompts/{key}/rollback ──────────────────────────────

@router.post("/{key}/rollback")
async def rollback(
    key: str,
    body: RollbackRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "rollback", operator.role)

    try:
        result = await prompt_service.rollback(
            key, body.version,
            actor=operator.actor,
            role=operator.role,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── POST /prompts/{key}/versions/{version}/transition ─────────

@router.post("/{key}/versions/{version}/transition")
async def transition_status(
    key: str,
    version: int,
    body: TransitionRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "transition", operator.role)

    try:
        result = await prompt_service.transition_status(
            key, version, body.status,
            actor=operator.actor,
            role=operator.role,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── POST /prompts/{key}/render ────────────────────────────────

@router.post("/{key}/render")
async def render_prompt(
    key: str,
    body: RenderRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", operator.role)

    template = body.template
    if not template:
        try:
            result = await prompt_service.render(key, **body.variables)
            return {
                "key": key,
                "version": result.version,
                "source": result.source,
                "text": result.text,
                "text_length": len(result.text),
            }
        except KeyError as e:
            raise HTTPException(404, str(e))
        except PromptRenderError as e:
            raise HTTPException(422, str(e))

    try:
        text = PromptRenderer.render(template, body.variables, spec=spec)
        return {
            "key": key,
            "version": None,
            "source": "playground",
            "text": text,
            "text_length": len(text),
        }
    except PromptRenderError as e:
        raise HTTPException(422, str(e))


# ── POST /prompts/{key}/playground ────────────────────────────

@router.post("/{key}/playground")
async def playground(
    key: str,
    body: PlaygroundRequest,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", operator.role)

    try:
        if body.template:
            text = PromptRenderer.render(body.template, body.variables, spec=spec)
            render_info = {"version": None, "source": "playground"}
        else:
            result = await prompt_service.render(key, **body.variables)
            text = result.text
            render_info = {"version": result.version, "source": result.source}
    except KeyError as e:
        raise HTTPException(404, str(e))
    except PromptRenderError as e:
        raise HTTPException(422, str(e))

    try:
        from backend.llm import get_llm
        llm = get_llm(body.model) if body.model else get_llm()

        import time
        t0 = time.monotonic()
        response = await llm.ainvoke(text)
        latency = round(time.monotonic() - t0, 3)

        rendered = {
            "text": text,
            "key": key,
            "version": render_info.get("version"),
            "source": render_info.get("source", "playground"),
        }
        return {
            "rendered": rendered,
            "llm_output": response.content if hasattr(response, "content") else str(response),
            "latency_ms": round(latency * 1000),
        }
    except Exception as e:
        logger.warning(f"[Playground] LLM invoke failed for {key}: {e}")
        rendered = {
            "text": text,
            "key": key,
            "version": render_info.get("version"),
            "source": render_info.get("source", "playground"),
        }
        return {
            "rendered": rendered,
            "llm_output": None,
            "llm_error": str(e),
            "latency_ms": None,
        }


# ── GET /prompts/{key}/audit ──────────────────────────────────

@router.get("/{key}/audit")
async def audit_log(
    key: str,
    limit: int = 50,
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        logs = await repo.list_audit(key, limit=limit)

    return {"items": [_audit_to_dict(a) for a in logs], "total": len(logs)}


# ── POST /prompts/seed ────────────────────────────────────────

@router.post("/seed")
async def seed_defaults(
    body: SeedRequest = SeedRequest(),
    operator: OperatorIdentity = Depends(resolve_operator_role),
):
    if operator.role != "admin":
        raise HTTPException(403, "Only admin can seed defaults")

    from backend.prompts.loader import seed
    result = await seed()
    return result
