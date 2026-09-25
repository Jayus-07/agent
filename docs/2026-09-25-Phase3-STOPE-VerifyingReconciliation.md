# Phase3 STOP E 验收报告——verifying / IN_DOUBT 人工 reconcile 闭环

> 日期：2026-09-25 ｜ Final Closure F1 阶段 ｜ 基线：HEAD（F0 后 62c2b39 一线）
> 前置：STOP D 报告（`docs/2026-09-24-Phase3-STOPD-BusinessEntityIdempotency.md` §17）明确放行 STOP E：「verifying/CONFIRMED/EXECUTING 卡死行的人工 reconcile workflow」

## 1. Verdict

```text
STOP_E_PASS=true
VERIFYING_RECONCILIATION_FROZEN=true
```

## 2. 只读审计结论（F1.1，14 问全部取证）

| # | 审计项 | 结论 |
|---|---|---|
| 1 | verifying 产生路径 | CS：`confirmation_flow._handle_confirm` 捕获 `SideEffectOutcomeUnknown` → `finalize_current(state='verifying')`（executed_at 落执行时刻）；booking：executor UNKNOWN → 订单 IN_DOUBT |
| 2 | 存哪张表 | `customer_service.confirmations`（state='verifying'）+ `ai.idempotency_records`（未决态）+ `travel.booking_orders`（in_doubt），同库 agent_memory |
| 3 | tenant_id | 有（051 身份列 + 账本 tenant_id） |
| 4 | operation identity | 有：051 五列身份（tenant/action/target/target_id/semantic_fingerprint） |
| 5 | idempotency key | 有：`operation='cs.action.execute'` + `client_key='cs_action:{confirmation_id}'` |
| 6 | provider request identity | 契约层 `derive_provider_key`（opaque 确定性派生）；CS 当前模拟执行未绑真实 provider |
| 7 | provider capability model | `PROVIDER_CONTRACTS` 集中 registry（native/lookup/policy/evidence/activation_gate） |
| 8 | 关联链查询 | 原 CLI 只查账本单行——本轮补齐 inspect（确认行+账本+守卫语义一屏） |
| 9 | 既有自动 reconcile | booking recovery scan（beat）有；CS 侧零自动对账（by design：UNKNOWN 不自动裁决） |
| 10 | 支持 lookup 的 provider | fake_booking_native、fake_booking_clientref |
| 11 | 完全不可查 | smtp、agently_mail、fake_booking_bare、business_service_http（UNKNOWN） |
| 12 | 裁决权限 | 容器内 CLI（复用，未造第二套后台）；executed 裁决强制 operator 身份 |
| 13 | 裁决审计 | ledger error_code 标记 + booking_events（cause=manual）+ **本轮新增 audit_logs 结构化审计**（actor_type='human'） |
| 14 | 三方收敛 | 原：账本仅 stale-running 形态可裁决（见 Gap G-E0）；booking 订单状态可转但账本 no-op；**CS 确认行零收敛路径**——本轮全部闭合 |

## 3. Gap Matrix（缺口 → 修复）

