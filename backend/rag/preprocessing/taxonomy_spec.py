"""元数据 taxonomy 的版本化加载与规范化入口。

taxonomy 文件是 doc_type、business_domain、规则和提示词枚举的共同事实源。
启动时严格校验，避免半套规则进入线上进程。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


class TaxonomyConfigError(ValueError):
    """taxonomy 配置不满足运行时契约。"""


@dataclass(frozen=True)
class RuleSpec:
    """一个可审计的正则证据规则。"""

    rule_id: str
    pattern: str
    weight: int


@dataclass(frozen=True)
class MetadataTaxonomy:
    """元数据类型、领域和规则的不可变快照。"""

    version: str
    doc_types: Sequence[str]
    domains: Sequence[str]
    doc_type_descriptions: Mapping[str, str]
    doc_type_rules: Mapping[str, Sequence[RuleSpec]]
    filename_hints: Mapping[str, str]
    folder_hints: Mapping[str, str]
    domain_rules: Mapping[str, Mapping[str, int]]
    aliases: Mapping[str, Mapping[str, str]]
    r0_allowlist: frozenset[str]
    raw_text: str

    def normalize_doc_type(self, value: object) -> str:
        """将外部值归一到 canonical doc_type；未知值安全回落 general。"""
        normalized = str(value or "").strip().lower()
        normalized = self.aliases.get("doc_type", {}).get(normalized, normalized)
        return normalized if normalized in self.doc_types else "general"

    def normalize_domain(self, value: object) -> str:
        """将外部值归一到 canonical domain；未知值安全回落 general。"""
        normalized = str(value or "").strip().lower()
        normalized = self.aliases.get("domain", {}).get(normalized, normalized)
        return normalized if normalized in self.domains else "general"

    def legacy_doc_type_rules(self) -> dict[str, list[tuple[str, int]]]:
        """导出旧分类器需要的规则形状，便于渐进迁移。"""
        return {
            doc_type: [(rule.pattern, rule.weight) for rule in rules]
            for doc_type, rules in self.doc_type_rules.items()
            if doc_type != "general"
        }


_TAXONOMY_PATH = Path(__file__).with_name("metadata_taxonomy.yaml")

# doc_type → 中文**显示名**（引用标注等展示层用）。
#
# 为什么放在这里：doc_type 清单的事实源是 metadata_taxonomy.yaml，而「某个类型
# 该显示成什么」也是同一份事实的一部分——此前它被手写在两处且互为反表
# （rag/citation.py 的 _TYPE_LABEL_MAP 与 agents/reporter/context_filter.py 的
# type_label_map），两侧还都只覆盖 9 类（缺 7 类 → 展示层悄悄回落英文原始码）。
# 现在只此一份，反向映射由消费方对这份 dict 求逆得到。
#
# 不加进 yaml 是有意的：yaml 的 sha256 指纹（taxonomy_fingerprint）被写进
# 入库 lineage 与 LR 模型版本号，改它会让新旧数据口径对不上。
DOC_TYPE_LABELS: Mapping[str, str] = {
    "general": "通用",
    "listing": "Listing",
    "sop": "SOP",
    "ad_policy": "广告政策",
    "faq": "FAQ",
    "product_spec": "产品规格",
    "training": "培训",
    "policy": "制度规范",
    "compliance": "合规",
    "legal": "法务",
    "security": "安全",
    "financial": "财务",
    "customer_data": "客户数据",
    "contract_template": "合同模板",
    "travel_guide": "旅游攻略",
}


def doc_type_label(doc_type: str) -> str:
    """doc_type 的中文显示名；未知类型原样返回（不臆造）。"""
    return DOC_TYPE_LABELS.get(doc_type, doc_type)


def _error(message: str) -> TaxonomyConfigError:
    return TaxonomyConfigError(f"metadata taxonomy invalid: {message}")


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(f"{field} must be an object")
    return value


def _normalize_aliases(
    raw: Any,
    doc_types: Sequence[str],
    domains: Sequence[str],
) -> dict[str, dict[str, str]]:
    aliases_raw = _require_mapping(raw, "aliases")
    result: dict[str, dict[str, str]] = {"doc_type": {}, "domain": {}}
    targets = {"doc_type": set(doc_types), "domain": set(domains)}
    for kind in result:
        values = aliases_raw.get(kind, {})
        values = _require_mapping(values, f"aliases.{kind}")
        for alias, target in values.items():
            alias_value = str(alias).strip().lower()
            target_value = str(target).strip().lower()
            if not alias_value:
                raise _error(f"empty {kind} alias")
            if target_value not in targets[kind]:
                raise _error(
                    f"alias {kind}.{alias_value} targets unknown value "
                    f"{target_value}"
                )
            result[kind][alias_value] = target_value
    return result


def _load_taxonomy(path: Path) -> MetadataTaxonomy:
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _error(f"cannot read {path}: {exc}") from exc

    try:
        payload = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise _error(f"invalid YAML: {exc}") from exc
    root = _require_mapping(payload, "root")

    version = str(root.get("version", "")).strip()
    if not version:
        raise _error("version is required")

    doc_type_payload = _require_mapping(root.get("doc_types"), "doc_types")
    domains_payload = _require_mapping(root.get("domains"), "domains")
    doc_types = tuple(str(value).strip().lower() for value in doc_type_payload)
    domains = tuple(str(value).strip().lower() for value in domains_payload)
    if len(doc_types) != len(set(doc_types)):
        raise _error("duplicate doc_type")
    if len(domains) != len(set(domains)):
        raise _error("duplicate domain")
    if "general" not in doc_types or "general" not in domains:
        raise _error("general must exist in doc_types and domains")

    # 展示名覆盖度：yaml 新增了 doc_type 但 DOC_TYPE_LABELS 没跟上 → 启动即失败。
    # 宁可拒绝启动，也不要让引用标注悄悄把英文原始码当标签显示给用户。
    missing_labels = [d for d in doc_types if d not in DOC_TYPE_LABELS]
    if missing_labels:
        raise _error(
            f"doc_types 缺中文显示名，请补 taxonomy_spec.DOC_TYPE_LABELS：{missing_labels}"
        )
    unknown_labels = sorted(set(DOC_TYPE_LABELS) - set(doc_types))
    if unknown_labels:
        raise _error(
            f"DOC_TYPE_LABELS 有 yaml 里不存在的 doc_type（标签已过期）：{unknown_labels}"
        )

    doc_type_descriptions: dict[str, str] = {}
    doc_type_rules: dict[str, tuple[RuleSpec, ...]] = {}
    rule_ids: set[str] = set()
    for doc_type, raw_spec in doc_type_payload.items():
        doc_type = str(doc_type).strip().lower()
        spec = _require_mapping(raw_spec, f"doc_types.{doc_type}")
        doc_type_descriptions[doc_type] = str(spec.get("description", ""))
        raw_rules = spec.get("rules", [])
        if not isinstance(raw_rules, list):
            raise _error(f"doc_types.{doc_type}.rules must be a list")
        parsed_rules: list[RuleSpec] = []
        for index, raw_rule in enumerate(raw_rules):
            rule = _require_mapping(
                raw_rule, f"doc_types.{doc_type}.rules[{index}]"
            )
            rule_id = str(rule.get("rule_id", "")).strip()
            pattern = str(rule.get("pattern", ""))
            try:
                weight = int(rule.get("weight"))
            except (TypeError, ValueError) as exc:
                raise _error(f"rule {rule_id or index} has invalid weight") from exc
            if not rule_id or rule_id in rule_ids:
                raise _error(f"duplicate or empty rule_id: {rule_id!r}")
            if weight <= 0:
                raise _error(f"rule {rule_id} weight must be positive")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise _error(f"rule {rule_id} has invalid regex: {exc}") from exc
            rule_ids.add(rule_id)
            parsed_rules.append(RuleSpec(rule_id, pattern, weight))
        doc_type_rules[doc_type] = tuple(parsed_rules)

    domain_rules: dict[str, Mapping[str, int]] = {}
    for domain, raw_spec in domains_payload.items():
        domain = str(domain).strip().lower()
        spec = _require_mapping(raw_spec, f"domains.{domain}")
        raw_rules = _require_mapping(spec.get("rules", {}), f"domains.{domain}.rules")
        parsed: dict[str, int] = {}
        for keyword, raw_weight in raw_rules.items():
            try:
                weight = int(raw_weight)
            except (TypeError, ValueError) as exc:
                raise _error(
                    f"domain rule {domain}.{keyword} has invalid weight"
                ) from exc
            if weight <= 0:
                raise _error(f"domain rule {domain}.{keyword} weight must be positive")
            parsed[str(keyword)] = weight
        domain_rules[domain] = parsed

    filename_hints = {
        str(key): str(value).strip().lower()
        for key, value in _require_mapping(
            root.get("filename_hints", {}), "filename_hints"
        ).items()
    }
    folder_hints = {
        str(key): str(value).strip().lower()
        for key, value in _require_mapping(
            root.get("folder_hints", {}), "folder_hints"
        ).items()
    }
    known_doc_types = set(doc_types)
    for field, values in (
        ("filename_hints", filename_hints),
        ("folder_hints", folder_hints),
    ):
        for hint, target in values.items():
            if target not in known_doc_types:
                raise _error(f"{field}.{hint} targets unknown doc_type {target}")

    aliases = _normalize_aliases(root.get("aliases", {}), doc_types, domains)
    raw_allowlist = root.get("r0_allowlist", [])
    if not isinstance(raw_allowlist, list):
        raise _error("r0_allowlist must be a list")
    r0_allowlist = frozenset(str(rule_id).strip() for rule_id in raw_allowlist)
    unknown_r0 = r0_allowlist - rule_ids
    if unknown_r0:
        raise _error(f"r0_allowlist contains unknown rule ids: {sorted(unknown_r0)}")

    return MetadataTaxonomy(
        version=version,
        doc_types=doc_types,
        domains=domains,
        doc_type_descriptions=doc_type_descriptions,
        doc_type_rules=doc_type_rules,
        filename_hints=filename_hints,
        folder_hints=folder_hints,
        domain_rules=domain_rules,
        aliases=aliases,
        r0_allowlist=r0_allowlist,
        raw_text=raw_text,
    )


@lru_cache(maxsize=1)
def get_taxonomy() -> MetadataTaxonomy:
    """加载并缓存当前 taxonomy；配置错误在启动/首次使用时直接暴露。"""
    return _load_taxonomy(_TAXONOMY_PATH)


def fingerprint_for_path(path: Path) -> str:
    """计算 taxonomy 文件指纹，兼顾规范化数据和原始变更。"""
    try:
        raw_text = path.read_text(encoding="utf-8")
        payload = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as exc:
        raise _error(f"cannot fingerprint {path}: {exc}") from exc
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest_input = f"{canonical}\n{raw_text}".encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


def taxonomy_fingerprint() -> str:
    """返回当前 taxonomy 的完整 SHA-256 指纹。"""
    return fingerprint_for_path(_TAXONOMY_PATH)


def metadata_rule_version() -> str:
    """返回可写入决策和缓存键的 taxonomy 规则版本。"""
    taxonomy = get_taxonomy()
    return f"{taxonomy.version}:{taxonomy_fingerprint()[:12]}"


def normalize_doc_type(value: object) -> str:
    return get_taxonomy().normalize_doc_type(value)


def normalize_domain(value: object) -> str:
    return get_taxonomy().normalize_domain(value)
