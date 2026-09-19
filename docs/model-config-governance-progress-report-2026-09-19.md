# 大模型配置治理 — 计划 / 已完成 / 未完成报告

> 生成时间：2026-09-19 15:20
> 范围：`docs/model-config-governance-design.md`（主设计）+ `docs/model-config-admin-ui-design.md`（实现级 UI 设计）两条线
> 工作时区：`D:/Program Files/workplace/agent`
> 说明：本报告只覆盖「大模型配置治理」这一条工作线。工作区内另有并发会话在推进**另一条完全独立的功能线**（幂等键 / 澄清卡片 / trace_id / 预算计价 / 客户服务子图加固），本报告在「阻塞项」章节中标注了与之的交集，但不评价其内容。

---

## 0. 结论先行

| 维度 | 状态 |
|---|---|
| **计划总批次** | P0 → P1a-1 → P1a-2 → P1b → P2 → 管理端页面（受 B.8 硬闸门约束） |
| **已完成并入库** | P0、P1a-1、P1b、P2 的两个数据源端点、批次 A 前半 —— 共 **17 笔提交** |
| **已完成但未入库** | F 批（会话级模型切换）8 个前端文件 + 2 个后端文件；**P1a-2 凭据链路 + `vllm` 分支（1 个文件）** —— 全部落码、测试通过、留在工作区 |
| **被阻塞** | 四个端点注册、F 批收口、**P1a-2 收口**、0017 迁移提交 —— **全部卡在同一处** |
| **未开工** | 管理端页面、P2 剩余端点、模型选型进 DB 的数据通道、批次 A 后半 |
| **本会话验证** | 后端 **199 passed**（含相邻既有测试）；前端 **36 passed**（28 + 5 + 3）；两端 `tsc --noEmit` 零错误 |
| **部署状态** | ⚠️ **所有改动均未部署**。未重启任何服务、未执行迁移、未占用任何端口。线上行为与本次工作开始前**完全一致** |

**一句话**：后端治理骨架（角色注册表 → 凭据抽象 → DB 覆盖层 → 探测服务 → 只读端点 → **凭据链路闭合**）已经成型，最后一块拼图（P1a-2）也已落码验证；剩下的问题**不是「不会做」，而是「4 件事堵在同一个并发冲突上」**。

> **2026-09-19 15:25 追加**：用户拍板「改」→ P1a-2 凭据链路与 `vllm` 分支**已在工作区落码并验证**（详见 §9）。§4.1 的第 2、5 两项因此从「未开工」变为「已实施待入库」。

---

## 1. 计划：批次划分与依据

主设计确定了三层事实来源收敛方向：**`.env` 底座 → DB `sys_config` 覆盖层 → 代码默认**，解析链统一为 `resolve(role)`。

| 批次 | 目标 | 行为变化 | 依赖 |
|---|---|---|---|
| **P0** | 收敛 8 个模型角色常量，建立统一解析入口 | **零行为变化** | 无 |
| **P1a-1** | 出站凭据传参式化 + 注册表统一入口 | **零行为变化** | P0 |
| **P1a-2** | billing 传播到计价链 + **运行时凭据链路闭合** | 有（BYOK 开始生效） | P1a-1 |
| **P1b** | 供应商连通性探测服务（两端点 + 四道限制） | 新增端点 | P1a-1 |
| **P2** | 管理端所需的只读/只写端点族 | 新增端点 | P1b |
| **F 批** | 会话级模型切换（零后端依赖，可提前） | 有（回收全局切换权） | 无 |
| **管理端页面** | tab①–⑤ 实现 | 新增页面 | ⛔ **B.8 硬闸门：P1a-2 / P1b 未完成前禁止开工** |

**B.8 硬闸门原文要点**：P1a-2 / P1b 未完成前不做管理端页面，否则得到的是「配了不生效」的页面。只做**纯函数与 API 模块（含 mock 测试）**是安全的，因为它们不产生可点击的 UI。

### 1.1 本会话对计划的三次调整（均有依据，非随意变更）

