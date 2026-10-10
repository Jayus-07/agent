# Prompt 发布门禁浏览器验收

## 目标

完成 Prompt 发布门禁的人工浏览器验收，并记录真实页面、发布状态、Runtime epoch 与 Trace 元数据结果。

## 范围

- 修复管理端 `/prompts` 的 BFF 503 后，使用本地管理端完成低风险 Prompt 发布流程。
- 验证评测未通过时发布受阻；评测通过并发布后，Runtime 显示新版本和递增 epoch。
- 发起可生成 Trace 的请求，确认 Trace 保留 Prompt 版本与 Tool 契约摘要。
- 不记录或提交本地账号密码；不重启其他会话拥有的服务。

## 验收标准

- 使用获授权的本地管理账号完成浏览器流程并留存结果。
- 发布状态、Runtime 状态/epoch 和 Trace 元数据均符合计划契约。
- 记录任何仍存在的预先失败及其与本任务的关系。

## 当前阻塞

管理端 `/prompts` 经 BFF 返回 503，故障现象与排查入口见[记录](evidence/prompts-bff-503.md)。
完成后先确认目标服务归属，再执行浏览器验收；结果和未完成项更新到 [PROGRESS.md](PROGRESS.md)。
