"""promote_prompt_default.py — 把 YAML default 升级为 DB 新版本草稿

为什么存在：运行时 Prompt 权威在 DB active_version，改 YAML defaults 只影响
新环境与降级路径（双源口径见 prompts/defaults/README.md）。默认模板变更后，
用本脚本把新模板造成 **draft 版本**，再走管理端既有发布门禁（评测→审批→
发布）激活——本脚本绝不触碰 active_version。

幂等：已存在同 template_hash 的版本时跳过（含草稿/已发布），避免重复建版。

用法（仓库根）::

    python -m backend.scripts.promote_prompt_default --key planner.system
    python -m backend.scripts.promote_prompt_default --key rag.qa \
        --change-kind minor --note "补充拒答口径"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.prompts.registry import PROMPT_REGISTRY  # noqa: E402
from backend.prompts.service import template_hash  # noqa: E402


async def promote(key: str, change_kind: str, note: str) -> int:
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository
    from backend.prompts.loader import load_defaults

    spec = PROMPT_REGISTRY.get(key)
    if spec is None:
        print(f"[promote] 未知 prompt key: {key}（先在 registry 注册）")
        return 2
    if spec.code_controlled:
        print(f"[promote] {key} 是 code_controlled，模板随代码走，不走 DB 版本")
        return 2

    template = load_defaults().get(key)
    if not template:
        print(f"[promote] {key} 无 YAML default 或被 loader 校验拒绝，先修 defaults")
        return 2

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(key)
        if prompt is None:
            print(
                f"[promote] DB 无 {key} 行——先启动 app 让 init_prompt_service "
                "seed v1，或经管理端 seed 端点补种"
            )
            return 2

        target_hash = template_hash(template)
        for ver in await repo.list_versions(prompt.id):
            if ver.template and template_hash(ver.template) == target_hash:
                print(
                    f"[promote] 跳过：{key} 已有同内容版本 v{ver.version}"
                    f"（status={ver.status}）"
                    + (f"，当前 active=v{prompt.active_version}" if prompt.active_version else "")
                )
                return 0

        result = await repo.create_version(
            prompt.id,
            template,
            variables=[v.name for v in spec.variables],
            status="draft",
            change_kind=change_kind,
            change_note=note or "YAML default 升级草稿（promote_prompt_default）",
            created_by="promote_prompt_default",
        )
        await repo.write_audit(
            key, "create_draft",
            to_version=result.version,
            actor="promote_prompt_default",
            detail={"source": "yaml_default", "change_kind": change_kind},
        )
        await session.commit()
        print(
            f"[promote] 已建草稿：{key} v{result.version}（status=draft，"
            f"active 仍为 v{prompt.active_version}）——"
            "下一步：管理端发布门禁（评测→审批→发布）激活"
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YAML default → DB draft 版本")
    parser.add_argument("--key", required=True, help="prompt key，如 planner.system")
    parser.add_argument(
        "--change-kind", default="minor", choices=["major", "minor", "patch"],
        help="变更类型（默认 minor）",
    )
    parser.add_argument("--note", default="", help="变更说明")
    args = parser.parse_args()

    try:
        return asyncio.run(promote(args.key, args.change_kind, args.note))
    except Exception as exc:  # noqa: BLE001 — CLI 出口统一可读报错
        print(f"[promote] 失败：{exc}")
        print("（DB 不可达？本脚本需要 agent_memory PG（5433），先恢复运行栈）")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
