# 剩余 needs_validation 优先级分档（2026-11-09）

来源：C:\Users\wh\security-audit-skill\agent\run-2\NEEDS-VALIDATION.md（81 条）。
分档依据：影响面（外部可利用/跨租户/身份/注入）> 前置条件与资源放大 > 部署与供应链配置。
状态列只标注能对应到已合并 PR 的项；其余一律「待验证」，不代表不存在问题。

## P0 · 立即验证/修复（外部可利用、跨租户、身份伪造、注入、密钥）

| # | 线索 | 档 | 状态 |
|---|---|---|---|
| 1 | agent-ask:runner-session-load-error-echoed-to-reply-channel | P0 | 待验证 |
| 2 | agents.inventory-read-no-identity-dependency | P0 | 待验证（#390 未明确覆盖 /agents） |
| 9 | backend.observability.replay.internal-product-default | P0 | 待验证 |
| 10 | backend/evaluation-v2/evidence-search-missing-kb-authz | P0 | 待验证 |
| 19 | cs-claim-close.missing-tenant-predicate | P0 | 待验证 |
| 20 | cs-realtime.event-envelope-missing-tenant-id | P0 | 待验证 |
| 21 | cs.faq.tenant_unbound_corpus | P0 | 已修 #394 |
| 24 | cs.refund.http_review_missing_tenant_binding | P0 | 待验证 |
| 25 | cs.ticket.admin_default_tenant_fallback | P0 | 待验证 |
| 26 | cs_ops.dispatch_stats.supervisor_cross_tenant_aggregate | P0 | 待验证 |
| 32 | evaluation.asset-reads-ungated | P0 | 待验证 |
| 33 | fastapi-direct-header-identity-trust | P0 | 待验证 |
| 37 | input_guard.question-context-exemption-defeats-strong-injection-block | P0 | 待验证 |
| 38 | internal-ai.actor-user-memory-retrieval | P0 | 待验证 |
| 39 | kafka-whatsapp:unverified-body-identity-as-agent-principal | P0 | 待验证 |
| 43 | local-auth.username-charset.gateway-header-smuggling | P0 | 待验证 |
| 44 | maps.navigate.backend-key-disclosure | P0 | 待验证 |
| 47 | mcp-rag.tools.missing-tenant-pushdown | P0 | 已修 T4c |
| 48 | mcp-sql-list-tables-missing-sql-read | P0 | 已修 T4c |
| 49 | mcp.bundle-global-namespace-foreign-tenant-mutation | P0 | T4c 部分 |
| 57 | prompt-eval.github-artifact-release-identity-unbound | P0 | 待验证 |
| 58 | prompt-release.approval-before-key-binding | P0 | 待验证 |
| 60 | rag-expansion-tenant-scope | P0 | 待验证 |
| 67 | rag.askchain.empty-tenant-no-predicate | P0 | 待验证 |
| 69 | rbac.reset-password.can-reset-super-admin | P0 | 已修 #393 |
| 70 | rbac.security-sessions.missing-tenant-scope | P0 | 已修 T4b |
| 71 | runner.clarify-exemption-unscoped-security-categories | P0 | 待验证 |
| 72 | selection-funnel-missing-tenant-isolation | P0 | T4d 部分 |
| 76 | sidecars.observability.unauth-listeners.flat-agent-net | P0 | 待验证 |
| 77 | tool-approval.auto-mode-unrestricted | P0 | 待验证 |
| 78 | toolserver.http-fetcher.outbound-fetch-no-ssrf-guard | P0 | 待验证 |
| 80 | travel.booking:transition_order-missing-state-whitelist | P0 | 待验证 |

## P1 · 高（需前置条件或用于资源放大、审计泄漏、边界不一致）

