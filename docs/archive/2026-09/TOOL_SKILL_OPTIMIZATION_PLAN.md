# Tool/Skill 优化实施计划

> **版本**: v1.0  
> **创建时间**: 2026-09-02  
> **负责人**: AI Development Team  
> **优先级**: P0 (立即执行)

---

## 📋 目录

1. [P0 Bug 修复：SQL 重复定义](#第一部分p0-bug 修复-sql 重复定义)
2. [Week 1-2: 核心 Tool 单元测试覆盖](#第二部分核心 tool 单元测试覆盖计划week-1-2)
3. [Month 1: WebSearch/DataCollection Skill 升级](#第三部分 webservice/datacollection skill 升级计划month-1)
4. [长期治理：代码评审 Checklist](#第四部分 toolskill 代码评审 checklist 体系)
5. [时间表与里程碑](#时间表与里程碑)

---

## 第一部分：P0 Bug 修复（SQL 重复定义）

### 问题描述

**发现位置**: `backend/tools/sql.py` 第 72-94 行  
**问题类型**: 同名函数 `sql_query_tool` 重复定义 2 次  
**影响等级**: 🔴 P0 (可能导致运行时不可预测行为)

```python
# ❌ 当前错误代码（已存在）
@tool
def sql_query_tool(question: str) -> str:  # L72-L82
    """查询 PostgreSQL 数据库中的结构化数据。"""
    logger.info(f"[Tool:sql_query] 问题：{question[:80]}...")
    agent = _get_sql_agent()
    return agent.ask(question, current_user_id=None)

@tool
def sql_query_tool(question: str) -> str:  # L84-L94 (完全重复!)
    """查询 PostgreSQL 数据库中的结构化数据。"""
    logger.info(f"[Tool:sql_query] 问题：{question[:80]}...")
    agent = _get_sql_agent()
    return agent.ask(question, current_user_id=None)
```

---

### 修复方案：单一注册表模式

#### Step 1: 删除重复定义

**文件**: `backend/tools/sql.py`

```python
"""SQL 工具 — 自然语言查数据库 + 安全原始 SQL 执行。"""
from langchain_core.tools import tool
from backend.shared.logger import logger

# =====================================================
# 懒加载单例（首次调用时初始化，避免启动时全部加载）
# =====================================================
_sql_agent = None


def _get_sql_agent():
    global _sql_agent
    if _sql_agent is None:
        from backend.config import BUSINESS_DB_CONFIG
        from backend.sql.sql_agent import init_sql_agent
        _sql_agent = init_sql_agent(dict(BUSINESS_DB_CONFIG), max_retries=2)
    return _sql_agent


def _get_rag_pipeline():
    """获取 RAG Pipeline 单例（统一入口，避免双重初始化）"""
    from backend.app.api.deps import get_rag_pipeline
    return get_rag_pipeline()


# =====================================================
# Tool 定义（单一事实来源，已删除重复）
# =====================================================

@tool
def execute_sql_tool(query: str) -> str:
    """
    直接执行原始 SQL 查询 PostgreSQL。
    输入 SQL SELECT 语句，返回 JSON 格式的查询结果。
    适用场景：Workflow step 中的确定性数据拉取（不经过 NL→SQL Agent）。

    ⚠️ 安全：SQL 必须经过 validator 校验，只允许 SELECT/只读事务。
    """
    import json as _json
    import time
    from backend.sql.schema_loader import schema_loader
    from backend.sql.sql_validator import sql_validator

    logger.info(f"[Tool:execute_sql] {query[:80]}...")

    try:
        # P0 安全加固：先校验 SQL，再执行
        safe_sql, _, _ = sql_validator.validate(query)
        timeout = schema_loader.query_timeout

        from backend.sql.executor import execute_sql_struct
        result = execute_sql_struct(safe_sql, timeout=timeout)

        if result.status in ("success", "no_data"):
            logger.info(f"[Tool:execute_sql] 返回 {result.row_count} 行")
            return _json.dumps(
                {"rows": result.rows, "columns": result.columns, "total": result.row_count},
                ensure_ascii=False, default=str,
            )
        else:
            logger.error(f"[Tool:execute_sql] 失败：{result.status} - {result.error}")
            return _json.dumps(
                {"error": result.error, "status": result.status},
                ensure_ascii=False,
            )
    except Exception as e:
        logger.error(f"[Tool:execute_sql] 失败：{e}")
        raise


@tool
def sql_query_tool(question: str) -> str:
    """
    查询 PostgreSQL 数据库中的结构化数据。
    输入自然语言问题，返回 Markdown 格式的查询结果表格。
    适用场景：数据统计、排行、筛选、聚合、对比分析。
    """
    logger.info(f"[Tool:sql_query] 问题：{question[:80]}...")
    agent = _get_sql_agent()
    return agent.ask(question, current_user_id=None)

# ✅ 删除了第 84-94 行的重复定义
```

#### Step 2: 创建 Tool 注册中心（防重复检测）

**新建文件**: `backend/tools/tool_registry.py`

```python
"""tools/tool_registry.py — Tool 注册中心与去重验证

职责：
1. 检测 Tool 重复定义（P0 级防护）
2. 提供统一的 Tool 发现 API
3. 记录 Tool 元数据用于 Planner prompt
"""
import inspect
from typing import Dict, Set
from functools import cached_property

from backend.shared.logger import logger


class DuplicateToolError(Exception):
    """Tool 重复定义异常"""
    pass


class ToolRegistry:
    """Tool 注册表（自动派生自 LangChain 装饰器注册）"""
    
    def __init__(self):
        self._registered_tools: Dict[str, object] = {}
        self._tool_sources: Dict[str, list] = {}  # tool_name → [file_paths]
    
    def register(self, fn, source_file: str = ""):
        """注册单个 Tool，检测重复定义"""
        name = fn.name if hasattr(fn, 'name') else fn.__name__
        
        if name not in self._tool_sources:
            self._tool_sources[name] = []
        
        self._tool_sources[name].append(source_file)
        
        # P0 检查：同一函数在同一文件多次定义
        unique_sources = set(self._tool_sources[name])
        if len(unique_sources) == 1 and len(self._tool_sources[name]) > 1:
            raise DuplicateToolError(
                f"检测到 Tool '{name}' 在文件中重复定义！\n"
                f"文件：{source_file}\n"
                f"所有定义位置：{self._tool_sources[name]}"
            )
        
        self._registered_tools[name] = fn
        logger.info(f"[ToolRegistry] 注册 Tool: {name} @ {source_file}")
    
    @cached_property
    def available_tools(self) -> Dict[str, object]:
        """返回所有已注册 Tool"""
        return dict(self._registered_tools)
    
    @cached_property
    def tool_names(self) -> Set[str]:
        """返回所有 Tool 名称集合"""
        return set(self._registered_tools.keys())
    
    def get_tool(self, name: str):
        """根据名称获取 Tool"""
        return self._registered_tools.get(name)
    
    def check_duplicates(self) -> Dict[str, list]:
        """检测所有重复定义（供 CI 使用）"""
        duplicates = {
            name: sources for name, sources in self._tool_sources.items()
            if len(sources) > 1
        }
        return duplicates
    
    def get_schema(self) -> dict:
        """生成 Planner prompt 用的 Tool schema"""
        schema = {}
        for name, tool_fn in self._registered_tools.items():
            if hasattr(tool_fn, 'description'):
                schema[name] = {
                    "description": tool_fn.description,
                    "parameters": getattr(tool_fn, 'args', []),
                    "source_file": self._tool_sources.get(name, ["unknown"])[-1],
                }
        return schema


# 全局单例
tool_registry = ToolRegistry()


def register_tool(tool_fn, source_file: str = ""):
    """便捷注册函数（可手动调用）"""
    import inspect
    frame = inspect.currentframe()
    if frame and frame.f_back:
        source_file = frame.f_back.f_code.co_filename
    tool_registry.register(tool_fn, source_file)
```

#### Step 3: 集成到模块加载流程

**修改文件**: `backend/tools/__init__.py`

```python
"""tools — 统一 Tool 层（PR-2.x 从 orchestration/ + data_collection/ 收敛）。

LangChain Tool 封装，零侵入接入已有子系统。
Skill → Tool → Infrastructure (RAG / SQL / Report)

包含以下 Tools：
- sql.py:              execute_sql_tool, sql_query_tool (已修复重复定义)
- rag.py:              search_knowledge_tool
- report.py:           generate_report_tool, run_report
- web.py:              web_search_tool, web_crawl_tool
- email.py:            send_email_tool
- export.py:           export_csv_tool
- data_collection.py:  data_collection_tool
- competitor.py:       competitor_analyze_tool
- session.py:          set_session_id (contextvar 辅助工具)
"""
from backend.tools.sql import execute_sql_tool, sql_query_tool  # noqa: F401
from backend.tools.rag import search_knowledge_tool  # noqa: F401
from backend.tools.report import generate_report_tool, run_report  # noqa: F401
from backend.tools.export import export_csv_tool  # noqa: F401
from backend.tools.web import web_search_tool, web_crawl_tool  # noqa: F401
from backend.tools.email import send_email_tool  # noqa: F401
from backend.tools.data_collection import data_collection_tool  # noqa: F401
from backend.tools.competitor import competitor_analyze_tool  # noqa: F401
from backend.tools.session import set_session_id, _get_session_id  # noqa: F401

# 自动注册与重复检测
from backend.tools.tool_registry import tool_registry
import inspect
import backend.tools as tools_module

# 在模块加载时自动扫描注册
for module_name in dir(tools_module):
    if module_name.startswith('_'):
        continue
    module = getattr(tools_module, module_name)
    if not inspect.ismodule(module):
        continue
    if hasattr(module, '__file__'):
        source_file = module.__file__
        for fn_name, fn in inspect.getmembers(module, callable):
            # 检测 LangChain Tool 或自定义 Tool
            if hasattr(fn, '__langchain__') or hasattr(fn, 'name'):
                try:
                    tool_registry.register(fn, source_file)
                except DuplicateToolError as e:
                    logger.critical(f"[ToolRegistry] 启动终止：{e}")
                    raise SystemExit(1)

__all__ = [
    'execute_sql_tool',
    'sql_query_tool',
    'search_knowledge_tool',
    'generate_report_tool',
    'export_csv_tool',
    'web_search_tool',
    'web_crawl_tool',
    'send_email_tool',
    'data_collection_tool',
    'competitor_analyze_tool',
    'set_session_id',
    '_get_session_id',
    'tool_registry',
]
```

---

### 单元测试用例设计

**新建文件**: `backend/tests/tools/test_sql_tool.py`

```python
"""tests/tools/test_sql_tool.py — SQL Tool 测试套件

覆盖：
1. 重复定义检测（P0）
2. execute_sql_tool 功能测试
3. sql_query_tool 功能测试
4. 安全校验测试
5. 超时与重试模拟
"""
import pytest
import json
from unittest.mock import MagicMock, patch
from backend.tools.sql import execute_sql_tool, sql_query_tool
from backend.tools.tool_registry import tool_registry, DuplicateToolError


# ==================== 第一部分：重复定义检测测试 ====================

class TestSQLToolRegistry:
    """SQL Tool 注册中心测试"""
    
    def test_no_duplicate_definition(self):
        """[P0] verify no duplicate sql_query_tool definition"""
        duplicates = tool_registry.check_duplicates()
        assert 'sql_query_tool' not in duplicates, \
            f"检测到重复定义：{duplicates}"
    
    def test_sql_tool_registered_once(self):
        """verify each SQL tool registered exactly once"""
        assert 'execute_sql_tool' in tool_registry.tool_names
        assert 'sql_query_tool' in tool_registry.tool_names
        
        exec_sql_source = tool_registry._tool_sources.get('execute_sql_tool', [])
        sql_query_source = tool_registry._tool_sources.get('sql_query_tool', [])
        
        assert len(exec_sql_source) == 1, \
            f"execute_sql_tool 定义了 {len(exec_sql_source)} 次"
        assert len(sql_query_source) == 1, \
            f"sql_query_tool 定义了 {len(sql_query_source)} 次"
    
    def test_duplicate_registration_raises_error(self):
        """[P0] 二次注册同名 Tool 应抛出异常"""
        with pytest.raises(DuplicateToolError):
            tool_registry.register(sql_query_tool, "test/path.py")


# ==================== 第二部分：execute_sql_tool 功能测试 ====================

class TestExecuteSQLTool:
    """execute_sql_tool 功能测试"""
    
    def test_execute_sql_valid_query(self):
        """正常执行 SELECT 查询"""
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.rows = [{"id": 1, "name": "test"}]
        mock_result.columns = ["id", "name"]
        mock_result.row_count = 1
        
        with patch('backend.tools.sql.execute_sql_struct', return_value=mock_result):
            result = execute_sql_tool.invoke({"query": "SELECT * FROM products"})
            parsed = json.loads(result)
            
            assert parsed['status'] == 'success'
            assert parsed['rows'][0]['id'] == 1
            assert parsed['total'] == 1
    
    def test_execute_sql_syntax_error(self):
        """语法错误返回失败状态"""
        mock_result = MagicMock()
        mock_result.status = "error"
        mock_result.error = "syntax error near SELECT"
        
        with patch('backend.tools.sql.execute_sql_struct', return_value=mock_result):
            result = execute_sql_tool.invoke({"query": "SELEC * FROM table"})
            parsed = json.loads(result)
            
            assert parsed['status'] == 'error'
            assert 'syntax error' in parsed['error'].lower()
    
    def test_execute_sql_security_validation(self):
        """SQL 注入尝试应被拒绝"""
        malicious_query = "SELECT * FROM users; DROP TABLE products; --"
        
        with patch('backend.tools.sql.sql_validator.validate', 
                  side_effect=ValueError("Security violation")):
            with pytest.raises(ValueError, match="Security"):
                execute_sql_tool.invoke({"query": malicious_query})
    
    def test_execute_sql_timeout_handling(self):
        """超长查询应在超时后返回"""
        import time
        
        def slow_execute(*args, **kwargs):
            time.sleep(2)
            return MagicMock(status="timeout", rows=[], columns=[], row_count=0)
        
        with patch('backend.tools.sql.execute_sql_struct', side_effect=slow_execute):
            with patch('backend.tools.sql.schema_loader.query_timeout', 1):
                result = execute_sql_tool.invoke({"query": "SELECT * FROM large_table"})
                assert "timeout" in result.lower() or "error" in result.lower()


# ==================== 第三部分：sql_query_tool 功能测试 ====================

class TestSQLQueryTool:
    """sql_query_tool 功能测试"""
    
    def test_sql_query_natural_language(self):
        """自然语言转 SQL 并执行"""
        mock_response = """
| id | name | price |
|----|------|-------|
| 1  | Apple | 5.5  |
| 2  | Banana | 3.2  |
"""
        
        with patch('backend.tools.sql._get_sql_agent') as mock_getter:
            mock_agent = MagicMock()
            mock_agent.ask = MagicMock(return_value=mock_response)
            mock_getter.return_value = mock_agent
            
            result = sql_query_tool.invoke({
                "question": "查询所有价格超过 4 元的水果"
            })
            
            assert mock_agent.ask.called
            assert "Apple" in result
            assert "Banana" not in result
    
    def test_sql_query_empty_results(self):
        """无匹配结果返回空表格"""
        mock_response = "| 无匹配数据 |"
        
        with patch('backend.tools.sql._get_sql_agent') as mock_getter:
            mock_agent = MagicMock()
            mock_agent.ask = MagicMock(return_value=mock_response)
            mock_getter.return_value = mock_agent
            
            result = sql_query_tool.invoke({
                "question": "查询不存在的商品 XXXXXX"
            })
            
            assert "无匹配" in result or "no data" in result.lower()
    
    def test_sql_query_retry_behavior(self):
        """网络故障时应自动重试"""
        retry_count = 0
        
        def fail_twice_then_succeed(*args, **kwargs):
            nonlocal retry_count
            retry_count += 1
            if retry_count < 3:
                raise ConnectionError("Database connection lost")
            return "| id | name |\n|----|------|\n| 1 | Success |"
        
        with patch('backend.tools.sql._get_sql_agent') as mock_getter:
            mock_agent = MagicMock()
            mock_agent.ask = MagicMock(side_effect=fail_twice_then_succeed)
            mock_getter.return_value = mock_agent
            
            result = sql_query_tool.invoke({
                "question": "查询商品列表"
            })
            
            assert retry_count >= 3  # 至少重试 3 次才成功


# ==================== 第四部分：性能基准测试 ====================

class TestSQLToolPerformance:
    """SQL Tool 性能基准测试"""
    
    @pytest.mark.benchmark
    def test_execute_sql_small_dataset(self):
        """小数据集查询<100ms"""
        mock_result = MagicMock()
        mock_result.status = "success"
        mock_result.rows = [{f"k{i}": f"v{i}"} for i in range(10)]
        mock_result.columns = [f"k{i}" for i in range(10)]
        mock_result.row_count = 10
        
        with patch('backend.tools.sql.execute_sql_struct', return_value=mock_result):
            import time
            start = time.perf_counter()
            execute_sql_tool.invoke({"query": "SELECT * FROM small_table"})
            elapsed = time.perf_counter() - start
            
            assert elapsed < 0.1, f"查询耗时{elapsed:.2f}s, 超过 100ms 基线"
    
    @pytest.mark.benchmark
    def test_sql_query_medium_dataset(self):
        """中等数据集查询<500ms"""
        mock_response = "\n".join([
            "| id | name |",
            "|----|------|" + "---|" * 50,
        ] + [f"| {i} | Item {i} |" for i in range(100)])
        
        with patch('backend.tools.sql._get_sql_agent') as mock_getter:
            mock_agent = MagicMock()
            mock_agent.ask = MagicMock(return_value=mock_response)
            mock_getter.return_value = mock_agent
            
            import time
            start = time.perf_counter()
            sql_query_tool.invoke({"question": "查询前 100 个商品"})
            elapsed = time.perf_counter() - start
            
            assert elapsed < 0.5, f"查询耗时{elapsed:.2f}s, 超过 500ms 基线"
```

---

### 工时估算与验收标准

#### 工时分配

| 任务 | 详情 | 预计工时 |
|------|------|---------|
| Step 1 | 删除重复定义 | 0.5 小时 |
| Step 2 | 创建 Registry 验证器 | 4 小时 |
| Step 3 | 集成到__init__.py | 2 小时 |
| Step 4 | 编写单元测试（15 个用例） | 6 小时 |
| Step 5 | Code Review & 本地测试 | 2 小时 |
| **总计** | | **14.5 小时 ≈ 2 个工作日** |

#### 验收标准

✅ **必须全部通过:**

1. 运行以下命令无重复定义报错：
   ```bash
   pytest backend/tests/tools/test_sql_tool.py::TestSQLToolRegistry::test_no_duplicate_definition -v
   ```

2. 代码扫描工具未发现任何重复定义：
   ```bash
   python scripts/tool_quality_check.sh
   ```

3. 所有原有测试用例通过率保持 100%：
   ```bash
   pytest backend/tests/ -v --tb=short
   ```

4. SQL 工具功能回归测试全部通过：
   ```bash
   pytest backend/tests/tools/test_sql_tool.py -v
   ```

5. 性能基线未下降（±10% 浮动）：
   ```bash
   pytest backend/tests/tools/test_sql_tool.py::TestSQLToolPerformance -m benchmark
   ```

---

## 第二部分：核心 Tool 单元测试覆盖计划（Week 1-2）

### 优先级排序矩阵

基于三个维度评分（每维度 1-5 分）:
1. **高频调用**（系统日志统计过去 30 天调用次数）
2. **数据写入风险**（写入操作优先级高于只读）
3. **外部依赖强度**（网络请求/DB 连接等高风险操作）

| Tool | 高频调用 | 数据写入 | 外部依赖 | 总分 | 优先级 | Week |
|------|---------|---------|---------|------|--------|------|
| `data_collection_tool` | 5 | 5 | 5 | 15 | P0 | Week 1 |
| `execute_sql_tool` | 5 | 3 | 4 | 12 | P0 | Week 1 |
| `sql_query_tool` | 5 | 0 | 4 | 9 | P1 | Week 1 |
| `web_search_tool` | 4 | 0 | 5 | 9 | P1 | Week 1 |
| `web_crawl_tool` | 3 | 0 | 5 | 8 | P1 | Week 1 |
| `competitor_analysis_tool` | 2 | 0 | 4 | 6 | P2 | Week 2 |
| `send_email_tool` | 2 | 0 | 3 | 5 | P2 | Week 2 |
| `generate_report_tool` | 3 | 0 | 0 | 3 | P3 | Week 2 |
| `export_csv_tool` | 1 | 0 | 2 | 3 | P3 | Week 2 |

---

### 测试要点清单（按优先级展开）

#### P0 级：DataCollectionTool (25 个测试用例)

**文件**: `backend/tests/tools/test_data_collection_tool.py`

```python
"""tests/tools/test_data_collection_tool.py — DataCollection Tool 测试套件"""
import pytest
import tempfile
import json
from unittest.mock import MagicMock, patch
from backend.tools.data_collection import data_collection_tool


class TestDataCollectionToolInputValidation:
    """输入校验测试"""
    
    def test_missing_source_parameter(self):
        """缺少 source 参数应拒绝执行"""
        result = data_collection_tool.invoke({
            "target_table": "products"
        })
        assert "错误" in result or "source" in result.lower()
    
    def test_invalid_source_format(self):
        """无效源格式应拒绝"""
        with pytest.raises(ValueError):
            data_collection_tool.invoke({
                "source": "invalid://protocol",
                "fetcher_type": "http"
            })
    
    def test_valid_source_formats(self):
        """各种有效源格式都接受"""
        valid_sources = [
            "static://datasets/products.json",
            "http://localhost:8001/mock/products",
            "https://api.example.com/data",
            "products",  # 简写形式
        ]
        
        for source in valid_sources:
            with patch('backend.tools.data_collection._build_pipeline'):
                # 仅验证参数解析通过（不验证实际执行）
                try:
                    data_collection_tool.invoke({"source": source})
                except Exception as e:
                    # 其他异常可接受（如网络错误）
                    assert isinstance(e, (ValueError, ConnectionError))


class TestDataCollectionToolRetryAndTimeout:
    """重试与超时模拟测试"""
    
    def test_http_fetcher_retry_on_connection_error(self):
        """HTTP Fetcher 应在连接失败时重试"""
        call_count = 0
        
        def fail_twice_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Connection refused")
            return [{"id": 1}]
        
        with patch('backend.data_collection.fetchers.http_fetcher.HttpFetcher.fetch',
                  side_effect=fail_twice_then_succeed):
            with patch('backend.tools.data_collection.pipeline.run',
                      return_value=MagicMock(ok=True)):
                result = data_collection_tool.invoke({
                    "source": "http://localhost:8001/mock/products",
                    "fetcher_type": "http"
                })
                
                assert call_count >= 3, "应至少重试 3 次"
    
    def test_static_fetcher_timeout_handling(self):
        """静态文件读取超时处理"""
        import time
        
        def slow_file_read(*args, **kwargs):
            time.sleep(10)  # 人为慢速
            return [{"id": 1}]
        
        with patch('backend.data_collection.fetchers.static_fetcher.StaticDataFetcher.fetch',
                  side_effect=slow_file_read):
            with patch('backend.data_collection.config.DC_TIMEOUT', 1):
                with pytest.raises(Exception, match="timeout"):
                    data_collection_tool.invoke({
                        "source": "static://datasets/slow.json"
                    })


class TestDataCollectionToolIdempotency:
    """幂等性验证测试"""
    
    def test_same_input_same_output(self):
        """相同输入应产生相同输出（确定性）"""
        test_data = [{"id": 1, "name": "test", "value": 100}]
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_data, f)
            temp_path = f.name
        
        try:
            with patch('backend.data_collection.fetchers.static_fetcher.StaticDataFetcher.fetch',
                      return_value=test_data):
                results = [
                    data_collection_tool.invoke({
                        "source": f"static://{temp_path}",
                        "target_table": "test_table"
                    })
                    for _ in range(3)
                ]
            
            # 验证所有结果一致
            assert all(r == results[0] for r in results), "结果不一致"
        finally:
            import os
            os.unlink(temp_path)
```

**测试要点总结:**
- ✅ 输入校验：验证所有必填参数
- ✅ 重试机制：模拟网络抖动 3 次失败后成功
- ✅ 超时处理：人为慢速操作触发超时保护
- ✅ 幂等性：相同输入 3 次调用结果一致
- ✅ Mock 策略：隔离外部依赖（API/文件/数据库）

---

#### P1 级：WebSearchTool (12 个测试用例)

**文件**: `backend/tests/tools/test_web_search_tool.py` (核心用例)

```python
"""tests/tools/test_web_search_tool.py — Web Search Tool 测试套件"""
import pytest
from unittest.mock import MagicMock, patch
from backend.tools.web import web_search_tool


class TestWebSearchToolInputValidation:
    """输入校验测试"""
    
    def test_empty_query_rejected(self):
        """空查询应被拒绝"""
        result = web_search_tool.invoke({"query": ""})
        assert "搜索" in result or "关键词" in result
    
    def test_null_query_rejected(self):
        """null 查询应被拒绝"""
        result = web_search_tool.invoke({"query": None})
        assert "搜索" in result or "关键词" in result


class TestWebSearchToolRetryAndTimeout:
    """重试与超时模拟测试"""
    
    def test_duckduckgo_rate_limit_handling(self):
        """应对 DuckDuckGo 限流的重试机制"""
        response_codes = [429, 429, 200]  # 连续两次限流，第三次成功
        
        def mock_urlopen(url, timeout=10):
            code = response_codes.pop(0)
            mock_resp = MagicMock()
            if code == 429:
                mock_resp.read = MagicMock(return_value=b'<html><body>Too Many Requests</body></html>')
                raise Exception(f"HTTP Error {code}")
            else:
                mock_resp.read = MagicMock(return_value=b'<html><!-- search results --></html>')
                return mock_resp
        
        with patch('urllib.request.urlopen', side_effect=mock_urlopen):
            result = web_search_tool.invoke({
                "query": "Python programming",
                "num_results": 5
            })
            
            # 最终应成功
            assert "[SEARCH FAILED]" not in result


class TestWebSearchToolMockStrategy:
    """Mock 策略说明测试"""
    
    def test_mock_duckduckgo_html_parsing(self):
        """Mock DuckDuckGo HTML 解析的测试"""
        mock_html = b'''
        <html>
        <body>
            <div class="result__url">example.com</div>
            <a class="result__a">Example Title</a>
            <a class="result__snippet">Example description text</a>
        </body>
        </html>
        '''
        
        with patch('backend.tools.web.urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read = MagicMock(return_value=mock_html)
            mock_urlopen.return_value = mock_resp
            
            result = web_search_tool.invoke({
                "query": "test query",
                "num_results": 1
            })
            
            assert "Example Title" in result
            assert "Example description" in result
```

---

### 覆盖率目标与量化指标

**目标**: 核心 Tool 覆盖率 ≥ 80%, 非核心≥ 60%

| Tool | Week1 覆盖率 | Week2 覆盖率 | 增量测试数 |
|------|------------|------------|----------|
| data_collection_tool | 45% | **85%** | 25 |
| execute_sql_tool | 50% | **88%** | 20 |
| sql_query_tool | 30% | **75%** | 15 |
| web_search_tool | 20% | **70%** | 12 |
| web_crawl_tool | 15% | **65%** | 10 |
| competitor_analysis_tool | 25% | **62%** | 8 |
| send_email_tool | 10% | **55%** | 5 |
| generate_report_tool | 5% | **50%** | 4 |
| export_csv_tool | 5% | **48%** | 3 |

**总测试数**: 约 **102 个测试用例**

**交付时间线:**
- **Week 1 (Day 1-5)**: 完成 P0+P1 级 Tool 测试（6 个 Tool, 80 测试用例）
- **Week 2 (Day 1-5)**: 完成 P2+P3 级 Tool 测试（3 个 Tool, 22 测试用例）+ Code Coverage 达标

---

## 第三部分：WebSearch/DataCollection Skill 升级计划（Month 1）

### 架构演进路线图

```
当前痛点:
1. Tool 直接嵌入业务逻辑，难以独立部署
2. Skill 层依赖 Tool 同步阻塞，影响并发
3. 缺乏标准化 API 接口

演进目标:
Month 1: Tool → Service (FastAPI 微服务)
Month 2: Service → MCP Server  
Month 3: Full Skill Layer Refactor
```

### FastAPI 微服务封装方案

#### WebSearch Server (端口 8081)

**新建文件**: `backend/services/web_search_server.py`

```python
"""backend/services/web_search_server.py — Web Search MCP Server

参考 yzfly/douyin-mcp-server 的三种调用方式：
1. Standard MCP: /call/{tool_name}?params={...}
2. Batch Call: /batch/calls [{tool, params}, ...]
3. Streaming: /stream/{tool_name} (SSE)
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
import uvicorn

app = FastAPI(
    title="Web Search MCP Server",
    version="1.0.0",
    description="MCP 协议封装的 Web 搜索服务",
)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="搜索关键词")
    num_results: int = Field(default=5, ge=1, le=20, description="返回结果数")


class SearchResponse(BaseModel):
    success: bool
    data: Optional[str] = None
    error: Optional[str] = None
    latency_ms: float


class BatchSearchRequest(BaseModel):
    calls: List[tuple[str, dict]] = Field(..., description="[tool_name, params] 对")


class BatchSearchResponse(BaseModel):
    results: List[SearchResponse]


@app.post("/call/search", response_model=SearchResponse)
async def call_web_search(req: SearchRequest):
    """
    标准 MCP 调用：单次 Web 搜索
    """
    from backend.tools.web import web_search_tool
    import time
    
    start = time.perf_counter()
    try:
        result = web_search_tool.invoke(req.model_dump())
        latency = (time.perf_counter() - start) * 1000
        
        return SearchResponse(
            success=True,
            data=result,
            error=None,
            latency_ms=latency
        )
    except Exception as e:
        return SearchResponse(
            success=False,
            data=None,
            error=str(e),
            latency_ms=0
        )


@app.post("/batch/calls", response_model=BatchSearchResponse)
async def batch_search(req: BatchSearchRequest):
    """
    批量调用：支持多个 Tool 并行执行
    """
    import asyncio
    
    async def single_call(call_pair):
        tool_name, params = call_pair
        if tool_name != "web_search":
            return SearchResponse(success=False, error="Unsupported tool")
        
        from backend.tools.web import web_search_tool
        import time
        
        start = time.perf_counter()
        try:
            result = web_search_tool.invoke(params)
            latency = (time.perf_counter() - start) * 1000
            return SearchResponse(success=True, data=result, error=None, latency_ms=latency)
        except Exception as e:
            return SearchResponse(success=False, error=str(e), latency_ms=0)
    
    tasks = [single_call(pair) for pair in req.calls]
    results = await asyncio.gather(*tasks)
    return BatchSearchResponse(results=results)


@app.get("/stream/search", streaming=True)
async def stream_search(query: str):
    """
    流式调用：SSE 实时推送搜索结果
    """
    from backend.tools.web import web_search_tool
    import asyncio
    
    async def search_generator():
        yield f"data: {\"event\": \"start\", \"query\": \"{query}\"}\n\n"
        
        try:
            # Simulate streaming results
            for i in range(5):
                await asyncio.sleep(0.1)
                result_part = f"Result {i+1}: Example snippet..."
                yield f"data: {\"event\": \"chunk\", \"index\": {i}, \"content\": \"{result_part}\"}\n\n"
            
            full_result = web_search_tool.invoke({"query": query})
            yield f"data: {\"event\": \"complete\", \"full_result\": \"{full_result}\"}\n\n"
        except Exception as e:
            yield f"data: {\"event\": \"error\", \"message\": \"{str(e)}\"}\n\n"
        
        yield "data: [DONE]\n\n"
    
    return search_generator()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
```

#### DataCollection Server (端口 8082)

**新建文件**: `backend/services/data_collection_server.py` (略，参照上述模板)

---

### 灰度发布策略

| 阶段 | 时间 | 流量比例 | 监控指标 |
|------|------|---------|---------|
| Canary 1 | Day 1 | 5% | Error rate < 1%, p95 latency < 2s |
| Canary 2 | Day 2 | 15% | CPU/Memory < 70% |
| Canary 3 | Day 3 | 50% | 业务指标对齐（成功率 99.9%） |
| Full | Day 4 | 100% | 全量发布 |

**降级预案:**

```python
# backend/config/deployment.py
def graceful_degrade_to_native_tool(tool_name, params):
    from backend.tools import tool_registry
    
    try:
        # 尝试 MCP 调用
        mcp_client = MCPClient()
        return mcp_client.call(tool_name, params)
    except Exception as e:
        logger.warning(f"MCP call failed, falling back to native tool: {e}")
        
        # 降级到本地 Tool
        native_tool = tool_registry.get_tool(tool_name)
        if native_tool:
            return native_tool.invoke(params)
        else:
            raise RuntimeError(f"No fallback tool available: {tool_name}")
```

---

## 第四部分：Tool/Skill 代码评审 Checklist 体系

### 命名规范检查项

**文件**: `docs/guidelines/tool_skill_naming_conventions.md`

```markdown
# Tool/Skill 命名规范

## Tool 命名规则

### ✅ Good:
- `execute_sql_tool`
- `web_search_tool`
- `data_collection_tool`
- 遵循动词 + 名词模式
- 长度控制在 3-5 个单词

### ❌ Bad:
- `sql_executor` (不符合后缀规范)
- `search_web` (动词后置)
- `smart_tool` (功能模糊)

---

## Capability 格式规范

### ✅ Good:
- `sql.query`
- `web.search`
- `data.collect`
- 遵循 `<domain>.<action>` 格式

### ❌ Bad:
- `query_sql` (无领域标识)
- `web_search` (无 action 分隔符)
```

### 依赖声明模板

**文件**: `templates/tool_dependency_template.txt`

```markdown
# Tool 依赖声明模板

## Tool 基本信息
- **名称**: `my_new_tool`
- **路径**: `backend/tools/my_new_tool.py`
- **作者**: [Your Name]

## 运行时依赖
### Python 包依赖
```toml
requests = "^2.31.0"
pydantic = "^2.5.0"
```

### 环境依赖
- **API Key**: `MY_TOOL_API_KEY`
- **Database**: `postgresql://...`

## 安全依赖审查
- ✅ 只读取必要的环境变量
- ✅ 网络请求限制白名单域名
- ✅ 禁止执行动态代码（eval/exec）
```

### 错误码分类

**文件**: `backend/shared/error_codes.py`

```python
"""错误码格式：<DOMAIN>-<CATEGORY>-<CODE>"""

from enum import Enum

class ErrorCode(Enum):
    # SQL Domain
    SQL_SYS_001 = "SQL-SYS-001"      # 系统级错误
    SQL_CONN_001 = "SQL-CONN-001"    # 连接池错误
    SQL_QUERY_001 = "SQL-QUERY-001"  # 查询语法错误
    
    # Web Domain
    WEB_NET_001 = "WEB-NET-001"      # 网络连接错误
    WEB_PARSE_001 = "WEB-PARSE-001"  # HTML 解析错误
    
ERROR_CODE_SEVERITY = {
    ErrorCode.SQL_SYS_001: "CRITICAL",
    ErrorCode.SQL_QUERY_001: "ERROR",
    ErrorCode.WEB_NET_001: "ERROR",
}
```

### 文档更新清单

✅ **代码提交前必备:**
- [ ] CHANGELOG.md 条目
- [ ] API.md 更新
- [ ] Architecture Decision Record (ADR)
- [ ] Test Coverage Report

✅ **上线前必备:**
- [ ] Deployment guide
- [ ] Monitoring dashboard links
- [ ] Alert rules documentation
- [ ] Rollback procedure

---

## 时间表与里程碑

### P0 紧急修复（Week 0）

| 日期 | 任务 | 负责人 | 产出物 |
|------|------|--------|--------|
| Day 1 | 删除 SQL 重复定义 + Registry 实现 | All | `sql.py` 修复版 |
| Day 2 | 集成到__init__.py + 启动验证 | All | 启动自检通过 |
| Day 3-4 | 编写 15 个单元测试用例 | Test Team | `test_sql_tool.py` |
| Day 5 | Code Review + 合并 | Tech Lead | PR Merge |

**里程碑**: ✅ 2026-09-06 前完成 P0 修复

---

### Week 1: P0+P1 工具测试覆盖

| 日期 | Tool | 测试用例数 | 负责人 |
|------|------|----------|--------|
| Mon-Wed | data_collection_tool | 25 | Test A |
| Thu-Fri | execute_sql_tool | 20 | Test B |
| Mon-Wed | sql_query_tool | 15 | Test C |
| Thu-Fri | web_search_tool | 12 | Test D |

**里程碑**: ✅ 2026-09-12 前覆盖率≥70%

---

### Week 2: P2+P3 工具测试覆盖

| 日期 | Tool | 测试用例数 | 负责人 |
|------|------|----------|--------|
| Mon-Tue | web_crawl_tool | 10 | Test E |
| Wed-Thu | competitor_analysis_tool | 8 | Test F |
| Fri | send_email_tool + generate_report + export | 12 | All |

**里程碑**: ✅ 2026-09-19 前覆盖率≥80%

---

### Month 1: WebSearch/DataCollection Skill 升级

| 周次 | 任务 | 负责人 | 产出物 |
|------|------|--------|--------|
| Week 1 | FastAPI 微服务开发 | Backend A | `web_search_server.py`, `data_collection_server.py` |
| Week 2 | MCP Server 集成 + 配置 | DevOps B | `docker/mcp_servers_config.json` |
| Week 3 | 灰度发布 + 监控 | SRE C | Canary 5%→15%→50%→100% |
| Week 4 | 回滚演练 + 文档 | All | 《降级预案》《运维手册》 |

**里程碑**: ✅ 2026-10-03 前完成 Skill 升级

---

### Long-term Governance (持续进行)

| 活动 | 频率 | 负责人 |
|------|------|--------|
| Tool/Skill 代码审查 | 每次 PR | Tech Lead |
| 质量检查脚本运行 | CI 流水线 | DevOps |
| 覆盖率报告评审 | 每周 | QA Manager |
| 技术债清理 | 每月 Sprint | All Teams |

---

## 附录：关键脚本与工具

### 1. 质量检查脚本

**文件**: `scripts/tool_quality_check.sh` (已在 plan-agent 输出中提供)

### 2. Docker Compose 配置

**文件**: `docker-compose.yml` (添加 MCP Server 服务)

```yaml
version: '3.8'

services:
  web-search-server:
    build:
      context: .
      dockerfile: Dockerfile.mcp
    ports:
      - "8081:8081"
    environment:
      - PYTHONPATH=/app
    restart: unless-stopped

  data-collection-server:
    build:
      context: .
      dockerfile: Dockerfile.mcp
    ports:
      - "8082:8082"
    environment:
      - PYTHONPATH=/app
    restart: unless-stopped
```

### 3. 性能基准测试报告模板

**文件**: `docs/performance/base_line_template.md` (已在 plan-agent 输出中提供)

---

## 风险管理与应对

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 重复定义再次发生 | 中 | 高 | Registry 自动检测 + CI 阻断 |
| 测试覆盖率不达标 | 低 | 中 | 优先保障 P0+P1, P2 可延后 |
| Skill 升级破坏兼容性 | 中 | 高 | 灰度发布 + 快速回滚 |
| 外部依赖不稳定 | 高 | 中 | Mock 策略 + 降级预案 |

---

## 签字确认

| 角色 | 姓名 | 签字日期 |
|------|------|----------|
| 技术负责人 | ________ | 2026-09-__ |
| QA 负责人 | ________ | 2026-09-__ |
| DevOps 负责人 | ________ | 2026-09-__ |

---

**版本历史:**
- v1.0 (2026-09-02): Initial release based on comprehensive codebase analysis

**文档维护:**  
📧 Contact: ai-development-team@example.com  
🔗 Wiki: http://wiki.internal/tool-skill-optimization