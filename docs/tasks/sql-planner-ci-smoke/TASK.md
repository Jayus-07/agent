# SQL 与 Planner 业务域自动化 CI 门禁流水线

## 1. 目标

为平台核心业务域 **SQL Agent** 与 **Planner（任务规划）** 建立轻量级、自动化、零额外模型付费成本的 CI 门禁流水线（GitHub Actions Workflow）。
解决此前仅有 RAG 拥有自动化 smoke 流水线、而 SQL 和 Planner 依赖本地人工评测的短板，确保任何触及规划、SQL 查询、数据权限和结构契约的代码变更在 PR 阶段被自动化阻断拦截。

## 2. 覆盖范围与断言层级

采用分层门禁策略（E0 资产与契约层 + E1 确定性单元与沙箱校验）：

### 2.1 Planner 门禁覆盖面
1. **E0 数据集与规范完整性**：
   - `backend/evaluation/datasets/planner/cases.jsonl` 与 `manifest.json` 的 content_hash 强校验；
   - 期望能力（capabilities）、依赖边（edges）和步骤范围（min_steps/max_steps）校验；
   - 依赖图拓扑排序与严格有向无环图（DAG 无环）校验。
2. **E1 规划契约与降级分类**：
   - 输出契约断言：节点字段、授权 capability、步骤唯一性、最大依赖深度；
   - 异常分类断言：运行异常 vs 结构失效 vs 预算超限分类（fail-closed 机制）。

### 2.2 SQL Agent 门禁覆盖面
1. **E0 数据集与真值 SQL 完整性**：
   - `backend/evaluation/datasets/sql/cases.jsonl` 与 `manifest.json` content_hash 校验；
   - 正向用例必需包含合法 `gold_sql` 与 `expected_tables`；拒答用例必需包含 `expected_status`。
2. **E1 安全与语法契约断言**：
   - 注入防御：对恶意 SQL 注入模式、堆叠查询（Stacked Queries）、绕过模式的阻断测试；
   - 安全表白名单与行级/租户级隔离契约；
   - 只读事务与参数化执行校验，禁止宽权限修改行为。

## 3. GitHub Actions 流水线设计 (`.github/workflows/sql_planner_smoke.yml`)

1. **触发路径过滤（Path Filter）**：
   - 触发文件变更：
     - `backend/agents/planner/**`
     - `backend/agents/sql/**`
     - `backend/evaluation/runners/planner.py`
     - `backend/evaluation/runners/sql.py`
     - `backend/evaluation/datasets/planner/**`
     - `backend/evaluation/datasets/sql/**`
     - 相关单元测试与本 workflow 文件。
2. **环境与运行成本**：
   - 纯免费运行（Free tier, no cloud API secret required）；
   - 使用确定性 Mock 与沙箱库，执行时间控制在 2 分钟以内；
   - 阻断机制：任一 E0 损坏或核心契约失败，PR 状态标记为 Failure，禁止合入 main。

## 4. 验收标准

1. Workflow YAML 语法通过校验，触发条件与路径过滤器精准。
2. 本地定向运行全量包含的测试集合，全部 PASS。
3. 纳入统一文档体系与测试策略。
