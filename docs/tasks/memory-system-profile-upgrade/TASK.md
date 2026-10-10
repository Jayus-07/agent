# 企业级三层记忆与动态用户画像改造计划

日期：2026-10-10。状态：实施中（分支 `codex/memory-system-profile-upgrade`）。

## 目标与任务来源

合并用户提供的《Agent Platform 企业级三层记忆系统改造任务》和《Agent Platform 企业级长期记忆与动态用户画像改造》，按照仓库 AGENTS.md 的长任务约定管理。前者作为整体主线，后者作为 Phase 3 的 L3 深化要求，共用审计、契约、测试与交付，避免重复施工。

用户已授权按本计划实施并要求新建分支。实施遵循 Phase 0–7，每阶段以实际证据收口；数据库迁移与运行切换仅在兼容性及阶段门禁核验后执行。

最终目标：同一真实用户能够跨会话记忆、纠错、召回和删除；长期画像随有效记忆演化，临时条件不污染 L3；租户、用户、会话和业务域权限正确；取消、失败、重试及旧异步事件不制造错误记忆；真实 API、Agent、数据库、向量检索与画像行为一致。

## 范围与约束

- L1 最近对话、L2 摘要及恢复、L3 生命周期、统一 Context Builder、多域权限、异步可靠性、观测和评测。
- 复用 MemoryService、现有 Context Budget/L1–L5、LangGraph Checkpointer、PostgreSQL/pgvector、Redis、Celery、统一 LLM/Prompt/计费和 Trace 链路。
- 不新建 Memory Agent、独立数据库、重复预算系统或不必要的 Graph 节点；不建设复杂画像前端，不实现模型自主改写系统规则的程序性记忆。
- 长期画像不是订单、退款、财务、客服接管、旅游 Active/Draft 等业务状态的权威来源；恢复不得绕过审批或改写正式业务状态。
- 身份取服务端验证后的上下文；资源授权落在服务和数据访问层；tenant_shared 必须独立授权，私人信息不得自动提升为共享信息。
- 保持 API/SSE 兼容；必要的新契约提供适配。每次修改前检查定向 git diff，保留其他会话的修改。
- 不灰度、不影子双跑；本机全部门禁通过后切换，保留配置和代码回滚路径。破坏性数据库操作必须具有明确迁移、备份与授权依据，否则阻断该操作并继续其他可执行事项。

## 当前源码定位（不是运行验收结论）

| 组件 | 已定位的入口 | 实施前核验重点 |
| --- | --- | --- |
| 统一记忆服务 | backend/memory/service.py、manager.py | 调用方、身份传递、终态、错误与后台任务生命周期 |
| L1/L2 | backend/memory/short_term.py、session.py、repository/session_repo.py | 消息去重、持久化、摘要 frontier、任务状态和恢复 |
| L3 | backend/memory/long_term.py、retriever.py、repository/memory_repo.py | 提取来源、召回过滤、写入裁决与向量一致性 |
| 偏好与冲突 | backend/memory/keying.py、conflict.py、models/memory.py | 已有 memory_key/structured_value、origin、is_active、superseded_by；与 scope/domain、递增版本和事件顺序目标的差距 |
| 数据迁移 | backend/sql/migrations/032_memory_embedding_vector.sql、047_memory_provenance.sql、048_memory_scope_and_versioning.sql | 实库应用情况、旧记录归属、唯一约束及兼容回滚；不直接重写历史迁移 |
| 画像/API/Tool | backend/app/api/routes/memory.py、backend/tools/memory.py、MemoryService.get_profile | 现有画像返回 records，核验结构化投影、分页、授权、删除与操作状态 |
| Context Budget | backend/context_budget/、backend/memory/token_budget.py | 统一组装、重复注入、L5 同步/异步一致性、预算硬门禁 |
| Checkpointer/Runtime | backend/orchestration/graph/checkpointer.py、runner.py、backend/core/node_runtime/ | 实际启用、namespace、恢复及 SSE 终态 |
| Celery | backend/tasks/celery_app.py、memory_maintenance_tasks.py | 已定位衰减任务，不据此断言提取已接入可靠队列 |
| 验证资产 | backend/tests/memory/、context_budget/、api/test_memory_routes.py、backend/scripts/e2e_memory_stopg.py、eval_memory_golden.py | 保留有效覆盖；核验黄金集、脚本是否覆盖真实现行链路 |

