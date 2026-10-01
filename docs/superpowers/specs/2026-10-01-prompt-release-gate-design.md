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
7. Prompt release callback 重复提交不会产生重复发布或重复审计记录。
