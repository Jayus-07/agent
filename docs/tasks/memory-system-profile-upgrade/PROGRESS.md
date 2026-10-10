# 进度与续工记录

## 2026-10-10：计划整理

- 已读取两份粘贴任务说明、仓库 AGENTS.md 和 testing-guide.md，并检查 Git 状态、差异概况与记忆相关源码入口。
- 已将三层记忆主线和动态画像深化合并为 [TASK.md](TASK.md)，按照 Phase 0–7 排序，列出阶段门禁、双 P0 验收、六个月演化与第七个月新会话、测试和交付要求。
- 初步源码证据：现有 MemoryRecord 已有 tenant_id、memory_key、structured_value、origin、is_active、superseded_by；已有冲突裁决、画像 records 查询、Context Budget、相关测试及 Celery 衰减任务。仅完成静态定位，不代表真实能力通过。
- 工作区包含大量已有未提交改动，涉及 Memory、Context Budget、API、迁移及其他模块；本次仅新增本任务文档，不修改业务代码或其他任务文件。
- 未执行：生产能力审计、API/数据库验证、业务测试、迁移、服务切换、Git Commit。所有实施阶段和功能验收均未完成。
- 文档 T0 校验：Python 检查阶段 0–7、六个月及新会话、双就绪门禁、删除/乱序、测试分层、进度链接和代码围栏，7 项通过；定向 git diff --check 无报错（新文件未跟踪，不将此结果视为完整内容检查）。

## 2026-10-10：Phase 0 审计与首个 P0 修复

- 已新建并切换到 `codex/memory-system-profile-upgrade`。切换前工作区有 1,216 条状态行；新分支保留了这批既有未提交改动。当前相关差异覆盖 MemoryService、memory API、Context Budget 和多份测试/迁移，后续只按小范围审阅，不回滚或批量重写。
- 已读取 docs/README.md、testing-guide.md、ai-runtime.md、domain-service-map.md 及 Memory/Runtime/身份/Checkpoint/Celery 源码。
- 真实代码现状：L1 有可配置消息窗口、连续重复消息裁剪；L2 会话消息与摘要分列，增量摘要有水位线 CAS，主图持久 Checkpoint 默认关闭且生产配置有硬失败策略；Context Budget/L4-L5 已有公共实现。
- L3 已有用户证据片段校验、PII 清理、origin、tenant/user SQL 过滤、向量检索相关性门、memory_key/structured_value 和同一活跃 key 唯一约束及原子 supersede。现有模型没有 scope/domain/显式递增版本/verification 状态字段，key 唯一范围仅 tenant+user；目前这些限制与动态画像验收的域级隔离、演化审计仍有差距。
- end_turn 在 L2 写入后用 `asyncio.ensure_future` 触发摘要和 L3 提取；未发现提取任务进入持久 Celery 队列或写入事件幂等账本的证据。进程退出、重复执行、旧事件晚完成和删除后重放尚未通过实测证明安全。
- profile 目前由 `GET /memory/profile` 返回受限条数的 active 记忆 records；没有发现用户长期记忆的授权删除 API/服务路径。当前模型无持久画像缓存，但检索权限过滤及删除后旧任务复活仍需补足。
- 发现并修复 `GET /memory/profile` 只传 user_id、遗漏服务端可信 tenant_id 的问题：路由现从同一次认证身份读取用户和租户并一同传到 MemoryService，增加了伪造 query tenant 不生效的回归测试。此修复只影响画像 API，不改变会话 API 契约。
- 只读执行 `python backend/scripts/verify_migration_state.py`：repo 登记 91/91；权威 PostgreSQL 两库迁移均已应用，`MIGRATION_STATE_OK`。此前首次调用路径错误（脚本实际在 backend/scripts），未产生副作用。
- 定向测试 `python -m pytest backend/tests/api/test_memory_routes.py -q --no-cov`：14 passed，10.86s；定向 `git diff --check` 无 whitespace error。测试验证画像 API tenant 透传，非真实 Agent/记忆 DB E2E。
- 基线黄金集文件目前 60 行；旧结果文件存在但尚未核验评测版本、真实运行环境及指标可比性，不能作为本任务基线。真实 API/Agent、准确率、误写率、延迟和成本测量尚未进行，Phase 0 尚未完成。
- 后续重点：建立隔离验证身份并检查 schema 实表；量清服务侧身份/tenant/domain 是否贯通各业务域；再基于实际差距设计 additive migration、可靠事件屏障、确定性删除及动态 profile 投影。