工作区存在大量未提交修改，其中包含 Memory、Context Budget、API 和迁移；Phase 0 必须记录定向差异与归属边界，不将现有修改冒充本任务产出。

## 执行顺序与阶段门禁

顺序：Phase 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7。身份、隔离、事件顺序和可观测性在前期契约及实现中就必须约束，后续阶段负责跨域接线与完整验证，不能留到最后才补安全。

### Phase 0：真实现状审计与基线

- [x] 读取 AGENTS.md、docs/README.md 导航、testing-guide.md、ai-runtime.md、domain-service-map.md 及实际相关源码；核对工作区和相关差异。
- [x] 静态定位主图 Runner 与 RAG chain 的 MemoryService 入口、MemoryService 管线、Context Budget、Checkpoint、SSE 路由、Celery task 注册及 DB repository，覆盖六个业务域的后续核验入口。
- [x] 静态审计 L1 窗口/重复裁剪、L2 增量摘要与 CAS frontier、L3 来源证据/Key 冲突/检索过滤、可信身份解析和异步 end_turn 写入。
- [x] 通过只读迁移 preflight 核验当前目录 91 个迁移均已登记，权威库均已应用；未执行任何迁移或索引重建。
- [ ] 建立隔离测试身份并实测真实 Memory API/Agent；测量准确率、误写率、延迟、Token 和额外模型调用成本，基于基线制定阈值。

产出：审计矩阵（功能/入口/源码/真实运行状态/问题/P0–P2 风险/复用方案）、基线证据、阶段最小文件范围与迁移差距。门禁：未运行能力明确标记未验证；完成审计后只修补缺口，不重复建设。

### Phase 1：L1 多轮连续性与去重

- [ ] 定义统一 Human/AI/Tool 消息及稳定消息标识，窗口可配置，区分消息数与对话轮数。
- [ ] 收口 Memory 和 Graph State 注入，防止同一消息重复加载或保存；取消、失败和重试遵循明确终态规则。
- [ ] 校验用户/租户/会话范围；核验 Redis 故障时的持久化恢复和服务重启行为，超窗历史交由 L2。

门禁：杭州三日游 → “第二天不要太累”正确指代；同一消息只注入一次；跨会话和用户不串话。覆盖 M01、M02、M04，关联 M17。

### Phase 2：L2、Checkpoint 与摘要

- [ ] 分离原始历史、会话摘要和结构化 Graph/Domain 任务状态，保存业务 ID/版本及未完成事项。
- [x] 按 Token 预算触发增量摘要，复用已有 frontier/版本机制；保留确认条件、未解决约束及关键引用。（L2 Token 触发已接入；关键条件保留与真实长会话门禁仍待验收。）
- [ ] 核验同 thread_id 持久恢复和 namespace 隔离；不得以摘要覆盖旅游 Active/Draft、客服确认或人工接管状态。
- [ ] 修补 L5 同步/异步差异、摘要失败和并发覆盖；失败保留安全上下文并记录降级。

门禁：超长会话关键条件不丢，取消/中断/重启后恢复正确，临时需求不会提升为 L3。覆盖 M05、M06，关联 MP-05、M17。

### Phase 3：L3 与动态画像（重点）

实施记录：删除 API/服务、版本链物理清理、删除屏障、完整结构化画像投影及 Agent 遗忘 Tool 已有代码和定向测试；统一 schema、显式版本/verification、域授权与持久提取队列仍未完成，不得据此勾选 Phase 3 门禁。

按以下子步骤推进；异步与缓存的一致性约束在此设计并实现，Phase 6 再验证故障边界。

