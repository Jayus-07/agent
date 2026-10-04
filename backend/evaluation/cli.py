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
from backend.evaluation.models import MODULE_KINDS
from backend.evaluation.report import (
    print_summary,
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
_DEFAULT_GOLDEN_PATH = None  # cli.py 零项目依赖原则：延迟到 main() 里解析


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


def _bootstrap_llm_registry() -> None:
    """独立评估进程启动时加载数据库模型、供应商和凭据覆盖层。"""
    try:
        import asyncio

        from backend.infra.llm.registry_store import refresh_registry

        asyncio.run(refresh_registry())
    except Exception as exc:  # noqa: BLE001
        # 注册表刷新失败不阻止纯离线评估；需要真实 RAG 时由下游给出明确错误。
        print(f"[bootstrap] refresh_registry 失败: {exc}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。

    独立成函数是为了让单测能直接校验 `module` 的 choices 与
    `models.ModuleKind` 完全一致（历史上这里手写过一份模块清单，与后端漂移）。
    """
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Agent Platform 评估框架 — 度量 Planner/RAG/SQL 质量",
    )
    parser.add_argument(
        "module", nargs="?", default="all",
        # 模块清单派生自 models.ModuleKind（唯一事实源），不再手写一份
        choices=["all", *MODULE_KINDS],
        help="评估模块 (默认: all)。cs=客服域（offline sanity=320 条锁版结构校验，"
             "--live 走真实图；runner 注册于 runners/cs.py）；"
             "travel=旅游规划质量金标（STOP I5）；"
             "travel-provider=Provider 层探针（STOP J9）；"
             "travel-commerce=Commerce 金标探针（STOP K7）；"
             "travel-booking=Booking 事务探针（STOP L8）",
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
        help="命名 RAG suite（如 pr_baseline/expanded_100/scale_20k）",
    )
    parser.add_argument("--kb-id", type=str, default=None, help="显式校验评测 KB")
    parser.add_argument("--fixture-set", type=str, default=None, help="显式校验运行时语料范围")
    parser.add_argument("--dataset-version", type=str, default=None, help="显式校验数据版本")
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="评测运行标识；中断后传入同一 ID 续跑 checkpoint",
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
    parser.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help="case 级并发线程数（默认 1=串行；本地 LLM 生成场景收益有限）",
    )
    parser.add_argument(
        "--ragas-workers", type=int, default=2, metavar="N",
        help="RAGAS 批量评估并发线程数（默认 2；4 曾触发 DashScope 限流丢样本，"
             "配合 RAGAS_METRIC_RETRIES 限流重试使用）",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="忽略 checkpoint 断点续跑，强制全量重跑",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="C2-2/RUN-02：对已终态 run_id 强制全量重跑（覆盖终态拒绝保护，"
             "操作落审计）；不带 --resume/--force 对终态 run 重跑会被拒绝（409 语义）",
    )
    parser.add_argument(
        "--multiquery", action="store_true",
        help="评测检索链套生产 MultiQuery 层（对齐线上真实链路口径）",
    )
    parser.add_argument(
        "--full-trace", action="store_true",
        help="per_case 保留完整 page_content 与 span input/output（默认瘦身）",
    )
    parser.add_argument(
        "--triggered-by", type=str, default=None, metavar="ACTOR",
        help="触发者身份（M7：落 eval_run_records.triggered_by；"
             "admin 发起时传操作者，CI 传 pipeline 名）",
    )
    parser.add_argument(
        "--judge-golden", type=str, default=None, metavar="PATH", nargs="?",
        const=str(_DEFAULT_GOLDEN_PATH),
        help="C4-4：运行 Judge golden 样本集跑分（换 Judge 模型/prompt 前必跑）；"
             "不带值时使用内置数据集 datasets/judge/golden.jsonl",
    )
    parser.add_argument(
        "--judge-golden-baseline", type=str, default=None, metavar="PATH",
        help="C4-5：与指定 golden 历史报告对比，档位翻转/分值漂移超阈值 → exit 2",
    )
    parser.add_argument(
        "--judge-golden-repeat", type=int, default=1, metavar="N",
        help="C4-6：同集重复运行 N 次输出波动报告（默认 1）",
    )
    parser.add_argument(
        "--judge-golden-threshold", type=float, default=0.1, metavar="D",
        help="C4-5：分值漂移容差（默认 0.1）",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.triggered_by:
        import os as _os
        _os.environ["EVAL_TRIGGERED_BY"] = args.triggered_by

    # RAGPipeline 会在 runner 执行阶段读取进程内 DB 覆盖层，必须先刷新。
    _bootstrap_llm_registry()

    # 注册 runner（在 run_all 之前）
    _bootstrap_runners(args.runner_config)

    # ── C4-4/5/6：Judge golden 三件套（跑分 / 基线对比 / 波动）──
    if args.judge_golden:
        _run_judge_golden_flow(args)
        return  # golden 流程独立成支，不落 run_all

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

    try:
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
            kb_id=args.kb_id,
            fixture_set=args.fixture_set,
            dataset_version=args.dataset_version,
            run_id=args.run_id,
            semantic_thresholds=semantic_thresholds,
            regression=args.regression,
            promote_baseline=args.promote_baseline,
            workers=args.workers,
            ragas_workers=args.ragas_workers,
            resume=not args.no_resume,
            multiquery=args.multiquery,
            full_trace=args.full_trace,
            force_rerun=args.force,
        )
    except Exception as e:
        # C2-2：终态 run 隐式重跑 → 409 语义（shell exit 12 供 CI 判别）
        from backend.evaluation.service import RunAlreadyFinalizedError

        if isinstance(e, RunAlreadyFinalizedError):
            print(f"❌ {e}")
            sys.exit(12)
        raise

    print_summary(report)

    # 统一输出到 data/eval_runs/{run_id}/，所有产物（report.json + per_case + markdown）在同一目录
    try:
        from backend.evaluation.storage import persist_report
        run_dir = persist_report(report)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  persist_report 失败: {e}")
        run_dir = None

    output_dir = Path(args.output) if args.output else (run_dir or RESULTS_DIR / report.timestamp.replace(":", "-"))
    write_markdown_report(report, output_dir)
    # JSON 报告只保留 persist_report 的 report.json 一份（原 write_json_report
    # 会再写内容重复的 eval-*.json，体积翻倍）；--compare 已兼容读取两种文件

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


def _run_judge_golden_flow(args) -> None:
    """judge-golden 跑分 + 可选基线对比 + 波动报告（exit 2=漂移阻断）。"""
    from pathlib import Path as _Path

    from backend.evaluation.judge_golden import (
        compare_golden_reports,
        persist_golden_report,
        run_golden,
    )

    golden_path = _Path(args.judge_golden) if args.judge_golden else None
    print(f"=== Judge Golden 跑分（repeat={args.judge_golden_repeat}）===")
    report = run_golden(golden_path=golden_path, repeat=args.judge_golden_repeat)

    for cid, c in report["cases"].items():
        flipped = any(v != c["expected_verdict"] for v in c["verdicts"])
        flag = "✅" if not flipped else "❌"
        print(
            f"  {flag} {cid}: mean={c['mean']} std={c['std']} "
            f"期望档位={c['expected_verdict']} 实际档位={sorted(set(c['verdicts']))}"
        )
    print(
        f"\n汇总：{report['total']} 条 | 档位翻转 {report['tier_flip_count']} | "
        f"judge 失败 {report['judge_error_count']} | 稳定性 std 均值 "
        f"{report['stability']['mean_of_std']}"
    )
    out = persist_golden_report(report)
    print(f"golden 报告已保存: {out}")

    if args.judge_golden_baseline:
        from backend.evaluation.judge_golden import load_golden_report

        baseline = load_golden_report(_Path(args.judge_golden_baseline))
        comparison = compare_golden_reports(
            report, baseline, deviation_threshold=args.judge_golden_threshold,
        )
        print(f"\n=== 基线对比（{args.judge_golden_baseline}）===")
        for d in comparison["score_drifts"]:
            print(
                f"  ⚠️ {d['case_id']}: {d['baseline_mean']} → {d['current_mean']} "
                f"(Δ{d['delta']})"
            )
        for cid in comparison["new_tier_flips"]:
            print(f"  ❌ {cid}: 期望档位翻转")
        print(comparison["summary"])
        if not comparison["passed"]:
            print("\n❌ Judge 漂移超过容差，阻断（exit 2）——升级前必须排查")
            sys.exit(2)


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

    # 找最近的 JSON 报告（persist_report 的 report.json + 历史 eval-*.json，递归查找）
    json_files = sorted(
        [p for pat in ("report.json", "eval-*.json") for p in results_dir.rglob(pat)],
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

    # 可比性校验：smoke(5条) vs 全量、offline vs live、不同 dataset/tier 的
    # 报告直接对比，delta 无意义。不匹配时警告并终止对比。
    mismatches = []
    if base.mode != current.mode:
        mismatches.append(f"mode: {base.mode} vs {current.mode}")
    if base.smoke != current.smoke:
        mismatches.append(f"smoke: {base.smoke} vs {current.smoke}")
    if base.tier != current.tier:
        mismatches.append(f"tier: {base.tier} vs {current.tier}")
    base_total = sum(s.total for s in base.summaries)
    cur_total = sum(s.total for s in current.summaries)
    if base_total != cur_total:
        mismatches.append(f"用例数: {base_total} vs {cur_total}")
    if mismatches:
        print(f"\n⚠️  历史报告 {prev_file.name} 与本次不可比（{'; '.join(mismatches)}），跳过对比。")
        print("   请用 --promote-base 重建同口径基线后再对比。")
        return

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
            # metrics 可能含嵌套 dict（token_summary）/None（无法计算），跳过非数值
            if isinstance(cur_val, bool) or not isinstance(cur_val, (int, float)):
                continue
            base_val = prev_s.metrics.get(key)
            if isinstance(base_val, bool) or not isinstance(base_val, (int, float)):
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
