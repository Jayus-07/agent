"""market_research Workflow 冒烟测试（mock 搜索/抓取工具 + mock LLM）

覆盖：
1. DAG 五层结构（collect → normalize → 3 组并行 → fact_lock → report）
2. happy path：全链路跑通，fact_lock 把坏引用降级为推断，报告含 Source Index
3. abort 语义：证据 < MIN_EVIDENCE → fail-fast，不产残缺报告
4. LLM 失败降级：组分析降级为骨架，整体报告仍产出
"""
import asyncio
import json
import re

import pytest

from backend.orchestration.workflow.dag import DAG
from backend.orchestration.workflow.executor import WorkflowExecutor
from backend.orchestration.workflow.meta import collect_step_methods
from backend.orchestration.workflow.registry import WorkflowRegistry
from backend.orchestration.workflows.market_research import MIN_EVIDENCE, MarketResearch
from backend.market_research.pipeline import (
    extract_numbers,
    lock_facts,
    normalize_evidence,
    parse_search_results,
)

# ── 纯函数层单测 ────────────────────────────────

SEARCH_MD = "\n\n".join(
    f"{i + 1}. **报告{i}：蓝牙耳机市场分析**\n   2026年市场规模1200亿，增速18%\n   "
    f"https://example.com/r{i}" for i in range(3))


def test_parse_search_results():
    out = parse_search_results(SEARCH_MD)
    assert len(out) == 3
    assert out[0]["title"] == "报告0：蓝牙耳机市场分析"
    assert out[0]["url"] == "https://example.com/r0"
    assert "1200亿" in out[0]["snippet"]
    assert parse_search_results("[NO RESULTS] 未找到 'x' 的相关结果") == []


def test_extract_numbers_and_classify():
    nums = extract_numbers("市场规模1200亿元，同比18%，均价299元，2026年发布")
    assert "1200亿元" in nums and "18%" in nums and "299元" in nums
    assert "2026" not in nums  # 纯年份跳过
    assert normalize_evidence([{ "url": "https://www.stats.gov.cn/a",
                                 "title": "t", "content": "规模100亿"}], "2026-09-15T00:00:00"
                              )[0]["source_type"] == "official"


def test_lock_facts_downgrades_bad_references():
    evidence = {"EV-00000001": {"evidence_id": "EV-00000001", "source_type": "official",
                                "numbers": ["1200亿"]}}
    groups_out = {"group_a": {"sections": [{"title": "s", "content": "c", "claims": [
        {"claim": "规模1200亿", "type": "fact", "evidence_ids": ["EV-00000001"],
         "numbers": ["1200亿"]},
        {"claim": "份额95%", "type": "fact", "evidence_ids": ["EV-deadbeef"],
         "numbers": ["95%"]},
    ]}]}}
    locked, downgrades = lock_facts(groups_out, evidence)
    claims = locked["group_a"]["sections"][0]["claims"]
    assert claims[0]["type"] == "fact" and claims[0]["confidence"] == "高"
    assert claims[1]["type"] == "inference"  # 引用不存在 → 降级
    assert len(downgrades) == 1
    # 数字无证据支撑也降级
    groups_out["group_a"]["sections"][0]["claims"][0]["numbers"] = ["9999亿"]
    locked, downgrades = lock_facts(groups_out, evidence)
    assert locked["group_a"]["sections"][0]["claims"][0]["type"] == "inference"


# ── Workflow 层（mock 工具 + LLM）────────────────

FAKE_SEARCH_MD = "\n\n".join(
    f"{i + 1}. **{['行业报告', '竞品榜单', '用户调研', '价格分析', '趋势观察', '渠道盘点'][i]}"
    f"：蓝牙耳机专题**\n   市场规模1200亿 增速18%\n   https://example.com/r{i}"
    for i in range(6))
FAKE_CRAWL = ("蓝牙耳机市场规模1200亿元，同比增长18%。头部品牌份额：A品牌25%，B品牌18%。"
              "用户痛点：续航虚标、佩戴不适。均价299元，主要渠道为电商平台。")


