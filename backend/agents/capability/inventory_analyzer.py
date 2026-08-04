"""capability/inventory_analyzer.py — 示例 Business Agent Skill

演示：库存异常分析 Agent
- 输入：销售数据 + 库存数据 + 补货规则（RAG 检索结果）
- 输出：异常列表 + 补货建议 + confidence
- LLM 走 infra/llm/proxy.py

这是 Commit 7 (daily_report workflow) 的"agent_analyze" step 会用到的 Skill。
"""
from __future__ import annotations

import json as _json
import re as _re

from backend.agents.capability.base import BaseAgentSkill
from backend.shared.logger import logger


class InventoryAnalyzer(BaseAgentSkill):
    """库存异常分析 + 补货建议（Business Agent Skill）

    Capabilities:
        - inventory.analyze

    输入 schema:
        {
            "sales_data": list[dict],   # 销售历史（SQL 查询结果）
            "inventory_data": list[dict],  # 当前库存
            "rules": str,                # RAG 检索的补货规则（可选）
            "alert_level": str,          # info/warning/critical（可选）
        }

    输出 schema:
        {
            "anomalies": list[dict],     # 异常商品列表
            "advice": list[dict],         # 补货建议
            "confidence": float,          # 推理置信度 (0~1)
            "reasoning": str,             # 推理说明
        }
    """

    name = "inventory_analyzer"
    capabilities = ["inventory.analyze"]

    SYSTEM_PROMPT = """你是电商库存分析专家。基于给定的销售数据、库存数据和补货规则，输出结构化分析。

严格要求：
- 只基于提供的数据推理，不要编造
- 输出严格 JSON，不要 markdown 围栏
- anomalies: 库存异常商品列表（含 product_id, current_qty, daily_sales, days_of_stock, level）
- advice: 补货建议列表（含 product_id, recommended_qty, urgency）
- confidence: 0~1，根据数据完整性打分
- reasoning: 一句话说明关键发现"""

    async def run(self, inputs: dict) -> dict:
        sales_data = inputs.get("sales_data", [])
        inventory_data = inputs.get("inventory_data", [])
        rules = inputs.get("rules", "")
        alert_level = inputs.get("alert_level", "warning")

        # 构造 prompt（结构化输入，避免 LLM 误读）
        user_prompt = f"""【补货规则参考】
{rules if rules else '（无）'}

【当前库存数据】
{_json.dumps(inventory_data[:20], ensure_ascii=False, indent=2)}

【销售数据】
{_json.dumps(sales_data[:20], ensure_ascii=False, indent=2)}

【告警级别】
{alert_level}

请输出 JSON 分析结果。"""

        try:
            content = await self._call_llm(
                prompt=user_prompt,
                system=self.SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=2000,
            )

            # 解析 JSON（容忍 markdown 围栏）
            match = _re.search(r"\{.*\}", content, _re.DOTALL)
            if match:
                data = _json.loads(match.group())
                return {
                    "anomalies": data.get("anomalies", []),
                    "advice": data.get("advice", []),
                    "confidence": float(data.get("confidence", 0.7)),
                    "reasoning": data.get("reasoning", ""),
                }
            else:
                logger.warning(f"[{self.name}] LLM 输出非 JSON: {content[:100]}")
                return self._fallback(inventory_data)
        except Exception as e:
            logger.error(f"[{self.name}] 推理失败: {e}")
            return self._fallback(inventory_data)

    @staticmethod
    def _fallback(inventory_data: list) -> dict:
        """LLM 失败时的兜底：基于简单阈值"""
        anomalies = []
        for item in inventory_data[:10]:
            qty = item.get("current_qty", 0)
            if qty < 10:
                anomalies.append({
                    "product_id": item.get("product_id", ""),
                    "current_qty": qty,
                    "level": "critical" if qty < 5 else "warning",
                })
        return {
            "anomalies": anomalies,
            "advice": [{"product_id": a["product_id"], "recommended_qty": 100, "urgency": a["level"]} for a in anomalies],
            "confidence": 0.3,
            "reasoning": "LLM 推理失败，基于简单阈值兜底",
        }