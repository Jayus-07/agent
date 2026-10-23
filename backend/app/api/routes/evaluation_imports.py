"""共享评测资产的导入、审核、绑定与执行入口。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.deps import require_user_actor
from backend.app.api.routes.evaluation import _eval_tenant_scope
from backend.evaluation.dataset import imports as assets


async def require_curator(operator=Depends(require_user_actor)):
    # 当前评测资产为平台共享、单租户 default；与知识库目录采用相同写入边界。
    if operator.role != "super_admin":
        raise HTTPException(403, "共享评测资产管理仅限 super_admin")
    return operator


class ImportUploadRoute(APIRoute):
    """在 multipart 解析前限制总请求体，含无 Content-Length 的上传。"""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            if request.method == "POST" and self.path.endswith(("/dataset-imports", "/dataset-imports/validate")):
                limit = assets.MAX_BYTES + 64 * 1024
                parts = []
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, "上传请求超过 5 MB 文件及清单的大小限制")
                    parts.append(chunk)
                request._body = b"".join(parts)
            return await handler(request)

        return bounded


router = APIRouter(route_class=ImportUploadRoute, dependencies=[Depends(require_curator), Depends(_eval_tenant_scope)])


def require_kb(kb_id: str) -> dict:
    from backend.rag.knowledge_base_store import get_knowledge_base

    item = get_knowledge_base(kb_id, include_inactive=False)
    if not item or item.get("alias_for") or item.get("deprecated"):
        raise HTTPException(422, "请选择启用的正式知识库标识，兼容别名不可绑定")
    return item


def public_error(exc: Exception):
    from backend.shared.idempotency import IdempotencyConflict, IdempotencyUnavailable

    if isinstance(exc, IdempotencyConflict):
        raise HTTPException(409, "请求标识正在处理或已用于不同参数，请查询任务状态") from exc
    if isinstance(exc, IdempotencyUnavailable):
        raise HTTPException(503, "幂等存储不可用，请使用同一请求标识重试") from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(404, "评测资产或审核记录不存在") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(422, str(exc)) from exc
    raise HTTPException(503, "评测资产存储不可用，请稍后重试") from exc


@router.get("/dataset-imports")
async def list_imports():
    try:
        return {"items": [assets.summary(record) for record in assets.list_records("imports")]}
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.post("/dataset-imports/validate")
async def validate_import(file: UploadFile = File(...), manifest: str = Form("{}")):
    try:
        metadata = json.loads(manifest)
    except ValueError as exc:
        raise HTTPException(422, "manifest 不是合法 JSON") from exc
    data = await file.read(assets.MAX_BYTES + 1)
    validation = assets.validate_upload(file.filename or "", data, metadata)
    return {key: value for key, value in validation.items() if key != "cases"} | {
        "preview": validation.get("cases", [])[:3],
    }


@router.post("/dataset-imports", status_code=201)
async def import_dataset(
    file: UploadFile = File(...),
    manifest: str = Form("{}"),
    operator=Depends(require_curator),
):
    try:
        metadata = json.loads(manifest)
        validation = assets.validate_upload(file.filename or "", await file.read(assets.MAX_BYTES + 1), metadata)
        if not validation["valid"]:
            raise HTTPException(422, {"message": "评测集校验失败", "errors": validation["errors"]})
        return assets.create_import(validation, operator.actor)
    except (OSError, ValueError) as exc:
        public_error(exc)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = Field(min_length=1, max_length=1000)


@router.post("/dataset-imports/{import_id}/review")
async def review_import(import_id: str, body: Review, operator=Depends(require_curator)):
    if not body.reason.strip():
        raise HTTPException(422, "审核说明不能为空白")
    try:
        assets.read("imports", import_id)
        decision = assets.write_once(
            "reviews",
            import_id,
            {
                "decision": body.decision,
                "reason": body.reason,
                "reviewer": operator.actor,
                "reviewed_at": assets.now(),
            },
        )
        if decision["decision"] != body.decision:
            raise HTTPException(409, "版本已完成审核，不能覆盖审核结果；请修订后上传新版本")
        return decision
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.get("/dataset-imports/{import_id}")
async def get_import(import_id: str):
    try:
        record = assets.read("imports", import_id)
        return {**record, "review": assets.summary(record)["review"]}
    except (OSError, ValueError) as exc:
        public_error(exc)


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    import_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    kb_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    split: str = Field(default="", max_length=80)
    mode: str = Field(default="retrieval", pattern="^(retrieval|answer)$")
    snapshot_id: str = Field(default="", pattern=r"^([0-9a-f]{24})?$")
    subject_user_id: int | None = Field(default=None, gt=0)
    min_samples: int = Field(default=5, ge=1, le=50)
    pass_rate: float = Field(default=0.95, ge=0.5, le=1.0, allow_inf_nan=False)


@router.get("/evaluation-bindings")
async def list_bindings(kb_id: str | None = None):
    try:
        runs = assets.list_records("executions")
        items = []
        for item in assets.list_records("bindings"):
            if kb_id and item["kb_id"] != kb_id:
                continue
            related = sorted(
                (run for run in runs if run["binding_id"] == item["binding_id"]),
                key=lambda run: run["finished_at"],
                reverse=True,
            )
            items.append({**item, "latest_run": related[0] if related else None})
        return {"items": items}
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.post("/evaluation-bindings", status_code=201)
async def bind_dataset(body: Binding, operator=Depends(require_curator)):
    require_kb(body.kb_id)
    try:
        record = assets.approved_import(body.import_id)
        if body.subject_user_id is not None:
            from backend.security.task_authorization import resolve_task_authorization

            resolve_task_authorization(str(body.subject_user_id), "default")
        active = [
            c
            for c in record["cases"]
            if not c.get("archived") and (not body.split or body.split in c.get("splits", []))
        ]
        if not active:
            raise ValueError("所选子集没有可执行题目")
        if any(c["expected"].get("reason") == "permission_denied" for c in active) and body.subject_user_id is None:
            raise ValueError("权限拒答题必须选择服务端可解析的测试用户")
        if body.mode == "answer" and any(
            c["expected"]["behavior"] == "answer" and not c["expected"]["reference_answer"].strip() for c in active
        ):
            raise ValueError("答案评测的回答题必须填写参考答案")
        if body.snapshot_id:
            from backend.evaluation.corpus_snapshots import load

            snapshot = load(body.snapshot_id)
            if snapshot["kb_id"] != body.kb_id:
                raise ValueError("语料快照不属于目标知识库")
            assets.check_evidence(record, snapshot["documents"])
        else:
            assets.check_evidence(record, await asyncio.to_thread(assets.corpus_snapshot, body.kb_id))
        identity = assets.digest(body.model_dump())[:24]
        return assets.write_once(
            "bindings",
            identity,
            {
                **body.model_dump(),
                "binding_id": identity,
                "created_at": assets.now(),
                "created_by": operator.actor,
                "dataset_name": record["manifest"]["name"],
                "dataset_version": record["manifest"]["version"],
                "content_hash": record["content_hash"],
            },
        )
    except (OSError, ValueError) as exc:
        public_error(exc)


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")


@router.post("/evaluation-corpus/{kb_id}/snapshots", status_code=201)
async def freeze_corpus(kb_id: str, operator=Depends(require_curator)):
    from backend.evaluation.corpus_snapshots import capture

    require_kb(kb_id)
    try:
        record = await asyncio.to_thread(capture, kb_id, operator.actor)
        return {key: record[key] for key in ("snapshot_id", "kb_id", "content_hash", "created_at")} | {
            "warnings": record.get("capture_warnings", [])
        }
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.post("/evaluation-bindings/{binding_id}/run", status_code=202)
async def run_binding(binding_id: str, body: RunRequest, operator=Depends(require_curator)):
    try:
        binding, _ = assets.load_binding(binding_id)
        require_kb(binding["kb_id"])
        from backend.evaluation.managed_tasks import submit

        return await asyncio.to_thread(submit, binding_id, operator.actor, body.request_id)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        public_error(exc)


def _run_binding(binding_id: str, actor: str, *, run_id=None, boundary=None) -> dict:
    from backend.evaluation.managed_execution import execute_binding

    return execute_binding(binding_id, actor, run_id=run_id, boundary=boundary)


@router.get("/evaluation-tasks/{task_id}")
async def evaluation_task(task_id: str):
    from backend.evaluation.managed_tasks import task_view
    from backend.services import task_service

    task = await asyncio.to_thread(task_service.get_task, task_id)
    if not task or task.graph_name != "rag_eval" or task.tenant_id != "default":
        raise HTTPException(404, "评测任务不存在")
    return task_view(task)


@router.post("/evaluation-tasks/{task_id}/cancel")
async def cancel_evaluation_task(task_id: str):
    from backend.tasks.task_manager import cancel_task

    await evaluation_task(task_id)
    result = await asyncio.to_thread(cancel_task, task_id)
    if not result.get("ok"):
        raise HTTPException(503, "取消请求未能送达，请重试；任务可能仍在执行")
    return result


@router.post("/evaluation-tasks/{task_id}/resume")
async def resume_evaluation_task(task_id: str):
    from backend.tasks.task_manager import resume_task

    await evaluation_task(task_id)
    try:
        await asyncio.to_thread(resume_task, task_id, allow_failed=True)
        return await evaluation_task(task_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    changes: dict[str, dict | None] = Field(max_length=100)


class CandidatePromotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str = Field(min_length=1, max_length=120)
    manifest: dict
    expected: dict


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    manifest: assets.Manifest
    count: int = Field(default=5, ge=1, le=5)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")


@router.post("/dataset-synthesis", status_code=202)
async def synthesize_dataset(body: SynthesisRequest, operator=Depends(require_curator)):
    from backend.evaluation.corpus_snapshots import load
    from backend.evaluation.managed_tasks import submit_payload

    try:
        snapshot = load(body.snapshot_id)
        require_kb(snapshot["kb_id"])
        payload = {
            "snapshot_id": body.snapshot_id,
            "manifest": body.manifest.model_dump(),
            "count": body.count,
            "operation": "synthesize",
        }
        return await asyncio.to_thread(submit_payload, payload, operator.actor, body.request_id)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        public_error(exc)


@router.post("/dataset-candidate-promotions", status_code=201)
async def promote_candidate(body: CandidatePromotion, operator=Depends(require_curator)):
    from backend.evaluation.dataset.candidates import promote

    try:
        return promote(body.candidate_id, body.manifest, body.expected, operator.actor)
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.post("/dataset-imports/{identity}/revisions", status_code=201)
async def revise_dataset(identity: str, body: Revision, operator=Depends(require_curator)):
    from backend.evaluation.dataset.revisions import revise

    try:
        return revise(identity, body.version, body.changes, operator.actor)
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.get("/dataset-imports/{identity}/diff/{other}")
async def compare_versions(identity: str, other: str):
    from backend.evaluation.dataset.revisions import diff

    try:
        return diff(identity, other)
    except (OSError, ValueError) as exc:
        public_error(exc)


class VersionLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class PublicationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    binding_id: str = Field(pattern=r"^[0-9a-f]{24}$")
    enabled: bool


@router.get("/evaluation-publication-policies/{kb_id}")
async def get_publication_policy(kb_id: str):
    from backend.evaluation.publication_gate import policy

    require_kb(kb_id)
    return {"policy": policy(kb_id)}


@router.post("/evaluation-publication-policies/{kb_id}")
async def publication_policy(kb_id: str, body: PublicationPolicy, operator=Depends(require_curator)):
    from backend.evaluation.publication_gate import configure

    require_kb(kb_id)
    try:
        return configure(kb_id, body.binding_id, body.enabled, operator.actor)
    except (OSError, ValueError) as exc:
        public_error(exc)


@router.post("/dataset-imports/{identity}/labels", status_code=201)
async def label_version(identity: str, body: VersionLabel, operator=Depends(require_curator)):
    from backend.evaluation.dataset.revisions import tag

    try:
        return tag(identity, body.label, operator.actor)
    except (OSError, ValueError) as exc:
        public_error(exc)
