# 前端拆分 v3 企业版修订计划（用户端 / 管理运营端双分区 + S0 安全轨）

> 建立时间：2026-09-15 22:25
> **取代 v2**：`docs/superpowers/plans/2026-09-15-frontend-split-implementation-plan.md`（v2 仅作历史留档）
> 来源：v2 计划 + 两轮只读审计（B1~B6）+ 人类 4 轮决策 + 另一会话「py / Java 彻底分离」决策的接缝评估
> 执行顺序：**S0 → P0 → P1 → P2**；S0 不等 `frontend/` 冻结，安全修复最高优先级
> 每笔提交单一目的、可独立 `git revert <sha>`，提交号回填本文件「决策台账」

> 📌 **跨会话协同**：与「py 自建用户体系」会话的接口、边界与冲突规则见
> `docs/coordination/2026-09-15-frontend-split-x-auth-session.md`（含 append-only 回执区）。
> 需要对方配合的三件事：① 给 roles claim（枚举对齐 `viewer/editor/admin`）；② 告知 `008_local_auth.sql` 执行方式；③ 确认 `lib/auth.ts`/`authFetch.ts` 归属与顺序。

---

## 零、决策台账（全部已锁定，不得推翻）

| # | 决策 | 结论 | 来源 |
|---|---|---|---|
| D1 | URL 加 `/admin` 前缀 | **不加** | 人类 |
| D2 | ARCH-06 服务端方法级权限 | **提前到 P2**，exit criteria = §八 5 组接口 5/5 覆盖（硬性） | 人类 |
| D3 | `features/` 目录约定 | **引入**：`features/<domain>/{components,api,hooks,types,store}`，**页面仍留 `app/` 下** | 人类 |
| D4 | `/reports`、`/alerts` 归属 | **`(workspace)` 单组**；用户端 nav「我的报告/我的告警」，管理端 nav「全部报告/告警规则」，页内按角色切数据范围。**不引入第二 URL** | 人类 |
| D5 | E2E 策略 | **P2 起最小 Playwright 3 例**；账号优先 auth-service 测试租户（仅 DB 插数据，不改 Java 代码），跨仓阻塞时白名单模拟并标注「非生产等价」 | 人类 |
| D6 | navConfig 测试语义 | **每份内部唯一 + 跨份 `SHARED_PATHS` allowlist**（当前仅 `/reports`、`/alerts`） | 审计 B6 |
| D7 | 不管 Java | N3a **只改 APISIX**；Java 源码不改，改为 `部署清单.md` 登记前置条件；**S0-3（改 `JwtUtil` 加 roles claim）取消** | 人类 |
| D8 | N3b 形态 | **纯 Python fail-closed**；`/prompts*` 全部端点（含读）要求 `X-Internal-Token`；浏览器侧本轮不开放 | 人类 |
| D9 | `/prompts` 定位 | **先预留**：P2-0（alembic 迁移）移出全部 gate；M3 敏感页可用性验收降为 **4 个**；**nav 中可见，但不参与验收** | 人类 |
| D10 | APISIX reload | **可以改**，接受 9080 短暂抖动 | 人类 |
| D11 | 另外 4 组敏感接口 | **拉进 S0**（见 ADR-011，含 audit 模式约束） | 人类 |
| D12 | 与 py 用户体系的接缝 | **P2-4 改挂 py 用户体系**（外部依赖）；`lib/auth.ts` / `lib/authFetch.ts` **本会话可先动** | 人类 |
| D13 | 前端非 auth 流量切回 APISIX | **另开一单评估**，不塞进 S0（但见 ADR-011 的耦合约束） | 人类 |
| D14 | `NEXT_PUBLIC_API_KEY` 明文 | **另开一单**，不塞进 S0 | 人类 |

**产品清单冻结**：P2 新增 `/agents`、`/skills`、`/approvals` + `/knowledge/authorization` 子页；**`/users` 移 P3**。
**路由计数**：现状 **31** → 分区后 **31** → M3 **35**。

---

## 零·二、进度看板（实时回填）

