"""tests/tools/test_data_collection_tool.py — Data Collection Tool 测试套件

覆盖：
1. Registry 注册验证
2. 参数校验（空/空白/超长/Unicode source）
3. 简写路径转换（static://datasets/{source}.json）
4. fetcher_type / write_mode / enable_analysis / dedup_keys / groupby_keys 参数行为
5. Markdown 报告输出（统计摘要 / 分组聚合 / 失败收口）
6. 错误处理（数据集不存在 → failed 报告而非抛异常）

写库用例统一走 test_dc_* 探针表（审批门由 conftest _auto_approve_tools 放行）。
"""
import pytest
from unittest.mock import MagicMock, patch


class TestDataCollectionToolRegistry:
    """Data Collection Tool 注册中心测试"""

    def test_tool_registered_in_registry(self):
        """verify data_collection_tool is registered"""
        from backend.tools.tool_registry import tool_registry

        assert 'data_collection_tool' in tool_registry.tool_names

    def test_no_duplicate_definition(self):
        """verify no duplicate definition exists"""
        from backend.tools.tool_registry import tool_registry

        duplicates = tool_registry.check_duplicates()
        assert 'data_collection_tool' not in duplicates, \
            f"检测到重复定义：{duplicates}"

    def test_tool_registered_once(self):
        """verify tool registered exactly once"""
        from backend.tools.tool_registry import tool_registry

        sources = tool_registry._tool_sources.get('data_collection_tool', [])
        assert len(sources) == 1, f"data_collection_tool 定义了 {len(sources)} 次"


class TestDataCollectionToolBasic:
    """data_collection_tool 基础功能测试"""

    def test_invoke_method_exists(self):
        """verify invoke method is available"""
        from backend.tools.data_collection import data_collection_tool

        assert hasattr(data_collection_tool, 'invoke')
        assert callable(data_collection_tool.invoke)

    def test_tool_name_correct(self):
        """verify tool name"""
        from backend.tools.data_collection import data_collection_tool

        assert data_collection_tool.name == 'data_collection_tool'

    def test_has_docstring(self):
        """verify tool has proper documentation"""
        from backend.tools.data_collection import data_collection_tool

        assert hasattr(data_collection_tool, '__doc__')
        # LangChain Tools 会有简短的 __doc__
        assert data_collection_tool.__doc__ is not None

    def test_args_schema_exposes_core_parameters(self):
        """args_schema 必须暴露核心参数（LLM 依赖 schema 传参）"""
        from backend.tools.data_collection import data_collection_tool

        props = data_collection_tool.args
        for key in ("source", "fetcher_type", "write_mode",
                    "dedup_keys", "groupby_keys", "enable_analysis"):
            assert key in props, f"args_schema 缺参数 {key}"


class TestSourceParameterValidation:
    """source 参数验证测试"""

    def test_empty_source_returns_error(self):
        """empty source should return error message"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({})

        assert isinstance(result, str)
        assert "错误" in result or "error" in result.lower()

    def test_shorthand_source_expanded_to_static_dataset(self):
        """简写 source 自动补全为 static://datasets/{source}.json 并取到数据"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "products"})

        assert isinstance(result, str)
        assert "数据采集报告" in result
        assert "**success**" in result

    def test_full_static_url_preserved(self):
        """完整 static:// URL 不被二次包装，直接命中数据集"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke(
            {"source": "static://datasets/products.json"})

        assert isinstance(result, str)
        assert "数据采集报告" in result
        assert "**success**" in result

    def test_http_url_passed_to_fetcher_unchanged(self):
        """http(s) URL 原样传给 HttpFetcher，不被简写包装改写"""
        from backend.data_collection.fetchers.base import RawData
        from backend.data_collection.fetchers.http_fetcher import HttpFetcher
        from backend.tools.data_collection import data_collection_tool
        import json as _json
        import time as _time

        raw_url = "http://localhost:8001/mock/products"
        mock_records = [{"SKU": "TEST-001", "售价": 9.9, "平台": "京东"}]
        mock_raw = RawData(
            source=raw_url,
            format="json",
            content=_json.dumps(mock_records, ensure_ascii=False),
            metadata={"fetcher": "http", "status_code": 200, "fetched_at": _time.time()},
        )

        with patch.object(HttpFetcher, 'fetch', return_value=mock_raw) as mock_fetch:
            result = data_collection_tool.invoke(
                {"source": raw_url, "fetcher_type": "http"})

        assert isinstance(result, str)
        assert "数据采集报告" in result
        # fetch 必须收到未改写的原始 URL
        assert mock_fetch.call_args is not None
        assert raw_url in str(mock_fetch.call_args.args)


class TestFetcherTypeValidation:
    """fetcher_type 参数行为测试"""

    def test_invalid_fetcher_falls_back_to_static(self):
        """未识别的 fetcher_type 走 static 分支（_build_pipeline 仅识别 http）"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke(
            {"source": "products", "fetcher_type": "bogus"})

        assert isinstance(result, str)
        assert "**success**" in result


