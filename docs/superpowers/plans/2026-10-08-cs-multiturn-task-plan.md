# 客服多轮组合任务与 LLM 闭环改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有客服图、ConfirmationFlow、Sandbox 业务服务和 CSDrawer 上安全实现 Pending 只读旁路、条件组合任务、事实约束回复、人工接管优先及可恢复的前端状态，并以证据决定本机 P0 是否可切换。

**Architecture:** 保留 `StateLoader → PendingHandler → Supervisor → 原五个 Expert → Reporter` 唯一执行链。新增的 turn decision、受限 CSTaskPlan/CSTaskResult 和递归 FactSet 投影作为现有 state/understanding/expert/reporter 的数据契约；ConfirmationStore 继续承担唯一副作用认领，人工接管由持久化 Handoff 状态优先裁决。前端继续使用现有客服抽屉、SSE 和确认卡。

**Tech Stack:** Python 3.11、FastAPI、LangGraph、Pydantic、PostgreSQL、现有 LLM/Prompt Registry、Next.js 14、React、TypeScript、SSE、Playwright。

**Spec:** `C:\Users\wh\.codex\attachments\40ed4a5c-d580-443a-8ebe-427a51ee6870\已粘贴的文本.txt`（用户本轮提供的完整施工规格；最终验收门仍以该规格 34 项 P0 为准）。

## Global Constraints

- 只改造现有客服系统，不新建客服系统、第二套状态机或额外客服图节点。
- Sandbox 是本轮唯一业务服务；不得触碰真实 Java 交易服务。
- 退款等副作用只有通过持久化原子认领、有效确认、权限和幂等门后才能执行。
- Pending 可放行 Knowledge/Query 只读能力；绝不放行 Action；Handoff waiting_human/human_active 高于 Pending。
- 条件未知、数据异常、订单不唯一时，不得生成写操作 Proposal。
- 所有 Python 变更先写可复现测试并观察 RED，再实现；局部 pytest 使用 `--no-cov`。
- 每阶段独立提交；迁移向后兼容；无灰度、无影子双跑；只有全部 P0 真实证据通过才可本机切换。
- 计费与模型调用继续走项目统一代理、Prompt Registry、Token/Cost 和 Trace 归因。

## Review Focus

- 同一会话两个浏览器标签以不同 proposal/version 并发确认时只能有一个副作用：Task 2 覆盖 DB 原子 claim 与重复 client_action_id。
- Handoff 状态读取超时或 DB 不可用时不能把旧 Pending 送去执行：Task 3 覆盖 fail-closed。
- 组合查询中的模型伪造订单号、未知状态、多个订单与错误依赖不能到达 ActionExpert：Task 4 覆盖 Schema、依赖和服务端事实来源。
- FactSet 嵌套对象和列表也不能绕过字段白名单泄漏身份、审计或 Sandbox 内部元数据：Task 5 覆盖递归投影及编造金额/承诺回退。
- SSE 缺少 `pending_action`、会话切换、刷新、人工状态变化时前端不能丢 pending 或继续展示 AI 可执行入口：Task 6 覆盖 store、状态恢复和浏览器走查。

---

### Task 1: Phase 0 基线审计与恢复点

**Files:**
- Create: `docs/reports/2026-10-08-客服Agent多轮组合任务与LLM闭环改造验收.md`
- Create: `docs/superpowers/plans/2026-10-08-cs-multiturn-task-plan.md`
- Test: `backend/tests/customer_service/`（复用现有完整基线，不改历史断言）
- Test: `backend/tests/api/test_cs_confirm_api.py`

**Interfaces:** 输入为用户规格和 HEAD `5f3762de7947f9d573291d3389e1feada94e6a3a`；输出为带真实基线、容器/Sandbox/Prompt/ConfirmationStore/Checkpointer 对账及未通过项的验收报告。已知基线客服集 `1299 passed, 5 failed`、黄金集 `226/230`、确认 API `7 passed`；这些失败只作基线，不得声明 P0 通过。