| # | 事项 | 状态 | 完成时间 | 提交 / 备注 |
|---|---|---|---|---|
| — | v3 计划落盘（取代 v2） | ✅ 完成 | 2026-09-15 22:31 | `78fd3aa` |
| **S0-4** | 内部令牌通道 fail-closed + 依赖上提共享 | ✅ 完成 | 2026-09-15 22:33 | `44d2e53`；`pytest backend/tests/api/` **81 passed**（新增 7 项契约测试 + 防回退锁） |
| **S0-1** | APISIX 剥离 `X-Operator-*` | ⬜ 待做（**下一笔**） | — | 改 `gateway-auth.lua` L35；**需 reload `agent-apisix`（已获人类授权）**；⚠️ **Java 登记项作废** —— `api-gateway/` 正被并发会话删除（76 项 staged 删除），N3a 简化为纯 APISIX |
| **S0-2** | prompts 关停客户端角色 + fail-closed + 角色来源收敛为单一入口 | ✅ 完成 | 2026-09-15 22:58 | `f5a95f0`；`pytest backend/tests/api/ backend/tests/prompts/` **238 passed**；18 处 `Header(default=)` 清零、`_operator_role`/`_operator_id` 死代码删除、13 个端点改用 `resolve_operator_role`（含此前无鉴权的 `/meta/registry`） |
| **S0-5** | 四组敏感接口收口（audit 模式） | ⬜ 待做 | — | 代码进 S0；**`enforce` 切换与 X-1（流量切回 APISIX）同批** |
| P0-1 ~ P0-6 | 地基收敛 | ⬜ 待做 | — | **前置：声明 `frontend/` 冻结**（约束 9 / ADR-009） |
| P1-0 ~ P1-6 | 分区 + 双 Shell | ⬜ 待做 | — | |
| P2-1 ~ P2-5 | 管理端 | ⬜ 待做 | — | |
| S0-4b | rag-server 内部令牌语义对齐 | ⬜ 待做 | — | **S0-4 暴露的遗留**：`backend/services/rag_server.py` 开发模式（token 空）仍全放行，与收紧后的 `/internal/ai/*` 语义不一致；其测试 docstring 中「对齐 `/internal/ai/*` 行为」已过时 |

---

## 一、企业版结论

**可实施 —— 无阻塞。** 落盘前 4 处实现核查（均已实测）：

| # | 核查项 | 结果 | 影响 |
|---|---|---|---|
| 1 | `prompts.py` 改动点 | `grep -c "Header(default="` = **18 处** | S0-2 以 18 为准 |
| 2 | fail-closed 仓内先例 | **有**。`middleware/auth.py`（2026-08-21 加固）：未配 `API_KEY` 且未开 `ALLOW_UNAUTHENTICATED` → **503 拒绝** | S0-4 照此约定修，不自创风格 |
| 3 | `verify_internal_token` 归属 | 现定义在 `routes/internal_ai.py` L29-40；`/internal` 在 `_SKIP_AUTH_PREFIXES` | S0-4 上提为共享依赖，供 prompts 复用 |
| 4 | 测试迁移点 | `test_prompts_api.py` 5 + `test_workflow.py` 7 = **12** ✓ | 同批迁移 |

**关键现状（构成 S0 的全部理由）**

1. `X-Operator-Role` 是**客户端自设头**，未被任何网关剥离，且是 prompts 的唯一角色来源 → 可伪造。
2. `prompts.py` 写端点默认 `editor`、`/seed` 默认 `admin` → **剥头后无凭据写请求仍通过**。
3. **浏览器业务流量直连 `:8000`，不经 APISIX**（`frontend/.env.local` 中 `NEXT_PUBLIC_API_URL` 两行均注释、`API_URL` 未设）⇒ 该路径上 `X-User-Id` **不会被注入**，角色判定**不能依赖网关注入**。
4. `NEXT_PUBLIC_API_KEY` 明文入客户端 bundle ⇒ `X-API-Key` **不构成秘密**。
5. 除 prompts 的伪鉴权外，`observability`/`evaluation`/`rag`/`schedules` 四组接口**零方法级鉴权**。
6. 当前唯一挡住 prompt 全控的是 **N1（`prompts` 表缺失 → 全 500）**，即风险**休眠** ⇒ **N1 迁移必须晚于 S0-2**。

