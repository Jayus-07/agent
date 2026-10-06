# -*- coding: utf-8 -*-
"""观测重构（2026-10-06）新指标回归测试。

覆盖：
- record_rag_stage：软失败 + 正常 observe（rag_stage_duration_seconds）
- agent_stage_total / agent_stage_duration_seconds：travel _evented_node 接线
- prometheus 全局 registry 收录（multiprocess 前提下直接查 registry 对象）
"""
import pytest

from backend.observability.metrics import (
    agent_stage_duration_seconds,
    agent_stage_total,
    rag_stage_duration_seconds,
    record_rag_stage,
)


def _hist_samples(hist, **labels):
    """读取直方图 _count 样本（含 label 过滤）。"""
    found = 0
    for metric in hist.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count") and all(
                sample.labels.get(k) == v for k, v in labels.items()
            ):
                found += int(sample.value)
    return found


class TestRecordRagStage:
    def test_observe_increments_count(self):
        before = _hist_samples(rag_stage_duration_seconds, stage="retrieve")
        record_rag_stage("retrieve", 1234.0)
        after = _hist_samples(rag_stage_duration_seconds, stage="retrieve")
        assert after == before + 1

    def test_min_clamp_1ms(self):
        """0/负值被钳到 1ms，不抛错（浮点计时下溢兜底）。"""
        record_rag_stage("generate", 0.0)
        assert _hist_samples(rag_stage_duration_seconds, stage="generate") >= 1

    def test_fixed_enums_present(self):
        """四个固定 stage 枚举都可写（Grafana 面板按此四值聚合）。"""
        for stage in ("retrieve", "rerank", "generate", "total"):
            record_rag_stage(stage, 5.0)
            assert _hist_samples(rag_stage_duration_seconds, stage=stage) >= 1


class TestAgentStageMetrics:
    def test_evented_node_records_success(self):
        from backend.travel.graph_builder import _evented_node

        def _probe_node(state):  # noqa: ANN001
            return {"ok": True}

        before = _hist_samples(agent_stage_duration_seconds,
                               domain="travel", stage="obs_probe_node")
        wrapped = _evented_node("obs_probe_node", _probe_node)
        assert wrapped({}) == {"ok": True}
        after = _hist_samples(agent_stage_duration_seconds,
                              domain="travel", stage="obs_probe_node")
        assert after == before + 1

        calls = 0
        for metric in agent_stage_total.collect():
            for sample in metric.samples:
                if all(sample.labels.get(k) == v for k, v in
                       {"domain": "travel", "stage": "obs_probe_node",
                        "status": "success"}.items()):
                    calls += int(sample.value)
        assert calls >= 1

    def test_evented_node_records_failure_and_reraises(self):
        from backend.travel.graph_builder import _evented_node

        def _boom(state):  # noqa: ANN001
            raise RuntimeError("probe")

        wrapped = _evented_node("obs_probe_fail", _boom)
        with pytest.raises(RuntimeError):
            wrapped({})
        calls = 0
        for metric in agent_stage_total.collect():
            for sample in metric.samples:
                if all(sample.labels.get(k) == v for k, v in
                       {"domain": "travel", "stage": "obs_probe_fail",
                        "status": "failed"}.items()):
                    calls += int(sample.value)
        assert calls >= 1