class TestWriteModeValidation:
    """write_mode 参数行为测试"""

    @pytest.mark.parametrize("mode", ["append", "replace", "upsert"])
    def test_valid_write_modes_complete_collection(self, mode):
        """三种合法 write_mode 均能完成采集收口（探针表隔离写入）"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({
            "source": "products",
            "target_table": "test_dc_write_mode_probe",
            "write_mode": mode,
        })

        assert isinstance(result, str)
        assert "数据采集报告" in result
        assert "**success**" in result


class TestAnalysisConfigValidation:
    """enable_analysis / groupby_keys / dedup_keys 参数行为测试"""

    def test_analysis_enabled_by_default(self):
        """默认（不传 enable_analysis）产出数值统计段"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "products"})

        assert isinstance(result, str)
        assert "**success**" in result
        assert "### 📊 数值字段统计" in result

    def test_analysis_disabled_omits_statistics(self):
        """enable_analysis=False 报告不含统计段"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke(
            {"source": "products", "enable_analysis": False})

        assert isinstance(result, str)
        assert "**success**" in result
        assert "📊" not in result

    def test_groupby_keys_produce_aggregation_section(self):
        """groupby_keys 指向真实字段时报告含分组聚合段"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({
            "source": "products",
            "groupby_keys": "平台,品类",
            "enable_analysis": True,
        })

        assert isinstance(result, str)
        assert "**success**" in result
        assert "### 📈 分组聚合结果" in result

    def test_dedup_keys_accepted_and_collection_succeeds(self):
        """dedup_keys 传入真实字段后采集正常收口（解析在生产 clean 阶段）"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({
            "source": "products",
            "dedup_keys": "sku,平台",
            "target_table": "test_dc_dedup_probe",
        })

        assert isinstance(result, str)
        assert "数据采集报告" in result
        assert "**success**" in result


class TestDataCollectionErrorHandling:
    """数据收集错误处理测试"""

    def test_missing_source_parameter(self):
        """missing source parameter returns error"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({})

        assert isinstance(result, str)
        assert ("错误" in result or
                "error" in result.lower() or
                "缺少" in result)

    def test_unknown_protocol_source_fails_gracefully(self):
        """未识别协议被包装成数据集路径后文件不存在 → failed 报告而非抛异常"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "bogus://whatever"})

        assert isinstance(result, str)
        assert "**failed**" in result
        assert "数据集文件不存在" in result

    def test_static_fetcher_with_missing_file(self):
        """静态文件不存在应返回友好错误"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({
            "source": "static://datasets/non_existent_file.json",
            "fetcher_type": "static"
        })

        assert isinstance(result, str)
        # Pipeline 捕获异常后返回 failed 状态报告，而非抛出异常
        assert "**failed**" in result
        assert "数据集文件不存在" in result


class TestDataCollectionEdgeCases:
    """边界条件测试（全部真实调用工具，验证异常输入优雅收口）"""

    def test_whitespace_source_fails_gracefully(self):
        """空白 source（真值）走数据集路径 → 文件不存在 → failed 报告"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "   "})

        assert isinstance(result, str)
        assert "**failed**" in result

    def test_unicode_source_fails_gracefully(self):
        """中文 source 同样走包装 → 不存在 → failed，不炸不挂"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "数据集中文"})

        assert isinstance(result, str)
        assert "**failed**" in result

    def test_large_string_source_fails_gracefully(self):
        """超长 source 不造成挂起/崩溃，优雅失败"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({"source": "a" * 10000})

        assert isinstance(result, str)
        assert "**failed**" in result


class TestDataCollectionIntegration:
    """数据收集集成测试（需环境）"""

    def test_real_products_dataset_collection(self):
        """test collection from products dataset file"""
        from backend.tools.data_collection import data_collection_tool

        result = data_collection_tool.invoke({
            "source": "products",
            "target_table": "test_stg_products",
            "enable_analysis": True,
        })

        assert isinstance(result, str)
        # 数据集文件存在，应成功产出采集报告
        assert "数据采集报告" in result
        assert "**success**" in result

    def test_http_api_source_collection(self):
        """test collection from HTTP API endpoint (mocked)"""
        from backend.data_collection.fetchers.base import RawData
        from backend.data_collection.fetchers.http_fetcher import HttpFetcher
        from backend.tools.data_collection import data_collection_tool
        import json as _json
        import time as _time

        mock_records = [{"SKU": "TEST-001", "售价": 9.9, "平台": "京东"}]
        mock_raw = RawData(
            source="http://localhost:8001/mock/products",
            format="json",
            content=_json.dumps(mock_records, ensure_ascii=False),
            metadata={"fetcher": "http", "status_code": 200, "fetched_at": _time.time()},
        )

        # 用 Mock 替代真实网络请求，验证 http fetcher 路径可用
        with patch.object(HttpFetcher, 'fetch', return_value=mock_raw):
            result = data_collection_tool.invoke({
                "source": "http://localhost:8001/mock/products",
                "fetcher_type": "http"
            })

        assert isinstance(result, str)
        assert "数据采集报告" in result


# ==================== 测试套件入口 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
