# DB 驱动的模型供应商与模型角色运行规范

> **目标契约；不等于实现已全部验收。** 本文管“供应商实例、数据库模型、角色绑定如何成为真实运行配置”，不复制详细 [模型配置治理设计](../model-config-governance-design.md)，也不承担预算扣费规则（见 [预算与计费](llm-budget-billing.md)）。模型厂商信息必须来自数据库登记和当前运行时注册表，不靠新增文档维护静态清单。

## 1. 三种实体必须区分

| 对象 | 责任与典型字段 | 事实源 |
| --- | --- | --- |
| **Provider Driver**（代码） | OpenAI 兼容/专用协议，流式、Embedding、rerank、超时和 Tool Calling 适配 | `backend/infra/llm/providers/`、`backend/infra/llm/models.py` |
| **Provider Instance**（数据库） | 实例 ID、driver、base_url、网络范围、服务能力、是否启用、加密凭据 | `backend/infra/llm/registry_store.py`、`backend/services/model_config.py`、供应商表/迁移 |
| **Model**（数据库） | 模型名称、provider_id、model_kind、能力、上下文窗口、输入输出限制、价格关联 | 模型注册表 DB、`backend/infra/llm/models.py` |
| **Model Role**（数据库覆盖 + 代码定义） | 已注册角色对应模型及运行策略；Embedding 匹配索引身份，rerank 独立排序文本候选 | `backend/config/model_roles.py`、DB 绑定、`registry_store.py` |
| **Secret / Runtime Snapshot** | 授权的密钥解密、热更新快照、失败时旧值保留及可观测来源 | `backend/infra/llm/credentials.py`、`registry_store.py` |

**数据库是用户配置的持久事实源，代码定义的是合法角色、Provider 驱动及安全约束**，进程快照是热路径投影，不是另一套可任意改写的配置真相。代码默认值和角色继承是兼容/缺省语义，不可无审计地把未登记或无凭据的模型当作健康可用模型。

## 2. 正常读写时序

```mermaid
sequenceDiagram
  participant Admin as 管理端
  participant API as 后端管理 API
  participant DB as PostgreSQL 注册表
  participant Snap as 运行时快照
  participant Proxy as LLM Proxy / Model Role
  participant Vendor as Provider
  Admin->>API: 登记实例/模型/角色 + 凭据
  API->>API: 管理员授权/字段与 URL 安全校验
  API->>DB: 事务写入 + 审计
  API-->>Admin: 明确结果/版本/生效状态
  DB-->>Snap: 后台读取并校验（刷新机制）
  Proxy->>Snap: 按角色查模型/提供者/策略
  Proxy->>Proxy: 权限、模型预算、可用性检查
  Proxy->>Vendor: 受限调用（凭据不暴露给浏览器）
  Vendor-->>Proxy: 内容、usage 或业务失败
```

