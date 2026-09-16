"""config/indexing_rules.py — 索引管线规则阈值单一收口（阶段 1 改造）

背景：MinHash 0.85、质量门禁 40/60 分界、分类加权（文件名+100/标题+20/
文件夹+40）、仲裁分差 ≤5 等阈值原先散落在 metadata.py / indexer.py 的
函数体内，调参要改代码重启。本模块把它们收口为一处：

优先级（高 → 低）：
  1. JSON 覆盖文件（INDEXING_RULES_FILE 指定路径，mtime 变化自动热加载）
  2. 环境变量（进程启动时读取）
  3. 代码默认值

用法：
    from backend.config.indexing_rules import get_rules
    rules = get_rules()
    if sim > rules.near_dup_similarity_threshold: ...

get_rules() 带 60s TTL 缓存，JSON 覆盖文件修改后最多 60s 生效，无需重启。
测试中可用 override_rules() / reset_rules_override() 临时改写。
"""
import json
import os
import threading
import time
from dataclasses import dataclass, field, fields, replace
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# JSON 覆盖文件路径（可选）。运营/调参场景：改文件即生效，不发版不重启。
RULES_OVERRIDE_FILE = os.getenv("INDEXING_RULES_FILE", "").strip()
_HOT_RELOAD_TTL_SECONDS = float(os.getenv("INDEXING_RULES_TTL_SECONDS", "60"))


@dataclass(frozen=True)
class IndexingRules:
    """索引管线全部可调阈值。字段分组注释对应管线阶段。"""

    # ── MinHash 近重复检测（→ pending_review 审核队列的唯一入口）──
    near_dup_similarity_threshold: float = 0.85   # > 视为近重复
    minhash_n_gram: int = 3                       # 字级 n-gram
    minhash_n_hashes: int = 128                   # 签名长度（越大越准越慢）
    minhash_text_max_chars: int = 5000            # 参与签名的全文前 N 字

    # ── 质量门禁 assess_quality（满分 100）──
    quality_completeness_max: int = 40
    quality_structure_max: int = 30
    quality_noise_max: int = 20
    quality_uniqueness_max: int = 10
    quality_reject_below: int = 40                # < reject（未通过）
    quality_warn_below: int = 60                  # < warn

    # ── 类型分类加权 classify_with_confidence ──
    classify_filename_weight: int = 100           # 文件名命中
    classify_title_weight: int = 20               # 标题词命中
    classify_folder_weight: int = 40              # 文件夹路径命中
    classify_folder_conf_bonus: float = 0.3       # 文件夹命中且 top 匹配 → confidence +
    legal_clause_bonus_per: int = 5               # 「第 N 条」每个加分
    legal_clause_bonus_cap: int = 20              # 加分上限
    arbitration_score_gap: int = 5                # 候选分差 ≤ 触发 LLM 仲裁
    arbitration_confidence: float = 0.95          # 仲裁返回的置信度
    llm_reverify_conf_below: float = 0.3          # 分类置信 < 且 general → LLM 复验
    llm_reverify_confidence: float = 0.7          # 复验成功后的置信度

    # ── 业务域识别 detect_business_domain ──
    domain_min_score: int = 2                     # top 分 < → general
    domain_alt_ratio: float = 0.3                 # 备选域 ≥ top × 该比例

    # ── SimHash chunk 去重 ──
    simhash_hamming_threshold: int = 3            # Hamming 距离 ≤ 视为重复

    # ── 标题辅助词表（原 _TITLE_TYPE_HINTS 内联函数体，收口为数据）──
    title_type_hints: dict = field(default_factory=lambda: {
        "合规": "compliance", "gdpr": "compliance", "数据保护": "compliance",
        "制度": "policy", "管理": "policy", "规范": "policy",
        "财务": "financial", "预算": "financial", "报销": "financial",
        "合同": "legal", "法律": "legal", "保密": "legal",
        "faq": "faq", "常见问题": "faq",
        "商品": "product_spec", "规格": "product_spec", "sku": "product_spec",
        "sop": "sop", "流程": "sop", "操作": "sop",
    })

    def merged_with(self, overrides: dict[str, Any]) -> "IndexingRules":
        """返回应用了 overrides 的新实例（忽略未知字段，类型按字段定义转换）。"""
        valid = {f.name: f for f in fields(self)}
        clean: dict[str, Any] = {}
        for k, v in overrides.items():
            if k not in valid or v is None:
                continue
            cur = getattr(self, k)
            try:
                if isinstance(cur, bool):
                    clean[k] = bool(v)
                elif isinstance(cur, int):
                    clean[k] = int(v)
                elif isinstance(cur, float):
                    clean[k] = float(v)
                elif isinstance(cur, dict):
                    clean[k] = dict(v)
                else:
                    clean[k] = v
            except (TypeError, ValueError):
                continue  # 脏值忽略，不让一处坏配置打挂索引
        return replace(self, **clean)