## 2026-10-10：从整合 main 建立隔离实施 Worktree

- 当前实施目录为 `C:\Users\wh\.codex\worktrees\memory-profile-main\agent`，分支 `codex/memory-profile-upgrade-main`，HEAD 基线精确为整合 `main` 的 `098632abfc59ae9c37bb2f9aef3d15f9523b4e8e`。共享主目录、旧实现 Worktree 和整合 main Worktree 均未修改。
- 已从旧实现 Worktree 逐项迁入本任务的记忆服务、模型、仓储、API、配置、队列、迁移及测试差异；迁移时发现 `runner.py` 的记忆域转发和成功终态保护已存在于基线 main，因此没有重复改动。当前代码有 L2 Token 触发、画像投影/确认/删除、scope/domain/version、删除屏障及 L3 durable outbox；尚未证明完整业务验收。
- 与旅游的关系目前仅是记忆域映射及 `backend/travel/memory/__init__.py` 的横向能力说明；本次没有迁入其他会话留下的旅游业务 UI/行程改动。按原任务要求暂不建设复杂画像前端。
- 只读 `D:/Python/python.exe backend/scripts/verify_migration_state.py`（进程内加载共享主目录 `.env`，`PGPORT=5433`）：仓库目录登记 94/94、权威本机双库记录迁移 94 项，`MIGRATION_STATE_OK`。本步骤没有执行迁移。
- T0：触及的 Python 源文件 `py_compile` 通过；`git diff --check` 通过；冲突标记搜索没有发现本次文件中的冲突标记。
- T1/T2：`profile_projection`、`memory_manager_timeout`、forget Tool 和 memory route 定向批次 23 passed / 9.33s；PostgreSQL provenance、conflict、delete barrier、extraction outbox、pgvector、profile、STOP E 与 route 批次 76 passed / 163.06s。后一批在 Windows 出现 15 条 asyncio Proactor 资源清理警告，pytest 退出成功。
- 契约/路由/治理：`test_registry_consistency.py`、`test_tool_contract_lock.py`、`test_task_queue_router.py`、治理 Runtime 和 forget Tool 批次 63 passed / 320.78s。契约锁新增 forget Tool 和可选 `domain` 参数；生成器原样写入 Worktree 绝对路径，故新增规范化逻辑使仓库内模块记录相对仓库根目录，并增加跨 Worktree 回归断言（该新断言尚待复测）。
- 契约快照复核：`gen_tool_contract_lock --check --json` 返回 `IN_SYNC`；重新生成相对路径快照后 `backend/tests/test_tool_contract_lock.py` 为 18 passed / 8.45s。生成脚本尝试把变更写入治理台账时因该命令进程未加载数据库密码而软失败，锁文件仍成功生成；没有伪报治理台账已写入。
- 会话分页新增 T2 回归：新增 API 游标透传断言后 `backend/tests/api/test_memory_routes.py` 为 19 passed / 10.43s。新增真实 PostgreSQL 同时间戳分页测试首轮失败，证实 schema 列为 `timestamp without time zone` 而 ORM 类型提示造成游标按 `timestamptz` 绑定、下一页漏行；仓储现将排序游标比较约束为 timestamp 并把带时区游标归一到 UTC。重跑 `backend/tests/memory/test_session_pagination.py` 为 1 passed / 15.75s。
- 数据副作用更正：本次 76 项 PG 批次包含 `backend/tests/memory/test_profile_endpoint.py`。复核当前源码确认其 fixture 没有 `DELETE`，每例使用随机唯一 user ID 且测试数据不自动清理；只读查询此刻看到 `profile-test-` 前缀记忆 110 行、会话 0 行。由于未记录测试前基数，无法区分既有行和本次新增行；未尝试清理，保留所有记录。此前续工备注关于该文件会删除该前缀记录的提醒与当前源码不符。
- 目前没有 Git Commit 或推送。上述通过只覆盖定向测试，不代表 30 项完整 Memory golden、6 个月画像演化、真实认证 Agent E2E、生产就绪或 Phase 0–7 完成。

