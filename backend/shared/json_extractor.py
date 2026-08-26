"""json_extractor.py — 从 LLM 输出中提取 JSON 的统一实现（P1-14）

此前 4 处各写一份、行为不一致（planner 4 层修复管道 / llm_router 与
nli_llm 三步链 / analyzer 抛 ValueError），修复策略散落难以统一演进。
现在收敛为单一策略链，调用方按需选择失败语义：

  extract_json(text)              -> dict | None   （宽松：全失败返回 None）
  extract_json_or_empty(text)     -> dict          （全失败返回 {}）
  extract_json_strict(text)       -> dict          （全失败抛 JsonExtractionError）

策略链（前者成功即返回）：
  Layer 0: 直接 json.loads（最快路径）
  Layer 1: 剥离 markdown 代码块围栏后解析
  Layer 2: 截取最外层 { } 再解析
  Layer 3: 修复常见小模型 JSON 错误（尾逗号/中文引号/未加引号 key/单引号）后解析
  Layer 4: 暴力正则提取嵌套对象（按长度降序逐个尝试）
"""
from __future__ import annotations

import json
import re
from typing import Optional, Union

__all__ = [
    "JsonExtractionError",
    "extract_json",
    "extract_json_or_empty",
    "extract_json_strict",
]


class JsonExtractionError(ValueError):
    """所有策略均无法从文本中提取出 JSON 对象。"""

    def __init__(self, text_preview: str):
        self.text_preview = text_preview
        super().__init__(f"无法从 LLM 响应中提取 JSON: {text_preview}")


# =====================================================
# 内部策略
# =====================================================

def _strip_markdown_code_block(text: str) -> str:
    """去除 markdown 代码块标记 (```json ... ```)"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _find_outer_braces(text: str) -> Optional[tuple]:
    """找到最外层的 { } 边界，返回 (start, end) 或 None"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return start, end
    return None


def _fix_unquoted_keys(text: str) -> str:
    """修复缺失引号的 key: {key: "value"} -> {"key": "value"}"""
    return re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', text)


def _replace_single_quotes_in_json(text: str) -> str:
    """在 JSON 上下文中的单引号替换为双引号（保守策略：仅 key 和简单字符串值）"""
    text = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', text)
    text = re.sub(r"(:\s*)'([^']*)'", r'\1"\2"', text)
    return text


def _repair_common_json_errors(text: str) -> str:
    """修复小模型常见的 JSON 格式错误"""
    # 1. 尾逗号
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    # 2. 中文引号 → 英文引号
    text = text.replace('“', '"').replace('”', '"')
    # 3. 未转义的控制字符
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    # 4. 缺失引号的 key
    text = _fix_unquoted_keys(text)
    # 5. 单引号替换
    text = _replace_single_quotes_in_json(text)
    return text


def _brute_force_extract(text: str) -> Optional[dict]:
    """暴力提取：正则找嵌套 JSON 对象，按长度降序逐个尝试解析"""
    matches = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    for match in sorted(matches, key=len, reverse=True):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return None


def _run_pipeline(text: str) -> Optional[dict]:
    """执行完整策略链，返回解析结果或 None。"""
    if not text or not isinstance(text, str):
        return None

    # Layer 0: 直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 1: 剥离 markdown 围栏
    stripped = _strip_markdown_code_block(text)
    try:
        result = json.loads(stripped)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 2: 截取最外层 {}
    for candidate in (stripped, text):
        bounds = _find_outer_braces(candidate)
        if bounds:
            try:
                result = json.loads(candidate[bounds[0]:bounds[1] + 1])
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                pass

    # Layer 3: 修复常见错误后解析
    try:
        repaired = _repair_common_json_errors(stripped)
        bounds = _find_outer_braces(repaired)
        if bounds:
            result = json.loads(repaired[bounds[0]:bounds[1] + 1])
            if isinstance(result, dict):
                return result
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # Layer 4: 暴力正则提取
    return _brute_force_extract(text)


# =====================================================
# 对外 API
# =====================================================

def extract_json(text: str) -> Optional[dict]:
    """宽松语义：提取失败返回 None（调用方自行降级/记日志）。"""
    return _run_pipeline(text)


def extract_json_or_empty(text: str) -> dict:
    """宽松语义（空 dict 默认值）：提取失败返回 {}。"""
    return _run_pipeline(text) or {}


def extract_json_strict(text: str) -> dict:
    """严格语义：提取失败抛 JsonExtractionError（ValueError 子类）。"""
    result = _run_pipeline(text)
    if result is None:
        raise JsonExtractionError(text[:200])
    return result
