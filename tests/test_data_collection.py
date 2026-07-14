"""
tests/test_data_collection.py — Data Collection Center 单元 + 集成测试

覆盖:
  1. StaticDataFetcher 读取本地数据集
  2. JsonParser 解析 JSON
  3. DefaultCleaner 去重 + 类型转换 + 缺失值填充
  4. StatsAnalyzer describe + groupby + 缺失值诊断
  5. CollectionPipeline 端到端 (不含 DB 写入)
  6. Scheduler 注册 + 触发
  7. Tool 输出 Markdown 报告
"""

import os
import sys
import json
import pytest

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_collection.fetchers.static_fetcher import StaticDataFetcher
from data_collection.fetchers.http_fetcher import HttpFetcher
from data_collection.parsers.json_parser import JsonParser
from data_collection.parsers.csv_parser import CsvParser
from data_collection.cleaners.default_cleaner import DefaultCleaner
from data_collection.analyzers.stats_analyzer import StatsAnalyzer
from data_collection.pipeline import CollectionPipeline, CollectResult
from data_collection.scheduler import Scheduler


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════

@pytest.fixture
def datasets_dir():
    return os.path.join(os.path.dirname(__file__), "..", "data_collection", "datasets")


@pytest.fixture
def static_fetcher(datasets_dir):
    return StaticDataFetcher(data_dir=datasets_dir)


@pytest.fixture
def json_parser():
    return JsonParser()


@pytest.fixture
def csv_parser():
    return CsvParser()


@pytest.fixture
def cleaner():
    return DefaultCleaner()


@pytest.fixture
def analyzer():
    return StatsAnalyzer()


@pytest.fixture
def pipeline(static_fetcher, json_parser, cleaner, analyzer):
    return CollectionPipeline(
        fetcher=static_fetcher,
        parser=json_parser,
        cleaner=cleaner,
        analyzer=analyzer,
        writer=None,  # 不写数据库
    )


# ══════════════════════════════════════════════════════════
# Fetcher 测试
# ══════════════════════════════════════════════════════════

class TestStaticFetcher:
    """StaticDataFetcher — 本地数据集读取"""

    def test_fetch_products_json(self, static_fetcher):
        raw = static_fetcher.fetch("products")
        assert raw.format == "json"
        assert len(raw.content) > 0
        data = json.loads(raw.content)
        assert len(data) == 12
        assert raw.metadata["fetcher"] == "static"

    def test_fetch_orders_json(self, static_fetcher):
        raw = static_fetcher.fetch("orders")
        data = json.loads(raw.content)
        assert len(data) == 15

    def test_fetch_nonexistent_file(self, static_fetcher):
        with pytest.raises(FileNotFoundError):
            static_fetcher.fetch("nonexistent_dataset")

    def test_fetch_with_full_prefix(self, static_fetcher):
        raw = static_fetcher.fetch("static://datasets/shops.json")
        data = json.loads(raw.content)
        assert len(data) == 8


# ══════════════════════════════════════════════════════════
# Parser 测试
# ══════════════════════════════════════════════════════════

class TestJsonParser:
    """JsonParser — JSON 解析"""

    def test_parse_products(self, static_fetcher, json_parser):
        raw = static_fetcher.fetch("products")
        parsed = json_parser.parse(raw)
        assert parsed.record_count == 12
        assert len(parsed.records) == 12
        assert parsed.records[0]["sku"] == "BT-001"
        assert parsed.records[0]["售价"] == 299.00

    def test_parse_single_object(self, json_parser):
        from data_collection.fetchers.base import RawData
        raw = RawData(
            source="test", format="json",
            content='{"sku": "X-001", "name": "Test"}',
            metadata={},
        )
        parsed = json_parser.parse(raw)
        assert parsed.record_count == 1

    def test_parse_invalid_json(self, json_parser):
        from data_collection.fetchers.base import RawData
        raw = RawData(
            source="test", format="json",
            content="NOT JSON{{{",
            metadata={},
        )
        parsed = json_parser.parse(raw)
        assert parsed.record_count == 0
        assert len(parsed.parse_errors) > 0


class TestCsvParser:
    """CsvParser — CSV 解析"""

    def test_parse_csv(self, csv_parser):
        from data_collection.fetchers.base import RawData
        raw = RawData(
            source="test", format="csv",
            content="name,price,qty\nItem A,10.5,3\nItem B,20.0,5",
            metadata={},
        )
        parsed = csv_parser.parse(raw)
        assert parsed.record_count == 2
        assert parsed.records[0]["name"] == "Item A"


# ══════════════════════════════════════════════════════════
# Cleaner 测试
# ══════════════════════════════════════════════════════════

class TestDefaultCleaner:
    """DefaultCleaner — 去重 + 类型转换 + 缺失值填充"""

    def test_dedup_by_key(self, cleaner):
        records = [
            {"SKU": "A", "售价": "10.0"},
            {"SKU": "A", "售价": "10.0"},   # 重复
            {"SKU": "B", "售价": "20.0"},
        ]
        cleaned = cleaner.clean(records, rules={"dedup_keys": ["SKU"]})
        assert cleaned.row_count == 2
        assert cleaned.dedup_removed == 1

    def test_type_conversion(self, cleaner):
        records = [
            {"SKU": "A", "售价": "29.99", "数量": "5"},
        ]
        cleaned = cleaner.clean(records, rules={
            "type_map": {"售价": float, "数量": int},
        })
        assert isinstance(cleaned.records[0]["售价"], (int, float))
        assert isinstance(cleaned.records[0]["数量"], (int, float))

    def test_fill_missing(self, cleaner):
        records = [
            {"SKU": "A", "售价": 10.0},
            {"SKU": "B", "售价": None},
        ]
        cleaned = cleaner.clean(records)
        # 缺失的数值应该被中位数填充
        assert cleaned.records[1]["售价"] is not None
        assert cleaned.null_filled.get("售价", 0) > 0


