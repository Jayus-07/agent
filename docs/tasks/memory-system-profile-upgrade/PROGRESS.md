# 进度与续工记录

## 2026-10-10：迁移到 main 基线独立 Worktree

- 新 Worktree：`C:\Users\wh\.codex\worktrees\memory-travel-frontend\agent`，分支 `codex/memory-travel-frontend`，基线 `098632abfc59ae9c37bb2f9aef3d15f9523b4e8e`。
- 只移入记忆任务明确涉及的服务、模型、检索、画像投影、删除屏障、异步 outbox、分页及迁移文件。与 main 的 `runner.py` 重叠改动未搬入，main 已含较新的 domain-scoped 调用。
- 主线 Travel V2 保持原样；不迁移旧快照中删除 V2 的旅游改造。门户、登录注册、记忆分页和助手侧栏等前端改动正在逐项迁入。
- 旧共享工作区、旧记忆 Worktree、错误应用 stash 的旧客服 Worktree 和 `stash@{0}` 均保留，未清理或覆盖。
- 此次从 `098632a` 重新合并后尚未执行测试、构建或浏览器验收；此前日志里的测试结果仍是旧工作区历史证据，不等同于当前 Worktree 验收。没有运行迁移，也没有提交。

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

## 下一步

继续 Phase 3：补齐 scope/domain/version/verification schema 及授权过滤；将 L3 自动提取改为可恢复的 Celery 持久任务（任务行与 turn 同事务入库、发布失败可扫描补投）；随后按 Phase 1–7 推进剩余契约和门禁。Phase 0 性能/准确率基线尚未采集，不能据现有定向测试宣称生产就绪。

## 当前风险与待核验

- 多个目标文件已有修改；不能覆盖他人改动或将其作为本任务提交。
- 已在 2026-10-10 只读核验当前迁移状态与本机 health；Redis 可连接。Celery worker、Checkpoint 恢复和 Provider 凭据在真实任务链路中的可用性仍未验收。
- 结构化画像投影和列表截断完整性已有定向代码与 PG 测试；版本/verification/domain 授权及六个月演化仍未实现/验收。
- 未确认长期记忆提取是否使用可靠队列；已发现衰减 Celery 任务不能作为提取可靠性的证明。
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

## 2026-10-10：整合后 main 上的可合并检查点

- 当前候选分支为 `codex/memory-travel-frontend`，基线 `098632abfc59ae9c37bb2f9aef3d15f9523b4e8e`；工作目录为 `C:\Users\wh\.codex\worktrees\memory-travel-frontend\agent`。没有在共享主工作区切分支、暂存、提交或合并；旧工作区和 stash 保留。
- 从已审阅的旧记忆改动迁入画像投影、受控删除与删除屏障、来源事件 outbox/Celery 任务、L2 token 触发和相应迁移/测试。主图 runner 继续使用整合后 main 的域级实现；旅游 V2 页面实现以 main 为准，没有迁回旧版本。数据库迁移没有执行。
- 前端候选包含统一门户、助手注册/登录回跳、记住账号密码、企业/客服/旅游独立页导航、统一头像侧栏、历史会话分页加载，以及桌面智能客服资料/订单面板。新增只读 `/api/cs/customer-context`：缺少业务档案或订单返回当前身份和空订单；真实数据库错误返回 503。账号是否有订单仍取决于实际业务库，本次没有验证当前数据库数据。
- 新增客服上下文路由测试，覆盖未认证、空订单、数据库错误。定向后端命令 `python -m pytest backend/tests/api/test_cs_customer_context.py backend/tests/api/test_memory_routes.py backend/tests/memory/test_memory_delete_barrier.py backend/tests/memory/test_memory_extraction_outbox.py backend/tests/memory/test_memory_manager_timeout.py backend/tests/memory/test_profile_projection.py backend/tests/memory/test_memory_conflict_resolution.py backend/tests/memory/test_memory_provenance.py backend/tests/memory/test_profile_endpoint.py backend/tests/memory/test_stop_e_summary_contract.py backend/tests/memory/test_golden_dataset_contract.py backend/tests/tools/test_memory_forget_tool.py -q --no-cov`：29 passed、53 skipped，16.02 秒。
- 前端类型检查 `node frontend/node_modules/typescript/bin/tsc --noEmit --incremental false -p frontend/tsconfig.json`：通过；用临时 junction 复用主工作区依赖，检查后已移除。定向 Vitest 覆盖 CS API/抽屉、旅游页和导航共 6 个文件：18 passed，6.48 秒。`git diff --check` 通过。
- 尚未验证：共享 PostgreSQL 上的真实迁移与账号订单数据、真实 Agent/LLM/队列链路、六个月画像演化、真实跨会话与跨租户 E2E。当前检查点不代表完整记忆方案已验收，也不代表已并入 main。
- 下一步：生成记忆实现与前端实现的候选提交，记录提交 SHA 后停止，等待用户统一整合；不得继续修改共享主工作区。