- [ ] **Step 1: 记录实现前工作区和容器证据**

分别运行 `git status --short`、`git diff --stat`、`git rev-parse HEAD`，并从 app 容器只读核实镜像创建时间、/health migrations/schema 摘要、Sandbox health 和合成用户只读订单查询；禁止输出凭据。

- [ ] **Step 2: 记录客服入口、Prompt、确认和 Checkpointer 对账**

引用具体代码入口：`cs_understanding.py`、`reporter.py`、`confirmation_store.py`、`state_transition.py`、`handoff/lifecycle.py`；将本次观察到的 Prompt active/production、Sandbox 为 business-mock、Checkpointer 开关实际值写入报告，不用历史报告代替。

- [ ] **Step 3: 自审报告和计划覆盖**

检查用户规格 Phase 0–6、34 项 P0、P1 和所有最终 false 门禁均在计划/报告中有归属；只有获得命令输出的项标 PASS。

- [ ] **Step 4: Commit Phase 0**

`git add docs/reports/2026-10-08-客服Agent多轮组合任务与LLM闭环改造验收.md docs/superpowers/plans/2026-10-08-cs-multiturn-task-plan.md`

`git commit -m "docs(cs): record phase 0 implementation baseline"`

**Expected:** Phase 0 文档提交独立可回滚；报告明确容器版本不能由镜像标签证明、当前迁移 drift、测试 5 个既有失败和黄金集 4 个攻击漏判。

### Task 2: Phase 1 确认安全与持久化原子认领

**Files:**
- Modify: `backend/app/api/routes/cs_admin.py`
- Modify: `backend/customer_service/confirmation_flow.py`
- Modify: `backend/customer_service/confirmation_store.py`
- Modify: `backend/customer_service/models/confirmation.py`
- Modify: `backend/customer_service/repository/confirmation_repo.py`
- Modify: `backend/customer_service/handoff/lifecycle.py`
- Modify: `backend/customer_service/security/input_guard.py`
- Modify: `backend/customer_service/vocab.py`
- Modify: `scripts/init_db.py`
- Create: `backend/sql/migrations/082_cs_confirmation_versioned_claim.sql`
- Test: `backend/tests/api/test_cs_confirm_api.py`
- Test: `backend/tests/customer_service/test_confirmation_store.py`
- Test: `backend/tests/customer_service/test_confirmation_repo.py`
- Test: `backend/tests/customer_service/test_confirmation.py`
- Test: `backend/tests/customer_service/test_handoff_lifecycle.py`
- Test: `backend/tests/customer_service/test_idempotency.py`
- Test: `backend/tests/customer_service/test_cs_input_guard.py`
- Test: `backend/tests/customer_service/test_vocab.py`
- Test: `backend/tests/customer_service/test_defect6_action_chain.py`
- Test: `backend/tests/test_cs_confirmation_migration.py`

**Interfaces:** 请求携带 `proposal_id: str`、`expected_version: int`、`client_action_id: UUID string` 和现有 `session_id: str/decision`；服务端绑定 authenticated user、tenant、conversation、当前有效 Proposal 和当前版本。响应保留旧 `{status,answer,confirmation_state,action_result}` 字段并增加已确认的 proposal/version/action/status 事实。DB 无法完成 strict claim 时返回拒绝/冲突且不得执行；L1 只允许非副作用读取。session-only 旧请求因无法证明用户看到的 Proposal 版本而返回 409；身份/租户缺失不映射到共享默认值。

- [x] **Step 1: 先写并运行失败测试**

增加断言：版本错、proposal 错、租户/用户不匹配、过期、人工接管、DB claim 失败、相同 action id 重试均不触发一次以上副作用；合法新请求仍调用既有 ConfirmationFlow。运行 `D:/Python/python.exe -m pytest backend/tests/api/test_cs_confirm_api.py backend/tests/customer_service/test_confirmation_store.py -q --no-cov`，确认新增用例因缺契约/旧 L1 执行而 RED。

