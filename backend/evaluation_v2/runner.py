"""评测 V2 到现有 RAG Runner 的受控适配器。"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from backend.evaluation_v2 import repository

_SUPPORTED_QUERY_TYPES = {
    "single_doc", "multi_doc", "table", "procedural",
    "no_evidence", "permission_denied",
}


class CorpusSnapshotMissingError(FileNotFoundError):
    """Run 引用的固定语料快照文件不存在。"""


def _snapshot_evidence_quote(anchor: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """只从 Run 固定语料解析已审核锚点，并复核其内容哈希。"""
    doc_id = str(anchor.get("doc_id", ""))
    anchor_id = str(anchor.get("anchor_id", ""))
    expected_hash = str(anchor.get("anchor_text_hash", "")).lower()
    if not doc_id or not anchor_id or len(expected_hash) != 64:
        raise ValueError("Golden 证据锚点缺少固定语料定位信息")
    matches = [
        row for row in snapshot.get("fulltext", [])
        if str(row.get("doc_id", "")) == doc_id
        and str(row.get("chunk_id", "")) == anchor_id
    ]
    if len(matches) != 1:
        raise ValueError("Golden 证据锚点在固定语料中缺失或不唯一")
    quote = str(matches[0].get("content", ""))
    actual_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    if not quote or actual_hash != expected_hash:
        raise ValueError("固定语料中的审核证据锚点哈希不匹配")
    return quote


def _refresh_model_registry() -> None:
    """在 Worker 上读取生效配置后，再与排队时的运行快照比较。"""
    from backend.infra.async_utils import run_on_worker_loop
    from backend.infra.llm.registry_store import refresh_registry

    run_on_worker_loop(refresh_registry())


def _legacy_dataset(run: dict[str, Any], definition: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """把已发布的 V2 修订投影为旧 Runner 的不可变输入格式。"""
    from backend.evaluation.dataset import imports as assets

    run_uuid = uuid.UUID(str(run["run_id"]))
    dataset_key = f"v2_run_{run_uuid.hex[:24]}"
    evidence_docs = {
        (str(item.get("doc_id", "")), str(item.get("version_id") or item.get("active_generation") or item.get("index_generation") or "")): item
        for item in snapshot.get("documents", [])
    }
    legacy_cases: list[dict[str, Any]] = []
    for ordinal, row in enumerate(definition["cases"]):
        source_input = row["input_json"]
        expected_source = row["expected_json"]
        evidence_source = row["evidence_json"] or []
        metadata = row["metadata_json"] or {}
        if isinstance(source_input, str):
            import json

            source_input = json.loads(source_input)
            expected_source = json.loads(expected_source) if isinstance(expected_source, str) else expected_source
            evidence_source = json.loads(evidence_source) if isinstance(evidence_source, str) else evidence_source
            metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
        question = str(source_input.get("query", "")).strip()
        if not question:
            raise ValueError("已发布样本缺少评测问题")
        behavior = str(expected_source.get("behavior", "answer"))
        if behavior not in {"answer", "refuse"}:
            raise ValueError("当前 Runner 不支持该样本的回答行为")
        reason = expected_source.get("reason")
        if behavior == "refuse" and reason not in {"no_evidence", "permission_denied"}:
            raise ValueError("拒答样本必须指定可验证的拒答原因")
        if behavior == "answer" and not evidence_source:
            raise ValueError("回答样本缺少已审核证据")
        if behavior == "refuse" and (expected_source.get("reference_answer") or expected_source.get("required_facts")):
            raise ValueError("拒答样本不能携带参考答案或必须事实")

        evidence = []
        for anchor in evidence_source:
            kb_id = str(anchor.get("kb_id", ""))
            if kb_id != str(run["kb_id"]):
                raise PermissionError("Golden 证据知识库与运行授权范围不一致")
            doc_id = str(anchor.get("doc_id", ""))
            doc_version = str(anchor.get("doc_version", ""))
            document = evidence_docs.get((doc_id, doc_version))
            if not document:
                raise ValueError("运行固定语料与 Golden 证据版本不一致")
            if str(document.get("file_hash", "")).lower() != str(anchor.get("source_content_hash", "")).lower():
                raise ValueError("运行固定语料的来源文件哈希与 Golden 证据不一致")
            evidence.append({
                "doc_id": doc_id,
                "source_file": str(document.get("file_name", "")),
                "quote": _snapshot_evidence_quote(anchor, snapshot),
                "version_id": str(document.get("version_id", "")),
            })

        if behavior == "refuse":
            query_type = str(reason)
            expected = {
                "behavior": "refuse",
                "reason": reason,
                "evidence": [],
                "reference_answer": "",
                "required_facts": [],
                "assertions": [],
            }
        else:
            query_type = str(metadata.get("query_type", "single_doc"))
            if query_type not in _SUPPORTED_QUERY_TYPES - {"no_evidence", "permission_denied"}:
                query_type = "single_doc"
            expected = {
                "behavior": "answer",
                "reason": None,
                "evidence": evidence,
                "reference_answer": str(expected_source.get("reference_answer", "")),
                "required_facts": expected_source.get("required_facts") or [],
                "assertions": expected_source.get("assertions") or [],
            }
            if not expected["reference_answer"].strip() and not expected["required_facts"] and not expected["assertions"]:
                raise ValueError("回答样本缺少可验证的标准答案或断言")

        legacy_cases.append({
            "id": str(row["case_id"]),
            "question": question,
            "tags": [str(value) for value in (metadata.get("tags") or []) if isinstance(value, str)][:20],
            "tier": str(metadata.get("tier", "core")) if metadata.get("tier") in {"smoke", "core", "hard", "regression"} else "core",
            "query_type": query_type,
            "expected": expected,
            "splits": [],
            "archived": False,
        })

    manifest = {
        "schema_version": "rag-eval/1.0",
        "dataset_key": dataset_key,
        "name": f"V2 Run {run_uuid.hex[:12]}",
        "version": str(definition["dataset_version"]),
        "module": "rag",
        "description": "由不可变 Evaluation V2 Suite/Golden 快照生成的 Runner 输入",
        "language": "zh-CN",
        "case_schema": {},
        "csv_mapping": {},
    }
    import json

    encoded = "\n".join(json.dumps(item, ensure_ascii=False) for item in legacy_cases).encode("utf-8")
    validation = assets.validate_upload("v2-cases.jsonl", encoded, manifest)
    if not validation.get("valid"):
        raise ValueError("V2 运行样本无法适配现有 RAG Runner：" + str(validation.get("errors", [])[:3]))
    record = assets.create_import(validation, run["created_by"])
    assets.write_once(
        "reviews",
        record["import_id"],
        {
            "decision": "approved",
            "reason": "V2 已发布 Golden 的只读 Runner 适配记录",
            "reviewer": run["created_by"],
            "reviewed_at": assets.now(),
        },
    )
    binding_id = assets.digest({
        "v2_run_id": str(run["run_id"]),
        "import_id": record["import_id"],
        "kb_id": str(run["kb_id"]),
        "snapshot_id": str(snapshot["snapshot_id"]),
    })[:24]
    run_snapshot = run.get("snapshot_json", {})
    if isinstance(run_snapshot, str):
        import json

        run_snapshot = json.loads(run_snapshot)
    evaluator_definitions = run_snapshot.get("evaluator_definitions") or []
    mode = "answer" if any(item.get("binding") == "answer_quality" for item in evaluator_definitions) else "retrieval"
    subject_user_id = run_snapshot.get("subject_user_id")
    assets.write_once(
        "bindings",
        binding_id,
        {
            "binding_id": binding_id,
            "import_id": record["import_id"],
            "kb_id": str(run["kb_id"]),
            "split": "",
            "mode": mode,
            "snapshot_id": str(snapshot["snapshot_id"]),
            "subject_user_id": subject_user_id,
            "min_samples": int(definition["minimum_valid_samples"]),
            "pass_rate": float((definition.get("metric_policy") or {}).get("min_pass_rate", 0.95)),
            "created_at": assets.now(),
            "created_by": run["created_by"],
            "dataset_name": manifest["name"],
            "dataset_version": manifest["version"],
            "content_hash": record["content_hash"],
        },
    )
    _binding, imported_record = assets.load_binding(binding_id)
    assets.check_evidence(imported_record, snapshot["documents"])
    return binding_id


def _gate(report: Any, definition: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """按 Suite 快照判断质量；基础设施失败与有效样本不足返回 INVALID。"""
    from backend.evaluation_v2.capabilities import CONTRACT_ONLY_RUNNER_MODULES

    if str(definition.get("module", "")) in CONTRACT_ONLY_RUNNER_MODULES:
        return "INVALID", [{
            "code": "runner_semantics_not_quality_evaluation",
            "module": str(definition.get("module", "")),
            "execution_semantics": "contract_only",
        }]
    policy = definition.get("metric_policy") or {}
    security = definition.get("security_policy") or {}
    if set(policy) - {"min_pass_rate"}:
        return "INVALID", [{"code": "unsupported_metric_policy", "fields": sorted(set(policy) - {"min_pass_rate"})}]
    if set(security) - {"max_failed_cases"}:
        return "INVALID", [{"code": "unsupported_security_policy", "fields": sorted(set(security) - {"max_failed_cases"})}]
    validity = (report.metadata.get("run_validity") or {}).get("validity")
    results = list(report.results)
    passed = sum(result.status == "pass" for result in results)
    failed = sum(result.status == "fail" for result in results)
    errors = sum(result.status == "error" for result in results)
    skipped = sum(result.status == "skip" for result in results)
    reasons: list[dict[str, Any]] = []
    valid_count = passed + failed
    if validity != "VALID":
        return "INVALID", [{"code": "invalid_run", "validity": validity or "unknown"}]
    if errors or skipped:
        return "INVALID", [{"code": "incomplete_results", "errors": errors, "skipped": skipped}]
    if valid_count < int(definition["minimum_valid_samples"]):
        return "INVALID", [{"code": "minimum_valid_samples", "required": int(definition["minimum_valid_samples"]), "actual": valid_count}]
    security_case_ids = set()
    for case in definition.get("cases") or []:
        expected = case.get("expected_json") or case.get("expected") or {}
        if isinstance(expected, str):
            try:
                expected = json.loads(expected)
            except ValueError:
                expected = {}
        if (
            isinstance(expected, dict)
            and expected.get("behavior") == "refuse"
            and expected.get("reason") == "permission_denied"
        ):
            security_case_ids.add(str(case.get("case_id", "")))
    security_failures = sum(
        result.status == "fail" and str(result.case_id) in security_case_ids
        for result in results
    )
    if security_failures:
        return "FAIL", [{"code": "security_hard_veto", "failed_cases": security_failures}]
    min_pass_rate = policy.get("min_pass_rate")
    if not isinstance(min_pass_rate, (int, float)) or isinstance(min_pass_rate, bool) or not 0 <= min_pass_rate <= 1:
        return "INVALID", [{"code": "missing_gate_policy", "field": "min_pass_rate"}]
    pass_rate = passed / valid_count
    if pass_rate < float(min_pass_rate):
        reasons.append({"code": "min_pass_rate", "required": float(min_pass_rate), "actual": round(pass_rate, 6)})
    max_failed = security.get("max_failed_cases")
    if max_failed is not None:
        if not isinstance(max_failed, int) or isinstance(max_failed, bool) or max_failed < 0:
            return "INVALID", [{"code": "invalid_security_policy", "field": "max_failed_cases"}]
        if failed > max_failed:
            reasons.append({"code": "security_hard_veto", "max_failed_cases": max_failed, "failed_cases": failed})
    if reasons:
        return "FAIL", reasons
    return "PASS", [{"code": "policy_satisfied", "pass_rate": round(pass_rate, 6), "valid_cases": valid_count}]


def _answer_generation_token_scores(actual: Any) -> dict[str, int]:
    """提取旧 RAG Runner 的逐例答案生成 Token，不把缺失计量伪装成零。"""
    actual = actual if isinstance(actual, dict) else {}
    trace = actual.get("trace")
    if not isinstance(trace, dict):
        return {}
    prompt_tokens = trace.get("prompt_tokens")
    completion_tokens = trace.get("completion_tokens")
    if (
        type(prompt_tokens) is not int
        or type(completion_tokens) is not int
        or prompt_tokens < 0
        or completion_tokens < 0
    ):
        return {}
    return {
        "answer_generation_prompt_tokens": prompt_tokens,
        "answer_generation_completion_tokens": completion_tokens,
        "answer_generation_total_tokens": prompt_tokens + completion_tokens,
    }


def _case_result_payload(item: Any, artifact_uri: str) -> dict[str, Any]:
    """生成数据库逐例结果行；成本与不同阶段用量缺失时不填零。"""
    actual = item.actual if isinstance(item.actual, dict) else {}
    scores = dict(item.metrics or {})
    scores.update(_answer_generation_token_scores(actual))
    return {
        "case_id": item.case_id,
        "status": item.status,
        "scores": scores,
        "failure_stage": item.error_stage or "",
        "latency_ms": max(0, int(item.duration_ms or 0)),
        "trace_id": str(actual.get("trace_id", "")),
        "artifact_uri": artifact_uri,
    }


class UnsafeRunnerEnvironmentError(RuntimeError):
    """非 RAG Runner 当前环境可能访问真实外部 Provider。"""


def _decode_json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return default if value is None else value


def _build_non_rag_cases(definition: dict[str, Any], module: str) -> list[Any]:
    """将不可变 V2 修订投影到旧 Runner 的 TestCase 契约。"""
    from backend.evaluation.models import TestCase
    from backend.evaluation_v2.domain_contracts import _case_query

    cases = []
    for row in definition["cases"]:
        case_input = _decode_json(row.get("input_json"), {})
        expected = _decode_json(row.get("expected_json"), {})
        metadata = _decode_json(row.get("metadata_json"), {})
        if not isinstance(case_input, dict) or not isinstance(expected, dict) or not isinstance(metadata, dict):
            raise ValueError("已发布非 RAG Golden 修订结构无效")
        question = _case_query(case_input, allow_turns=module == "cs")
        cases.append(TestCase(
            id=str(row["case_id"]),
            question=question,
            module=module,
            expected=expected,
            metadata=metadata,
        ))
    return cases


def _execute_non_rag_run(
    run: dict[str, Any],
    definition: dict[str, Any],
    snapshot_json: dict[str, Any],
    *,
    tenant_id: str,
    actor: str,
    boundary,
) -> dict[str, Any]:
    """以现有 Runner 执行无语料模块；禁止通过 live 模式触发模型或外部服务。"""
    module = str(definition["module"])
    from backend.evaluation_v2.capabilities import (
        CONTRACT_ONLY_RUNNER_MODULES,
        SUPPORTED_RUNNER_MODULES,
    )

    if module not in SUPPORTED_RUNNER_MODULES or module == "rag":
        raise ValueError("非 RAG Suite 模块未登记到受控 Runner")
    environment = snapshot_json.get("environment") or {}
    expected_semantics = "contract_only" if module in CONTRACT_ONLY_RUNNER_MODULES else "system_quality"
    if (
        snapshot_json.get("module") != module
        or snapshot_json.get("execution_semantics") != expected_semantics
        or environment.get("runner") != f"legacy-{module}-v1"
        or environment.get("module") != module
        or environment.get("execution_semantics") != expected_semantics
        or environment.get("suite_hash") != str(definition["suite_hash"])
        or environment.get("dataset_hash") != str(definition["dataset_hash"])
    ):
        raise ValueError("非 RAG Runner 环境快照与已发布 Suite 不一致")

    expected_refs = {
        (str(item["case_id"]), int(item["case_revision"]), str(item["content_hash"]))
        for item in snapshot_json.get("cases", [])
    }
    actual_refs = {
        (str(item["case_id"]), int(item["case_revision"]), str(item["revision_hash"]))
        for item in definition["cases"]
    }
    if expected_refs != actual_refs or len(expected_refs) != len(definition["cases"]):
        raise ValueError("运行样本与不可变 Suite 版本不一致")

    if module == "travel":
        from backend.tools.travel.live_map import is_enabled as live_map_enabled

        if live_map_enabled():
            raise UnsafeRunnerEnvironmentError("travel_live_provider_enabled")

    from backend.evaluation.storage import load_report, persist_report

    try:
        report, _meta = load_report(str(run["run_id"]))
    except FileNotFoundError:
        report = None
    if report is None:
        boundary()
        if repository.get_run(tenant_id, str(run["run_id"]))["job_status"] != "running":
            repository.set_run_status(tenant_id, actor, str(run["run_id"]), "running")
        # builtin 导入仅注册 Runner；执行时永远传 live=False，不允许模型或付费 Provider 出站。
        import backend.evaluation.runners.builtin  # noqa: F401
        from backend.evaluation.registry import get_runner

        entry = get_runner(module)
        if entry is None or entry.needs_live:
            raise ValueError("非 RAG Runner 未注册离线执行契约")
        cases = _build_non_rag_cases(definition, module)
        results = entry.func(cases, live=False)
        boundary()
        result_ids = [str(item.case_id) for item in results]
        case_ids = [str(item.id) for item in cases]
        if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(case_ids):
            raise ValueError("非 RAG Runner 返回的逐例结果与 Suite 样本不一致")

        from backend.evaluation.models import EvalReport
        from backend.evaluation.service import _build_summary

        report = EvalReport(
            module=module,
            mode="offline",
            summaries=[_build_summary(results, module)],
            results=results,
            metadata={
                "run_id": str(run["run_id"]),
                "runner": f"legacy-{module}-v1",
                "suite_hash": str(definition["suite_hash"]),
                "dataset_hash": str(definition["dataset_hash"]),
                "execution_semantics": expected_semantics,
                "run_validity": {"validity": "VALID"},
                "cost_status": snapshot_json.get("cost_status", "not_applicable_no_model_call"),
                "model_calls": False,
            },
        )
        persist_report(report, run_id=str(run["run_id"]))
    else:
        report_metadata = report.metadata or {}
        if (
            report.module != module
            or report_metadata.get("suite_hash") != str(definition["suite_hash"])
            or report_metadata.get("dataset_hash") != str(definition["dataset_hash"])
            or report_metadata.get("execution_semantics") != expected_semantics
        ):
            raise ValueError("已有逐例产物与非 RAG Run 快照不一致")
        expected_case_ids = {str(item["case_id"]) for item in snapshot_json.get("cases", [])}
        if {str(item.case_id) for item in report.results} != expected_case_ids:
            raise ValueError("已有非 RAG 逐例产物与运行样本不一致")

    boundary()
    current_status = repository.get_run(tenant_id, str(run["run_id"]))["job_status"]
    if current_status != "aggregating" and current_status != "completed":
        repository.set_run_status(tenant_id, actor, str(run["run_id"]), "aggregating")
    results = [
        _case_result_payload(item, f"evaluation-run:{run['run_id']}")
        for item in report.results
    ]
    repository.append_run_results(tenant_id, actor, str(run["run_id"]), results)
    gate, reasons = _gate(report, definition)
    boundary()
    repository.complete_run_with_gate(
        tenant_id, actor, str(run["run_id"]),
        policy_hash=repository.content_hash({
            "metric_policy": definition.get("metric_policy") or {},
            "security_policy": definition.get("security_policy") or {},
            "minimum_valid_samples": definition["minimum_valid_samples"],
            "execution_semantics": expected_semantics,
        }),
        verdict=gate,
        reasons=reasons,
    )
    return {
        "run_id": str(run["run_id"]),
        "status": "SUCCESS",
        "gate_verdict": gate,
        "total_cases": len(results),
        "completed_cases": len(results),
        "passed_cases": sum(item["status"] == "pass" for item in results),
        "validity": (report.metadata.get("run_validity") or {}).get("validity", "unknown"),
        "cost_status": (report.metadata or {}).get("cost_status", "not_applicable_no_model_call"),
    }


def execute_run(run_id: str, *, tenant_id: str, actor: str, boundary) -> dict[str, Any]:
    """执行已入队 V2 Run，并复用当前受控的 RAG/非 RAG Runner adapter。"""
    run = repository.get_run(tenant_id, run_id)
    if tenant_id != "default":
        repository.set_run_status(tenant_id, actor, run_id, "blocked", invalid_reason="legacy_runner_single_tenant")
        return {"status": "BLOCKED", "run_id": run_id, "reason": "legacy_runner_single_tenant"}
    if run["job_status"] in {"cancelled", "blocked"}:
        return {"run_id": run_id, "status": run["job_status"].upper(), "gate_verdict": run.get("gate_verdict")}
    if run["job_status"] == "completed" and repository.list_gate_decisions(tenant_id, run_id):
        return {"run_id": run_id, "status": "SUCCESS", "gate_verdict": run.get("gate_verdict")}
    if run["job_status"] in {"queued", "failed"}:
        repository.set_run_status(tenant_id, actor, run_id, "preparing")
    try:
        boundary()
        definition = repository.get_run_definition(tenant_id, str(run["suite_version_id"]))
        snapshot_json = run["snapshot_json"]
        if isinstance(snapshot_json, str):
            import json

            snapshot_json = json.loads(snapshot_json)
        if snapshot_json.get("suite_hash") != str(definition["suite_hash"]):
            raise ValueError("已发布方案快照与运行记录不一致")
        if snapshot_json.get("dataset_hash") != str(definition["dataset_hash"]):
            raise ValueError("Golden 数据快照与运行记录不一致")
        if definition["module"] != "rag":
            return _execute_non_rag_run(
                run, definition, snapshot_json,
                tenant_id=tenant_id, actor=actor, boundary=boundary,
            )
        expected_refs = {
            (str(item["case_id"]), int(item["case_revision"]), str(item["content_hash"]))
            for item in snapshot_json.get("cases", [])
        }
        actual_refs = {
            (str(item["case_id"]), int(item["case_revision"]), str(item["revision_hash"]))
            for item in definition["cases"]
        }
        if expected_refs != actual_refs or len(expected_refs) != len(definition["cases"]):
            raise ValueError("运行样本与不可变 Suite 版本不一致")

        from backend.evaluation.corpus_snapshots import load

        artifact_uri = str(run.get("artifact_uri") or "")
        if not artifact_uri.startswith("rag-snapshot:"):
            raise ValueError("运行缺少受控固定语料快照")
        try:
            snapshot = load(artifact_uri.removeprefix("rag-snapshot:"))
        except FileNotFoundError as exc:
            raise CorpusSnapshotMissingError("运行引用的固定语料快照不存在") from exc
        if snapshot["content_hash"] != str(run["corpus_hash"]):
            raise ValueError("固定语料内容哈希不一致")
        if snapshot["kb_id"] != str(run["kb_id"]):
            raise PermissionError("固定语料知识库与运行范围不一致")

        from backend.evaluation_v2.runtime_environment import (
            assert_runtime_environment,
        )

        expected_environment = snapshot_json.get("runtime_environment")
        _refresh_model_registry()

        def guarded_boundary(*args, **kwargs):
            boundary(*args, **kwargs)
            assert_runtime_environment(expected_environment)

        guarded_boundary()
        binding_id = _legacy_dataset(run, definition, snapshot)
        guarded_boundary()
        from backend.evaluation.managed_execution import execute_binding
        from backend.evaluation.storage import load_report

        try:
            report, _meta = load_report(run_id)
        except FileNotFoundError:
            report = None
        if report is None:
            current_status = repository.get_run(tenant_id, run_id)["job_status"]
            if current_status == "completed":
                repository.record_gate_decision(
                    tenant_id, actor, run_id,
                    policy_hash=repository.content_hash({"terminal": "legacy_report_missing"}),
                    verdict="INVALID",
                    reasons=[{"code": "legacy_report_missing"}],
                )
                return {"run_id": run_id, "status": "SUCCESS", "gate_verdict": "INVALID"}
            if current_status != "aggregating":
                repository.set_run_status(tenant_id, actor, run_id, "running")
            execute_binding(
                binding_id,
                actor,
                run_id=run_id,
                boundary=guarded_boundary,
                prompt_versions=snapshot_json.get("prompt_versions") or {},
                evaluation_mode=str(snapshot_json.get("evaluation_mode") or "offline"),
                refresh_registry_before_run=False,
            )
        elif repository.get_run(tenant_id, run_id)["job_status"] in {"preparing", "running"}:
            repository.set_run_status(tenant_id, actor, run_id, "running")
        guarded_boundary()
        report, _meta = load_report(run_id)
        guarded_boundary()
        current_status = repository.get_run(tenant_id, run_id)["job_status"]
        if current_status != "aggregating" and current_status != "completed":
            repository.set_run_status(tenant_id, actor, run_id, "aggregating")
        per_case_uri = f"evaluation-run:{run_id}"
        results = [_case_result_payload(item, per_case_uri) for item in report.results]
        repository.append_run_results(tenant_id, actor, run_id, results)
        gate, reasons = _gate(report, definition)
        guarded_boundary()
        repository.complete_run_with_gate(
            tenant_id, actor, run_id,
            policy_hash=repository.content_hash({
                "metric_policy": definition.get("metric_policy") or {},
                "security_policy": definition.get("security_policy") or {},
                "minimum_valid_samples": definition["minimum_valid_samples"],
            }),
            verdict=gate,
            reasons=reasons,
        )
        return {
            "run_id": run_id,
            "status": "SUCCESS",
            "gate_verdict": gate,
            "total_cases": len(results),
            "completed_cases": len(results),
            "passed_cases": sum(item["status"] == "pass" for item in results),
            "validity": (report.metadata.get("run_validity") or {}).get("validity", "unknown"),
            "cost_status": "not_reported_by_legacy_runner",
        }
    except Exception as exc:
        from backend.orchestration.checkpoint import TaskCancelled

        if isinstance(exc, TaskCancelled):
            repository.terminate_run_with_gate(
                tenant_id, actor, run_id,
                status="cancelled",
                invalid_reason="cancelled",
                policy_hash=repository.content_hash({"terminal": "cancelled"}),
                verdict="INVALID",
                reasons=[{"code": "cancelled"}],
            )
            return {"run_id": run_id, "status": "CANCELLED", "gate_verdict": "INVALID"}
        if isinstance(exc, PermissionError):
            repository.set_run_status(tenant_id, actor, run_id, "blocked", invalid_reason="authorization_changed")
            return {"run_id": run_id, "status": "BLOCKED", "reason": "authorization_changed"}
        current = repository.get_run(tenant_id, run_id)
        if current["job_status"] not in {"completed", "cancelled", "blocked"}:
            from backend.evaluation_v2.runtime_environment import (
                RuntimeEnvironmentMismatchError,
            )

            if isinstance(exc, UnsafeRunnerEnvironmentError):
                reason_code = str(exc) or "unsafe_runner_environment"
            elif isinstance(exc, CorpusSnapshotMissingError):
                reason_code = "corpus_snapshot_missing"
            elif isinstance(exc, RuntimeEnvironmentMismatchError):
                reason_code = exc.reason_code
            else:
                reason_code = "runner_error"
            repository.terminate_run_with_gate(
                tenant_id, actor, run_id,
                status="failed",
                invalid_reason=reason_code,
                policy_hash=repository.content_hash({"terminal": reason_code}),
                verdict="INVALID",
                reasons=[{"code": reason_code}],
            )
        raise