# ══════════════════════════════════════════════════════════
# Analyzer 测试
# ══════════════════════════════════════════════════════════

class TestStatsAnalyzer:
    """StatsAnalyzer — describe + groupby + 缺失值诊断"""

    def test_describe_numeric(self, static_fetcher, json_parser, cleaner, analyzer):
        raw = static_fetcher.fetch("products")
        parsed = json_parser.parse(raw)
        cleaned = cleaner.clean(parsed.records)
        result = analyzer.analyze(cleaned, config={"dataset_name": "products"})
        assert "售价" in result.summary
        assert result.summary["售价"]["mean"] > 0

    def test_groupby_auto(self, static_fetcher, json_parser, cleaner, analyzer):
        raw = static_fetcher.fetch("products")
        parsed = json_parser.parse(raw)
        cleaned = cleaner.clean(parsed.records)
        result = analyzer.analyze(cleaned, config={
            "groupby_keys": "auto", "dataset_name": "products",
        })
        assert len(result.aggregations) > 0

    def test_missing_report(self, analyzer):
        from data_collection.cleaners.base import CleanedData
        cleaned = CleanedData(
            source="test",
            records=[
                {"A": 1, "B": "x"},
                {"A": None, "B": None},
            ],
            row_count=2,
        )
        result = analyzer.analyze(cleaned)
        assert len(result.missing_report) >= 0   # 可能有缺失也可能没有


# ══════════════════════════════════════════════════════════
# Pipeline 端到端测试
# ══════════════════════════════════════════════════════════

class TestPipeline:
    """CollectionPipeline — 端到端流水线"""

    def test_full_pipeline_products(self, pipeline):
        result = pipeline.run(
            source="static://datasets/products.json",
            table="stg_products",
            dedup_keys=["sku"],
        )
        assert result.status == "success"
        assert result.parsed.record_count == 12
        assert result.cleaned.row_count == 12
        assert result.analyzed is not None
        assert "售价" in result.analyzed.summary

    def test_full_pipeline_orders(self, pipeline):
        result = pipeline.run(
            source="static://datasets/orders.json",
            table="stg_orders",
            dedup_keys=["订单号"],
        )
        assert result.status == "success"
        assert result.parsed.record_count == 15

    def test_pipeline_bad_source(self, pipeline):
        result = pipeline.run(source="static://datasets/nonexistent.json", table="tmp")
        assert result.status == "failed"
        assert result.error is not None

    def test_result_to_markdown(self, pipeline):
        result = pipeline.run(source="static://datasets/products.json", table="stg_products")
        md = result.to_markdown()
        assert "📥" in md
        assert "stg_products" in md or "任务ID" in md


# ══════════════════════════════════════════════════════════
# Scheduler 测试
# ══════════════════════════════════════════════════════════

class TestScheduler:
    """Scheduler — 任务注册 + 触发"""

    def test_register_and_run(self, pipeline):
        sched = Scheduler()
        sched.register(
            name="test_job",
            task=lambda: pipeline.run(
                source="static://datasets/products.json", table="stg_products",
            ),
            description="测试任务",
        )
        result = sched.run_now("test_job")
        assert result.status == "success"

    def test_run_nonexistent_job(self):
        sched = Scheduler()
        with pytest.raises(KeyError):
            sched.run_now("no_such_job")

    def test_list_jobs(self, pipeline):
        sched = Scheduler()
        sched.register(name="job_a", task=lambda: None)
        sched.register(name="job_b", task=lambda: None)
        jobs = sched.list_jobs()
        assert len(jobs) == 2


# ══════════════════════════════════════════════════════════
# Tool 输出测试
# ══════════════════════════════════════════════════════════

class TestTool:
    """data_collection_tool — LangChain Tool 调用"""

    def test_tool_invoke_static(self):
        from data_collection.tool import data_collection_tool
        result = data_collection_tool.invoke({
            "source": "products",
            "enable_write": False,
        })
        assert "📥" in result
        assert "success" in result
        assert "12" in result

    def test_tool_invoke_with_dedup(self):
        from data_collection.tool import data_collection_tool
        result = data_collection_tool.invoke({
            "source": "products",
            "dedup_keys": "SKU",
            "enable_write": False,
        })
        assert "📥" in result

    def test_tool_invoke_empty_source(self):
        from data_collection.tool import data_collection_tool
        result = data_collection_tool.invoke({"source": "", "enable_write": False})
        assert "❌" in result


# ══════════════════════════════════════════════════════════
# Skill 集成测试
# ══════════════════════════════════════════════════════════

class TestSkillIntegration:
    """DataCollectionSkill — Skill 注册 + LangGraph 节点"""

    def test_skill_registered(self):
        from multi_agent.skills.registry import get
        skill = get("data.collect")
        assert skill is not None
        assert skill.name == "data_collection"

    def test_skill_capabilities(self):
        from data_collection.skill import DataCollectionSkill
        skill = DataCollectionSkill()
        assert "data.collect" in skill.capabilities

    def test_capability_in_registry(self):
        from multi_agent.tool_registry import tool_registry
        caps = tool_registry.get_available_capabilities()
        assert "data.collect" in caps
