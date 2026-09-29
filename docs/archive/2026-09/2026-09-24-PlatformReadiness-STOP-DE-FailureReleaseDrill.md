# Platform Readiness STOP D/E — 依赖故障恢复 与 重启/发布演练

> 日期：2026-09-24 ｜ 前置：STOP C PASS（30b76de）
> 工具：`backend/scripts/e2e_platform_drills.py`（容器级真实操作，逐项验后即恢复）
> 上轮 Domain Runtime STOP E 已覆盖项以引用方式并入

---

## STOP D — Dependency Failure & Data Consistency

| # | 依赖故障 | 演练/证据 | 结果 |
|---|---|---|---|
| D1 | Redis down | 上轮 E7：Redis 死端口→ConversationContext memory fallback→完整出单；本轮 E3 重启演练恢复 | PASS |
| D2 | PG 故障 | 启动期：compose db-migrate 门 fail-closed（端口不开放，E8 实测+A3 链路）；重启后连接池自愈（本轮 E4） | PASS |
| D3 | Celery broker down | 设计口径=代码证据：tasks API broker 不可达 503 语义（tasks.py:98-104、task_manager.py:178-181）；rag_upload 可信租户不降级直接 settle 失败/否则进程内索引兜底（rag_upload.py:1044-1060）；broker redis noeviction+AOF（compose:1061-1072） | PASS（设计+代码实证） |
| D4 | Worker SIGKILL | **本轮实机**：任务 RUNNING 中容器内 `kill -9 1`（崩溃语义）→ unless-stopped 自动重启 → lease 回拨 + `stale_execution_recovery` sweep（生产同路径）→ **SUCCESS，E1=be2d6f68→E2=c7efcc87 换发，recovery=1** | PASS |
| D5 | APISIX down | **本轮实机**：stop apisix → gateway 0（不可达）；期间 app 直连口绑定 127.0.0.1（PortBindings 实测）无外部暴露；start 后 gateway 200 | PASS |
| D6 | LLM provider | timeout/429/5xx 由冻结 Model Governance 承担（proxy fallback 链+llm_failures/llm_fallback_total 指标族）；实机注入需改共享 DB 治理数据，披露为结构验证 | PASS（披露） |
| D7 | Embedding 失败 | 上轮 E5：embedding/rag-service 死端口 → RAG 拒答兜底、路由规则层照常守门、不随机应答 | PASS |
| D8 | Reranker 失败 | chain 门控降级（rag_gate_degraded_total{layer} 指标在 registry；EvidenceGate 降级路径），检索失败走拒答不猜答案（D7 同路径） | PASS |
| D9 | MinIO | **平台无对象存储**（compose/Dockerfile 零命中；存储=pgvector+app_data 卷） | N/A（拓扑事实） |
| D10 | 部分事务 | CS outbox 与状态变更同事务（outbox.py:38-70）；CS 动作审计同事务幂等 ledger（audit_repo ON CONFLICT）；幂等账本/确认 CAS 兜底 | PASS（结构） |
| D11 | 重复投递 | Phase2 R 矩阵 SUCCESS_NOOP 吸收（frozen 报告）；确认单 CAS 仅一方成功 | PASS |
| D12 | Recovery+重复 | D4 实机：E1 崩溃 → sweep → E2 重执行；副作用幂等由 D10/D11 结构收敛（at-least-once + idempotent convergence） | PASS |
| D13 | Memory 写失败 | end_turn 落库/摘要/L3 全部后台化+try/except（service.py），聊天不因 memory 失败失败；失败有 error 日志不假装成功 | PASS（代码） |
| D14 | Usage/Billing 失败 | usage 写入失败不阻断已完成的 LLM answer（proxy 用量写入软失败+llm_usage_missing_total 指标留痕） | PASS（代码+指标） |
| D15 | 依赖恢复 | Redis/PG/APISIX/worker 四类重启演练全部**免全平台重启**恢复（本轮 E1/E3/E4/D4/D5） | PASS |

**发现（非缺陷，运维语义澄清）**：`restart: unless-stopped` 对 `docker kill`（手动停止语义）不拉起，仅对崩溃退出码拉起——演练方法从 `docker kill` 修正为容器内 `kill -9 1`（真实崩溃语义）后闭环。运维 runbook 须写明：手动 stop 的 worker 需手动 start。

---

## STOP E — Restart / Release / Rollback

| # | 场景 | 实测 | 结果 |
|---|---|---|---|
| E1 | app 重启（活跃 session） | `docker restart -t 20`：重启前「记住 47」→ 重启后问数字 → **回答 47**；chat_messages=4 条无损（PG 权威历史） | PASS |
| E2 | worker 重启（RUNNING 任务） | =D4（崩溃语义自动重启+recovery） | PASS |
| E3 | Redis 重启 | restart 后 health=200、chat 正常（控制面/缓存恢复） | PASS |
| E4 | PG 重启 | restart 后 app/worker 连接池自愈、chat 正常 | PASS |
| E5 | Rolling App | **单机 compose 单副本，无滚动能力——诚实声明 stop-the-world**（全量 dev-rebuild 为唯一发布形态），不伪装支持 | 明确 |
| E6 | Rolling Worker | 同上；新旧 worker 共存风险面（任务名/队列/checkpoint/角色快照）已在 STOP A §5 列明，Gate 以 GIT_COMMIT 三方一致拦截 | 明确 |
| E7 | Schema upgrade 顺序 | **先 migration 后 app**（db-migrate 门已强制该顺序）；044-049 纯 expand → 旧 app 兼容新 schema（B5 扫描） | PASS |
| E8 | Rollback | 044-049 无破坏性操作 → 新 schema 兼容旧 app，**当前无 ROLLBACK_BLOCKED_AFTER_MIGRATION 边界**；回滚=换回上一镜像（本地镜像保留旧版，无 registry 为已知限制 P2-20） | 明确 |
| E9 | Feature flag | CS/TRAVEL/SELECTION/REFUSAL_CLARIFY 等 env 开关在册，改 .env 重启生效（免代码回滚）；不新造 flag 系统 | 明确 |
| E10 | In-flight SSE | 发布/重启时 SSE 断开；客户端重连+会话历史无损（E1 实证 PG 历史权威）；服务端 graceful 700s 温停 | PASS |
| E11 | In-flight task | 发布中任务由 lease/recovery 收敛（D4 实证），新旧代码混合不会写出错误状态（fencing 拒绝旧 owner） | PASS |
| E12 | db-migrate 镜像版本 | **以 GIT_COMMIT=c5d8d2c 重建 db-migrate → rc=0 → preflight `--image` 三层 MIGRATION_STATE_OK（53/53）** | PASS |
| E13 | Release script | `scripts/release.sh`（见 G1：preflight→build→up→smoke 全流程） | 交付 |

**恢复语义总表**：全部依赖（Redis/PG/APISIX/worker）故障与重启均无需全平台重启即恢复；唯一例外=发布本身（stop-the-world）。

```text
STOP_D_PASS    = true
STOP_E_PASS    = true
STOP_F_ALLOWED = true
```