```python
def test_confirm_rejects_stale_proposal_without_executing(client, fake_identity, fake_store, fake_process):
    fake_store.load_result = {"action_id": "act-1", "proposal_id": "proposal-current", "version": 3}
    response = client.post("/cs/confirm", json={
        "session_id": "s1", "decision": "confirm", "proposal_id": "proposal-old",
        "expected_version": 2, "client_action_id": "d2c8f8bb-5a87-4e3c-98c4-2dcb3a8f2a11",
    })
    assert response.status_code == 409
    assert fake_process.calls == []
```

- [x] **Step 2: 实现兼容请求校验和严格 claim**

使用 Pydantic 请求模型校验新增字段；session-only 旧客户端无法证明用户看到的目标版本，全部拒绝并要求刷新卡片。持久 claim 在同一事务中验证身份、租户、proposal/version/state/expiry/handoff 和 `client_action_id`；strict DB 错误禁止调用 L1 执行路径。更新 Proposal 使用 pending 状态与上一版本的 CAS，认领后不能再覆写目标。重用 `cs_action:{confirmation_id}` 幂等账本，不创建平行副作用账本。

- [x] **Step 3: 迁移与安全对抗回归**

新增向后兼容 migration，旧行有确定性初始 version；补测朋友订单、借用账号、免验证退款、伪管理员、提示注入/绕过确认。运行目标测试、客服确认与权限测试、`D:/Python/python.exe -m backend.scripts.verify_migration_state`。回滚只回滚应用代码，不删除已写入的兼容迁移数据。

记录：Phase 1 目标测试 `222 passed`、词表门禁全过、Layer1 黄金回放 `230/230`；迁移仓库层 `86/86` 已登记。权威 PostgreSQL 只读 preflight 报告基线迁移 `078/080/081` 未应用，且新迁移 `082` 待应用；本阶段不提前改动数据库，留在 P0 本机环境切换前处理。

- [x] **Step 4: Commit Phase 1**

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "fix(cs): require durable versioned confirmation"`。

**Expected:** 所有有效副作用只有一条原有执行链；strict DB 故障和旧客户端歧义场景均 fail-closed；新 migration 不丢旧 confirmation。

### Task 3: Phase 2 Pending 期间只读对话与人工优先

**Files:**
- Modify: `backend/customer_service/graph_state.py`
- Modify: `backend/customer_service/graph_builder.py`（StateLoader 每轮重置 turn state）
- Modify: `backend/customer_service/pending_handler.py`
- Modify: `backend/customer_service/supervisor.py`
- Modify: `backend/customer_service/handoff/lifecycle.py`
- Modify: `backend/customer_service/state_transition.py`（仅需要让一次只读旁路可通过时）
- Test: `backend/tests/customer_service/test_pending_handler.py`
- Test: `backend/tests/customer_service/test_cs_supervisor.py`
- Test: `backend/tests/customer_service/test_handoff_lifecycle.py`
- Test: `backend/tests/customer_service/test_cs_graph.py`（StateLoader turn reset 与 Reporter 跳过 Composer）

**Interfaces:** `PendingTurnDecision ∈ {CONFIRM,CANCEL,READ_ONLY_QUERY,NEW_WRITE_CONFLICT,HANDOFF,AMBIGUOUS}`；一次只读标记仅在当前 turn state 有效。Handoff authoritative read 返回明确 lifecycle state；读取失败不是 `None/未排队`，而是 fail-closed。只读白名单仅 `KnowledgeExpert` 与 `QueryExpert`；Action 永远不在集合中。

- [x] **Step 1: 写 Pending 转移矩阵测试并观察 RED**

覆盖 active Pending 下普通 FAQ/物流查询保留原 Proposal，疑问式“可以退吗”不得确认，新写操作冲突，模糊语句追问；need_info 下“退款一般多久到账？”走知识、“第二个订单”补槽、“算了”取消。测试 waiting_human/human_active 与 handoff 状态读取异常均不进入 AI 查询、Action 或 AI confirm。

```python
def test_readonly_question_keeps_pending_and_routes_to_supervisor():
    cmd = cs_pending_handler_node(_state(
        user_message="退款一般多久到账？",
        pending_action=_pending_action(),
        confirmation_state="pending",
    ))
    assert cmd.goto == "cs_supervisor"
    assert cmd.update["pending_turn_decision"] == "READ_ONLY_QUERY"
    assert cmd.update["pending_action"]["proposal_text"] == _pending_action()["proposal_text"]
