# B2 进度

## 基线

- Base branch: `codex/t1-refund-policy`（PR #61）
- Base HEAD: `56589b9707748fcbb4f31ea89ae920f2fbb22be9`
- Stacked branch: `codex/refund-request-mvp-b2`
- 最新 `origin/main`: `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`

## 已核对

- `RefundService.simulate_execute` 不写业务数据；确认流程通过统一 Side Effect Idempotency，但当前 refund 执行器仍是模拟实现。
- `customer_service.cs_case` 是已存在的统一案件事实源，含 tenant/user、案件状态、上下文和确认关联，可供坐席队列处理；不需要第二张退款申请账本。
- `customer_service.confirmations` 已有 user/tenant、Proposal Version CAS、原子确认认领；退款动作的语义重复由 Business Operation Guard 拦截。
- CS 用户确认接口只接受当前 `proposal_id + expected_version + client_action_id`；本人待确认接口按可信会话 owner 返回脱敏快照。
- B2 将优先复用以上机制，并通过真实隔离 PostgreSQL 证明持久化、去重和资源隔离。

## 当前状态

`IMPLEMENTED — STACKED PR PENDING`：B2 已实现，独立分支仍以 PR #61 的 HEAD 为父提交。退款申请只写入既有 `customer_service.cs_case`，语义为“待人工审核”，不调用退款执行端点，也不声称款项已退回。申请需显式开启 sandbox 配置；缺少可信用户/租户、有效订单/金额/政策快照或安全网关状态时 fail-closed。原因编辑只允许本人修改并以 proposal version CAS 更新；查询按本人及租户范围过滤。

## 验证证据

- 后端定向 T1/T2（refund service、confirmation store/repository/idempotency、CSCase、客服 API）：`104 passed`。
- 隔离 PostgreSQL `refund_b2_test`（新退款申请真实持久化/幂等/owner scope + Side Effect Idempotency）：`28 passed`。只连接本任务一次性容器 `codex-b2-refund-pg-20261015`，没有访问共享或生产数据库；测试后已停止该容器。
- 客服前端 API/确认卡/抽屉：`13 passed`；`npx tsc --noEmit` 通过。
- 变更 Python `py_compile`、目标 Ruff 检查、`git diff --check` 均通过。
- 初次误以默认 `localhost:5432` 跑 PG 依赖测试，因本机默认配置未提供密码而报 23 个连接错误；切换到明确隔离 DSN 后，同一 PG 测试集重跑为 `28 passed`。此为环境选择错误，不是通过/隐藏的代码测试。

## 限制与下一步

- 未调用真实 Provider、未产生付费模型请求；Clash 网络下 Provider 连通性仍按 Issue #67 标注为阻断。
- 远端 Git TLS 在 Clash 环境下握手失败，无法用 `git ls-remote` 确认 main 或常规 git push；GitHub API 集成可用。以最新已核对 main SHA `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27` 和当前 PR #61 元数据为依据，继续创建 stacked PR 并检查 CI/Review。
- 待发布分支并创建 PR 后，将 PR 链接、commit、CI 和 Review 状态同步至 Issue #67；B2 依赖 #61，不能在该依赖合并前单独进入 main。
