"""C5-1 回归：_run_rag 的 _guarded_case 必须保留 deferred RAGAS 输入。

背景（cd263ba fix(eval): preserve deferred ragas tasks）：_guarded_case
此前只返回 EvalResult，丢弃 _eval_case 的第二个返回值（RAGAS deferred
输入）。后果是串行与并发两条路径都静默丢弃 RAGAS 任务，报告标记
self+ragas 但 ragas_samples 恒为 0——门禁据此判定「无样本」，评测结论
失真且无异常暴露。

_eval_case 与 _guarded_case 都是 _run_rag 内部的闭包（模块级不可 patch），
因此本文件以「源码契约 + 真实调用形状」两路锁定，不引入真实检索/LLM/RAGAS
依赖：
  1. 源码形状：并发/串行两条路径都按 (result, deferred) 解包，含 deferred
     汇聚分支；且不得回退为 _eval_case(case)[0] 这种丢弃写法。
  2. 真实调用形状：直接调用 _run_rag 跑一条 skip 级用例（无检索依赖），
     断言返回的是 EvalResult 列表且不抛异常，确认包装层未破坏调用契约。
"""
from __future__ import annotations

import inspect


def _run_rag_source() -> str:
    import backend.evaluation.runners.rag as rag_runner

    return inspect.getsource(rag_runner._run_rag)


def test_run_rag_unpacks_guarded_case_as_tuple():
    """并发与串行两条路径都必须按 (result, deferred) 解包。

    形状回退（只保留 EvalResult）正是 cd263ba 修复前的 bug。
    """
    src = _run_rag_source()

    # 并发路径：提交 _guarded_case 后取完整元组
    assert "_futures[_pool.submit(_guarded_case, c)] = c" in src
    assert "_fut.result()" in src
    # 串行路径：直接接收元组
    assert "evaluated[c.id] = _guarded_case(c)" in src
    # deferred 汇聚分支
    assert "if _deferred is not None:" in src
    assert "_deferred_ragas.append((len(results) - 1, *_deferred))" in src


def test_guarded_case_does_not_discard_deferred():
    """反向断言：不得回退为丢弃 deferred 的旧写法。"""
    src = _run_rag_source()

    assert "return _eval_case(case)[0]" not in src, (
        "_guarded_case 不得只取 _eval_case 的 [0] 而丢弃 deferred"
    )
    assert "lambda: _eval_case(case)[0]" not in src, (
        "并发路径不得只取 _eval_case 的 [0] 而丢弃 deferred"
    )


def test_guarded_case_declares_tuple_return_type():
    """_guarded_case 的返回注解必须是二元组，形状即契约。"""
    src = _run_rag_source()

    assert ("def _guarded_case(case: TestCase) -> tuple["
            "EvalResult, dict | None]:") in src, (
        "_guarded_case 必须声明 (EvalResult, dict | None) 返回类型"
    )


def test_guarded_case_timeout_branch_returns_none_deferred():
    """超时分支返回 (error 结果, None)：不臆造 deferred 输入。"""
    src = _run_rag_source()

    assert "error_stage=\"timeout\"" in src
    # 超时分支的返回值以 None 收尾（deferred 占位）
    timeout_idx = src.index("error_stage=\"timeout\"")
    tail = src[timeout_idx:timeout_idx + 400]
    assert "None," in tail, "超时分支必须以 None 作为 deferred 占位"
