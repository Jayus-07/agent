# Travel Runtime — STOP H 最终验收报告

> 日期:2026-09-24 ｜ HEAD:370402c → 收口:d575c9e ｜ 审计:docs/2026-09-24-TravelRuntime-STOPH0-运行时审计.md

# 1. Verdict

```text
STOP_H_PASS=true
TRAVEL_RUNTIME_E2E_PASS=true
TRAVEL_INFRASTRUCTURE_FROZEN=true
```

# 2. Runtime Before

| 项 | 审计时实际状态 |
|---|---|
| backend 镜像 | 审计前数分钟刚被并行会话 rebuild,实测已含 STOP G 代码(context_repository / is_cancel_run_query / CONVERSATION_CONTEXT_BACKEND=redis 全 FOUND)→ **RUNTIME_IMAGE_STALE=false** |
| ConversationContext backend | redis / healthy(/health 实证) |
| checkpointer runtime status | TRAVEL_CHECKPOINTER_ENABLED=true(env 实测)+ **PostgresSaver** 实例化成功 + checkpoints 三表在库 |
| APISIX | :9080 Up 18h,/health 200 |

# 3. Deployment

- 判定:首轮 rebuild 已由并行会话完成,以**字节级比对**替代重复部署(context_repository.py=6c3493cc、slot_filler.py=543e0fa1,容器≡HEAD;654f168..370402c 四个并行 commit 未动任何 STOP G 文件)。
- 后续两次受控重建(devctl.bat restart backend /y,官方唯一入口):`5c217199`(载入 H-D1)→ `7e414351`(载入 H-D2/D3,当前运行,字节级复核 cd4908f4/6ff4eace ≡ HEAD)。
- 触碰服务:仅 `app`(及一次性依赖 db-migrate);redis/postgres/apisix/workers/frontend **未动**;无 down -v / volume rm;volumes 全程保留(H-T1 会话跨重启继续为证)。

# 4. Runtime Architecture

```
Client(e2e_travel_runtime.py)
 ↓ APISIX :9080(JWT gateway-auth → X-Tenant-Id/X-User-Id 注入)
 ↓ FastAPI app 容器(IDENTITY_SOURCE=header)
 ↓ router_node → assemble_routing_context(peek Redis)
 ↓ ConversationContextRepository(Redis db0,WATCH CAS)
 ↓ travel_prefilter / TravelPendingResolver → Travel Graph
 ↓ PostgresSaver checkpoints(travel:{conversation_id})
 ↓ Reporter → SSE(meta/status/delta/done)
```

# 5. Runtime Configuration(脱敏)

| 配置 | 实际生效值 |
|---|---|
| CONVERSATION_CONTEXT_BACKEND | redis(代码默认) |
| CONVERSATION_CONTEXT_REQUIRE_SHARED | false(代码默认;生产收紧可设 true,H-R1 已验证其 fail-closed 路径) |
| CONVERSATION_CONTEXT_TTL_SECONDS | 1800 |
| REDIS_ENABLED / REDIS_URL | true / redis:6379/0(host=容器内网,密码已配置) |
| TRAVEL_CHECKPOINTER_ENABLED / 后端 | true / **PostgresSaver** |
| TRAVEL_PENDING_RESUME_ENABLED | true |
| TRAVEL_REQUIRE_PERSISTENCE / TRAVEL_USER_DECISION_INTERRUPT | false / false |
| backend 进程拓扑 | 单 uvicorn 进程(H-M 系列以独立容器实测多进程,见 §10) |

# 6. E2E Conversation Evidence(全部经 APISIX :9080 + 真实 JWT,专用会话 stopH-*)