```

- [x] **Step 2: 实现 Handoff 优先和当前轮决策**

入口先读 Handoff 权威状态，再分类 Pending 消息；只读/确认/取消使用既有专家和 ConfirmationFlow，不创建第二张图。StateLoader 每轮初始化 turn decision，Supervisor 只对当前 turn 允许一次 Knowledge/Query，回环时直接 Reporter，不二次触发 pending 追问。

- [x] **Step 3: 针对执行链和迁移恢复跑测试**

运行 `D:/Python/python.exe -m pytest backend/tests/customer_service/test_pending_handler.py backend/tests/customer_service/test_cs_supervisor.py backend/tests/customer_service/test_handoff_lifecycle.py -q --no-cov`；对照 Pending 对象指纹、expiry、proposal/version 执行前后不变。

记录：先运行新测试观察到 12 项 RED（Pending 分类、Handoff 优先、Supervisor 白名单与 StateLoader 重置均缺失），实现后覆盖 Phase 2 四个主测试文件与确认/API/状态迁移/权限执行链，共 `274 passed`。PostgreSQL Handoff 读取以 tenant+conversation 限定并启用 `raise_on_error=True`；等待人工与读取失败均直接 Reporter，读取失败不调 ConfirmationFlow；Pending FAQ/物流分别只派 Knowledge/Query 一次，need_info 只接受明确订单选择/编号继续 ActionExpert。

- [x] **Step 4: Commit Phase 2**

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "feat(cs): allow guarded readonly turns while pending"`。

**Expected:** 同一用户可只读咨询且 Pending 不变；等待人工期间只有真实排队状态，不发 AI 业务答复、不查订单、不新建/执行 Proposal。

### Task 4: Phase 3 有限 CSTaskPlan 与条件执行

**Files:**
- Modify: `backend/customer_service/understanding/contracts.py`
- Create: `backend/customer_service/understanding/task_plan.py`
- Modify: `backend/customer_service/understanding/semantic.py`
- Modify: `backend/customer_service/understanding/validator.py`
- Modify: `backend/customer_service/graph_state.py`
- Modify: `backend/customer_service/graph_builder.py`
- Modify: `backend/customer_service/supervisor.py`
- Modify: `backend/customer_service/experts/query.py`
- Modify: `backend/customer_service/experts/action.py`
- Test: `backend/tests/customer_service/test_understanding_contracts.py`
- Test: `backend/tests/customer_service/test_task_plan.py`
- Test: `backend/tests/customer_service/test_query_expert.py`
- Test: `backend/tests/customer_service/test_conditional_action_plan.py`
- Test: `backend/tests/customer_service/understanding/test_semantic_layer.py`

**Interfaces:** `CSTaskPlan(schema_version=1, primary_intent, tasks)` 限定 query_logistics/query_order_status/propose_refund；每个 task 有 `task_id, capability, depends_on, optional condition`。condition 只允许 `shipping_status eq <枚举值>` 等固定字段和枚举。纯函数 `evaluate_task_condition(condition, facts) -> bool` 对缺失/未知 fact 返回 `False`。`CSTaskResult` 含 `task_id,status,facts,source,error_type`；Graph state 使用 `task_plan/task_cursor/task_results/current_task`。用户请求和本地退款资格规则必须同时允许才进入 Action Expert。结构化理解复用客服 Understanding Layer 已接入的统一 LLM 代理和 Prompt Registry 版本，不新增客服私有代理或未注册 Prompt。

- [x] **Step 1: 写 Schema、DAG、未知值和真实订单来源测试并观察 RED**

