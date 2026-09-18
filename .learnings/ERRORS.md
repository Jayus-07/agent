# Errors

Command failures and integration errors.

---

## [ERR-20260918-005] celery_dependency_missing_in_target_python

**Logged**: 2026-09-18T11:08:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
协议旁路测试在收集阶段因目标 Python 环境缺少 Celery 而无法导入既有任务模块。

### Error
```
ModuleNotFoundError: No module named 'celery'
```

### Context
- 命令：`D:/Python/python.exe -m pytest backend/tests/test_error_protocol.py -q --no-cov`
- 直接导入 `backend.app.api.routes.tasks` 时，模块链路加载 `backend.tasks.celery_app`。
- 同样导致 `test_rag_upload_celery_mode.py` 无法收集，未进入断言阶段。

### Suggested Fix
将不依赖 Celery 的错误协议适配器抽到轻量模块，测试直接验证该模块；完整 Celery 行为测试在安装 Worker 依赖的环境中执行。

### Metadata
- Reproducible: yes
- Related Files: backend/app/api/routes/tasks.py, backend/tasks/celery_app.py, backend/tests/test_error_protocol.py

### Resolution
- **Resolved**: 2026-09-18T11:22:00+08:00
- **Notes**: `D:/Python/python.exe` 仅用于不依赖 Celery 的专项协议测试；Celery 任务回归改用项目 `.venv/Scripts/python.exe`，`test_rag_upload_celery_mode.py` 17 项通过。

---

## [ERR-20260918-GIT] git_commit_index_lock_permission

**Logged**: 2026-09-18T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
提交架构设计文档时，Git 无法创建 `.git/index.lock`。

### Error
```
fatal: Unable to create 'D:/Program Files/workplace/agent/.git/index.lock': Permission denied
```

### Context
- 操作：仅暂存并提交 `docs/superpowers/specs/2026-09-18-rag-eval-kb-unification-design.md`
- 工作区存在其他未提交改动，未使用宽范围暂存或清理操作。
- 设计文档已经写入工作区，但提交未成功。

### Suggested Fix
确认 `.git` 目录和索引文件的写权限、是否存在残留 `index.lock`，再使用路径限定的 `git add`/`git commit` 重试；不要删除未知来源的锁文件。

### Metadata
- Reproducible: unknown
- Related Files: .git/index.lock, docs/superpowers/specs/2026-09-18-rag-eval-kb-unification-design.md

---

## [ERR-20260918-006] rg_regex_escape

**Logged**: 2026-09-18T07:46:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
一次 PowerShell 中的 `rg` 检索表达式括号转义不完整，命令解析失败。

### Error
```text
rg: regex parse error: unclosed group
```

### Context
- 失败只发生在排查 JWT 角色字段时，不涉及项目代码。
- 改用不含复杂正则分组的检索后确认 JWT 使用 `roles` 数组。

### Suggested Fix
PowerShell 中优先使用简单单引号模式，复杂正则先单独校验。

### Metadata
- **Reproducible**: yes
- Related Files: backend/security/local_jwt.py

### Resolution
- **Resolved**: 2026-09-18T07:46:00+08:00
- **Notes**: 使用简单检索完成确认。

---

## [ERR-20260918-005] vitest_invalid_jest_flag

**Logged**: 2026-09-18T07:42:43+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
前端 Vitest 不支持 Jest 的 `--runInBand` 参数，导致一次错误的测试命令退出码为 1。

### Error
```text
CACError: Unknown option `--runInBand`
```

### Context
- 项目脚本是 `vitest run`，不是 Jest。
- 改用项目原生 `npm test` 后 15 个测试文件、231 个测试全部通过。

### Suggested Fix
运行该项目的前端测试时使用 `npm test`；如需限制并发，使用 Vitest 自身支持的选项。

### Metadata
- Reproducible: yes
- Related Files: frontend-admin/package.json