| 轮 | 输入 | 路由/resume | run_id | stage/pending | Redis 实测(version 单调) |
|---|---|---|---|---|---|
| H-T1 | 帮我规划厦门的行程 | prefilter→travel | trv_e75cd822_**001** | **slot / pending days** | v6,seq=1,pending=tq_*:['days'],dest=厦门;追问「打算玩几天?」✓ |
| H-T2 | 三天(纯短答案) | **resolver continue** | 同 run_001 | planned→completed,清 pending | v10,days=3,真实出单鼓浪屿行程 |
| H-T3 | 预算改成6万 | resolver continue | 同 run_001 | 重规划出单 | v14,**budget=60000.0 唯一值**(无双值) |
| H-T4 | 不去鼓浪屿了 | 非 cancel(词表排除) | run 不变 | **未触发 travel.run.cancelled** ✓ | v14 不变(run 未被取消;avoid PATCH 通道在 completed 态不存在=既有路由契约,登记 §22) |
| H-T5 | 这次旅行不规划了 | **resolver cancel** | run 身份清 | **cancelled**/pending=null/seq 保留/摘要保留 | v20,`travel.run.cancelled` 日志 ✓;二次同话幂等放行 |
| H-T6 | 重新规划杭州两天的行程 | prefilter→NEW_RUN | trv_e75cd822_**002** | completed | v18,seq=2 单调,dest=杭州 days=2,旧 itinerary 不污染 |
| (补) | 重新规划福州一天的行程 | NEW_RUN | trv_e75cd822_**003** | completed | v24,取消后再规划 seq 递增不撞号 |
| H-T7 | 厦门→预算5000元→住鼓浪屿附近→两天 | resolver continue ×3 | trv_881d811a_001 | completed | v6→10→15→19;**budget=5000.0 与 lodging='鼓浪屿厦门' 跨轮共存** ✓ |

# 7. Redis Evidence

- key:`agent:conversation_context:v1:default:49:stopH-…`(quote 三元隔离,userId=JWT `userId` claim 口径)
- version 单调:每 applied mutation +1(H-T1 六步=6;全程无跳变/回退)
- run_seq:1→2→3(cancel/NEW_RUN 语义);run_id:trv_*_001→002→003
- pending 生命周期:created(days)→ same-question_id 保留(H-T7 同轮重发 tq_9fd9a28d 不变)→ resolved
- stage 生命周期:slot→completed→cancelled→(新 run)slot→completed
- 全程只读观察 + H-C3 定向删除(见 §9);无 FLUSHALL/FLUSHDB

# 8. Cancel Evidence(PATCH ≠ CANCEL)

- 「不去鼓浪屿了」:is_cancel_run_query=False → 无 cancel 事件、run 保持(RAG 拒答,未进 PATCH——登记见 §22)
- 「这次旅行不规划了」:resume_mode=cancel → 短路(不跑 POI/交通/预算/风险专家,973ms vs 完整规划 2~9s)→ stage=cancelled、pending=null、run 清、摘要保留
- `travel.run.cancelled` 真实日志存在(docker logs 实证 ×1)

# 9. Checkpoint Evidence

| 项 | 实测 |
|---|---|
| H-C1 checkpoint 存在 | `SELECT … FROM checkpoints`:travel:stopH-* 各 thread 5~73 行 |
| H-C2 backend restart | devctl 两次真实重启;重启后同会话继续(H-T5/T6/run_003 全部成功,不 500);checkpoint 静默快路径(resume_mode=checkpoint 不打日志,行为证据=跨重启续跑成功) |
| H-C3 context 丢 + checkpoint hit | 定向删除测试会话 context key(先验归属 tenant/conversation,禁 flush)→「厦门行程改成3天」→ 200 出 3 天单;职责分离实证:routing 靠 prefilter、graph 靠 checkpoint;context 事后自动重建(run 编号重置=shared≠durable 冻结语义) |
| H-C4 checkpoint 丢 + context hit | DELETE 仅测试 thread 的 5 行 checkpoint →「厦门行程改成3天」→ 200 出单 + 日志 **`resume_mode=reconstruct`** ✓(摘要回灌) |
| H-C5 both missing | 各新会话首轮 = fresh(日志 resume_mode=fresh ×4),不 500 |

# 10. Multi-worker Evidence(方案 B:独立容器)

- 拓扑:正式 app-A(:9080 经 APISIX)+ 临时 `stoph-backend-b`(:18001 直连,同镜像、同 Redis/PG,**独立 Docker 容器/进程**)
- **H-M1**:U1→A 建缺槽 pending(run=trv_7402e9e8_001)→ U2「两天」→**B** resolver 命中 1.4s → **同 run** 出单 stage=completed
- **H-M2**:四轮 A→B→A→B 交替(缺槽/预算5000元/住白城沙滩附近/三天)→ shared context **budget=5000.0 + lodging='白城沙滩厦门' + days=3 全共存**
- 身份口径:直连 B 时注入网关等价头(X-Tenant-Id/X-User-Id)仅用于实例级验证,主验收链路全部经 APISIX 真实注入,未伪造
- 结论:**runtime multi-process E2E = EXECUTED AND PASSED**(高于「可 Deferred」基线)

# 11. Redis Failure Policy(隔离实例实测,未触碰共享 Redis)

