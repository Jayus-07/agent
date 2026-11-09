# 剩余 needs_validation 优先级分档（2026-11-09）

来源：C:\Users\wh\security-audit-skill\agent\run-2\NEEDS-VALIDATION.md（81 条）。
分档依据：影响面（外部可利用/跨租户/身份/注入）> 前置条件与资源放大 > 部署与供应链配置。
状态列只标注能对应到已合并 PR 的项；其余一律「待验证」，不代表不存在问题。


## 状态更新（2026-11-09 收尾）

本轮及上一轮已合并：#387 #388 #389 #390 #391 #392 #393 #394 #395 #396 #397 #398 #400 #401 #402 #404 #405 #406 #407 #408。

- 已修：2 / 6 / 7 / 9 / 10 / 11 / 12 / 13 / 14 / 15 / 19 / 20 / 21 / 24 / 25 / 26 / 32 / 37 / 38 / 41 / 43 / 44 / 47 / 48 / 54 / 55 / 57 / 58 / 61 / 62 / 63 / 64 / 65 / 69 / 70 / 71 / 73 / 75 / 78 / 79
- 已修（分支待合并，2026-11-09 批次三）：1 / 39 / 60 / 67 / 77 / 80 —— 各自独立 worktree 已完成实现与验证（双租户/无凭据对照测试、容器内运行时验证、本机重建健康），分支：fix/rag-tenant-failclosed-20261109（60+67）、fix/kafka-inbound-strict-identity-20261109（39）、fix/tool-approval-auto-prod-guard-20261109（77）、fix/travel-booking-transition-whitelist-20261109（80）、fix/runner-session-load-error-envelope-20261109（1）
- 部分：49（T4c 余项）、72（T4d owner 维度）
- 已实现但生产待启用：33（GATEWAY_TRUST_SECRET；本机已开并验证）
- 决策保留：40（/llm/balance 登录可读，客服工具栏依赖）；49 余项（bundle 平台级共享授权单元为设计意图，T4c 已覆盖动态成员与 grant 一致性）；72 余项（选品池同租户内多操作者共池为设计意图，T4d 已覆盖租户隔离主体）
- 仍待验证（P0）：76（sidecar 鉴权，源码最小部分为 alert-bridge token 校验，部署面需 owner 观察）——P0 源码可修项至此全部处置完毕

下一步建议顺序：批次三六分支提交建 PR 合并 → #78 → #76（源码部分）→ P1 按域成批推进（68 记忆产品命名空间需先定旧记忆迁移策略）。

## P0 · 立即验证/修复（外部可利用、跨租户、身份伪造、注入、密钥）

