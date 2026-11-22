"""Prompt 发布门禁使用的本地评测适配器。

2026-10-04 验收收敛（一期）：评测结果之外装配结构化发布门禁判定——
tier 阈值门（SELF-07）+ 最低有效样本量门（GATE-12/DATA-09）+ 基线回归门
（GATE-11，baseline_unavailable 显式口径 REG-10）+ RAGAS 门（GATE-03），
未过规则汇总为 ``blocked_rules`` 数组（GATE-16）随 metrics 落 release 行，
publish 侧按灰度开关（audit/enforce）裁决。

2026-11-22 模块路由（SQL 发布门禁）：此前硬编码 module="rag"，SQL prompt
的发布门禁跑的是 RAG 题集——门禁「能走通」但评的不对题。现按 prompt
registry category 路由评测模块；SQL 门禁采用「候选 vs 现网」双跑对比口径
（tier 绝对阈值为 RAG 校准，对 SQL 真实水位不可达，见 _assemble_sql_gate）。
"""
from __future__ import annotations

import asyncio
import os
from contextlib import contextmanager
from typing import Any

from backend.config.settings import (
    PROMPT_RELEASE_RAGAS_GATE_ENABLED,
    PROMPT_RELEASE_REGRESSION_GATE_ENABLED,
)
from backend.evaluation.config import EvalConfig
from backend.evaluation.mode import resolve_evaluation_mode
from backend.shared.logger import logger
from backend.evaluation.service import EvaluationService
from backend.evaluation.storage import persist_report
from backend.infra.llm.registry_store import refresh_registry
from backend.prompts.release_models import PromptReleaseRecord

# GATE-03：RAGAS 建议阈值（清单三：最终阈值必须保存到评测快照，
# 不能只存在前端代码中——随 gate 判定进 release metrics JSONB）
RAGAS_GATE_THRESHOLDS: dict[str, float] = {
    "ragas_faithfulness": 0.85,
    "ragas_answer_relevancy": 0.80,
    "ragas_context_precision": 0.80,
    "ragas_context_recall": 0.85,
}
RAGAS_VALID_RATIO_MIN = 0.90  # C4-7：有效样本率 < 90% → ragas_degraded

# ── 发布评测模块路由（2026-11-22）─────────────────────────────
# prompt registry category → 评测模块。未登记的 category 回落 "rag"
# 维持历史行为（planner/reporter/router/business/memory 等暂无专属
# 评测模块，冒充 RAG 门禁的历史问题显式留痕，按域补齐时在此登记）。
_CATEGORY_TO_MODULE: dict[str, str] = {
    "sql": "sql",
    "rag": "rag",
    "rag_preprocessing": "rag",
}

# SQL 对比门：候选全局通过率允许的最大降幅（与 GATE-11 基线回归门同款）。
SQL_CANDIDATE_REGRESSION_THRESHOLD = 0.05
# SQL 防全挂下限：候选 smoke 层（缺 smoke 层时用全局）通过率下限——
# 防止「候选与现网同时全挂的环境故障」被对比门误判为「不回退」。
SQL_ABSOLUTE_FLOOR = 0.20


def _resolve_eval_module(prompt_key: str) -> str:
    """按 prompt registry category 路由发布评测模块。"""
    from backend.prompts.registry import PROMPT_REGISTRY

    spec = PROMPT_REGISTRY.get(prompt_key)
    category = getattr(spec, "category", "") if spec else ""
    if category and category not in _CATEGORY_TO_MODULE:
        logger.warning(
            "[prompt_release] prompt %s category=%r 无专属评测模块，回落 rag（历史行为）",
            prompt_key, category,
        )
    return _CATEGORY_TO_MODULE.get(category, "rag")


async def run_prompt_release_evaluation(
    release: PromptReleaseRecord,
) -> dict[str, Any]:
    """用数据库绑定的外部模型执行发布评测并返回门禁结果。"""
    # 2026-10-29：候选版本（草稿）不在进程 Prompt 快照/历史内，仅传版本号
    # 会被 bind_prompt_versions 静默跳过、生成回落已发布版——发布门禁曾在
    # 此缺陷下从未评测到候选模板。async 侧先取出模板正文，显式注入评测。
    prompt_templates = await _load_candidate_templates(release.prompt_snapshot)
    # 2026-11-22：SQL 门禁需要「候选 vs 现网」双跑对比基线，现网已发布
    # 版模板同样不在进程快照内，async 侧一并取出。
    current_templates = await _load_current_release_template(release.prompt_key)
    return await asyncio.to_thread(
        _run_sync, release, prompt_templates, current_templates,
    )


