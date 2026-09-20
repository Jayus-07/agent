# 大模型配置治理 — 计划 / 已完成 / 未完成报告

> 生成时间：2026-09-19 15:20
> 范围：`docs/model-config-governance-design.md`（主设计）+ `docs/model-config-admin-ui-design.md`（实现级 UI 设计）两条线
> 工作时区：`D:/Program Files/workplace/agent`
> 说明：本报告只覆盖「大模型配置治理」这一条工作线。工作区内另有并发会话在推进**另一条完全独立的功能线**（幂等键 / 澄清卡片 / trace_id / 预算计价 / 客户服务子图加固），本报告在「阻塞项」章节中标注了与之的交集，但不评价其内容。
>
> **实施更新说明（2026-09-19 16:50）**：本文前半部分是 15:20 的阶段快照，
> 其中「未开工 / 被阻塞」结论已被本轮实现推进覆盖；请以文末 §10 为当前状态。

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

---

## 10. 实施更新：管理端模型配置闭环（2026-09-19 23:52）

本轮已按本报告指出的阻塞项继续实现，目标是「管理端配置能落库、能生效、能验证、能追溯」，不再停留在只读契约层。

### 10.1 已落地

| 层次 | 实现 | 关键行为 |
|---|---|---|
| 数据库 | 新增迁移 `0018_model_config_governance.py` | 角色绑定、统一配置历史、探测结果字段；历史只存展示值/指纹，绝不存明文或 Fernet 密文；并为 7 个内置 provider 建立统一配置行 |
| 后端写 API | `services/model_config.py` + `routes/model_config.py` | `PUT /sys/model-roles/{role}`、`PUT /sys/providers/{id}`、历史查询/回滚、漂移检查；写操作要求 `Idempotency-Key`，管理员写、editor 只读；事务在提前退出 async session 前显式提交 |
| 路由接线 | `app/api/router.py` | 角色、供应商、探测、写入、history、drift 全部接入主 `api_router` |
| 密钥安全 | Fernet 加密 + 指纹/末四位 | API Key 不回显、不进历史值、不进入普通响应；密钥历史明确不可回滚 |
| 运行时 | `registry_store` → `model_roles` / `models` / `credentials`；主问答/兜底及非索引链路运行时解析 | 后台刷新 DB 覆盖；`main`、`fallback`、`doc`、`tool_selector`、`ocr`、`rerank`、`eval_gen` 在刷新后由调用点读取新模型；`embedding` 只在新建/重建索引实例时读取；供应商 URL/密钥变更会清空 proxy/factory 客户端缓存；相同快照不会重复清缓存 |
| 探测闭环 | `sys_providers` + `last_probe_*` | 已保存 provider 复测结果持久化，列表不再恒为 `lastProbe=null` |
| 管理端页面 | `/settings/models` | 角色绑定、供应商与密钥、价格、历史、漂移 5 个 tab；editor 看到角色/价格/历史/漂移只读视图，供应商 tab 仅 admin；通用 LLM 与 `eval_gen` 从目录选择，OCR/embedding/rerank 支持专用模型名输入；旧 `/cost-governance/prices` 重定向 |
| 前端 API | `frontend-admin/src/api/modelConfig.ts` + `app/api/[...path]/route.ts` | 单域请求模块使用统一 mutation 幂等键；BFF 已透传 `Idempotency-Key`，治理写入可真正到达后端 |

### 10.2 当前验证结果

- 本地记忆库已从 `0016` 升级到 `0018`；5 张模型治理表均可读，内置 7 个 provider 可返回。
- 后端模型配置焦点回归：`57 passed`（含写 API 6、模型角色运行时 7、数据库提交回归 1，以及 provider/role/registry 相关测试）；BFF 代理转发回归：`4 passed`。
- 管理端全量 `28` 个测试文件、`283 passed`；`npx tsc --noEmit` 与 `npm run build` 均通过，并生成 `/settings/models`。
- 非索引角色调用点与相邻文档处理/工具选择/OCR/评测/重排回归：`84 passed`；后端相关文件 `py_compile` 通过。
- 实际 TestClient 读取验证：`/sys/providers` 返回 7 个内置 provider，`/sys/model-roles` 返回 8 个角色，history/drift 均返回裸 dict；响应中未发现 `api_key` 或 `key_cipher`。
- 本地真实链路已验证：迁移到 `0018 (head)`，重建并重启 backend；管理端保存 `fallback=qwen3.7-plus` 后，数据库有 `llm_model_role_bindings` 记录，页面显示「DB 覆盖 / 可用」，严重漂移从 1 降为 0。

