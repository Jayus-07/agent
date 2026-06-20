"""CLI 入口 — python -m evaluation [module] [options]"""

import argparse
import sys
from pathlib import Path
from evaluation.runner import run_all
from evaluation.report import print_summary, write_markdown_report, compare_reports

# Windows console encoding fix: force UTF-8 to avoid UnicodeEncodeError on CJK + emoji
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main():
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Agent Platform 评估框架 — 度量 Planner/RAG/SQL/E2E 质量",
    )
    parser.add_argument(
        "module", nargs="?", default="all",
        choices=["all", "planner", "rag", "sql", "e2e"],
        help="评估模块 (default: all)",
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

    args = parser.parse_args()

    live = args.live or args.judge

    if not live:
        print("⚠️  离线模式（--no-live），Planner/SQL/E2E 将跳过。使用 --live 获取真实评估。")

    report = run_all(
        module=args.module,
        live=live,
        smoke=args.smoke,
        judge=args.judge,
    )

    print_summary(report)

    output_dir = Path(args.output) if args.output else RESULTS_DIR / report.timestamp.replace(":", "-")
    write_markdown_report(report, output_dir)

    if args.verbose:
        print("\n--- Detailed Results ---")
        for r in report.results:
            icon = {"pass": "✓", "fail": "✗", "error": "⚠", "skip": "○"}.get(r.status, "?")
            print(f"  {icon} {r.case_id} [{r.status}] {r.metrics}")
            if r.error_msg:
                print(f"     error: {r.error_msg}")

    if args.compare:
        _do_compare(args.compare, report, RESULTS_DIR)

    # 返回适当退出码
    has_failures = any(r.status in ("fail", "error") for r in report.results)
    sys.exit(1 if has_failures and not args.smoke else 0)


def _do_compare(compare_id: str, current: "EvalReport", results_dir: Path):
    """加载历史报告并对比。"""
    from evaluation.models import EvalReport

    if compare_id == "latest":
        # 找最近的结果目录
        dirs = sorted(results_dir.glob("*"), key=lambda p: p.name, reverse=True)
        if not dirs:
            print("No previous results to compare.")
            return
        prev_dir = dirs[0]
    else:
        prev_dir = results_dir / compare_id

    # 尝试加载先前的 summary
    summary_files = list(prev_dir.glob("summary*.md"))
    if not summary_files:
        print(f"No summary found in {prev_dir}")
        return

    # 简化：重新构造基础报告用于对比
    print(f"\nComparing with: {prev_dir.name}")
    print(f"Current total_score: {current.total_score}")
    # 对比需要完整的历史 EvalReport，这里仅展示简单对比
    # 完整实现需要序列化 EvalReport 到 JSON 并反序列化


if __name__ == "__main__":
    main()