| # | 线索 | 档 | 状态 |
|---|---|---|---|
| 1 | agent-ask:runner-session-load-error-echoed-to-reply-channel | P0 | 已修（分支待合并 batch3） |
| 2 | agents.inventory-read-no-identity-dependency | P0 | 已修 #400 |
| 9 | backend.observability.replay.internal-product-default | P0 | 已修 #407 |
| 10 | backend/evaluation-v2/evidence-search-missing-kb-authz | P0 | 已修 #401 |
| 19 | cs-claim-close.missing-tenant-predicate | P0 | 已修 #404 |
| 20 | cs-realtime.event-envelope-missing-tenant-id | P0 | 已修 #407 |
| 21 | cs.faq.tenant_unbound_corpus | P0 | 已修 #394 |
| 24 | cs.refund.http_review_missing_tenant_binding | P0 | 已修 #404 |
| 25 | cs.ticket.admin_default_tenant_fallback | P0 | 已修 #404 |
| 26 | cs_ops.dispatch_stats.supervisor_cross_tenant_aggregate | P0 | 已修（2026-11-08，本轮复核） |
| 32 | evaluation.asset-reads-ungated | P0 | 已修 #401 |
| 33 | fastapi-direct-header-identity-trust | P0 | 开关已实现 #407/#408（生产待启用） |
| 37 | input_guard.question-context-exemption-defeats-strong-injection-block | P0 | 已修 #402 |
| 38 | internal-ai.actor-user-memory-retrieval | P0 | 已修 #406 |
| 39 | kafka-whatsapp:unverified-body-identity-as-agent-principal | P0 | 已修（分支待合并 batch3） |
| 43 | local-auth.username-charset.gateway-header-smuggling | P0 | 已修 #406 |
| 44 | maps.navigate.backend-key-disclosure | P0 | 已修 #406 |
| 47 | mcp-rag.tools.missing-tenant-pushdown | P0 | 已修 T4c |
| 48 | mcp-sql-list-tables-missing-sql-read | P0 | 已修 T4c |
| 49 | mcp.bundle-global-namespace-foreign-tenant-mutation | P0 | 部分 T4c；余项决策保留（平台级共享授权单元为设计意图） |
| 57 | prompt-eval.github-artifact-release-identity-unbound | P0 | 已修 #405 |
| 58 | prompt-release.approval-before-key-binding | P0 | 已修 #405 |
| 60 | rag-expansion-tenant-scope | P0 | 已修（分支待合并 batch3） |
| 67 | rag.askchain.empty-tenant-no-predicate | P0 | 已修（分支待合并 batch3） |
| 69 | rbac.reset-password.can-reset-super-admin | P0 | 已修 #393 |
| 70 | rbac.security-sessions.missing-tenant-scope | P0 | 已修 T4b |
| 71 | runner.clarify-exemption-unscoped-security-categories | P0 | 已修 #402 |
| 72 | selection-funnel-missing-tenant-isolation | P0 | 部分 T4d；余项决策保留（同租户共池为设计意图） |
| 76 | sidecars.observability.unauth-listeners.flat-agent-net | P0 | 待验证（部署面 owner 观察；源码最小部分 alert-bridge token 校验未动工） |
| 77 | tool-approval.auto-mode-unrestricted | P0 | 已修（分支待合并 batch3） |
| 78 | toolserver.http-fetcher.outbound-fetch-no-ssrf-guard | P0 | 已修 #392 |
| 80 | travel.booking:transition_order-missing-state-whitelist | P0 | 已修（分支待合并 batch3） |

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
| 41 | local-auth.change-password.reissues-stale-roles | P1 | 已修 T5 |
| 42 | local-auth.session-revocation.audit-default-gates-log-only | P1 | 待验证 |
| 45 | markdown.external-image.selection-report | P1 | 待验证 |
| 50 | memory_store_tool.explicit-write-no-confirmation | P1 | 已修（证据门，分支待合并 memory-p1） |
| 51 | observability.alert-detail.user-content-unredacted-egress | P1 | 待验证 |
| 52 | observability.alert-webhook.credential-in-failure-log | P1 | 待验证 |
| 53 | observability:dto-redaction-skips-span-metrics | P1 | 待验证 |
| 55 | observability:rag-traces-endpoints-unauthed-full-trace-read | P1 | 已修 #390 |
| 56 | observability:trace-summary-no-time-retention-plaintext-question | P1 | 待验证 |
| 59 | prompt-release.eval-version-fallback | P1 | 待验证 |
| 61 | rag-lifecycle-stale-publication | P1 | 已修 #397 |
| 62 | rag-nonactive-chunk-detail | P1 | 已修 #397 |
| 63 | rag-pdf-ocr-region-fanout | P1 | 已修 #395 |
| 64 | rag-pdf-page-raster-area | P1 | 已修 #395 |
| 65 | rag-upload-worker-stale-authz | P1 | 已修 #397 |
| 66 | rag-xml-dom-prelimit-allocation | P1 | 待验证 |
| 68 | rag.chain.memory-callsites-missing-product-owner | P1 | 已修（ADR-0012 第 1 期收口，分支待合并 memory-p1） |
| 73 | selection-funnel-unbounded-import-body | P1 | 已修 T6 |
| 74 | sensitive-api-guard.audit-non-admin-bypass | P1 | 待验证 |
| 75 | server.metrics-outside-auth-middleware | P1 | 已修 T1 |
| 79 | toolserver.sqlalchemy-writer.raw-table-identifier-interpolation | P1 | 已修 #392 |
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
