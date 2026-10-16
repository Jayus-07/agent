# PR-INTEGRATION-PHASE2-20261016 进度

## 基线

- 当前修复分支：`codex/price-monitor-followup-20261016`
- 起始 `main` / PR #81 merge SHA：`89315c9d32b383d31dd390d7a90dbe6b9c4eb75b`；本轮核对的 `origin/main` 同 SHA。
- 主 checkout 存在其他会话未提交文件；本任务未修改。
- P0-A Follow-up #81 已合并：merge SHA `89315c9d32b383d31dd390d7a90dbe6b9c4eb75b`，commit `f1ca05cb95d13aec9bd837fab3bffb703bc81a6f`；定向验证 84 passed，隔离 PostgreSQL 验证，无 Provider 调用。

## 当前批次：P1-A #79 价格巡检

- 已完成本地实现：迁移 100 持久化价格变化告警 outbox；数据库事务内保存报价和待处理事件；租约、重试恢复、已触发状态及未知送达状态。Worker 强制终止和未启动任务通过 Celery `task_revoked` 生命周期信号对账为 `cancelled`；`terminate=False` 的活动任务继续运行，不会误标取消；软超时仍传播并持久化为 `failed`。
- 已完成本地实现：Doubao Seed 2.0 mini 仅支持已登记的 `[0, 32K]` 价格档；代理、旅游/RAG 裸调用及云端评测生成入口在预算预占或 Provider 请求前拒绝超限输入，无法识别的输入形态失败关闭。代理按 `invoke` 与 `batch` 调用语义分别累计单条输入或计算最大批次输入，防止用一批字符串逃过 32K 价格门禁。官方价格页更新时间为 2026-10-15，链接见价格巡检治理文档。
- 隔离库：Docker PostgreSQL `codex-pr77-price-isolation-20261016`，只绑定 `127.0.0.1:55462`，数据库 `agent_memory_test`；未连接生产或共享业务库。
- 初始定向结果：价格巡检单测与隔离 PostgreSQL outbox 集成、Context Budget、价格快照、流式 usage、旅游/RAG 调用路径、评测模型路由共 `161 passed`；没有 Provider 调用。
- 自动 Review 发现 3 项后已修复：价格巡检管理端补 `cancelled` 状态；新增 Celery revoke 信号对账并在隔离 PostgreSQL 验证活动终止、未启动取消和 `terminate=False`；价格档位预检区分 `invoke` 和 `batch` 字符串语义。
- 修复 Review 后最新定向结果：价格巡检 + 隔离 PostgreSQL outbox + Context Budget + 流式 usage `75 passed`；包含报价提交后 Worker 失败、同日重跑恢复、Celery 生命周期终态和 32K 边界拒绝。单独的管理端价格巡检、模型健康 UI 与类型测试 `60 passed`。隔离 PostgreSQL 仍仅使用本机 `127.0.0.1:55462/agent_memory_test`。没有 Provider 调用。
- 静态结果：修改源码 `py_compile`、`git diff --check` 通过；排除仓库现存 Ruff 规则诊断后，对本批改动文件的剩余检查通过。完整 Ruff 输出中的既有异常命名、历史导入顺序和未用变量不属于本批修改范围。
- PR #82 已创建：[fix(model-prices): recover alerts and enforce pricing boundaries](https://github.com/Jayus-07/agent/pull/82)。其首个自动 Review 针对 `908acbc` 提出上述 3 项，当前均已修复；新提交推送后需等待最新 HEAD 的自动 Review 和必需 CI。首轮免费检查通过；`RAG Smoke Evaluation (8 cases, real API)` 因未启用合法 Provider 凭据而 skipped，故不能记为 `MODEL_QUALITY_PASS`。未发生真实模型调用。
- PR #73 Follow-up 已在独立 Worktree `C:\Users\wh\.codex\worktrees\model-health-followup\agent` 完成本地代码与测试：后端定向 `43 passed`，与价格巡检管理端一并运行的前端定向 `60 passed`；尚未提交或创建 PR。下一步先收口 #82 最新 HEAD Review / CI，然后提交该分支并创建 #73 Follow-up。

## 后续门禁

- P1-C #74：尚未施工；待 #82 和 #73 Follow-up 最新 Review / CI 收口后继续。
- P2：待核实正式价格治理与隔离评测权限、evaluation-only 价格快照和累计预算硬边界；在能证明剩余授权上限前不发起真实 Provider 调用。
- P3：#52、#66、#69 当前 HEAD、依赖、CI、真实端到端门禁待核对。
- P4：097/098/099 只允许在隔离库验证；目标部署数据库状态及授权尚未核实。
- P5：每个 Follow-up PR 均需在最新 HEAD 收口 review 和必需 CI 后正常合并。
