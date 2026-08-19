"""CLI 入口 — python -m evaluation [module] [options]

可移植性：此文件零项目依赖。通过 --runner-config 或默认导入 runners_config
来注册项目特定的 runner。复制到新项目后无需修改此文件。

V1.0: 中文化 verbose 输出 + compare 输出。
"""
import argparse
import importlib
import sys
from pathlib import Path
from backend.evaluation.runner import run_all
from backend.evaluation.report import (
    print_summary,
    write_markdown_report,
    write_json_report,
    compare_reports,
    flag_regressions,
)

# Windows console encoding fix: force UTF-8 to avoid UnicodeEncodeError on CJK + emoji
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS_DIR = Path(__file__).resolve().parent / "results"

_DEFAULT_RUNNER_CONFIG = "backend.evaluation.runners_config"


def _bootstrap_runners(config_module: str | None = None):
    """在 run_all() 之前注册 runner。

    1. 如果指定了 config_module，导入它
    2. 否则尝试导入默认的 evaluation.runners_config
    3. 如果默认也不存在（纯净框架），静默跳过——所有模块返回 skip
    """
    module_name = config_module or _DEFAULT_RUNNER_CONFIG
    try:
        importlib.import_module(module_name)
    except ImportError:
        if config_module:
            print(f"⚠️  未找到 Runner 配置模块: {config_module}")
            print("   没有注册任何 runner，所有模块将返回 'skip'。")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Agent Platform 评估框架 — 度量 Planner/RAG/SQL/E2E 质量",
    )
    parser.add_argument(
        "module", nargs="?", default="all",
        choices=["all", "planner", "rag", "sql", "e2e"],
        help="评估模块 (默认: all)",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="快速冒烟（每模块仅取 5 条用例）",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="启用真实 LLM 调用（推荐用于获取真实基线）",
    )
    parser.add_argument(
        "--judge", action="store_true",
        help="启用 LLM-as-Judge 评分 E2E 答案（隐含 --live）",
    )
    parser.add_argument(
        "--compare", type=str, default=None, metavar="ID",
        help="与指定历史跑分对比，ID 可以是 'latest'",
    )
    parser.add_argument(
        "--output", type=str, default=None, metavar="DIR",
        help="报告输出目录",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="输出每条用例的详细结果",
    )
    parser.add_argument(
        "--runner-config", type=str, default=None, metavar="MODULE",
        help="自定义 runner 注册模块 (例如 myproject.eval_runners)",
    )
    parser.add_argument(
        "--dataset", type=str, default=None, metavar="FILE",
        help="自定义评测集文件名（如 rag_test_kb.json），用 rag runner 跑该评测集",
    )

    args = parser.parse_args()

    # 注册 runner（在 run_all 之前）
    _bootstrap_runners(args.runner_config)

    live = args.live or args.judge

    if not live:
        print("⚠️  离线模式（未启用 --live），Planner/SQL/E2E 将跳过。使用 --live 获取真实评估。")

    report = run_all(
        module=args.module,
        live=live,
        smoke=args.smoke,
        judge=args.judge,
        dataset_file=args.dataset,
    )

    print_summary(report)

    output_dir = Path(args.output) if args.output else RESULTS_DIR / report.timestamp.replace(":", "-")
    write_markdown_report(report, output_dir)
    # V2.0: HTML Dashboard 报告（自包含 CSS，浏览器直接打开）
    from backend.evaluation.report import write_html_report
    write_html_report(report, output_dir)
    # 持久化 JSON 全量报告（含每条 case 检索轨迹），供 baseline 对比
    write_json_report(report, output_dir)

    # V1.0: 持久化到 data/eval_runs/ + 写 PostgreSQL 聚合
    #   - run_dir 包含 report.json + per_case/{id}.json + meta.json（git_sha 等）
    #   - 失败不阻断：CLI 主流程已成功，持久化失败仅 warn
    try:
        from backend.evaluation.storage import persist_report
        persist_report(report)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  V1.0 persist_report 失败: {e}")

    if args.verbose:
        _print_verbose(report)

    if args.compare:
        _do_compare(args.compare, report, RESULTS_DIR, current_dir=output_dir)

    # 返回适当退出码
    has_failures = any(r.status in ("fail", "error") for r in report.results)
    sys.exit(1 if has_failures and not args.smoke else 0)


def _print_verbose(report) -> None:
    """verbose 模式：逐 case 输出（中文标签）。"""
    from backend.evaluation.report import METRIC_LABELS, STATUS_LABELS, STATUS_ICONS
    print("\n--- 详细结果 ---")
    for r in report.results:
        icon = STATUS_ICONS.get(r.status, "?")
        status_zh = STATUS_LABELS.get(r.status, r.status)
        zh_metrics = {METRIC_LABELS.get(k, k): v for k, v in r.metrics.items()}
        print(f"  {icon} {r.case_id} [{status_zh}] {zh_metrics}")
        if r.error_msg:
            print(f"     错误: {r.error_msg}")


def _do_compare(compare_id: str, current, results_dir: Path, current_dir: Path | None = None):
    """加载最近的历史 JSON 报告，反序列化为 EvalReport 后对比指标 + 标记下降（中文）。"""
    import json
    from backend.evaluation.models import EvalReport as _EvalReport
    from backend.evaluation.report import METRIC_LABELS, MODULE_LABELS

    # 找最近的 JSON 报告（write_json_report 存档的，递归查找）
    json_files = sorted(
        results_dir.rglob("eval-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # 排除当前这次的结果目录（对比「上一次 baseline」，而非自己）
    if current_dir is not None:
        json_files = [
            p for p in json_files
            if p.parent != current_dir and current_dir not in p.parents
        ]
    if not json_files:
        print("\n未找到可对比的历史 JSON 报告。")
        return

    prev_file = json_files[0]
    with open(prev_file, encoding="utf-8") as f:
        prev_dict = json.load(f)
    base = _EvalReport.model_validate(prev_dict)

    print(f"\n=== 基线对比: {prev_file.name} ===")

    # 逐模块打印对比（中文）
    base_by_mod = {s.module: s for s in base.summaries}
    for cur_s in current.summaries:
        prev_s = base_by_mod.get(cur_s.module)
        if prev_s is None:
            continue
        mod_zh = MODULE_LABELS.get(cur_s.module, cur_s.module)
        pass_delta = cur_s.pass_rate - prev_s.pass_rate
        pass_flag = "  ⚠️ 下降" if pass_delta < -0.05 else ""
        print(f"\n[{mod_zh}]  通过率: {prev_s.pass_rate:.1%} → "
              f"{cur_s.pass_rate:.1%} ({pass_delta:+.1%}){pass_flag}")
        for key, cur_val in cur_s.metrics.items():
            base_val = prev_s.metrics.get(key)
            if base_val is None:
                continue
            delta = cur_val - base_val
            label = METRIC_LABELS.get(key, key)
            flag = "  ⚠️ 下降" if delta < -0.05 else ""
            print(f"  {label}: {base_val:.4f} → {cur_val:.4f} ({delta:+.4f}){flag}")

    # 统一回归判断走 flag_regressions（消除重复实现，单一阈值源）
    warnings = flag_regressions(base=base, current=current)
    if warnings:
        print(f"\n⚠️  共 {len(warnings)} 项指标下降超过 5%")
        for w in warnings:
            print(f"  {w}")
    else:
        print("\n✅ 无指标显著下降")


if __name__ == "__main__":
    main()