### Resolution
- **Resolved**: 2026-09-18T07:43:00+08:00
- **Notes**: 使用原生 Vitest 命令重新验证通过。

---

## [ERR-20260918-001] docker_compose_readiness_check

**Logged**: 2026-09-18T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Docker-based database and service readiness checks cannot run from the current sandbox because access to the Docker named pipe is denied.

### Error
```
permission denied while trying to connect to the docker API at npipe:////./pipe/docker_engine
```

### Context
- Attempted read-only `docker compose ps` and `psql` version checks during release-readiness review.
- No service-health conclusion can be inferred from this permission failure.

### Suggested Fix
Grant Docker named-pipe access for the review session, or run the documented database/version checks from an authorized local terminal.

### Metadata
- Reproducible: unknown
- Related Files: docker-compose.yml, backend/sql/alembic

---

## [ERR-20260918-002] alembic_windows_default_encoding

**Logged**: 2026-09-18T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
On Windows with the default GBK locale, Alembic cannot parse the UTF-8 `alembic.ini`; documented migration commands fail before loading either migration chain.

### Error
```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 in position 25
```

### Context
- `.venv/Scripts/python.exe -m alembic -c alembic.ini -n <business|memory> heads` fails.
- Adding Python UTF-8 mode (`-X utf8`) succeeds and reports business `0001` and memory `0010` heads.

### Suggested Fix
Make the Alembic config locale-safe (for example ASCII-only comments), or make UTF-8 mode an explicit, enforced part of every documented migration command and deployment script.

### Metadata
- Reproducible: yes
- Related Files: alembic.ini, backend/sql/alembic/business/env.py, backend/sql/alembic/memory/env.py

---

## [ERR-20260918-003] customer_service_async_teardown

**Logged**: 2026-09-18T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Customer-service graph tests can return passing assertions while a timed-out knowledge-expert worker continues its RAG initialization and external embedding calls after the test output stream closes.

### Error
```
httpx.ConnectError: [WinError 10013]
ValueError: I/O operation on closed file
RuntimeWarning: coroutine 'StateTransitionService._async_apply' was never awaited
```

### Context
- `run_expert_safely()` calls `ThreadPoolExecutor.shutdown(wait=False)` after a timeout, so a running worker remains alive.
- The worker enters `get_rag_pipeline()` and attempts external embedding calls after the test process has moved on.

### Suggested Fix
Add deterministic test doubles for the knowledge expert and ensure timeout work has a cooperative cancellation/owned lifecycle; investigate the un-awaited state-transition coroutine separately.

### Metadata
- Reproducible: yes
- Related Files: backend/customer_service/experts/base.py, backend/customer_service/experts/knowledge.py, backend/customer_service/state_transition.py, backend/tests/customer_service/test_cs_graph.py

---

## [ERR-20260918-004] release_database_schema_drift

**Logged**: 2026-09-18T00:00:00+08:00
**Priority**: critical
**Status**: pending
**Area**: infra

### Summary
The configured PostgreSQL target is behind the current memory migration chain and lacks core customer-service and RAG tables required by the running code.

### Error
```
agent_memory Alembic current: 0008 (code head: 0010)
customer_service.events: absent
audit_logs_result_check: does not allow pending
doc_registry / chunk_store: absent
rag_vectors: only router_index (99 rows)
```

### Context
- Read-only checks used the same application database configuration as runtime code.
- pgvector extension and `rag_vectors` exist, but no main knowledge collections are present.

### Suggested Fix
First make the Alembic entry point encoding-safe, then upgrade/stamp both database chains under deployment control, verify required tables and constraints, ingest the production knowledge corpus, and run end-to-end RAG/CS smoke tests before enabling the release.

### Metadata
- Reproducible: yes
- Related Files: alembic.ini, backend/sql/alembic/memory/versions/0009_cs_events.py, backend/sql/alembic/memory/versions/0010_audit_result_pending.py, backend/rag/vectorstore/pgvector_store.py

---
