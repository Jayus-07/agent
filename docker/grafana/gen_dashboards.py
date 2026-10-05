# -*- coding: utf-8 -*-
"""生成 4 个 Grafana 看板 JSON + provider 配置，落到仓库 docker/grafana/provisioning/dashboards/
指标名与标签均来自 2026-10-05 Prometheus 实测（/api/v1/label/__name__/values + 指标定义源码核对）。
"""
import json
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisioning", "dashboards")
DS = {"type": "prometheus", "uid": sys.argv[1] if len(sys.argv) > 1 else "prometheus"}
_pid = 0


def _next_id():
    global _pid
    _pid += 1
    return _pid


def panel(ptype, title, x, y, w, h, targets, unit=None, desc=None, thresholds=None):
    """targets: list of (expr, legend)"""
    t = [{"expr": e, "legendFormat": lg, "refId": chr(65 + i), "datasource": DS}
         for i, (e, lg) in enumerate(targets)]
    p = {
        "id": _next_id(), "type": ptype, "title": title,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": DS,
        "targets": t,
        "fieldConfig": {"defaults": {"custom": {}}, "overrides": []},
        "options": {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    if thresholds:
        p["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute", "steps": thresholds}
    if desc:
        p["description"] = desc
    if ptype == "timeseries":
        p["options"] = {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        }
    else:  # stat
        p["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value", "graphMode": "area", "textMode": "auto",
            "orientation": "auto",
        }
    return p


def dashboard(uid, title, panels):
    return {
        "uid": uid, "title": title, "tags": ["agent-platform"],
        "description": "配置源(Git): docker/grafana/provisioning/dashboards/ 由 provisioning 自动装配，勿在 UI 手改（会被覆盖）",
        "timezone": "browser", "schemaVersion": 39, "version": 1,
        "refresh": "30s", "time": {"from": "now-24h", "to": "now"},
        "panels": panels,
    }


# ───────────────────────── D1 业务链路 ─────────────────────────
P = []
P.append(panel("stat", "聊天 QPS", 0, 0, 4, 8,
               [("sum(rate(chat_request_total[5m]))", "QPS")], "reqps",
               desc="有真实聊天流量后才有数据（multiprocess 模式计数器首次写入后出现）"))
P.append(panel("stat", "24h 聊天请求数", 4, 0, 4, 8,
               [("sum(increase(chat_request_total[24h]))", "请求数")], "short"))
P.append(panel("stat", "SSE 执行池活跃数", 8, 0, 4, 8,
               [("chat_sse_executor_active", "活跃")], "short"))
P.append(panel("stat", "LLM 失败率", 12, 0, 4, 8,
               [("100 * sum(rate(llm_failures_total[5m])) / sum(rate(llm_requests_total[5m]))",
                 "失败率")], "percent"))
P.append(panel("stat", "1h LLM 降级(fallback)", 16, 0, 4, 8,
               [("sum(increase(llm_fallback_total[1h]))", "次")], "short"))
P.append(panel("stat", "1h 请求级降级", 20, 0, 4, 8,
               [("sum(increase(agent_request_degraded_total[1h]))", "次")], "short"))

P.append(panel("timeseries", "聊天请求耗时 P50/P95/P99", 0, 8, 12, 9, [
    ('histogram_quantile(0.50, sum(rate(chat_request_duration_seconds_bucket[5m])) by (le))', "P50"),
    ('histogram_quantile(0.95, sum(rate(chat_request_duration_seconds_bucket[5m])) by (le))', "P95"),
    ('histogram_quantile(0.99, sum(rate(chat_request_duration_seconds_bucket[5m])) by (le))', "P99"),
], "s", desc="SLO 口径见 docs/observability/slo.md（告警阈值 P95/P99 各有专项规则）"))
P.append(panel("timeseries", "首字时间 TTFT P50/P95", 12, 8, 12, 9, [
    ('histogram_quantile(0.50, sum(rate(chat_ttft_seconds_bucket[5m])) by (le))', "P50"),
    ('histogram_quantile(0.95, sum(rate(chat_ttft_seconds_bucket[5m])) by (le))', "P95"),
], "s", desc="首 token 到达时间——用户感知「卡不卡」的核心指标"))

P.append(panel("timeseries", "每输出 Token 耗时 TPOT P95", 0, 17, 8, 9, [
    ('histogram_quantile(0.95, sum(rate(chat_tpot_seconds_bucket[5m])) by (le))', "P95"),
], "s"))
P.append(panel("timeseries", "SSE 流事件产出速率（按事件类型）", 8, 17, 8, 9, [
    ('sum(rate(chat_stream_event_produced_total[5m])) by (event)', "{{event}} 事件"),
], "reqps", desc="status | delta | log | done | error | meta（event_schema 13 帧口径）"))
P.append(panel("timeseries", "LLM 请求速率（按模型×状态）", 16, 17, 8, 9, [
    ('sum(rate(llm_requests_total[5m])) by (model, status)', "模型 {{model}} · {{status}}"),
], "reqps"))

D1 = dashboard("agent-biz-overview", "Agent 平台 · 业务链路", P)

# ───────────────────────── D2 LLM 成本 ─────────────────────────
P = []
P.append(panel("stat", "24h Token 总消耗", 0, 0, 6, 8,
               [("sum(increase(llm_tokens_total[24h]))", "tokens")], "short"))
P.append(panel("stat", "Token 消耗速率", 6, 0, 6, 8,
               [("sum(rate(llm_tokens_total[5m]))", "tokens/s")], "ops"))
P.append(panel("stat", "24h 记账缺失", 12, 0, 6, 8,
               [("sum(increase(llm_usage_missing_total[24h]))", "次")], "short",
               desc="usage_missing = 供应商响应无 usage，成本只能靠对账——持续增长要查"))
P.append(panel("stat", "24h 估算兜底", 18, 0, 6, 8,
               [("sum(increase(llm_usage_estimated_total[24h]))", "次")], "short",
               desc="估算兜底（32f1f43 零依赖字符统计）兜住的调用次数"))

P.append(panel("timeseries", "Token 消耗速率（输入/输出）", 0, 8, 12, 9, [
    ('sum(rate(llm_tokens_total{direction="prompt"}[5m])) by (model)', "输入 {{model}}"),
    ('sum(rate(llm_tokens_total{direction="completion"}[5m])) by (model)', "输出 {{model}}"),
], "ops"))
P.append(panel("timeseries", "24h Token 消耗（按模型）", 12, 8, 12, 9, [
    ('sum(increase(llm_tokens_total[24h])) by (model)', "模型 {{model}}"),
], "short", desc="折算 ¥ 成本明细在管理端成本看板（llm_usage.cost_cny），此处看趋势"))

P.append(panel("timeseries", "预算额度结果速率（按结果）", 0, 17, 12, 9, [
    ('sum(rate(budget_quota_total[5m])) by (result)', "预算:{{result}}"),
], "reqps", desc="预占/结算/拒绝——scope_type、period_type 细分可自行加"))
P.append(panel("timeseries", "预算检查请求速率", 12, 17, 12, 9, [
    ("sum(rate(budget_request_total[5m]))", "全部"),
    ("sum(rate(side_effect_budget_total[5m]))", "副作用预算"),
], "reqps"))

D2 = dashboard("agent-cost", "Agent 平台 · LLM 成本", P)

# ───────────────────────── D3 质量门 ─────────────────────────
P = []
P.append(panel("stat", "RAG 拒答率", 0, 0, 4, 8,
               [("rag_reject_rate", "拒答率")], "percentunit",
               thresholds=[{"color": "green", "value": None},
                           {"color": "yellow", "value": 0.2},
                           {"color": "red", "value": 0.4}],
               desc="E1 拍板门限 0.40（生产以 .env 覆盖值为准）"))
P.append(panel("stat", "RAG 命中率", 4, 0, 4, 8,
               [("rag_hit_rate", "命中率")], "percentunit"))
P.append(panel("stat", "客服 FAQ 命中率（日）", 8, 0, 4, 8,
               [("cs_qa_daily_faq_hit_ratio", "命中率")], "percentunit",
               desc="A6 知识质量门对应指标，gauge 刷新器每 600s 拉日报"))
P.append(panel("stat", "客服转人工率（日）", 12, 0, 4, 8,
               [("cs_qa_daily_handoff_rate", "转人工率")], "percentunit"))
P.append(panel("stat", "客服满意度（日）", 16, 0, 4, 8,
               [("cs_qa_daily_satisfaction", "满意度")], "percentunit"))
P.append(panel("stat", "24h 澄清展示次数", 20, 0, 4, 8,
               [("sum(increase(agent_clarify_shown_total[24h]))", "次")], "short"))

P.append(panel("timeseries", "Tool 错误七分类（1h 增量）", 0, 8, 12, 9, [
    ('sum(increase(agent_tool_error_class_total[1h])) by (error_class)', "错误:{{error_class}}"),
], "short", desc="M3 统一口径：timeout/network_error/permission_denied/validation_error/business_error/contract_error/provider_error"))
P.append(panel("timeseries", "SQL 网关：请求与拦截", 12, 8, 12, 9, [
    ("sum(rate(sql_agent_requests_total[5m]))", "请求"),
    ("sum(rate(sql_agent_denied_total[5m]))", "拦截"),
], "reqps"))

P.append(panel("timeseries", "RAG 检索请求速率", 0, 17, 12, 9, [
    ("sum(rate(rag_query_total[5m]))", "检索"),
    ("sum(rate(rag_permission_filtered_total[5m]))", "权限过滤剔除"),
], "reqps"))
P.append(panel("timeseries", "语义校验结果速率（按结果）", 12, 17, 12, 9, [
    ('sum(rate(semantic_validation_total[5m])) by (result)', "校验:{{result}}"),
], "reqps"))

D3 = dashboard("agent-quality", "Agent 平台 · 质量门", P)

# ───────────────────────── D4 任务队列 ─────────────────────────
P = []
P.append(panel("timeseries", "Celery 队列积压（按队列）", 0, 0, 12, 9, [
    ("celery_queue_length", "{{queue_name}} 队列"),
], "short", desc="agent | rag_index | report | maintenance … 双队列路由见 celery_app.py"))
P.append(panel("stat", "Worker 在线数", 12, 0, 4, 9,
               [("sum(celery_worker_up)", "在线")], "short",
               thresholds=[{"color": "red", "value": None},
                           {"color": "green", "value": 1}]))
P.append(panel("stat", "活跃任务数", 16, 0, 4, 9,
               [("sum(celery_worker_tasks_active)", "活跃")], "short"))
P.append(panel("stat", "队列总积压", 20, 0, 4, 9,
               [("sum(celery_queue_length)", "积压")], "short"))

P.append(panel("timeseries", "任务成功/失败/重试速率", 0, 9, 12, 9, [
    ("sum(rate(celery_task_succeeded_total[5m]))", "成功"),
    ("sum(rate(celery_task_failed_total[5m]))", "失败"),
    ("sum(rate(celery_task_retried_total[5m]))", "重试"),
], "reqps"))
P.append(panel("timeseries", "任务运行时长 P95", 12, 9, 12, 9, [
    ('histogram_quantile(0.95, sum(rate(celery_task_runtime_bucket[5m])) by (le))', "P95"),
], "s"))

P.append(panel("timeseries", "Worker 终态任务速率（按流程×状态）", 0, 18, 12, 9, [
    ('sum(rate(task_terminal_total[5m])) by (workflow, status)', "{{workflow}} · {{status}}"),
], "reqps", desc="来源 :9809 worker 运行时指标（multiprocess 聚合）"))
P.append(panel("timeseries", "任务排队等待 P95", 12, 18, 12, 9, [
    ('histogram_quantile(0.95, sum(rate(task_queue_wait_seconds_bucket[5m])) by (le))', "P95"),
], "s"))

P.append(panel("timeseries", "准入：拒绝与过期", 0, 27, 12, 9, [
    ("sum(rate(task_admission_rejected_total[5m]))", "拒绝"),
    ("sum(rate(task_admission_expired_total[5m]))", "预占过期"),
    ("task_admission_active_global", "全局并发中"),
], "reqps", desc="分布式准入门 DIST_CONCURRENCY_ENABLED，扩容时开启"))
P.append(panel("timeseries", "重试与恢复", 12, 27, 12, 9, [
    ("sum(rate(task_retry_total[5m]))", "重试"),
    ("sum(rate(task_recovery_total[5m]))", "checkpoint 恢复"),
], "reqps"))

D4 = dashboard("agent-tasks", "Agent 平台 · 任务队列与 Worker", P)

# ───────────────────────── 写盘 ─────────────────────────
os.makedirs(OUT, exist_ok=True)
for fn, data in [
    ("agent-biz-overview.json", D1),
    ("agent-cost.json", D2),
    ("agent-quality.json", D3),
    ("agent-tasks.json", D4),
]:
    path = os.path.join(OUT, fn)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    n = len(data["panels"])
    print(f"OK {fn}: {n} panels")

print("total dashboards: 4, total panels:", _pid)
