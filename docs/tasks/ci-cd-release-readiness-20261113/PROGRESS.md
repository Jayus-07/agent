# 进度

## 当前状态

`BLOCKED`：PR #466 已创建，代码提交的 PR 检查通过；生产完整发布仍被供应商凭据和 Prometheus 配置阻塞。未合并、未部署本分支。

## 恢复信息

- Worktree：`C:\Users\wh\.codex\worktrees\ci-cd-release-acceptance-20261113\agent`
- 分支：`codex/ci-cd-release-readiness-20261113`
- 基线 HEAD：`4f1ec9f73a55e8eae06451711e7084dda5137764`
- 最近核对的 `origin/main`：同一 SHA。
- 初始代码提交：`ae4c167102095f51b4b60cfaf688e7fb09b1c775`
- PR #466：`https://github.com/Jayus-07/agent/pull/466`，目标 `main`；未修改或合并其他 PR。

## 已有生产证据

- PR #465 已合并，镜像发布成功；生产 runner 已按固定 digest 执行部署。
- Release workflow run `43340644487` 未通过：构建 commit 的 7/8 位缩写冲突、业务问答无有效答案、任务运行门禁使用历史成功任务数量、模型门禁只检查 API HTTP 200、Prometheus 没有活跃目标。
- runner 已将新后端镜像部署到生产且 `/health` 返回 200，但 Release Gate 失败；这不是完整发布通过，也没有自动回滚。
- 当前生产仍需重新录入无法由现有 `SECRETS_ENCRYPTION_KEY` 解开的供应商 API Key，并配置可从服务器访问的 `PROMETHEUS_URL`。具体密钥不得写入本文件。

## 本分支改动

- `scripts/release.sh`：保留工作流注入的 7 位 SHA，校验它确实是当前 HEAD 前缀。
- `backend/scripts/verify_release_gate.py`：问答门禁要求 SSE `done`、答案非空且无错误；Celery 门禁检查 worker 在线并消费 `agent` 队列；模型门禁检查当前 `main` 角色可用且 24 小时内真实探测成功；Prometheus 地址可配置并验证活跃目标。
- 新增服务切换前的供应商密文解密检查，错误只输出脱敏的供应商 ID 和数量。
- 将定向测试接进 `rag_smoke`，并将脚本与测试路径加入 PR 免费检查过滤器，确认 main push 不因此触发付费评测；同步更新生产环境模板和发布规范。

## 验证

- 定向 pytest：51 passed（脚本身份、密文校验、Release Gate、生产工作流契约及 Smoke 路径过滤）。
- Ruff、`py_compile`、Git Bash `bash -n`、`git diff --check`：通过。
- PR #466 的 Secret Scan、Smoke 路径检测和免费定向回归通过；真实 API 评测因 PR 安全规则跳过。PR 与 `main` 无冲突。
- 从本分支构建后端镜像成功；镜像内两个发布脚本导入成功。导入时因未提供本地数据库密码出现非致命的内存预热告警。
- 未替换共享 Docker 服务；当前共享栈由其他 Worktree 组成，因此本分支应用服务健康未验证。生产 runner 尚未运行本分支改动。

## 下一步

1. PR #466 的代码检查已通过；文档进度更新后再确认最新 PR 状态。
2. 生产管理员在管理端重新录入失效 Provider API Key，在生产环境文件设置 `PROMETHEUS_URL`。
3. 按用户明确授权完成 PR 合并和生产审批后，观察新的生产 runner 全链路；必须 Release Gate 通过并成功写入发布记录，才可标记完成。