### 10.3 尚需上线前处理

1. 本地 backend 已重建并重启，生产环境仍需按发布流程执行 `alembic upgrade head`，发布包含 BFF `Idempotency-Key` 透传与后端显式提交的版本后再灰度。
2. 漂移检查对「索引模型元数据」会明确返回 `info`，因为仓库尚无统一索引元数据表；切换 embedding 后仍需人工确认并全量重建索引，不能把此项误读为自动通过。
3. 生产密钥写入前必须配置 `SECRETS_ENCRYPTION_KEY`；缺失时接口会拒绝写入，不会降级保存明文。
4. 当前已接通调用点运行时解析的是 `main`、`fallback`、`doc`、`tool_selector`、`ocr`、`rerank`、`eval_gen`；`embedding` 也已接入专项绑定。Embedding wrapper 与 RAG 独立服务会在注册表刷新后热切换出站客户端，但旧索引的向量空间不会自动转换，切换 embedding 模型后仍必须全量重建索引并验证。
5. 本地 Docker 栈已配置并持久化 `SECRETS_ENCRYPTION_KEY`，应用实际使用的 `agent_memory` 已执行迁移 `0021`；生产发布必须把该主密钥纳入密钥托管与备份清单，不能临时生成后丢失。

### 10.7 评测生成模型切换修复（2026-09-20）

- `eval_gen` 不再默认 `qwen2.5:3b`，独立环境变量改为 `EVAL_GEN_MODEL`，当前为空表示未配置。
- 管理端角色绑定改为从已登记模型目录选择；已禁用的本地 Ollama 模型会显示具体原因，不能保存为可用配置。
- 评测答案生成与 RAGAS 均通过统一角色路由读取供应商、Base URL 和托管凭据，不再把云端模型名固定送进 `ChatOllama`。
- 旧的 `eval_gen` 绑定可以在管理端选择“未配置（停用评测生成）”清除；云端评测仍需显式 `--judge` / `--ragas` opt-in。

### 10.4 供应商真实探测复核（2026-09-20）

本地管理端通过真实 `POST /api/sys/providers/{id}/verify` 逐一复测 7 个内置供应商，
并刷新页面确认 `lastProbe` 持久化结果可见。复测期间没有修改 URL、密钥或模型配置，
只更新了每个 provider 的最近探测字段。

| 供应商 | 结果 | 首个阻断级别 | 现场结论 |
|---|---|---|---|
| `deepseek` | 未通过 | L2 | 供应商返回 HTTP 402：账户余额不足 |
| `minimax` | 未通过 | L2 | 供应商返回 HTTP 429：Token Plan 用量达到上限 |
| `ollama` | 未通过 | L0 | `localhost` 被 SSRF 防护拦截；容器内本机地址不是宿主机 Ollama |
| `qwen` | 未通过 | L2 | 供应商返回 HTTP 400：账户/访问权限被拒 |
| `qwen_tp` | **通过** | — | L0/L1/L2/L3 全部通过 |
| `siliconflow` | **通过** | — | L0/L1/L2/L3 全部通过 |
| `vllm` | 未通过 | L0 | `localhost` 被 SSRF 防护拦截；当前地址不是可达的 vLLM 服务地址 |

复测同时发现并修复两处代码问题：

1. 已存内置供应商复测原来只从 DB 模型表取模型名；内置模型实际来自“代码注册表 + DB
   动态模型”合并层，导致 L2 收到空模型名。现在复用 `_merged_models()`，并新增回归测试。
2. `qwen3.7-plus@tp` 的 `@tp` 是内部注册后缀，运行时客户端会剥除，但探测器此前直接
   把它发给 Token Plan API，导致模型不存在。探测器已与运行时保持一致，新增回归测试。

代码回归：供应商 API/列表/探测服务共 `40 passed`；真实管理端页面显示 `已验证 2/7`，
其中 `qwen_tp` 与 `siliconflow` 为“已通过”。

随后针对管理端复测发现的两个问题又完成了闭环修复：

3. provider 只有 DB Base URL 覆盖、没有托管 Key 时，凭据解析与探测路由均改为按字段合并，
   保留环境变量 API Key；列表也不再把地址覆盖误显示成“已托管”。新增凭据层、列表层、探测
   路由层回归测试。
4. 管理端“探测”栏现在保留本次 L0–L3 结果、失败级别、后端摘要和截断原文；FastAPI 422
   的 `detail` 字符串/数组会转换为可读的具体原因（如 `body.model_name：不能为空`），
   不再统一退化为“请求参数有误”或“操作失败”。