---

## 二、ADR 与修订补丁

### ADR-001（B1）· 双端页单组归属

**决策**：`/reports`、`/alerts`（含 `[id]`）归属 `(workspace)` 单组；管理端从 AdminShell nav 进入同一 URL，页内按角色切数据范围。
**依据**：Next.js route group 不产生路径段，**一个 URL 只能解析到一个 group**；两组同名路径 `next build` 直接报错。

```markdown
| **D4** | `/alerts`、`/reports` 归属 | **归属 `(workspace)` 单组**。用户端 nav「我的报告 / 我的告警」；管理端 nav「全部报告 / 告警规则」——**两份 nav 指向同一 URL，差异在页内数据范围（我的 / 全部）由角色决定**。「同一 URL、两个 Shell」在 Next.js 不可实现（route group 不产生路径段，一个 URL 只能解析到一个 group），故不采用；引入第二 URL 破坏「URL 不变」，本阶段不做。 | ADR-001 |
```

### ADR-002（B2）· 错误码契约先行

```markdown
| P0-1a | 建 `src/api/client.ts`：合并 `lib/fetcher.ts`(108 行) + `lib/authFetch.ts`(49 行)，统一 ApiError / 401→refresh / 超时 / SSE 首帧解析；**基址改为可配置映射（当前全部等价指向同一 base），为「业务数据迁 Java 独立库 / 彻底分离」预留多后端支持** | 一个 client | ① `npx tsc --noEmit` 0 错；② 401 refresh 行为不变（含 single-flight）；③ 旧两文件改 re-export，调用方零改动；④ 基址映射有默认值，当前行为完全等价 |
| P0-1b | 错误码**机制**：新增 `src/api/errors.ts`（`DomainErrorCode` 联合 + 映射层 + 扩展点），`ApiError` 增 `code?: string` | 映射层 + 测试 | 用**合成码**（如 `RAG_TEST_001`）写单测断言映射与降级文案；**不要求真实 RAG 码存在**（后端 `RAG_XXXX` 尚未定义）。真实码映射为**前后端联合项**，后端定义后单独 PR |
```

### ADR-003（B3）· 产品清单冻结

```markdown
| P2-3 | 新增页 **3 个顶层**：`/agents`、`/skills`、`/approvals`；知识库授权矩阵**并入 `/knowledge/authorization` 子页**；`/users` 移 P3 | 3 顶层 + 1 子路由（31 → 35） | 数据真实来自后端，无硬编码；接口未就绪只出骨架 + 空态 |

| +32 | `/approvals` | 管理端 | ★ P2-3（后端 `approvals` router 已有） |
| +33 | `/agents` | 管理端 | ★ P2-3（需 `GET /api/agents`） |
| +34 | `/skills` | 管理端 | ★ P2-3（需 `GET /api/capabilities`） |
| +35 | `/knowledge/authorization` | 管理端 | ★ P2-3 子路由（需 `GET /api/knowledge/authorization`；`owner_depts` 已有，`allowed_roles` 为规划字段未实现） |
| — | `/users` | 管理端 | **移至 P3** |
```

### ADR-004（B4）· N3 拆解与角色来源重建 ★核心

**决策**：N3a 只改 APISIX；N3b 纯 Python fail-closed，**角色来源收敛为单一可插拔解析器**；顺序 **S0-4 → S0-2 不可颠倒**。