1. **F 批提到最前**（文档 §15 第 690 行原已如此建议）—— 零后端依赖，且能立即收掉「editor 现在可切全局模型」的权限洞。
2. **管理端页面两次被否** —— 第一次理由：B.8 闸门未开 + 管理端前端目录已被并发会话占用；第二次理由更强（见 §5 结论 1）：**闸门要防的问题真实存在，但它在后端不在页面**，先做页面不会暴露它。
3. **P1b 整批提前** —— 探测服务不碰 `proxy` / `budget` / `pricing` / `quota` 四块并发热区，全部落在干净文件上，是当时唯一能推进的关键路径。

---

## 2. 已完成（已入库）— 17 笔提交

### 2.1 P0：模型角色注册表收敛

| 提交 | 内容 |
|---|---|
| `41a5df9` | `config/model_roles.py`（新增）：8 角色注册表 + `resolve_raw` / `resolve_name` / `resolve_effective` / `provider_of` / `get_secret` / `effective_snapshot` / `validate_roles`。**硬约束：纯 stdlib，不得 import `infra.llm.*`**（避免循环依赖） |
| `41a5df9` | `config/llm.py`、`config/rag.py`：常量改由 `resolve_name` 物化（保留字面值空串语义）；`config/startup.py` 全角色校验 |
| `dba3d57` | 实施记录：范围收窄、三个坑、并发状态、两个既有缺陷 |

- 8 个角色：`main` / `doc` / `tool_selector` / `fallback` / `ocr` / `embedding` / `rerank` / `eval_gen`
- 附带发现（**未擅自修改，仅点名**）：`LLM_FALLBACK_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507` 指向**未注册模型** → 熔断兜底实际不可用

### 2.2 P1a-1：出站凭据传参式化

| 提交 | 内容 |
|---|---|
| `3b8238f` | 抽 `shared/crypto.py`（Fernet 原语，strict=False 优雅降级 / strict=True fail-loud）；新增 `infra/llm/credentials.py`（`ProviderCredentials` + `resolve_credentials()`）；新增 `registry_store.py`；改造 `models.py` / `factory.py` / 7 个 provider 为 `build_xxx(model_name, credentials=None)` 传参式 |
| — | `models.py` 新增 `get_available_models()` / `resolve_provider(strict=False/True)` / `PROVIDER_BILLING`（`qwen_tp` → `subscription`） |

- **零行为变化**：`credentials=None` 时全部回落 `.env`，与改造前逐字一致

### 2.3 前端既有 bug 修复

| 提交 | 内容 |
|---|---|
| `ecf2e90` | 修 `frontend-admin/src/api/securityOps.ts` 的「假失败」——`PUT /sys/config/{key}` 返回**裸 dict**，前端按 `Result` 取 `res.data` 恒 `undefined` → `security/page.tsx` 抛 TypeError，**但后端已写库**。修前端不改后端（`test_sys_config_admin.py:177` 锁定契约） |
| `ecf2e90` | 新增 `securityOps.test.ts` 4 例契约测试，含**反证验证**（临时改回 `res.data` → 3 failed / 1 passed） |

### 2.4 设计文档成型

| 提交 | 内容 |
|---|---|
| `3f4a525` | `docs/model-config-admin-ui-design.md`（新增）：17 节实现级 UI 设计，含组件树、TS 接口、四级探测状态机；并修正主设计的权限与端点契约 |
| `d84dbc6` | 三项拍板落地：tab② 按 B.6「查看供应商 = admin」从严（整 tab 隐藏）；会话级模型需后端校验；删 `/cost-governance/prices` nav 条目 |

### 2.5 F 批后端半边（已入库，前端半边未入库）

| 提交 | 内容 | 测试 |
|---|---|---|
| `e2ab3ae` | `models.py` 新增 `validate_override_model()` —— 覆盖校验的**单一事实来源**；`chat.py` 的 `/chat` 与 `/chat/stream` 加 `_validate_model_override()`，非法 `model` → **400 fail-fast** | `test_model_override_validation.py` **10 例** + `test_llm_bind_tools.py` **10 例**回归 |

- **本批唯一行为变更**：对「不传 `model`」的现有前端**零影响**（现有前端从不传）
- 上下文绑定层 `proxy.set_request_model` 的**宽容静默**语义保持不动（既有测试锁定）
- 坑：既有测试用 `monkeypatch.setattr(proxy_mod, "OLLAMA_ENABLED", False)` 锁定语义，把依赖从**模块全局提升为函数参数**（`validate_override_model(model, *, ollama_enabled=None)`）后，既有 monkeypatch 路径继续有效

