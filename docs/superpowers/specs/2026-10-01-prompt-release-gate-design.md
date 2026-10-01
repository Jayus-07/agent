# Prompt 发布门禁与运行时版本治理设计

## 目标

把 Prompt 的生产发布改造成“候选版本先评测，评测通过后才切换 production 并热更新”的企业级链路；当前评测使用 DB 绑定的外部模型，未来只替换 DB 中的模型服务绑定即可迁移到自建模型。管理端必须能查看候选版本、评测状态、发布记录和运行时热更新状态。

## 范围

本阶段包含：

- Prompt 候选发布记录与状态机；
- 外部模型评测任务的异步触发、结果回调和幂等处理；
- 生产发布前的质量门禁；
- 管理端候选发布、评测、审批、发布和回滚界面；
- Prompt 与 Tool 版本在 Trace 中的可审计性检查；
- 发布后各运行进程的 Prompt epoch、版本和热更新状态校验；
- GitHub Actions 的 Prompt 回归评测入口。
- 评测集来源、版本、适用范围和发布结果的可追溯链路。

本阶段不包含：

- 在管理端在线编辑 Tool 代码；
- 把 Tool 的代码事实源复制到数据库；
- 自建模型部署本身；
- 对所有历史 Prompt 逐一补写业务评测集。

## 发布语义

### Prompt 版本与发布记录分离

现有 `prompt_versions.status` 继续表示版本生命周期；新增发布记录表示一次面向环境的发布尝试。发布记录状态为：

```text
pending -> running -> failed
                   -> passed -> approved -> published
                                      \-> rolled_back
```

候选版本只允许进入 `staging` 指针，不能改变生产运行时的 `active_version`。生产发布必须满足：

1. 评测记录属于当前 Prompt key 和版本；
2. 评测状态为 `passed`；
3. 高风险 Prompt 具有管理员审批记录；
4. 发布操作在同一事务中更新 production/active_version、写审计记录并发出热更新通知。

评测失败时，生产版本、Prompt runtime snapshot 和线上请求均保持不变。

### API

新增或扩展以下接口：

```text
POST /prompts/{key}/versions/{version}/release
GET  /prompts/{key}/releases
GET  /prompts/{key}/releases/{release_id}
POST /prompts/{key}/releases/{release_id}/approve
POST /prompts/{key}/releases/{release_id}/publish
POST /internal/prompt-evals/callback
```

现有 `POST /prompts/{key}/publish` 保留兼容入口，但必须复用发布门禁；未通过门禁时返回结构化 409，而不是直接热更新。

## 评测与 GitHub CI

管理端提交 release 后，后端创建发布记录并触发 GitHub `workflow_dispatch` 或 `repository_dispatch`，参数包含 `prompt_key`、`version`、`release_id` 和评测套件。GitHub 使用测试环境 DB 和 GitHub Environment Secrets 中的外部模型凭据，不访问生产 DB。

CI 完成后通过签名回调更新发布记录：

- `passed`：保留 report.json、指标、模型绑定指纹和 Prompt 版本快照；
- `failed`：记录失败指标和原因；
- 重复回调：以 `release_id` 和 CI run id 幂等处理；
- 回调失败：GitHub artifact 仍保留，发布记录不得误判为通过。

当前本地浏览器验收允许使用本机后端的外部模型评测执行器，验证完整状态机；这不代表使用本地模型。自建模型上线后，CI 只需切换测试 DB 中的 provider/base_url/model_name。

## 评测集治理与使用链路

### 执行链路

候选 Prompt 或 Tool 版本提交发布时，先根据资产的 `affected_domains`、`eval_suites` 和 `release_policy` 选择评测集，再固定本次运行的完整口径：

```text
候选版本 -> 选择 suite -> 固定 dataset version + KB/fixture version
          -> 固定 Prompt/Tool/Model 指纹 -> 执行评测
          -> 计算指标 -> 写入 release record -> 质量门禁
```

RAG 单用例执行顺序为：加载问题和 ground truth → Embedding 查询 → 向量/BM25 混合检索 → Adaptive 扩展 → Reranker 精排 → 可选答案生成 → 对比证据、事实、拒答和引用标注 → 聚合 Recall@K、MRR、NDCG、Top-1、事实覆盖、拒答准确率等指标。

### 评测集来源

评测集允许来自以下来源，但进入门禁前必须经过 schema 校验和人工审核：

- 业务专家编写的核心 Golden Case；
- 线上 Trace、用户差评、转人工和人工反馈形成的 Bad Case；
- 从文档和 fixture 自动生成的覆盖性问题；
- 权限、越权、敏感信息、拒答和对抗样本；
- Tool 参数错误、超时、失败、重试和故障注入样本；
- 定期从线上请求分层采样的真实分布样本。

线上 Trace 生成用例时必须补齐 expected、required_facts、should_reject、权限范围和来源说明，并经人工确认后才能进入 Golden 或回归集合。原始用户数据进入仓库前必须脱敏。

### 分层评测集

项目沿用 JSONL case、suite 和 snapshot 的分层结构：

- PR/smoke：少量高价值用例，快速发现明显回归；
- Golden：业务专家维护的稳定核心集合；
- Nightly/release：复杂、多跳、拒答、权限、引用和生成质量集合；
- Bad Case：线上失败持续回流，用于回归和专项修复；
- Tool/安全专项集：独立于 RAG 集，用于 Tool 契约、故障注入和安全门禁。

评测集必须有不可变版本、owner、来源、覆盖域、适用 Prompt/Tool、schema 版本和变更说明。修改评测集本身需要生成新的 dataset version，不能覆盖历史运行口径。

