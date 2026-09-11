"""SQL Runner — NL2SQL 全链路评测（router → generate → validate → execute）。

双模式（与 rag runner 的 live/offline 双轨一致）：

离线模式 (live=False) — 数据集健全性校验，不调 LLM / 不连库：
  - 每条正向用例的 gold_sql 必须通过 sql_validator（表白名单、保留字、
    LIMIT 语义），保证 gold 本身可在业务仓库执行；
  - expected_tables 必须全部在表白名单内；
  - 对抗用例（should_reject）无 gold SQL，标记 skip。
  适合 CI 常驻：数据集改动即触发校验。

在线模式 (live=True) — 真实调用 SQLAgent，三指标：
  - router_hit       生成 SQL 引用的表对 expected_tables 的覆盖率
  - execution_match  执行准确率：生成 SQL 与 gold_sql 的结果集一致性
                     （Spider 系评测的标准做法；gold 先过 validator 以
                     获得与生成 SQL 相同的 LIMIT/引号归一化）
  - safety_pass      正向用例未被安全层拦截
  对抗用例按 expected_status 判定拒绝语义是否生效。
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from decimal import Decimal

from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner
from backend.shared.logger import logger

# 不可重试且意味着"正确拒绝"的状态在用例 expected_status 中显式声明；
# 这里只定义正向用例的成功状态。
_SUCCESS_STATUSES = {"success", "no_data"}

# 从 SQL 文本提取 FROM/JOIN 引用的 schema 限定表名。
# 每段标识符允许可选双引号（PG 保留字 schema 如 "order" 必须带引号，
# LLM 生成与 gold SQL 都可能出现）。
_TABLE_RE = re.compile(r'(?:FROM|JOIN)\s+"?([a-z_][a-z_0-9]*)"?\."?([a-z_][a-z_0-9]*)"?', re.IGNORECASE)


def _extract_tables(sql_text: str | None) -> set[str]:
    if not sql_text:
        return set()
    return {f"{s}.{t}".lower() for s, t in _TABLE_RE.findall(sql_text)}


def _norm_value(v) -> str:
    """结果值归一化：Decimal/日期/浮点统一为可比较字符串。"""
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(round(float(v), 4))
    if isinstance(v, float):
        return str(round(v, 4))
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def _rows_to_tuples(rows: list[dict], columns: list[str]) -> list[tuple]:
    return [tuple(_norm_value(row.get(c)) for c in columns) for row in rows]


def _results_match(gold_rows, gold_cols, pred_rows, pred_cols, ordered: bool) -> bool:
    """结果集一致性：列数一致 + 行元组一致（有序或无序多重集）。"""
    if not gold_cols or not pred_cols:
        return False
    if len(gold_cols) != len(pred_cols):
        return False
    g = _rows_to_tuples(gold_rows or [], gold_cols)
    p = _rows_to_tuples(pred_rows or [], pred_cols)
    if len(g) != len(p):
        return False
    if ordered:
        return g == p
    return sorted(map(repr, g)) == sorted(map(repr, p))


# =================================================
# 离线模式：数据集健全性校验
# =================================================

def _run_offline_sanity(cases: list[TestCase]) -> list[EvalResult]:
    from backend.sql.schema_loader import schema_loader
    from backend.sql.sql_validator import sql_validator, ValidationError

    results: list[EvalResult] = []
    for case in cases:
        exp = case.expected
        if exp.get("should_reject") or not exp.get("gold_sql"):
            results.append(EvalResult(
                case_id=case.id, module="sql", status="skip",
                expected=exp, actual={},
            ))
            continue

        problems: list[str] = []
        bad_tables = [
            t for t in exp.get("expected_tables", [])
            if t not in schema_loader.allowed_tables
        ]
        if bad_tables:
            problems.append(f"expected_tables 不在表白名单: {bad_tables}")

        try:
            sql_validator.validate(exp["gold_sql"])
        except ValidationError as e:
            problems.append(f"gold_sql 未通过安全校验(layer={e.layer}): {e}")
        except Exception as e:
            problems.append(f"gold_sql 校验异常: {e}")

        results.append(EvalResult(
            case_id=case.id, module="sql",
            status="fail" if problems else "pass",
            expected=exp,
            actual={"problems": problems},
            error_msg="; ".join(problems) or None,
        ))
    return results


# =================================================
# 在线模式：真实链路评测
# =================================================

def _eval_reject_case(case: TestCase, result, exp: dict) -> EvalResult:
    """对抗用例：agent 状态落在 expected_status 内即为正确拒绝。"""
    expected_status = set(exp.get("expected_status") or ["no_table"])
    rejected = result.status in expected_status
    return EvalResult(
        case_id=case.id, module="sql",
        status="pass" if rejected else "fail",
        expected=exp,
        actual={
            "status": result.status,
            "error_type": result.error_type,
            "error": result.error,
        },
        metrics={"safety_pass": 1.0 if rejected else 0.0},
        error_msg=None if rejected else (
            f"期望拒绝状态 {sorted(expected_status)}，实际 status={result.status}"
        ),
    )


def _eval_positive_case(case: TestCase, result, exp: dict) -> EvalResult:
    """正向用例：router 命中 + 执行准确率 + 安全通过。"""
    expected_tables = set(exp.get("expected_tables") or [])
    selected = _extract_tables(result.sql_text)
    router_hit = (
        len(selected & expected_tables) / len(expected_tables)
        if expected_tables else None
    )
    safety_pass = 1.0 if result.status in _SUCCESS_STATUSES else 0.0
    metrics: dict[str, float | None] = {
        "router_hit": router_hit,
        "safety_pass": safety_pass,
        "execution_match": None,
    }

    if result.status not in _SUCCESS_STATUSES:
        return EvalResult(
            case_id=case.id, module="sql", status="fail",
            expected=exp,
            actual={"status": result.status, "error": result.error,
                    "sql": result.sql_text},
            metrics=metrics,
            error_msg=f"查询失败 status={result.status}: {result.error}",
        )

    gold_sql = exp.get("gold_sql")
    if not gold_sql:
        passed = router_hit is None or router_hit == 1.0
        return EvalResult(
            case_id=case.id, module="sql",
            status="pass" if passed else "fail",
            expected=exp,
            actual={"status": result.status, "row_count": result.row_count,
                    "sql": result.sql_text},
            metrics=metrics,
            error_msg=None if passed else f"router 未命中全部期望表: {sorted(expected_tables - selected)}",
        )

    # gold SQL 先过 validator → 与生成 SQL 相同的 LIMIT/引号归一化
    from backend.sql.sql_validator import sql_validator
    from backend.sql.executor import execute_sql_struct

    try:
        safe_gold, _, _ = sql_validator.validate(gold_sql)
        gold_res = execute_sql_struct(safe_gold, {})
    except Exception as e:
        logger.warning(f"[SQL Eval] gold SQL 执行失败，降级为 router 判定: {e}")
        gold_res = None

    if gold_res is not None and gold_res.status in _SUCCESS_STATUSES:
        ordered = "ORDER BY" in gold_sql.upper()
        match = _results_match(
            gold_res.rows, gold_res.columns, result.rows, result.columns,
            ordered=ordered,
        )
        metrics["execution_match"] = 1.0 if match else 0.0
        passed = match
        error_msg = None if match else "生成 SQL 结果集与 gold_sql 不一致"
    else:
        # 库不可用 / gold 执行失败 → 降级为 router 命中判定
        passed = router_hit is not None and router_hit == 1.0
        error_msg = None if passed else f"router 未命中全部期望表: {sorted(expected_tables - selected)}"

    return EvalResult(
        case_id=case.id, module="sql",
        status="pass" if passed else "fail",
        expected=exp,
        actual={"status": result.status, "row_count": result.row_count,
                "sql": result.sql_text},
        metrics=metrics,
        error_msg=error_msg,
    )


def _run_live(cases: list[TestCase]) -> list[EvalResult]:
    from backend.sql.sql_agent import get_sql_agent

    agent = get_sql_agent()
    results: list[EvalResult] = []
    for case in cases:
        t0 = time.time()
        exp = case.expected
        try:
            result = agent.ask_struct(case.question)
        except Exception as e:
            results.append(EvalResult(
                case_id=case.id, module="sql", status="error",
                expected=exp, actual={},
                error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
            ))
            continue

        try:
            if exp.get("should_reject"):
                er = _eval_reject_case(case, result, exp)
            else:
                er = _eval_positive_case(case, result, exp)
        except Exception as e:
            er = EvalResult(
                case_id=case.id, module="sql", status="error",
                expected=exp, actual={"status": result.status},
                error_msg=f"评测逻辑异常: {e}",
            )
        er.duration_ms = int((time.time() - t0) * 1000)
        results.append(er)
    return results


def _run_sql(cases: list[TestCase], live: bool = False, **kwargs) -> list[EvalResult]:
    if not live:
        return _run_offline_sanity(cases)
    return _run_live(cases)


register_runner("sql", _run_sql, needs_live=False)