### 2.6 P1b：探测服务（三片）

| 提交 | 内容 | 测试 |
|---|---|---|
| `a8c21f4` | `registry_store.refresh_loop()`（15s 轮询，与 `sys_config.refresh_loop` 同构）+ `app/server.py` startup 钩子 —— DB 覆盖层**真正接线** | `test_llm_registry_store.py` **5 例** |
| `153b475` | `tools/url_guard.py` 增 `allow_private` 关键字（默认 `False` → 与改造前逐字一致）；`services/provider_probe.py`（新增）四级探测 L0–L3 | `test_provider_probe.py` **18 例** + `test_url_guard.py` **19 例**回归 |
| `18bccd2` | `routes/sys_providers.py`：两个探测端点 `POST /sys/providers/{id}/verify` 与 `POST /sys/providers/verify-draft`，落实 B.6 四道限制 | `test_sys_providers_probe_api.py` **14 例** |

**P1b 实施时新定的三条决策（原设计未写）：**

1. **探测不经 proxy** —— L2/L3 按 driver 用裸 langchain 客户端构建，**结构性**满足 B.4「探测排除在用量与预算统计之外」，无需侵入 `token_tracker`（那正是并发会话在改的文件）。用 **AST 断言 import 列表**守住这条。
2. **私网放行比 B.6 更严** —— 只放开 IP 网段、不放开协议；云元数据地址（`169.254.169.254` / `fd00:ec2::254`）**即便放行也始终拦截**。
3. **只有 L0 失败短路** —— L1 的 404/401 一律降级继续跑 L2，避免「测试不通过但能用」毁掉按钮可信度。

### 2.7 P2 数据源（两个只读端点）

| 提交 | 内容 | 测试 |
|---|---|---|
| `e76bb2a` | `GET /sys/providers` 只读清单 + `RegistrySnapshot.credential_meta`（`fingerprint` / `last4` / `rotatedAt` / `rotatedBy`）—— 补上 tab② 需要但 SQL 查出后**被丢弃**的凭据状态 | `test_sys_providers_list_api.py` **6 例** |
| `e608484` | `GET /sys/model-roles` 角色绑定视图（tab①⑤ 数据源），字段对齐 §5.4 `RoleBinding` | `test_sys_model_roles_api.py` **9 例** |

**三条新定决策：**

1. **展示元数据不进 `ProviderCredentials`** —— 那是出站调用热路径数据类；另立 `credential_meta`，且与「能否解密」**解耦**（解密失败仍要看到「已落库但运行时不可用」）。
2. **fail-open 兜底不是空列表** —— DB 未就绪时返回代码层内置厂商，响应加 `source`（`db` / `builtin`）字段让前端能区分。空列表会把「库没就绪」伪装成「配置被删光」。
3. **分支依据是 `loaded` 而非 `items` 是否为空** —— `loaded=true` + 空表必须**如返回空**（真·没配 ≠ 故障）。

**角色视图端点修的三个一致性缺陷：**

- `source` 两处漏网：内部 `'code-default'`（契约 `'default'`）、继承态 `'inherit:main'`（契约 `'inherit'`）→ 边界收敛，不改 P0 常量
- `provider` 与 `registered` **必须同源** —— 原打算用 `model_roles.provider_of`，但它只看代码层 `AVAILABLE_MODELS` → 自建模型会显示「已注册但无所属供应商」的自相矛盾

### 2.8 跨会话交接 + 批次 A 前半

| 提交 | 内容 |
|---|---|
| `9f99d63` | `docs/coordination/2026-09-19-llm-model-config-handoff.md`（新增）：§2① `router.py` 要加的**确切 4 行**与两条坑；§2② `proxy` 的**可粘贴补丁骨架**；§2③ 提醒持有方提交时勿丢弃我的段；§5 共享资源占用登记；§6 append-only 回执区。设计文档 §15.5 同步记录三条实测结论 |
| `d8c076d` | 批次 A 前半：`frontend-admin/src/types/modelConfig.ts`（类型契约 + 纯函数）+ `modelConfig.test.ts` **28 例**。两条对 §5.4 签名的**有意偏离**已记入 §16.1 |