async def _load_candidate_templates(
    snapshot: dict[str, Any] | None,
) -> dict[str, tuple[int, str]]:
    """按 prompt_snapshot 取各候选版本模板正文（草稿必须显式读取）。"""
    if not snapshot:
        return {}
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    out: dict[str, tuple[int, str]] = {}
    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        for key, version in snapshot.items():
            try:
                version_int = int(version)
            except (TypeError, ValueError):
                logger.warning("[prompt_release] 候选版本号非法，跳过: %s=%r", key, version)
                continue
            prompt = await repo.get_by_key(str(key))
            if prompt is None:
                logger.warning("[prompt_release] 候选 Prompt 不存在: %s", key)
                continue
            ver = await repo.get_version(prompt.id, version_int)
            if ver is None:
                logger.warning("[prompt_release] 候选版本不存在: %s v%s", key, version_int)
                continue
            out[str(key)] = (version_int, ver.template)
    return out


async def _load_current_release_template(
    prompt_key: str,
) -> dict[str, tuple[int, str]]:
    """取当前已发布（active）版本的模板正文，作为 SQL 对比门的现网基线。

    从未发布过（无 active_version）时返回空 dict——对比门显式降级为
    current_unavailable（REG-10 同款口径：不伪造对比结论，落痕放行）。
    """
    from backend.memory.database import AsyncSessionLocal
    from backend.memory.repository.prompt_repo import PromptRepository

    async with AsyncSessionLocal() as session:
        repo = PromptRepository(session)
        prompt = await repo.get_by_key(prompt_key)
        if prompt is None:
            logger.warning("[prompt_release] 现网 Prompt 不存在: %s", prompt_key)
            return {}
        active = getattr(prompt, "active_version", None)
        if not active:
            return {}
        ver = await repo.get_version(prompt.id, int(active))
        if ver is None:
            logger.warning("[prompt_release] 现网版本不存在: %s v%s", prompt_key, active)
            return {}
        return {prompt_key: (int(active), ver.template)}


def _warm_external_models() -> None:
    """发布评测前预热外置模型（软失败）。

    2026-10-28 desktop_upload_smoke 首跑 7/8 题 rerank_failed：外置 8088
    重排服务闲置后首批推理超 RERANK_TIMEOUT=4s（热态实测 0.8-1.0s），
    冷启动超时按既有"传输失败不自动重发"策略整轮降级，门禁被运维态而非
    提示词质量打穿。此处用 2 文档最小请求预热重排/embedding；失败仅记
    日志，不阻塞评测（真实故障会在用例中如实暴露）。
    """
    try:
        from backend.rag.embedding_singleton import get_embedding
        from backend.rag.reranker import get_reranker_backend

        get_embedding().embed_query("发布评测预热")
        reranker = get_reranker_backend()
        warm = getattr(reranker, "rank", None)
        if callable(warm):
            # GPU 重排 kernel 按批大小分桶编译：2 文档预热覆盖不了真实
            # 候选批量（首轮 desktop smoke 仍 7/8 超时、末题通过即此特征），
            # 用 12 文档 × 2 轮覆盖典型批大小并留足编译时间。
            docs = [f"预热文档{i}：知识库检索与重排链路验证。" for i in range(12)]
            for _ in range(2):
                warm("发布评测预热查询", docs, top_k=5)
        logger.info("[prompt_release] 外置模型预热完成")
    except Exception as exc:  # noqa: BLE001 — 预热失败不阻塞评测
        logger.warning("[prompt_release] 外置模型预热失败（继续评测）: %s", exc)