| 缺口 | 内容 | 修复 | 证据 |
|---|---|---|---|
| **G-E0**（主缺口） | `resolve_stale_side_effect` WHERE 只匹配 `status='running'`；而 CS verifying / booking Model C 的账本主形态是 executor 落库的 `failed+IDEMPOTENCY_UNCERTAIN`——**既有裁决通道对主形态静默 no-op**，`_decide_claim` 对该形态恒 CONFLICT → 裁决 not_executed 后重试被永久阻断 | `shared/idempotency.py` 新增双形态裁决族：`_resolve_side_effect_in_connection`（同事务核）+ `resolve_uncertain_side_effect` + `resolve_side_effect`（统一分发，支持 `conn=` 参与外部事务）；`resolve_stale_side_effect` 语义不变（重构为委托） | test_uncertain_form_* 4 例 + dispatcher 例 |
| **G-E1** | CS confirming 行 verifying 态零收敛代码（状态机已预留 VERIFYING→SUCCESS/FAILED 出口但无触达者），051 守卫被永久占据 | 新模块 `customer_service/reconciliation.py`：`resolve_verifying_confirmation`——FOR UPDATE 行锁 + 账本双形态 CAS + 确认行 CAS + audit_logs 审计**单事务原子**；账本无未决记录拒绝收敛（不猜） | test_verifying_* 全组 |
| **G-E2** | booking `manual_resolve` 账本侧静默 no-op（同 G-E0），reason 硬编码 | 改走 `resolve_side_effect` 分流 + reason 参数透传 | tests/travel/booking/test_stop_e_manual_resolve_ledger.py ×2 |
| **G-E3** | 运维面缺 list verifying / inspect 关联链 / 裁决确认行能力；裁决无结构化审计 | `scripts/idempotency_ops.py` 新增 `verifying` / `inspect-confirmation` / `resolve-confirmation` 三子命令；审计落 `customer_service.audit_logs`（actor_type='human'、actor_id=operator、before/after 快照） | 运行态实证（§6） |
| **G-E4** | CONFIRMED/EXECUTING 崩溃卡死行（账本 stale-running 双子形态）无收敛入口 | 同一出口：executing 行 + 账本 stale-running（崩溃窗）可裁决；活跃执行（租约未过期）账本 CAS 自动拒绝 | test_stale_running_executing_row_converges |

## 4. 裁决状态机（对齐现有命名，零新状态）

```text
VERIFYING/EXECUTING(卡死)
  ├─ decision=executed      → CONFIRMED_SUCCESS：账本→SUCCEEDED(MANUAL_RESOLVED_EXECUTED)
  │                           + 确认行→success（同 key 后续重放结果，绝不重执行）
  ├─ decision=not_executed  → CONFIRMED_NOT_EXECUTED：账本→RESOLVED_NOT_EXECUTED
  │                           （解除 UNCERTAIN 阻断，FAILED 可接管重试）+ 确认行→failed
  └─ decision=unresolved    → UNRESOLVED：账本/确认行/守卫一律不动，仅落审计
```

不引入 INVESTIGATING 新状态（禁止破坏已冻结状态机）；UNRESOLVED=保持 verifying 即保持阻断。

## 5. Tests（F1.4 测试矩阵 14 项全覆盖）

命令与结果（三个环境，如实区分）：

```text
# ① app 容器内（容器网络直连权威库 agent_memory；第二路径，docker cp 本轮改动文件）
python -m pytest tests/test_stop_e_verifying_reconciliation.py \
  tests/travel/booking/test_stop_e_manual_resolve_ledger.py -q
→ 18 passed in 14.81s   【权威库证据】

# ② 宿主机 venv（默认 PGPORT=5432，与历史基线同环境）
python -m pytest tests/test_stop_e_verifying_reconciliation.py \
  tests/travel/booking/test_stop_e_manual_resolve_ledger.py -q --no-cov
→ 18 passed in 34.36s

# ③ 宿主机 venv 直连 5433：单文件实跑因宿主→docker PG 连接风暴
#    （~5.1s/连接，见 §7）达数十分钟级，中止未取全量结果——以①替代权威库口径。
#    business_guard（051 真库并发组）宿主机 5433 实跑：20 passed in 271.93s
```

# L2 触碰面回归（容器内权威库）：Step6 幂等 + SMTP durable + provider 契约
#   + tests/travel/booking 全套 + CS confirmation/repo
→ 168 passed（7 failed + 20 error 均为镜像陈旧产物：烘焙的是 addaa32 之前
  的旧 mock/旧配置，非本轮文件；对应现行文件宿主机实跑全过）
