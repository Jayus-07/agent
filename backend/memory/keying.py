"""memory_key / structured_value / tenant_id 规范化 + 写入裁决结果契约。

（STOP C，Memory Production Closure）

- memory_key 表达「属性身份」（如 project.main_llm），同 key 不同值构成
  事实版本链；不能把值编进 key（project.main_llm.deepseek 等于消灭冲突）。
- key/value 一律经代码层 normalize+validate，模型输出不可直接信任；
  key/value 必须成对——任一非法则双双置 NULL（走 unkeyed 语义路径）。
- tenant_id 运行时归一：未声明租户 → 'default'（本部署唯一真实租户，
  与 auth.users 现值一致）；任何查询永远携带具体 tenant 值，不存在
  「缺失 → 查所有租户」的 fail-open 形态。'quarantine' 为迁移保留哨兵，
  运行时永不产出。
"""
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# scope 租户哨兵（迁移保留值，运行时归一永不产出）
TENANT_QUARANTINE = "quarantine"
# 未声明租户的运行时归一值（与 auth.users 现网唯一真实租户一致）
TENANT_DEFAULT = "default"

_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
# dot-separated snake_case：段首字母、段内 [a-z0-9_]、1~4 段
_MEMORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}$")

MEMORY_KEY_MAX_LEN = 128
MEMORY_VALUE_MAX_LEN = 256


def normalize_tenant_id(raw: str | None) -> str:
    """租户归一：空/未声明 → default；非法字符 → default（不 fail-open）。

    fail-closed 的含义是「查询永远带具体 tenant 值」，而非「缺租户即拒绝」
    ——本部署唯一真实租户为 default（见模块 docstring），归一后 scope
    仍然精确：tenant=X 的请求永远查不到 default 的记忆，反之亦然。
    """
    value = (raw or "").strip()
    if not value or value == TENANT_QUARANTINE or not _TENANT_RE.fullmatch(value):
        return TENANT_DEFAULT
    return value


def normalize_memory_key(raw: str | None) -> str | None:
    """memory_key 归一：NFKC → trim → lower → 空白转下划线 → 格式校验。

    非法（空/超长/字符集外/纯数字段/控制字符）→ None（调用方置 NULL，
    走 unkeyed 路径）。key 只表达属性类别，字符集白名单天然挡住
    email（@）等 PII 形态；纯数字段拒绝（防手机号等进 key）。
    """
    if raw is None:
        return None
    value = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"-{2,}", "-", value).strip("-_")
    if not value or len(value) > MEMORY_KEY_MAX_LEN:
        return None
    for seg in value.split("."):
        if seg.isdigit():  # 纯数字段：属性身份不应是数字（防 PII 形态）
            return None
    if not _MEMORY_KEY_RE.fullmatch(value):
        return None
    return value


def normalize_memory_value(raw: str | None) -> str | None:
    """structured_value 归一：NFKC → trim → lower。

    不做激进标准化（Qwen3-8B 与 Qwen3-14B 必须保持可区分；lower 不影响
    型号数字区分，仅消除大小写漂移）。空/超长 → None（连同 key 双双置空）。
    """
    if raw is None:
        return None
    value = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    if not value or len(value) > MEMORY_VALUE_MAX_LEN:
        return None
    return value


class StoreOutcome(str, Enum):
    """写入裁决结果（§31：禁止再用 True/False 表达所有行为）。"""

    INSERTED = "INSERTED"
    DUPLICATE = "DUPLICATE"
    REAFFIRMED = "REAFFIRMED"
    SUPERSEDED = "SUPERSEDED"
    CONFLICT_BLOCKED_EXPLICIT = "CONFLICT_BLOCKED_EXPLICIT"
    REJECTED = "REJECTED"


@dataclass
class StoreResult:
    """store_with_resolution 的裁决产物。"""

    outcome: StoreOutcome
    memory_id: str | None = None
    superseded_memory_id: str | None = None
    reason: str = ""

    @property
    def stored(self) -> bool:
        """兼容 store_single()->bool 的语义：事实被接受（新增/替换/确认）。"""
        return self.outcome in (
            StoreOutcome.INSERTED,
            StoreOutcome.SUPERSEDED,
            StoreOutcome.REAFFIRMED,
        )


def origin_priority(origin: str) -> int:
    """写入通道优先级：explicit > inferred > legacy（§26）。

    priority 表达 provenance 通道可信序，不与 confidence 数值混用。
    """
    return {"explicit": 2, "inferred": 1, "legacy": 0}.get(origin, 0)