**验证汇总（本会话）**

| 层次 | 结果 |
|---|---|
| 后端合并回归（11 个文件） | **172 passed** 零回归 |
| 前端 `modelConfig.test.ts` | **28 passed** |
| 前端 `useSSE.test.tsx` + `LLMSwitcher.test.tsx` | **5 passed** |
| `frontend` / `frontend-admin` `tsc --noEmit` | **零错误**（两端） |

---

## 3. 已完成但**未入库**（留在工作区）

### 3.1 F 批前端整体（8 个文件）

| 落点 | 改动 |
|---|---|
| `frontend/src/api/chat.ts`、`frontend-admin/src/api/chat.ts` | `ChatRequest` 加 `model?: string` |
| `frontend/src/hooks/useSSE.ts`、admin 同名 | `runStream(…, modelOverride?)` 第三参数，含 `startStream` / `regenerate` 透传 |
| `frontend/src/store/chat.ts`、admin 同名 | `sessionModel` + `setSessionModel`（与 `sessionId` 同生命周期，**刻意不进** `resetStream`） |
| `frontend/src/components/agent/LLMSwitcher.tsx`、admin 同名 | 受控化：读全局默认（只读）、写会话态（**不调 `switchLLM`**）、会话覆盖时显示「· 本会话」后缀 + 回到全局入口。admin 端多一个 `atLeast('admin')` 可见的「设为全局默认」 |

> ⚠️ 上表 8 个文件的**当前工作区 diff 同时包含并发会话的改动**（幂等键 / 澄清卡片 / `trace_id`），因此 `git diff --stat` 显示的 300 insertions 是**双方合计**，不等于我的改动量。`git commit <pathspec>` 只能按**文件**隔离，同一文件内的他人改动**无法剥离** —— 这是路径限定提交的能力边界。

### 3.2 F 批后端门禁（1 个文件）

| 落点 | 改动 | 状态 |
|---|---|---|
| `backend/app/api/routes/llm.py` | `POST /llm/switch` 叠加 `require_admin_user` 依赖，回收非 admin 的全局切换权 | 已落码、`py_compile` 通过、**未提交** |

**为什么不单独提交**：`SENSITIVE_API_GUARD_MODE` 默认 **enforce**，门禁单独上线会让仍在调 `switchLLM` 的旧前端**直接 403** → 必须与前端受控化**原子提交**。

### 3.3 0017 迁移（1 个文件）

| 文件 | 状态 |
|---|---|
| `backend/sql/alembic/memory/versions/0017_llm_providers.py` | **未跟踪**（`??`）。`down_revision` 指向并发会话的 `0016_price_governance`，而 **0014 / 0015 / 0016 三份迁移当前也全部未跟踪** → 单独提交会形成断链 |

---

## 4. 未完成清单

### 4.1 被阻塞（卡在同一处并发冲突）

| # | 未完成项 | 阻塞原因 | 解锁条件 |
|---|---|---|---|
| 1 | **四个端点注册**：`GET /sys/providers`、`GET /sys/model-roles`、两个探测端点 | `backend/app/api/router.py` 被并发会话持有未提交改动（含 `include_router(budgets / model_prices / idempotency)`，而**这三个模块文件本身未提交**）→ 提交 router.py 会让主干启动即 `ImportError`。且脏改动**全部属于对方**，我的注册行必须紧邻它们 → **无法拆 hunk**；强行部分提交后，对方下次提交 router.py 会**静默删掉我的行** | `router.py` 落定（对方提交或还原）后补 4 行 |
| 2 | **P1a-2 凭据链路闭合** ✅ **已实施待入库**（见 §9） | `proxy.py` 含双方改动（我的在 200–283 行，对方的 budget/reserve 在 340+ 行），且引用的符号在未提交的 `budget.py` / `pricing.py` 中 → 单独提交会让**运行时**在预算路径抛 `ImportError` | 对方 `budget` 线落定 |
| 3 | **F 批前端收口** | 8 个前端文件与并发会话的未提交改动**同文件** | 对方前端线落定 |
| 4 | **0017 迁移提交** | `down_revision` 依赖未提交的 0016 | 0014/0015/0016 入库 |
| 5 | **`vllm` 分发分支修复** ✅ **已实施待入库**（见 §9） | 与 #2 同一文件同一函数（`proxy._build_llm_for`） | 同 #2 |