# ── 环境变量 → 字段映射（仅列出支持 env 覆盖的核心阈值，避免命名空间爆炸）──
_ENV_KEYS = {
    "NEAR_DUP_SIMILARITY_THRESHOLD": "near_dup_similarity_threshold",
    "MINHASH_N_GRAM": "minhash_n_gram",
    "MINHASH_N_HASHES": "minhash_n_hashes",
    "MINHASH_TEXT_MAX_CHARS": "minhash_text_max_chars",
    "QUALITY_REJECT_BELOW": "quality_reject_below",
    "QUALITY_WARN_BELOW": "quality_warn_below",
    "CLASSIFY_FILENAME_WEIGHT": "classify_filename_weight",
    "CLASSIFY_TITLE_WEIGHT": "classify_title_weight",
    "CLASSIFY_FOLDER_WEIGHT": "classify_folder_weight",
    "CLASSIFY_FOLDER_CONF_BONUS": "classify_folder_conf_bonus",
    "ARBITRATION_SCORE_GAP": "arbitration_score_gap",
    "DOMAIN_MIN_SCORE": "domain_min_score",
    "SIMHASH_HAMMING_THRESHOLD": "simhash_hamming_threshold",
}

_lock = threading.Lock()
_cached: IndexingRules | None = None
_cached_at: float = 0.0
_cached_file_mtime: float | None = None
# 测试/运行时覆盖（优先级最高，越过 JSON 文件）
_runtime_overrides: dict[str, Any] = {}


def _load_env_rules() -> IndexingRules:
    base = IndexingRules()
    env_overrides = {}
    for env_key, field_name in _ENV_KEYS.items():
        raw = os.getenv(env_key, "").strip()
        if raw:
            env_overrides[field_name] = raw
    return base.merged_with(env_overrides)


def _read_override_file() -> tuple[dict[str, Any], float | None]:
    if not RULES_OVERRIDE_FILE or not os.path.isfile(RULES_OVERRIDE_FILE):
        return {}, None
    try:
        mtime = os.path.getmtime(RULES_OVERRIDE_FILE)
        with open(RULES_OVERRIDE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data if isinstance(data, dict) else {}), mtime
    except (OSError, ValueError):
        # 文件读不到/坏 JSON → 按无覆盖处理（软降级，不阻塞索引）
        return {}, None


def get_rules() -> IndexingRules:
    """当前生效规则（60s TTL；JSON 覆盖文件 mtime 变化立即失效缓存）。"""
    global _cached, _cached_at, _cached_file_mtime
    now = time.time()
    with _lock:
        if _cached is not None and (now - _cached_at) < _HOT_RELOAD_TTL_SECONDS:
            return _cached
        file_overrides, mtime = _read_override_file()
        file_changed = mtime != _cached_file_mtime
        if _cached is None or file_changed:
            rules = _load_env_rules().merged_with(file_overrides)
            if _runtime_overrides:
                rules = rules.merged_with(_runtime_overrides)
            _cached = rules
            _cached_file_mtime = mtime
        else:
            # TTL 到期但文件未变 → 仅应用运行时覆盖的变更
            rules = _load_env_rules().merged_with(file_overrides)
            if _runtime_overrides:
                rules = rules.merged_with(_runtime_overrides)
            _cached = rules
        _cached_at = now
        return _cached


def override_rules(**kwargs: Any) -> None:
    """运行时覆盖（测试/管理端调参），立即生效。"""
    global _cached
    with _lock:
        _runtime_overrides.update({k: v for k, v in kwargs.items() if v is not None})
        base = _load_env_rules()
        file_overrides, mtime = _read_override_file()
        rules = base.merged_with(file_overrides).merged_with(_runtime_overrides)
        _cached = rules
        _cached_file_mtime = mtime
        _cached_at = time.time()


def reset_rules_override() -> None:
    """清空运行时覆盖（测试 teardown 用）。"""
    global _cached
    with _lock:
        _runtime_overrides.clear()
        _cached = None
        _cached_at = 0.0
