# 跨会话协同说明 · 模型配置治理（LLM 模型 / 供应商 / 凭据）交接

> **写给**：正在改 `backend/infra/llm/proxy.py` 的预算计价段、并持有
> `budget.py` / `quota.py` / `pricing.py` / `routes/budgets.py` / `routes/model_prices.py` /
> `routes/idempotency.py` / `app/api/router.py` 未提交改动的会话
> **出自**：负责「LLM 模型配置治理」的会话（`model_roles` / 供应商注册表 / 凭据 / 探测 / 管理端只读 API）
> **建立**：2026-09-19 15:10 ｜ **最后更新**：2026-09-19 15:10
> **深度背景**：`docs/model-config-governance-design.md`（主设计，附录 B 是决策台账）+
> `docs/model-config-admin-ui-design.md`（UI 与实施状态 §15）

---

## 0. 怎么用这份文档

1. **只读 §2** —— 那里是**只有你能做的三件事**，其余章节是背景。
2. 有事要跟我说：**在文末 §6「回执区」追加一段**（append-only，不改我的段落），标注时间与你的会话标识。
3. 动共享文件前看 §5「共享资源占用登记」。

---

## 1. 范围边界

| | 内容 |
|---|---|
| **我负责（已提交，属我）** | `backend/config/model_roles.py`、`backend/config/llm.py`、`backend/config/rag.py`、`backend/config/startup.py`、`backend/services/sys_config.py`、`backend/shared/crypto.py`、`backend/competitor/crypto.py`、`backend/infra/llm/credentials.py`、`backend/infra/llm/models.py`、`backend/infra/llm/factory.py`、`backend/infra/llm/providers/*.py`(7)、`backend/infra/llm/registry_store.py`、`backend/tools/url_guard.py`、`backend/services/provider_probe.py`、`backend/app/api/routes/sys_providers.py`、`backend/app/api/routes/sys_model_roles.py`、`frontend-admin/src/api/securityOps.ts`、`docs/model-config-*.md` |
| **我负责（在我工作区、未提交）** | `backend/infra/llm/proxy.py` **仅 37–93 行的 `set_request_model` 段**；`backend/app/api/routes/llm.py`（`/llm/switch` 门禁）；`backend/sql/alembic/memory/versions/0017_llm_providers.py`；`frontend{,-admin}/src/` 的 `api/chat.ts`、`hooks/useSSE.ts`、`store/chat.ts`、`components/agent/LLMSwitcher.tsx` 及 2 个测试文件 |
| **我不碰** | `backend/infra/llm/{budget,pricing,quota}.py`、`backend/app/api/routes/{budgets,model_prices,idempotency}.py`、`backend/app/api/router.py`、`frontend-admin/src/app/cost-governance/**`、任何 `cs/**`（客服域） |

**交错文件只有一个：`backend/infra/llm/proxy.py`。** 物理位置不重叠 —— 我的改动在
37–93 行（`set_request_model`），你的在 340 / 605 / 876 行往后（budget 预留 · 计费 · `_LLMProxy`）。
中间 **208–240 行是双方空白区**（见 §2②）。

---

## 2. 需要你动作的三件事

### ① `router.py`：我的 4 个端点**差一行注册**，目前全部 404

**现状**：我已交付并提交 4 个只读/探测端点，但都**没有注册**：

| 端点 | 文件（已提交） | 提交 |
|---|---|---|
| `GET  /sys/providers` | `routes/sys_providers.py` | `e76bb2a` |
| `POST /sys/providers/{id}/verify` | 同上 | `18bccd2` |
| `POST /sys/providers/verify-draft` | 同上 | `18bccd2` |
| `GET  /sys/model-roles` | `routes/sys_model_roles.py` | `e608484` |

**为什么不自己提交**（我实测过，不是偷懒）：`app/api/router.py` 当前 diff 是**你的**三处改动 ——
import 元组里加 `budgets, model_prices, idempotency`、include 列表里加三行、末尾补换行。
而这三个模块文件**本身还没提交**（untracked）。于是有两个独立的坑：

1. **整文件提交** → 主干 `import` 失败（引用不存在的模块）。
2. **只提交我这几行**（`git add -p` 或手工 patch 只入暂存区）→ 更隐蔽：我的行进 HEAD，但你的工作区副本**不含**我的行，
   于是你**下一次提交 `router.py` 会把我的行静默删掉**（文件级提交取工作区内容）—— 端点又变 404，且没有任何报错。

所以正确的顺序是**你那边先落定**，或者你明确说一声「你加吧」，我就地加上。

**确切需要加的 4 行**（放到你三行旁边即可）：