### 4.2 未开工

| # | 未完成项 | 说明 |
|---|---|---|
| 6 | **管理端页面**（tab①–⑤） | ⛔ 受 **B.8 硬闸门**约束：P1a-2 未完成前开工 = 交付「配了不生效」的页面 |
| 7 | **P2 剩余端点** | #2 写端点（供应商创建/修改）、#4 写端点（凭据轮换）、#6 配置历史读取、#7 配置漂移（drift） |
| 8 | **「模型选型进 DB」的数据通道** | 见 §5 结论 3 —— 只有解析器、没有数据通道，且即便接线也**不会即时生效** |
| 9 | **`provider_credentials_history` 表** | 实际**不存在**（0017 只建 3 张表）。tab④ 契约的 `object` 联合里也没有守卫开关这类 → 契约与数据源错位 |
| 10 | **探测结果持久化**（`lastProbe`） | 当前恒 `null`；`GET /sys/providers` 的 `lastProbe` 字段因此无数据 |
| 11 | **批次 A 后半**：`frontend-admin/src/api/modelConfig.ts` | 刻意延后 —— 四个端点未注册生效前写，对着 404 路径写死会分不清谁错 |

---

## 5. 本轮查实的三条关键结论（重要）

### 结论 1（最严重）：BYOK 至今在线上**不生效**

三行代码的证据链：

| 事实 | 位置 |
|---|---|
| 线上聊天用的 `get_llm()` **来自 proxy，不是 factory** | `backend/infra/llm/__init__.py:17` |
| `factory` **已经**在调用时解析凭据并传给 provider | `backend/infra/llm/factory.py:128` |
| 但 `proxy._build_llm_for` 自己的分发表**不传凭据** | `backend/infra/llm/proxy.py:208–240` |

**后果**：管理端 / DB 里配好的供应商与密钥，在真实问答中**被忽略、一律回落 `.env`**。即 P1a-1 + P1b（自建供应商 + 密钥托管）目前处于**「能配、能测、不能用」**状态。

**这正是 B.8 硬闸门要防的「配了不生效」，但发生在后端而非页面** —— 所以即使先把管理端页面做出来也**不会暴露它**。这是本轮否掉「先做管理端」的直接依据。

### 结论 2（同型第二例缺陷）：`proxy` 分发缺 `vllm` 分支

`backend/infra/llm/models.py:136` 有 `Qwen/Qwen3-32B-AWQ`（`provider=vllm`）供用户选择，但 proxy 分发无该分支 → 落到 `ChatOllama` 兜底，报一个与真因无关的 Ollama 错误。与 2026-09-17 修过的 `qwen_tp` **完全同类**（手写分发表与 factory 双维护）。

### 结论 3：「角色改值即时生效」的 blast radius 远超原估

- `model_roles.inject_overrides()` **零调用点** —— `sys_config._fetch_overrides` 的 SQL 是 `WHERE key = ANY(:keys)` 且 `keys = list(_SWITCHES)`，模型角色不在那张表里；`sys_config.py` 注释也明说模型角色不登记在开关表。
- **更关键**：`config/llm.py` 的 8 个角色常量（`LLM_MODEL = _literal_model("main")`）与 `config/rag.py` 的 2 个，全部是**模块级赋值 → 导入时冻结**。DB 覆盖在启动后才读到 → **只影响下次启动**。
- 主设计 §6.1 对该对象的承诺是「本实例即时，其他实例 ≤1 TTL」→ **该承诺当前不成立**。
- 要做到「本实例即时」，须把 **8 个消费方文件**从「读常量」改为「调用时 `resolve_name(role)`」。

**处理决定**：**没有接**这条线。接一个「看起来支持 DB 覆盖、实际要重启」的通道比不接**更危险** —— 管理员会以为改完就生效。已记成缺口 + 单独立项。建议只对 `main` / `fallback`（`proxy.py` + `factory.py`）做调用时解析，其余角色**如实标注「重启生效」**。

### 附：三条「拒绝交付」的判断

