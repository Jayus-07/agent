# PROGRESS — ci-regression-debt

> 记录证据、阻塞和下一步。每消化一批即更新。

## 2026-10-30 开工

- 环境核对：本地 main = origin/main = 2517e98（≥ b4c881f）✓
- 工作树：`agent-wt-ci-debt`（分支 fix/ci-debt-hangup，自 origin/main 2517e98 创建），.env 已从主仓库复制。
- 任务文档建立。

## 专项一：api/rag 挂死用例

### 关键转折：api suite 不是挂死问题（2026-10-30）

- 命令：`D:/Python/python.exe -m pytest backend/tests/api --timeout=150 -n 4 --dist loadfile -q --no-cov`（worktree 内，输出 /d/tmp/ci-debt/api_run1.txt）
- 结果：**59 failed / 816 passed / 0 Timeout，10.5 分钟跑完**。150s 超时一次未触发——api 在本地无挂死用例，此前 CI 被杀是收集崩溃（已根修）与失败用例耗时的历史叠加，不是持续挂死。
- 基线失败清单复核：GitHub artifacts 重新下载（本地 d:/tmp/regmap 的 zip 全部损坏不可用），11 个清单合计 108 行与基线 v1 一致；`failures-api` 为空（被杀前未落盘）。
- **59 个失败全部定性为测试资产漂移**（重构后测试未同步），非产品 bug：
  - **A 组 travel V1 退役（35 例）**：b178338 把全部 V1 端点搬进 `_retired_v1_router` 且无处挂载（`tests/travel_v2/test_legacy_persistence_retired.py` 显式守卫「不得挂载」）；V1 函数路径因账本退役 fail-closed 必失败。candidates/plan_versions 两个文件整文件测退役端点 → 删除（git rm）；auth/trace/checkpoint/request_execution 迁移到 V2 入口（`travel_v2_plan_stream`），并补 V2 stream 的 401 边界用例。
  - **B 组 rbac 分离（16 例）**：9d7c582「客服与部门管理员分离」重构未同步任何后端测试。users 列表字段契约变化（去 csAgent 加 email）；cs 档案字段从 users PATCH 拆到 PATCH `/customer-service/team/{id}`（会话吊销语义完整保留，测试迁移后继续覆盖）；GET /users 改 `require_rbac_access`（平台管理员 OR 部门主管）；users 详情去 csRoles；departments 表新增 department_type/manager_user_id 与 _change_department 重写。修复 = fake 替身扩列 + 用例迁新端点 + 断言对齐新契约。
  - **C 组其他（8 例）**：trace_children → get_trace 资源级授权迁移（身份打桩 + list_children 带 tenant_id）；selection → PUT /weights 加 require_admin_user（测试补 admin 身份头）；cs_pending → 投影新增 reason 字段（补断言）；automations scan → **单跑通过，xdist 共享 DB 竞争，归入专项三**。

### 批次验证记录

- 批 1 travel：test_travel_auth(9) + trace(2) + checkpoint(2) + request_execution(10) = 23 passed；删除 test_travel_candidates_api.py、test_travel_plan_versions_api.py。
- 批 2 rbac：test_rbac_api.py 33 passed、department_management 6 passed、user_lifecycle 端到端 passed、auth_session_family 端到端 passed。
- 批 3：trace_children 2 passed、selection 10 passed、cs_pending 5 passed。
- api 全套第二轮回归：RUNNING（api_run2.txt）

### 已知实证（勿重走弯路）

- 单 job 一次性全量跑不完（挂死用例排队超 180min，进程被杀失败清单也不落盘）——只能 matrix 按 suite 跑。
- 本地全量跑到 ~46% 会挂死——本机 D:/Python 已装 pytest-timeout，测试务必带 `--timeout=150`。
- rag/api 在 50min job 上限下跑不完——api 修复合入后 dispatch suites=api 验证即可判定。
- CI artifacts 的本地 zip 副本易损坏，一律 `gh run download` 现取。

## 阻塞

（无）

## 下一步

1. api 第二轮本地回归 → 剩余失败复核（预期仅 automations 并发敏感 1 例）→ 提 PR。
2. PR 合并后 `HTTPS_PROXY=http://127.0.0.1:7897 gh workflow run full_regression.yml -f suites=api` 验证 CI 收敛。
3. 专项二：demo 库环境补齐——`DC_DATABASE_URL` 默认 `localhost:5432/demo`（backend/config/data_collection.py:29），CI workflow「Initialize isolated databases」步加 CREATE DATABASE demo + 初始化（预期消化 data_collection 12 例 + sql-infra 部分用例）。
4. 专项三：并发敏感（orchestration/travel/customer_service ~41 + misc inventory 10 + automations scan）。
5. rag 基线补取。