- **管理面**：`/sys/providers` 与 `/sys/model-roles` 是列表/校验等入口；真实创建/编辑 API 由 `backend/services/model_config.py` 与对应路由提供。不能只根据前端 UI 展示“已保存”认定所有 worker 已生效。
- **热路径**：`backend/config/model_roles.py` 明确保持 stdlib 轻量、禁在模块顶层导入重量 `infra.llm`；DB 注册表由 `registry_store.refresh_registry()` 维护进程快照，业务消费统一模型解析/代理，不能让每个 Agent 自建数据库查询、独立厂商 SDK 或 `.env` 覆盖。
- **刷新失败**：当前 `registry_store` 表示 DB 抖动时保留旧快照而非清空；但**凭据解密失败、绑定不存在或模型未注册不能假成功/回退到不安全的环境密钥**。管理端应展示最后刷新版本、失败原因以及“旧快照仍有效”的区别。
- **模型探测**：真实推理探测与“列出模型目录”是两个能力；后者失败不等于供应商不可用。OCR 角色不参加后台周期探测，避免把“凭据存在”误报为上游可调用；管理员可手动执行一次最小文本调用验证模型名与凭据。该探测可能产生少量供应商费用，且不代表图片识别质量已验证。供应商地址、协议、凭据或请求头变更后，旧人工探测标记为待重新验证。带密钥的草稿探测必须经过管理员闸、受限 URL/SSRF 策略、低速率及请求体白名单。
- **手动健康探测限流**：按网关注入的操作者身份执行限制，复用 `/sys/providers` 每实例 10 次/滚动分钟的滑动窗口，并用现有 Redis Token Bucket 共享多实例的每人 10 次突发额度和 10 次/分钟补充速率。Token Bucket 不是严格的全局滚动分钟窗口。Redis 不可用时，滑动窗口仍限制当前实例；多实例合计配额无法保证为每人每分钟 10 次。
- **模型能力**：Chat、Vision、Embedding、rerank、Tool Calling、结构化输出、流式 usage 能力按该模型真实声明和实测判定。Embedding 变化检查向量维度及索引身份；同为 1024 维不代表相同语义空间。rerank 输入文本对、输出分数，无需匹配向量维度；单独切换 rerank 不重建向量，但质量、超时与阈值仍需验收。

## 3. 配置、凭据与模型调用红线

1. 浏览器仅使用 BFF/API；服务器端 decrypt，不返回原始密钥。入站 API Key、出站供应商 API Key 不混淆；审计日志记录操作者/实例/安全探测结论但不记录密钥明文或完整敏感 payload。
2. 管理端写入模型、凭据、价格、角色绑定按既有权限、幂等与审计机制；`editor` 的会话级选择不等于改变全局默认。
3. `base_url`、网络范围、重定向、域名/DNS/private IP 的风险在每一次出站探测的可信边界校验；不能通过目录端点绕过 SSRF。
4. 调用前固定本次 model/provider/role/Prompt/price snapshot 的相关版本与归因；fallback 只能选注册且授权的模型，遵守预算和能力兼容，不换用不同模态冒充成功。
5. 价格、缓存 token、订阅制及 CNY 折算按专门计费文档执行；模型“健康”不证明账单结算正确。Trace 标明 requested/upstream model 与版本，不泄露密钥。

## 4. 验收（MP-01～MP-12）

| 编号 | 最低证明 |
| --- | --- |
| MP-01 | DB 新增 provider/model 后管理端可见；运行时快照刷新，新请求真正使用绑定版本 |
| MP-02 | DB 改 role、删除/停用被占用模型时拒绝悬空引用；旧请求/新请求版本行为清晰 |
| MP-03 | Redis/DB 抖动保留合法旧快照并标记陈旧；恢复后刷新；不静默取旧环境密钥 |
| MP-04 | 缺密钥、错误密钥、解密异常可诊断，不泄漏敏感数据，也不绕到未知提供商 |
| MP-05 | Admin 写、Editor 会话临时覆盖、Viewer 只读/无权动作权限边界正确 |
| MP-06 | SSRF：公网/私网范围、重定向和非法 URL 拒绝；model-catalog 不绕探测闸 |
| MP-07 | 200 + 非模型响应、错误模型、401/429/5xx、超时和模型无流式能力真实分类 |
| MP-08 | 流式/非流式、工具调用、Embedding/rerank/Vision 能力不兼容可见且拒用 |
| MP-09 | Embedding 模型更换触发索引兼容检查；不在不兼容向量上继续检索 |
| MP-10 | 真实模型调用的 token、价格版本、计费与 Trace requested/upstream 归因一致 |
| MP-11 | 多 worker 热更新、缓存失效、回滚与并发请求隔离满足已公布契约 |
| MP-12 | 模型数据库不可用、备份/恢复及发布回滚有可核验证据 |

验收先跑 isolated unit/DB integration，再用至少一个授权真实 Provider 测试（需要本机凭据时标 `BLOCKED`）。新增厂商不是给调用点新增第二份 Provider 配置表；应从实际运行 `ModelRole → Registered Model → Provider Instance → Driver` 链路证明。