```markdown
| **8a** | **N3a · APISIX 剥离** | `apisix/plugins/gateway-auth.lua` L35 `FORGED_HEADERS` = 四头，**不含** `X-Operator-Role` | 加入 `X-Operator-Role`、`X-Operator-Id`。**Java 源码不改**，改为 `部署清单.md` 登记前置条件：「启用 Java 网关前必须先把 X-Operator-* 加入其 forgedHeaders」 | S0 高（纵深防御） |
| **8b** | **N3b · 纯 Python fail-closed（角色来源可插拔）** | `prompts.py` **18 处** `Header(default=...)`；`_operator_role()`/`_operator_id()` 死代码（L68-72） | ① 18 处改 `None`，缺省 **403**；② 新增**单一角色解析入口** `resolve_operator_role(request)`，当前实现 = 仅认 `X-Internal-Token`（浏览器侧不开放）；③ **`/prompts*` 所有端点（含读）一律经该解析器判定**，禁止散落判定；④ 删除一切 `X-Operator-Role` 读取与两处死代码；⑤ 同批迁移 **12 处**测试调用方 | S0 最高 |
| **8c** | **S0-4 · 令牌通道 fail-closed（8b 硬前置）** | `internal_ai.py` L29-40 `if not AI_INTERNAL_TOKEN: return` —— 未配置时**放行**；而 `middleware/auth.py` 同场景是 **503 拒绝** | 照 `ALLOW_UNAUTHENTICATED` 既有约定改为**显式开关**：生产未配令牌 → 拒绝 + 告警；**并把依赖上提为共享依赖**（`app/api/deps.py` 或 `middleware/auth.py`）供 prompts 复用 | S0 最高（先于 8b） |
```

> **为什么必须单一入口**：py 用户体系上线后，角色来源会从「没有」变为「py 自己的身份体系」。届时**只在该解析器内加一个分支**；若 18 处各自判定，将来必然重写。这让「S0-2 现在做」与「将来接 py 用户体系」**正交**。

### ADR-005（B5）· 验收三层分层

```markdown
| A9 | 全路由 curl 200 的**假通过**（SPA 一律 200；`/prompts` 等为 `'use client'` + useEffect 取数，接口 500 也返回 200，实例如 N1） | 中 | 高 | 冒烟**分三层**：① **HTTP 可达层** 31 条 curl 200（只证可达）；② **API 业务层** 直查各页依赖后端接口，断言响应体不含 `"error"`（**真发现层**），并设 **`RESERVED_ENDPOINTS` 豁免清单（当前仅 `/prompts*`）**；③ **E2E 行为层** 仅覆盖权限重定向 / 落地重定向 / Shell 差异。**禁止对 SPA HTML 断言业务错误** |
```

### ADR-006（B6）· nav 测试语义

```markdown
| P1-3 | `navConfig.tsx` 拆 `nav/{workspaceNav,adminNav}.tsx`；`AdminShell` 按"内容/运营/可观测/治理"分组；双端页两处不同标签（「我的报告/我的告警」vs「全部报告/告警规则」）**指向同一 URL**。**`/prompts` 保持 nav 可见**（决策 D9） | 两份导航配置 | `navConfig.test.ts` 改为：① **每份内部**路径唯一；② 跨份共享路径须显式登记 `SHARED_PATHS`（当前仅 `/reports`、`/alerts`）；③ `/agent` 入口不丢失 |
```

### ADR-007 · N1 降级 + 迁移前置检查

```markdown
> **N1（alembic 迁移）降级为「后续完善批次」，不进任何 gate。**
> 权威命令（容器内）：`docker exec agent-app-1 python -m alembic -c alembic.ini -n memory upgrade head`
> **迁移前置检查（硬性）**：执行前必须确认 **S0-2 已绿**。原因：`prompts` 表一出现，N3 立即从「休眠」变为「可利用」。
> **✅ 2026-09-15 22:58 起该前置已满足**（`f5a95f0` 闭环 N3）—— 迁移竞态解除：
> 并发会话的 `008_local_auth.sql` 与 `prompts`（alembic memory 线 0002）同属 `agent_memory`，
> 若其执行方式为 `alembic -n memory upgrade head`，会连带建出 `prompts` 表；**现在跑是安全的**。
> 落地形态：`scripts/precheck_n1_migration.sh` 断言 ① `to_regclass('public.prompts')` 为空；② 无凭据请求 `/prompts` 返回 **401/403**（而非 500/200）。
```

### ADR-008 · P2-4 角色来源改挂 py 用户体系（R2）

```markdown
| P2-4 | `feat(auth): 前端消费真实角色` | 替换 P1-0 白名单为真实角色源。**来源 = py 用户体系（另一会话交付，标为外部依赖）**；S0-3（改 JwtUtil 加 roles claim）已因「不管 Java」取消 | 白名单代码删除；E2E E1/E2 绿 | **若 py 用户体系未就绪 → P2-4 降级 P3**，前端继续用 `NEXT_PUBLIC_ADMIN_USERS` 白名单并标注「非生产等价」，**不得宣称已实现权限隔离** |
```