修复后通过真实管理端连续复测 `qwen_tp` 两次，均显示“探测通过”；真实失败的 DeepSeek
   在页面显示为 `L2 / HTTP 402 / Insufficient Balance`，并展开 L0–L2 明细。新增前端
探测组件与错误展示回归共 `66 passed`，后端供应商相关回归共 `52 passed`（Windows
   Proactor 仅有既存的 event-loop 清理 warning）。

### 10.5 供应商配置体验闭环（2026-09-20）

根据管理端实际使用流程，将供应商配置收敛为“预设供应商 / 自定义 API + API Key + 模型名”
的弹窗操作：

1. `/settings/models?tab=providers` 新增“新增供应商”入口，编辑与新增统一使用 Modal；
   自定义供应商最小输入为 Base URL、API Key 和模型名称，内置 Token Plan 等预设会带出地址
   和默认模型。
2. 列表增加模型列，托管密钥使用 `****末四位` 展示；编辑时密钥永不回填，留空表示保持原值，
   高级设置仍可显式清除托管 Key。
3. “测试并保存”会先执行草稿态 L0–L3 探测，只有探测通过才创建自定义供应商，并将模型写入
   `llm_models`；Key 仍通过 Fernet 加密保存，响应不携带明文。
4. 修复草稿探测的前后端字段契约：后端同时兼容 `baseUrl/modelName/apiKey/networkScope`，
   不再因前端 camelCase 被 FastAPI 误报 `body.model_name` 等参数错误。
5. 新增自定义供应商创建、模型注册、camelCase 探测和脱敏显示回归；本轮定向回归为后端
   `26 passed`、管理端相关测试 `38 passed`，`npx tsc --noEmit`、`py_compile`、`git diff --check`
   均通过。
6. 测试连接期间，列表行和编辑弹窗会实时显示“正在测试 · 已耗时 X.X 秒”；探测返回后显示
   总耗时，并保留 L0–L3 各阶段耗时。网络异常也会在前端错误信息中附带本次耗时；该交互新增
   回归覆盖，管理端全量测试当前为 `294 passed`，`npx tsc --noEmit` 与 `npm run build` 均通过。
7. 测试流程改为“快速测试 / 完整测试”两档：快速测试默认只执行 L0–L2，L2 通过即可判定
   模型可调用，并将 L3 标为“已跳过（不影响可用性）”；完整测试通过 `mode=full` 显式检查
   流式 usage。前端快速模式请求超时上限为 45 秒，完整模式为 60 秒，避免 L3 的慢响应把
   正常模型误报为整体不可达。
8. 针对推理模型快速测试继续收敛耗时：快速 L2 改为只等待首个流式分片并主动关闭流；
   SiliconFlow 的 Qwen3 快速探测额外发送 `enable_thinking=false`、`max_tokens=1`，不等待
   完整思考链。完整测试仍保留原始非流式 L2 + 流式 usage 检查，避免把能力检查与可用性检查
   混为一谈。

### 10.6 向量 / 重排专项模型配置闭环（2026-09-20）

针对“用户只输入 API Key、向量模型名、重排模型名，后续仍可接入新模型服务”的需求，新增独立
专项协议适配层，不把 embedding / rerank 错当成通用 Chat 模型：

1. 新增迁移 `0019_specialized_model_bindings.py` 与 `llm_specialized_model_bindings` 表，保存
   `role/provider/adapter/model/base_url/options/last_probe_*`；Key 仍复用 `llm_provider_credentials`
   的 Fernet 加密，不进入专项绑定表。
2. 后端适配器白名单当前包含 `dashscope_embedding`、`openai_embedding`、`dashscope_rerank`、
   `jina_rerank`。后续增加服务只需新增适配器，不需要让前端理解请求体、路径或响应格式。
3. 新增 `GET /sys/specialized-models` 与 `POST /sys/specialized-models/test-and-save`：先按角色
   发最小真实请求，两个测试全部通过后才落库；每项返回状态码、后端摘要、截断后的安全错误和
   `elapsedMs`，失败不会保存半成品。
4. 管理端“角色绑定”页新增专项模型卡片和弹窗。Key 输入不回显，只显示掩码/指纹；编辑时留空
   表示复用原 Key。向量模型变化明确提示必须全量重建索引。
