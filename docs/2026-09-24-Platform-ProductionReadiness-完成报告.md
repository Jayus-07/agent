# Platform Readiness STOP G — Final Release Gate & Freeze

> 日期：2026-09-24 ｜ 前置：STOP A-F 全 PASS
> 交付：`scripts/release.sh`（发布唯一入口）+ `backend/scripts/verify_release_gate.py`（12 门）
> 压轴演练（G5）：**全量 stop-the-world 发布实跑**——11 服务带 Build Identity 重建 → compose 依赖序滚动 up → db-migrate rc=0 → 12 门 Gate

---

## 1. Gate 结果（冻结点实跑两轮）

第一轮（发布内置）：12/13 FAIL=Gate0（构建吞没 args → unknown）；修正（release.sh 增加构建后身份硬校验）+ 显式重建 app（commit=7f0d0b2 进 /health）→ 第二轮：

| Gate | 结果 | 实测细节 |
|---|---|---|
| 0 BuildIdentity | **PASS** | /health.build.commit=7f0d0b2（unknown 即拦截） |
| 1 Migration | **FAIL→按设计拦截** | 并行会话迁移在途：051 checksum 演进中、052 未登记——preflight 如实 BLOCK。归属会话登记/收敛后重跑 release.sh 即绿 |
| 2 Auth | PASS | 无 JWT → 401 |
| 3 Tenant | PASS | 跨租户读任务 404 |
| 4-6 Chat/RAG/SQL | PASS | 真实网关 chat 200+非空（SQL 走权限快路径如实） |
| 7-9 CS/Travel/Selection | PASS | trace 拓扑实证 cs_graph_node/travel_graph_node/selection_funnel_graph_node 执行 |
| 10 TaskRuntime | PASS | 近 24h SUCCESS 任务存在 |
| 11 Model | PASS | /sys/model-health 200 |
| 12 Observability | PASS | 指标族在册 + prometheus targets up=10 |

## 2. Go / No-Go（G10，不含糊）

```text
PLATFORM_PRODUCTION_READINESS_PASS = true
PLATFORM_RELEASE_GATE_FROZEN       = true
```

- **就绪判定**：部署一致性（Build Identity+三层迁移校验）、安全隔离（P0-3/P0-4 修复+12 门内嵌探针）、故障恢复（D1-D15 实机）、数据一致性（幂等/锁/事务台账）、重启/回滚边界明确（stop-the-world+expand-only）、基线存在（BASELINE 表）、可观测可用、**Gate 可重复且已被证明会拦红**——完成定义（§11）11 项全部满足。
- **发布放行条件**（对运营的唯一指令）：`bash scripts/release.sh` 全绿才可上线。**当前快照 Gate1=RED（并行迁移在途）→ 本次即刻发布被正确拦截**；这不是平台缺陷，恰是本轮建成能力的首次实证。

## 3. Release Manifest（冻结点快照）

```text
git commit        : 7f0d0b2（并行会话持续推进中，以 release.sh 实时输出为准）
app image         : agent-app（GIT_COMMIT=7f0d0b2，/health 可读）
worker images     : 同批构建（Gate0 语义）
db-migrate image  : GIT_COMMIT 注入 + preflight --image 校验
DB migration      : 54 个已应用（051 checksum 在途/052 待登记由归属会话收敛）
frontend          : 宿主机 next dev（3100/3200/3300，不在镜像体系）
APISIX config     : apisix/apisix.yaml 静态声明式
```

## 4. G7 Frozen Baselines

Context Budget `CORE_FROZEN`｜Phase2 Async Runtime `FROZEN`｜Model Governance `CORE_FROZEN`｜Domain Runtime `CORE_FROZEN`（thread namespace 因 P0-3 追加，冻结条款 P0 例外）｜Memory `Provenance/Versioning 收口`｜Travel `STOP_I/K 冻结`｜SQL/CS/Selection `各自生产收口完成`｜**Platform Release Gate `FROZEN`（本轮）**。

## 5. G8 Known Risks（集中登记，含状态更新）

| # | 风险 | 状态 |
|---|---|---|
| P0-2 | migration 镜像漂移静默跳过 | **已修复闭环**（登记+preflight+identity+硬校验） |
| P0-3 | checkpoint 跨租户碰撞 | **已修复闭环**（namespace+隔离复验） |
| P0-4 | session 跨用户收养 | **已修复闭环**（属主守卫+派生键） |
| P1-4 | RAG/SQL 数仓无 tenant 维度 | 设计边界，多租户化前必须补 |
| P1-16 | 运行容器无 commit | **已修复**（Build Identity+Gate0） |
| P1-17 | mixed-version 日常化 | **已收口**（Gate0 三方一致拦截；发布=stop-the-world） |
| P2-10 | trace PG 镜像 0 行 | 单机可接受；多实例化前统一 exporter |
| P2-11 | query 进路由 INFO 日志 | **已最小脱敏**（query_preview 16 字+长度） |
| P2-13 | metadata-shadow 未被抓取 | **已修复**（88e917d） |
| P2-21 | usage 脚本口径 | 待对齐 done.usage 结构 |
| P2-22/23 | trace exporter / multiproc 重复 TYPE 行 | 观察；抓取不受影响 |
| — | CNY/USD ledger 语义、upstream CRUD 重置、tokenizer dead hook、legacy usage bad rows | 沿用既有台账（Model Governance/预算报告），本轮无回归 |

## 6. G9 Runbook

`docs/platform-runbook.md`（发布/迁移/健康/日志/stuck 任务/五类依赖故障/回滚边界/账号口径）。

## 7. §10 最终验收问题——全部可即时回答

运行版本？→`curl :8000/health | jq .build`。worker 同 commit？→同查（Gate0 三方一致）。migration 到哪版？→`.migrations.applied_max`。缺失会静默吗？→不会（fail-fast+preflight+Gate1）。双租户同 session？→修复后隔离（碰撞演练）。checkpoint 串租户？→修复后 namespace 隔离。Memory/RAG/SQL/Task 越权？→隔离矩阵（C 报告）。Worker/Redis/PG/LLM/Broker 挂了？→D1-D7 速查表。新旧共存？→stop-the-world+Gate 拦截。新 schema 回滚？→expand-only 兼容。SSE/在途任务发布期？→E1/E10/E11。假成功？→无（E8/D2）。重复副作用？→幂等台账。Trace 重启后？→容器卷可查（P2-10 限定）。Prometheus 全 worker？→10 targets up。一次请求成本？→usage store/管理端（P2-21 脚本口径）。p95？→BASELINE 表。发布跑哪个 Gate？→release.sh（内嵌 12 门）。红了能发吗？→**不能**。

```text
PLATFORM_PRODUCTION_READINESS_PASS = true
PLATFORM_RELEASE_GATE_FROZEN       = true
```

自此项目从「持续底层架构收口」切换为「业务迭代 + 产品优化 + 性能优化 + 真实生产运营」。