| 场景 | 实测 |
|---|---|
| H-R1 REQUIRE_SHARED=true + Redis 不可达 | mutate raise `ContextBackendUnavailable`(fail-closed);**fallback memory 未被偷写**(readback None);日志 `backend_error … fail-closed`;health=**degraded**(H-D3 修复后) |
| H-R2 REQUIRE_SHARED=false + Redis 不可达 | mutate applied(memory 兜底)+ readback 正常;WARNING `backend_degraded → fallback memory`;`conversation_context_fallback_total{reason="redis_unavailable"} 1.0`、`backend_error_total 1.0`;health=degraded(不冒充 healthy) |

# 12. SSE Evidence

- Content-Type `text/event-stream`;帧序 `meta → status×2 → delta → done`;done 恰 1 次(无重复/无挂死)
- 缺槽轮正确结束(1.1s,连接不保持等待用户);规划轮(出单)正确结束(2~9s)
- Cancel 轮正确结束(973ms,未执行专家链)
- 断连 smoke:进程内 Abort/断开由既有 SSE 适配处理,本轮未见异常堆栈与 context 污染(下一轮继续成功)

# 13. Observability

- **/metrics**(真实请求后):conversation_context_* 44 行;read{redis,miss=2/hit=3}、write=5、mutation{mark_turn/merge/start applied}与单轮 mutation 序列精确吻合;零 duplicate registration
- **label 巡检**:0 个 tenant_id=/user_id=/conversation_id=/travel_run_id=/question_id= label
- **logs**:travel.run.created/completed/cancelled/turned、travel.pending.created/resolved、conversation_context.backend_degraded/backend_error 全部真实出现;无用户原始全文/JWT/secret/Redis 密码
- **trace**:trace tags 打点位在运行链(travel_graph_node `_stamp_context_tags`);外部 trace exporter 本轮未启用,如实登记 instrumented + exporter unavailable

# 14. Isolation

| 维度 | 结论 |
|---|---|
| conversation | **runtime verified**:conv-A 建 pending 后,conv-B 同短答案未被拦截(RAG 拒答) |
| user | **automated verified**(STOP G 真 Redis T5)+ H-M 系列隐式(所有请求 user=49 单一) |
| tenant | **automated verified**(STOP G 真 Redis T4 双 tenant);runtime 单租户(default)部署,不虚构证据 |

# 15. Performance Snapshot(仅记录,非优化目标)

| 轮型 | e2e latency |
|---|---|
| 缺槽轮 | 0.9~1.1s |
| resolver 续槽/补槽 | 0.27~0.44s |
| PATCH/REPLAN 出单 | 2.1~7.3s |
| cancel(短路) | ~1.0s(≪ 完整规划,未跑专家链 ✓) |
| NEW_RUN 出单 | 2.7s |
| login(APISIX) | 0.21~0.62s |

# 16. Defects Found

Runtime defects attributable to STOP G:**3 项,全部已修复并回归**:

| ID | 症状 | 根因 | 修复 | 回归/复测 | commit |
|---|---|---|---|---|---|
| H-D1 | 出单(completed)后「这次旅行不规划了」不触发 cancel,落 RAG | G3 保守条件把 completed 排除在可取消态外,与 H-T5 场景冲突 | 活跃判定放宽为「非 cancelled 即可取消」;seq 保留语义不变 | travel cancel 专项 27 绿 + 实机 H-T5 重测(staged=cancelled ✓) | 5f5c1c4 |
| H-D2 | devctl rebuild 时 db-migrate fail-fast → app DOWN(共享栈半死) | 他会话 untracked 047_memory_provenance.sql 进 build 上下文,但 MIGRATION_TARGETS 登记未提交 | 仅补登记行(047→memory);与 047_side_effect 同号不同名可共存;不收编迁移本体 | devctl 重启成功 app healthy | 83cf890 |
| H-D3 | Redis 不可达实例的 /health 误报 healthy | status 的 _degraded 仅在操作失败后置位,未主动探测 | healthy 以「当前可达」为准;client 不可得即 degraded | H-R1 实例复测 degraded ✓ + context 专项 135 绿 | 080d1c0 |

另登记(非缺陷,既有冻结口径):「预算改成5000」裸数字抽取为 None(extract_budget 冻结契约:需「万/元」单位,防裸数字误抽;带单位即正常);lodging 抽取带城市尾缀(「鼓浪屿厦门」,STOP F 既有 _clean_lodging 边界)。

# 17. Files Changed

