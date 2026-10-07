"""PostgresLLMUsageStore — llm_usage 明细的 PostgreSQL 连接层。

与 SQLite 版 `LLMUsageStore` 对外接口完全一致（record/by_trace/list_calls/
dashboard），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
工厂分发见 `llm_usage_store.py::get_llm_usage_store`。

方言映射要点：
  - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`（id 语义不变，
    list_calls 的 ORDER BY id DESC 即插入序）
  - `substr(ts, 1, 10)` PG 原生支持
  - `COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END)`：
    CASE 空串返回 NULL，PG COUNT 自动忽略 NULL，语义一致
软失败原则同 SQLite 版：写入/查询失败只记日志，绝不向上抛异常。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import OBS_DB_PG_CONFIG
from backend.infra.db import engine_for
from backend.observability.llm_usage_store import (
    _MAX_ROWS,
    LLMUsageStore,
    _cfg_enabled,
    _now_iso,
)
from backend.shared.logger import logger

_COLS = ("ts, trace_id, request_id, session_id, user_id, tenant_id, "
         "component, model, provider, "
         "prompt_tokens, completion_tokens, total_tokens, "
         "cached_tokens, reasoning_tokens, cost_usd, "
         "billable_input_tokens, input_cost, cached_input_cost, "
         "output_cost, total_cost, cost_status, currency, "
         "duration_ms, finish_reason, decision")
_COLS += ", run_id, step_id, role, stage"
# STOP C：身份链 + 单价快照随查询透出（trace 回填/看板可直接看到
# upstream 回传名与调用时单价）
_COLS += (", requested_model, upstream_model_id, binding_source, "
          "input_unit_price, output_unit_price, cache_input_unit_price")
# Billing V2（2026-10-07 收口，migration 079）：唯一计费事实链落库列
_COLS += (", billing_schema_version, native_cost, native_currency, "
          "billed_cost_cny, fx_rate, price_version, pricing_source, usage_source, "
          "reasoning_cost_cny, cache_write_cost_cny, tool_call_cost_cny, "
          "reasoning_unit_price, cache_write_unit_price, tool_call_unit_price")


def _optional_decimal(value) -> float | None:
    """单价快照入库转换：缺省/None → NULL（price_unknown 语义），数值 → float。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cost_cny_sql_expr() -> str:
    """行级记账本位币（CNY）SQL 表达式——全仓库唯一折算口径（G2）。

    - Billing V2 行（billing_schema_version>=2）：直接取 ``billed_cost_cny``
      ——汇率已在写入时由 BillingResult 固化，**读层禁止二次换汇**（P0-A 修复点）；
    - legacy 行（版本 1）：按旧 currency 逻辑展示（CNY 原值 / 其余 × 当前
      FX）——历史行币种不可机判，属 legacy_estimated 兼容语义，不伪造精确。
    消费方：dashboard / cost_gauge_snapshot / breakdown / quota.summary。
    """
    from backend.config.budget import BUDGET_FX_USD_CNY

    return (
        "CASE WHEN billing_schema_version >= 2 AND billed_cost_cny IS NOT NULL "
        "THEN billed_cost_cny "
        "WHEN COALESCE(NULLIF(currency, ''), 'USD') = 'CNY' THEN total_cost "
        f"ELSE total_cost * {float(BUDGET_FX_USD_CNY)} END"
    )


