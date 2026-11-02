# 下一个会话接手提示词

请在本仓库继续执行全仓审计后的修复优化计划。先阅读仓库根 AGENTS.md、docs/tasks/full-repo-audit-20261102/TASK.md、PROGRESS.md、FINDINGS.md，再核对当前 origin/main、worktree 状态、相关源码和现行测试。不得假设审计时的 HEAD/行号仍是最新事实。

## 目标
按计划优先级修复 S1–S7、Q1–Q2，并补齐 C1 的 rag_smoke 清单。优先从 S1（MCP 跨租户授权）开始。逐批完成、验证并更新 PROGRESS.md。不要扩大到未确认的外部配置问题；把代理头、GitHub Environment/Runner、DNS rebinding 作为需补充证据的独立事项。

## 工作方式
- 每个独立批次使用单独 Git Worktree 和 codex/ 分支，从最新已提交 origin/main 创建；建议批次和目录见 TASK.md。
- 创建前确认目标目录不存在，确认新 worktree 的 HEAD 与 origin/main 一致且干净。不得在原共享主工作树或审计 worktree 改业务代码；保留其他会话的未提交内容。
- 开始修复前复核 finding 的 source→sink、现有权限契约与调用方。只修确实存在的问题，不凭猜测扩大行为变化。
- 按 docs/development/testing-guide.md 执行最小充分定向验证。授权、跨租户、数据、凭据、SSRF 属 T2：需有拒绝负例和合法正例。测试失败时定位根因，不删断言、不 skip、不 mock 掩盖。
- 代码修改后按仓库规则从对应 worktree 重建受影响的本机服务并确认健康；无法安全重建或健康失败就报告 BLOCKED/UNVERIFIED 和下一步。
- 同步现行文档、必要的迁移/生成文件和 workflow 引用。不得运行默认全量回归。
- 每批交付 diff、HEAD、实际测试/重建/健康证据、未验证风险；未经用户明确确认不提交、不合并、不清理 worktree。所有代码最终必须通过 PR 合并 main。

## 优先顺序和验证重点

1. W1 S1 + S2：MCP
   - grant 写入、读取、执行三处保证 tenant 一致；错配历史记录失败关闭，不能解密凭据或打 upstream。
   - HTTP 探活禁用未经验证的跳转；手动和周期健康检查共用逻辑。
   - 测两租户隔离、同租户成功、错误 tenant 负例；探活重定向私网不可达、跨源无认证头。涉及迁移时验证合法/错配历史数据。
2. W2 S3 + S4：共享 data API
   - 依据权限标准定义 read/write 角色和资源范围；不能以客户端 enable_write 决定授权。
   - 上传文件用服务端 basename，containment 检查和安全清理。
   - 测低权限拒绝、合法身份访问受限范围、FastAPI 直连保护、路径穿越及错误清理边界。
3. W3 Q1 + Q2 + C1：旅行与 CI
   - 午餐不得越过离开时间；补齐儿童否定短语并保留转折语义。
   - 将旅行缓冲测试加入 rag_smoke，核对工作流预算及实际命令。
   - 运行表驱动边界测试与工作流对应定向测试。
4. W4 S6 + S7：登录
   - 三端同源 redirect；主站移除明文密码 localStorage 并清旧值。
   - 测同源、外站、协议相对及反斜杠输入；检查登录/登出后持久存储。
5. W5 S5：报告图表
   - 提升公开 URL token 熵，保留匿名展示的产品行为和目录限制。
   - 验证随机熵、公开加载、路径限制。

## 结束条件
每批完成前更新进度文档并逐条回应 finding。仅在实际执行后标测试/重建为通过。完成修复后展示所有批次 diff 和验证证据，等待用户决定是否提交/合并；不要代替用户做未授权的提交或合并。
