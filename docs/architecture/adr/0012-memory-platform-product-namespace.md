# ADR-0012: 记忆平台化——产品命名空间与统一记忆服务（分期施工）

状态：Accepted（分期施工；本文是依据，不以单 PR 实现）
日期：2026-11-10

## 背景与动机

项目已有完整的三层记忆体系（L1 请求内 / L2 会话 / L3 长期事实与画像，
见 [memory-governance.md](../governance/memory-governance.md)），核心机制
（冲突裁决、画像投影、MM-01～MM-10 验收）经评估保持不变。

该治理规范同时写明："本版不新增 agent_id 列、不强制不同 Agent 独立记忆
空间；**引入第二个需要私有 L3 的 Agent 时**先定义授权矩阵再设计 schema"。
平台现有三个产品（企业助手 `/agent`、客服、旅游）均已/将接入记忆，触发条件
成立。审计 #68 证实了按现状续行的实际缺陷：

- **跨产品串扰**：`memory_user_id()`（`core/product_context.py`）已把客服
  产品换算为 `customer_service::<user>` 命名空间，但只有主图路径（runner）
  消费；RAG 链（`rag/chain.py`、`rag/pipeline.py`）与客服知识服务
  （`customer_service/knowledge/service.py`）以 raw user_id 直接读写——
  客服会话历史进入企业助手上下文，反之亦然。
- **写入无意图门**：`memory_store_tool` 由模型自主调用即写 verified
  explicit 记忆（治理登记 `confirmation_policy: none`），且 explicit 新值
  可覆盖一切旧通道（审计 #50）。
- **结构性风险**：`memory_user_id` 只是可绕过的函数约定——每个新调用点
  都要"记得"做换算，靠代码评审维持正确性不可持续。

外部参照（2026-11-10 调研）：ChatGPT 记忆 = 显式 saved memories + 自动
事实/摘要直接注入（非向量检索）+ dreaming 后台整合，用户可见可删；Mem0
以 `user_id/agent_id/run_id/org_id` 四维命名空间做多 Agent 隔离；Zep 以
时间有效性（事实失效而非删除）治理长周期事实；Letta 为 Agent 内核记忆
（不适用本平台多产品共享形态）。

## 决策

### D1. 记忆读写收编唯一入口；产品命名空间服务端强制

新建统一记忆服务层（`backend/memory/` 内收口，非新进程）：所有产品的
L2/L3 读写必须经该入口，入口内部按**服务端可信上下文**解析完整记忆键：

```
memory_key = (tenant_id, product, user_id)
```

- `product` 由调用入口声明（`agent` / `customer_service` / `travel`），
  身份（tenant/user）只取服务端验证后的 Principal——调用方传入的 raw
  user_id 不再是记忆键的来源。
- 调用方无法绕过：入口是唯一路径，`memory_user_id()` 退化为入口内部
  实现；新增 Agent/新链路接入即自动获得隔离，不存在"忘了传参"。
- 修掉 #68 的方式是结构性收口（打补丁方案——在 RAG 链调用点补换算——
  被本决策取代）。

### D2. 三层命名空间与方向性共享规则

| 层 | 键 | 方向性规则 |
| --- | --- | --- |
| 产品层（默认私有） | `(tenant, product, user)` | 各产品独立；互不可见 |
| 身份层（白名单共享） | `(tenant, user)` + 字段白名单 | 只读**下发**到产品；产品层事实默认**不回流**，仅用户显式升级（"以后都用 X"）才写入 |
| 租户层 | `(tenant)` | 组织级事实（企业助手为主） |

- 白名单字段清单进配置并随查询审计，不是隐式全量共享。
- 该规则落实治理规范铁律："不允许把单轮旅游偏好无条件升级为持久人格
  偏好"（旅游偏好留在 `travel::` 命名空间，衰减快）。

### D3. 事实带时间有效性：失效而非删除（Zep 模式）

L3 事实增加 `valid_from` / `invalid_at` / `evidence`（来源消息引用）：

- 过期/被取代事实不参与召回，但可审计还原（隐私合规"可追溯"）。
- `conflict.py` 的裁决语义从"新值覆盖旧值"扩展为"旧值标记失效"；
  origin priority（explicit > behavioral）与 confidence 机制保留。
- evidence 引用使每条 L3 事实可溯源到具体用户轮次（同时收窄 #50 的
  可追溯缺口）。

### D4. dreaming 式后台整合（防膨胀、防矛盾）

低频后台任务（复用既有 outbox / beat 基础设施）：

- 碎片事实合并、置信度强化/衰减、矛盾事实触发失效或澄清建议。
- 衰减速度按产品配置：旅游偏好快衰减（半年不复现降权）、客服案件完结
  衰减、身份事实慢衰减。
