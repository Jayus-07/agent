# Travel Runtime — STOP H0 运行时审计

> 日期:2026-09-24 ｜ HEAD:370402c(含全部 STOP G commits)｜ **STOP_H0_PASS=true**

## Git 基线(H0-1)

- HEAD = `370402c`,branch = main;STOP G 六 commits(8a19098/1eb18d3/865c579/b35fef6/654f168/370402c)全部在 ancestry(另穿插其他会话的 tasks step6/cs 幂等/llm governance 提交,属正常并行)。
- 并行 WIP 清单(不碰、不收编):staged 3(tests/context_budget)、modified 19(config/memory/context_budget/customer_service/observability/metrics.py/rag/tasks/tools/docs)、untracked 5(e2e_async_runtime.py、migration 047、memory 测试、worker_metrics 测试、审查报告)。
- `docker-compose.yml` 与 `.env` 当前**无未提交改动**(利于镜像可复现)。
- 禁令遵守:全程无 reset --hard / checkout . / clean / stash。

## 容器与镜像版本(H0-2)

| 项 | 实测 |
|---|---|
| app 容器 | `agent-app-1`,created 2026-09-23T18:46:50Z(审计前数分钟刚被并行会话 rebuild),healthy,:8000 |
| 镜像内 STOP G 代码 | `context_repository.py` FOUND / `context_metrics.py` FOUND / `is_cancel_run_query` FOUND / `CONVERSATION_CONTEXT_BACKEND=redis` 生效 |
| 字节级比对 | `context_repository.py`=6c3493cc48e3、`slot_filler.py`=543e0fa15642 —— **容器 ≡ 工作区 ≡ HEAD**(654f168..370402c 的 4 个并行 commit 未动任何 STOP G 文件) |
| 结论 | **RUNTIME_IMAGE_STALE=false** —— app 容器已运行含 STOP G 的 HEAD 代码,无需再 rebuild |

其余栈:apisix(:9080, apache/apisix:3.13.0, Up 18h)、redis 7.4.9、postgres(pgvector:pg16)、4 个 celery worker + beat + cs-dispatcher + 监控套件全部 healthy。

## Runtime 配置(H0-3,脱敏)

| 配置 | 实际生效值 | 来源 |
|---|---|---|
| REDIS_ENABLED | true | env |
| CONVERSATION_CONTEXT_BACKEND | **redis** | 代码默认(env unset) |
| CONVERSATION_CONTEXT_REQUIRE_SHARED | false | 代码默认(开发兜底策略;如需生产收紧设 true) |
| CONVERSATION_CONTEXT_TTL_SECONDS | 1800 | 代码默认 |
| TRAVEL_CHECKPOINTER_ENABLED | **true** | env |
| checkpointer 后端 | **PostgresSaver**(实例化成功) | runtime 探测 |
| TRAVEL_REQUIRE_PERSISTENCE | false | 代码默认 |
| TRAVEL_PENDING_RESUME_ENABLED | **true** | 代码默认 |
| TRAVEL_USER_DECISION_INTERRUPT | false | 代码默认 |
| ENV_MODE | cloud | env |
| REDIS_URL | redis:6379/0(host=db0,密码已配置,不展示) | env |
| PG | agent_memory(@postgres:5432 容器内网) | env |
| backend 进程拓扑 | **单 uvicorn 进程**(无 --workers) | docker top |

## Runtime dependency health(H0-4)

- Redis:PING ✓(v7.4.9)
- PostgreSQL:`SELECT 1` ✓;checkpoints / checkpoint_blobs / checkpoint_writes 表存在(PostgresSaver 已迁移)
- Backend:`/health` 200,且已含 **`"conversation_context":{"backend":"redis","status":"healthy"}`** ✓
- APISIX:`:9080/health` → 200 ✓

## Checkpointer 实际状态(H0-5)

`TRAVEL_CHECKPOINTER_ENABLED=true`(env 实际值,非源码默认推测)+ PostgresSaver 实例化成功 + checkpoint 三表在库 → **travel durable resume 运行时在位**,H-C 系列将实测读写。

## STOP H 计划影响

- H1(受控重建):部署事实已由并行会话的 rebuild 完成,本轮以字节级比对 + 行为验证替代重复 rebuild(避免与并行会话栈操作撞车)。
- H2 起执行真实 Gateway E2E(APISIX :9080 + 真实 JWT)。
- H3:生产拓扑=单 uvicorn 进程 → 按任务书 §26 方案 B(临时第二实例,同 Redis/PG)实测,或如实登记 `MULTI_PROCESS_RUNTIME_E2E_NOT_EXECUTED`(STOP G 已有真 Redis 双 repository matrix)。

**STOP_H0_PASS=true**