| 对象 | 拒绝理由 |
|---|---|
| `inject_overrides` 接线 | 半成品会误导（见结论 3） |
| `#6` 配置历史端点 | `provider_credentials_history` 表不存在；`sys_config_history` 只记两个守卫开关，而 tab④ 契约的 `object` 联合里**没有守卫开关这一类** |
| `#7` drift 端点 | 4 项检查中 3 项恒空（角色从不是 `db`、`lastProbe` 恒 null、索引无 embedding 模型元数据）→ 交付出来会渲染 §11 要求的「**4 项检查全部通过**」绿色卡，**那是假绿** |

---

## 6. 下一步与解锁路径

### 6.1 立即可做（不依赖任何人）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | **批次 A 后半**：`api/modelConfig.ts` | 纯 API 模块 + mock 测试，是 §15 明确允许「不产生可点击 UI」的安全项 |
| P1 | 补 `frontend-admin` 端 F 批测试 | 与 web 端对齐（当前只有 web 端 5 例） |
| P2 | 文档收尾 | 修正主设计 §6.1 的「即时生效」承诺文案 |

### 6.2 需要并发会话先落定

`router.py` 一干净 → 补 4 行注册，**四个端点一次全部生效**；P1a-2 一落定 → 管理端页面才是做它的正确时机。

### 6.3 需要用户拍板（**已拍板 → 已完成**）

**是否由我直接改 `proxy.py` 的 `_build_llm_for`？** → 用户回「改」，已实施，见 **§9**。

- **改的收益**：凭据链路 + `vllm` 分支这两条一起收掉（结论 1 + 结论 2）✅ 已达成。
- **风险**：同一文件并发写有 lost-update 风险；实际落点（200–283 行）**落在双方改动的空白区**（我的原在 37–93，对方的在 340+），未发生覆盖，已 `git diff` 回读确认。
- **提交**：仍不提交（理由见 §9.5）—— **不是因为改不了，而是因为此刻提交没有生产收益**。

---

## 7. 风险与注意事项登记

| 类别 | 内容 |
|---|---|
| **部署** | 所有改动**未部署**。未重启服务、未执行迁移、未占用端口。线上行为不变 |
| **权限现状** | ⚠️ editor **目前仍能**切换全局模型（门禁代码已落但未提交，且未部署） |
| **提交纪律** | 本会话全部采用**路径限定提交**，每次提交前后核对暂存区，反向核验未裹入他人改动 |
| **已知陷阱（本轮新发现）** | 「部分提交会被对方下次提交静默撤销」—— 同一文件内若我的行紧邻对方的引用未提交模块的行，则既不能单独提交、也不能部分提交。此为路径限定提交的**能力边界**（已沉淀进 `multi-session-coordination` skill） |
| **自我纠错记录** | 本轮曾对 `registry_store.py` 同消息并发发两个 Edit，前者被后者**静默覆盖**（两个都报 success），且 **`py_compile` 通过**（丢的是字段定义，语法仍合法），**跑测试才炸**。已改为同文件串行编辑 + `git diff` 回读确认 |

---

## 8. 附：相关文档索引

| 文档 | 用途 |
|---|---|
| `docs/model-config-governance-design.md` | 主设计（§7 权限、附录 A/B/C、B.4 四级探测、B.6 四道限制、B.8 硬闸门、B.9 校验契约） |
| `docs/model-config-admin-ui-design.md` | 实现级 UI 设计（17 节）+ §15.1–**§15.6** 实施状态 |
| `docs/coordination/2026-09-19-llm-model-config-handoff.md` | 跨会话交接单（含确切 4 行注册、可粘贴补丁骨架、回执区） |
| `docs/model-config-governance-progress-report-2026-09-19.md` | **本报告** |

---

## 9. 追加：P1a-2 收口（2026-09-19 15:25）

用户拍板「改」后，把 §5 结论 1、结论 2 两条一起收掉。**改动仅在工作区、未提交。**

### 9.1 落点：`backend/infra/llm/proxy.py`（1 个文件）