## 下一步

继续核对迁移、删除/权限、事件顺序和 worker 失败状态的边界；随后进入剩余 Phase 1–7 门禁。优先补齐真实业务域身份矩阵、完整记忆 Context Builder/Checkpoint 与 M/MP 黄金验收。Phase 0 的准确率、误写率、延迟和成本基线、6 个月演化及真实 Agent E2E 仍未采集，不能据当前定向通过宣称生产就绪。

## 当前风险与待核验

- 多个目标文件已有修改；不能覆盖他人改动或将其作为本任务提交。
- 已在 2026-10-10 只读核验当前迁移状态与本机 health；Redis 可连接。Celery worker、Checkpoint 恢复和 Provider 凭据在真实任务链路中的可用性仍未验收。
- 结构化画像投影、候选确认/删除、scope/domain/version 和队列状态机已有代码及定向 PG 覆盖；完整跨业务域授权矩阵与六个月演化仍未验收。
- 本机 5433 已存在 088/089 且迁移核验为全量一致；生产部署、回滚演练和多 worker/broker 故障恢复仍未验收。
- 现有租户归一默认值与版本链方案需结合认证入口核验，不能直接认定满足多租户及新版本契约。

## 2026-10-10：Phase 2 L2 Token 触发与 Phase 3 删除/画像首批实现

- 已在 `backend/config/memory.py` 增加 `CONTEXT_L2_SUMMARY_TRIGGER_TOKENS`，默认复用 `HISTORY_TOKEN_BUDGET`。摘要读取 frontier 后的有界增量，Token 达阈值时可绕过小批消息数攒批门；旧的会话总消息数上限仍保留，最近轮保护与 CAS 不变。
- `python -m pytest backend/tests/memory/test_stop_e_summary_contract.py -q --no-cov`：6 passed。Python 编译通过；pytest 出现 Windows asyncio `ProactorBasePipeTransport.__del__` event-loop-closed 警告，不影响退出码。
- 修复 `GET /memory/profile` 漏传可信 tenant_id；新增认证用户删除本人画像记忆的 `DELETE /memory/profile/{memory_id}`，租户/用户只从服务端身份读取。
- 新增删除屏障：按 tenant/user/key（或 unkeyed 内容指纹）取得 PostgreSQL advisory transaction lock，物理清理正文和 vector；keyed 版本链先断开自引用版本关系再删除，避免外键阻断。只保留空正文、无 embedding 的 inactive tombstone。删除前来源事件重放被阻止；删除后新来源可再写入。显式 key 与 unkeyed tombstone 查询相互隔离。
- 增加 `memory_forget_tool`，检索输出携带 memory_id；删除作为 Governance `operation=delete`、`risk_level=R1`、`user_confirmation`，服务端身份不由模型提供。已运行 `backend/scripts/gen_tool_contract_lock.py` 更新生成快照。
- 新增 Profile Projection：从全部当前 eligible active structured records 按 memory_key 点分段构造映射；仅转换无歧义布尔值，不拼接/总结自由文本。records 展示仍受 limit 控制，`total`/`has_more` 显示分页状态，projection 使用完整记录集。
- 新增无 DB 画像投影测试、遗忘 Tool 测试及 UUID 隔离 PostgreSQL 删除/版本链/旧重放/新来源/越租户/完整投影测试。`python -m pytest backend/tests/tools/test_memory_forget_tool.py backend/tests/tool_governance/test_runtime.py backend/tests/api/test_memory_routes.py backend/tests/memory/test_profile_projection.py backend/tests/memory/test_memory_delete_barrier.py -q --no-cov`：33 passed，27.35s；真实 PG 删除测试本次 3 项均执行通过（隔离 user 前缀 `memdel-<随机>`，未运行危险的 `profile-test-` 测试）。有相同 Windows asyncio 警告。
- `python -m pytest backend/tests/test_registry_consistency.py backend/tests/tool_governance/test_runtime.py backend/tests/tools/test_memory_forget_tool.py -q --no-cov`：30 passed，53.09s；`gen_tool_contract_lock --check --json` 的漂移仅为新增 `memory_forget_tool`，随后运行生成器写入锁文件。
- 上述工具/路由门禁首次运行时发现 `backend/orchestration/router/capability_router.py` 文件末尾已有字面量 `\\n` 导致 SyntaxError；查看差异确认仅删除该孤立字面量后路由清单测试恢复通过，其余文件中已有改动保留。
- 当前仍未完成：Phase 0 的真实 Agent 基线与性能/成本测量；schema scope/domain/version/verification 和检索授权过滤；可靠 Celery 提取队列/状态查询；Checkpoint/L1/Context Builder/全业务域隔离；黄金集、六个月演化以及真实 Agent E2E。未运行迁移、服务切换、全量测试或 Git Commit。

