# SDD ledger — plan: D:/Program Files/workplace/agent/docs/superpowers/plans/客服高并发派单、RBAC 与登出实施方案.md

## Plan decomposition

The source plan is phase-based rather than headed `Task N` sections. For execution, P0–P9 are treated as Tasks 0–9 in order; each task keeps its own tests, review package, and completion gate.

| Task | Plan phase | Scope | Dependencies |
| --- | --- | --- | --- |
| 0 | P0 | Baseline, decision freeze, ownership inventory, contracts | Existing repository state |
| 1 | P1 | Data model and migrations | Task 0 decisions; existing CS schema/models |
| 2 | P2 | RBAC and session backend | Task 1 auth-user/agent binding schema |
| 3 | P3 | RBAC UI and logout | Task 2 API/auth contract |
| 4 | P4 | User handoff ingress and idempotency | Tasks 1–2 tenant/user authority |
| 5 | P5 | Presence and multi-instance WebSocket ticketing | Tasks 1–4 event and identity contracts |
| 6 | P6 | Automatic dispatcher | Tasks 1, 4, 5 data/presence contracts |
| 7 | P7 | Accept/decline/reassign/reaper and workbench | Task 6 assignment/offer state machine |
| 8 | P8 | Durable outbox relay and observability | Tasks 1, 5–7 event lifecycle |
| 9 | P9 | Load/fault tests and rollout evidence | Tasks 0–8 |

## Pre-flight conflict scan

| Row | Shared surface / self-consistency check | Finding | Ruling |
| --- | --- | --- | --- |
| 0↔1 | P0 decisions → P1 schema | P1 depends on tenant, state, offer, retry, and priority values frozen in P0. | Use the plan's recommended defaults as the implementation baseline because the user explicitly requested implementation; record any later change as a new ruling before migration changes. |
| 1↔2 | P1 `cs_agents.auth_user_id` → P2 RBAC | P2 requires a persisted user/agent binding and optimistic versioning. | P1 owns the schema; P2 consumes it through repository/service APIs and may not duplicate binding truth. |
| 1↔4 | P1 handoff uniqueness → P4 idempotent ingress | P4 requires one active handoff per tenant/session and a durable tenant key. | Enforce uniqueness in PostgreSQL and return the existing handoff for concurrent retries. |
| 1↔5 | P1 events/tenant fields → P5 ticket/event routing | P5 needs tenant- and agent-scoped durable identifiers. | Event/ticket payloads carry tenant and authenticated agent identity; Redis is not lifecycle authority. |
| 1↔6 | P1 assignment constraints → P6 concurrent dispatch | P6 requires row locking and a partial unique active-assignment invariant. | Correctness lives in PostgreSQL transaction/constraints; Redis only supplies presence candidates. |
| 2↔3 | P2 role/session revocation → P3 cached user/logout state | P3 must reflect backend role changes and clear all local query/socket state on logout. | P2 response/session contracts are authoritative; P3 must not infer permissions solely from cached UI state. |
| 4↔6 | P4 waiting handoff → P6 dispatcher | The dispatcher must only consume durable `waiting_human` work. | P4 commits the handoff; P6 never binds before a committed row is visible. |
| 5↔8 | P5 targeted delivery → P8 outbox relay | Notifications can be lost between transaction commit and publish without an outbox. | P8 extends the same event records and relay semantics; P5 remains transport/presence only. |
| 6↔7 | P6 offered assignment → P7 accept/decline/reaper | State/version/expiry semantics must be identical across dispatcher and agent APIs. | Define one shared state contract in the model/service layer; stale mutations return conflict. |
| 7↔8 | P7 retries/close → P8 audit/metrics | Every lifecycle transition must emit one ordered event with actor and tenant. | Transition service owns event creation; metrics consume events/state, not ad-hoc route writes. |
| 8↔9 | P8 metrics/outbox → P9 acceptance tests | P9 success criteria require measurable latency, duplicate, overload, and recovery signals. | P9 may fail a rollout when evidence is missing or thresholds are not met; it cannot weaken production invariants. |
| 0 | P0 self-consistency | The source plan lists Q1–Q7 as blocking questions but also supplies recommended values and asks to begin by freezing them. | Treat the recommended values as provisional frozen decisions for implementation; surface the cost of changing them before P1 migration. |
| 1 | P1 self-consistency | Empty and existing database upgrade paths both need idempotent migration behavior and preflight duplicate checks. | Add migration tests and an explicit preflight check before marking P1 complete. |
| 2 | P2 self-consistency | “Last admin” protection and session revocation require transactionally serialized role changes. | Implement under one repository transaction with a version predicate; return 409 on stale versions. |
| 3 | P3 self-consistency | UI authorization and logout are separate behaviors but share auth cache/socket teardown. | Keep permission gating and logout teardown in testable helpers; do not duplicate auth state. |
| 4 | P4 self-consistency | Idempotency and ownership checks must happen before side effects. | Use authenticated user/tenant and PostgreSQL uniqueness as the authority; no natural-language trigger. |
| 5 | P5 self-consistency | Redis failure must stop new binding without losing PostgreSQL work. | Presence lookup fails closed for dispatch; existing durable handoffs remain queryable. |
| 6 | P6 self-consistency | Dispatcher replicas must not coordinate through a Redis lock. | Use `FOR UPDATE SKIP LOCKED`, deterministic lock order, and database uniqueness only. |
| 7 | P7 self-consistency | Reaper and user accept can race. | Require assignment/version predicates so exactly one terminal transition wins. |
| 8 | P8 self-consistency | Relay retries can duplicate delivery. | Use durable event IDs and consumer-side deduplication; mark published only after successful publish. |
| 9 | P9 self-consistency | Rollout thresholds are operational gates, not unit-test behavior. | Keep load/fault evidence separate from deterministic contract tests and require APISIX for business E2E. |