def _build_eval_config(
    release: PromptReleaseRecord,
    module: str,
    mode: Any,
    prompt_versions: dict[str, Any],
    prompt_templates: dict[str, tuple[int, str]] | None,
) -> EvalConfig:
    """构造发布评测配置。

    SQL 模块不使用 RAG 的 pr_smoke suite（sql/suites 无此 suite 会
    FileNotFoundError），回落 sql canonical 权威集（manifest 锁定，含
    smoke/core/hard 分层与拒答、对抗边界题）。
    """
    return EvalConfig(
        module=module,
        live=mode.live,
        # 发布评测必须真实生成答案：被评审的 prompt（如自纠错改写）
        # 影响生成链路，不生成则基于答案的用例永远无法判定（实测
        # desktop smoke 全部 ans_len=0）。这是发布链路自身的云生成
        # opt-in，与 --judge 语义一致；供应商仍走 DB 绑定的评测模型。
        judge=True,
        ragas=mode.ragas,
        no_ragas=mode.no_ragas,
        selection=release.eval_suite if module == "rag" else None,
        dataset_version=str(
            release.dataset_provenance.get("version")
            or release.dataset_provenance.get("dataset_version")
            or ""
        ),
        prompt_versions=prompt_versions,
        # 候选模板正文显式注入（草稿不在进程快照内，仅版本号会回落发布版）
        prompt_templates=prompt_templates or None,
        release_id=release.release_id,
        # C9-2：发布评测强制严格字段校验——不接受静默降级样本。
        # 2026-11-22：strict 校验的是 RAG 数据契约（expected_answer/ground_truth）；
        # SQL 题集的标注结构是 gold_sql/expected_tables/should_reject（E0 数据
        # 校验器已按模块契约把关），套用 RAG strict 会误杀全部 SQL 样本
        # （生产 rel-ebe174ee 实证：9 条 SC-* 因缺 RAG 字段被拒启动）。
        strict_fields=(module == "rag"),
    )


def _run_sync(
    release: PromptReleaseRecord,
    prompt_templates: dict[str, tuple[int, str]] | None = None,
    current_templates: dict[str, tuple[int, str]] | None = None,
) -> dict[str, Any]:
    module = _resolve_eval_module(release.prompt_key)
    mode = resolve_evaluation_mode(
        release.dataset_provenance.get("evaluation_mode")
    )
    with _evaluation_env(release.created_by):
        asyncio.run(refresh_registry())
        # 冷启动预热仅 RAG 需要（embedding/rerank 8088 外置服务）；SQL 评测
        # 不经检索链路，跳过预热避免无谓的外置服务依赖与冷启动耗时。
        if module == "rag":
            _warm_external_models()
        report = EvaluationService().evaluate(
            _build_eval_config(release, module, mode, release.prompt_snapshot, prompt_templates),
        )
        run_dir = persist_report(report)
        # SQL 门禁（2026-11-22）：同环境双跑现网已发布版作对比基线——
        # 同题集同模型，候选与现网的环境波动对称抵消。
        current_report = None
        current_run_dir = None
        if module == "sql" and current_templates:
            current_versions = {
                key: version for key, (version, _) in current_templates.items()
            }
            current_report = EvaluationService().evaluate(
                _build_eval_config(release, module, mode, current_versions, current_templates),
            )
            current_run_dir = persist_report(current_report)

    summaries = [summary for summary in report.summaries if summary.module == module]
    summary = summaries[0] if summaries else None
    current_summary = None
    if current_report is not None:
        current_summaries = [
            s for s in current_report.summaries if s.module == module
        ]
        current_summary = current_summaries[0] if current_summaries else None

    gate = _assemble_release_gate(
        report, summary, release, module=module,
        current_summary=current_summary, current_expected=bool(current_templates),
    )
    metrics = dict(summary.metrics if summary else {})
    if summary is not None:
        metrics["pass_rate"] = summary.pass_rate
        metrics["total"] = summary.total
        metrics["passed"] = summary.passed
    if module == "sql":
        # 对比门数据落痕：候选/现网通过率同入 metrics，审批侧可复核
        metrics["candidate_pass_rate"] = summary.pass_rate if summary else None
        metrics["current_pass_rate"] = (
            current_summary.pass_rate if current_summary else None
        )
        metrics["current_run_id"] = (
            current_run_dir.name if current_run_dir is not None else None
        )
    metrics["gate"] = gate

    blocked = gate["blocked_rules"]
    passed = _gate_passed(gate, module)
    failure_reason = ""
    if not passed:
        failure_reason = "；".join(
            [r.get("message", "") for r in blocked if r.get("severity") == "error"]
        ) or "评测层级未达到阈值"
    return {
        "status": "passed" if passed else "failed",
        "run_id": run_dir.name,
        "metrics": metrics,
        "failure_reason": failure_reason,
        "provenance": {"blocked_rules": blocked, "eval_module": module},
    }