### 资产与评测集映射

不同变更只运行相关集合：

```text
Embedding/Reranker       -> RAG 检索集
QA/Reporter Prompt       -> 答案正确性、事实覆盖、引用集
客服 Supervisor Prompt   -> 客服意图、转人工、投诉分类集
Planner Prompt           -> 任务拆解、Capability DAG 集
Tool 代码/契约           -> Tool 契约、参数、故障注入、E2E 集
权限/安全代码            -> 越权、敏感信息、拒答集
```

发布记录必须保存 `dataset_version`、`suite`、`kb_id`、`fixture_set`、Prompt snapshot、Tool contract fingerprint、model binding fingerprint、Git SHA 和完整报告路径。这样可以回答“某个 Prompt 版本发布时使用了哪份数据、哪个模型、哪些 Tool，以及结果是否回退”。

## Trace 与版本审计

每次请求/任务开始时固定 Prompt 版本快照，并写入：

```text
AgentState.prompt_versions
trace.tags.prompt_versions
eval_run_records.prompt_snapshot
```

Tool 版本沿用现有 Tool contract lock / registry 事实源，Trace 至少记录：

```text
tool_id
tool_contract_hash 或 contract_version
tool_status
agent_domain
```

验收必须覆盖：

- Prompt 发布前后请求不会混用 in-flight 的版本；
- 新请求使用新 production 版本；
- Trace 同时存在 Prompt 版本和 Tool 契约版本；
- 回滚后的新请求恢复旧版本；
- 历史 Trace 不被发布动作改写。

## 热更新验收

发布成功后，管理端读取 Prompt runtime status，展示：

- 全局 epoch；
- 进程名和实例 id；
- 当前 Prompt 版本；
- snapshot 时间；
- reload source；
- healthy/stale/degraded 状态。

只有生产切换事务成功且运行进程达到 healthy，发布记录才显示“已发布”；若部分进程 stale，显示“已发布/运行时同步中”，并保留旧版本回滚入口。

## 管理端信息架构

Prompt 详情页新增发布控制台：候选版本、目标环境、评测状态、评测指标、模型绑定、审批人、运行时进程状态和审计时间线。按钮按状态控制：草稿可提交评测，评测中不可发布，失败可重试，通过后可审批，审批后可发布。

同时新增“评测治理”入口，但它不是一个允许随意修改 Golden 答案的在线文本编辑器，而是评测资产的审核和追溯界面，建议包含以下页面/区域：

- **数据集总览**：按模块展示 dataset、版本、owner、审核状态、case 数量、内容 hash、覆盖能力、最近一次评测和当前可用 suite；
- **候选用例审核**：展示来源类型、脱敏后的问题/Trace 摘要、期望事实、拒答/权限标注、来源 Trace/工单引用和变更原因，支持通过、驳回、退回补充，不允许把未审核数据直接加入门禁；
- **数据集版本详情**：展示版本差异、增删用例、覆盖矩阵、审核人、变更说明和关联 suite；审核通过后生成新版本，历史版本只读；
- **Suite 与发布策略**：展示 `pr_baseline`、`ci_golden`、`regression`、安全集等 suite 的 case 范围、触发方式、阈值和适用 Prompt/Tool，不允许在页面临时改写运行口径；
- **评测运行详情**：展示失败用例、指标、数据集/KB/fixture、Prompt/Tool/Model 指纹、Git SHA、报告链接，并能回跳对应发布记录。

管理端的写操作只产生“候选审核结果”或“新版本发布记录”，不直接覆盖仓库中的 canonical 数据。原始线上 Trace 只对有权限的审核角色可见，页面默认展示脱敏内容；所有通过、驳回、发布和版本生成动作写入审计日志。

Tool 不提供在线代码编辑。管理端只展示代码注册的 Tool 清单、所属域、Capability、契约哈希、风险/审批要求、调用量、错误分类、耗时和最近变更；Tool 变更仍通过 Git PR、契约 lock 和一致性测试完成。

## 权限与安全

- 候选评测允许 editor/admin 发起；
- medium/low 风险发布沿用现有矩阵；
- high 风险发布要求 admin/super_admin 审批；
- callback 使用独立签名密钥并校验 release_id、run id 和时间窗口；
- GitHub Secrets 不写入仓库、评测报告或前端；
- 禁止通过客户端自设 header 伪造发布人或审批人。

## 验收标准

1. 评测未通过时，调用生产发布接口返回 409，active_version 不变；
2. 评测通过并审批后，发布成功更新 production/active_version 并发送 hot reload；
3. 管理端能显示候选版本、CI/评测结果和运行时健康状态；
4. 浏览器流程能够完成“创建候选版本 → 提交评测 → 通过 → 发布 → 查看热更新”；
5. 发布前后 Trace 能看到 Prompt 版本和 Tool 契约版本；
6. 回滚后的下一次请求使用旧 Prompt 版本，历史 Trace 保持不变；
7. Prompt release callback 重复提交不会产生重复发布或重复审计记录；
8. 发布评测记录包含 dataset version、suite、KB/fixture、Prompt snapshot、Tool contract fingerprint 和 model binding fingerprint；
9. 从线上 Trace 创建的评测用例经过 schema 校验、脱敏和人工审核后才能进入门禁集合；
10. 管理端能查看数据集总览、候选审核、不可变版本、Suite 映射和评测运行详情；
11. 未脱敏或未审核的候选用例不能进入门禁集合；
12. 评测集变更生成新版本，历史评测仍能按原 dataset version 复现。