### ADR-009 · 并行冻结与 auth 链路先后（R4）

```markdown
> **冻结清单（开工前 `git status` + mtime 核对）**：`frontend/` 全域 + `frontend/src/lib/auth.ts` + `frontend/src/lib/authFetch.ts`。
> **先后顺序（决策 D12）**：**本会话先动** —— 先做 P0-1（只做结构合并、零行为变更），再让 py 用户的 auth 重构在其上落地。
> 理由：结构合并是纯搬迁、可 revert；若先重构 auth，P0-1 就要在移动的靶子上合并。
```

### ADR-010 · S0-5 四组敏感接口只读收口（决策 D11）

**决策**：`observability` / `evaluation` / `rag` / `schedules` 四组接口的收口**拉进 S0**，但**必须带模式开关**，否则会打断 4 个管理页。

```markdown
| **S0-5** | 四组敏感接口收口 | 上述四组目前**零方法级鉴权**（仅公开的 `X-API-Key`） | ① 定义「可信身份」= `X-User-Id`（网关注入）**或** `X-Internal-Token`；② **模式开关 `SENSITIVE_API_GUARD_MODE=audit|enforce`，默认 `audit`**（记录不阻断）；③ 因浏览器业务流量当前**直连 8000、无身份头**，`enforce` 必须与 **D13（流量切回 APISIX）** 同批切换 —— 否则 4 个管理页会全 403 | S0（代码）；enforce 待 D13 |
```

> **与 D13 的耦合说明**：D13 原定「另开一单」。但 **S0-5 的 `enforce` 依赖它**。因此：**S0-5 代码进 S0，`enforce` 切换与 D13 同批**。这样既满足「拉进 S0」，又不让 4 个管理页在 S0 期间不可用。

---

## 三、S0 · 安全轨（可立刻开工，与前端冻结解耦）

| # | 提交 | 改动文件 | 验收命令 | 回滚点 |
|---|---|---|---|---|
| **S0-4** | `security(internal): 令牌通道 fail-closed + 依赖上提` | `backend/app/api/routes/internal_ai.py`（L29-40）、`backend/app/api/deps.py`（新增共享依赖） | ① 生产语义未配令牌 → 拒绝（非放行）；② 显式 dev 开关下仍可用；③ `./.venv/Scripts/python.exe -m pytest backend/tests/ -q -p no:randomly --no-cov -k "internal or auth"` 绿 | 单文件 + 一处新增 |
| **S0-1** | `security(gateway): 剥离 X-Operator-*` | `apisix/plugins/gateway-auth.lua` **L35**；`部署清单.md`（登记 Java 前置条件） | ① 经 9080 带 `X-Operator-Role: admin` → 上游收不到；② 清单逐字对账 | 单文件；**需 reload `agent-apisix`**（已获授权） |
| **S0-2** | `security(prompts): 关停客户端角色 + fail-closed` | `backend/app/api/routes/prompts.py`（**18 处** + L68-72 死代码）、`tests/api/test_prompts_api.py`（**5**）、`tests/prompts/test_workflow.py`（**7**） | ① `pytest backend/tests/api/test_prompts_api.py backend/tests/prompts/ -q -p no:randomly --no-cov` 全绿；② 无凭据 → **403**；③ 带 `X-Internal-Token` → 通过；④ `grep -c "X-Operator-Role" backend/app/` = 0 | 单文件；**12 处测试须同批** |
| **S0-5** | `security(api): 四组敏感接口收口（audit 模式）` | `observability` / `evaluation` / `rag` / `schedules` 路由的收口依赖；`SENSITIVE_API_GUARD_MODE` 配置 | ① `audit` 模式下行为不变、审计日志有记录；② `enforce` 下单测断言 403（用测试开关跑）；③ 现有相关测试绿 | 逐路由；模式开关可退回 `audit` |
| ~~S0-3~~ | ~~`security(auth): JWT 加 roles claim`~~ | — | **按 D7 取消** | — |

