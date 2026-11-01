# -*- coding: utf-8 -*-
"""CS 决策层离线回放门禁（cs-evaluation-upgrade P1，2026-11-02）。

将 320 条锁版断言从「存在性测试」（test_regression 只验能加载/能过 validate）
升级为真实执行：query -> CSRouter.route()（完整生产入口，零 LLM）
-> intent / route_path / risk_level，对照 expected.{intent,target,risk_level}。

数据集：backend/evaluation/datasets/cs/cases.jsonl（v2.0 锁版，统一 loader 加载，
manifest content_hash 在加载时强制校验——原地改即 fail-fast）。

基线与阈值（cs-evaluation-upgrade P3，2026-11-02，数据集 v2.2 共 324 条）：
  门禁口径 intent 75.7%（215/284）/ target 80.3%（228/284）/ risk 79.9%（195/244）
  阈值防退化不防历史：intent≥0.74 / target≥0.79 / risk≥0.78。
  （v2.1 基线 intent 56.8% / target 62.9% / risk 72.5% 系 P3 词表扩圈前口径，
  扩圈依据见 PR 与 manifest notes；v2.0→v2.1 口径修订史见 P2 记录。基线
  漂移需以「阈值更新 + 依据」走 PR，不得静默放宽。）

离线回放范围（分层诚实，TASK §5）：
  - multi_turn 40 条整类 skip：会话级断言需上下文，单 query 离线回放口径
    错位——转 live/graph 层消费（P4），skip 显式计数（TestReplayCoverage）。
  - safety 类 risk 维度 skip：risk=high 期望属安全检测层（live）能力，规则层
    risk 系 INTENT_PROFILES 静态画像映射，离线不可满足；intent/target 保留。
  - LLM 依赖维度（allowed/forbidden_facts、must_contain 等 live 断言）离线
    不执行；词表层回放由 cs-gate（test_cs_eval_gate）覆盖。
"""

from collections import Counter, defaultdict

import pytest

from backend.customer_service.router import cs_router as cs_router_module
from backend.customer_service.router.cs_router import get_cs_router
from backend.evaluation.dataset import load_dataset

# 防退化阈值（防退化不防历史），依据见模块 docstring 与 manifest notes v2.2
THRESHOLDS = {"intent": 0.74, "target": 0.79, "risk": 0.78}

# 离线回放范围（分层诚实）：multi_turn 会话级断言需上下文，整类 skip；
# safety 的 risk=high 属安全检测层能力，规则层静态画像离线不可满足
_SKIP_CATEGORY_ALL = {"multi_turn"}
_SKIP_CATEGORY_DIM = {"risk": {"safety"}}

# CSRoutePath -> 数据集 expected.target 值域（锁版 v2.0）
TARGET_MAP = {
    "knowledge_query": "cs_knowledge",
    "business_query": "cs_business_query",
    "business_action": "cs_business_action",
    "complaint_flow": "cs_complaint",
    # human_handoff→cs_handoff：数据集值域为 cs_handoff（探针初版误写
    # cs_human，2026-11-02 与 v1 target 实测值核对后修正）
    "human_handoff": "cs_handoff",
}

# 断言维度 -> expected 字段名
_DIM_EXPECT_KEY = {"intent": "intent", "target": "target", "risk": "risk_level"}


class _NoCache:
    """测试内禁用路由缓存：隔离共享 Redis 读写，保证按当前代码即时回放。"""

    def get_json(self, _key):
        return None

    def set_json(self, _key, _data):
        return None


@pytest.fixture(scope="module", autouse=True)
def _no_router_cache():
    original = cs_router_module._cs_cache
    cs_router_module._cs_cache = _NoCache()
    yield
    cs_router_module._cs_cache = original


def _replay_rows():
    """回放 320 条锁版：[(case, query, pred)]；单条故障计 miss 不中断。"""
    cases = load_dataset("cs")
    assert len(cases) == 324, (
        f"锁版 324 条，实际加载 {len(cases)}——数据集/manifest 漂移，"
        "先核对 content_hash 与 case_count 再看门禁"
    )
    router = get_cs_router()
    rows = []
    for case in cases:
        # 多轮按首轮回放（基线口径）；单轮 v2 的 turns[0] 即问题本体
        turns = case.metadata.get("turns") or []
        query = turns[0]["text"] if turns else case.question
        try:
            result = router.route(query)
            pred = {
                "intent": result.intent,
                "target": TARGET_MAP.get(result.route_path.value, ""),
                "risk": result.risk_level,
            }
        except Exception as e:  # noqa: BLE001 — route() 自身已兜底，此处防御 start_span 前置异常
            pred = {"intent": f"<error:{type(e).__name__}>", "target": "", "risk": ""}
        rows.append({"case": case, "query": query, "pred": pred})
    return rows


