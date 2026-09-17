"""tools/tool_registry.py — Tool 层的发现与查重权威

职责：
1. 检测 Tool 重复定义（P0 级防护）
2. 静态发现：AST 扫描 ``@tool`` 声明（``scan_repo_declared_tools``）——
   质量脚本与守护测试的唯一判据来源，消除「两份硬编码清单各自漂移」
3. 登记运行期已加载的 Tool 对象（``register``）

⚠️ 与同名模块的区分（读 import 路径，别看名字）::

    backend/tools/tool_registry.py          ← 本模块：**Tool** 注册表（34 个 @tool）
    backend/orchestration/capability_registry.py  ← **Capability** 注册表（从 Skill 派生，
                                               被 Planner / tool_selector / builder 消费）

两者同名不同物。本表**不参与** Planner prompt —— Planner 的输入来自
Capability 层（``CAPABILITY_SCHEMA`` 是 ``orchestration.capability_registry`` 的
派生属性，与本表无关）。

消费方：``scripts/tool_quality_check.py``、``backend/tests/test_layer_consistency.py``
（均在运行时之外；详见 docs/2026-09-16-Agent-Skill-Tool-MCP四层设计规范.md §3.3）
"""
import ast
import inspect
from pathlib import Path
from typing import Dict, List, Set, Tuple
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
        """注册单个 Tool，检测重复定义
        
        Args:
            fn: LangChain Tool 对象或自定义 Tool
            source_file: 源文件路径
            
        Raises:
            DuplicateToolError: 检测到同一文件中重复定义
        """
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
        logger.debug(f"[ToolRegistry] 注册 Tool: {name} @ {source_file}")
    
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
        """检测所有重复定义（供 CI 使用）
        
        Returns:
            Dict: {tool_name: [file_paths]} 的字典，仅包含有重复的 Tool
        """
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
    
    def log_registration_summary(self):
        """打印注册汇总信息（用于启动日志）"""
        total = len(self._registered_tools)
        duplicates = self.check_duplicates()
        dup_count = len(duplicates)
        
        logger.info(f"[ToolRegistry] 总计注册 {total} 个 Tool")
        if dup_count > 0:
            logger.warning(f"[ToolRegistry] ⚠️ 发现 {dup_count} 个重复定义:")
            for name, sources in duplicates.items():
                logger.warning(f"  - {name}: {len(sources)} 次定义")
        else:
            logger.info("[ToolRegistry] ✅ 无重复定义")


# 全局单例
tool_registry = ToolRegistry()


def register_tool(tool_fn, source_file: str = ""):
    """便捷注册函数（可手动调用）
    
    Args:
        tool_fn: Tool 对象
        source_file: 源文件路径（自动从调用栈获取）
    """
    if not source_file:
        frame = inspect.currentframe()
        if frame and frame.f_back:
            source_file = frame.f_back.f_code.co_filename
    tool_registry.register(tool_fn, source_file)


# =====================================================
# 静态发现（AST）—— 脚本 / 守护测试共用的唯一判据
# =====================================================

# @tool 允许出现的扫描根（相对仓库根）。
# 同时扫 skills/ 是为了能发现「Tool 出界到 Skill 层」这种偏差。
TOOL_SCAN_ROOTS: Tuple[str, ...] = ("backend/tools", "backend/skills")


def scan_decorated_tools(root: str | Path) -> List[Tuple[Path, str, int]]:
    """AST 扫描 root 下所有被 ``@tool`` / ``@<x>.tool`` 装饰的函数。

    纯静态解析，**不导入模块** —— 因此不受导入副作用与循环导入影响，
    可在脚本、测试、CI 任意上下文使用。

    Returns:
        ``[(源文件 Path, 函数名, 行号), ...]``（按文件路径排序，结果稳定）
    """
    root = Path(root)
    found: List[Tuple[Path, str, int]] = []
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:  # 语法坏文件不应让整个扫描崩掉，也要能被看见
            logger.warning(f"[ToolRegistry] 跳过无法解析的文件 {py}: {e}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    deco = dec.id
                elif isinstance(dec, ast.Attribute):
                    deco = dec.attr
                else:
                    continue
                if deco == "tool":
                    found.append((py, node.name, node.lineno))
    return found


def scan_repo_declared_tools(repo_root: str | Path) -> Dict[str, List[str]]:
    """扫描 ``TOOL_SCAN_ROOTS`` 下所有 ``@tool`` 声明。

    Returns:
        ``{函数名: ["backend/tools/sql.py:90", ...]}``

    与 ``tool_names`` 的差集即「漏注册」（已定义但未登记）。
    """
    repo_root = Path(repo_root)
    result: Dict[str, List[str]] = {}
    for rel in TOOL_SCAN_ROOTS:
        for py, fn, lineno in scan_decorated_tools(repo_root / rel):
            try:
                where = f"{py.relative_to(repo_root).as_posix()}:{lineno}"
            except ValueError:  # 不在仓库内（测试用 tmp 目录）
                where = f"{py.as_posix()}:{lineno}"
            result.setdefault(fn, []).append(where)
    return result
