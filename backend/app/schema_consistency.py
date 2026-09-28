"""schema_consistency — 生产关键对象实存校验（STOP A2/A4）

背景：2026-09-28 实测 schema_migrations 台账把 018_prompts_pg.sql 记为
applied，但 prompts 三表已被测试 teardown（tests/prompts/test_prompt_repo.py
的 drop_all）从权威库删掉——台账只证明「曾经执行过」，不能证明「对象还在」。

本模块维护一份**生产关键对象清单**（刻意只覆盖上线必需项，不做全量
schema 快照），通过 information_schema/to_regclass 实查真实存在性：
  - memory 库（agent_memory）：Prompt / Auth / RAG 存储 / Model Registry /
    Task Runtime / Idempotency / Trace / Budget / CS / Travel / Checkpoint
  - business 库（agent_business）：业务核心表 + 审批
  - 扩展：agent_memory.vector（pgvector）
  - 关键列：只挑「后续迁移追加、且能区分迁移代际」的列（如
    llm_models.upstream_model_name=038），用于捕捉「表在但迁移半途」的形态

消费方：
  - /health 的 schema_consistency 段（软失败，不影响存活判定）
  - backend/scripts/validate_schema_consistency.py CLI（exit 0/1/2 作发布 preflight）
"""
import psycopg2

from backend.config.database import BUSINESS_DB_CONFIG, MEMORY_DB_CONFIG
from backend.shared.logger import logger

# agent_memory（memory 库）关键表——全部为运行链路硬依赖
_MEMORY_TABLES = [
    # Prompt CI/CD（018）
    "prompts", "prompt_versions", "prompt_audit_log",
    # RAG 存储（010/011/014/024/027）
    "doc_registry", "chunk_store", "rag_vectors", "doc_operation_log",
    "keyword_rules", "rag_processing_runs", "rag_processing_steps",
    "rag_index_meta",
    # Model Governance（023/031/034/038/039/045 + model_price 族）
    "llm_models", "llm_providers", "llm_provider_credentials",
    "llm_model_role_bindings", "llm_specialized_model_bindings",
    "model_price", "model_price_versions", "llm_config_history",
    # Auth（008/009/023/029/033/041）
    "auth.users", "auth.sessions", "auth.refresh_tokens",
    "auth.departments", "auth.rbac_audits",
    # Task Runtime + Idempotency（ai schema）
    "ai.idempotency_records", "ai.agent_tasks", "ai.agent_trace",
    "ai.gateway_access_logs",
    "tasks",
    # 可观测（012/013）
    "trace_store", "trace_summary",
    # 记忆 / 会话 / 工作流 / 预算 / SQL 审计
    "memory_records", "chat_sessions", "chat_messages",
    "workflow_runs", "sql_query_audits",
    "budget_policies", "budget_ledger",
    # CS（006/036/037）与 Travel（052）
    "customer_service.tickets", "customer_service.conversations",
    "customer_service.qa_daily_reports", "customer_service.cs_agents",
    "travel.booking_orders", "travel.booking_quotes",
    "travel.booking_events", "travel.booking_webhook_inbox",
    # 台账自身 + checkpointer
    "schema_migrations", "checkpoints",
]

# agent_business（business 库）关键表
_BUSINESS_TABLES = [
    "product.products", "order.orders", "order.order_items",
    "inventory.inventory", "inventory.purchase_orders",
    "customer.customers", "finance.daily_profit",
    "crawler.competitor_products",
    "ai.tool_approval_requests",
    "public.selection_tasks", "public.feedback",
    "public.schema_migrations",
]

# 关键列（表名.列名）：只放已核实的「迁移代际探测列」
_MEMORY_COLUMNS = [
    "llm_models.upstream_model_name",   # 038
    "llm_models.model_kind",            # 039
    "auth.users.dept",                  # 041
    "auth.users.must_change_password",  # 033
    "doc_registry.fixture_set",         # 024
    "tasks.execution_id",               # 任务运行时租约
]

# 扩展：库 → 扩展名
_MEMORY_EXTENSIONS = ["vector"]

_TIMEOUT = 3


def _objects_exist(cfg: dict, qualified: list[str]) -> tuple[list[str], list[str]]:
    """返回 (缺失对象, 已检对象)。qualified 形如 'table:prompts' / 'column:llm_models.upstream_model_name' / 'extension:vector'。"""
    missing: list[str] = []
    checked: list[str] = []
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"], connect_timeout=_TIMEOUT)
    try:
        with conn.cursor() as cur:
            for item in qualified:
                kind, _, name = item.partition(":")
                if kind == "extension":
                    cur.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (name,))
                elif kind == "column":
                    # 支持 [schema.]table.column 三段式：剥离 schema 后按
                    # table_name 查询（information_schema 的 table_name 不含 schema）
                    table, _, col = name.rpartition(".")
                    table = table.rsplit(".", 1)[-1]
                    cur.execute(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = %s AND column_name = %s", (table, col))
                else:
                    cur.execute("SELECT to_regclass(%s)", (name,))
                row = cur.fetchone()
                if row is None or row[0] is None:
                    missing.append(item)
                checked.append(item)
    finally:
        conn.close()
    return missing, checked


def check_critical_objects() -> dict:
    """实查两库关键对象。返回：
    {"status": "ok"|"drift"|"unknown", "checked": N, "missing": [...],
     "databases": {"agent_memory": {...}, "agent_business": {...}}}
    任何连接异常 → "unknown"（与 health 其他观测段的软失败口径一致）。
    """
    result = {"status": "ok", "checked": 0, "missing": [], "databases": {}}
    for label, cfg, tables, columns, extensions in (
        ("agent_memory", MEMORY_DB_CONFIG, _MEMORY_TABLES, _MEMORY_COLUMNS, _MEMORY_EXTENSIONS),
        ("agent_business", BUSINESS_DB_CONFIG, _BUSINESS_TABLES, [], []),
    ):
        qualified = ([f"table:{t}" for t in tables]
                     + [f"column:{c}" for c in columns]
                     + [f"extension:{e}" for e in extensions])
        try:
            missing, checked = _objects_exist(cfg, qualified)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[SchemaConsistency] {label} 校验失败: {exc}")
            result["databases"][label] = {"status": "unknown", "error": str(exc)[:120]}
            result["status"] = "unknown"
            continue
        result["databases"][label] = {
            "status": "drift" if missing else "ok",
            "checked": len(checked),
            "missing": missing,
        }
        result["checked"] += len(checked)
        result["missing"].extend(f"{label}:{m}" for m in missing)
    if result["missing"] and result["status"] != "unknown":
        result["status"] = "drift"
    return result
