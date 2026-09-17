# Errors

Command failures and integration errors.

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