拒绝额外 capability/任意 URL/SQL/代码、重复 task id、循环依赖、超量任务、模型提供的真实订单号、多个/不存在订单；未知 shipping status 必须 skip write。用受控 fake LLM/Sandbox 边界断言不发生第二次同轮 `_llm_decompose_intents()`。

```python
def test_task_plan_rejects_untrusted_capability():
    with pytest.raises(ValidationError):
        CSTaskPlan.model_validate({
            "schema_version": 1, "primary_intent": "t_logistics",
            "tasks": [{"task_id": "a1", "capability": "sql.execute", "depends_on": []}],
        })
```

```python
def test_missing_shipping_fact_never_satisfies_refund_condition():
    condition = CSTaskCondition(fact="shipping_status", operator="eq", value="not_shipped")
    assert evaluate_task_condition(condition, {}) is False
```

记录：先提交 Schema、Graph 输入、Query 与 Action 红测；首次运行因 `task_plan.py` 尚不存在而在收集阶段 RED。随后补齐显式复合请求解析、真实订单来源、未知物流状态、重复 LLM 分解和多订单澄清断言。

- [x] **Step 2: 实现候选解析和服务端验证**

Pydantic 验证白名单与有限步数；用户订单号只能用于查询服务端授权结果，不可由 LLM 输出替代。QueryExpert 返回结构化 facts/source/error_type，来自 `business-mock` 权威订单及物流适配；重复分解使用同一结构化 understanding，不再调用重复意图拆解。

- [x] **Step 3: 实现规则条件、资格门和 Proposal**

Supervisor 按 DAG 单次执行；条件 evaluator 为纯确定性代码。ActionExpert 只在 query 成功、订单唯一、用户有权、条件成立、退款资格通过后生成确认 Proposal；执行仍只在后续用户确认中调用 ConfirmationFlow。

- [x] **Step 4: 运行 TaskPlan/Sandbox 安全回归**

运行 13 个 TaskPlan、Query、Action、Pending、Supervisor、Handoff、CS 图、订单引用与 Semantic 相关测试文件：`237 passed`；其中新计划与语义测试筛选 `28 passed`。独立审查发现任务计划绕过 handoff/risk 门及退款否定句误触发两项问题，现已修复并补回归：覆盖人工接管、风险命中、不要/别/不需要/没打算/没必要/不考虑退款，以及“算了/不用了/改主意/反悔/收回/放弃”等句中撤回；Graph 新请求无候选时明确重置计划及执行游标。覆盖条件成立走现有 Proposal、业务资格复核拒绝不生成 Pending、已发货/未知/服务失败/多订单/无显式退款均不触发提案。一次完整 Semantic 运行中的旧异步 Ollama fallback 收到 502 并打印线程日志异常，但 pytest 断言全部通过；不作为模型在线可用性证据。

- [x] **Step 5: Commit Phase 3**

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "feat(cs): execute constrained conditional task plans"`。

### Task 5: Phase 4 递归事实白名单和回复闭环

**Files:**
- Modify: `backend/customer_service/response/composer.py`
- Create: `backend/customer_service/response/facts.py`
- Modify: `backend/customer_service/reporter.py`
- Modify: `backend/customer_service/experts/knowledge.py`
- Modify: `backend/customer_service/experts/query.py`
- Modify: `backend/customer_service/experts/complaint.py`
- Modify: `backend/customer_service/trace.py`
- Test: `backend/tests/customer_service/response/test_composer.py`
- Test: `backend/tests/customer_service/response/test_facts.py`
- Test: `backend/tests/customer_service/test_reporter_facts.py`

**Interfaces:** `FactSet` 由 Query/Complaint/Knowledge 实际结果投影而来；仅允许白名单业务字段和经屏蔽的值，嵌套 object/list 递归按对应 schema 投影。Composer 输入只包含 FactSet、用户问题和允许的知识证据；输出必须通过金额、到账承诺、物流状态、操作状态和 Sandbox 来源一致性检查，否则返回固定模板。Response Composer 至多一次 LLM；Knowledge 已有 RAG 回复不再无条件二次调用。

- [x] **Step 1: 写嵌套恶意事实和编造回复测试并观察 RED**

注入嵌套手机号、他人身份、审计字段、模型 prompt、伪造金额/到账日期/已退款/虚构物流/Sandbox 真实化等；断言投影剔除敏感键、OutputGuard 仍最后执行、Composer 不一致时用模板。

```python
def test_fact_projection_recursively_drops_identity_and_audit_fields():
    projected = project_fact_set({"order": {"status": "shipped", "user_id": "other-user",
                                              "audit": {"actor": "admin"}}})
    assert projected == {"order": {"status": "shipped"}}