class _FakeTool:
    def __init__(self, fn):
        self._fn = fn

    def invoke(self, args: dict):
        return self._fn(**args)


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _fake_llm_invoke(messages):
    text = " ".join(str(getattr(m, "content", m)) for m in messages)
    if "品类市场调研分析师" in text:
        ids = re.findall(r"EV-[0-9a-f]{8}", text)
        good_id = ids[0] if ids else "EV-00000000"
        return _FakeResp(json.dumps({"sections": [
            {"title": "市场规模与增速",
             "content": "2026年市场规模1200亿元，同比增长18%。",
             "claims": [
                 {"claim": "市场规模1200亿元", "type": "fact",
                  "evidence_ids": [good_id], "numbers": ["1200亿元"]},
                 {"claim": "某品牌份额95%", "type": "fact",
                  "evidence_ids": ["EV-deadbeef"], "numbers": ["95%"]},
             ]},
        ]}, ensure_ascii=False))
    return _FakeResp("not-json-at-all")


@pytest.fixture
def patched_env(monkeypatch, tmp_path, patched_persistence, patched_trace_collector):
    import backend.orchestration.workflows.market_research as wf_mod

    monkeypatch.setattr(wf_mod, "web_search_tool",
                        _FakeTool(lambda query, num_results=4: FAKE_SEARCH_MD))
    monkeypatch.setattr(wf_mod, "web_crawl_tool",
                        _FakeTool(lambda url, mode="markdown": FAKE_CRAWL))
    monkeypatch.setattr(wf_mod, "llm",
                        type("L", (), {"invoke": staticmethod(_fake_llm_invoke)}))
    from backend.market_research.store import MarketResearchStore
    store = MarketResearchStore(db_path=str(tmp_path / "mr.db"))
    monkeypatch.setattr(wf_mod, "get_market_research_store", lambda: store)
    return store


def test_dag_layers_structure():
    steps = collect_step_methods(MarketResearch)
    dag = DAG({name: cfg for name, (_, cfg) in steps.items()})
    layers = dag.layers
    assert layers[0] == ["collect"]
    assert layers[1] == ["normalize"]
    assert set(layers[2]) == {"group_a", "group_b", "group_c"}
    assert layers[3] == ["fact_lock"]
    assert layers[-1] == ["report"]


def test_full_run_happy_path(patched_env):
    reg = WorkflowRegistry()
    reg.register(MarketResearch)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run(
        "market_research", inputs={"category": "蓝牙耳机", "task_id": "t-happy"}))
    assert ctx.status == "success", ctx.error
    rep = ctx.outputs["report"]
    assert rep["evidence_count"] >= MIN_EVIDENCE
    md = rep["report_md"]
    assert "## 12. 进入建议" in md and "## Source Index" in md
    assert "市场规模1200亿元" in md  # 高置信事实结论进执行摘要
    assert "推断" in md  # 坏引用（EV-deadbeef）被 fact_lock 降级
    row = patched_env.get("t-happy")
    assert row["status"] == "success" and row["report_md"] == md
    # 证据落库
    assert len(patched_env.list_evidence("t-happy")) == rep["evidence_count"]


def test_collect_abort_when_no_evidence(patched_env, monkeypatch):
    import backend.orchestration.workflows.market_research as wf_mod
    monkeypatch.setattr(wf_mod, "web_search_tool",
                        _FakeTool(lambda query, num_results=4: "[NO RESULTS] 未找到"))
    reg = WorkflowRegistry()
    reg.register(MarketResearch)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run(
        "market_research", inputs={"category": "小众品类", "task_id": "t-abort"}))
    assert ctx.status == "failed"
    assert "category" in ctx.error or "证据" in ctx.error or "搜索" in ctx.error


def test_llm_failure_degrades_to_skeleton(patched_env, monkeypatch):
    import backend.orchestration.workflows.market_research as wf_mod
    monkeypatch.setattr(wf_mod, "llm",
                        type("L", (), {"invoke": staticmethod(
                            lambda ms: _FakeResp("not-json-at-all"))}))
    reg = WorkflowRegistry()
    reg.register(MarketResearch)
    ctx = asyncio.run(WorkflowExecutor(registry=reg).run(
        "market_research", inputs={"category": "蓝牙耳机", "task_id": "t-degrade"}))
    assert ctx.status == "success"  # 降级不中止整体报告
    md = ctx.outputs["report"]["report_md"]
    assert "模板骨架" in md
    assert ctx.outputs["report"]["claim_count"] == 0