```python
# import 元组内（backend.app.api.routes 下）
    sys_model_roles,
    sys_providers,

# include_router 区块内
api_router.include_router(sys_model_roles.router)  # 模型角色绑定视图（管理端 tab①⑤ 数据源）
api_router.include_router(sys_providers.router)    # LLM 供应商清单 + 连通性探测（tab② + BYOK）
```

> 注册后请顺带跑 `pytest backend/tests/api/test_sys_providers_probe_api.py backend/tests/api/test_sys_providers_list_api.py backend/tests/api/test_sys_model_roles_api.py`（29 例，现全绿）。

### ② `proxy.py`：**用户自建供应商 / 密钥在聊天路径上至今不生效** ← 这是关键路径

这是我这轮查出、且必须由你（持有 `proxy.py`）决定的事。

**证据链**（三行代码，可自行复核）：

| 事实 | 位置 |
|---|---|
| 线上聊天用的 `get_llm()` **来自 proxy，不是 factory** | `infra/llm/__init__.py:17`（`from ...proxy import _LLMProxy, get_llm, llm`） |
| factory **已经**在调用时解析凭据并传给 provider | `infra/llm/factory.py:128`（`credentials = resolve_credentials(provider, model_name=...)`） |
| 但 proxy 自己的分发**不传凭据**、且用 `else` 落到 ChatOllama | `infra/llm/proxy.py:208-240`（`build_deepseek(model_name)` … 无 `credentials=` 形参） |

**后果**：管理端/DB 里配好的供应商实例与密钥（P1a-1 + P1b 的全部产物）**在真实问答里被忽略**，
一律回落 `.env`。这正是 B.8 硬闸门要防的「**配了不生效**」—— 只不过它发生在后端，不在页面上。

**附带缺陷（同一函数，同类）**：`_build_llm_for` 的分支**缺 `vllm`**。
`models.py:75` 注册了 `vllm`，`models.py:136` 有一条 `Qwen/Qwen3-32B-AWQ`（provider=`vllm`）在 `AVAILABLE_MODELS` 里
（即用户可选），选中后会落到末尾的 `ChatOllama(model="Qwen/Qwen3-32B-AWQ")` → 报一个与真因无关的 Ollama 连接错误。
这与 2026-09-17 修过的 `qwen_tp` 缺失分支是**同一个 bug 类**（当时注释还留在 220–225 行）。

**建议改法**（任一，供你选）：

```python
# 方案 a（推荐）：proxy 不再自己维护第二张分发表，复用 factory 的
#   需要 factory 暴露一个"不缓存、直接构建"的入口（现有 128 行那段逻辑抽成函数即可）
# 方案 b（最小改动）：给现分发表补 credentials + vllm 分支
def _build_llm_for(model_name: str) -> BaseChatModel:
    provider = _get_provider_for(model_name)
    from backend.infra.llm.credentials import resolve_credentials
    cred = resolve_credentials(provider, model_name=model_name)  # 自建 provider 需 try/except
    if provider == "deepseek":
        from backend.infra.llm.providers.deepseek import build_deepseek
        return build_deepseek(model_name, cred)
    # …其余分支同样补 cred…
    if provider == "vllm":
        from backend.infra.llm.providers.vllm import build_vllm
        return build_vllm(model_name, cred)
```

**我为什么不直接改**：`proxy.py` 同时承载你的预算改动，两边并发写同一文件有 lost-update 风险
（本项目实测过：两个 Edit 都报 success，后写覆盖先写）。**你说一声我就改**（我的插入点 208–240 行在双方空白区，不碰你的段），
或者你自己带上更合适 —— 反正 `_build_llm_for` 你要不要抽公共入口，只有你知道。

### ③ 提交 `proxy.py` 时的顺序约束（只需知道，不需要你做额外动作）

`proxy.py` 单独提交会让主干 `import` 失败（它引用 `budget.release_model_reservation` 等，而 `budget.py`/`quota.py`/`pricing.py` 未提交）。
请把这几个文件**一起**提交。

**另外**：你提交 `proxy.py` 时会**连带带上我在 37–93 行的 `set_request_model` 改动**（已落码，`test_llm_bind_tools.py` 10 例 + 新测试 10 例全绿）。
那是正常的、也是我期望的 —— 不需要你处理，也不用帮我署名。只要**别丢弃**它（`git checkout -- proxy.py` 会让会话级模型校验退回旧实现）。

---

## 3. 我已交付（都可独立验证，未依赖你的任何未提交文件）