```

- [x] **Step 2: 实现递归 FactSet 投影和输出校验**

复用统一 pii masker；schema 白名单按 facts 类别定义，不把 audit/raw LLM payload 复制进 Prompt。将 `cs_understanding_source`、`cs_task_plan_source`、`cs_task_count`、`cs_task_status`、`cs_pending_turn_kind`、`cs_response_source`、`cs_response_guard_result` 及 Prompt 版本沿用现有 Trace tags，Token/Cost 由统一 LLM 账本归因。

- [x] **Step 3: 运行回复与现有 EvidenceGate 测试**

运行 `D:/Python/python.exe -m pytest backend/tests/customer_service/response/test_composer.py backend/tests/customer_service/response/test_facts.py backend/tests/customer_service/test_reporter_facts.py -q --no-cov`，并确认 Knowledge RAG 已答复路径 composer 调用次数为零。

记录：先对嵌套身份/审计投影、虚构事实、状态/金额/ETA、退款执行、Sandbox 来源和 OutputGuard 异常补测试并观察 RED。独立复审发现多笔订单金额串单、负数/未知币种、退款待确认和真实来源改写问题，逐项补回归并修正。后续复审继续发现“另外一单/余下那单”、多单状态串用、物流 ETA 混作退款到账 ETA、否定模拟措辞与 sandbox 退款成功缺标注、ETA 跨承诺和年份丢失、金额类型串用、“剩下的那笔”、相对订单状态歧义和复合真实来源等反例，均已加入测试并收紧守卫。最新一轮还补了混合订单发货正反状态、每条退款执行声明单独模拟标记、金额旁注不能更改其类别、长修饰语真实数据、“到达”ETA，以及退款完成不可借用订单完成状态、未知来源不可宣称真实业务数据、裸星期 ETA、退款资格否定极性、退款申请完成同义句、已寄出物流状态、双重否定、退款成功/失败事实冲突、“已完成退款”语序、快递揽收、简式退款资格、退款成功/申请提交别名、已出库物流状态、发货 ETA 不能借用送达 ETA、“支持退款”“已成功申请退款”“已发出”“预计揽收日期不能借用送达 ETA”、同订单订单/物流冲突状态、预计出库/寄出时间不能借用送达 ETA，以及早期“模拟流程”标记不能覆盖靠近执行声明的否定模拟标记和“送到”类 ETA 承诺。新增回归均先 RED 后修复；当前运行回复与 Expert/EvidenceGate 相关的 10 个测试文件结果 `179 passed`，语法编译及 `git diff --check` 通过。测试日志仍显示本 worktree 缺少 `.env` 导致 PostgreSQL 预检不可用，该项不作为 Phase 6 真实 PG 证据。

- [x] **Step 4: Commit Phase 4**

补充复核：为“预计明天签收/收货”及送货、配送、投递、妥投、派送等常见 ETA 目标词补充无事实拒绝用例，并区分送达 ETA 与发货/揽收 ETA；相关 10 文件回归仍为 `179 passed`。

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "fix(cs): recursively constrain customer service facts"`。

### Task 6: Phase 5 前端会话 Pending 恢复、确认卡和真实进度

