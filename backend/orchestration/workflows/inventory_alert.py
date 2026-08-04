"""workflows/inventory_alert.py — 库存预警 Workflow（Phase 2 核心）

架构（按企业方案）：
- 8 Step DAG，6 层（含并行层）
- 每个 step 是结构化操作（数据获取 / 评估 / 状态机 / 通知）
- 不调 Planner（确定流程，决策 2.5 选"Workflow 调 Domain Agent Skill，不调 Planner"）

Step 设计：
- Layer 0 (并行):
    ├─ scan_inventory          SQL 查所有商品当前库存
    └─ fetch_sales_history    SQL 查近 30 天销售
- Layer 1: calculate_inventory_health  调 evaluate_batch
- Layer 2: evaluate_thresholds         调 find_threshold
- Layer 3: alert_state_machine         调 decide（纯规则）
- Layer 4 (并行):
    ├─ create_event           写 events 表
    └─ load_notification_policies  查 policies
- Layer 5: send_alert_email    调 email skill
"""
from __future__ import annotations

from datetime import datetime

from backend.orchestration.workflow import workflow, step
from backend.orchestration.workflow.skill_adapter import call_email, call_sql
from backend.orchestration.inventory import (
    InventoryStore,
    get_inventory_store,
    evaluate_batch,
    decide,
    plan,
    render_email_body,
    InventoryState,
)
from backend.orchestration.inventory.notification import NotificationPlan
from backend.orchestration.inventory.state_machine import AlertDecision
from backend.orchestration.inventory.state import InventoryAssessment
from backend.shared.logger import logger