def _gate_passed(gate: dict[str, Any], module: str) -> bool:
    """按模块聚合门禁通过性。

    RAG：tier 阈值门 + 样本量门（GATE-11/12 一期口径不变）。
    SQL：样本量门 + 候选对比门 + 防全挂下限（tier 绝对阈值仅展示，
    见 _assemble_sql_gate）。
    """
    if module == "sql":
        return (
            bool(gate["sample_pass"])
            and bool(gate["candidate_regression_pass"])
            and bool(gate["absolute_floor_pass"])
        )
    return bool(gate["tier_pass"]) and bool(gate["sample_pass"])


def _evaluate_sample_gate(
    report: Any,
    summary: Any,
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    """最低有效样本量门（GATE-12/DATA-08/09）：RAG/SQL 同口径共用。

    返回 (sample_pass, sample_gate, blocked_rule)；blocked_rule 仅在
    未通过时非空，由调用方并入 blocked_rules。
    """
    total = summary.total if summary else 0
    errors = summary.errors if summary else 0
    skipped = summary.skipped if summary else 0
    valid = total - errors - skipped
    governance = report.metadata.get("suite_governance") or {}
    required = (
        governance.get("min_valid_samples")
        or governance.get("min_samples")
        or int(os.getenv("EVAL_MIN_SAMPLES", "8"))
    )
    sample_pass = valid >= required
    blocked_rule = None
    if not sample_pass:
        blocked_rule = {
            "rule": "min_samples",
            "expected": f">= {required}",
            "actual": str(valid),
            "severity": "error",
            "message": (
                f"有效样本不足：valid={valid}（total={total} - errors={errors} "
                f"- skipped={skipped}）< 最低要求 {required}；"
                f"极小样本 100% 通过不构成发布依据"
            ),
        }
    return sample_pass, {"required": required, "valid": valid, "total": total}, blocked_rule


def _assemble_release_gate(
    report: Any,
    summary: Any,
    release: PromptReleaseRecord,
    *,
    module: str = "rag",
    current_summary: Any = None,
    current_expected: bool = False,
) -> dict[str, Any]:
    """装配结构化发布门禁判定，按评测模块分派（GATE-16 统一出口）。"""
    if module == "sql":
        return _assemble_sql_gate(
            report, summary, current_summary, current_expected,
        )
    return _assemble_rag_gate(report, summary, release)


def _assemble_rag_gate(
    report: Any,
    summary: Any,
    release: PromptReleaseRecord,
) -> dict[str, Any]:
    """RAG 发布门禁（GATE-11/12/03/16，2026-10-04 一期口径不变）。

    记录侧（release 行 metrics）只判 tier + 样本量两道硬门；回归门与
    RAGAS 门在此计算并落痕，发布侧按灰度开关裁决（audit 先行）。
    """
    blocked_rules: list[dict[str, Any]] = []
    tier_pass = bool(report.tier_summaries) and all(
        item.passed_threshold for item in report.tier_summaries
    )
    if not tier_pass:
        for item in report.tier_summaries:
            if not item.passed_threshold:
                blocked_rules.append({
                    "rule": "tier_threshold",
                    "expected": f">= {item.threshold:.2f}",
                    "actual": f"{item.pass_rate:.4f}",
                    "severity": "error",
                    "message": f"[{item.tier}] {item.gate_reasons[0] if item.gate_reasons else '通过率未达阈值'}",
                })

    # ── 最低有效样本量门（GATE-12/DATA-08/09）──
    sample_pass, sample_gate, sample_block = _evaluate_sample_gate(report, summary)
    if sample_block:
        blocked_rules.append(sample_block)

    # ── 基线回归门（GATE-11/REG-03/04/06/10）──
    dataset_version = str(
        report.metadata.get("dataset_version")
        or release.dataset_provenance.get("version")
        or release.dataset_provenance.get("dataset_version")
        or ""
    )
    regression = _evaluate_regression(report, summary, dataset_version)
    for entry in regression.get("errors", []):
        blocked_rules.append({
            "rule": "baseline_regression",
            "expected": f"{entry['metric']} 降幅 ≤ {entry['threshold']}",
            "actual": f"{entry['baseline']} → {entry['current']}",
            "severity": "error",
            "message": f"基线回归：{entry['metric']} {entry['baseline']} → {entry['current']}",
        })
    for bucket in regression.get("bucket_regressions", []):
        blocked_rules.append({
            "rule": "bucket_regression",
            "expected": f"{bucket['bucket']} 通过率降幅 ≤ {bucket['threshold']:.0%}",
            "actual": (
                f"{bucket['baseline_pass_rate']} → {bucket['current_pass_rate']}"
            ),
            "severity": "error",
            "message": (
                f"分桶回归：[{bucket['dimension']}/{bucket['bucket']}] "
                f"{bucket['baseline_pass_rate']} → {bucket['current_pass_rate']}"
            ),
        })

    # ── RAGAS 门（GATE-03 双门禁）──
    ragas_gate = _evaluate_ragas_gate(report, summary)
    if (
        PROMPT_RELEASE_RAGAS_GATE_ENABLED == "enforce"
        and not ragas_gate["ragas_pass"]
    ):
        blocked_rules.append({
            "rule": ragas_gate["rule"],
            "expected": ragas_gate["expected"],
            "actual": ragas_gate["actual"],
            "severity": "error",
            "message": ragas_gate["message"],
        })

    return {
        "tier_pass": tier_pass,
        "sample_pass": sample_pass,
        "sample_gate": sample_gate,
        "regression_gate_mode": PROMPT_RELEASE_REGRESSION_GATE_ENABLED,
        "ragas_gate_mode": PROMPT_RELEASE_RAGAS_GATE_ENABLED,
        "regression": regression,
        "ragas": ragas_gate,
        "blocked_rules": blocked_rules,
    }


def _assemble_sql_gate(
    report: Any,
    summary: Any,
    current_summary: Any,
    current_expected: bool,
) -> dict[str, Any]:
    """SQL 发布门禁（2026-11-22 双跑对比口径）。

    tier 绝对阈值（smoke 0.95/core 0.85/hard 0.70）按 RAG 题集质量水位
    校准；SQL 生成的真实水位远低于此（生产口径 sql_v7 全局 38.5%，各层
    均不达标），照搬会永久阻塞 SQL 发布。因此 SQL 不套用 tier 绝对阈值
    （tier_summaries 仅展示），改以「候选不得低于现网」的相对口径为主门禁：

    - candidate_regression：候选全局通过率 >= 现网 - 5%（GATE-11 同款降幅；
      现网已发布版同题集同模型双跑，环境波动对称抵消）
    - absolute_floor：候选 smoke 层（缺 smoke 层用全局）通过率 >= 0.20，
      防止候选与现网同时全挂的环境故障被对比门误判为「不回退」
    - min_samples：有效样本量门（GATE-12，与 RAG 同口径）
    - RAGAS 门对 SQL 不适用（N/A 落痕；publish 侧按 ragas_applicable 跳过）
    """
    blocked_rules: list[dict[str, Any]] = []
    sample_pass, sample_gate, sample_block = _evaluate_sample_gate(report, summary)
    if sample_block:
        blocked_rules.append(sample_block)

    # ── tier 分层展示（display_only，不阻塞发布）──
    tiers = [
        {
            "tier": item.tier,
            "pass_rate": item.pass_rate,
            "threshold": item.threshold,
            "passed": item.passed_threshold,
            "total": item.total,
            "passed_count": item.passed,
        }
        for item in report.tier_summaries
    ]

    # ── 候选对比门（GATE-11 相对口径：候选不得比现网差）──
    if not current_expected:
        # 现网从未发布（无 active 版本）：对比基线不存在，显式降级放行
        # 并留痕（REG-10 口径）；此时防全挂下限与样本量门仍生效。
        candidate_regression_pass = True
        candidate_regression = {
            "regression_pass": None,
            "note": "current_release_unavailable: 现网无已发布版本，对比门降级放行并留痕",
        }
    elif current_summary is None:
        # 现网评测已执行但未产出摘要：数据缺失不能假装对比成功（fail-closed）
        candidate_regression_pass = False
        candidate_regression = {
            "regression_pass": False,
            "note": "current_evaluation_missing: 现网双跑未产出摘要，对比门不可判",
        }
        blocked_rules.append({
            "rule": "candidate_regression",
            "expected": "现网双跑产出可对比摘要",
            "actual": "无摘要",
            "severity": "error",
            "message": "现网版本双跑未产出评测摘要，对比门不可判（fail-closed）",
        })
    else:
        candidate_rate = summary.pass_rate if summary else 0.0
        current_rate = current_summary.pass_rate
        floor = current_rate - SQL_CANDIDATE_REGRESSION_THRESHOLD
        candidate_regression_pass = candidate_rate >= floor
        candidate_regression = {
            "regression_pass": candidate_regression_pass,
            "candidate_pass_rate": candidate_rate,
            "current_pass_rate": current_rate,
            "threshold": SQL_CANDIDATE_REGRESSION_THRESHOLD,
        }
        if not candidate_regression_pass:
            blocked_rules.append({
                "rule": "candidate_regression",
                "expected": f"候选通过率 >= 现网 {current_rate:.4f} - {SQL_CANDIDATE_REGRESSION_THRESHOLD:.2f}",
                "actual": f"{candidate_rate:.4f}",
                "severity": "error",
                "message": (
                    f"候选版本通过率 {candidate_rate:.4f} 低于现网 {current_rate:.4f}，"
                    f"超过允许降幅 {SQL_CANDIDATE_REGRESSION_THRESHOLD}"
                ),
            })

    # ── 防全挂下限 ──
    smoke_tier = next(
        (t for t in report.tier_summaries if t.tier == "smoke"), None,
    )
    floor_rate = (
        smoke_tier.pass_rate if smoke_tier is not None
        else (summary.pass_rate if summary else 0.0)
    )
    floor_basis = "smoke 层" if smoke_tier is not None else "全局"
    absolute_floor_pass = floor_rate >= SQL_ABSOLUTE_FLOOR
    if not absolute_floor_pass:
        blocked_rules.append({
            "rule": "absolute_floor",
            "expected": f"{floor_basis}通过率 >= {SQL_ABSOLUTE_FLOOR:.2f}",
            "actual": f"{floor_rate:.4f}",
            "severity": "error",
            "message": (
                f"候选版本 {floor_basis}通过率 {floor_rate:.4f} 低于防全挂下限 "
                f"{SQL_ABSOLUTE_FLOOR:.2f}——疑似环境故障而非质量结论"
            ),
        })

    ragas_gate = dict(_evaluate_ragas_gate(report, summary))
    ragas_gate["not_applicable"] = True

    return {
        # tier_pass 字段语义：tier 门是否阻塞发布。SQL 不套用 tier 绝对
        # 阈值（分层结果见 tiers，display_only），故恒 True；真实分层
        # 通过率以 tiers 数据为准。
        "tier_pass": True,
        "tier_gate_mode": "display_only",
        "tiers": tiers,
        "sample_pass": sample_pass,
        "sample_gate": sample_gate,
        "candidate_regression": candidate_regression,
        "candidate_regression_pass": candidate_regression_pass,
        "absolute_floor_pass": absolute_floor_pass,
        "ragas_applicable": False,
        "ragas": ragas_gate,
        # SQL 的回归语义由候选对比门承担（同环境双跑）；baseline fixture
        # 门按模块不适用，publish 侧读取 regression_pass 不为 False 不阻塞。
        "regression": {
            "baseline_available": False,
            "note": "SQL 使用候选 vs 现网双跑对比门",
        },
        "blocked_rules": blocked_rules,
    }


def _evaluate_regression(
    report: Any, summary: Any, dataset_version: str,
) -> dict[str, Any]:
    """结构化基线回归门；逐样本/分桶维度尽力而为、缺失显式降级。"""
    from backend.evaluation.gate.regression import (
        diff_samples,
        evaluate_regression_gate,
        load_baseline_report,
    )

    result: dict[str, Any] = evaluate_regression_gate(
        "rag",
        dataset_version,
        dict(summary.metrics if summary else {}),
        summary.pass_rate if summary else 0.0,
        current_buckets=report.metadata.get("buckets") or {},
    )
    # 逐样本对比：baseline JSON 带 run_id 且报告仍存在时才可判（REG-03/04）
    baseline_report = load_baseline_report("rag", dataset_version)
    if baseline_report is not None:
        result["samples"] = diff_samples(report, baseline_report)
    else:
        result["samples"] = {
            "regression_samples": [],
            "improvement_samples": [],
            "compared": 0,
            "note": (
                "baseline_samples_unavailable: 基线未记录源 run 或报告已清理，"
                "逐样本对比降级为仅摘要级"
            ),
        }
    return result


def _evaluate_ragas_gate(report: Any, summary: Any) -> dict[str, Any]:
    """RAGAS 门判定：未执行/不可用/有效样本率不足/指标不达标均不通过。"""
    metrics = dict(summary.metrics if summary else {})
    ragas_stats = report.metadata.get("ragas_samples") or {}
    valid = int(ragas_stats.get("valid", 0) or 0)
    invalid = int(ragas_stats.get("invalid", 0) or 0)

    executed = any(key in metrics for key in RAGAS_GATE_THRESHOLDS)
    if not executed:
        return {
            "ragas_pass": False,
            "rule": "ragas_not_executed",
            "expected": "RAGAS 四指标实分且达标（双门禁）",
            "actual": "RAGAS 未执行或未产出任何分值",
            "message": "RAGAS 门：未执行/不可用（fail-closed，不视为通过）",
            "thresholds": dict(RAGAS_GATE_THRESHOLDS),
            "valid_samples": valid,
            "invalid_samples": invalid,
        }

    failed_metrics = []
    for key, threshold in RAGAS_GATE_THRESHOLDS.items():
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            failed_metrics.append(f"{key}=unavailable(<{threshold})")
        elif value < threshold:
            failed_metrics.append(f"{key}={value:.4f}(<{threshold})")
    attempted = valid + invalid
    valid_ratio = (valid / attempted) if attempted else 0.0
    degraded = attempted > 0 and valid_ratio < RAGAS_VALID_RATIO_MIN
    passed = not failed_metrics and not degraded
    if degraded:
        failed_metrics.append(
            f"valid_ratio={valid_ratio:.2f}(<{RAGAS_VALID_RATIO_MIN}) ragas_degraded"
        )
    return {
        "ragas_pass": passed,
        "rule": "ragas_gate",
        "expected": " / ".join(
            f"{k.removeprefix('ragas_')}>= {v}" for k, v in RAGAS_GATE_THRESHOLDS.items()
        ) + f"（valid_ratio>= {RAGAS_VALID_RATIO_MIN}）",
        "actual": "；".join(failed_metrics) or "全部达标",
        "message": (
            "RAGAS 门：通过" if passed
            else f"RAGAS 门未通过：{'；'.join(failed_metrics)}"
        ),
        "thresholds": dict(RAGAS_GATE_THRESHOLDS),
        "valid_samples": valid,
        "invalid_samples": invalid,
        "valid_ratio": round(valid_ratio, 4),
        "degraded": degraded,
    }


@contextmanager
def _evaluation_env(triggered_by: str):
    old_trigger = os.environ.get("EVAL_TRIGGER")
    old_triggered_by = os.environ.get("EVAL_TRIGGERED_BY")
    os.environ["EVAL_TRIGGER"] = "prompt_publish"
    os.environ["EVAL_TRIGGERED_BY"] = triggered_by or "prompt-publish"
    try:
        yield
    finally:
        _restore_env("EVAL_TRIGGER", old_trigger)
        _restore_env("EVAL_TRIGGERED_BY", old_triggered_by)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


__all__ = ["run_prompt_release_evaluation"]
