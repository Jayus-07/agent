# SQL 与 Planner CI 门禁流水线 进度追踪

## 当前状态

`DONE_LOCAL_VERIFIED`。工作树 `D:\Program Files\workplace\agent-sql-planner-ci`（分支 `feat/sql-planner-ci-smoke`）已完成全部实现与定向断言验证。

## 进展与交付证据

1. **新建 `.github/workflows/sql_planner_smoke.yml` 自动化流水线**：
   - 触发路径：严格绑定 `backend/agents/planner/`、`backend/agents/sql/`、`backend/sql/`、评测 Runner 与测试文件；
   - 包含阶段：
     - E0 阶段：自动校验 Planner（20 样本）与 SQL（15 样本）的锁版数据集、manifest content_hash、DAG 无环与字段规范；
     - Planner 阶段：回归测试输出契约、依赖边解析、步骤界限与降级分类；
     - SQL 阶段：回归测试注入渗透、行级隔离、参数化校验与只读守卫；
   - 纯免费运行（No cloud API required），执行时间约 20 秒。

2. **断言健全性修复与沙箱隔离**：
   - 修复 `test_planner_params.py`：对齐因商户试点调整后 `planner_params.json` 真实 6 条样本的守卫断言；
   - 完善 `backend/tests/sql/conftest.py`：在离线 SQL 单元测试中隔离 `get_knowledge_bases` 外部 DB 依赖，满足"纯内存 AST + policy，零外部 DB 副作用"的规范要求。

3. **测试验证数据**：
   - 本地模拟 E0 预检命令：Planner (20 cases) OK, SQL (15 cases) OK；
   - 全量回归测试：**211 项测试全部 PASSED**（包括 94 项 SQL 注入与策略测试、8 项参数测试、80+ 项规划契约测试与 29 项 Schema 预检测试）。
   - 工作流 YAML 语法解析验证有效。