## 续工要求

每阶段更新实际文件范围、命令、数量与耗时、证据/Trace ID、Commit、阻塞和下一步；仅以真实结果勾选 TASK.md。未执行验证不得写 PASS，也不得宣称生产就绪。

## 2026-10-10：停止点——可信业务域贯通记忆检索 Tool

- 将 `domain_hint` 从 GraphRunner 入口写入权威 `RequestContext`，并经 `checkpoint_safe()` / `get_context_from_state()` 还原及 `bind()` 绑定到 Tool ContextVar。字段放在 dataclass 末尾，保留现有位置参数顺序；旧 checkpoint 缺少该字段时默认为空，并由绑定清除线程池遗留域。
- `memory_search_tool` 不再向模型公开 `domain` 参数；检索域仅从已绑定请求上下文读取并传给 MemoryService，工具测试同时确认参数 schema 中没有 `domain` 且 travel 上下文实际传入服务。
- 生成 `backend/tool_contracts.lock.json` 后，`D:/Python/python.exe -m backend.scripts.gen_tool_contract_lock --check --json` 返回 `IN_SYNC`。移除参数依生成规则分类为 BREAKING，作为本次隔离目标的有意契约变更记录。生成器尝试写治理变更台账时因本进程未加载 PostgreSQL 密码软失败；lock 已成功生成，此台账未确认写入。
- `py_compile`：本切片触及的 8 个 Python 文件通过。
- 定向 T1/T2：`backend/tests/orchestration/test_request_context.py`、`backend/tests/tools/test_memory_domain_scope.py`、`backend/tests/api/test_memory_routes.py`、`backend/tests/tools/test_memory_forget_tool.py`、`backend/tests/test_tool_contract_lock.py`：52 passed，11.17s。覆盖可信域绑定、checkpoint 往返和旧 checkpoint 清理、Tool schema/透传、既有记忆 API/遗忘契约及契约锁。
- 七个月画像场景已有独立数据/服务层验证：`backend/tests/memory/test_profile_evolution.py` 为 1 passed（12.62s）。这是确定性数据库服务测试，不等价于真实 LLM Agent E2E；真实 Agent E2E、准确率/误写率、性能成本基线与生产故障恢复仍未完成。
- T0 `git diff --check` 无 whitespace error；Git 对生成的 lock 文件提示工作副本 CRLF 将规范化为 LF。没有运行迁移，也没有改共享主目录或整合 main 工作树。
- 本停止点包含此前已迁入的记忆/Profile/outbox/删除屏障及其定向测试差异（共 36 个已跟踪文件修改、13 个新文件/目录；提交前已跟踪文件统计为 1,407 insertions / 213 deletions）。功能实现已提交到 `codex/memory-profile-upgrade-main`，SHA `3679821`。后续不进入更多 Phase，先处理合并。
- 合并约束：整合 `main` 工作树 `C:\Users\wh\.codex\worktrees\tool-governance-runtime\agent` 仍处于 HEAD `098632a`，有 851 条其他会话暂存状态。该工作树的 index/文件内容不可由本任务改写，因此本次没有执行合并。功能提交是 `098632a` 的直接子提交，干净目标上可 fast-forward；待共享工作树中的暂存工作由其归属会话安全收口后再更新 `main`。未 stash、清理或迁移他人改动。
