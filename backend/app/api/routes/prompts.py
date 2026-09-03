"""api/routes/prompts.py — Prompt 管理 API

端点:
  GET    /prompts                          — 列表（filter: category, risk_level, q）
  GET    /prompts/meta/registry            — 静态注册表元数据
  GET    /prompts/{key}                    — 详情 + active template
  GET    /prompts/{key}/versions           — 版本历史
  GET    /prompts/{key}/versions/{version} — 单版本
  GET    /prompts/{key}/diff               — 版本间 unified diff
  POST   /prompts/{key}/drafts             — 创建草稿
  POST   /prompts/{key}/publish            — 发布版本
  POST   /prompts/{key}/rollback           — 回滚
  POST   /prompts/{key}/render             — 干跑渲染（无 LLM）
  POST   /prompts/{key}/playground         — 渲染 + LLM 调用
  GET    /prompts/{key}/audit              — 审计日志
  POST   /prompts/seed                     — 种子默认值
"""
import difflib
from typing import Any

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

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


# ── Permission helpers ────────────────────────────────────────

def _operator_role(headers: dict | None = None, x_operator_role: str = "viewer") -> str:
    return x_operator_role


def _operator_id(x_operator_id: str = "anonymous") -> str:
    return x_operator_id


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
        "high":   {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["admin"], "rollback": ["admin"]},
        "medium": {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["editor", "admin"], "rollback": ["editor", "admin"]},
        "low":    {"read": ["viewer", "editor", "admin"], "draft": ["editor", "admin"], "publish": ["editor", "admin"], "rollback": ["editor", "admin"]},
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
    }


# ── GET /prompts ──────────────────────────────────────────────

@router.get("")
async def list_prompts(
    category: str | None = None,
    risk_level: str | None = None,
    q: str | None = None,
    x_operator_role: str = Header(default="viewer"),
):
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompts = await repo.list_all(category=category, risk_level=risk_level, q=q)

    items = []
    for p in prompts:
        spec = PROMPT_REGISTRY.get(p.key)
        mask = spec and spec.code_controlled
        items.append(_prompt_to_dict(p, mask=mask))

    return {"items": items, "total": len(items)}


# ── GET /prompts/meta/registry ────────────────────────────────

@router.get("/meta/registry")
async def get_registry():
    return {
        "specs": [_spec_to_dict(s) for s in PROMPT_REGISTRY.values()],
        "total": len(PROMPT_REGISTRY),
    }


# ── GET /prompts/{key} ────────────────────────────────────────

@router.get("/{key}")
async def get_prompt(
    key: str,
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", x_operator_role)

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
    return result


# ── GET /prompts/{key}/versions ───────────────────────────────

@router.get("/{key}/versions")
async def list_versions(
    key: str,
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", x_operator_role)

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
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", x_operator_role)

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
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    if spec.code_controlled:
        raise HTTPException(403, "Cannot diff code-controlled prompt")

    _check_permission(spec.risk_level, "read", x_operator_role)

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
    x_operator_role: str = Header(default="editor"),
    x_operator_id: str = Header(default="anonymous"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "draft", x_operator_role)

    try:
        result = await prompt_service.create_draft(
            key, body.template,
            change_note=body.change_note,
            created_by=x_operator_id,
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
    x_operator_role: str = Header(default="editor"),
    x_operator_id: str = Header(default="anonymous"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "publish", x_operator_role)

    try:
        result = await prompt_service.publish(
            key, body.version,
            actor=x_operator_id,
            role=x_operator_role,
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
    x_operator_role: str = Header(default="editor"),
    x_operator_id: str = Header(default="anonymous"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "rollback", x_operator_role)

    try:
        result = await prompt_service.rollback(
            key, body.version,
            actor=x_operator_id,
            role=x_operator_role,
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
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", x_operator_role)

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
    x_operator_role: str = Header(default="viewer"),
):
    spec = PROMPT_REGISTRY.get(key)
    if not spec:
        raise HTTPException(404, f"Prompt not found: {key}")

    _check_permission(spec.risk_level, "read", x_operator_role)

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

        return {
            "key": key,
            **render_info,
            "rendered_text": text,
            "llm_response": response.content if hasattr(response, "content") else str(response),
            "latency_s": latency,
            "model": body.model or "default",
        }
    except Exception as e:
        logger.warning(f"[Playground] LLM invoke failed for {key}: {e}")
        return {
            "key": key,
            **render_info,
            "rendered_text": text,
            "llm_response": None,
            "llm_error": str(e),
            "latency_s": None,
            "model": body.model or "default",
        }


# ── GET /prompts/{key}/audit ──────────────────────────────────

@router.get("/{key}/audit")
async def audit_log(
    key: str,
    limit: int = 50,
    x_operator_role: str = Header(default="viewer"),
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
    x_operator_role: str = Header(default="admin"),
    x_operator_id: str = Header(default="anonymous"),
):
    if x_operator_role != "admin":
        raise HTTPException(403, "Only admin can seed defaults")

    from backend.prompts.loader import seed
    result = await seed()
    return result