**Files:**
- Modify: `frontend/src/store/csChat.ts`
- Modify: `frontend/src/api/cs.ts`
- Modify: `frontend/src/hooks/useCSChat.ts`
- Modify: `frontend/src/components/cs/CSDrawer.tsx`
- Modify: `frontend/src/components/cs/CSConfirmCard.tsx`
- Modify: `frontend/src/components/cs/CSMessageList.tsx`
- Modify: `frontend/src/components/cs/CSInput.tsx`
- Modify: `frontend/src/lib/types.ts`
- Modify: `backend/app/api/routes/cs_admin.py`
- Modify: `backend/orchestration/graph/events.py`
- Modify: `backend/orchestration/graph/runner.py`
- Test: `frontend/src/store/csChat.test.ts`
- Test: `frontend/src/api/cs.test.ts`
- Create: `frontend/src/hooks/useCSChat.test.tsx`
- Create: `frontend/src/components/cs/CSConfirmCard.test.tsx`
- Create: `frontend/src/components/cs/CSDrawer.test.tsx`
- Create: `frontend/src/components/cs/CSMessageList.test.tsx`
- Test: `backend/tests/orchestration/graph/test_reply_source.py`
- Create: `backend/tests/api/test_cs_pending_api.py`
- Test: `frontend/e2e/cs-pending-confirmation.spec.ts`

**Interfaces:** Store `pendingBySession: Record<session_id, PendingSnapshot | null>` 以会话隔离，写口 `setPendingAction(session_id, snapshotOrNull)`；SSE `pending_action` 缺省表示“不变”，显式 null 表示清空。打开/切换/刷新经 authenticated endpoint 恢复 `{proposal_id,version,action_type,masked_target,summary,expires_at,state}`。确认提交 `{proposal_id,expected_version,client_action_id,session_id,decision}`；重复点击禁用；409 后 GET authoritative state 并展示解释。业务进度完全来自 status/log/SSE 事件，不用定时器。

- [x] **Step 1: 写 Store/API/组件和响应式浏览器测试并观察 RED**

覆盖两个会话独立 pending、SSE 缺字段不清空、显式 null 清空、刷新及切换会话恢复、请求期间双击仅发送一次、409 展示最新状态、真实事件形成进度、不靠 `setTimeout` 伪造业务阶段。Playwright viewport 至少 390px、中间断点及桌面。

```typescript
it('retains a session proposal when SSE omits pending_action', () => {
  useCSChatStore.getState().setPendingAction('cs1', pendingFixture)
  useCSChatStore.getState().addStreamEvent({ event: 'done', data: {} }, 'cs1')
  expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(pendingFixture)
})
```

记录：先新增测试；原实现下 Pending Store 三条用例 RED（缺少 `setPendingAction`），待确认 API 五条用例 RED（路由 404），会话切换 UI RED（无会话选择器）。增加实现后这些目标用例全部转绿。

- [x] **Step 2: 实现 typed client 和状态恢复**

从服务端权威数据重建 store；confirm API 绑定版本与幂等 id；将存在 pending 和 human queue 的禁用规则用于现有入口/抽屉，不新增页面。头部会话选择器可切换已恢复会话，pending map 随 session_id 独立读取。

- [x] **Step 3: 实现确认卡、真实事件进度与 Handoff 状态**

确认卡显示脱敏操作/目标/摘要/期限/状态和按钮；Handoff 队列使用后端真实 deadline/status，human_active 禁止 AI 业务入口。

- [ ] **Step 4: 运行前端验证与三尺寸浏览器走查**

进入 `frontend` 目录，运行 `npx tsc --noEmit`。在同一目录单独运行 `npm test`，再单独运行 `npm run build`。随后按 Playwright 技能启动真实本机浏览器操作，报告每个尺寸、刷新/切换、确认/取消/409 的截图及操作证据。

当前自动验证：`npx tsc --noEmit` 通过；`npm test` 52 文件/441 用例通过；`npm run build` 退出码 0；后端待确认/确认/SSE 事件目标回归 41 条通过。E2E 脚本已覆盖 390px/768px/1280px、刷新/切换、确认/取消/409 与截图输出。真实浏览器走查暂缓：Phase 6 明确要求 P0 自动化全绿后才启动本机服务；当前尚未完成 Phase 6 P0 门禁。