## Rulings

- Ruling: implement the plan's recommended Q1–Q7 values as the initial contract — the user asked to begin implementation and the source plan provides concrete defaults; cost if wrong: migration/API/state-machine rework before release.
- Ruling: treat the phase table as the executable task list because the source document has no granular `Task N` headings — this preserves the plan's scope while allowing reviewable increments; cost if wrong: some steps may need further decomposition before their implementer dispatch.

## Progress

- Task 0: complete (P0 baseline and decision freeze recorded)
  - Backend baseline: 840 passed, 2 skipped, 4 existing Windows asyncio warnings.
  - Frontend baseline: 234/234 tests and TypeScript clean.
  - Admin baseline: 318/318 tests and TypeScript clean after restoring the user's ignored `frontend-admin/src/lib` baseline files.
  - Current worktree contains only the required untracked migration overlay (`0014–0022`, `027`) plus ignored `frontend-admin/src/lib`; none is part of this task's commit set.
  - Design freeze: `docs/superpowers/specs/2026-09-20-cs-dispatch-rbac-logout-design.md`.

- Task 1: fix round 1/5 in progress — task review rejected the first implementation.
  - Critical: legacy assignments with NULL `handoff_id` must not be treated as active offers or make migration fail.
  - Important: assignment/handoff/agent tenant relationships need cross-tenant protection; schema tests need real migration coverage.
  - Minor: remove duplicate empty-schema foreign-key creation and scope SQL assertions to each statement/table.
- Task 1: fix round 1/5 — 3 findings addressed, 2 Important findings remain open (commit 37a2c46..279087e).
  - Remaining: preserve legacy `ON DELETE SET NULL` semantics for agent deletion; prevent the complete Alembic chain from retaining the old single-column assignment FK alongside the new tenant composite FK.
- Task 1: fix round 2/5 — 2 findings addressed, 0 Critical/Important open; one wording minor deferred (commit 279087e..e7c7bae).
- Task 1: complete (commits 37a2c46..e7c7bae, review clean).

- Task 2: fix round 1/5 — first review rejected P2 (commit e7c7bae..a7f8438).
  - Critical:客服档案创建遗漏 `display_name` 非空字段，真实 PostgreSQL 会失败。
  - Important: `csRole` 变更未吊销旧 session/refresh；租户缺失静默降级 `default` 且列表/审计边界不完整；最后 admin 并发锁顺序可能死锁；关键安全行为测试是假绿。
- Task 2: fix round 1/5 — implementation reports all Critical/Important findings addressed (commit a7f8438..72be04d); re-review pending.
  - Evidence reported: P2 focused 65 passed, P1 schema 15 passed, `py_compile`, Ruff, and `git diff --check` passed.
  - Explicit verification boundary: shared database has pre-existing duplicate active P1 handoffs, so the complete 028→029 chain was not claimed as verified; clean/governed database validation remains a P9/deployment gate.
- Task 2: fix round 2/5 — re-review found test evidence gaps: old access token was not sent through the real middleware, and PostgreSQL concurrency was hand-written SQL (commit 72be04d..8db330d).
- Task 2: fix round 3/5 — real middleware 401 evidence added; re-review then required the concurrency test to call the production RBAC update function and clean audit rows (commit 8db330d..a23ebe4).
- Task 2: complete (commit range e7c7bae..a23ebe4; final re-review PASS).
  - Latest reported evidence: P2/P1 focused 66 passed, real production-function concurrency 1 passed, `py_compile`, Ruff, and `git diff --check` passed.
  - Remaining declared gates: complete shared 028→029 deployment chain, APISIX session/tenant injection, and global advisory-lock throughput are P9/deployment or Minor scope items.

