"""CLI 入口 — python -m evaluation [module] [options]

可移植性：此文件零项目依赖。通过 --runner-config 或默认导入 runners_config
来注册项目特定的 runner。复制到新项目后无需修改此文件。

V1.0: 中文化 verbose 输出 + compare 输出。
"""
import argparse
import importlib
import sys
from pathlib import Path

from backend.evaluation.gate import flag_regressions
from backend.evaluation.report import (
    print_summary,
    write_json_report,
    write_markdown_report,
)
from backend.evaluation.runner import run_all
from backend.evaluation.storage import DATA_ROOT

# Windows asyncio 修复：SelectorEventLoop 避免 ProactorEventLoop 清理时的
# "RuntimeError: Event loop is closed" 错误（来自 aiohttp/Ollama HTTP 客户端的 pipe transport）
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore[attr-defined]

# Windows console encoding fix: force UTF-8 to avoid UnicodeEncodeError on CJK + emoji
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS_DIR = DATA_ROOT

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
        description="Agent Platform 评估框架 — 度量 Planner/RAG 质量",
    )
    parser.add_argument(
        "module", nargs="?", default="all",
        choices=["all", "planner", "rag"],
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
        help="启用 LLM-as-Judge 评分（隐含 --live）",
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
        help="自定义评测集文件名（如 custom.json），用 rag runner 跑该评测集",
    )
    parser.add_argument(
        "--selection", type=str, default=None, metavar="NAME",
        help="命名选择集（如 ci_golden），从 datasets/rag/{NAME}.jsonl 加载",
    )
    parser.add_argument(
        "--tier", type=str, default="all",
        choices=["all", "smoke", "core", "hard", "regression"],
        help="分层评估: 按用例 tier 过滤 (默认: all, 不过滤)",
    )
    parser.add_argument(
        "--ragas", action="store_true",
        help="RAGAS-only 模式（跳过自研 semantic 评测）",
    )
    parser.add_argument(
        "--no-ragas", action="store_true",
        help="Semantic-only 模式（跳过 RAGAS 评测）",
    )
    parser.add_argument(
        "--ragas-level", type=str, default="standard",
        choices=["basic", "standard", "full"],
        help="RAGAS 指标档位: basic(2项) / standard(4项, 默认) / full(5项)",
    )
    parser.add_argument(
        "--semantic-thresholds", type=str, default=None, metavar="JSON",
        help="语义指标阈值配置（JSON 字符串），如 '{\"sem_context_recall_min\": 0.55}'",
    )
    parser.add_argument(
        "--regression", action="store_true",
        help="与上一次 baseline 对比，检测指标回归",
    )
    parser.add_argument(
        "--promote-baseline", action="store_true",
        help="将本次结果提升为新 baseline",
    )

    args = parser.parse_args()

    # 注册 runner（在 run_all 之前）
    _bootstrap_runners(args.runner_config)

    live = args.live or args.judge

    # 解析语义阈值 JSON
    semantic_thresholds = None
    if args.semantic_thresholds:
        import json as _json
        try:
            semantic_thresholds = _json.loads(args.semantic_thresholds)
        except _json.JSONDecodeError as e:
            print(f"⚠️  --semantic-thresholds JSON 解析失败: {e}")
            sys.exit(1)

    if not live:
        print("⚠️  离线模式（未启用 --live），Planner 将跳过。使用 --live 获取真实评估。")

    report = run_all(
        module=args.module,
        live=live,
        smoke=args.smoke,
        judge=args.judge,
        dataset_file=args.dataset,
        tier=args.tier,
        ragas=args.ragas,
        no_ragas=args.no_ragas,
        ragas_level=args.ragas_level,
        selection=args.selection,
        semantic_thresholds=semantic_thresholds,
        regression=args.regression,
        promote_baseline=args.promote_baseline,
    )

    print_summary(report)

    # 统一输出到 data/eval_runs/{run_id}/，所有产物（report.json + per_case + markdown + JSON）在同一目录
    try:
        from backend.evaluation.storage import persist_report
        run_dir = persist_report(report)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  persist_report 失败: {e}")
        run_dir = None

    output_dir = Path(args.output) if args.output else (run_dir or RESULTS_DIR / report.timestamp.replace(":", "-"))
    write_markdown_report(report, output_dir)
    write_json_report(report, output_dir)

    # 回归检测
    if args.regression:
        try:
            from backend.evaluation.gate.regression import check_regression
            exit_code = check_regression(report)
            if exit_code == 0:
                print("✅ 回归检测: 无显著下降")
            else:
                print(f"\n⚠️  回归检测: 指标下降超过阈值 (exit_code={exit_code})")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  回归检测失败: {e}")

    # 提升 baseline
    if args.promote_baseline:
        try:
            from backend.evaluation.gate.regression import promote
            promote(report)
            print("✅ 本次结果已提升为新 baseline")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  提升 baseline 失败: {e}")

    if args.verbose:
        _print_verbose(report)

    if args.compare:
        _do_compare(args.compare, report, RESULTS_DIR, current_dir=output_dir)

    # V2: 分层退出码 — 任一层级未达阈值即退出 1
    tier_failed = [ts for ts in report.tier_summaries if not ts.passed_threshold]
    if tier_failed:
        for ts in tier_failed:
            print(
                f"⚠️  [{ts.tier}] 通过率 {ts.pass_rate:.1%} "
                f"< 阈值 {ts.threshold:.1%}"
            )
        sys.exit(1)
    sys.exit(0)


def _print_verbose(report) -> None:
    """verbose 模式：逐 case 输出（中文标签）。"""
    from backend.evaluation.report import METRIC_LABELS, STATUS_ICONS, STATUS_LABELS
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
