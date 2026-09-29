"""从 Capability manifest 绑定 Skill 兼容 metadata。

Skill 只实现执行行为。描述、输入契约和 Planner 示例的唯一作者源是
``orchestration/router/capabilities.yaml``；本模块仅在启动期向旧消费方
投影兼容属性。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from backend.orchestration.router.manifest import CapabilityDecl, load_manifest


def bind_manifest_metadata(skills: Iterable[object]) -> None:
    """将 manifest CapabilityDecl 绑定到已实例化的 Skill。

    每个 Skill 的 ``name`` 必须与 manifest ``skill`` 字段一致。多 capability
    Skill 保留旧的聚合属性，同时在 ``capability_metadata`` 保存逐能力参数，
    使执行期参数校验不再依赖 Skill 类上的重复定义。
    """
    declarations_by_skill: dict[str, list[CapabilityDecl]] = defaultdict(list)
    for declaration in load_manifest().capabilities:
        declarations_by_skill[declaration.skill].append(declaration)

    seen_skill_names: set[str] = set()
    for skill in skills:
        skill_name = str(getattr(skill, "name", ""))
        if not skill_name:
            raise RuntimeError("Skill 缺少 name，无法绑定 Capability manifest")
        if skill_name in seen_skill_names:
            raise RuntimeError(f"Skill name 重复: {skill_name}")
        seen_skill_names.add(skill_name)

        declarations = declarations_by_skill.pop(skill_name, [])
        if not declarations:
            raise RuntimeError(f"Skill {skill_name!r} 没有 manifest capability")

        per_capability = {
            declaration.name: {
                "description": declaration.description,
                "params_schema": dict(declaration.params_schema),
                "planner_examples": [
                    dict(example) for example in declaration.planner_examples
                ],
            }
            for declaration in declarations
        }
        # 向后兼容：旧调用方仍可在 Skill 上读取聚合 metadata；新代码应按
        # capability 从 Capability Registry / capability_metadata 读取。
        primary = declarations[0]
        skill.capabilities = [declaration.name for declaration in declarations]
        skill.description = primary.description
        skill.params_schema = dict(primary.params_schema)
        skill.examples = [
            dict(example) for example in primary.planner_examples
        ]
        skill.capability_metadata = per_capability

        skill_class = type(skill)
        skill_class.capabilities = list(skill.capabilities)
        skill_class.description = skill.description
        skill_class.params_schema = dict(skill.params_schema)
        skill_class.examples = [dict(example) for example in skill.examples]
        skill_class.capability_metadata = per_capability

    if declarations_by_skill:
        orphaned = ", ".join(sorted(declarations_by_skill))
        raise RuntimeError(f"manifest 引用了未注册 Skill: {orphaned}")
