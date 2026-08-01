# Phase 3 演示基础设施 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建报告中心（`/reports`）和告警中心（`/alerts`）独立页面，通过 Workflow Registry 动态驱动业务类型。

**Architecture:** 后端加 `daily_reports` 表 + 扩展现有 inventory API + Demo seed endpoint；前端两个新路由 `/reports`、`/alerts` 复用 Tailwind tokens + lucide-react + 原生 fetch 模式。

**Tech Stack:** Python 3.10+ / FastAPI / SQLite / Next.js 14 / Tailwind 3.4 / lucide-react

## Global Constraints

- 所有 Python 代码用 snake_case + 类型注解 + `from backend.shared.logger import logger`
- 所有前端页面 `'use client'`，用 `useToast()` 不用 `alert()`/`confirm()`
- 前端服务层用对象式 API（`xxxService.method()` 模式），原生 `fetch`
- 样式用 Tailwind 自定义 tokens（`text-text-primary`、`bg-surface-base`、`border-border-subtle`、`shadow-card`、`text-accent`）
- 图标用 `lucide-react`
- 数据库路径约定：`data/xxx.db`，SQLite + threading.Lock
- 路由注册在 `backend/app/api/router.py` 集中管理
- Commit 格式：`type(scope): 中文 subject`

---

## File Structure

```
新建:
backend/
├── seed/demo/
│   └── runner.py                  Demo 场景编排 + daily_reports 存储
├── app/api/routes/
│   └── demo.py                    Demo API: POST /seed, POST /run/{scenario}

修改:
backend/
├── orchestration/workflow/
│   ├── meta.py                    WorkflowMeta 加 category 字段
│   └── decorator.py               @workflow 加 category 参数
├── orchestration/workflows/
│   └── daily_report.py            send_email step 写 daily_reports
├── orchestration/inventory/
│   └── store.py                   加 list_all_cases() 方法
├── app/api/routes/
│   └── inventory_alerts.py        加 stats + patch 端点
└── app/api/
    └── router.py                  注册 demo + daily_reports 路由

新建（前端）:
frontend/src/
├── services/
│   ├── reports.ts                 报告服务层
│   └── alerts.ts                  告警服务层
├── app/reports/
│   ├── page.tsx                   报告中心列表页
│   └── [id]/
│       └── page.tsx               报告详情页
└── app/alerts/
    ├── page.tsx                   告警中心列表页
    └── [id]/
        └── page.tsx               告警详情页

修改（前端）:
frontend/src/components/
└── Sidebar.tsx                    加 /reports、/alerts 入口
```

---

### Task 1: WorkflowMeta 加 category 字段

**Files:**
- Modify: `backend/orchestration/workflow/meta.py`
- Modify: `backend/orchestration/workflow/decorator.py`
- Modify: `backend/orchestration/workflows/daily_report.py`
- Modify: `backend/orchestration/workflows/inventory_alert.py`

**Interfaces:**
- Produces: `WorkflowMeta.category: str = ""` — 新增字段
- Produces: `@workflow(..., category="")` — 装饰器支持 category 参数
- Consumes: `DailyReport` / `InventoryAlert` 声明 category

- [ ] **Step 1: 在 meta.py 的 WorkflowMeta 加 category 字段**

