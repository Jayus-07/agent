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
  - workflow 名须形如 ^[a-z][a-z0-9_]*$（纯蛇形，不带点；带点是 capability 的
    命名空间）→ workflow 至少需 1 条 examples
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_MANIFEST_PATH = Path(__file__).with_name("capabilities.yaml")
_CAP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_WF_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MIN_ROUTED_EXAMPLES = 2

# 分层路由（2026-09-22）：粗域与能力风险声明
_VALID_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")
# 默认风险：未声明 risk_level 的能力按 LOW 处理（只读语义）；
# HIGH 能力永不进 Fast Path（双保险：fast_path_enabled 也须显式 false）
_DEFAULT_RISK_LEVEL = "LOW"


class ManifestError(RuntimeError):
    """capabilities.yaml 结构/内容非法（启动期 fail-fast）。"""


@dataclass(frozen=True)
class DomainDecl:
    """粗业务域声明（CoarseIntentClassifier 的分类对象）。

    description/examples 供 embedding prototype 分类器建域心；
    keywords 供规则 hint（强信号直接判域，弱信号做分数加成）。
    """
    name: str
    description: str
    examples: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityDecl:
    name: str
    skill: str
    routed: bool
    examples: tuple[str, ...]
    reason: str = ""
    # 规则路由关键词（可选，2026-09-15 迁入）：单意图强/弱信号判定用。
    # 未声明时 rule_router 回退内置缺省表（行为零变化）。
    rule_keywords: tuple[str, ...] = ()
    # 分层路由三字段（2026-09-22）：
    #   domain            归属粗域（须在 domains 段声明，违例 fail-fast）
    #   risk_level        LOW/MEDIUM/HIGH；HIGH 永不进 Fast Path
    #   fast_path_enabled 细路由 Fast Path 候选白名单
    domain: str = ""
    risk_level: str = _DEFAULT_RISK_LEVEL
    fast_path_enabled: bool = True


@dataclass(frozen=True)
class WorkflowDecl:
    name: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class RouterManifest:
    capabilities: tuple[CapabilityDecl, ...]
    workflows: tuple[WorkflowDecl, ...]
    domains: tuple[DomainDecl, ...] = ()

    @property
    def routed_capabilities(self) -> tuple[CapabilityDecl, ...]:
        return tuple(c for c in self.capabilities if c.routed)

    @property
    def all_capability_names(self) -> tuple[str, ...]:
        """全部声明过的 capability（含 routed:false 的内部能力）。"""
        return tuple(c.name for c in self.capabilities)

    @property
    def domain_names(self) -> tuple[str, ...]:
        return tuple(d.name for d in self.domains)

    @property
    def domain_by_name(self) -> dict[str, DomainDecl]:
        return {d.name: d for d in self.domains}

    @property
    def capabilities_by_domain(self) -> dict[str, tuple[str, ...]]:
        """粗域 → 该域全部 capability 名（含 routed:false，保序）。

        Domain Tool Registry 的派生视图：细路由只在域内候选中选工具。
        """
        buckets: dict[str, list[str]] = {}
        for c in self.capabilities:
            if c.domain:
                buckets.setdefault(c.domain, []).append(c.name)
        return {k: tuple(v) for k, v in buckets.items()}

    @property
    def rule_keyword_groups(self) -> dict[str, tuple[str, ...]]:
        """声明了 rule_keywords 的 capability → 关键词组（保持 yaml 顺序）。"""
        return {
            c.name: c.rule_keywords
            for c in self.capabilities if c.rule_keywords
        }

    @property
    def domain_keyword_groups(self) -> dict[str, tuple[str, ...]]:
        """粗域 → 规则 hint 关键词组（CoarseIntentClassifier 消费）。"""
        return {d.name: d.keywords for d in self.domains if d.keywords}

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

    seen: dict[str, str] = {}  # name -> 类别（capability/workflow/domain），查重

    # ── domains 段（分层路由粗域声明）──────────────────────────
    domains: list[DomainDecl] = []
    for i, item in enumerate(raw.get("domains") or []):
        where = f"domains[{i}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} 必须是 mapping")
        name = str(item.get("name", "")).strip()
        if not _WF_NAME_RE.match(name):
            raise ManifestError(f"{where}: 域名须为小写蛇形，实际 {name!r}")
        if name in seen:
            raise ManifestError(f"{where}: 域名重复: {name}")
        seen[name] = "domain"
        description = str(item.get("description", "")).strip()
        if not description:
            raise ManifestError(f"{where} ({name}): description 必填（embedding 域心文本）")
        examples = tuple(str(e).strip() for e in (item.get("examples") or []) if str(e).strip())
        if len(examples) < 2:
            raise ManifestError(
                f"{where} ({name}): 至少需 2 条 examples（prototype 域心质量），实际 {len(examples)}"
            )
        if len(set(examples)) != len(examples):
            raise ManifestError(f"{where} ({name}): examples 有重复")
        keywords = tuple(str(k).strip() for k in (item.get("keywords") or []) if str(k).strip())
        domains.append(DomainDecl(name=name, description=description, examples=examples, keywords=keywords))

    if not domains:
        raise ManifestError("manifest 未声明任何 domain（分层路由需要 domains 段）")

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

        # ── 分层路由三字段（2026-09-22）────────────────────────
        domain = str(item.get("domain", "")).strip()
        if not domain:
            raise ManifestError(f"{where} ({name}): domain 必填（粗域归属，分层路由唯一事实源）")
        if domain not in seen:
            raise ManifestError(
                f"{where} ({name}): domain {domain!r} 未在 domains 段声明"
            )
        risk = str(item.get("risk_level", _DEFAULT_RISK_LEVEL)).strip().upper()
        if risk not in _VALID_RISK_LEVELS:
            raise ManifestError(
                f"{where} ({name}): risk_level 须为 {'/'.join(_VALID_RISK_LEVELS)}，实际 {risk!r}"
            )
        fast_path = bool(item.get("fast_path_enabled", True))
        if risk == "HIGH" and fast_path:
            raise ManifestError(
                f"{where} ({name}): HIGH 风险能力禁止 fast_path_enabled: true"
                "（副作用工具必须走细路由 + 审批门）"
            )

        caps.append(
            CapabilityDecl(
                name=name,
                skill=skill,
                routed=routed,
                examples=examples,
                reason=str(item.get("reason", "")).strip(),
                rule_keywords=tuple(
                    str(k).strip() for k in (item.get("rule_keywords") or []) if str(k).strip()
                ),
                domain=domain,
                risk_level=risk,
                fast_path_enabled=fast_path,
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
        if not _WF_NAME_RE.match(name):
            raise ManifestError(
                f"{where}: workflow 名须形如 ^[a-z][a-z0-9_]*$（纯蛇形、不带点），"
                f"实际 {name!r}"
            )
        if name in seen:
            raise ManifestError(f"{where}: 名字与 capability 冲突: {name}")
        seen[name] = "workflow"

        examples = tuple(str(e).strip() for e in (item.get("examples") or []) if str(e).strip())
        if not examples:
            raise ManifestError(f"{where} ({name}): workflow 至少需 1 条 examples")

        wfs.append(WorkflowDecl(name=name, examples=examples))

    return RouterManifest(capabilities=tuple(caps), workflows=tuple(wfs), domains=tuple(domains))