| 改动 | 内容 |
|---|---|
| `_build_llm_for` | **调用时** `resolve_credentials(provider, model_name=…)`，并显式传入每个 `build_xxx(model_name, credentials)` —— 即 BYOK 链路闭合 |
| `_build_llm_for` | 补 **`vllm` 分支**（此前缺失 → 落到 Ollama 兜底报无关错误） |
| `_get_provider_for` | 由「遍历代码层 `AVAILABLE_MODELS`」改为委托 `models.resolve_provider`（与 `factory` 同源） |
| ollama 兜底 | 内联 `ChatOllama(...)` → 复用 `providers/ollama.build_ollama`（与 factory 同源） |
| `_resolve_credentials_or_none`（新） | 凭据解析异常 → warning + 返回 `None`（= 与改造前逐位一致），**不在聊天热路径新增崩溃点** |

### 9.2 一条**独立于凭据**的正确性缺陷（顺手修掉）

`_get_provider_for` 旧实现只遍历代码层 `AVAILABLE_MODELS` → DB 覆盖层登记的自建模型一律**误判成 ollama**。而同一个值还会写进 `_last_call_meta["provider"]` 供**计价链**读取 → **费用归属记到错误的 provider 上**。这不是整洁问题，是**计价正确性**问题，与凭据链路同源。统一到 `resolve_provider` 后消失。

### 9.3 测试：10 例，含两条结构性契约 + 反证验证

| 测试 | 守什么 |
|---|---|
| `test_every_provider_build_call_passes_credentials` | **AST 断言**：proxy 里每个 `build_*` 调用必须两参 —— 凭据不许再被静默丢弃 |
| `test_proxy_dispatch_covers_every_known_provider` | 分发表必须覆盖 `models.PROVIDERS` 每个 provider —— 防第三例 `qwen_tp`/`vllm` 型漏项 |
| 其余 8 例 | 凭据透传恒等 / 按解析出的 provider 取凭据 / 解析异常回落 `None` / `vllm` 分支不走 ollama / ollama 用共享构建器 / `_get_provider_for` 委托与动态层可见性 |

**反证验证**：临时把 `build_vllm(model_name, credentials)` 改回单参 → 断言**变红** → 按 sha256 还原（`dc2c429d2844` 前后一致）。即守卫**确实**能抓到，不是摆设。

**合并回归 199 passed** 零失败。

### 9.4 为什么零行为变化（已实测，这是可以安全落码的关键）

DB 覆盖层为空时，`resolve_credentials` 对 `deepseek` / `qwen` / `vllm` / `ollama` / `siliconflow` 全部返回 `source=env`，且值与 provider 自身回落值**相同**。故本改动在 **0017 表 + P2 写端点落地前是行为等价的**。

### 9.5 为什么**不提交**（四条，第 3 条是对旧表述的更正）

| # | 约束 |
|---|---|
| 1 | `proxy.py` 同文件承载并发会话的预算改动，路径限定提交**不能剥离**同文件内的他人改动 |
| 2 | 提交后运行时引用到未提交的 `budget.release_model_reservation` / `pricing.calculate_current_cost`（已实测 `git show HEAD:` 确认缺失） |
| 3 | ⚠️ **更正**：此前写「单独提交会让主干 `import` 失败」**不准确**。这些 `from backend.infra.llm.budget import (...)` 是**函数内延迟导入**，`import backend.infra.llm.proxy` **会成功**，故障在**调用到预算预留/计价那几行**才抛 `ImportError` → 排查时容易误判成预算模块自身的问题。（交接单 §2③ 已同步更正；该表述对 `router.py` 仍成立，那里的 `include_router` 是**模块级**导入） |
| 4 | **无生产收益** —— 因 9.4 的「行为等价」，此刻提交不产生任何线上收益 → **不值得**为此承担并发写风险 |

### 9.6 对闸门与计划的影响

技术上前置已完成（§5 结论 1、2 均已修），但**落码未入库、且依赖 0017 + P2 写端点**。故：

- **B.8 闸门判定不变** —— 管理端页面开工时机仍是「`proxy.py` / `router.py` 落定 + P2 写端点可用」之后。
- **未完成清单的变化**：原 §4.1 第 2 项（P1a-2 凭据链路）与第 5 项（`vllm` 分支）→ 状态改为「**已实施待入库**」；两者仍随 `proxy.py` 的落定一并收口。
- **可立即做的**：批次 A 后半（`api/modelConfig.ts`，纯 API 模块 + mock 测试）。