5. 本地浏览器真实验证结果：

   | 角色 | 模型 | 适配器 | 结果 |
   |---|---|---|---|
   | embedding | `qwen3.7-text-embedding` | `dashscope_embedding` | 通过，390ms；实际返回 1024 维 |
   | rerank | `qwen3.7-text-rerank` | `dashscope_rerank` | 通过，299ms；运行时实际返回排序分数 |

   页面已显示两个角色为“DB 覆盖 / 已配置”，并显示专项测试耗时。RAG 服务日志进一步确认
   实际初始化模型为 `qwen3.7-text-embedding`，独立运行时重排实例为 `qwen3.7-text-rerank`。
6. 实测过程中发现并修复两个上线阻塞：应用 Docker 容器未执行 `0019` 会导致专项页加载失败；
   未配置 `SECRETS_ENCRYPTION_KEY` 会在测试通过后保存阶段返回 503。现在缺少主密钥仍会拒绝写入，
   但前端不会把它伪装成模型不可达，后端会记录安全的异常类型，便于运维排查。
7. 本轮回归：专项配置 / 探测 / 运行时共 `20 passed`；Embedding 与 Rerank 真实远端调用均已
   验证。用户此前在对话中粘贴过真实 Key，建议上线前主动轮换该 Key；仓库文档、日志和 API
   响应均不保存或回显 Key 明文。

### 10.8 模型目录按用途拆分（2026-09-20）

为避免把文本、向量、重排模型混在同一个下拉框里，新增 `llm_models.model_kind` 作为模型目录
事实源，取值只有 `chat`、`embedding`、`rerank`；迁移 `0020_model_kind_catalog.py` 会把旧数据
安全回填为 `chat`。供应商仍保存协议、Base URL 与凭据，同一供应商可以登记多个不同用途的模型。

本轮实现：

1. 新增供应商弹窗明确选择“文本模型 / 向量模型 / 重排模型”，列表中每个模型名旁显示用途标签；
   新增模型弹窗复用已有供应商的 URL 和加密 Key，后端按用途测试成功后才写入目录。
2. 新增 `POST /sys/providers/{id}/models` 与 `GET /sys/providers/{id}/models?modelKind=...`；
   `embedding` 走 OpenAI 兼容 `/embeddings`，`rerank` 按地址选择 DashScope 原生重排或 Jina
   兼容 `/rerank`，文本模型继续走 Chat 探测。协议差异仍收敛在后端适配层，前端只选择用途。
3. 角色切换下拉框只展示匹配用途的模型：`main/doc/tool_selector/fallback/eval_gen` 只能选
   文本模型，`embedding` / `rerank` 的已登记模型会按对应类型提示；后端保存时再次校验，错误
   类型返回可操作原因而不是保存后运行时失败。
4. 编辑已有供应商时，如果修改 Base URL、模型名、模型用途或 Key，会重新执行快速测试；仅修改
   展示名、计费或网络范围则不重复发起模型调用。已有模型不能直接改用途，需通过“新增模型”建立
   新目录条目，避免同名模型的运行协议和角色语义漂移。

验证：模型类型/探测/配置 API 定向回归 `68 passed`（Windows Proactor 有既存 event-loop 清理
warning）；专项 provider probe `23 passed`；管理端模型配置相关 Vitest `41 passed`，
`npx tsc --noEmit` 和 Python 编译检查通过。上线前仍需在部署环境执行 `alembic upgrade head`
并重新加载管理端。

### 10.9 统一供应商模型目录与角色选择（2026-09-20）

专项模型不再只存在于 `llm_specialized_model_bindings`：测试并保存成功后，会同步登记到统一的
`llm_models` 目录，并写入对应的 `model_kind`。历史专项绑定由 `0021_catalog_specialized_models.py`
回填，因此供应商页面可以完整展示同一供应商下的文本、向量、重排模型。

1. 供应商列表按模型目录完整展示所有模型，并显示“已配置 N 个模型”和每个用途标签；角色页不再
   放置独立的向量/重排编辑卡，专项模型与 OCR 统一通过供应商页的“新增供应商 / 新增模型”登记。
   历史专项供应商通过供应商页上方兼容卡编辑；新登记的向量 / 重排供应商仍各自使用独立 Key。
2. 角色绑定编辑统一改为下拉选择，不再允许自由输入模型名；当前失效值会作为禁用项保留，必须
   重新选择已登记且用途匹配的模型。
3. 后端角色写入同样拒绝未登记模型和用途不匹配模型，避免绕过前端写入 `qwen3.7-text-reran`
   这类拼写错误。

### 10.10 专项模型供应商隔离与 OCR 目录化（2026-09-20）

本轮按“跟文本模型一样添加”的管理体验收敛专项配置：

1. 向量、重排、OCR 均从供应商页新增；每条供应商记录拥有独立加密凭据。需要不同 Key 时分别
   新增供应商，不再由角色页专项卡把两个模型强制放在同一个 `provider + apiKey` 草稿中。
