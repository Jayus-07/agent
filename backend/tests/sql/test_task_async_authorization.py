# -*- coding: utf-8 -*-
"""STOP D P0 回归：异步任务路径执行时授权闭环（2026-09-23）。

漏洞回归背景：TaskGraphExecutor 构造图状态时不写 request_context →
SQLSkill._build_sql_policy_context 返回 None → ask_struct(policy=None)
授权未启用旧行为——无 sql.read 的用户可经 POST /api/tasks 触发 SQL 执行。

修复语义（身份固定 / 授权动态 / 缺失拒绝 / 恢复重验）：
  - TaskGraphExecutor 每次执行/恢复前按任务持久化 actor 重新解析
    auth.users 当前授权（security/task_authorization.py）；
  - 解析结果以 RequestContext.checkpoint_safe() 注入图状态；
  - SQLSkill 生产边界缺 request_context 时 fail-closed（纵深防御）。

只 mock 外部边界（auth.users 行 / task_service DB 写 / LLM / executor /
audit 连接走 conftest autouse）；授权判定、上下文注入、Guard 策略链、
执行器控制流全部真实执行。

用例与验收规格对应：
  A1 viewer 异步任务 → permission denied, LLM=0, executor=0
  A2 editor(shared) 合法查询 → ALLOW, Guard>0, executor>0
  A3 editor 跨 scope → DENY_SCOPE, executor=0
  A4 执行时租户 membership 撤销 → 任务 FAILED, 图未执行
  A5 执行时权限撤销（role 改 viewer）→ deny, LLM=0, executor=0
  A6 resume 重验（撤权后恢复 → FAILED；正常恢复用新解析角色）
  A7 授权解析异常（DB 故障）→ fail-closed
  A8 SQLSkill 纵深：无 request_context → 拒绝且 LLM=0 executor=0
  （A8 的 tool spoof 反向用例 = 既有 TestToolSpoof，回归阶段复跑）
"""
import asyncio

import pytest

from backend.models.task import TaskRecord, TaskStatus
from backend.sql.sql_result import SQLResult


# ── 测试基建 ──────────────────────────────────────────────

def _auth_row(role="editor", status=1, dept="ecom", tenant="t1"):
    """模拟 auth.users 一行（role, status, dept, tenant_id）。"""
    return (role, status, dept, tenant)


@pytest.fixture
def auth_user(monkeypatch):
    """可编程 auth.users 权威行（每个用例自行设定当前状态）。"""
    import backend.security.task_authorization as ta

    holder = {"row": _auth_row()}

    def _set(**kw):
        holder["row"] = _auth_row(**kw)

    holder["set"] = _set
    monkeypatch.setattr(ta, "_fetch_auth_user",
                        lambda uid: holder["row"])
    return holder


@pytest.fixture
def task_db(monkeypatch):
    """task_service DB 写入 mock（记录 update_status 调用轨迹）。"""
    from backend.services import task_service

    calls = {"status": []}
    monkeypatch.setattr(task_service, "list_checkpoints",
                        lambda task_id, user_id: [])
    monkeypatch.setattr(
        task_service, "update_status",
        lambda task_id, status, **kw: calls["status"].append(status))
    monkeypatch.setattr(task_service, "update_progress",
                        lambda *a, **k: True)
    monkeypatch.setattr(task_service, "append_checkpoint",
                        lambda *a, **k: True)
    monkeypatch.setattr(task_service, "get_user_input", lambda task_id: "")
    return calls


def _sql_agent_mocks(monkeypatch, tables, sql):
    """mock SQLAgent 的 LLM/executor 外部边界，返回调用计数 dict。

    select_tables/generate_sql 是 LLM 边界，execute_sql_struct 是 DB
    executor 边界——deny 用例必须三者全 0 或按语义为 0。
    """
    import backend.sql.sql_agent as agent_mod

    calls = {"select": 0, "generate": 0, "executor": 0, "precheck": 0,
             "guard_rw": 0}
    seen = {}

    def _select(q):
        calls["select"] += 1
        return tables

    def _generate(q, t, feedback=None):
        calls["generate"] += 1
        return sql

    def _exec(*a, **k):
        calls["executor"] += 1
        return SQLResult.success([{"ok": 1}], columns=["ok"],
                                 sql=sql, elapsed=0)

    monkeypatch.setattr(agent_mod, "select_tables", _select)
    monkeypatch.setattr(agent_mod, "generate_sql", _generate)
    monkeypatch.setattr(agent_mod, "execute_sql_struct", _exec)

    from backend.sql.policy import SQLPolicyGuard as Guard
    real_precheck = Guard.precheck
    real_rw = Guard.validate_and_rewrite

    def _pre(self, policy):
        calls["precheck"] += 1
        seen["policy"] = policy
        return real_precheck(self, policy)

    def _rw(self, sql_text, policy):
        calls["guard_rw"] += 1
        return real_rw(self, sql_text, policy)

    monkeypatch.setattr(Guard, "precheck", _pre)
    monkeypatch.setattr(Guard, "validate_and_rewrite", _rw)
    return calls, seen