## 5. 本地与云端嵌入模型切换

当前实现采用维护窗口：切换后暂停向量检索，全量重建发布成功后恢复。不是无停机双索引切流，也不会自动回退到另一个嵌入模型。

1. 在既有供应商/模型注册表登记目标，云端按量模型先提供有效价格；本地 `billing=local` 声明仅超级管理员用户可写。
2. 超级管理员在角色绑定选择目标，`POST /sys/model-roles/embedding/preview` 实际调用一条文本，校验输出数量、有限数值、非零及真实存储列维度，返回影响集合/向量数、配置指纹、费用方式及 Trace。预检本身也有用量，云端可能收费。
3. 管理端展示后果并二次确认。`PUT /sys/model-roles/embedding` 必须携带 `expectedModel`、`expectedFingerprint`，涉及重建时须 `confirmRebuild=true`。后端会再预检，配置并发变化返回 409；维度不符返回 422，保存前拒绝。空库可直接启用，新旧 worker 仍受全局身份围栏保护。
4. 点击“检查并重建全部向量”，经预检及确认后调用 `POST /sys/model-roles/embedding/rebuild`，携带模型、指纹、确认及 `Idempotency-Key`。任务复用 `rag_index` 队列、TaskState 租约、进度、Trace 和统一用量账本。`GET` 同路径读取最近一次全局任务；任务中心提供既有控制入口。
5. 本次只重做向量，复用已入库文本及权限元数据，不重复 OCR、解析或切分；解析策略/内容改变另走文档 reindex。重建覆盖所有主向量集合（含文档、分块、路由及保留的历史集合），不只处理当前可见文档。
6. 候选完整后，持任务租约与配置行锁，核对源文本/元数据快照，一次事务发布。失败保留旧向量且不解除待重建状态；不会把一个已完成的批次当作全库成功。同任务恢复复用已落库且快照一致的候选；同事务提交凭证防止“向量已提交、任务状态未成功”恢复时重复生成。供应商已收费但响应丢失的调用仍可能无法复用，不能宣称上游费用严格恰好一次。

索引身份包括登记模型、供应商、上游名、地址、协议及模型参数的指纹，密钥轮换不改变向量空间。已有向量但无身份不自动认领；有旧身份但缺指纹的存量索引暂按模型名兼容，下次正式重建补齐指纹。正在使用的嵌入配置不能通过旧专项入口、原地改上游地址/模型或历史回滚绕过预检；需登记独立供应商/模型再切换。重建活动期间禁止再次切换嵌入角色。

查询在调用模型前检查索引，获得查询向量后按其固定身份再次核对，并持共享事务锁完成向量 SQL；发布不能穿过第二次核对与检索之间。仅在请求开始时检查不足以覆盖模型调用期间的切换。

`VECTOR_PG_DIM` 必须与真实 `vector(N)` 列一致；本功能拒绝维度不符，不会自动改列或丢弃数据。将来迁移不同维度属于独立存储迁移，需先备份、规划容量及恢复方式。

## 管理端展示契约（2026-10-26）

- 历史接口保留原始 `operator`，另以 `operatorName` 返回当前用户名；无法匹配时显示未知用户及原 ID，系统/自动化身份单独标注。用户名为当前目录值，不是历史姓名快照。
- 模型测试保留原有 summary/steps，在 `userMessage` 和 `nextAction` 中返回依据失败 reason 分类的中文结论与建议；未知原因不猜测。原始报文只放技术详情。模型调用成功不等于业务质量或索引兼容验收。
- 向量预检分别返回 `modelChanged` 与 `incompatibleCollections`；collections/vectors 为全部处理范围，不是文档数，也不等同于全部不兼容。相同显式模型的无变化保存由前端直接结束编辑；主动重建仍须预检及确认，不绕过后端指纹和检索门禁。
