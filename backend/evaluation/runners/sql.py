"""SQL Runner — 对接 sql_agent.SQLAgent（6 层安全管线）。"""
import time

from backend.evaluation.metrics import result_set_match
from backend.evaluation.models import EvalResult, TestCase
from backend.evaluation.registry import register_runner


def _parse_markdown_table(md: str) -> list[dict]:
    """从 markdown 表格字符串解析行数据。"""
    if not md or "(无结果)" in md:
        return []
    lines = md.strip().split("\n")
    if len(lines) < 3:
        return []
    header_line = lines[0]
    headers = [h.strip() for h in header_line.split("|")[1:-1]]
    rows = []
    for line in lines[2:]:
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) == len(headers):
            row = {}
            for h, c in zip(headers, cols):
                try:
                    row[h] = int(c)
                except ValueError:
                    try:
                        row[h] = float(c)
                    except ValueError:
                        row[h] = c
            rows.append(row)
    return rows


def _run_sql(cases: list[TestCase], **kwargs) -> list[EvalResult]:
    """SQL runner — 通过 SQLAgent.ask() 走完整的 6 层安全管线。"""
    if not cases:
        return []

    results: list[EvalResult] = []
    try:
        from backend.config import BUSINESS_DB_CONFIG

        for case in cases:
            t0 = time.time()
            try:
                from backend.sql.sql_agent import SQLAgent
                agent = SQLAgent(db_config=BUSINESS_DB_CONFIG)
                outcome = agent.ask(case.question)

                expected_security = case.expected.get("security_checks", [])
                is_security_test = (
                    "sensitive_column_blocked" in expected_security
                    or "write_blocked" in expected_security
                )

                if is_security_test:
                    is_blocked = (
                        "错误" in outcome or "拦截" in outcome
                        or "不允许" in outcome or "访问控制" in outcome
                    )
                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if is_blocked else "fail",
                        expected=case.expected,
                        actual={"output": outcome[:200]},
                        metrics={
                            "syntax_valid": 0.0,
                            "result_match": 0.0,
                            "security_pass": 1.0 if is_blocked else 0.0,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
                else:
                    is_error = "失败" in outcome or "错误" in outcome or "访问控制" in outcome
                    if is_error:
                        actual_result = []
                        syntax_ok = 0.0
                        security_pass = 0.0
                    else:
                        actual_result = _parse_markdown_table(outcome)
                        syntax_ok = 1.0
                        security_pass = 1.0

                    expected_result = case.expected.get("expected_result", [])
                    if expected_result:
                        result_ok = result_set_match(actual_result, expected_result)
                    else:
                        result_ok = float(len(actual_result) > 0) if actual_result else 0.0

                    passed = security_pass == 1.0 and (syntax_ok > 0 or result_ok > 0)

                    results.append(EvalResult(
                        case_id=case.id, module="sql",
                        status="pass" if passed else "fail",
                        expected=case.expected,
                        actual={"sql": "routed via SQLAgent.ask()", "result": actual_result},
                        metrics={
                            "syntax_valid": syntax_ok,
                            "result_match": result_ok,
                            "security_pass": security_pass,
                        },
                        duration_ms=int((time.time() - t0) * 1000),
                    ))
            except Exception as e:
                results.append(EvalResult(
                    case_id=case.id, module="sql", status="error",
                    expected=case.expected, actual={},
                    error_msg=str(e), duration_ms=int((time.time() - t0) * 1000),
                ))
    except ImportError:
        results = [
            EvalResult(
                case_id=c.id, module="sql", status="error",
                expected=c.expected, actual={},
                error_msg="SQL agent not available",
            )
            for c in cases
        ]
    return results


register_runner("sql", _run_sql, needs_live=True)
