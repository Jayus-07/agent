"""评测测试 conftest — 自动生成报告到 data/eval_runs/ 目录。

pytest_sessionfinish hook 在测试结束后：
  1. 从 session.config._golden_eval_report 获取 EvaluationService 产出的 EvalReport
  2. 调用 storage.persist_report 持久化到 data/eval_runs/{run_id}/
  3. 生成 Markdown + JSON 报告到同一目录
"""


def pytest_sessionfinish(session, exitstatus):
    """测试结束后生成报告。"""
    report = getattr(session.config, "_golden_eval_report", None)
    if not report:
        return

    try:
        from backend.evaluation.report import print_summary
        from backend.evaluation.storage import persist_report
    except ImportError:
        return

    try:
        print_summary(report)
    except UnicodeEncodeError:
        print("\n[报告摘要] (控制台编码不支持 emoji，详见 JSON/Markdown 报告)")

    try:
        run_dir = persist_report(report)
        print("\n报告已生成:")
        print(f"   目录: {run_dir}")
    except Exception as e:
        print(f"\n⚠️  报告生成失败: {e}")