| 文件 | 变更 |
|---|---|
| backend/scripts/e2e_travel_runtime.py **新** | STOP H E2E 驱动(login/SSE/Redis 只读观测/场景矩阵) |
| backend/orchestration/context/travel_pending_resolver.py | H-D1 cancel 判定放宽 |
| backend/orchestration/graph/travel_graph_node.py | H-D1 短路判定放宽 |
| backend/tests/travel/test_cancel_lifecycle.py | H-D1 语义反转测试 |
| scripts/init_db.py | H-D2 登记行 |
| backend/orchestration/context/context_repository.py | H-D3 status 探测修复 |
| docs/2026-09-24-TravelRuntime-STOPH0-运行时审计.md **新** | H0 审计 |
| docs/2026-09-24-TravelRuntime-STOPH-最终验收报告.md **新** | 本报告 |

# 18. Tests

| command | passed | failed | exit |
|---|---|---|---|
| pytest tests/travel/ tests/orchestration/ tests/test_stop_g4_matrix.py -q --no-cov | **1032** | **0** | 0 |
| pytest tests/orchestration/context/ tests/test_stop_g4_matrix.py tests/travel/test_cancel_lifecycle.py -q --no-cov(H-D3 后) | 135 | 0 | 0 |
| pytest tests/travel/test_cancel_lifecycle.py -q --no-cov(H-D1 后) | 27 | 0 | 0 |
| 实机 E2E:H-T1~T7、H-C1~C5、H-M1/M2、H-R1/R2、H8(§6~§11、§14) | 全通过 | 0 | — |

# 19. Commits

| hash | subject | scope |
|---|---|---|
| f12f11e | docs(travel): STOP H0 runtime audit | docs |
| 5f5c1c4 | fix(travel): STOP H-D1——completed 后显式取消不生效 | resolver/adapter/tests |
| 83cf890 | fix(deploy): STOP H-D2——登记 047_memory_provenance 解锁部署 | scripts/init_db.py |
| 080d1c0 | fix(observability): STOP H-D3——Redis 不可达时 health 误报 healthy | context_repository |
| d575c9e | test(travel): STOP H2 E2E 驱动 | backend/scripts |
| (本提交) | docs(travel): STOP H 最终验收报告 + 冻结声明 | docs |

全部双重路径限定;无 git add . / -A(裸)。

# 20. Parallel Workspace Protection

- Detected WIP(全程不碰):staged 3、modified 19(config/memory/context_budget/customer_service/metrics.py/rag/tools 等)、untracked 5——H-D2 仅向 init_db.py 追加登记行(其自有改动未被收编,该文件当时无他人未提交 hunk)。
- 无 reset --hard/checkout ./clean/stash;无 accidental staging(逐提交 --stat 复核)。
- 共享栈操作:app 容器 3 次受控重建均走 devctl 官方入口;redis/postgres/apisix/workers 未动;临时验收容器(--rm:backend-b/c/d)用后即删。

# 21. Freeze Contract

**Travel Runtime Infrastructure frozen.** 冻结项:TravelGraphState、图拓扑、TravelRun/travel_run_id、TravelPendingQuestion、TravelPendingResolver、ConversationContextRepository 接口、Redis context key/schema v1、mutation 契约(16 项)、version 语义、pending question_id CAS、run_id CAS、TTL 语义、shared/fail policy(REQUIRE_SHARED 两态实测)、thread_id 命名空间 travel:{conversation_id}、checkpoint/context 职责分离、CONTINUE/PATCH/REPLAN/NEW_RUN/CANCEL、resume_mode(continue/cancel/checkpoint/reconstruct/fresh)。

冻结内允许:bugfix、schema_version 迁移、backend 内部 WATCH→Lua、性能与安全修复。禁止:改 context key、绕过 Repository 直写 Redis JSON、绕过 mutation、裸 conversation_id thread、从 checkpoint 恢复身份、Context 与 Checkpoint 混同。

# 22. Deferred(全部非阻塞)

1. 「不去X了」在 completed 态(无 pending)的 avoid PATCH 无路由通道——既有路由契约(补槽仅 pending 期),建议 STOP I 连同 itinerary 质量一起演进。
2. extract_budget/lodging 抽取边界(裸数字、城市尾缀)——STOP I 数据质量域。
3. 第二 tenant 实机 E2E(当前单租户部署;tenant 隔离已由 STOP G 真 Redis T4 自动化覆盖)。
4. APISIX 多 backend LB 压测 / 高并发 benchmark / 长 soak / chaos——容量与韧性专项。
5. WATCH→Lua(当前 T7/M2 强竞争实测零 conflict 耗尽)。