在 [meta.py:19](backend/orchestration/workflow/meta.py#L19) 的 `WorkflowMeta` dataclass 中，`default_kbs` 后添加：

```python
    # 业务类别：用于前端自动分组
    # "report" → 报告中心 / "alert" → 告警中心 / "" → 通用（chat）
    category: str = ""
```

- [ ] **Step 2: 在 decorator.py 的 @workflow 装饰器加 category 参数**

在 [decorator.py:35-43](backend/orchestration/workflow/decorator.py#L35-L43) 的 `workflow()` 函数签名中：

```python
def workflow(
    *,
    name: str,
    description: str = "",
    objects: list[str] | None = None,
    actions: list[str] | None = None,
    examples: list[str] | None = None,
    default_kbs: list[str] | None = None,
    category: str = "",  # 新增
) -> Callable[[type], type]:
```

在 [decorator.py:60-68](backend/orchestration/workflow/decorator.py#L60-L68) 的 `decorator` 函数中，`WorkflowMeta` 构造加 `category=category`：

```python
        meta = WorkflowMeta(
            name=name,
            description=description,
            objects=list(objects or []),
            actions=list(actions or []),
            examples=list(examples or []),
            default_kbs=list(default_kbs or []),
            category=category,
        )
```

- [ ] **Step 3: 给两个 workflow 类声明 category**

[daily_report.py:28-38](backend/orchestration/workflows/daily_report.py#L28-L38) 的 `@workflow()` 加 `category="report"`：

```python
@workflow(
    name="daily_report",
    description="每日经营日报 — SQL 拉数据 + RAG 查模板 + Agent 分析异常 + 报告 + 邮件",
    objects=["日报", "销售", "运营", "经营"],
    actions=["生成", "发送", "导出"],
    examples=[
        "生成今天的经营日报",
        "跑一下今天的销售日报",
        "把今天的日报发给我",
    ],
    default_kbs=["analytics"],
    category="report",
)
```

[inventory_alert.py:41-52](backend/orchestration/workflows/inventory_alert.py#L41-L52) 的 `@workflow()` 加 `category="alert"`：

```python
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
```

- [ ] **Step 4: 验证**

```bash
cd backend && ..\.venv\Scripts\python.exe -c "
from backend.orchestration.workflow.registry import get_workflow_registry
from backend.orchestration.workflows.daily_report import DailyReport
from backend.orchestration.workflows.inventory_alert import InventoryAlert
r = get_workflow_registry()
r.register(DailyReport)
r.register(InventoryAlert)
for m in r.list_metas():
    print(f'{m.name}: category={m.category!r}')
"
```
Expected: `daily_report: category='report'` / `inventory_alert: category='alert'`

- [ ] **Step 5: Commit**

```bash
git add backend/orchestration/workflow/meta.py backend/orchestration/workflow/decorator.py backend/orchestration/workflows/daily_report.py backend/orchestration/workflows/inventory_alert.py
git commit -m "feat(workflow): WorkflowMeta 加 category 字段（report/alert/通用）"
```

---

### Task 2: daily_reports 存储

**Files:**
- Create: `backend/seed/demo/runner.py`

**Interfaces:**
- Produces: `DailyReportStore` class — `save(report)`, `get(id)`, `list(type, page, page_size)`, `get_latest(type)`
- Produces: `get_daily_report_store()` — 模块级单例

- [ ] **Step 1: 创建 backend/seed/demo/runner.py 含 DailyReportStore**

```python
"""seed/demo/runner.py — Demo 场景编排 + daily_reports 存储

设计：
- DailyReportStore：SQLite 单表，存日报完整内容 + KPI 摘要
- DemoRunner：编排 demo 场景（seed / trigger workflow / agent prompt）
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from backend.shared.logger import logger

# ─────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_reports (
    id              TEXT PRIMARY KEY,
    report_date     TEXT NOT NULL,
    report_type     TEXT NOT NULL DEFAULT 'daily_report',
    status          TEXT NOT NULL DEFAULT 'success',
    kpi_summary     TEXT,
    report_content  TEXT NOT NULL,
    trace_id        TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date
    ON daily_reports(report_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_reports_type
    ON daily_reports(report_type, report_date DESC);
"""


class DailyReportStore:
    """日报 SQLite 存储（线程安全）"""

    def __init__(self, db_path: str = "data/daily_reports.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def save(self, report: dict[str, Any]) -> str:
        """保存日报，返回 report id"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO daily_reports
                   (id, report_date, report_type, status, kpi_summary,
                    report_content, trace_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report["id"],
                    report["report_date"],
                    report.get("report_type", "daily_report"),
                    report.get("status", "success"),
                    _json.dumps(report.get("kpi_summary", {}), ensure_ascii=False),
                    report["report_content"],
                    report.get("trace_id", ""),
                    report.get("created_at", datetime.now().isoformat()),
                ),
            )
            conn.commit()
        logger.debug(f"[DailyReportStore] 保存日报 {report['id']}")
        return report["id"]

    def get(self, report_id: str) -> dict[str, Any] | None:
        """获取单条日报详情"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_reports WHERE id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["kpi_summary"] = _json.loads(data.get("kpi_summary") or "{}")
        except Exception:
            data["kpi_summary"] = {}
        return data

    def list(
        self,
        report_type: str = "daily_report",
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """列出日报列表（不含完整 content，仅摘要）"""
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT id, report_date, report_type, status, kpi_summary,
                          trace_id, created_at
                   FROM daily_reports
                   WHERE report_type = ?
                   ORDER BY report_date DESC LIMIT ? OFFSET ?""",
                (report_type, page_size, offset),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["kpi_summary"] = _json.loads(d.get("kpi_summary") or "{}")
            except Exception:
                d["kpi_summary"] = {}
            results.append(d)
        return results

    def get_latest(self, report_type: str = "daily_report") -> dict[str, Any] | None:
        """获取最新一条日报（含完整 content）"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM daily_reports
                   WHERE report_type = ? AND status = 'success'
                   ORDER BY report_date DESC LIMIT 1""",
                (report_type,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["kpi_summary"] = _json.loads(data.get("kpi_summary") or "{}")
        except Exception:
            data["kpi_summary"] = {}
        return data


# 模块级单例
_store: DailyReportStore | None = None


def get_daily_report_store() -> DailyReportStore:
    global _store
    if _store is None:
        _store = DailyReportStore()
    return _store
```

- [ ] **Step 2: 验证 DailyReportStore**

```bash
cd backend && ..\.venv\Scripts\python.exe -c "
from backend.seed.demo.runner import get_daily_report_store
store = get_daily_report_store()
store.save({
    'id': 'test-001',
    'report_date': '2026-08-01',
    'report_content': '# 测试日报\nhello',
    'kpi_summary': {'total_products': 10, 'alert_count': 2},
    'trace_id': 'abc123',
})
r = store.get('test-001')
print('saved:', r['id'], r['report_date'])
print('kpi:', r['kpi_summary'])
latest = store.get_latest()
print('latest:', latest['id'])
items = store.list(page=1, page_size=5)
print('list count:', len(items))
"
```
Expected: 正常输出 saved/latest/list

- [ ] **Step 3: Commit**

```bash
git add backend/seed/demo/runner.py
git commit -m "feat(demo): DailyReportStore — 日报 SQLite 持久化存储"
```

---

### Task 3: DailyReport.send_email 写 daily_reports

**Files:**
- Modify: `backend/orchestration/workflows/daily_report.py`

**Interfaces:**
- Consumes: `DailyReportStore` from `backend.seed.demo.runner`
- Produces: send_email step 新增写 daily_reports 逻辑

- [ ] **Step 1: 改造 send_email step**

在 [daily_report.py:128-157](backend/orchestration/workflows/daily_report.py#L128-L157) 的 `send_email` 方法中，发邮件前加写 daily_reports：

将整个 `send_email` 方法替换为：

```python
    @step(
        depends_on=["generate_report"],
        timeout_sec=60,
        retry=2,
        on_error="abort",
    )
    async def send_email(self, ctx):
        """Step 7: 发邮件 + 写 daily_reports 表"""
        logger.info("[DailyReport] Step send_email 开始")
        report_output = ctx.outputs.get("generate_report", {}).get("report", {})
        if isinstance(report_output, str):
            body = report_output
        elif isinstance(report_output, dict):
            body = (
                f"## 销售摘要\n{report_output.get('sales_summary', '')}\n\n"
                f"## 库存预警\n{report_output.get('inventory_alerts', '')}\n\n"
                f"## Agent 分析\n{report_output.get('agent_analysis', '')}\n"
            )
        else:
            body = str(report_output)

        from datetime import date
        today = date.today().isoformat()

        # 提取 KPI 摘要（从上游 step output）
        sales = ctx.outputs.get("fetch_sales", {}).get("sales", [])
        inventory = ctx.outputs.get("fetch_inventory", {}).get("inventory", [])
        alerting = [i for i in inventory if isinstance(i, dict) and i.get("current_qty", 999) < i.get("min_qty", 0)]

        kpi_summary = {
            "total_products": len(inventory),
            "alert_count": len(alerting),
            "sales_records": len(sales),
            "report_date": today,
        }

        # 写 daily_reports 表
        from backend.seed.demo.runner import get_daily_report_store
        report_store = get_daily_report_store()
        report_store.save({
            "id": ctx.run_id,
            "report_date": today,
            "report_content": f"# 经营日报 {today}\n\n{body}",
            "kpi_summary": kpi_summary,
            "trace_id": ctx.trace_id or "",
            "status": "success",
        })
        logger.info(f"[DailyReport] 日报已写入 daily_reports: {ctx.run_id}")

        # 发邮件
        result = await call_email({
            "to": ["ops@demo.local", "ceo@demo.local"],
            "subject": f"[经营日报] {today}",
            "body": f"# 经营日报 {today}\n\n{body}",
        })
        return {"email": result, "report_id": ctx.run_id}
```

- [ ] **Step 2: 验证语法**

```bash
cd backend && ..\.venv\Scripts\python.exe -m py_compile backend/orchestration/workflows/daily_report.py
```
Expected: 无输出（编译成功）

- [ ] **Step 3: Commit**

```bash
git add backend/orchestration/workflows/daily_report.py
git commit -m "feat(workflow): daily_report send_email 写入 daily_reports 表"
```

---

### Task 4: InventoryStore 加 list_all_cases + API 扩展

**Files:**
- Modify: `backend/orchestration/inventory/store.py`
- Modify: `backend/app/api/routes/inventory_alerts.py`

**Interfaces:**
- Produces: `InventoryStore.list_all_cases(status, level, page, page_size)` — 带过滤的列表
- Produces: `GET /api/inventory/stats` — 告警统计
- Produces: `PATCH /api/inventory/cases/{case_id}` — 更新 case 状态

- [ ] **Step 1: store.py 加 list_all_cases 方法**

在 [store.py](backend/orchestration/inventory/store.py) 的 `InventoryStore` 类中，`list_open_cases()` 方法后添加：

```python
    def list_all_cases(
        self,
        status: str = "",
        level: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """列出所有 case（带过滤 + 分页）

        Returns:
            (cases, total)
        """
        offset = (page - 1) * page_size
        where_clauses = []
        params: list[Any] = []

        if status:
            if status == "active":
                where_clauses.append("status IN ('open', 'acknowledged')")
            elif status == "history":
                where_clauses.append("status IN ('resolved', 'closed')")
            else:
                where_clauses.append("status = ?")
                params.append(status)
        if level:
            where_clauses.append("current_level = ?")
            params.append(level)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        order_sql = (
            "ORDER BY CASE current_level "
            "WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, "
            "last_detected_at DESC"
        )

        with self._lock, self._conn() as conn:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM inventory_alert_cases {where_sql}",
                params,
            ).fetchone()
            total = count_row[0] if count_row else 0

            rows = conn.execute(
                f"SELECT * FROM inventory_alert_cases {where_sql} "
                f"{order_sql} LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()

        return [dict(r) for r in rows], total

    def get_stats(self) -> dict[str, int]:
        """告警统计：按 level 分组计数"""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT current_level, COUNT(*) as cnt
                   FROM inventory_alert_cases
                   WHERE status IN ('open', 'acknowledged')
                   GROUP BY current_level"""
            ).fetchall()
        stats = {"critical": 0, "warning": 0, "info": 0, "resolved": 0}
        for r in rows:
            level = r[0] or "info"
            if level in stats:
                stats[level] = r[1]
        # resolved 计数
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM inventory_alert_cases "
                "WHERE status IN ('resolved', 'closed')"
            ).fetchone()
        stats["resolved"] = row[0] if row else 0
        return stats
```

- [ ] **Step 2: inventory_alerts.py 加 stats + patch 端点 + 改造 GET /cases**

在 [inventory_alerts.py](backend/app/api/routes/inventory_alerts.py) 的 `GET /cases` 端点后添加 stats 端点：

```python
@router.get("/stats")
async def get_alert_stats(
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """告警统计（按级别分组）"""
    return {"stats": store.get_stats()}
```

改 `GET /cases` 支持更多过滤参数。将现有 `list_cases` 替换为：

```python
@router.get("/cases")
async def list_cases(
    status: str = "",
    level: str = "",
    page: int = 1,
    page_size: int = 20,
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """列出所有 alert case（可按 status + level 过滤）"""
    cases, total = store.list_all_cases(
        status=status, level=level, page=page, page_size=page_size
    )
    return {"cases": cases, "total": total, "page": page, "page_size": page_size}
```

添加 PATCH 端点（在 `manual_resolve_case` 后）：

```python
@router.patch("/cases/{case_id}")
async def update_case_status(
    case_id: int,
    body: dict = Body(...),
    store: InventoryStore = Depends(get_store),
) -> dict[str, Any]:
    """更新 case 状态（acknowledged / resolved / closed）"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")

    new_status = body.get("status")
    resolution_type = body.get("resolution_type")
    valid_statuses = {"acknowledged", "resolved", "closed"}

    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status: {new_status}, must be one of {valid_statuses}"
        )

    if new_status == "resolved" and not resolution_type:
        resolution_type = "MANUAL_RESOLVED"

    store.update_case_status(case_id, new_status, resolution_type=resolution_type)

    # 记录事件
    store.insert_event({
        "case_id": case_id,
        "event_type": new_status,
        "from_state": case.get("current_state"),
        "to_state": case.get("current_state"),
        "qty": None,
        "stock_days": None,
        "reason": [f"状态更新为 {new_status}"],
        "notified": False,
    })

    return {"updated": True, "case_id": case_id, "status": new_status}
```

- [ ] **Step 3: 验证语法**

```bash
cd backend && ..\.venv\Scripts\python.exe -m py_compile backend/orchestration/inventory/store.py backend/app/api/routes/inventory_alerts.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/orchestration/inventory/store.py backend/app/api/routes/inventory_alerts.py
git commit -m "feat(inventory): list_all_cases 分页过滤 + stats + PATCH 端点"
```

---

### Task 5: Demo API 端点

**Files:**
- Create: `backend/app/api/routes/demo.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/seed/demo/runner.py`

**Interfaces:**
- Consumes: `DailyReportStore`, `WorkflowRegistry`, `WorkflowScheduler`
- Produces: `POST /api/demo/seed` — 导入 demo 数据
- Produces: `POST /api/demo/run/{scenario_id}` — 触发场景

- [ ] **Step 1: 在 runner.py 添加 DemoRunner 类**

在 `runner.py` 末尾添加：

```python
# ─────────────────────────────────────────────────────────────
# DemoRunner
# ─────────────────────────────────────────────────────────────


class DemoRunner:
    """编排 Demo 场景（seed / trigger / agent prompt）"""

    def seed_data(self) -> dict[str, Any]:
        """导入所有 demo 数据"""
        from backend.seed.demo.run import run_seed
        return run_seed(verbose=True)

    async def run_daily_report(self, inputs: dict | None = None) -> dict[str, Any]:
        """触发 daily_report workflow"""
        from backend.orchestration.workflow.scheduler import get_workflow_scheduler
        scheduler = get_workflow_scheduler()
        ctx = await scheduler.run_now("daily_report", inputs or {})
        return {
            "scenario": "daily_report",
            "run_id": ctx.run_id,
            "status": ctx.status,
            "trace_id": ctx.trace_id,
            "outputs_keys": list(ctx.outputs.keys()),
            "duration_ms": ctx.duration_ms,
            "error": ctx.error,
        }

    async def run_inventory_alert(self, inputs: dict | None = None) -> dict[str, Any]:
        """触发 inventory_alert workflow"""
        from backend.orchestration.workflow.scheduler import get_workflow_scheduler
        scheduler = get_workflow_scheduler()
        ctx = await scheduler.run_now("inventory_alert", inputs or {})
        return {
            "scenario": "inventory_alert",
            "run_id": ctx.run_id,
            "status": ctx.status,
            "trace_id": ctx.trace_id,
            "outputs_keys": list(ctx.outputs.keys()),
            "duration_ms": ctx.duration_ms,
            "error": ctx.error,
        }

    async def run_scenario(self, scenario_id: str) -> dict[str, Any]:
        """分发到具体场景"""
        scenario_map = {
            "daily_report": self.run_daily_report,
            "inventory_alert": self.run_inventory_alert,
        }
        if scenario_id in scenario_map:
            return await scenario_map[scenario_id]()

        # Agent 场景：返回 /agent 跳转链接
        agent_scenarios = {
            "sales_anomaly": "上周为什么华为 Mate 60 销量跌了？",
            "product_optimization": "帮我看看哪些商品标题不行，怎么改？",
        }
        if scenario_id in agent_scenarios:
            return {
                "scenario": scenario_id,
                "mode": "agent",
                "prompt": agent_scenarios[scenario_id],
                "agent_url": f"/agent?prompt={agent_scenarios[scenario_id]}",
            }
        raise ValueError(f"未知场景: {scenario_id}")


_demo_runner: DemoRunner | None = None


def get_demo_runner() -> DemoRunner:
    global _demo_runner
    if _demo_runner is None:
        _demo_runner = DemoRunner()
    return _demo_runner
```

- [ ] **Step 2: 创建 demo.py API 路由**

创建 `backend/app/api/routes/demo.py`：

```python
"""app/api/routes/demo.py — Demo API

端点：
- POST /api/demo/seed              导入 demo 数据
- POST /api/demo/run/{scenario_id}  触发单个 demo 场景
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from backend.seed.demo.runner import get_demo_runner
from backend.shared.logger import logger

router = APIRouter(prefix="/demo", tags=["Demo"])


@router.post("/seed")
async def seed_demo_data() -> dict[str, Any]:
    """导入所有 demo 数据（商品 + 销售 + 阈值 + 策略）"""
    try:
        runner = get_demo_runner()
        result = runner.seed_data()
        return {"ok": True, "result": result}
    except Exception as e:
        logger.warning(f"[Demo] seed 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run/{scenario_id}")
async def run_demo_scenario(scenario_id: str) -> dict[str, Any]:
    """触发单个 demo 场景

    scenario_id:
    - daily_report: 经营日报（Workflow）
    - inventory_alert: 库存预警（Workflow）
    - sales_anomaly: 销量异常分析（Agent /chat 跳转）
    - product_optimization: 商品优化建议（Agent /chat 跳转）
    """
    valid = {"daily_report", "inventory_alert", "sales_anomaly", "product_optimization"}
    if scenario_id not in valid:
        raise HTTPException(
            status_code=404,
            detail=f"未知场景: {scenario_id}，可选: {valid}"
        )

    try:
        runner = get_demo_runner()
        result = await runner.run_scenario(scenario_id)
        return {"ok": True, **result}
    except Exception as e:
        logger.warning(f"[Demo] run {scenario_id} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **Step 3: 注册路由**

在 [router.py](backend/app/api/router.py) 的 import 块加：

```python
    demo,
```

在路由注册块（`api_router.include_router(inventory_alerts.router)` 后）加：

```python
api_router.include_router(demo.router)
```

- [ ] **Step 4: 验证语法**

```bash
cd backend && ..\.venv\Scripts\python.exe -m py_compile backend/seed/demo/runner.py backend/app/api/routes/demo.py backend/app/api/router.py
```

- [ ] **Step 5: Commit**

```bash
git add backend/seed/demo/runner.py backend/app/api/routes/demo.py backend/app/api/router.py
git commit -m "feat(demo): Demo API — seed + run/{scenario_id} 端点"
```

---

### Task 6: 前端服务层（reports.ts + alerts.ts）

**Files:**
- Create: `frontend/src/services/reports.ts`
- Create: `frontend/src/services/alerts.ts`

**Interfaces:**
- Produces: `reportService` — `getReports`, `getLatestReport`, `getReport`
- Produces: `alertService` — `getStats`, `getAlerts`, `getAlert`, `patchAlert`

- [ ] **Step 1: 创建 reports.ts**

```typescript
// Service — Reports API

const BASE = '/api'

export interface DailyReportSummary {
  id: string
  report_date: string
  report_type: string
  status: string
  kpi_summary: {
    total_products?: number
    alert_count?: number
    sales_records?: number
    report_date?: string
  }
  trace_id: string
  created_at: string
}

export interface DailyReportDetail extends DailyReportSummary {
  report_content: string
}

export interface ReportListResult {
  reports: DailyReportSummary[]
  total: number
  page: number
  page_size: number
}

export const reportService = {
  async getReports(params?: {
    type?: string; page?: number; page_size?: number
  }): Promise<ReportListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/reports?${qs}`)
    return res.json()
  },

  async getLatestReport(type: string = 'daily_report'): Promise<{ report: DailyReportDetail | null }> {
    const res = await fetch(`${BASE}/reports/latest?type=${type}`)
    return res.json()
  },

  async getReport(id: string): Promise<{ report: DailyReportDetail }> {
    const res = await fetch(`${BASE}/reports/${id}`)
    if (!res.ok) throw new Error(`Report ${id} not found`)
    return res.json()
  },
}
```

- [ ] **Step 2: 创建 alerts.ts**

```typescript
// Service — Alerts API

const BASE = '/api/inventory'

export interface AlertStats {
  critical: number
  warning: number
  info: number
  resolved: number
}

export interface AlertCase {
  id: number
  product_id: string
  current_state: string
  current_level: string
  status: string
  resolution_type: string | null
  first_detected_at: string
  last_detected_at: string
  last_notified_at: string | null
  created_at: string
  updated_at: string
}

export interface AlertEvent {
  id: number
  case_id: number
  event_type: string
  from_state: string | null
  to_state: string | null
  qty: number | null
  stock_days: number | null
  reason: string[]
  notified: boolean
  created_at: string
}

export interface AlertDetail {
  case: AlertCase
  events: AlertEvent[]
}

export interface AlertListResult {
  cases: AlertCase[]
  total: number
  page: number
  page_size: number
}

export const alertService = {
  async getStats(): Promise<{ stats: AlertStats }> {
    const res = await fetch(`${BASE}/stats`)
    return res.json()
  },

  async getAlerts(params?: {
    status?: string; level?: string; page?: number; page_size?: number
  }): Promise<AlertListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/cases?${qs}`)
    return res.json()
  },

  async getAlert(caseId: number): Promise<AlertDetail> {
    const res = await fetch(`${BASE}/cases/${caseId}`)
    if (!res.ok) throw new Error(`Alert ${caseId} not found`)
    return res.json()
  },

  async patchAlert(caseId: number, body: {
    status?: string; resolution_type?: string
  }): Promise<{ updated: boolean; case_id: number; status: string }> {
    const res = await fetch(`${BASE}/cases/${caseId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(err.detail || 'Update failed')
    }
    return res.json()
  },
}
```

- [ ] **Step 3: 验证 TypeScript**

```bash
cd frontend && npx tsc --noEmit
```
Expected: 无新增错误（现有错误可忽略）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/reports.ts frontend/src/services/alerts.ts
git commit -m "feat(frontend): reports + alerts 服务层"
```

---

### Task 7: Backend Reports API 端点

**Files:**
- Create: `backend/app/api/routes/reports.py`
- Modify: `backend/app/api/router.py`

**Interfaces:**
- Consumes: `DailyReportStore` from `backend.seed.demo.runner`
- Produces: `GET /api/reports`, `GET /api/reports/latest`, `GET /api/reports/{report_id}`

- [ ] **Step 1: 创建 reports.py**

```python
"""app/api/routes/reports.py — 报告中心 API

端点：
- GET /api/reports             报告列表（按 type 过滤）
- GET /api/reports/latest      最新报告（含完整 content）
- GET /api/reports/{report_id} 报告详情
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.seed.demo.runner import get_daily_report_store

router = APIRouter(prefix="/reports", tags=["报告中心"])


@router.get("")
async def list_reports(
    type: str = Query(default="daily_report"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """报告列表（按类型 + 分页）"""
    store = get_daily_report_store()
    reports = store.list(report_type=type, page=page, page_size=page_size)
    return {"reports": reports, "page": page, "page_size": page_size}


@router.get("/latest")
async def get_latest_report(
    type: str = Query(default="daily_report"),
) -> dict[str, Any]:
    """最新一条报告（含完整内容 + KPI 摘要）"""
    store = get_daily_report_store()
    report = store.get_latest(report_type=type)
    return {"report": report}


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict[str, Any]:
    """报告详情"""
    store = get_daily_report_store()
    report = store.get(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id} not found")
    return {"report": report}
```

- [ ] **Step 2: 注册路由**

在 [router.py](backend/app/api/router.py) 的 import 块加：

```python
    reports,
```

在路由注册块加：

```python
api_router.include_router(reports.router)
```

- [ ] **Step 3: 验证语法**

```bash
cd backend && ..\.venv\Scripts\python.exe -m py_compile backend/app/api/routes/reports.py backend/app/api/router.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes/reports.py backend/app/api/router.py
git commit -m "feat(api): Reports API — 列表/最新/详情"
```

---

### Task 8: 前端 /reports 列表页

**Files:**
- Create: `frontend/src/app/reports/page.tsx`

**Interfaces:**
- Consumes: `reportService` from `@/services/reports`
- Consumes: `get_workflow_registry` API for dynamic tabs

- [ ] **Step 1: 创建 reports/page.tsx**

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { FileText, AlertTriangle, TrendingUp, Package, ChevronRight } from 'lucide-react'
import { reportService, type DailyReportSummary } from '@/services/reports'
import { clsx } from 'clsx'

interface WorkflowMeta {
  name: string; description: string; category: string
}

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  success: { label: '成功', cls: 'bg-green-100 text-green-700' },
  failed: { label: '失败', cls: 'bg-red-100 text-red-700' },
  partial: { label: '部分成功', cls: 'bg-yellow-100 text-yellow-700' },
}

export default function ReportsPage() {
  const router = useRouter()
  const [reports, setReports] = useState<DailyReportSummary[]>([])
  const [latestKpi, setLatestKpi] = useState<DailyReportSummary['kpi_summary'] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function load() {
      const [listRes, latestRes] = await Promise.all([
        reportService.getReports({ type: 'daily_report' }),
        reportService.getLatestReport('daily_report'),
      ])
      setReports(listRes.reports || [])
      if (latestRes.report) {
        setLatestKpi(latestRes.report.kpi_summary)
      }
      setLoading(false)
    }
    load()
  }, [])

  const weekDay = (dateStr: string) => {
    const d = new Date(dateStr)
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">报告中心</h1>
          <p className="text-xs text-text-muted mt-1">Workflow 自动生成 · 按日期归档</p>
        </div>

        {/* KPI Summary Cards */}
        {latestKpi && (
          <div className="grid grid-cols-4 gap-4 mb-8">
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <Package size={14} /> 商品总数
              </div>
              <div className="text-2xl font-semibold text-text-primary">{latestKpi.total_products ?? '-'}</div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <AlertTriangle size={14} /> 异常库存
              </div>
              <div className={clsx('text-2xl font-semibold', (latestKpi.alert_count ?? 0) > 0 ? 'text-red-500' : 'text-text-primary')}>
                {latestKpi.alert_count ?? '-'}
              </div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <TrendingUp size={14} /> 销售记录
              </div>
              <div className="text-2xl font-semibold text-text-primary">{latestKpi.sales_records ?? '-'}</div>
            </div>
            <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 text-xs text-text-muted mb-1">
                <FileText size={14} /> 日报日期
              </div>
              <div className="text-lg font-semibold text-text-primary">{latestKpi.report_date ?? '-'}</div>
            </div>
          </div>
        )}

        {/* Report List */}
        <div className="space-y-2">
          {loading && <p className="text-xs text-text-muted py-4">加载中...</p>}
          {!loading && reports.length === 0 && (
            <p className="text-xs text-text-muted py-4">暂无报告，请先运行日报 Workflow</p>
          )}
          {reports.map(r => {
            const status = STATUS_MAP[r.status] || STATUS_MAP.success
            return (
              <button
                key={r.id}
                onClick={() => router.push(`/reports/${r.id}`)}
                className="w-full bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow text-left"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <FileText size={18} className="text-accent" />
                    <div>
                      <span className="text-sm text-text-primary">
                        {r.report_date} · {weekDay(r.report_date)}
                      </span>
                      <span className={clsx('ml-2 text-[10px] px-2 py-0.5 rounded-full', status.cls)}>
                        {status.label}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-text-muted">
                    {r.kpi_summary?.alert_count ? (
                      <span className="text-red-500">⚠ {r.kpi_summary.alert_count} 异常</span>
                    ) : (
                      <span className="text-green-600">✅ 正常</span>
                    )}
                    <ChevronRight size={14} />
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 验证 TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/reports/page.tsx
git commit -m "feat(frontend): /reports 报告中心列表页"
```

---

### Task 9: 前端 /reports/[id] 详情页

**Files:**
- Create: `frontend/src/app/reports/[id]/page.tsx`

**Interfaces:**
- Consumes: `reportService.getReport(id)` + existing trace page

- [ ] **Step 1: 创建 reports/[id]/page.tsx**

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { reportService, type DailyReportDetail } from '@/services/reports'

export default function ReportDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [report, setReport] = useState<DailyReportDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [showTech, setShowTech] = useState(false)

  useEffect(() => {
    reportService.getReport(id).then(r => {
      setReport(r.report)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [id])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">加载中...</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">报告未找到</p>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.back()} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">
              经营日报 · {report.report_date}
            </h1>
            <p className="text-xs text-text-muted">
              生成时间: {report.created_at?.slice(0, 19)} · 状态: {report.status}
            </p>
          </div>
        </div>

        {/* Content: Business View */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-6 mb-4">
          <div className="prose prose-sm max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {report.report_content}
            </ReactMarkdown>
          </div>
        </div>

        {/* Tech View (collapsible) */}
        <div className="bg-surface-base rounded-xl border border-border-subtle">
          <button
            onClick={() => setShowTech(!showTech)}
            className="w-full flex items-center justify-between px-6 py-3 text-sm text-text-secondary hover:bg-black/5 rounded-xl transition-colors"
          >
            <span className="flex items-center gap-2">
              📐 技术视图
            </span>
            {showTech ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showTech && (
            <div className="px-6 pb-4 space-y-3">
              <div className="text-xs text-text-muted space-y-1">
                <p>Report ID: <code className="text-[11px] bg-black/5 px-1 rounded">{report.id}</code></p>
                <p>KPI Summary: <code className="text-[11px] bg-black/5 px-1 rounded">{JSON.stringify(report.kpi_summary)}</code></p>
              </div>
              {report.trace_id && (
                <a
                  href={`/observability/traces/${report.trace_id}`}
                  target="_blank"
                  className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                >
                  查看完整 Trace <ExternalLink size={12} />
                </a>
              )}
              {!report.trace_id && (
                <p className="text-xs text-text-muted">无关联 Trace</p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 验证 TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/reports/\[id\]/page.tsx
git commit -m "feat(frontend): /reports/[id] 报告详情页（业务+技术双视图）"
```

---

### Task 10: 前端 /alerts 列表页

**Files:**
- Create: `frontend/src/app/alerts/page.tsx`

**Interfaces:**
- Consumes: `alertService` from `@/services/alerts`

- [ ] **Step 1: 创建 alerts/page.tsx**

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AlertTriangle, AlertCircle, Info, CheckCircle2, ChevronRight, RefreshCw } from 'lucide-react'
import { alertService, type AlertCase, type AlertStats } from '@/services/alerts'
import { clsx } from 'clsx'

const LEVEL_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  critical: { icon: <AlertCircle size={16} />, label: '紧急', color: 'text-red-500 bg-red-50 border-red-200' },
  warning: { icon: <AlertTriangle size={16} />, label: '警告', color: 'text-yellow-500 bg-yellow-50 border-yellow-200' },
  info: { icon: <Info size={16} />, label: '提醒', color: 'text-blue-500 bg-blue-50 border-blue-200' },
}

const STATUS_MAP: Record<string, string> = {
  open: 'OPEN',
  acknowledged: 'ACK',
  resolved: '已解决',
  closed: '已关闭',
}

export default function AlertsPage() {
  const router = useRouter()
  const [stats, setStats] = useState<AlertStats>({ critical: 0, warning: 0, info: 0, resolved: 0 })
  const [alerts, setAlerts] = useState<AlertCase[]>([])
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'active' | 'history'>('active')

  async function loadData() {
    setLoading(true)
    const [statsRes, alertsRes] = await Promise.all([
      alertService.getStats(),
      alertService.getAlerts({ status: tab === 'active' ? 'active' : 'history' }),
    ])
    setStats(statsRes.stats)
    setAlerts(alertsRes.cases || [])
    setLoading(false)
  }

  useEffect(() => { loadData() }, [tab])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">告警中心</h1>
            <p className="text-xs text-text-muted mt-1">库存预警实时监控 · 状态机管理</p>
          </div>
          <button onClick={loadData} className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors">
            <RefreshCw size={14} /> 刷新
          </button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-4 gap-4 mb-6">
          {[
            { key: 'critical', label: '紧急', icon: <AlertCircle size={16} />, cls: 'border-red-200 bg-red-50' },
            { key: 'warning', label: '警告', icon: <AlertTriangle size={16} />, cls: 'border-yellow-200 bg-yellow-50' },
            { key: 'info', label: '提醒', icon: <Info size={16} />, cls: 'border-blue-200 bg-blue-50' },
            { key: 'resolved', label: '已解决', icon: <CheckCircle2 size={16} />, cls: 'border-green-200 bg-green-50' },
          ].map(s => (
            <div key={s.key} className={clsx('rounded-xl border p-4', s.cls)}>
              <div className="flex items-center gap-2 text-xs mb-1">{s.icon} {s.label}</div>
              <div className="text-2xl font-semibold text-text-primary">{stats[s.key as keyof AlertStats] ?? 0}</div>
            </div>
          ))}
        </div>

        {/* Tab + Filters */}
        <div className="flex items-center gap-4 mb-4">
          <div className="flex rounded-lg bg-black/5 p-0.5">
            <button
              onClick={() => setTab('active')}
              className={clsx('px-3 py-1.5 text-xs rounded-md transition-colors',
                tab === 'active' ? 'bg-white text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary')}
            >
              ● 活跃告警
            </button>
            <button
              onClick={() => setTab('history')}
              className={clsx('px-3 py-1.5 text-xs rounded-md transition-colors',
                tab === 'history' ? 'bg-white text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary')}
            >
              ○ 历史告警
            </button>
          </div>
        </div>

        {/* Alert List */}
        <div className="space-y-2">
          {loading && <p className="text-xs text-text-muted py-4">加载中...</p>}
          {!loading && alerts.length === 0 && (
            <p className="text-xs text-text-muted py-4">
              {tab === 'active' ? '当前无活跃告警 ✅' : '暂无历史告警'}
            </p>
          )}
          {alerts.map(a => {
            const levelCfg = LEVEL_CONFIG[a.current_level] || LEVEL_CONFIG.info
            return (
              <button
                key={a.id}
                onClick={() => router.push(`/alerts/${a.id}`)}
                className="w-full bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow text-left"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <span className={clsx('w-9 h-9 rounded-lg flex items-center justify-center border', levelCfg.color)}>
                      {levelCfg.icon}
                    </span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-text-primary">{a.product_id}</span>
                        <span className={clsx('text-[10px] px-2 py-0.5 rounded-full', levelCfg.color)}>
                          {levelCfg.label}
                        </span>
                        <span className="text-[10px] bg-black/5 text-text-muted px-2 py-0.5 rounded-full">
                          {STATUS_MAP[a.status] || a.status}
                        </span>
                      </div>
                      <div className="text-xs text-text-muted mt-0.5">
                        当前库存: {a.current_state} · 检测时间: {a.first_detected_at?.slice(0, 10)}
                      </div>
                    </div>
                  </div>
                  <ChevronRight size={14} className="text-text-muted" />
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 验证 TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/alerts/page.tsx
git commit -m "feat(frontend): /alerts 告警中心列表页（统计卡片+活跃/历史切换）"
```

---

### Task 11: 前端 /alerts/[id] 详情页

**Files:**
- Create: `frontend/src/app/alerts/[id]/page.tsx`

**Interfaces:**
- Consumes: `alertService.getAlert(id)`, `alertService.patchAlert(id, body)`

- [ ] **Step 1: 创建 alerts/[id]/page.tsx**

```typescript
'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, AlertCircle, AlertTriangle, CheckCircle2, XCircle, ExternalLink } from 'lucide-react'
import { alertService, type AlertDetail, type AlertEvent } from '@/services/alerts'
import { clsx } from 'clsx'

const EVENT_LABELS: Record<string, string> = {
  created: 'CREATE — 首次触发',
  upgraded: 'UPGRADE — 状态升级',
  reminded: 'REMIND — 持续提醒',
  resolved: 'RESOLVE — 已恢复',
  reopened: 'REOPEN — 重新激活',
  acknowledged: '确认告警',
  closed: '关闭告警',
}

export default function AlertDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [detail, setDetail] = useState<AlertDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState(false)

  useEffect(() => {
    alertService.getAlert(Number(id)).then(d => {
      setDetail(d)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [id])

  async function handleAction(action: string) {
    setActing(true)
    try {
      if (action === 'acknowledged') {
        await alertService.patchAlert(Number(id), { status: 'acknowledged' })
      } else if (action === 'resolved') {
        await alertService.patchAlert(Number(id), { status: 'resolved', resolution_type: 'MANUAL_RESOLVED' })
      } else if (action === 'closed') {
        await alertService.patchAlert(Number(id), { status: 'closed', resolution_type: 'MANUAL_IGNORED' })
      }
      // refresh
      const d = await alertService.getAlert(Number(id))
      setDetail(d)
    } catch (e) {
      console.error('Action failed:', e)
    } finally {
      setActing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">加载中...</p>
      </div>
    )
  }

  if (!detail) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-sm text-text-muted">告警未找到</p>
      </div>
    )
  }

  const c = detail.case

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button onClick={() => router.back()} className="p-1.5 rounded-lg hover:bg-black/5 text-text-muted">
            <ArrowLeft size={18} />
          </button>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">{c.product_id} · 库存预警</h1>
            <p className="text-xs text-text-muted">Case #{c.id} · 首次检测: {c.first_detected_at?.slice(0, 19)}</p>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-6">
          {/* Left: Timeline + Agent Analysis */}
          <div className="col-span-2 space-y-4">
            {/* Timeline */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-3">事件时间线</h2>
              <div className="space-y-3">
                {(detail.events || []).map((ev: AlertEvent, i: number) => (
                  <div key={ev.id || i} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <div className={clsx(
                        'w-2 h-2 rounded-full mt-1.5',
                        ev.notified ? 'bg-accent' : 'bg-text-muted'
                      )} />
                      {i < (detail.events || []).length - 1 && (
                        <div className="w-px flex-1 bg-border-subtle my-1" />
                      )}
                    </div>
                    <div className="flex-1 pb-2">
                      <div className="text-xs text-text-secondary">
                        {EVENT_LABELS[ev.event_type] || ev.event_type}
                      </div>
                      <div className="text-[11px] text-text-muted mt-0.5">
                        {ev.created_at?.slice(0, 19)}
                        {ev.from_state && ev.to_state && ` · ${ev.from_state} → ${ev.to_state}`}
                        {ev.notified && ' · 已通知'}
                      </div>
                      {ev.reason && ev.reason.length > 0 && (
                        <div className="text-[11px] text-text-muted mt-0.5">
                          {ev.reason.join('; ')}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {(!detail.events || detail.events.length === 0) && (
                  <p className="text-xs text-text-muted">暂无事件</p>
                )}
              </div>
            </div>

            {/* Agent Analysis placeholder */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-2">Agent 分析</h2>
              <p className="text-xs text-text-muted">
                库存状态: {c.current_state} · 级别: {c.current_level}
                {c.resolution_type ? ` · 解决方式: ${c.resolution_type}` : ''}
              </p>
            </div>
          </div>

          {/* Right: Ticket Operations */}
          <div className="space-y-4">
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-3">工单操作</h2>
              <div className="space-y-2 text-xs text-text-secondary mb-4">
                <div className="flex justify-between">
                  <span>状态</span>
                  <span className="font-medium">{STATUS_MAP[c.status] || c.status}</span>
                </div>
                <div className="flex justify-between">
                  <span>级别</span>
                  <span className={clsx(
                    c.current_level === 'critical' ? 'text-red-500' : c.current_level === 'warning' ? 'text-yellow-500' : 'text-blue-500'
                  )}>{c.current_level}</span>
                </div>
                <div className="flex justify-between">
                  <span>库存状态</span>
                  <span>{c.current_state}</span>
                </div>
              </div>

              {c.status === 'open' || c.status === 'acknowledged' ? (
                <div className="space-y-2">
                  {c.status !== 'acknowledged' && (
                    <button
                      onClick={() => handleAction('acknowledged')}
                      disabled={acting}
                      className="w-full py-2 text-xs rounded-lg bg-accent/5 text-accent hover:bg-accent/10 transition-colors disabled:opacity-50"
                    >
                      ✓ 确认告警
                    </button>
                  )}
                  <button
                    onClick={() => handleAction('resolved')}
                    disabled={acting}
                    className="w-full py-2 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100 transition-colors disabled:opacity-50"
                  >
                    🔧 已解决
                  </button>
                  <button
                    onClick={() => handleAction('closed')}
                    disabled={acting}
                    className="w-full py-2 text-xs rounded-lg bg-gray-50 text-text-muted hover:bg-gray-100 transition-colors disabled:opacity-50"
                  >
                    🚫 忽略
                  </button>
                </div>
              ) : c.status === 'resolved' ? (
                <p className="text-xs text-green-600">✅ 已解决 ({c.resolution_type})</p>
              ) : c.status === 'closed' ? (
                <p className="text-xs text-text-muted">🚫 已关闭</p>
              ) : null}
            </div>

            {/* Trace link */}
            <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
              <h2 className="text-sm font-semibold text-text-primary mb-2">技术信息</h2>
              <p className="text-xs text-text-muted mb-2">
                Case ID: {c.id} · Product: {c.product_id}
              </p>
              <a
                href="/observability/traces"
                target="_blank"
                className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
              >
                查看 Trace <ExternalLink size={12} />
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

const STATUS_MAP: Record<string, string> = {
  open: 'OPEN',
  acknowledged: 'ACK',
  resolved: '已解决',
  closed: '已关闭',
}
```

- [ ] **Step 2: 验证 TypeScript**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/alerts/\[id\]/page.tsx
git commit -m "feat(frontend): /alerts/[id] 告警详情页（时间线+工单操作）"
```

---

### Task 12: Sidebar 加入口

**Files:**
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: 在 Sidebar.tsx 加 /reports + /alerts 入口**

在 [Sidebar.tsx](frontend/src/components/Sidebar.tsx) 的 NAV 数组中，`AI任务中心` group 后添加：

```typescript
  {
    icon: <FileText size={18} />, label: '报告中心', path: '/reports',
  },
  {
    icon: <AlertTriangle size={18} />, label: '告警中心', path: '/alerts',
  },
```

同时在 import 中加这两个图标：

```typescript
import { Sparkles, PanelLeft, PanelLeftClose, LayoutDashboard, Download, Cog, Database, BookOpen, Brain, Activity, FileText, AlertTriangle } from 'lucide-react'
```

- [ ] **Step 2: 验证 TypeScript + Build**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Sidebar.tsx
git commit -m "feat(frontend): Sidebar 加报告中心+告警中心入口"
```

---

### Task 13: 服务端启动 + 端到端验证

- [ ] **Step 1: 重启后端**

```bash
cd backend && ..\.venv\Scripts\python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8000 --reload
```

验证后端启动日志无报错。

- [ ] **Step 2: 导入 Demo 数据**

```bash
curl -s -X POST http://localhost:8000/api/demo/seed | python -m json.tool
```
Expected: `{"ok": true, "result": {"products": 10, "sales_records": 300, "thresholds": 6, "policies": 5}}`

- [ ] **Step 3: 触发 daily_report**

```bash
curl -s -X POST http://localhost:8000/api/demo/run/daily_report | python -m json.tool
```
Expected: `{"ok": true, "scenario": "daily_report", "run_id": "...", "status": "success"}`

- [ ] **Step 4: 验证 reports API**

```bash
curl -s "http://localhost:8000/api/reports?type=daily_report" | python -m json.tool
curl -s "http://localhost:8000/api/reports/latest?type=daily_report" | python -m json.tool
```
Expected: 列表有数据，latest 返回完整 content

- [ ] **Step 5: 触发 inventory_alert**

```bash
curl -s -X POST http://localhost:8000/api/demo/run/inventory_alert | python -m json.tool
```
Expected: `{"ok": true, "scenario": "inventory_alert", ...}`

- [ ] **Step 6: 验证 alerts API**

```bash
curl -s "http://localhost:8000/api/inventory/stats" | python -m json.tool
curl -s "http://localhost:8000/api/inventory/cases?status=active" | python -m json.tool
```
Expected: stats 有数据，cases 有 alerting 商品（Mate 60 / MAC Ruby Woo）

- [ ] **Step 7: 验证 PATCH endpoint**

```bash
curl -s -X PATCH http://localhost:8000/api/inventory/cases/1 -H "Content-Type: application/json" -d '{"status":"acknowledged"}' | python -m json.tool
```
Expected: `{"updated": true, "case_id": 1, "status": "acknowledged"}`

- [ ] **Step 8: 前端启动验证**

```bash
cd frontend && npx next dev
```

浏览器验证：
- [ ] `http://localhost:3000/reports` — KPI 摘要卡片 + 日报列表
- [ ] `http://localhost:3000/reports/{id}` — 混合布局 + Trace 跳转
- [ ] `http://localhost:3000/alerts` — 统计卡片 + 活跃/历史切换
- [ ] `http://localhost:3000/alerts/{id}` — 时间线 + 工单操作
- [ ] `http://localhost:3000/agent` — 已有 Chat 不变

- [ ] **Step 9: 跑现有测试确认无回归**

```bash
cd backend && ..\.venv\Scripts\python.exe -m pytest backend/tests/orchestration/ backend/tests/inventory/ -q
```
Expected: 243 passed

---

## Verification Summary

| # | 验证项 | 方法 |
|---|--------|------|
| 1 | WorkflowMeta.category 正确 | Python 检查 list_metas() |
| 2 | daily_reports 表写入 | curl POST /api/demo/seed + run/daily_report |
| 3 | Reports API 正常 | curl GET /api/reports + /latest + /{id} |
| 4 | Alerts API 正常 | curl GET /api/inventory/stats + cases + PATCH |
| 5 | /reports 页面渲染 | 浏览器验证 |
| 6 | /reports/{id} 详情渲染 | 浏览器验证 |
| 7 | /alerts 页面渲染 | 浏览器验证 |
| 8 | /alerts/{id} 详情 + 工单操作 | 浏览器验证 |
| 9 | Sidebar 入口可点击跳转 | 浏览器验证 |
| 10 | 现有测试无回归 | pytest 243 passed |

---

## Phase 3.5 — 定时任务调度（补漏）

### Task 14: server.py 启动注册定时任务

**Files:**
- Modify: `backend/app/server.py`

**实施：** 在 `on_event("startup")` 中加 scheduler 初始化（见上文代码）。

- [ ] **Step 1:** 在 `eager_init_rag_pipeline` 后面加第二个 startup event
- [ ] **Step 2:** 注册 daily_report (9:00) + inventory_alert (8:00)
- [ ] **Step 3:** 重启后端验证 scheduler 日志输出

---

### Task 15: 定时任务配置 API

**Files:**
- Create: `backend/app/api/routes/schedules.py`

**端点：**
- `GET /api/schedules` — 列出所有定时任务（name / next_run / interval）
- `PATCH /api/schedules/{name}` — 修改定时参数（hour / minute / enabled）

---

### Task 16: 前端定时任务配置页

**Files:**
- Create: `frontend/src/app/schedules/page.tsx`

**功能：**
- 列表展示所有定时任务：名称 / 描述 / 执行时间（HH:MM）/ 状态（启用/禁用）/ 下次执行时间
- 点击编辑 → 弹窗修改小时/分钟/启停
- 调用 `PATCH /api/schedules/{name}` 生效
- Sidebar 加入口

**注：** 修改后需调用 scheduler 的 `reschedule` 方法立即使改动生效，不需要重启服务
