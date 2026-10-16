# PR-INTEGRATION-PHASE2-20261016 进度

## 基线

- 当前修复分支：`codex/price-monitor-followup-20261016`
- 起始 `main` / PR #81 merge SHA：`89315c9d32b383d31dd390d7a90dbe6b9c4eb75b`；本轮核对的 `origin/main` 同 SHA。
- 主 checkout 存在其他会话未提交文件；本任务未修改。
- P0-A Follow-up #81 已合并：merge SHA `89315c9d32b383d31dd390d7a90dbe6b9c4eb75b`，commit `f1ca05cb95d13aec9bd837fab3bffb703bc81a6f`；定向验证 84 passed，隔离 PostgreSQL 验证，无 Provider 调用。

## 当前批次：P1-A #79 价格巡检

- 已完成本地实现：迁移 100 持久化价格变化告警 outbox；数据库事务内保存报价和待处理事件；租约、重试恢复、已触发状态及未知送达状态；任务级软超时和撤销分别传播并持久化为 `failed` / `cancelled`。
- 已完成本地实现：Doubao Seed 2.0 mini 仅支持已登记的 `[0, 32K]` 价格档；代理、旅游/RAG 裸调用及云端评测生成入口在预算预占或 Provider 请求前拒绝超限输入，无法识别的输入形态失败关闭。官方价格页更新时间为 2026-10-15，链接见价格巡检治理文档。
- 隔离库：Docker PostgreSQL `codex-pr77-price-isolation-20261016`，只绑定 `127.0.0.1:55462`，数据库 `agent_memory_test`；未连接生产或共享业务库。
- 最近定向结果：价格巡检单测与隔离 PostgreSQL outbox 集成、Context Budget、价格快照、流式 usage、旅游/RAG 调用路径、评测模型路由共 `161 passed`；包含报价提交后 Worker 失败、同日重跑恢复、超时/撤销终态及 32K 边界拒绝。第一次合并验证发现流式测试替身使用零参数调用，按真实接口补空输入后重跑通过。没有 Provider 调用。
- 静态结果：修改源码 `py_compile`、`git diff --check` 通过；排除仓库现存 Ruff 规则诊断后，对本批改动文件的剩余检查通过。完整 Ruff 输出中的既有异常命名、历史导入顺序和未用变量不属于本批修改范围。
- 尚未提交、推送或创建 PR；本地 diff 最终复核完成后提交并发起 Follow-up PR。

## 后续门禁

- P1-B #73、P1-C #74：待当前批次完成后分别施工和验证。
- P2：待核实正式价格治理与隔离评测权限、evaluation-only 价格快照和累计预算硬边界；在能证明剩余授权上限前不发起真实 Provider 调用。
- P3：#52、#66、#69 当前 HEAD、依赖、CI、真实端到端门禁待核对。
- P4：097/098/099 只允许在隔离库验证；目标部署数据库状态及授权尚未核实。
- P5：每个 Follow-up PR 均需在最新 HEAD 收口 review 和必需 CI 后正常合并。
