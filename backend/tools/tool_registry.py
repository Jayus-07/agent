"""tools/tool_registry.py — Tool 注册中心与去重验证

职责：
1. 检测 Tool 重复定义（P0 级防护）
2. 提供统一的 Tool 发现 API
3. 记录 Tool 元数据用于 Planner prompt
4. 在模块加载时自动扫描并注册所有 Tool
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