| # | 线索 | 档 | 状态 |
|---|---|---|---|
| 4 | automation.cron.unbounded_fanout | P1 | 待验证 |
| 5 | automations.celery.broker-publish-acl-unbound | P1 | 待验证 |
| 6 | automations.webhook.apikey-middleware-vs-gateway-route | P1 | 已修 T7b |
| 7 | automations.webhook.body-buffer-before-auth-no-size-limit | P1 | 已修 T6 |
| 8 | automations.webhook.hmac-no-timestamp-optional-dedup | P1 | 待验证 |
| 12 | bff-arraybuffer-body-unbounded | P1 | 已修 T6 |
| 13 | bff-xff-header-rate-limit-bypass | P1 | 已修 #387 |
| 14 | business-mock.x-internal-token.fail-open-default | P1 | 已修 T7a |
| 15 | capabilities.manifest-read-no-identity-dependency | P1 | 已修 #390 |
| 16 | celery-unbound-task-id-failed-replay | P1 | 待验证 |
| 22 | cs.output-guard.other-user-mask.context-field-never-populated | P1 | 待验证 |
| 23 | cs.qa.global_faq_miss_cross_tenant | P1 | 待验证 |
| 35 | frontend-admin/evaluation/evidence-sessionstorage-account-switch | P1 | 待验证 |
| 41 | local-auth.change-password.reissues-stale-roles | P1 | 待验证 |
| 42 | local-auth.session-revocation.audit-default-gates-log-only | P1 | 待验证 |
| 45 | markdown.external-image.selection-report | P1 | 待验证 |
| 50 | memory_store_tool.explicit-write-no-confirmation | P1 | 待验证 |
| 51 | observability.alert-detail.user-content-unredacted-egress | P1 | 待验证 |
| 52 | observability.alert-webhook.credential-in-failure-log | P1 | 待验证 |
| 53 | observability:dto-redaction-skips-span-metrics | P1 | 待验证 |
| 55 | observability:rag-traces-endpoints-unauthed-full-trace-read | P1 | 已修 #390 |
| 56 | observability:trace-summary-no-time-retention-plaintext-question | P1 | 待验证 |
| 59 | prompt-release.eval-version-fallback | P1 | 待验证 |
| 61 | rag-lifecycle-stale-publication | P1 | PR #397 |
| 62 | rag-nonactive-chunk-detail | P1 | PR #397 |
| 63 | rag-pdf-ocr-region-fanout | P1 | 已修 #395 |
| 64 | rag-pdf-page-raster-area | P1 | 已修 #395 |
| 65 | rag-upload-worker-stale-authz | P1 | PR #397 |
| 66 | rag-xml-dom-prelimit-allocation | P1 | 待验证 |
| 68 | rag.chain.memory-callsites-missing-product-owner | P1 | 待验证 |
| 73 | selection-funnel-unbounded-import-body | P1 | 已修 T6 |
| 74 | sensitive-api-guard.audit-non-admin-bypass | P1 | 待验证 |
| 75 | server.metrics-outside-auth-middleware | P1 | 已修 T1 |
| 79 | toolserver.sqlalchemy-writer.raw-table-identifier-interpolation | P1 | 待验证 |
| 81 | workflow.execution.request-budget-unbound | P1 | 待验证 |

## P2 · 中（部署面配置、CI 供应链、低危或已有缓解）

| # | 线索 | 档 | 状态 |
|---|---|---|---|
| 3 | apisix-test.envfile_full_secret_injection | P2 | 待验证 |
| 11 | backend/sql/policy.py:cte-name-skips-qualified-table-scope | P2 | 已修 #391 |
| 17 | ci.pip-upgrade-unpinned-secret-eval | P2 | 待验证 |
| 18 | ci.pr-workflows.pr-scripts-run-with-default-github-token | P2 | 待验证 |
| 27 | deploy.isolated-stack.shared-workspace-env-credentials | P2 | 待验证 |
| 28 | deployment.auth-redis.requirepass-optional-auth-store | P2 | 待验证 |
| 29 | deployment.grafana.default-admin-password | P2 | 待验证 |
| 30 | deployment.identity-header.agent-net-peer-co-residence | P2 | 待验证 |
| 31 | deployment.promtail.docker-socket-mount | P2 | 待验证 |
| 34 | fastapi.metrics.unscoped-api-fallback | P2 | 待验证 |
| 36 | github-actions.workflow-dispatch-unreviewed-ref-secrets | P2 | 待验证 |
| 40 | llm.balance-no-identity-gate | P2 | 决策保留（登录可读） |
| 46 | mcp-probe.dns-target-not-pinned | P2 | 待验证 |
| 54 | observability:pg-trace-mirror-no-retention | P2 | 已修 T7c |

## 建议执行顺序

1. P0 中尚未验证的：先做 2（/agents 清单）、10（评测资产读）、19/24/25（客服租户）、37/71（输入注入豁免）、57/58（Prompt 发布身份链）。
2. P0 已修项回填审计台账，逐条给出「PR + 测试」证据，避免重复排查。
3. P1 按域成批推进（RAG 资源上限、审计/日志脱敏、CI 工作流权限）。
4. P2 与部署面一起做，需 owner 在目标环境观察，不能仅靠源码判定。

## 计数

- P0：32 条；P1：35 条；P2：14 条；合计 81 条。
- 已能对应到 PR 的：25 条；其余待验证。