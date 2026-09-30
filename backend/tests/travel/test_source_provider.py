"""tests/travel/test_source_provider.py — 「来源性质」判定收口（结构病审查 P3-1）

病史：`poi.source` 的「提供方」段被判定四遍且口径不一 —— 三处用
``str.startswith("seed")``（poi_service / risk_service / validator），
reporter 的 ``describe_source`` 用的却是按 ``:`` 切分。任一处改名即静默落错分支：

  · 证据层（poi_service）：判错 → **SEED 示例数据被标成 LIVE** 并写上
    ``verified_at=observed_at``，等于把本地示例冒充为实时核实事实；
  · 风险层（risk_service）：判错 → 用户看不到「这是本地示例数据」的免责声明；
  · 校验层（validator）：判错 → 可信度分数不再扣减（示例数据拿满分）。

本文件把判定收口到 ``models/poi.is_seed_source``（唯一一份），并钉住
``startswith`` 判错的那个边界（``seeded:cache``）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.travel.models.poi import (
    SOURCE_PROVIDER_SEED,
    is_seed_source,
    source_provider,
)


class _Poi:
    def __init__(self, source: str):
        self.source = source


class _Itinerary:
    def __init__(self, sources):
        self._sources = list(sources)

    def all_pois(self):
        return [_Poi(s) for s in self._sources]


class TestSourceProvider:
    @pytest.mark.parametrize(
        "raw,expected",
        [("seed:local", "seed"), ("tencent:lbs", "tencent"),
         ("estimate:local", "estimate"), ("rag:travel", "rag"),
         ("seed", "seed"), ("", ""), (None, "")],
    )
    def test_provider_segment(self, raw, expected):
        assert source_provider(raw) == expected

    def test_seed_token_literal(self):
        """锁字面量：提供方段的种子标识就是 "seed"。"""
        assert SOURCE_PROVIDER_SEED == "seed"

    @pytest.mark.parametrize("raw", ["seed:local", "seed"])
    def test_seed_sources(self, raw):
        assert is_seed_source(raw) is True

    @pytest.mark.parametrize("raw", ["seeded:cache", "seedcache", "tencent:lbs",
                                     "estimate:local", "", None])
    def test_non_seed_sources(self, raw):
        """`seeded:cache` / `seedcache` 这一组就是 startswith 判错的那批。

        「以 seed 开头」不等于「提供方是 seed」——这正是当初三处
        ``startswith("seed")`` 会静默误判成种子数据的边界。
        """
        assert is_seed_source(raw) is False


class TestRiskServiceUsesProviderSegment:
    def test_seed_source_emits_local_sample_warning(self):
        from backend.travel.services.risk_service import assess_risks

        warnings, sources = assess_risks(_Itinerary(["seed:local"]))
        assert sources == ["seed:local"]
        assert any("本地示例数据" in w for w in warnings)

    def test_seed_prefixed_non_seed_source_does_not_warn(self):
        """`seeded:cache` 不是种子数据 → 不该出现「本地示例数据」免责声明。"""
        from backend.travel.services.risk_service import assess_risks

        warnings, _sources = assess_risks(_Itinerary(["seeded:cache"]))
        assert not any("本地示例数据" in w for w in warnings)

    def test_live_source_does_not_warn(self):
        from backend.travel.services.risk_service import assess_risks

        warnings, _sources = assess_risks(_Itinerary(["tencent:lbs"]))
        assert not any("本地示例数据" in w for w in warnings)


class TestNoStartswithRegression:
    """防回退：三处消费方都必须走唯一判定，不得再写 startswith。

    AST 判定而非扫文本 —— 只在真实调用上判定，注释里提旧写法不算
    （否则守护会被自己写的病史注释咬红）。
    """

    @staticmethod
    def _startswith_calls(src: str) -> list[tuple[int, str]]:
        tree = ast.parse(src)
        found = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "startswith"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.append((node.lineno, node.args[0].value))
        return found

    @pytest.mark.parametrize(
        "rel",
        ["services/poi_service.py", "services/risk_service.py", "validator.py"],
    )
    def test_consumer_uses_is_seed_source(self, rel):
        src = (Path(__file__).resolve().parents[2]
               / "travel" / rel).read_text(encoding="utf-8")
        offenders = [ln for ln, prefix in self._startswith_calls(src)
                     if prefix.startswith("seed")]
        assert not offenders, (
            f"travel/{rel} 回退到前缀判定（行 {offenders}），应用 is_seed_source"
        )
        assert "is_seed_source" in src


class TestReporterSharesTheSameParsing:
    def test_describe_source_uses_shared_helper(self):
        """reporter 的来源描述走同一个提供方段解析，不再自写一套 split(":")。"""
        src = (Path(__file__).resolve().parents[2]
               / "travel" / "reporter.py").read_text(encoding="utf-8")
        assert 'split(":")[0]' not in src
        assert "source_provider" in src

    def test_describe_source_still_resolves_known_sources(self):
        from backend.travel.reporter import describe_source

        assert "本地示例数据" in describe_source("seed:local")
        assert "腾讯位置服务" in describe_source("tencent:lbs")
        assert "未登记" in describe_source("no_such_provider:x")