class PostgresLLMUsageStore(LLMUsageStore):
    """llm_usage 明细存储的 PostgreSQL 实现（LLMUsageStore 子类）。"""

    def __init__(self, db_path: str = "data/analytics.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("OBS_DB_PG_TABLE_PREFIX", "") + "llm_usage"
        self._write_count = 0
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = engine_for(OBS_DB_PG_CONFIG).raw_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _exec_scalar(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    # ---- 建表（幂等，与 backend/sql/migrations/013_obs_analytics_pg.sql 一致）----

    def _init_db(self):
        t = self._table
        with self._lock, self._conn() as conn:
            conn.cursor().execute(f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    id                BIGSERIAL PRIMARY KEY,
                    ts                TEXT NOT NULL,
                    trace_id          TEXT NOT NULL DEFAULT '',
                    request_id        TEXT NOT NULL DEFAULT '',
                    session_id        TEXT NOT NULL DEFAULT '',
                    user_id           TEXT NOT NULL DEFAULT '',
                    tenant_id         TEXT NOT NULL DEFAULT '',
                    component         TEXT NOT NULL DEFAULT 'llm',
                    model             TEXT NOT NULL DEFAULT '',
                    provider          TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cached_tokens     INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
                    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
                    duration_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
                    finish_reason     TEXT NOT NULL DEFAULT '',
                    decision          TEXT NOT NULL DEFAULT 'primary',
                    created_at        TEXT NOT NULL
                )
            """)
            cur = conn.cursor()
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS request_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS user_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS tenant_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS decision "
                "TEXT NOT NULL DEFAULT 'primary'"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS run_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS step_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS role "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS stage "
                "TEXT NOT NULL DEFAULT ''"
            )
            # 成本计量第一阶段（2026-09-22）：分项成本 + 状态 + 货币。
            # 与 sql/migrations/032_llm_usage_cost_columns.sql 同口径，启动即自愈补列。
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS billable_input_tokens "
                "INTEGER NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS input_cost "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS cached_input_cost "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS output_cost "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS total_cost "
                "DOUBLE PRECISION NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS cost_status "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS currency "
                "TEXT NOT NULL DEFAULT ''"
            )
            # Model Governance STOP C（2026-09-23）：身份链 + 单价快照，
            # 与 sql/migrations/046_llm_usage_identity_billing.sql 同口径。
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS requested_model "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS upstream_model_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS binding_source "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS input_unit_price "
                "NUMERIC(18, 6)"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS output_unit_price "
                "NUMERIC(18, 6)"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS cache_input_unit_price "
                "NUMERIC(18, 6)"
            )
            # 业务归因（M5 / 台账 D5）：skill/tool/域维度成本归因，
            # 与 sql/migrations/055_llm_usage_attribution.sql 同口径，启动即自愈补列。
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS skill_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS tool_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS agent_domain "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_ts ON {t}(ts)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_model ON {t}(model, ts)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_trace ON {t}(trace_id)")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_tenant_user_ts "
                f"ON {t}(tenant_id, user_id, ts DESC)"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_component "
                        f"ON {t}(component, ts)")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_processing "
                f"ON {t}(run_id, step_id, ts)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_attribution "
                f"ON {t}(skill_id, tool_id, agent_domain, ts)"
            )
            # Billing V2（2026-10-07 收口）：唯一计费事实链，与
            # sql/migrations/079_llm_usage_billing_v2.sql 同口径，启动即自愈补列。
            # billed_cost_cny 可空 = legacy 行未回填（读层按旧 currency 逻辑展示）。
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS billing_schema_version "
                "INTEGER NOT NULL DEFAULT 1"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS native_cost "
                "NUMERIC(18, 6) NOT NULL DEFAULT 0"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS native_currency "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS billed_cost_cny "
                "NUMERIC(18, 6)"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS fx_rate NUMERIC(18, 6)"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS price_version "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS pricing_source "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS usage_source "
                "TEXT NOT NULL DEFAULT ''"
            )
            for _cost_col in (
                "reasoning_cost_cny", "cache_write_cost_cny", "tool_call_cost_cny",
            ):
                cur.execute(
                    f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {_cost_col} "
                    "NUMERIC(18, 6) NOT NULL DEFAULT 0"
                )
            for _price_col in (
                "reasoning_unit_price", "cache_write_unit_price",
                "tool_call_unit_price",
            ):
                cur.execute(
                    f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS {_price_col} "
                    "NUMERIC(18, 6)"
                )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_billing "
                f"ON {t}(billing_schema_version, cost_status, ts)"
            )

    # ---- 写入 ----

    def record(self, event: dict[str, Any]) -> bool:
        """写入单次调用用量（LLM / Embedding / Reranker）。失败只记日志返回 False。"""
        if not _cfg_enabled():
            return False
        try:
            now = _now_iso()
            component = event.get("component", "llm")
            with self._lock, self._conn() as conn:
                self._exec(conn, f"""
                    INSERT INTO {self._table} (
                        ts, trace_id, request_id, session_id, user_id, tenant_id,
                        component, model, provider,
                        prompt_tokens, completion_tokens, total_tokens,
                        cached_tokens, reasoning_tokens, cost_usd,
                        billable_input_tokens, input_cost, cached_input_cost,
                        output_cost, total_cost, cost_status, currency,
                        requested_model, upstream_model_id, binding_source,
                        input_unit_price, output_unit_price, cache_input_unit_price,
                        duration_ms, finish_reason, decision,
                        run_id, step_id, role, stage,
                        skill_id, tool_id, agent_domain,
                        billing_schema_version, native_cost, native_currency,
                        billed_cost_cny, fx_rate, price_version, pricing_source,
                        usage_source, reasoning_cost_cny, cache_write_cost_cny,
                        tool_call_cost_cny, reasoning_unit_price,
                        cache_write_unit_price, tool_call_unit_price, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s)
                """, (
                    event.get("timestamp") or now,
                    str(event.get("trace_id") or ""),
                    str(event.get("request_id") or ""),
                    str(event.get("session_id") or ""),
                    str(event.get("user_id") or ""),
                    str(event.get("tenant_id") or ""),
                    str(component),
                    str(event.get("model") or ""),
                    str(event.get("provider") or ""),
                    int(event.get("prompt_tokens") or 0),
                    int(event.get("completion_tokens") or 0),
                    int(event.get("total_tokens") or 0),
                    int(event.get("cached_tokens") or 0),
                    int(event.get("reasoning_tokens") or 0),
                    float(event.get("cost_usd") or 0.0),
                    int(event.get("billable_input_tokens") or 0),
                    float(event.get("input_cost") or 0.0),
                    float(event.get("cached_input_cost") or 0.0),
                    float(event.get("output_cost") or 0.0),
                    float(event.get("total_cost") or event.get("cost_usd") or 0.0),
                    str(event.get("cost_status") or ""),
                    str(event.get("currency") or ""),
                    # 身份链（STOP C C2）：requested/upstream/绑定来源
                    str(event.get("requested_model") or ""),
                    str(event.get("upstream_model_id") or ""),
                    str(event.get("binding_source") or ""),
                    # 单价快照（原生币种，V2 语义）：None 落 NULL
                    _optional_decimal(event.get("input_unit_price")),
                    _optional_decimal(event.get("output_unit_price")),
                    _optional_decimal(event.get("cache_input_unit_price")),
                    float(event.get("duration_ms") or 0.0),
                    str(event.get("finish_reason") or ""),
                    str(event.get("decision") or "primary"),
                    str(event.get("run_id") or ""),
                    str(event.get("step_id") or ""),
                    str(event.get("role") or ""),
                    str(event.get("stage") or ""),
                    str(event.get("skill_id") or ""),
                    str(event.get("tool_id") or ""),
                    str(event.get("agent_domain") or ""),
                    # ── Billing V2（2026-10-07 冻结契约）──────────────────
                    int(event.get("billing_schema_version") or 1),
                    float(event.get("native_cost") or 0.0),
                    str(event.get("native_currency") or ""),
                    _optional_decimal(event.get("billed_cost_cny")),
                    _optional_decimal(event.get("fx_rate")),
                    str(event.get("price_version") or ""),
                    str(event.get("pricing_source") or ""),
                    str(event.get("usage_source") or ""),
                    float(event.get("reasoning_cost_cny") or 0.0),
                    float(event.get("cache_write_cost_cny") or 0.0),
                    float(event.get("tool_call_cost_cny") or 0.0),
                    _optional_decimal(event.get("reasoning_unit_price")),
                    _optional_decimal(event.get("cache_write_unit_price")),
                    _optional_decimal(event.get("tool_call_unit_price")),
                    now,
                ))
                self._write_count += 1
                # 每 2000 次写入做一次裁剪检查（语义同 SQLite 版）
                if self._write_count % 2000 == 0:
                    count = self._exec_scalar(
                        conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]
                    if count > _MAX_ROWS:
                        conn.cursor().execute(
                            f"DELETE FROM {self._table} WHERE id IN "
                            f"(SELECT id FROM {self._table} ORDER BY id ASC LIMIT %s)",
                            (count - _MAX_ROWS,),
                        )
            return True
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] 写入失败: {e}")
            return False

    # ---- 查询 ----

    def by_trace(self, trace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if not trace_id:
            return []
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn,
                    f"""SELECT {_COLS}
                       FROM {self._table} WHERE trace_id = %s ORDER BY ts ASC LIMIT %s""",
                    (trace_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] by_trace({trace_id}) 查询失败: {e}")
            return []

    def by_processing_run(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """返回一次入库运行关联的实际模型调用明细。"""
        if not run_id:
            return []
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn,
                    f"""SELECT {_COLS}
                       FROM {self._table}
                      WHERE run_id = %s
                      ORDER BY ts ASC, id ASC
                      LIMIT %s""",
                    (run_id, min(max(int(limit), 1), 2000)),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] by_processing_run({run_id}) 查询失败: {e}")
            return []

    def list_calls(self, days: int = 7, model: str | None = None,
                   component: str | None = None,
                   limit: int = 20, offset: int = 0) -> dict:
        try:
            cutoff = self._cutoff_iso(days)
            where, params = ["ts >= %s"], [cutoff]
            if model:
                where.append("model = %s")
                params.append(model)
            if component and component != "all":
                where.append("component = %s")
                params.append(component)
            where_sql = " AND ".join(where)
            with self._lock, self._conn() as conn:
                total = self._exec_scalar(
                    conn,
                    f"SELECT COUNT(*) FROM {self._table} WHERE {where_sql}",
                    tuple(params),
                ).fetchone()[0]
                rows = self._exec(
                    conn,
                    f"""SELECT {_COLS}
                       FROM {self._table} WHERE {where_sql}
                       ORDER BY id DESC LIMIT %s OFFSET %s""",
                    (*params, int(limit), int(offset)),
                ).fetchall()
            return {"calls": [dict(r) for r in rows], "total": total}
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] list_calls 失败: {e}")
            return {"calls": [], "total": 0}

    def cost_status_summary(self, hours: float = 24) -> dict:
        """按 cost_status 聚合窗口内的调用分布（对账看板：estimated 占比等）。

        ts 为 ISO 字符串（字典序可比），窗口起点现算，支持小时级窗口；
        软失败：查询异常返回空分布，不影响调用方。
        """
        try:
            import time as _time

            cutoff = _time.strftime(
                "%Y-%m-%dT%H:%M:%S",
                _time.gmtime(_time.time() - float(hours) * 3600),
            )
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn,
                    f"""SELECT COALESCE(cost_status, ''), COUNT(*)
                        FROM {self._table} WHERE ts >= %s
                        GROUP BY 1""",
                    (cutoff,),
                ).fetchall()
            by_status = {r[0] or "missing_status": int(r[1]) for r in rows}
            return {
                "window_hours": float(hours),
                "total_calls": sum(by_status.values()),
                "by_status": by_status,
                "estimated_calls": by_status.get("estimated", 0),
            }
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] cost_status_summary 失败: {e}")
            return {"window_hours": float(hours), "total_calls": 0,
                    "by_status": {}, "estimated_calls": 0}

    def dashboard(self, days: int = 7, component: str | None = None,
                  period: str = "day") -> dict:
        """period="month"（M11 尾项/D11）：趋势按自然月分桶（substr(ts,1,7)），
        时间窗仍由 days 决定（查询时现算，量级小不物化）。day 为默认，行为不变。"""
        if period not in ("day", "month"):
            period = "day"
        bucket_key = "month" if period == "month" else "day"
        bucket_expr = "substr(ts, 1, 7)" if period == "month" else "substr(ts, 1, 10)"
        # Billing V2 感知折算（唯一口径见 cost_cny_sql_expr）：V2 行取
        # billed_cost_cny 不再二次换汇；legacy 行按旧 currency 逻辑兜底。
        cost_cny_expr = (
            f"COALESCE(SUM({cost_cny_sql_expr()}), 0) AS cost_cny"
        )
        empty = {
            "totals": {
                "requests": 0, "calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0,
                "cost_usd": 0.0, "cost_cny": 0.0,
            },
            "daily": [],
            "monthly": [],
            "models": [],
        }
        try:
            cutoff = self._cutoff_iso(days)
            where_base = ["ts >= %s"]
            params_base = [cutoff]
            if component and component != "all":
                where_base.append("component = %s")
                params_base.append(component)
            where_sql = " AND ".join(where_base)

            daily: list[dict] = []
            monthly: list[dict] = []
            with self._lock, self._conn() as conn:
                # ① 总量（requests = 去重轮次；calls = 调用次数）。
                # cost_cny（2026-10-01 本位币口径）：按行币种折 CNY 汇总，
                # 供总览/管理端「成本人民币为主」展示——cost_usd 是历史
                # 混算字段，保留兼容但不再作为展示主口径。
                totals = dict(self._exec(conn, f"""
                    SELECT COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COUNT(*) AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd,
                           {cost_cny_expr}
                    FROM {self._table} WHERE {where_sql}
                """, tuple(params_base)).fetchone())

                # ② 趋势（日/月 × 输入/输出/成本；period=month 时 monthly 有值）
                trend_sql = f"""
                    SELECT {bucket_expr}             AS {bucket_key},
                           COUNT(*)                      AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd,
                           {cost_cny_expr}
                    FROM {self._table} WHERE {where_sql}
                    GROUP BY {bucket_expr}
                    ORDER BY {bucket_key} ASC
                """
                trend = [dict(r) for r in self._exec(
                    conn, trend_sql, tuple(params_base)).fetchall()]
                if period == "month":
                    monthly = trend
                else:
                    daily = trend

                # ③ 按模型细分（Provider/Model 维度；cost_cny 为记账主口径）
                models = [dict(r) for r in self._exec(conn, f"""
                    SELECT provider, model,
                           COUNT(*) AS calls,
                           COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd,
                           {cost_cny_expr}
                    FROM {self._table} WHERE {where_sql}
                    GROUP BY provider, model
                    ORDER BY total_tokens DESC
                """, tuple(params_base)).fetchall()]

                # M11（台账 D11）：成本按币种分组（currency 混算口径修正——
                # CNY 价格行与 USD 行此前混进同一个 cost_usd 总数）
                by_currency = [dict(r) for r in self._exec(conn, f"""
                    SELECT COALESCE(NULLIF(currency, ''), 'USD') AS currency,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens,
                           COALESCE(SUM(total_cost), 0)   AS cost
                    FROM {self._table} WHERE {where_sql}
                    GROUP BY COALESCE(NULLIF(currency, ''), 'USD')
                    ORDER BY cost DESC
                """, tuple(params_base)).fetchall()]

            totals["cost_usd"] = round(totals.get("cost_usd", 0) or 0, 6)
            totals["cost_cny"] = round(float(totals.get("cost_cny") or 0), 6)
            for d in daily:
                d["cost_usd"] = round(d.get("cost_usd", 0) or 0, 6)
                d["cost_cny"] = round(float(d.get("cost_cny") or 0), 6)
            for m in monthly:
                m["cost_usd"] = round(m.get("cost_usd", 0) or 0, 6)
                m["cost_cny"] = round(float(m.get("cost_cny") or 0), 6)
            for m in models:
                m["cost_usd"] = round(m.get("cost_usd", 0) or 0, 6)
                m["cost_cny"] = round(float(m.get("cost_cny") or 0), 6)
            result = {"totals": totals, "daily": daily, "monthly": monthly,
                      "models": models,
                      "period": period}
            result["by_currency"] = by_currency

            return result
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] dashboard 聚合失败: {e}")
            return empty

    def cost_gauge_snapshot(self, cutoff: str) -> list[dict]:
        """账本成本投影（observability/cost_gauge 消费，只读非记账）。

        成本口径与 dashboard() 一致（cost_cny_sql_expr 唯一折算口径，V2 行
        取 billed_cost_cny 不二次换汇）。domain 归并：agent_domain 优先（M5
        域图归因列），空则 component（'llm' 为主图 → 'main'，同 M5 口径），
        再空 'main'。软失败返回空列表，绝不影响账本本身。"""
        cost_cny_expr = f"COALESCE(SUM({cost_cny_sql_expr()}), 0)"
        domain_expr = (
            "COALESCE(NULLIF(agent_domain, ''), "
            "NULLIF(CASE WHEN component = 'llm' THEN 'main' ELSE component END, ''), 'main')"
        )
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(conn, f"""
                    SELECT {domain_expr} AS domain,
                           model,
                           {cost_cny_expr} AS cost_cny
                    FROM {self._table} WHERE ts >= %s
                    GROUP BY 1, 2
                    HAVING {cost_cny_expr} > 0
                    ORDER BY 3 DESC
                    LIMIT 200
                """, (cutoff,)).fetchall()
            return [
                {"domain": str(r["domain"]), "model": str(r["model"]),
                 "cost_cny": round(float(r["cost_cny"]), 6)}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] cost_gauge_snapshot 失败: {e}")
            return []

    # M5 归因列 → group_by 合法维度白名单（防注入：列名不进参数）
    _GROUP_BY_COLUMNS = {
        "user": "user_id", "tenant": "tenant_id", "model": "model",
        "skill": "skill_id", "tool": "tool_id", "domain": "agent_domain",
    }

    def breakdown(self, days: int = 7, group_by: str = "model",
                  component: str | None = None, limit: int = 50) -> list[dict]:
        """M11（台账 D11）：按六维（user/tenant/model/skill/tool/domain）聚合
        用量与成本。列名走白名单（不拼接用户输入），skill/tool/domain 维度
        依赖 M5 归因列（空串=未归因桶）。软失败返回空列表。"""
        column = self._GROUP_BY_COLUMNS.get(group_by)
        if column is None:
            return []
        try:
            cutoff = self._cutoff_iso(days)
            where = ["ts >= %s"]
            params: list = [cutoff]
            if component and component != "all":
                where.append("component = %s")
                params.append(component)
            with self._lock, self._conn() as conn:
                rows = self._exec(conn, f"""
                    SELECT {column} AS bucket,
                           COUNT(*) AS calls,
                           COALESCE(SUM(total_tokens), 0) AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                           COALESCE(SUM(cost_usd), 0)     AS cost_usd,
                           COALESCE(SUM({cost_cny_sql_expr()}), 0) AS cost_cny
                    FROM {self._table} WHERE {' AND '.join(where)}
                    GROUP BY {column}
                    ORDER BY total_tokens DESC
                    LIMIT %s
                """, tuple(params + [limit])).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                # cost_usd 为 deprecated 兼容列（混算语义，2026-10-07 起
                # 不再作为展示口径）；cost_cny = 记账本位币唯一口径
                d["cost_usd"] = round(d.get("cost_usd", 0) or 0, 6)
                d["cost_cny"] = round(float(d.get("cost_cny") or 0), 6)
                out.append(d)
            return out
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] breakdown 聚合失败: {e}")
            return []
