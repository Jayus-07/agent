# 任务三：评测中心双轨收口与运行记录统一 (Dual-track Run Records Convergence)

## 背景与目标
当前评测系统存在双轨运行记录割裂问题：
1. **历史/CLI/门禁轨**：`python -m backend.evaluation.cli run`、CI smoke、security redteam 门禁等运行记录写入 `ai.eval_run_records` 表（Obs PG 库）及 `data/eval_runs/` 文件，通过 `/api/evaluation/runs` 读取。
2. **管理端 V2 轨**：评测中心 V2 发起评测记录写入 `evaluation_v2.eval_runs` 表，通过 `/api/evaluation/v2/runs` 读取。
3. **割裂痛点**：
   - `/api/evaluation/v2/runs` 无法查到 CLI / CI 门禁产生的评测，详情接口 `/api/evaluation/v2/runs/{run_id}` 限制 UUID 且不查 ledger，导致非 V2 run 无法在统一入口查看；
   - 前端 `DomainOverviewBar.tsx` 被迫发起 3 次 HTTP 请求，在前端遍历拉取的 150 条数据做域指标归并计算；
   - `RunsTab.tsx` 前端混排在分页时数据来源不一致。

## 目标与范围
1. **后端只读投影收敛（Read-side Convergence）**：
   - 保持底层各自落库与不可变契约不变，避免破坏现有租户隔离；
   - 在 `backend/evaluation_v2/repository.py` 实现 `list_runs_unified`（支持包含 `ai.eval_run_records` 来源的投影）；
   - 在 `backend/app/api/routes/evaluation_v2.py` 的 `GET /runs` 路由接入只读统一投影，支持 `include_ledger=true`（默认开启）；
   - 在 `GET /runs/{run_id}` 放宽为字符串类型，支持非 UUID 的 CLI run ID，自动回退查询 `ai.eval_run_records` 与报告文件并标准化返回。
2. **域质量总览端点（Domain Overview API）**：
   - 在 `backend/app/api/routes/evaluation_v2.py` 新增 `GET /domain-overview` 端点，服务端直接聚合全域最新通过率与运行状态，单次网络请求搞定；
   - 前端 `DomainOverviewBar.tsx` 优先调用新端点，原有前端聚合逻辑保留为安全回退。
3. **前端防重与统一展示**：
   - `RunsTab.tsx` 增加 `seenRunIds` 去重，确保无论前端是否同时启用 ledgerQuery 均不产生重复行；
   - `frontend-admin/src/api/evaluation.ts` 补充 `getDomainOverview` API 调用方法与类型定义。
4. **定向测试与回归保障**：
   - 编写 `backend/tests/evaluation_v2/test_dual_track_runs_unification.py`，覆盖统一列表投影、CLI run 详情回退、域质量总览聚合及数据库异常时的软失败回退。

## 验收标准
- [ ] `GET /api/evaluation/v2/runs` 返回结果中包含来自 `ai.eval_run_records` 的标准化记录，标记来源为 `ledger` / `cli`。
- [ ] `GET /api/evaluation/v2/runs/{run_id}` 传入非 UUID 的 CLI run_id（如 `run_xxx`）能正确读取并返回标准化响应，不报 422。
- [ ] `GET /api/evaluation/v2/domain-overview` 正确返回各域（rag, sql, cs, travel, agent, security）最新的指标与状态。
- [ ] 前端 `DomainOverviewBar.tsx` 与 `RunsTab.tsx` 类型安全，无运行时警告或重复渲染。
- [ ] 所有定向单元测试 100% 通过。
