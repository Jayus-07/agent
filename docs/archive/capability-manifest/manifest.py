"""manifest.py — Capability 路由清单加载器（唯一事实源）

治理目标：消灭「capability 声明散落多处、漏改一处静默失效」。
此前 ALL_CAPABILITIES / ROUTE_EXAMPLES / skills 注册表三处手写，曾出现
competitor.analyze 已注册、可被规则路由、却被 LLM Router 拒绝的漂移
（tool_selector.py 注释承认"ALL_CAPABILITIES 静态表缺 competitor.analyze"）。

现在：capabilities.yaml 是唯一声明处，本模块加载 + fail-fast 校验，
types.py / vector_router.py 从这里派生。一致性由
test_registry_consistency.py 做双向守护（manifest ↔ skills/registry）。

校验规则（违例直接抛 ManifestError，启动即炸，不静默）：
  - YAML 可解析、version == 1
  - capability 名形如 <domain>.<action>（小写字母数字下划线）
  - 名字全局唯一（capability 之间、capability 与 workflow 之间）
  - routed: true → examples >= 2（建议 5-10，太少向量路由会不稳）
  - routed: false → reason 必填（说不清为什么不对用户开放就别注册）
  - workflow → examples >= 1
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_MANIFEST_PATH = Path(__file__).with_name("capabilities.yaml")
_CAP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_MIN_ROUTED_EXAMPLES = 2


class ManifestError(RuntimeError):
    """capabilities.yaml 结构/内容非法（启动期 fail-fast）。"""


@dataclass(frozen=True)
class CapabilityDecl:
    name: str
    skill: str
    routed: bool
    examples: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class WorkflowDecl:
    name: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class RouterManifest:
    capabilities: tuple[CapabilityDecl, ...]
    workflows: tuple[WorkflowDecl, ...]

    @property
    def routed_capabilities(self) -> tuple[CapabilityDecl, ...]:
        return tuple(c for c in self.capabilities if c.routed)

    @property
    def all_capability_names(self) -> tuple[str, ...]:
        """全部声明过的 capability（含 routed:false 的内部能力）。"""
        return tuple(c.name for c in self.capabilities)

    @property
    def total_example_count(self) -> int:
        """routed capability examples + workflow examples 总条数。

        VectorRouter 用它和 Chroma collection count() 对账：
        数量对不上 = manifest 改过而索引没重建 → 自动重建。
        """
        return sum(len(c.examples) for c in self.routed_capabilities) + sum(
            len(w.examples) for w in self.workflows
        )


@lru_cache(maxsize=1)
def load_manifest(path: str | None = None) -> RouterManifest:
    """加载并校验 manifest。进程内缓存；测试可传自定义路径。"""
    p = Path(path) if path else _MANIFEST_PATH
    if not p.exists():
        raise ManifestError(f"capability manifest 不存在: {p}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError(f"capability manifest YAML 解析失败: {e}") from e

    if not isinstance(raw, dict):
        raise ManifestError("capability manifest 顶层必须是 mapping")
    if raw.get("version") != 1:
        raise ManifestError(f"manifest version 必须为 1，实际: {raw.get('version')!r}")

    seen: dict[str, str] = {}  # name -> 类别（capability/workflow），查重

    caps: list[CapabilityDecl] = []
    for i, item in enumerate(raw.get("capabilities") or []):
        where = f"capabilities[{i}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} 必须是 mapping")
        name = str(item.get("name", "")).strip()
        if not _CAP_NAME_RE.match(name):
            raise ManifestError(f"{where}: capability 名须形如 <domain>.<action>，实际 {name!r}")
        if name in seen:
            raise ManifestError(f"{where}: capability 名重复: {name}")
        seen[name] = "capability"

        skill = str(item.get("skill", "")).strip()
        if not skill:
            raise ManifestError(f"{where} ({name}): skill 字段必填（供一致性测试对账）")

        routed = bool(item.get("routed", True))
        examples = tuple(str(e).strip() for e in (item.get("examples") or []) if str(e).strip())
        if len(examples) != len(item.get("examples") or []):
            raise ManifestError(f"{where} ({name}): examples 含空白项")
        if len(set(examples)) != len(examples):
            raise ManifestError(f"{where} ({name}): examples 有重复")

        if routed:
            if len(examples) < _MIN_ROUTED_EXAMPLES:
                raise ManifestError(
                    f"{where} ({name}): routed capability 至少需 {_MIN_ROUTED_EXAMPLES} 条 "
                    f"examples（建议 5-10），实际 {len(examples)}"
                )
        elif not str(item.get("reason", "")).strip():
            raise ManifestError(f"{where} ({name}): routed: false 必须给 reason")

        caps.append(
            CapabilityDecl(
                name=name,
                skill=skill,
                routed=routed,
                examples=examples,
                reason=str(item.get("reason", "")).strip(),
            )
        )

    if not caps:
        raise ManifestError("manifest 未声明任何 capability")

    wfs: list[WorkflowDecl] = []
    for i, item in enumerate(raw.get("workflows") or []):
        where = f"workflows[{i}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} 必须是 mapping")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ManifestError(f"{where}: name 必填")
        if name in seen:
            raise ManifestError(f"{where}: 名字与 capability 冲突: {name}")
        seen[name] = "workflow"

        examples = tuple(str(e).strip() for e in (item.get("examples") or []) if str(e).strip())
        if not examples:
            raise ManifestError(f"{where} ({name}): workflow 至少需 1 条 examples")

        wfs.append(WorkflowDecl(name=name, examples=examples))

    return RouterManifest(capabilities=tuple(caps), workflows=tuple(wfs))