- 红线：整合只调整置信度与有效性，**不臆造新事实**；整合动作全部留痕
  （trace + 事实版本），可回滚。
- **衰减边界（防误读）**：衰减只降低自动注入上下文的排序权重，**不删除、
  不隐藏**——未失效的记忆在用户主动查看（设置页全量列表）中始终可见、
  仍可被显式检索召回。健康/安全类事实（过敏、无障碍、宗教饮食限制等）
  标记为长期事实，**豁免衰减**，永远参与自动注入。两年前写入且从未被
  冲突的记忆保持有效、照常可召回；"没有冲突"不构成失效条件。

### D5. 三产品统一引擎、差异配置（不做三套实现）

| 配置项 | 企业助手 `agent` | 客服 `customer_service` | 旅游 `travel` |
| --- | --- | --- | --- |
| 写入激进程度 | 中（显式优先） | 低（事务型，偏好以工单/订单为准） | 高（偏好即核心资产） |
| 衰减速度 | 慢 | 中 | 快 |
| 身份层 | 读 + 可写 | 只读 | 读 + 显式可写 |
| 显式写确认门（#50） | 开 | 开 | 开 |

### D6. `memory_store_tool` 加用户意图门（#50）

**第 1 期落地形态：证据门（2026-11-10 修订）**。写入前校验当前会话最近
用户消息确实表达过该内容（jieba 分词语义词 ≥2 命中或规范化子串复述，
双通道任一），无证据即拒绝（fail-closed）——与后台 inferred 管线的
`_evidence_from_user` 防线对齐，补齐 provenance 契约（"用户消息 = 唯一
可形成用户长期记忆的证据，prompt 不是安全边界"）在显式工具通道的唯一
例外。`confirmation_policy` 暂保持 `none`。

**user_confirmation 不在本期启用的原因**：探索确认 Prepare→Confirm
治理机制（guard/preview/TTL/指纹）完整，但图内回路未接线——
`tool_confirmation_id` 无写入者、无确认 API、前端无弹窗，现有
`memory_forget_tool`（user_confirmation）因此实际不可执行。直接改登记
会把 store 工具同样置于不可用状态。**确认回路接线（后端 SSE 确认卡 +
前端弹窗 + 既有 user_confirmation 工具修复）单独立项**，完成后本工具
对称升级为 `user_confirmation`。

### D7. schema 与存量数据迁移

- L3 事实表与 L2 会话表增加 `product` 列（存量回填 `agent`）；唯一键从
  `(tenant, user, memory_key)` 扩展为 `(tenant, product, user, memory_key)`。
  结构变更走迁移脚本、可回滚（仓库工程规范）。
- 存量客服串写数据：按客服 session_id 特征**归档**到
  `customer_service` 命名空间（不删除，保可追溯）；无法可靠归因的行标记
  失效。
- 迁移前后跑 MM-05 用户/租户/产品交叉召回对照实验，禁止只靠静态源码
  确认。

### D8. 前端呈现与用户记忆管理（三端分工）

原则：**隔离由服务端强制，前端只做查看入口**（前端传什么都不能改变
命名空间解析）；对齐 ChatGPT 记忆管理 UX——可见、可删、可确认候选，
**不提供原文编辑**（编辑 = 删除 + 会话中重说，保住 evidence 语义，
避免手改绕过冲突裁决）。

现状盘点（2026-11-10）：后端 `app/api/routes/memory.py` 已按产品前缀
拆分（`/memory/agent/*`、`/memory/customer-service/*`）且已消费
`memory_user_id()`；`/profile/pending` + `/profile/{id}/verify` +
`DELETE /profile/{id}` 接口齐备。缺口在前端 UI 与 product 维度展示。

| 端 | 现状 | 改造 |
| --- | --- | --- |
| 用户端 `frontend`（3100） | 设置页仅画像**只读**列表（`settings/page.tsx`） | ① 挂 pending/verify"待确认"区（API 已有）；② 每条删除按钮（API 已有）；③ 产品 Tab（企业助手/客服/旅游，旅游接入时启用）；④ 身份层白名单事实独立"基础信息"区（可编辑，第 3 期） |
| 客服端 `frontend-cs`（3300） | 无记忆管理入口 | **不加**：坐席不管理用户记忆；用户本人经用户端管理 |
| 管理端 `frontend-admin`（3200） | 有数据面 memory API | 租户视角**治理页**：记忆量/增长/抽取失败率监控（MM-06/MM-10）、租户层公共记忆管理、用户申诉"忘记我"时代查（审计留痕）——不替用户编辑 |

