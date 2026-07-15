"""
router.py — 表筛选 (Schema Routing)

根据用户自然语言问题，用 LLM 选出可能相关的表。
只返回表名列表，不给 LLM 完整 schema，减少 token 消耗。
"""
import json
from typing import List

from backend.infra.llm.llm_factory import llm
from backend.sql.schema_loader import schema_loader
from backend.shared.logger import logger

ROUTER_PROMPT = """你是数据库表路由助手。给定用户问题和可用表列表，选出回答问题可能需要的表。

规则:
1. 只从给定列表中选择，不要编造表名
2. 选择最少但足够的表（通常 1-2 张）
3. 如果问题不涉及任何表，返回空数组
4. 严格输出 JSON 数组格式

可用表:
{table_list}

用户问题: {question}

请输出 JSON 数组，不要添加任何解释。"""


def select_tables(question: str) -> List[str]:
    """
    根据用户问题，用 LLM 选出相关表名。

    返回:
        相关表名列表，如 ["users"] 或 ["users", "departments"]
        如果 LLM 失败，回退为所有表
    """
    all_tables = schema_loader.get_all_table_names()
    if len(all_tables) <= 2:
        logger.info(f"[Router] 表数量 ≤ 2，直接返回全部: {all_tables}")
        return all_tables

    # 优先匹配用户显式提到的表名（如 "stg_inventory"）
    explicit = [t for t in all_tables if t.lower() in question.lower()]

    table_list = "\n".join(
        f"  - {t}: {schema_loader.get_table_description(t)}"
        for t in all_tables
    )

    prompt = ROUTER_PROMPT.format(table_list=table_list, question=question)

    try:
        resp = llm.invoke(prompt)
        content = resp.content.strip()

        # 提取 JSON 数组
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1:
            content = content[start:end + 1]

        selected = json.loads(content)

        if isinstance(selected, list):
            valid = [t for t in selected if t in all_tables]
            # 保证用户显式提到的表一定被选中
            for t in explicit:
                if t not in valid: valid.append(t)
            if not valid:
                logger.warning(f"[Router] LLM 返回无效表名: {selected}，回退全部")
                return all_tables
            logger.info(f"[Router] 用户问题 '{question[:40]}...' → 选中表: {valid}")
            return valid

    except json.JSONDecodeError as e:
        logger.warning(f"[Router] JSON 解析失败: {e}，回退全部")
    except Exception as e:
        logger.error(f"[Router] LLM 调用失败: {e}，回退全部")

    return all_tables