- Task 3: fix round 1/5 — first P3 review found production Sidebar outside QueryClientProvider and collapsed menu hidden (commit f9d76ad..e059fe5).
- Task 3: fix round 2/5 — final review found mobile zero-width sidebar overflow after enabling desktop popover overflow (commit e059fe5..bb83772).
- Task 3: complete (commit range a23ebe4..bb83772; final re-review PASS).
  - Focused evidence: 7 files / 55 tests; full frontend-admin suite reported 23 files / 284 tests; `git diff --check` passed.
  - `npx tsc --noEmit` remains a declared pre-existing/deployment gate: generated `.next*` references and ignored `lib/fetcher.ts` exports do not match base `api/client.ts`; no P3 source error was reported.

- Task 4: fix round 1/5 — independent review found dependency-stage database failures could escape the endpoint `try` block as HTTP 500 (commit 811c39b..5b19987).
- Task 4: complete (commits 811c39b, 5b19987; final re-review PASS).
  - Added tenant/user-trusted `POST /cs/conversations/{conversation_id}/handoff` with required `Idempotency-Key`, PostgreSQL conversation row lock, active handoff reuse, atomic conversation projection update, 600-second deadline and fail-closed database errors.
  - Replaced the user-side transfer quick prompt with a direct API call, stable per-conversation idempotency key, duplicate-click guard and waiting state card; ordinary quick prompts remain on the streaming path.
  - Evidence: backend P4 `12 passed, 5 skipped`; user frontend full suite `235 passed`; frontend TypeScript, Ruff and diff-check passed. Real PostgreSQL transaction/100-concurrent acceptance is explicitly skipped in this environment because the configured database lacks the P4 schema fields; it remains a deployment gate, not a pass claim.
  - Review finding closed: `get_session()` now maps dependency-stage `MemoryDatabaseUnavailable` to 503 and has a dedicated regression test.

- Task 5: implemented (commit 00466df) + fix round applied and verified.
  - Redis-backed ticket (60s TTL, `GETDEL`/Lua atomic redeem), presence TTL 45s, heartbeat 15s, targeted broadcast by `(tenant_id, agent_id)`, ticket endpoint resolves agent via `cs_agents.auth_user_id`.
  - Fix round (commit 5b9175a) closed 4 review gaps: API-Key channel rejected for ticket issuance; presence key components URL-encoded; sync Redis calls moved to `asyncio.to_thread`; APISIX `gateway-auth.lua` injects `X-Tenant-Id` from JWT claim and strips it from forged headers.
  - Evidence: P5 focused `13 passed`; P4 + gateway-contract focused `13 passed, 5 skipped`; `compileall`, Ruff and `git diff --check` clean. Report: `task-5-report.md`.
  - Declared gates (not claimed as passed): real two-instance Redis ticket redemption across API-A/API-B, and a real APISIX request proving `X-Tenant-Id` injection.

- Task 6: implemented, pending independent review.
  - Added `dispatch/{repository,presence,event_relay}.py`, `workers/cs_dispatcher.py`, `config/cs_dispatch.py`, and the `cs-dispatcher` compose service (2 replicas, healthcheck, `CS_DISPATCH_MODE` default `off`).
  - `dispatch_once` performs the whole offer in one PostgreSQL transaction: priority/FIFO handoff claim, Redis-backed online filter, capacity + least-loaded round-robin agent selection, `agent_offered` transition, `offered` assignment, conversation projection, durable `conversation.offered` event; broadcast happens after commit only.
  - Lock order fixed as `conversations → handoffs → cs_agents` with a lock-free queue-head pre-read, because P4's ingress transaction locks conversation-then-handoff and the reverse order deadlocks on the same conversation.
  - `CS_DISPATCH_MODE` three-state: `off` never touches the DB, `shadow` is a real dry-run (selection computed, nothing written/broadcast), `enforce` binds.
  - Evidence: P6 focused `53 passed, 1 skipped`; `compileall`, Ruff and `git diff --check` clean; `docker compose config --services` resolves `cs-dispatcher`.
  - Declared gates (not claimed as passed): real PostgreSQL 100-way concurrency / 500-dispatch load-delta / no-overload acceptance (shared DB still lacks the 028 columns), actually starting the compose service (would disturb the dev stack other sessions are using), Redis outage drill, and end-to-end offer delivery to the agent browser (needs P7 workbench).

- Task 6: review follow-ups absorbed into Task 7 batch (commits 079ff58 → 74ef4c3); the real-PostgreSQL acceptance gates that Task 6 declared are now closed by Task 9's temp-DB acceptance run (see Task 9).