2. 重排探测按已登记 Base URL 选择协议：DashScope 使用 `dashscope_rerank`，SiliconFlow 使用
   `jina_rerank`；向量使用 OpenAI 兼容 Embedding 端点。前端只选择“模型用途”，不感知请求路径。
3. 注册表刷新时按 `llm_model_role_bindings` 当前选择的模型目录派生 embedding/rerank 运行绑定，
   provider、Base URL 和凭据各自解析；旧 `llm_specialized_model_bindings` 仅作兼容回退。
4. OCR 角色选择已登记的文本模型后，OCR 运行时优先从该模型所属供应商读取 DB URL/Key/模型名；
   未配置 DB 角色时继续兼容 `RAG_OCR_PROVIDER` 与旧 DashScope env 配置。
5. 角色接口现在对 OCR / embedding / rerank 同样返回“未注册、用途不匹配、未配置 API Key”等
   可操作原因，角色页只能选择供应商目录中已测试保存的对应分类模型。

### 10.11 历史专项供应商编辑与重复模型迁移修复（2026-09-20）

真实管理端操作又暴露了两个上线阻塞：旧的“阿里云百炼专项”记录被前端当作只读，
以及新供应商测试通过后，保存阶段因同名模型已存在而返回 409，页面只显示“请求已处理或正在处理”。
现已完成以下修复：

1. 供应商页统一展示所有模型用途，不再渲染独立的“专项模型”卡；同一供应商的文本、向量、重排、
   视觉和语音模型在同一行按用途标注，模型仍可逐个测试和追加。Key 仍为空输入复用、界面只显示末四位和指纹。
2. 新供应商测试通过后，如果同名模型仅属于历史 `driver=specialized` 供应商，允许一次明确迁移：
   更新 `llm_models.provider_id`，同步迁移对应专项绑定；普通供应商之间仍保持模型名全局唯一，防止
   角色按模型名解析到错误 Key。
3. 模型配置写入错误通过安全文案进入统一错误封套，前端保存弹窗会保留具体冲突原因，而不是只显示
   幂等冲突兜底文案。
4. 历史 `driver=specialized` 供应商已恢复通用测试、追加模型和编辑动作；旧专项 API 仅保留兼容，
   管理端不再调用。模型目录新增视觉、语音用途，并由迁移 `0022` 放开数据库约束。

### 10.12 数据库配置成为云模型唯一运行时来源（2026-09-20）

针对“已在管理端配置后，重启仍应使用数据库配置，删除 env 模型配置”的上线要求，已完成
迁移、代码收口和容器验证：

1. 新增 `backend/scripts/migrate_model_env_to_db.py`，先将现有活动 env 中的供应商 Base URL、
   API Key 和角色选择迁移到 DB；只在 DB 对应字段为空时补写，不覆盖管理端已有配置。API Key
   通过现有 Fernet 凭据表加密保存，迁移输出不打印明文。
2. 云端模型名、供应商 URL、API Key、OCR/Embedding/Rerank 运行绑定和评测模型均改为读取 DB
   注册表；没有 DB 覆盖时只返回代码默认值并保持“未配置”状态，不再读取旧 env 作为运行时兜底。
   本地 Ollama 地址仍属于本机基础设施默认值，不属于云端凭据回退。
3. 根 `.env` 中活动的模型/供应商配置已移除，`docker-compose.yml` 也不再向 app、RAG、worker、
   MCP 注入这些模型 env。非模型基础设施配置保留；历史 `.env.bak-*` 仅作为备份保留，不参与
   运行时读取。
4. 本地 DB 当前已验证：8 个供应商实例、2 个自建模型、6 份加密凭据、6 个角色覆盖；角色包括
   主模型、文档、评测、兜底、向量和重排，重排拼写错误已修复为已登记模型名。
5. 重建后的容器日志确认 `LLMRegistry DB 覆盖层已注入`，app 与 RAG `/health`、`/readyz`
   均返回成功，容器内目标模型/API Key env 名称为 0 个。百炼调用日志中的 `Arrearage /
   overdue-payment` 是账户侧余额/权限拒绝，属于供应商外部状态，不是 env 配置回退。
6. 最终回归：模型配置治理及相邻 API 定向测试 `149 passed`；Python 编译、`git diff --check`
   和活动模型 env 检查均通过。管理端后续新增或切换供应商、模型、角色，应以 DB 管理页为准，
   修改后由注册表刷新/服务重启加载，不再编辑 env。