# business_guard（051 真库并发组）宿主机 5433：20 passed in 271.93s
# confirmation_store/repo/confirmation 宿主机 5432：此前 L2 批全过
```

矩阵映射：并发双裁决 ✅ / 重复 success 裁决 ✅ / 重复 not_executed 裁决 ✅ / 跨租户 IDOR ✅ /
verifying 不允许直接释放守卫（unresolved 后仍阻断）✅ / provider A lookup 成功（STOP L B 系既有 +
恢复扫描回归）✅ / provider B lookup 确认未执行 ✅ / provider C 无法查询 ✅ / ledger UNCERTAIN 双向裁决 ✅ /
本地 success+provider unknown（crash 窗）✅ / provider success+local crash（stale running 形态）✅ /
reconcile 后重复 worker 恢复（重试恰好一次）✅ / 审计日志完整性 ✅ / CLI 面（运行态）✅。

红线断言：**false_success=0 / false_failure=0 / duplicate_side_effect=0 / cross_tenant_access=0**（各测试内显式断言）。

## 6. Runtime Evidence（真实权威库闭环）

容器内 CLI（`idempotency_ops.py`，agent_memory 权威库实跑）：

```text
1) verifying → 列出 2 条种子卡死行，ledger_pending=true
2) resolve-confirmation --confirmation-id stope-live-1 --decision not_executed
   --operator wang --reason "op:wang 供应商后台无此退款单 TKT-101"
   → {"resolved": true, "state": "failed", "ledger={'uncertain': True, ...}"}
3) resolve-confirmation --confirmation-id stope-live-2 --decision unresolved
   → {"resolved": false, "state": "verifying", "kept_unresolved（守卫保持阻断）"}

库内终态核验：
  stope-live-1: conf=failed   ledger=failed/RESOLVED_NOT_EXECUTED   （已收敛，可安全重试）
  stope-live-2: conf=verifying ledger=failed/IDEMPOTENCY_UNCERTAIN  （守卫持续占住）
审计：audit_logs 2 行——human/wang not_executed success；human/li unresolved denied
清理：种子 4 行全部删除，库归零（count=0 核验）
```

## 7. 环境披露（本轮实证发现的运行环境事实）

- **宿主机→docker PG（5433）新建连接 ~5.1s/个**（vpnkit 回环特性；5432 原生 PG ~50ms）。
  逐操作新建连接的测试形态在宿主机跑 5433 会放大为数十分钟级——非挂死（CPU≈0、PG 侧无等待）。
  既定绕行 = 容器内第二路径（本轮已实测）。属环境特性，不修代码。
- **宿主机 pytest 默认 `PGPORT=5432` 落到宿主机原生同名库**（`.env` 的 `PGPORT=5432` 是容器
  网络口径）；宿主机直连权威库必须显式 `PGPORT=5433`。双 PG 陷阱的测试侧变体。
- app 容器镜像烘焙的测试文件落后 HEAD（addaa32 之前的 mock 签名），容器内复跑涉及 CS
  confirmation_store / business_guard 时需先 docker cp 现行文件——镜像漂移债（X-6 同类），
  随下次镜像构建消化。

## 8. Gates

```text
G-E1 裁决状态机三出口=PASS（零新状态）
G-E2 运维面三能力（list/inspect/resolve）=PASS（CLI 复用，运行态实证）
G-E3 认证/权限/租户/ reason 必填/CAS/审计/operator/时间戳 =PASS
     （容器内 CLI 天然认证边界=主机 root；HTTP 面零暴露——grep 路由无 reconcile 端点）
G-E4 测试矩阵 14 项=PASS（双环境）
G-E5 红线四零（false_success/false_failure/duplicate/cross_tenant）=PASS
```

## 9. Remaining Risk

- IN_DOUBT admin 列表页/告警仍为 Deferred（CLI 发现路径本轮实测可用）——维持登记。
- 账本侧（无确认行绑定的通用 ledger 行，如 SMTP）的人工裁决仍走原 `resolve` 子命令
  （stale running 形态）；`failed+UNCERTAIN` 形态如无 CS/booking 包装层，暂无 CLI 直达
  （函数层 `resolve_uncertain_side_effect` 已可用）——登记为 P3 便利性债务，非安全缺口。

## 10. External Blocker

NONE。

## 11. Commit

见本报告随附提交（pathspec：shared/idempotency.py、customer_service/reconciliation.py、
travel/booking/reconciliation.py、scripts/idempotency_ops.py、tests×2、runbook、本报告）。