1. **统一契约与迁移**：映射现有字段，按实际差距补 scope、domain、verification_status、版本、事件顺序等必要能力。规范 preference/fact/episodic 和稳定 key。唯一有效记录身份为 tenant/user/scope/domain/key。复用 structured_value 等既有字段，避免无意义改名；迁移编号从现行注册体系确定。
2. **Extractor/Policy/Matcher**：LLM 仅生成候选；确定性 Schema、来源、权限、敏感信息、长期/临时规则决定是否落库。推断只能作为待确认候选，不得当作明确事实；拒绝秘密、无依据事实、失败 Tool 结果和一次性预算。受限语义匹配不确定时保留待确认状态。
3. **ADD/UPDATE/NOOP/DELETE**：复用现有裁决和版本链，提供四种明确操作结果及适配；最新用户更正优先，临时例外不覆盖长期偏好。CAS/唯一约束保护并发，内容更新同步维护 Embedding，处理首次并发插入冲突。
4. **删除与防复活**：明确删除走确定性授权路径，不依赖 LLM 提取成功；清理正文、Embedding、缓存和画像，保留最小必要审计/删除屏障元数据，旧任务不能重新创建。备份和日志按实际保留策略说明，不承诺未经验证的完全物理抹除。
5. **Profile Projection**：从当前有效、已授权记忆按 domain 投影结构化画像；不追加 summary。防止列表 limit/分页造成画像漏字段，版本更新、过期、删除触发一致失效。
6. **Retriever**：数据层先过滤 tenant/user/scope/domain/type、有效性和过期，再召回、排序、Top-K、预算注入；新会话实际使用最新版本。无结果正常执行，检索故障显式降级，身份验证失败必须拒绝。
7. **异步提交契约**：复用 Celery，将普通提取与响应解耦；事件幂等、有界重试、CAS 与可信事件序列共同阻止旧任务覆盖新记忆。明确“记住/更正/删除”返回可查询的实际操作状态，入队不能报告保存成功；DB 与审计一致性采用现有可靠机制，审计后确定最小补齐方式。

门禁：ADD/UPDATE/NOOP/DELETE、临时隔离、跨会话召回、画像一致、并发、乱序和删除后重放有真实证据。覆盖 M07–M10、M12、M14、M18，MP-01–MP-11、MP-14–MP-16、MP-18；其余安全项由 Phase 5/6 完整验收。

### Phase 4：Context Builder 统一治理

- [ ] 在既有预算链路中统一 System、Developer/Agent 指令、当前请求、L1、L2、L3、RAG、Tool 输出的组装边界与优先级。
- [ ] 去重并记录来源 Token；预留模型输出及安全空间，适配实际模型窗口，保留当前请求、必要业务状态和关键约束。
- [ ] 当前明确要求优先于旧偏好；检索记忆按不可信数据处理，不执行其中指令。复用现有安全边界，不单靠提示词防注入。

门禁：模型输入不超预算，关键约束保留，无重复注入，恶意记忆不能改变授权或执行。覆盖 M04、M15、M16，MP-19。

### Phase 5：多 Agent 与权限隔离

- [ ] 验证 user_global、user_domain、session、tenant_shared 的访问矩阵；业务域私有信息不能自动跨域共享。
- [ ] 检查主 Agent/子 Agent/Tool 的可信身份传递以及 API、服务、仓储授权；伪造客户端 ID 不影响真实归属。
- [ ] 核验跨租户、同租户不同用户、同用户不同会话、普通用户/管理员边界和用户自身更正/删除权限。
- [ ] 按 Phase 0 的实际入口验证所有业务域，保护 SQL 安全、旅游状态机及客服接管/审批。

门禁：授权访问矩阵全部通过；未授权读取/写入/删除拒绝，租户 namespace 不共享。覆盖 M02、M03、M13、M27，MP-12、MP-13。

### Phase 6：可靠性、生命周期与观测

