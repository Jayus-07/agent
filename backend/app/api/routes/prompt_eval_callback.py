"""外部 Prompt 评测结果回调。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.routes.prompt_releases import get_release_service
from backend.prompts.release_models import PromptReleaseRecord, PromptReleaseStatus

router = APIRouter(prefix="/internal/prompt-evals", tags=["内部-Prompt评测"])


@router.post("/callback")
async def prompt_eval_callback(request: Request) -> dict[str, Any]:
    """接收 GitHub/外部评测回调，仅写入评测终态，不自动发布。"""
    raw_body = await request.body()
    _verify_signature(request, raw_body)
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="评测回调 JSON 无效") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="评测回调必须是 JSON object")

    release_id = str(payload.get("release_id") or "").strip()
    if not release_id:
        raise HTTPException(status_code=422, detail="release_id 不能为空")
    service = get_release_service()
    try:
        release = await service.get(release_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Prompt release 不存在") from exc

    _verify_release_identity(release, payload)
    status = str(payload.get("status") or "").strip().lower()
    if status not in {PromptReleaseStatus.PASSED.value, PromptReleaseStatus.FAILED.value}:
        raise HTTPException(status_code=422, detail="评测回调终态只能是 passed 或 failed")

    if release.status in {PromptReleaseStatus.PASSED, PromptReleaseStatus.FAILED}:
        if release.status.value != status:
            raise HTTPException(status_code=409, detail="release 已有相反的终态评测结果")
        return _release_payload(release, idempotent=True)

    if release.status != PromptReleaseStatus.RUNNING:
        raise HTTPException(status_code=409, detail="release 尚未进入 running，拒绝外部回调")

    try:
        result = await service.record_result(
            release_id,
            {
                "status": status,
                "run_id": payload.get("eval_run_id") or payload.get("run_id"),
                "metrics": payload.get("metrics") or {},
                "provenance": payload.get("provenance") or {},
                "failure_reason": payload.get("failure_reason") or "",
            },
            actor=str(payload.get("actor") or "prompt-eval:callback"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _release_payload(result, idempotent=False)


def _verify_signature(request: Request, raw_body: bytes) -> None:
    secret = os.getenv("PROMPT_EVAL_CALLBACK_SECRET", "").strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={"code": "CALLBACK_NOT_CONFIGURED", "message": "未配置评测回调密钥"},
        )
    timestamp = request.headers.get("X-Prompt-Eval-Timestamp", "").strip()
    signature = request.headers.get("X-Prompt-Eval-Signature", "").strip()
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="评测回调时间戳无效") from exc
    tolerance = int(os.getenv("PROMPT_EVAL_CALLBACK_TOLERANCE_SECONDS", "300"))
    if abs(time.time() - timestamp_value) > tolerance:
        raise HTTPException(status_code=401, detail="评测回调已过期")

    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    provided = signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="评测回调签名无效")


def _verify_release_identity(release: PromptReleaseRecord, payload: dict[str, Any]) -> None:
    if str(payload.get("prompt_key") or release.prompt_key) != release.prompt_key:
        raise HTTPException(status_code=409, detail="回调 prompt_key 与 release 不匹配")
    if payload.get("version") is not None and int(payload["version"]) != release.version:
        raise HTTPException(status_code=409, detail="回调 version 与 release 不匹配")
    external_run_id = str(payload.get("external_run_id") or "").strip()
    if not external_run_id or external_run_id != release.external_run_id:
        raise HTTPException(status_code=409, detail="回调 external_run_id 与 release 不匹配")


def _release_payload(record: PromptReleaseRecord, *, idempotent: bool) -> dict[str, Any]:
    return {
        "release_id": record.release_id,
        "prompt_key": record.prompt_key,
        "version": record.version,
        "status": record.status.value,
        "eval_run_id": record.eval_run_id,
        "idempotent": idempotent,
    }


__all__ = ["router"]