@pytest.fixture(scope="module")
def replay_rows():
    return _replay_rows()


def _hit_stats(rows: list[dict], dim: str):
    """按类目聚合单维度命中（含 skip 口径）：返回 (total, hits, by_cat, misses)。

    expected 缺该维度断言（v1 的 intent/risk_level）计命中——基线口径。
    """
    skip_cats = _SKIP_CATEGORY_ALL | _SKIP_CATEGORY_DIM.get(dim, set())
    by_cat: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for row in rows:
        case = row["case"]
        cat = case.metadata.get("category", "v1")
        if cat in skip_cats:
            continue
        want = case.expected.get(_DIM_EXPECT_KEY[dim])
        got = row["pred"][dim]
        hit = True if want is None else got == want
        by_cat[cat]["n"] += 1
        by_cat[cat]["hit"] += hit
        if not hit:
            misses.append((case.id, want, got, row["query"]))
    total = sum(s["n"] for s in by_cat.values())
    hits = sum(s["hit"] for s in by_cat.values())
    return total, hits, by_cat, misses


class TestDecisionReplayBaselines:
    """决策层回放基线：整体命中率 ≥ 防退化阈值，失败输出类目分布 + 样例。"""

    def _assert_dimension(self, replay_rows, dim: str):
        total, hits, by_cat, misses = _hit_stats(replay_rows, dim)
        rate = hits / total
        breakdown = "\n".join(
            f"  {cat:12s} n={s['n']:3d} hit={s['hit']:3d} ({s['hit'] / s['n'] * 100:.1f}%)"
            for cat, s in sorted(by_cat.items())
        )
        samples = "\n".join(
            f"  {cid}: want={want} got={got} q={q[:40]}"
            for cid, want, got, q in misses[:20]
        )
        assert rate >= THRESHOLDS[dim], (
            f"决策层回放 {dim} 命中率 {rate:.3f}（{hits}/{total}）"
            f"跌破防退化阈值 {THRESHOLDS[dim]:.2f}\n"
            f"先查数据集/路由链路是否被改动；阈值更新须走 PR 带依据（TASK §9）\n"
            f"按类目：\n{breakdown}\n失败样例（前 20/{len(misses)}）：\n{samples}"
        )

    def test_intent_baseline(self, replay_rows):
        self._assert_dimension(replay_rows, "intent")

    def test_target_baseline(self, replay_rows):
        self._assert_dimension(replay_rows, "target")

    def test_risk_baseline(self, replay_rows):
        self._assert_dimension(replay_rows, "risk")


class TestReplayCoverage:
    """锁版断言真实执行的覆盖性（与 test_regression 的存在性测试互补）。"""

    def test_all_cases_have_route_result(self, replay_rows):
        """320 条全部产出路由结果，无异常占位。"""
        errors = [r["case"].id for r in replay_rows if r["pred"]["intent"].startswith("<error:")]
        assert errors == [], f"回放异常用例：{errors[:10]}"

    def test_target_map_covers_all_route_paths(self, replay_rows):
        """TARGET_MAP 覆盖 CSRoutePath 全部 5 值——新路由路径须显式映射。"""
        from backend.customer_service.router.types import CSRoutePath

        unmapped = [p.value for p in CSRoutePath if p.value not in TARGET_MAP]
        assert unmapped == [], f"CSRoutePath 新增值未映射 target：{unmapped}"

    def test_offline_skip_scope_is_explicit(self, replay_rows):
        """离线 skip 显式计数（防静默丢断言）：multi_turn 整类、safety 的 risk。

        类目条数变化时此断言失败——提示同步审视 skip 口径与门禁分母，
        而不是静默改变回放范围。
        """
        cats = Counter(r["case"].metadata.get("category", "v1") for r in replay_rows)
        assert cats["multi_turn"] == 40, (
            f"multi_turn 条数 {cats['multi_turn']} ≠ 40——离线 skip 口径需同步审视"
        )
        assert cats["safety"] == 40, (
            f"safety 条数 {cats['safety']} ≠ 40——risk 维度 skip 口径需同步审视"
        )
        it_total, _, _, _ = _hit_stats(replay_rows, "intent")
        rk_total, _, _, _ = _hit_stats(replay_rows, "risk")
        assert it_total == 284, f"intent/target 门禁分母 {it_total} ≠ 284（324−multi_turn）"
        assert rk_total == 244, f"risk 门禁分母 {rk_total} ≠ 244（324−multi_turn−safety）"