- [ ] 验证响应成功/失败/取消/超时/SSE 断开/重试对应的 L1、L2、L3 写入语义，失败业务不能变为成功事实。
- [ ] 验证 worker 中断、重复投递、乱序、队列故障、DB/Redis 故障、删除后重放；恢复保留中断状态，不伪造成功终态。
- [ ] 接入现有过期、衰减和清理能力，确认不能覆盖最新用户更正；补必要管理治理接口，不扩大前端范围。
- [ ] 复用 Trace/Prometheus，覆盖 memory.read、l1.load、l2.restore、l3.search、context.build、extract、validate、deduplicate、write、update、delete、error。
- [ ] 记录 trace/thread、scope/type、memory IDs、检索/过滤/注入数量、Token、耗时、状态与错误码；更新记录 operation/key、old/new version、reason、extractor model 和用量。身份脱敏，不记录敏感正文或密钥。
- [ ] 指标覆盖读写成功率、召回/提取延迟、重复/冲突、注入 Token、画像更新成功率、队列积压与失败，避免高基数标签。

门禁：降级可追溯且不绕过权限，重试不重复、旧事件不覆盖、删除不复活、Trace 不泄密。覆盖 M11、M12、M17–M19、M28–M30，MP-09–MP-11、MP-17、MP-18。

### Phase 7：真实验收与最终收口

- [ ] 建立至少 30 条正负例黄金集，覆盖指代、超长会话、写入/召回/纠错/冲突、临时过滤、无结果、Tool 失败、越权、并发、SSE 中断、重启和注入。
- [ ] 建立 memory_profile_evolution 独立回归，以固定时间轴/可注入时钟模拟六个月，增加第七个月全新会话验证；不等待真实时间，不污染真实用户。
- [ ] 跑通真实认证 API → Agent → Memory → DB/pgvector → 画像 → 新会话回答；记录请求、操作结果、数据库断言和 Trace ID，禁止手工构造最终 JSON 冒充验收。
- [ ] 完成 M01–M20 和 MP-01–MP-20 双矩阵，可复用证据但保留原编号；执行 M21–M30 质量/治理评测并记录待办。
- [ ] 各阶段按 T0/T1/T2 定向验证，P0 后最小 E2E Smoke；按任务原文要求在收口执行一次 T3 全量回归，不每轮重复。全量的后端/前端范围与准确命令在 Phase 0 确定，环境缺失明确 BLOCKED。
- [ ] 比较基线与改造后准确率、误写率、摘要质量、Token/上下文、L3 检索/写入、请求延迟、额外模型调用成本及容量增长；不编造提升比例。
- [ ] 同步受影响的现行架构/接口/配置文档，提供代码、配置、数据库兼容回滚步骤；明确回滚不能恢复用户已要求删除的内容。

## 六个月画像演化验收时间轴

| 时间 | 事件 | 预期操作与关键断言 |
| --- | --- | --- |
| 月 1 | 以后旅行轻松，酒店安静 | ADD travel.pace=relaxed、hotel.quiet=true；2 条有效记录，来源可追踪，跨会话可读 |
| 月 2 | 这次杭州，预算 3000，酒店近地铁 | 只进入 L2；L3 无新增，长期偏好不变 |
| 月 3 | 以后酒店近地铁 | ADD hotel.location=near_metro；3 条有效记录 |
| 月 4 | 这次苏州紧凑一点 | L2 pace=packed；L3 travel.pace 仍为 relaxed |
| 月 5 | 以后紧凑一些 | UPDATE travel.pace=moderately_packed；版本 1→2，唯一有效版本，Embedding 与召回更新 |
| 乱序验证 | 月 4 旧任务在月 5 后完成 | 旧事件不能覆盖月 5 更正；重复投递不产生新记录 |
| 月 6 | 遗忘安静酒店，交通方便优先 | DELETE hotel.quiet；NOOP hotel.location；正文/向量/缓存/投影清理 |
| 删除后重放 | 旧安静酒店任务重试 | 不恢复已删除偏好 |
| 月 7 | 全新会话规划南京三日游 | 实际使用 moderately_packed 与 near_metro；不使用安静、预算 3000、苏州临时状态或他人偏好 |
| 补充负例 | “这次想住安静酒店” | 只影响当前任务，不恢复长期 hotel.quiet |
| 补充纠错 | “之前说错了，其实一直喜欢紧凑行程” | 按更正来源更新；旧异步事件不能反向覆盖 |

