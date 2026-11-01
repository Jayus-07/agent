# -*- coding: utf-8 -*-
"""CS 决策层离线回放门禁（cs-evaluation-upgrade P1，2026-11-02）。

将 320 条锁版断言从「存在性测试」（test_regression 只验能加载/能过 validate）
升级为真实执行：query -> CSRouter.route()（完整生产入口，零 LLM）
-> intent / route_path / risk_level，对照 expected.{intent,target,risk_level}。

数据集：backend/evaluation/datasets/cs/cases.jsonl（v2.0 锁版，统一 loader 加载，
manifest content_hash 在加载时强制校验——原地改即 fail-fast）。

基线（2026-11-01 首次全量回放；2026-11-02 修正 TARGET_MAP 后重测）：
  intent 40.6% / target 57.2% / risk 63.4%
  修正说明：探针初版把 route_path=human_handoff 映射为 cs_human，而数据集
  target 值域实为 cs_handoff（17 条），凡路由正确转人工的用例被系统性误判
  miss——TASK §2.1 预留的「complaint/human 映射待核对」结论即此。

阈值防退化不防历史：intent≥0.40 / target≥0.56 / risk≥0.60。
（target 阈值 0.56 对应修正后基线 57.2%，与 intent/risk 约 1pp 余量的
口径一致；TASK §4 初拟的 0.52 系按未修正基线计算。基线漂移需以
「阈值更新 + 依据」走 PR，不得静默放宽。）

口径注记：
- 多轮用例（40 条）按首轮回放——与锁定基线口径一致；末轮+上下文回放的
  口径修订属 cs-evaluation-upgrade P2 范畴。
- expected 缺失某维度断言时（v1 的 intent/risk_level）计命中——沿用基线
  探针口径，保证与 TASK §1.1 分类目数字可比。
- LLM 依赖维度（allowed/forbidden_facts、must_contain 等 live 断言）离线
  不执行；词表层回放由 cs-gate（test_cs_eval_gate）覆盖，本门禁只管
  决策层路由。
"""

from collections import Counter, defaultdict

import pytest

from backend.customer_service.router import cs_router as cs_router_module
from backend.customer_service.router.cs_router import get_cs_router
from backend.evaluation.dataset import load_dataset

# 防退化阈值（防退化不防历史），依据见模块 docstring
THRESHOLDS = {"intent": 0.40, "target": 0.56, "risk": 0.60}

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
    assert len(cases) == 320, (
        f"锁版 320 条，实际加载 {len(cases)}——数据集/manifest 漂移，"
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
    """按类目聚合单维度命中：返回 (total, hits, by_cat, misses)。"""
    by_cat: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for row in rows:
        case = row["case"]
        want = case.expected.get(_DIM_EXPECT_KEY[dim])
        got = row["pred"][dim]
        # expected 缺该维度断言（v1 的 intent/risk_level）计命中——基线口径
        hit = True if want is None else got == want
        cat = case.metadata.get("category", "v1")
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