def _stub_graph():
    """最小任务图：sql_step 节点直调 SQLSkill（模拟 skill_executor 职责）。

    带 MemorySaver：resume 分支的 update_state 授权刷新走真实 LangGraph
    通道（生产为 PostgresSaver，测试用内存 saver——同一 API 语义）。
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver

    from backend.orchestration.state import OrchestratorState
    from backend.skills.sql.skill import SQLSkill

    g = StateGraph(OrchestratorState)

    def sql_step(state):
        # 生产节点均为 wrap_sync_node 同步包装（builder.py 同形态）；
        # SQLSkill.execute 是 async，节点内收敛事件循环
        s = dict(state)
        s["current_step_id"] = "1"  # skill_executor 职责：按 plan 取 step
        s["plan"] = {"nodes": {"1": {"step_id": "1",
                                     "capability": "sql.query"}},
                     "edges": {}}
        s.setdefault("step_results", {})
        sr = asyncio.run(SQLSkill().execute(s, step_capability="sql.query"))
        return {"step_results": sr.get("step_results", {})}

    g.add_node("sql_step", sql_step)
    g.add_edge(START, "sql_step")
    g.add_edge("sql_step", END)
    return g.compile(checkpointer=MemorySaver())


def _record(thread_id="", input_extra=None):
    return TaskRecord(
        id="task-a1", user_id="3", tenant_id="t1",
        status=TaskStatus.PENDING,
        input={"query": "查商品库存",
               **(input_extra or {})},
        thread_id=thread_id)


def _run_executor(record):
    from backend.orchestration.checkpoint.task_executor import (
        TaskGraphExecutor,
    )

    ex = TaskGraphExecutor(graph=_stub_graph(), poll_control_flags=False)
    return ex.execute(record)


@pytest.fixture(autouse=True)
def _product_table():
    """注册 fixture 业务表（真实白名单语义：未注册表会被 DENY_TABLE）。"""
    from backend.sql.schema_loader import TablePolicy, schema_loader

    qname = "product.products"
    existed = qname in schema_loader.allowed_tables
    previous_config = schema_loader._config["tables"].get(qname)
    previous_policy = schema_loader.table_policies.get(qname)
    schema_loader.register_table(
        qname, {"id": "PK", "sku": "VARCHAR"},
        description="fixture", policy=TablePolicy(data_domain="shared"))
    yield
    if existed:
        schema_loader.allowed_tables.add(qname)
        schema_loader._config["tables"][qname] = previous_config
        if previous_policy is None:
            schema_loader.table_policies.pop(qname, None)
        else:
            schema_loader.table_policies[qname] = previous_policy
    else:
        schema_loader.allowed_tables.discard(qname)
        schema_loader.table_policies.pop(qname, None)
        schema_loader._config["tables"].pop(qname, None)


# ── A1：viewer（无 sql.read）异步任务 → precheck 拒绝 ──────

def test_a1_viewer_async_task_denied_before_llm(auth_user, task_db,
                                                monkeypatch):
    auth_user["set"](role="viewer")
    # 创建时 body 伪造提权字段：执行时授权只认 auth.users，伪造无效
    calls, seen = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record(input_extra={"role": "admin",
                                             "data_scope": "all"}))

    sr = out["step_results"]["1"]
    assert sr["status"] == "failed"
    assert sr["error_type"] == "permission_denied"
    # precheck-first：路由/LLM/executor 均未发生（W2 语义）
    assert calls["select"] == 0
    assert calls["generate"] == 0
    assert calls["executor"] == 0


# ── A2：editor(shared) 合法查询 → 全链放行 ────────────────

def test_a2_editor_allowed_end_to_end(auth_user, task_db, monkeypatch):
    calls, seen = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record())

    sr = out["step_results"]["1"]
    assert sr["status"] == "success", sr
    # precheck 门 + validate_and_rewrite 内纵深重复检查
    assert calls["precheck"] >= 1
    assert calls["guard_rw"] >= 1
    assert calls["executor"] == 1
    # 执行时解析的当前授权真实进入策略链（而非无主 legacy）
    assert seen["policy"] is not None
    assert seen["policy"].user_id == "3"
    assert seen["policy"].tenant_id == "t1"
    assert "editor" in seen["policy"].principal.roles
    assert seen["policy"].source_channel == "graph"


# ── A3：editor 跨 scope 查财务 → DENY_SCOPE，executor=0 ───

def test_a3_editor_cross_scope_denied(auth_user, task_db, monkeypatch):
    auth_user["set"](role="editor", dept="hr")
    calls, _ = _sql_agent_mocks(
        monkeypatch, ["finance.expenses"],
        "SELECT amount FROM finance.expenses")

    # finance.expenses 是真实白名单表（scope=department + dept=hr → 拒绝）
    out = _run_executor(_record(input_extra={"query": "查财务费用"}))

    sr = out["step_results"]["1"]
    assert sr["status"] == "failed"
    assert sr["error_type"] == "permission_denied"
    assert calls["generate"] == 0  # 表域判定前置拒绝，生成零调用（2026-10-06 语义对齐）
    assert calls["executor"] == 0
    assert TaskStatus.FAILED not in task_db["status"]  # 任务本身成功收尾（skill 内失败≠任务失败）


# ── A4：执行时租户 membership 撤销 → fail-closed ──────────

def test_a4_tenant_membership_revoked(auth_user, task_db, monkeypatch):
    auth_user["set"](role="editor", tenant="other-tenant")
    calls, _ = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record())

    assert out.get("blocked") is True
    assert TaskStatus.FAILED in task_db["status"]
    assert calls["select"] == 0 and calls["generate"] == 0
    assert calls["executor"] == 0


# ── A5：执行时权限撤销（editor→viewer）→ precheck 拒绝 ────

def test_a5_permission_revoked_at_execution(auth_user, task_db,
                                            monkeypatch):
    auth_user["set"](role="viewer")
    calls, _ = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record())

    sr = out["step_results"]["1"]
    assert sr["status"] == "failed"
    assert sr["error_type"] == "permission_denied"
    assert calls["select"] == 0  # precheck 在路由选表前已拒绝
    assert calls["executor"] == 0


# ── A6：resume 恢复重验（不信任 checkpoint 旧权限）────────

def test_a6_resume_revalidates_authorization(auth_user, task_db,
                                             monkeypatch):
    from dataclasses import replace

    from backend.orchestration.checkpoint.task_executor import (
        TaskGraphExecutor,
    )
    from backend.services import task_service as task_service_mod

    calls, seen = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    # 第一轮：真实全新执行，checkpoint 里留下 roles=("admin",) 的旧授权
    # 快照（制造"创建后角色被降级"的场景）
    auth_user["set"](role="admin")
    ex = TaskGraphExecutor(graph=_stub_graph(), poll_control_flags=False)
    rec_fresh = TaskRecord(id="task-a6", user_id="3", tenant_id="t1",
                           status=TaskStatus.PENDING,
                           input={"query": "查商品库存"},
                           thread_id="task-a6")
    out0 = ex.execute(rec_fresh)
    assert out0["step_results"]["1"]["status"] == "success"
    exec_after_fresh = calls["executor"]

    # 第二轮：resume。执行时刻 auth.users 当前角色已降为 editor →
    # 授权刷新以新解析值覆盖 checkpoint 旧快照（身份固定/授权动态）
    auth_user["set"](role="editor")
    monkeypatch.setattr(task_service_mod, "list_checkpoints",
                        lambda tid, uid: ["cp1"])
    rec_resume = replace(rec_fresh)
    ex.execute(rec_resume)
    snap = ex._graph.get_state({"configurable": {"thread_id": "task-a6"}})
    ctx = (snap.values or {}).get("request_context") or {}
    # checkpoint 序列化会把 roles 存成 list——比较时归一化
    assert list(ctx.get("roles") or ()) == ["editor"], ctx
    assert ctx.get("user_id") == "3"
    assert ctx.get("data_scope") == "department"  # editor 按当前 roles 折算
    # 恢复轮不重放已完成节点（LangGraph 位置语义），executor 不再发生
    assert calls["executor"] == exec_after_fresh

    # 第三轮：撤权到 viewer 后 resume → 授权刷新注入 viewer（sql.read 已
    # 不在 permission_codes）；恢复位置在末尾无节点重放，executor 不发生。
    # 若任务尚未完成（真实场景：还有 SQL 节点待跑），下一跳 SQLSkill 的
    # precheck 会按刷新后的 viewer 身份拒绝（A1 已验证该语义）。
    auth_user["set"](role="viewer")
    out2 = ex.execute(rec_resume)
    assert "step_results" in out2
    snap2 = ex._graph.get_state({"configurable": {"thread_id": "task-a6"}})
    ctx2 = (snap2.values or {}).get("request_context") or {}
    assert list(ctx2.get("roles") or ()) == ["viewer"]  # 刷新生效
    assert ctx2.get("data_scope") == "self"  # viewer 折算
    assert calls["executor"] == exec_after_fresh


# ── A7：授权解析异常（DB 故障）→ fail-closed ──────────────

def test_a7_resolver_failure_fail_closed(task_db, monkeypatch):
    import backend.security.task_authorization as ta

    def _boom(uid):
        raise RuntimeError("auth db connection lost")

    monkeypatch.setattr(ta, "_fetch_auth_user", _boom)
    calls, _ = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record())

    assert out.get("blocked") is True
    assert TaskStatus.FAILED in task_db["status"]
    assert calls["select"] == 0 and calls["executor"] == 0


# ── A8：SQLSkill 纵深防御（缺 request_context 直接拒绝）───

def test_a8_skill_fail_closed_without_request_context(monkeypatch):
    from backend.skills.sql.skill import SQLSkill

    calls, _ = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    # 模拟上下文传播断裂的图状态（无 request_context）——纵深处拒绝，
    # 不再走 policy=None legacy
    state = {
        "question": "查商品",
        "plan": {"nodes": {"1": {"step_id": "1",
                                 "capability": "sql.query"}}},
        "current_step_id": "1",
        "step_results": {},
    }

    async def run():
        return await SQLSkill().execute(state, step_capability="sql.query")

    result = asyncio.run(run())
    sr = result["step_results"]["1"]
    assert sr["status"] == "failed"
    assert sr["error_type"] == "permission_denied"
    assert calls["select"] == 0 and calls["generate"] == 0
    assert calls["executor"] == 0


# ── A9：用户不存在 → fail-closed（快照用户被删 / id 失效）──

def test_a9_user_missing_fail_closed(task_db, monkeypatch):
    import backend.security.task_authorization as ta

    monkeypatch.setattr(ta, "_fetch_auth_user", lambda uid: None)
    calls, _ = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    out = _run_executor(_record())

    assert out.get("blocked") is True
    assert TaskStatus.FAILED in task_db["status"]
    assert calls["select"] == 0 and calls["generate"] == 0
    assert calls["executor"] == 0


# ── A10：升权 resume → 恢复跳以当前新角色执行业务节点 ──────

def _two_step_graph():
    """两节点 stub 图：node_a（无授权语义）→ sql_step（授权消费方）。

    用于构造「跑到一半暂停」的真实生命周期：pause 点在 node_a 之后，
    resume 时 sql_step 尚未执行——恢复跳的授权必须来自本次重新解析。
    """
    from langgraph.graph import StateGraph, START, END
    from langgraph.checkpoint.memory import MemorySaver

    from backend.orchestration.state import OrchestratorState
    from backend.skills.sql.skill import SQLSkill

    g = StateGraph(OrchestratorState)

    def node_a(state):
        s = dict(state)
        s.setdefault("step_results", {})
        return {"step_results": {**s["step_results"],
                                 "a": {"status": "success"}}}

    def sql_step(state):
        s = dict(state)
        s["current_step_id"] = "1"
        s["plan"] = {"nodes": {"1": {"step_id": "1",
                                     "capability": "sql.query"}},
                     "edges": {}}
        s.setdefault("step_results", {})
        sr = asyncio.run(SQLSkill().execute(s, step_capability="sql.query"))
        return {"step_results": {**s["step_results"], **sr.get("step_results", {})}}

    g.add_node("node_a", node_a)
    g.add_node("sql_step", sql_step)
    g.add_edge(START, "node_a")
    g.add_edge("node_a", "sql_step")
    g.add_edge("sql_step", END)
    return g.compile(checkpointer=MemorySaver())


def test_a10_escalated_resume_runs_with_current_role(auth_user, task_db,
                                                     monkeypatch):
    from backend.orchestration.checkpoint import TaskPaused
    from backend.orchestration.checkpoint.task_executor import (
        TaskGraphExecutor,
    )
    from backend.services import task_service as task_service_mod

    calls, seen = _sql_agent_mocks(
        monkeypatch, ["product.products"],
        "SELECT sku FROM product.products LIMIT 3")

    # T1：viewer 创建并执行，node_a 完成后在边界暂停（checkpoint 留下
    # roles=("viewer",) 的旧快照）
    auth_user["set"](role="viewer")
    ex = TaskGraphExecutor(graph=_two_step_graph(), poll_control_flags=False)
    pause_holder = {"paused": True}
    monkeypatch.setattr(ex, "_paused",
                        lambda tid: pause_holder["paused"])
    rec = TaskRecord(id="task-a10", user_id="3", tenant_id="t1",
                     status=TaskStatus.PENDING,
                     input={"query": "查商品库存"},
                     thread_id="task-a10")
    try:
        ex.execute(rec)
        raised = False
    except TaskPaused:
        raised = True
    assert raised  # 暂停发生在 node_a 之后、sql_step 之前
    assert calls["executor"] == 0  # sql_step 未执行

    # T3→T4：暂停期间 DB 升权 viewer→editor；resume 必须以 editor 执行
    # 剩余业务节点（不是 checkpoint 里的旧 viewer）
    auth_user["set"](role="editor")
    pause_holder["paused"] = False
    monkeypatch.setattr(task_service_mod, "list_checkpoints",
                        lambda tid, uid: ["cp-after-node-a"])
    out = ex.execute(rec)

    assert out["step_results"]["a"]["status"] == "success"
    assert out["step_results"]["1"]["status"] == "success"
    assert calls["executor"] == 1
    # 恢复跳进入 SQLSkill 的授权 = 执行时解析的 editor（升权生效）
    assert seen["policy"] is not None
    assert "editor" in seen["policy"].principal.roles
    assert "viewer" not in seen["policy"].principal.roles


# ── A11：并发交错（两个租户内用户同时执行，授权无串扰）─────

def test_a11_concurrent_executors_no_auth_crosstalk(task_db, monkeypatch):
    """Task A(user 3, editor) 与 Task B(user 4, viewer) 强制并发交错。

    授权上下文为函数局部量 → 图状态（executor 实例私有），无进程级共享；
    本用例以 barrier 强制两执行流同时在跑，验证 precheck 看到的 policy
    身份与调用任务一一对应（A/B 不串）。
    """
    import threading

    import backend.security.task_authorization as ta

    rows = {3: _auth_row(role="editor"), 4: _auth_row(role="viewer")}
    monkeypatch.setattr(ta, "_fetch_auth_user", lambda uid: rows[int(uid)])

    import backend.sql.sql_agent as agent_mod
    from backend.sql.policy import SQLPolicyGuard as Guard

    captures = []  # (user_id, roles) 每次真实进入策略链的授权身份
    lock = threading.Lock()
    real_precheck = Guard.precheck

    def _pre(self, policy):
        with lock:
            captures.append((policy.user_id,
                             tuple(policy.principal.roles)))
        return real_precheck(self, policy)

    monkeypatch.setattr(Guard, "precheck", _pre)

    def _select(question, *args, **kwargs):
        # 现契约：Router 收问题返表清单；收下 allowed_tables 以走
        # _select_authorized_tables 的授权收口分支（editor 命中 shared 表）
        return ["product.products"]

    def _generate(tables, question, **kwargs):
        return "SELECT sku FROM product.products LIMIT 3"

    def _exec(sql, *args, **kwargs):
        return SQLResult.success([{"ok": 1}], columns=["ok"],
                                 sql=sql, elapsed=0)

    monkeypatch.setattr(agent_mod, "select_tables", _select)
    monkeypatch.setattr(agent_mod, "generate_sql", _generate)
    monkeypatch.setattr(agent_mod, "execute_sql_struct", _exec)

    from backend.orchestration.checkpoint.task_executor import (
        TaskGraphExecutor,
    )

    results = {}

    def _run(key, user_id):
        rec = TaskRecord(id=f"task-a11-{key}", user_id=str(user_id),
                         tenant_id="t1", status=TaskStatus.PENDING,
                         input={"query": "查商品库存"},
                         thread_id=f"task-a11-{key}")
        ex = TaskGraphExecutor(graph=_stub_graph(), poll_control_flags=False)
        barrier.wait()
        results[key] = ex.execute(rec)

    barrier = threading.Barrier(2)
    threads = [threading.Thread(target=_run, args=("A", 3)),
               threading.Thread(target=_run, args=("B", 4))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)

    # 结果各归各位：A(editor) success，B(viewer) permission_denied
    assert results["A"]["step_results"]["1"]["status"] == "success"
    assert results["B"]["step_results"]["1"]["status"] == "failed"
    assert results["B"]["step_results"]["1"]["error_type"] == "permission_denied"

    # 进入策略链的每次授权身份与任务一一对应（无串扰）
    assert captures, "策略链未被触达"
    for uid, roles in captures:
        if uid == "3":
            assert "editor" in roles and "viewer" not in roles
        elif uid == "4":
            assert roles == ("viewer",)