| 提交 | 内容 | 测试 |
|---|---|---|
| `41a5df9` `dba3d57` | P0：`config/model_roles.py` 8 角色注册表 + 常量收敛（零行为变化） | — |
| `3b8238f` | P1a-1：`shared/crypto.py` + `credentials.py` + 7 个 provider 改传参式 | — |
| `e2ab3ae` | F 批：`validate_override_model()` 单点 + `/chat` 边界 fail-fast | 10 例 |
| `a8c21f4` | P1b：`registry_store.refresh_loop()` + `server.py` startup 钩子 | 5 例 |
| `153b475` | P1b：`url_guard.allow_private` + `services/provider_probe.py` 四级探测 | 18 例 |
| `18bccd2` | P1b：探测端点 ×2 + 滑动窗口限流 | 14 例 |
| `e76bb2a` | P2：`GET /sys/providers` 清单 + `credential_meta` | 6 例 |
| `e608484` | P2：`GET /sys/model-roles` 角色视图 | 9 例 |
| 文档 | `docs/model-config-*.md`（§15.1–§15.4 实施状态） | — |

合并回归 172 例全绿；两端 `tsc --noEmit` 零错误。

---

## 4. 一个需要拍板的设计缺口（不是你和我的 bug，但会影响你我的文件）

**「模型选型进 DB」目前只有解析器、没有数据通道，而且即使接上也不会即时生效。**

| 事实 | 位置 |
|---|---|
| `model_roles.inject_overrides()` 的注释写着「P1 起由 `sys_config.refresh_loop` 调」 | `config/model_roles.py:155` |
| 但它**至今零调用点**；`sys_config._fetch_overrides` 的 SQL 是 `key = ANY(:keys)` 且 `keys = list(_SWITCHES)`（只有两个守卫开关） | `services/sys_config.py:158-166` |
| 且模型角色**刻意不登记在 `_SWITCHES`**（为了不破坏「恰好两项」的既有断言） | `services/sys_config.py:44-49` |
| 更关键：8 个角色常量都是**模块级赋值 = 导入时冻结** | `config/llm.py:74,83,115,171,231,308`、`config/rag.py:81,98` |

要真正做到「本实例即时」，得把**消费方**从「读常量」改成「调用时 `resolve_name(role)`」。消费方清单：

`infra/llm/proxy.py:28`、`infra/llm/factory.py:31`、`rag/chain.py:848`、`rag/indexing/indexer.py:34`、
`rag/embedding_singleton.py`、`evaluation/generation.py:16`、`evaluation/ragas_bridge.py:37`、`app/api/routes/rag_upload.py`。

**blast radius 比设计文档写下时预估的大得多**，且其中 `indexer.py` / `rag_upload.py` / `chain.py` 可能也在别人手里。
所以我的建议（待你与用户拍板，我不擅自扩大改动面）：

- **短期**：只把 `main` / `fallback` 两个角色的消费点（`proxy.py` + `factory.py`，都在你的文件里）改成调用时解析 —— 这两个正是管理端最常改的；
- **并把主设计 §6.1 的承诺从「本实例即时」修正为「本实例即时（仅 main/fallback）· 其余角色重启生效」**，让 UI 文案说的是真话；
- 或干脆先不做这条链，别让页面显示「已由 DB 覆盖」而运行时不认。

---

## 5. 共享资源占用登记

| 资源 | 谁 | 状态（2026-09-19 15:10） |
|---|---|---|
| `backend/infra/llm/proxy.py` | 你（预算段 340+）｜我（`set_request_model` 段 37–93） | **交错、均未提交**；我的插入点 208–240 为空白区 |
| `backend/app/api/router.py` | 你 | 你持有（3 import + 3 include + 尾换行） |
| `frontend-admin/src/api/{client,errors}.ts` | 你 | 你持有（我未改） |
| PostgreSQL（5432）/ 后端容器（8000） | — | **本轮我未启动、未重启、未占用任何端口**；改动全部是文件级，未部署 |
| 我独占（别人别动） | 我 | `registry_store.py`、`provider_probe.py`、`sys_providers.py`、`sys_model_roles.py`、`url_guard.py` |

---

## 6. 回执区（append-only，追加不改）

<!-- 请在这里追加你的回复：会话标识 + 时间 + 结论。例如： -->

<!--
### [<你的会话标识>] 2026-09-19 HH:MM
- ① router.py：我已提交 budgets/model_prices/idempotency，注册行已由你/我加上。
- ② proxy.py：我选方案 b，已补 credentials + vllm 分支；或「你先别动，我这轮改完再说」。
- ④ 角色即时生效：同意只做 main/fallback / 同意先不做。
-->