@workflow(
    name="inventory_alert",
    description="库存预警：动态评估（min_qty + days_of_stock）+ 状态机 + 多 Policy 通知",
    objects=["库存", "补货", "预警"],
    actions=["扫描", "监控", "预警"],
    examples=[
        "扫描库存预警",
        "检查库存风险",
        "运行库存告警",
    ],
    default_kbs=["policies"],
    category="alert",
)
class InventoryAlert:
    """库存预警 Workflow（8 Step）"""

    @step(name="扫描库存")
    async def scan_inventory(self, ctx):
        """Step 1: 扫所有商品当前库存（SQL）"""
        logger.info("[InventoryAlert] Step scan_inventory 开始")
        result = await call_sql({
            "query": (
                "SELECT product_id, product_name, category, current_qty, "
                "supplier_grade FROM inventory WHERE current_qty >= 0"
            ),
        })
        items = result.get("rows", result)
        return {"inventory_items": items}

    @step(name="拉取销售历史")
    async def fetch_sales_history(self, ctx):
        """Step 2: 查近 30 天销售历史（SQL，与 scan_inventory 并行）"""
        logger.info("[InventoryAlert] Step fetch_sales_history 开始")
        result = await call_sql({
            "query": (
                "SELECT product_id, date, SUM(qty) as qty "
                "FROM sales WHERE date > CURRENT_DATE - INTERVAL '30 days' "
                "GROUP BY product_id, date"
            ),
        })
        rows = result.get("rows", result)
        # 转 {product_id: [{date, qty}, ...]}
        sales_by_sku = {}
        for r in rows:
            sales_by_sku.setdefault(r["product_id"], []).append({
                "date": r["date"],
                "qty": r["qty"],
            })
        return {"sales_by_sku": sales_by_sku}

    @step(
        depends_on=["scan_inventory", "fetch_sales_history"],
        timeout_sec=30,
        name="计算库存健康度",
    )
    async def calculate_inventory_health(self, ctx):
        """Step 3: 批量评估库存状态（动态：min_qty + days_of_stock）"""
        logger.info("[InventoryAlert] Step calculate_inventory_health 开始")
        items = ctx.outputs.get("scan_inventory", {}).get("inventory_items", [])
        sales_by_sku = ctx.outputs.get("fetch_sales_history", {}).get("sales_by_sku", {})

        # 按 product_id 找 threshold rule
        store = get_inventory_store()
        thresholds_by_sku = {}
        for item in items:
            pid = item["product_id"]
            # 优先 sku 规则，其次 category，最后 global
            rule = store.find_threshold(
                product_id=pid,
                category=item.get("category"),
            )
            if rule:
                thresholds_by_sku[pid] = rule

        # 批量评估
        assessments = evaluate_batch(
            items=[
                {
                    "product_id": i["product_id"],
                    "current_qty": i["current_qty"],
                    "category": i.get("category"),
                }
                for i in items
            ],
            thresholds_by_sku=thresholds_by_sku,
            sales_by_sku=sales_by_sku,
        )

        return {
            "assessments": [a.to_dict() for a in assessments],
            "alerting_count": sum(1 for a in assessments if a.state != InventoryState.NORMAL),
        }

    @step(
        depends_on=["calculate_inventory_health"],
        timeout_sec=30,
        name="评估阈值规则",
    )
    async def evaluate_thresholds(self, ctx):
        """Step 4: 对每个 alerting 商品跑状态机决策

        输入：assessments（list of dict）
        输出：decisions（每个商品一个 AlertDecision）
        """
        logger.info("[InventoryAlert] Step evaluate_thresholds 开始")
        assessments_dicts = ctx.outputs.get("calculate_inventory_health", {}).get("assessments", [])
        store = get_inventory_store()
        now = datetime.now()

        # 批量加载现有 case + 最后事件（一次连接搞定，避免 N+1）
        alerting = [a for a in assessments_dicts if a["state"] != "normal"]
        product_ids = [a["product_id"] for a in alerting]
        cases_map = store.get_cases_by_products(product_ids)
        case_ids = [c["id"] for c in cases_map.values() if c.get("id")]
        events_map = store.get_last_events_by_cases(case_ids)

        decisions = []
        for a_dict in alerting:
            pid = a_dict["product_id"]
            current_case = cases_map.get(pid)
            last_event = events_map.get(current_case["id"]) if current_case else None

            # 状态机决策
            decision = decide(
                inventory_state=InventoryState(a_dict["state"]),
                inventory_level=a_dict["alert_level"],
                current_case=current_case,
                last_event=last_event,
                now=now,
            )
            decisions.append({
                "product_id": pid,
                "assessment": a_dict,
                "decision": decision.to_dict(),
                "current_case": current_case,
            })

        return {"decisions": decisions}

    @step(
        depends_on=["evaluate_thresholds"],
        timeout_sec=30,
        name="告警状态机决策",
    )
    async def alert_state_machine(self, ctx):
        """Step 5: 状态机执行：apply decision → 更新 case / 写 event

        输入：decisions
        输出：executed（每个执行结果）
        """
        logger.info("[InventoryAlert] Step alert_state_machine 开始")
        decisions = ctx.outputs.get("evaluate_thresholds", {}).get("decisions", [])
        store = get_inventory_store()
        now = datetime.now()
        now_str = now.isoformat()

        executed = []
        for d in decisions:
            pid = d["product_id"]
            decision = AlertDecision(**d["decision"])
            current_case = d["current_case"]
            assessment = d["assessment"]

            # 1. 写 event
            event_id = None
            if decision.action != "SILENT":
                event_id = store.insert_event({
                    "case_id": current_case["id"] if current_case else 0,  # 临时，可能后面 update
                    "event_type": decision.action.lower(),
                    "from_state": (current_case or {}).get("current_state"),
                    "to_state": assessment["state"],
                    "qty": assessment["current_qty"],
                    "stock_days": assessment.get("stock_days", 0),
                    "reason": decision.reason,
                    "notified": decision.notify,
                })
                # case_id 需要在 create case 后再写 — 这里用 placeholder 后修复

            # 2. 更新 / 创建 case
            if decision.action == "CREATE":
                case_id = store.upsert_case({
                    "product_id": pid,
                    "current_state": assessment["state"],
                    "current_level": assessment["alert_level"],
                    "status": "open",
                })
                # 修正 event 的 case_id
                if event_id:
                    store._conn().execute(
                        "UPDATE inventory_alert_events SET case_id = ? WHERE id = ?",
                        (case_id, event_id),
                    )
                    store._conn().commit()
            elif decision.action in ("UPGRADE", "REMIND"):
                if current_case:
                    store.upsert_case({
                        "product_id": pid,
                        "current_state": assessment["state"],
                        "current_level": assessment["alert_level"],
                        "status": "open",
                    })
                    if decision.notify:
                        # 更新 last_notified_at
                        store._conn().execute(
                            "UPDATE inventory_alert_cases SET last_notified_at = ? WHERE id = ?",
                            (now_str, current_case["id"]),
                        )
                        store._conn().commit()
            elif decision.action == "RESOLVE":
                if current_case:
                    is_manual = decision.reason and "人工" in (decision.reason[0] if decision.reason else "")
                    store.update_case_status(
                        current_case["id"],
                        "resolved",
                        resolution_type="MANUAL_RESOLVED" if is_manual else "AUTO_RECOVERED",
                    )
            elif decision.action == "REOPEN":
                if current_case:
                    store.update_case_status(current_case["id"], "open")

            executed.append({
                "product_id": pid,
                "action": decision.action,
                "notify": decision.notify,
                "decision": decision.to_dict(),
                "assessment": assessment,
                "current_case_id": (current_case or {}).get("id"),
            })

        return {"executed": executed}

    @step(
        depends_on=["alert_state_machine"],
        timeout_sec=30,
        name="记录告警事件",
    )
    async def create_event(self, ctx):
        """Step 6a: 聚合已写入的 event（并行占位 step，与 load_notification_policies 并行）"""
        # 实际的事件已在 alert_state_machine 里写
        # 这里只是聚合 outputs 给 send_alert_email 用
        executed = ctx.outputs.get("alert_state_machine", {}).get("executed", [])
        return {"executed": executed, "executed_count": len(executed)}

    @step(
        depends_on=["alert_state_machine"],
        timeout_sec=10,
        name="加载通知策略",
    )
    async def load_notification_policies(self, ctx):
        """Step 6b: 查所有 policy（供 send_alert_email 用）

        注：实际 plan() 决策在 send_alert_email 里做，这里只 cache policy 列表
        """
        logger.info("[InventoryAlert] Step load_notification_policies 开始")
        store = get_inventory_store()
        policies = store.list_policies(enabled_only=True)
        return {"policy_count": len(policies), "policies_loaded": True}

    @step(
        depends_on=["create_event", "load_notification_policies"],
        timeout_sec=60,
        retry=2,
        on_error="abort",  # 邮件失败应该让 workflow 失败（不能假装成功）
        name="发送告警邮件",
    )
    async def send_alert_email(self, ctx):
        """Step 7: 对每个需通知的决策调 email skill"""
        logger.info("[InventoryAlert] Step send_alert_email 开始")
        executed = ctx.outputs.get("create_event", {}).get("executed", [])
        store = get_inventory_store()
        now_str = datetime.now().isoformat()

        sent_count = 0
        for e in executed:
            if not e["notify"]:
                continue

            # 找匹配 policy 并合并收件人
            assessment = e["assessment"]
            np = plan(
                decision=AlertDecision(**e["decision"]),
                inventory_state=assessment["state"],
                alert_level=assessment["alert_level"],
                category=assessment.get("threshold_rule", {}).get("category"),
                store=store,
            )
            if np is None:
                logger.info(f"[InventoryAlert] {e['product_id']} 无 policy 匹配")
                continue

            # 渲染 + 发邮件
            subject, body = render_email_body(np, extra={
                "product_id": e["product_id"],
                "current_qty": assessment["current_qty"],
                "daily_sales_avg": assessment.get("daily_sales_avg", 0),
                "stock_days": assessment.get("stock_days", 0),
                "case_id": e.get("current_case_id", "N/A"),
                "detected_at": now_str,
            })

            # call_email 调 email skill
            await call_email({
                "to": np.recipients,
                "subject": subject,
                "body": body,
            })
            sent_count += 1
            logger.info(
                f"[InventoryAlert] {e['product_id']} {e['action']} 邮件已发到 {np.recipients}"
            )

        return {"sent_count": sent_count}


__all__ = ["InventoryAlert"]