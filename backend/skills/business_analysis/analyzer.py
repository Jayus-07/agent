"""
skills/business_analysis/analyzer.py — 业务分析核心

职责：接收 SQLResult + RAG 知识 → LLM 生成 BusinessInsight
不访问数据库，不依赖 Skill/Tool 层。
"""
import json
import re
from pathlib import Path
from typing import Any

from backend.infra.llm import llm
from backend.shared.logger import logger
from backend.skills.sql.models import SQLResult
from backend.skills.business_analysis.models import BusinessInsight

# 加载 Prompt 模板
_PROMPT_PATH = Path(__file__).parent / "prompts" / "business_analysis.md"
with open(_PROMPT_PATH, encoding="utf-8") as _f:
    BUSINESS_ANALYSIS_PROMPT = _f.read()


def _truncate_rows(rows: list[dict[str, Any]], max_rows: int = 20) -> str:
    """截断行数据，防止 Prompt 过长"""
    display = rows[:max_rows]
    return json.dumps(display, ensure_ascii=False, default=str)


def _extract_json(content: str) -> dict:
    """从 LLM 响应中提取 JSON 对象"""
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试匹配 { ... } 对象
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从 LLM 响应中提取 JSON: {content[:200]}")


class BusinessAnalyzer:
    """业务分析器 — 将 SQL 数据转化为业务洞察。

    使用方式:
        analyzer = BusinessAnalyzer()
        insight = analyzer.analyze(sql_result, rag_knowledge)
    """

    def analyze(
        self,
        sql_result: SQLResult,
        rag_knowledge: str = "",
    ) -> BusinessInsight:
        """对 SQL 查询结果进行业务分析。

        参数:
            sql_result: SQLSkill 产出的结构化查询结果
            rag_knowledge: RAG 检索到的业务知识（规则/策略/历史）

        返回:
            BusinessInsight 结构化洞察
        """
        logger.info(
            f"[BusinessAnalyzer] 分析 {sql_result.row_count} 行数据, "
            f"表: {sql_result.tables}"
        )

        columns_str = ", ".join(sql_result.columns)
        data_str = _truncate_rows(sql_result.rows)
        knowledge_str = rag_knowledge or "（无额外业务知识）"

        prompt = BUSINESS_ANALYSIS_PROMPT.format(
            columns=columns_str,
            sql_data=data_str,
            knowledge=knowledge_str,
        )

        try:
            resp = llm.invoke(prompt)
            content = resp.content.strip()
            parsed = _extract_json(content)

            insight = BusinessInsight(
                summary=str(parsed.get("summary", "")),
                risks=[
                    str(r) for r in parsed.get("risks", [])
                    if isinstance(r, str) and r.strip()
                ],
                suggestions=[
                    str(s) for s in parsed.get("suggestions", [])
                    if isinstance(s, str) and s.strip()
                ],
                confidence=float(parsed.get("confidence", 0.5)),
                related_knowledge=(
                    [rag_knowledge[:200]] if rag_knowledge else []
                ),
            )

            logger.info(
                f"[BusinessAnalyzer] 分析完成: "
                f"summary={insight.summary[:80]}..., "
                f"confidence={insight.confidence}"
            )
            return insight

        except Exception as e:
            logger.error(f"[BusinessAnalyzer] LLM 分析失败: {e}")
            # 降级：返回基础摘要
            return BusinessInsight(
                summary=f"查询返回 {sql_result.row_count} 行数据，"
                        f"涉及 {len(sql_result.tables)} 张表",
                risks=[],
                suggestions=[],
                confidence=0.0,
            )