最终投影应为 travel.pace=moderately_packed、hotel.location=near_metro；补充纠错场景独立隔离，避免改变主时间轴的最终断言。

## 验收编号与证据要求

| 编号 | 验收内容 |
| --- | --- |
| M01–M05 | 多轮连续性、会话隔离、租户隔离、L1 去重、L2 关键条件 |
| M06–M10 | 中断恢复、跨会话、偏好更新、长期去重、临时过滤 |
| M11–M15 | Tool 失败、L3 降级、权限防伪造、删除不可召回、注入防护 |
| M16–M20 | Token 预算、SSE 终态、异步幂等、Trace、真实完整链路 |
| MP-01–MP-05 | ADD、UPDATE、NOOP、DELETE、临时偏好隔离 |
| MP-06–MP-10 | 最新跨会话召回、明确纠正、推断待确认、并发无丢失、异步乱序 |
| MP-11–MP-15 | 删除后重放、跨租户、横向越权、向量一致、画像一致 |
| MP-16–MP-20 | Agent 实际使用、Tool 失败、写入失败与重试、注入防护、六个月演化 |
| M21–M25（P1） | 召回相关性、重要性/时效排序、摘要质量、误写率、Token 对比 |
| M26–M30（P1） | 延迟、多域共享、过期清理、管理治理、数量与容量分析 |

每项记录：编号、PASS/FAIL/BLOCKED、实测方式、命令/请求、通过/失败/跳过数量、耗时、证据路径、Trace ID、未通过原因。计划期间标记未执行；最终若因环境未能实测则 BLOCKED。测试断言检验行为与数据，不能只看 HTTP 200。

证据放在本任务 evidence/ 下，实际生成时再创建；禁止存放密码、Token 和敏感记忆正文。PROGRESS.md 只记录已发生的事实和下一步，不生成竞争性的实施方案。

## 最终交付与放行

交付审计矩阵、实际架构图、复用/新增/删除重复能力、代码文件与迁移/API/配置清单、每月画像与事件操作、有效记录及真实 Agent 回答、两套 P0 与 P1 矩阵、性能成本、各阶段独立 Commit 和回滚步骤。仅提交本任务明确拥有的改动，不包含他人未提交修改，不自动推送或合并。

只有 M01–M20 全部实测通过并具有真实链路证据，才可设置 MEMORY_PRODUCTION_READY=true；只有 MP-01–MP-20 全部实测通过，才可设置 MEMORY_PROFILE_READY=true。任一 P0 未通过则对应 ready=false，明确原因和下一步；P1 未完成项单独列出。

最终报告同时输出：

```text
MEMORY_PRODUCTION_READY=true/false
P0_PASS:
P0_FAIL:
P0_BLOCKED:
P1_PENDING:
L1_STATUS:
L2_STATUS:
L3_STATUS:
CHECKPOINTER_STATUS:
CONTEXT_BUDGET_STATUS:
TENANT_ISOLATION_STATUS:
MEMORY_E2E_STATUS:

MEMORY_PROFILE_READY=true/false
MEMORY_EVOLUTION_6_MONTHS=PASS/FAIL/BLOCKED
MEMORY_CROSS_SESSION=PASS/FAIL/BLOCKED
MEMORY_CONFLICT_UPDATE=PASS/FAIL/BLOCKED
MEMORY_DELETE=PASS/FAIL/BLOCKED
MEMORY_TENANT_ISOLATION=PASS/FAIL/BLOCKED
MEMORY_ASYNC_ORDERING=PASS/FAIL/BLOCKED
MEMORY_REAL_E2E=PASS/FAIL/BLOCKED
```