- [x] **Step 5: Commit Phase 5**

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "feat(cs): restore pending state and show live progress"`。

阶段提交：`825665e feat(cs): restore pending state and show live progress`。Phase 5 浏览器自动化文件已提交；实际浏览器走查待 Phase 6 P0 自动化门禁通过后补记证据。

### Task 7: Phase 6 端到端 P0 验收、报告和本机切换

**Files:**
- Modify: `docs/reports/2026-10-08-客服Agent多轮组合任务与LLM闭环改造验收.md`
- Modify: 仅修复本阶段验证真实发现且有 RED 测试证明的代码文件
- Test: `backend/tests/customer_service/`
- Test: `backend/tests/api/test_cs_confirm_api.py`
- Test: `backend/tests/e2e/test_cs_multiturn_p0.py`
- Test: `frontend/e2e/cs-pending-confirmation.spec.ts`

**Interfaces:** 报告为全部 P0 清单逐项记证据及最终 true/false；生产就绪只有满足全部用户定义 P0 才可 true。无证据、环境不可用、基线失败未排除均保留 false。交付记录 commit 列表、迁移前置检查、P0 失败时代码回退版本及兼容迁移说明。

- [ ] **Step 1: 写并运行端到端测试矩阵**

使用合成用户经过 APISIX→真实本机 app→PostgreSQL→Sandbox，验证 stream 的 FAQ、Pending FAQ/物流、确认/取消、组合任务真/假/未知、DB/Sandbox/LLM 故障、并发确认、跨用户/租户、人工排队/接管、SSE 断线重连和 prompt/token/trace。所有写操作只针对 Sandbox 合成记录。

```python
def test_conditional_refund_stream_creates_pending_but_does_not_execute(cs_client, sandbox):
    response = cs_client.stream("查下物流，如果没发货就申请退款", user_id="cs_phase0_probe")
    assert response.final_event["pending_action"]["confirmation_state"] == "pending"
    assert sandbox.refund_execution_count == 0
```

- [ ] **Step 2: 跑全部指定自动化**

分别运行 `D:/Python/python.exe -m pytest backend/tests/customer_service -q --no-cov`、`D:/Python/python.exe -m pytest backend/tests/api/test_cs_confirm_api.py -q --no-cov`，再进入 `frontend` 目录运行 `npx tsc --noEmit`，单独运行 `npm test`，单独运行 `npm run build`；将每条命令、退出码和输出证据写进报告，不隐藏未通过项。

- [ ] **Step 3: 按项目运维纪律切换本机服务**

只有 P0 自动化全绿后，使用仓库 `devctl.bat` 正式入口从干净 worktree 运行代码；确认迁移向后兼容，使用 APISIX 和浏览器重复主 E2E。不得借主工作区未提交旅行改动构建客服镜像。若任何 P0 不通过，不切换服务、按恢复 commit 回退代码并保留迁移数据。

- [ ] **Step 4: 更新最终验收报告与回滚操作**

包含审计/容器版本、分阶段 commit、Graph/状态矩阵、任务契约、confirm 契约、Sandbox 来源、事实安全、浏览器尺寸截图、PG/SSE/HTTP/Trace/成本证据、安全/黄金集/回归结果、逐项 P0/P1 和可执行回滚。只把有运行证据的门禁置 true；`CS_PRODUCTION_READY=true` 必须同时满足用户规格全部 P0。

- [ ] **Step 5: Commit Phase 6**

阶段提交：先 `git status --short` 并只暂存本任务 Files 列表中的路径，再检查 `git diff --cached --stat` 和 `git diff --cached --check`，最后运行 `git commit -m "test(cs): record p0 end-to-end release evidence"`。

**Expected:** P0 全部有实测 PASS 才可直接切换本机服务并标生产就绪；否则留下可回滚代码提交、兼容数据库和准确列明阻断项的验收报告，不虚报上线。