**S0 内部依赖**：`S0-4 → S0-2` 不可颠倒；`S0-1` 与 `S0-2`/`S0-5` 顺序不敏感、可并行。

---

## 四、P0 / P1 / P2

### P0 · 地基收敛（6 笔，前端冻结后开工）

| # | 提交 | 改动文件 | 验收命令 | 回滚点 |
|---|---|---|---|---|
| P0-1 | `refactor(api): 合并网络层（含多 base 预留）` | 新增 `src/api/client.ts`；`src/lib/{fetcher,authFetch}.ts` → re-export | `npx tsc --noEmit && npx vitest run` | 旧文件在，revert 即恢复 |
| P0-2 | `feat(api): 错误码机制` | 新增 `src/api/errors.ts`、`src/api/__tests__/errors.test.ts`；`ApiError` 加 `code?` | `npx vitest run src/api` | 无调用方，无损 |
| P0-3 | `refactor(api): 域模块迁入 src/api` | `lib/api/*`(5) + `services/*`(11) → `src/api/<domain>.ts` | `npx tsc --noEmit && npx vitest run`；`grep -rn "@/services/" src \| grep -v index` 为空 | **建议单域一 commit** |
| P0-4 | `refactor(ui): components/ui 与域目录归位` | 按附录 C 迁 11 个根级组件；**合并 `EmptyState` 双份**；**本轮不引入 `features/`** | `npx tsc --noEmit && npx vitest run` + `NEXT_DIST_DIR=.next-p04 npx next build` | 逐文件可 revert |
| P0-5 | `refactor(api): barrel 兼容层` | `src/lib/api.ts`、`src/services/index.ts` 改纯 re-export | `npx tsc --noEmit` | revert 回旧路径 |
| P0-6 | `test(api): surface 契约测试` | 新增 `src/api/__tests__/surface.test.ts` | `npx vitest run`（124 → ~132） | 纯新增 |

### P1 · 分区 + 双 Shell（7 笔）

| # | 提交 | 改动文件 | 验收命令 | 回滚点 |
|---|---|---|---|---|
| P1-0 | `feat(auth): useRole` | 新增 `src/lib/role.ts`（`userInfo.roles` → 白名单兜底）+ 单测；**文件头注明「非安全边界」** | `npx vitest run src/lib/role` | 纯新增 |
| P1-1 | `refactor(app): route groups 三分区` | 建 `(public)/(workspace)/(admin)` 移动 31 条路由（**URL 不变**）+ `features/` 一次搬完 | `NEXT_DIST_DIR=.next-p11 npx next build`（路径冲突天然报错） | 纯移动，可 revert |
| P1-2 | `refactor(layout): 拆双壳` | `app/layout.tsx` 瘦身为纯壳；新增两组 layout；删 `pathname === '/agent'` 特判 | `NEXT_DIST_DIR=.next-p12 npx next build` + 三层冒烟 | 单文件 |
| P1-3 | `refactor(nav): 双 nav 分治`（ADR-006） | `navConfig.tsx` → `nav/{workspaceNav,adminNav}.tsx`；`navConfig.test.ts` 改语义；TaskSidebar「全部功能」改读 adminNav | `npx vitest run src/components/layout` | 保留旧 `navConfig.tsx` 一周期 |
| P1-4 | `refactor(trace): 详情去重` | `knowledge/operations/traces/[id]/page.tsx` 改复用 observability 组件；**两条 URL 均保留** | 两 URL curl 200 + 渲染一致 | 单文件 |
| P1-5 | `feat(auth): RoleGate` | `components/AuthGate.tsx` 加角色维度（复用 `lib/role`）；`/` 非管理员 → `/agent` | `npx tsc --noEmit`；手工：非管理员访问 `/` 落 `/agent` | 保留纯鉴权分支 |
| P1-6 | `test(smoke): 两层冒烟脚本`（ADR-005） | 新增 `scripts/smoke_frontend_routes.sh`（HTTP 31/31 + API 层无 `"error"` + `RESERVED_ENDPOINTS` 豁免） | 脚本自跑 | 纯新增 |

### P2 · 管理端（6 笔）