记忆过多：后端靠 D4 整合与快衰减保证有界；前端默认展示高置信度
top N（约 20 条）+ 折叠分页 + 类型/产品筛选 + 搜索。冲突对用户仅呈现
为"待确认"（裁决在服务端，用户不感知冲突过程）。

### D9. Trace 链路观测位：读在链路前端、写分两段、补三处断链

现状链路（保持不变的部分）：

```
【同步请求 trace T1】
  guard → session_load
    memory.retrieve（type=retrieval；属性仅 count/threshold 枚举，
                     严禁记忆原文/PII/租户标识——§七红线）
  make_initial_state（L1 装配）→ graph 执行 → end_turn
    ├─ save_turn（L2，与回答同事务，强一致）
    └─ MemoryExtractionJob 入 outbox（写触发点）→ T1 finish
【异步（T1 已结束）】
  extraction worker：抽取 LLM 调用 → conflict 裁决 → L3 落库 → 画像投影
```

第 1 期随收口补三处断链：

1. **job 记 `source_trace_id`**：extraction job 关联产生它的请求 trace，
   记忆条目 → evidence（消息 ID）→ trace 全链可回溯（用户申诉排障的
   前提）；dreaming 整合任务（D4）产出**任务级 root trace**（type=memory，
   动作计数进 span 属性），与触发它的 job 群关联。
2. **extraction LLM 调用挂预算归因**：抽取跑在异步 worker、无请求预算
   绑定，`llm_usage` 归因为空（与 P1 #81 workflow 预算旁路同构）——按
   服务身份（svc 记忆抽取账号）预占结算，成本可见可控。
3. **span 增加 `product` 枚举维度**：product 是非 PII 枚举，允许进 span
   属性，支撑 D5 三产品差异配置的观测对比；原文/PII/租户红线不变。

读向位置的责任含义：记忆检索在 `make_initial_state` 之前完成，检索
质量直接决定整图执行质量；观测保持"计数可见、内容不可见"，记忆内容
错误靠 evidence 链反查而非 span 属性。

## 分期路线（每期独立可验收）

| 期 | 内容 | 验收 |
| --- | --- | --- |
| **1. 收口** | D1 唯一入口 + D7 schema 迁移 + 全部调用点迁移（RAG 链、客服知识服务、runner）+ D6 确认门 + D9 三处断链（source_trace_id / 预算归因 / product span） | 双产品对照：客服会话不进 agent 上下文；MM-05 实验通过；#68/#50 关闭；记忆→trace 可回溯、抽取成本入账。**✅ 已落地（#413 + #415）** |
| **2. 时效** | D3 时间有效性（迁移 145：valid_from/invalid_at，supersede 失效双写，五路径召回过滤）+ D4 dreaming 整合（beat memory.dreaming，supersede 版本链承载合并/矛盾失效，安全豁免，任务级 trace）+ D5 衰减产品档位 + D-1 契约变更（废止"<0.2 归档"，衰减只降权） | 过期事实不召回可审计（test_memory_time_validity）；记忆量有界（dreaming 合并 + 失效通道）；整合可回滚（版本链）。**✅ 已落地（2026-11-10，MEMORY_DREAMING_ENABLED 默认关待观测开启）** |
| **3. 共享与可见** | D2 身份层白名单下发 + D8 前端（用户端待确认/删除/产品 Tab/基础信息区 + 管理端治理页）+ travel 前缀归一补位（评审遗留：`known_prefixes` 加 `travel::`） | 白名单外零串读；删除联动画像/缓存失效（MM-07）；三端无越权入口 |

## 不做与替代方案否决

- **不引入 Mem0/Zep 等外部记忆服务**：平台红线是自研 PG + 统一治理
  （预算/trace/计费/审计）链路；现有冲突裁决与画像内核方向正确，缺的
  只是入口收口与 product 维度。
- **不做 Letta 式"记忆住进 Agent 进程"**：三产品共享平台，外部记忆层
  形态（集中服务 + 命名空间）才是目标。
- **不新增第二份"动态记忆配置文件"**：命名空间授权矩阵以 DB/迁移为
  唯一事实源，与 ADR-0011 的"DB 是动态配置唯一事实源"同构。

## 与既有文档的关系

- 本 ADR 触发并取代 `memory-governance.md`"隔离范围与未来演进"一节的
  "本版不新增 agent_id 列"决策；MM-01～MM-10 验收框架不变，MM-05 扩展
  产品维度交叉实验。治理规范在该节随第 1 期施工同步修订。
- 审计 #68 / #50 的关闭以第 1 期落地为准；PRIORITY.md 状态回填随施工
  PR 同步。
