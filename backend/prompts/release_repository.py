"""Prompt 发布记录仓储。

发布记录与 Prompt 版本分表，所有状态迁移使用带前置状态的 SQL 更新，
避免重复回调把终态重新改回运行中。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.memory.database import AsyncSessionLocal


class PromptReleaseRepository:
    """基于 agent_memory 的 Prompt 发布记录仓储。"""

    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _row(result: Any) -> dict[str, Any]:
        return dict(result.mappings().one())

    async def create_release(self, **values: Any) -> dict[str, Any]:
        query = text(
            """
            INSERT INTO ai.prompt_release_records (
                release_id, prompt_key, version, target_env, status,
                eval_suite, dataset_provenance, prompt_snapshot,
                tool_contract_fingerprint, model_binding_fingerprint, executor,
                created_by
            ) VALUES (
                :release_id, :prompt_key, :version, :target_env, 'pending',
                :eval_suite, CAST(:dataset_provenance AS jsonb),
                CAST(:prompt_snapshot AS jsonb), :tool_contract_fingerprint,
                :model_binding_fingerprint, :executor, :created_by
            )
            ON CONFLICT (prompt_key, version, target_env, eval_suite)
            DO UPDATE SET updated_at = now()
            RETURNING *
            """
        )
        params = {
            **values,
            "dataset_provenance": _json(values.get("dataset_provenance")),
            "prompt_snapshot": _json(values.get("prompt_snapshot")),
        }
        async with self._session_factory() as session:
            result = await session.execute(query, params)
            row = self._row(result)
            await session.commit()
            return row

    async def get_release(self, release_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT * FROM ai.prompt_release_records "
                    "WHERE release_id = :release_id"
                ),
                {"release_id": release_id},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def mark_running(self, release_id: str, external_run_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE ai.prompt_release_records
                    SET status = 'running', external_run_id = :external_run_id,
                        updated_at = now()
                    WHERE release_id = :release_id
                      AND (
                        status = 'pending'
                        OR (status = 'running' AND external_run_id = :external_run_id)
                      )
                    RETURNING *
                    """
                ),
                {"release_id": release_id, "external_run_id": external_run_id},
            )
            row = self._row(result)
            await session.commit()
            return row

    async def record_result(self, release_id: str, **values: Any) -> dict[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE ai.prompt_release_records
                    SET status = :status,
                        eval_run_id = COALESCE(:eval_run_id, eval_run_id),
                        metrics = CAST(:metrics AS jsonb),
                        dataset_provenance = CASE
                            WHEN CAST(:provenance AS jsonb) = '{}'::jsonb
                            THEN dataset_provenance
                            ELSE dataset_provenance || CAST(:provenance AS jsonb)
                        END,
                        failure_reason = :failure_reason,
                        updated_at = now()
                    WHERE release_id = :release_id
                      AND status IN ('pending', 'running')
                    RETURNING *
                    """
                ),
                {
                    "release_id": release_id,
                    "status": values["status"],
                    "eval_run_id": values.get("eval_run_id"),
                    "metrics": _json(values.get("metrics")),
                    "provenance": _json(values.get("provenance")),
                    "failure_reason": values.get("failure_reason", ""),
                },
            )
            row = self._row(result)
            await session.commit()
            return row

    async def approve(self, release_id: str, actor: str) -> dict[str, Any]:
        return await self._transition(
            release_id,
            from_status="passed",
            to_status="approved",
            actor_column="approved_by",
            actor=actor,
        )

    async def mark_published(self, release_id: str, actor: str) -> dict[str, Any]:
        return await self._transition(
            release_id,
            from_status="approved",
            to_status="published",
            actor_column="published_by",
            actor=actor,
        )

    async def mark_rolled_back(self, release_id: str, actor: str) -> dict[str, Any]:
        return await self._transition(
            release_id,
            from_status="published",
            to_status="rolled_back",
            actor_column="rollback_by",
            actor=actor,
        )

    async def _transition(
        self,
        release_id: str,
        *,
        from_status: str,
        to_status: str,
        actor_column: str,
        actor: str,
    ) -> dict[str, Any]:
        if actor_column not in {"approved_by", "published_by", "rollback_by"}:
            raise ValueError(f"非法发布人字段: {actor_column}")
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    f"""
                    UPDATE ai.prompt_release_records
                    SET status = :to_status, {actor_column} = :actor,
                        updated_at = now()
                    WHERE release_id = :release_id AND status = :from_status
                    RETURNING *
                    """
                ),
                {
                    "release_id": release_id,
                    "from_status": from_status,
                    "to_status": to_status,
                    "actor": actor,
                },
            )
            row = self._row(result)
            await session.commit()
            return row


def _json(value: Any) -> str:
    import json

    return json.dumps(value or {}, ensure_ascii=False)


__all__ = ["PromptReleaseRepository"]