| # | 提交 | 改动文件 | 验收命令 | 回滚点 |
|---|---|---|---|---|
| P2-1 | `feat(admin): 敏感页 403 守卫` | 4 个功能敏感页（observability / evaluations / knowledge-pending / schedules）非管理员重定向 403；**`/prompts` 三路由纳入守卫但保持 nav 可见、标注功能预留** | 非管理员访问 → 重定向 403；**口径：UI 层，API 层拦截依赖 ARCH-06** | 新增守卫可关 |
| P2-2 | `feat(api): 4 个只读接口` | `GET /api/agents`、`/api/capabilities`、`/api/knowledge/authorization`；workflow 复用 `GET /workflows` | 契约测试 + 响应真实数据 | 新路由，可删 |
| P2-3 | `feat(admin): 3 顶层 + 1 子路由`（ADR-003） | `/agents`、`/skills`、`/approvals`、`/knowledge/authorization`（31 → **35**） | 35/35 curl 200 + API 层无 `"error"`（豁免 `/prompts*`） | 逐页可 revert |
| P2-4 | `feat(auth): 前端消费真实角色`（ADR-008） | 替换 P1-0 白名单；来源 = py 用户体系 | 白名单删除；E2E E1/E2 绿 | **未就绪则降 P3** |
| P2-5 | `feat(security): ARCH-06 敏感接口鉴权` | 5 组敏感接口方法级鉴权 | **exit criteria：§八 表 5/5 覆盖**（硬性） | 逐接口 |
| ~~P2-0~~ | ~~`fix(prompts): alembic 迁移`~~ | — | **移出 P2**，降级后续完善批次；前置检查见 ADR-007 | DB 迁移不可 revert，须 S0-2 先绿 |

---

## 五、门禁与发布

| 层 | 命令 | 含义 |
|---|---|---|
| 类型 | `cd frontend && npx tsc --noEmit` | 0 错，每笔必跑 |
| 前端单测 | `npx vitest run` | **基线 124 passed**，只增不减 |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest <目标> -q -p no:randomly --no-cov` | **必须 `--no-cov`**（55% 全局覆盖率门槛 + 沙箱 `SAFE_DELETE_FAIL_CLOSED`） |
| 构建 | `NEXT_DIST_DIR=.next-<递增> npx next build` | 指向**不存在的空目录** |
| 冒烟①HTTP | 31 条路由 curl 200 | **只证可达** |
| 冒烟②API | 直查各页依赖接口，断言无 `"error"`，**`RESERVED_ENDPOINTS` 豁免** | **真发现层** |
| 冒烟③E2E | Playwright 3 例 | 见下 |
| 回滚 | 每笔 `git revert <sha>` | 单一目的提交；DB 迁移类除外 |

**E2E 触发条件**（任一满足即跑）：① 触及 `AuthGate`/`RoleGate`/`lib/role`/两份 layout/两份 nav；② 任一 P2 提交合并前；③ 发布候选构建。
**运行环境**：`NEXT_DIST_DIR=.next-e2e-<递增> npx next dev -p 3100`。
**E1/E2/E3**：E1 `RoleGate` 对 4 个功能敏感页 + `/prompts` 的重定向；E2 `/` 非管理员落地 `/agent`；E3 `/agent` 与 `/knowledge` 的 Shell 差异。

---

## 六、另立项（不塞进 S0/P0-P2，按决策 D13/D14）

| # | 事项 | 说明 |
|---|---|---|
| X-1 | **前端非 auth 流量切回 APISIX** | `API_URL=http://127.0.0.1:9080`（纯配置）。**本为既定架构**（`chat-sse` 路由就是为经网关的 SSE 存在；当前直连 8000 的注释自称「临时回滚」）。**S0-5 的 `enforce` 与之同批切换** |
| X-2 | **`NEXT_PUBLIC_API_KEY` 收回** | 把它从客户端收回 / 改由 Next rewrite 服务端注入 |
| X-3 | **N1 迁移（`/prompts` 完善）** | 前置：S0-2 已绿（ADR-007） |
| X-4 | **`/users`（P3）** | 依赖 py 用户体系 + RBAC |