- Task 7: implemented (commit 74ef4c3).
  - Offer lifecycle: `dispatch/offers.py` (accept/decline/reassign), `GET /cs/agents/me/offers`, `POST .../accept|decline`, `POST /cs/handoffs/{id}/reassign` (supervisor/platform-admin only). Stale version / expired / reaped offer → 409, non-assigned agent → 403, cross-tenant → 404.
  - `dispatch/reaper.py` runs every 1s tick in front of dispatch: 30s expired offers go back to the queue; attempts ≥ 5 or total deadline passed → terminal close, conversation resumes AI, notification event persisted to the outbox. Declining/timing-out agents enter a 60s per-handoff offer cooldown (`agent_in_offer_cooldown_expr`); attempt budget is not rolled back on decline.
  - Handoff state machine fixed: `agent_offered` had been written since P6 but was missing from the enum (would raise `ValueError` on close/intercept paths); now WAITING_HUMAN ⇄ AGENT_OFFERED → HUMAN_ACTIVE/CLOSED with matching intercept handling.
  - Durable outbox write side: all lifecycle events are appended in-transaction (`outbox.append_event(pending)`), published after commit by the worker relay.
  - Identity: offer endpoints resolve the agent server-side from `cs_agents.auth_user_id` (API-Key channel 403); claim/agent-messages/close bodies no longer require client agent_id.
  - Frontend workbench: manual agent-ID input removed; new "待接单" panel (offers with countdown, accept/decline); queue renamed 处理中/排队; WS handles offered/offer_expired/offer_declined/reassigned/handoff_closed; offer list authority is `GET /cs/agents/me/offers` (WS events only trigger refresh).
  - Evidence: P6+P7 focused `94 passed, 1 skipped`; compileall, Ruff, `git diff --check` clean. Report: `task-7-report.md`.

- Task 8: implemented (commit 9de1e26).
  - Prometheus metrics exposed via `/metrics`: `cs_dispatch_queue_depth`, `cs_dispatch_offered_total{result}`, `cs_dispatch_wait_seconds`, `cs_dispatch_reaped_total{action}`, `cs_dispatch_online_agents`, `cs_outbox_pending`, `cs_outbox_lag_seconds`, `cs_outbox_published_total{result}`. Instrumented at worker tick (result counters + per-second gauge refresh), relay (per-event published|deferred + pending/lag gauges), reaper (released/closed_*), and service (wait histogram from ingress to dispatch).
  - Ops endpoint `GET /cs/ops/dispatch/stats` (require_admin_user): DB snapshot of queue/offered/enabled agents/outbox pending and lag, consistent with `/metrics` semantics.
  - Alert rules: new group `agent-platform-cs-dispatch` in `docker/prometheus-alert-rules.yml` (outbox lag > 5s, backlog > 100, queue depth > 50, online agents == 0, presence-unavailable spike, reaper close spike).
  - Evidence: P8 focused tests (tick metric assertions + ops stats 3 cases) — P6–P8 focused `80 passed, 1 skipped`; YAML parses; Ruff/compileall/diff-check clean. Report: `task-8-report.md`.

- Task 9: implemented (uncommitted at note time), evidence-first.
  - **Real PostgreSQL acceptance PASSED** on an isolated temp DB (`cs_dispatch_acceptance_*`, migrations 002→006→014→020→028 applied, dropped afterwards): A) 40 handoffs × 8 concurrent dispatcher tasks → 40 dispatched, zero duplicate active assignments, zero over-capacity, handoff/assignment state fully consistent (674 SKIP LOCKED contentions absorbed); B) capacity cap=2 → exactly 2 dispatched; C) 3 equal-capacity agents × 15 handoffs → 5/5/5 perfect round-robin. Report: `docs/reports/cs-dispatch-pg-acceptance-2026-09-20.json` (supersedes Task 6's "shared DB lacks 028 columns" gate).
  - Rollout ladder: `CS_DISPATCH_ROLLOUT_PERCENT` registered in `sys_config` (DB override + 15s TTL, env fallback); `dispatch_once` gates `enforce` by stable conversation-hash bucketing (`rollout_skipped`), `shadow` stays full-coverage; controller `scripts/cs_dispatch_rollout.py` (--show/--set with audit JSON).
  - Load test: `scripts/cs_dispatch_loadtest.py` (200-concurrency burst + 20 RPS × 10 min sustained, JSON report). Gateway-attached runs require the APISIX entry from the gateway-migration line; script probes before firing.
  - Fault drills: `scripts/cs_dispatch_chaos.py` — read-only preflight (Redis ping/heartbeat keys, outbox backlog, 028 columns, API health) + the only controlled write drill (`stop/start cs-dispatcher`, heartbeat expiry observation); Redis/PG/API drills remain manual checklists because those containers are shared with other sessions. Preflight executed: PG reachable, Redis reachable, dispatcher not running (mode off), shared DB confirmed without 028 (production gate stands).
  - Declared gates (not claimed as passed): actual gateway-attached load run and container-level chaos execution need the deployment environment (APISIX entry + change window); shared-DB 028/029 migration chain still pending.
