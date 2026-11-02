# 全仓审计与修复优化计划

## 目标与完成标准

基于 origin/main 快照完成静态全仓安全与高影响正确性审计，并把已验证问题整理为可分批实施、可验证的修复计划。当前阶段只提交审计计划，不改业务代码、不提交代码、不合并。

本阶段完成标准：
- 安全扫描报告、发现清单、进度和下一个会话提示词都已保存。
- 每项发现包含代码位置、触发前提、影响边界、修复方向和定向验证方案。
- 实施计划按风险排序，并给出每批次独立 worktree 与分支建议。
- 工作区只包含审计计划文档；原工作树和其他 worktree 内容未被覆盖。
- 本轮不执行应用测试或部署。后续实现阶段遵循仓库 AGENTS.md 的测试、重建、健康检查及提交规则。

## 审计基线和范围

- 仓库：Agent Platform（FastAPI、LangGraph、Next.js、PostgreSQL、Redis、APISIX、RAG、SQL、客服、旅游、选品、Celery/MCP）。
- 审计目标：干净 worktree 中的 origin/main。
- HEAD：775cf745f6b7088e062639390dbf688f0be61756。
- 审计 worktree：C:\Users\wh\.codex\worktrees\full-repo-audit-plan\agent。
- 分支：codex/full-repo-audit-plan。
- 扫描：Codex Security Standard 全仓安全扫描，扫描清单 6422 项；结合重点身份、租户、数据、工具调用链和近期主线变更的静态审查。
- 限制：未逐行人工审阅全部文件；未运行应用、测试、Docker、数据库或外部服务；未读取 .env 或秘密；生产代理、出口策略、动态数据库记录、GitHub Environment 保护和 runner 隔离未验证。

## 总体修复顺序

### 阶段 0：实施前基线确认
每批任务开始前从最新已提交 origin/main 创建独立 worktree 和 codex/ 分支；确认 HEAD、git status、相关文档和调用链。若主线已变化，重新核对发现是否仍成立，不照搬过期行号或实现。

### 阶段 1：跨租户与数据边界（最高优先）
1. MCP 授权绑定租户（P1）：grant、runtime、tool、connection 的租户必须一致；修复 grant 创建查询和执行校验，评估数据库约束及历史脏数据。
2. 共享数据 API 授权（P2）：按现有权限契约分别保护 preview/read 与 collect/write；不得信任客户端 enable_write。
3. 上传文件路径约束（P2）：使用服务端生成文件名，验证最终路径位于上传根目录；异常清理只能删除本次创建的文件。

### 阶段 2：出站与登录安全
4. MCP 探活重定向 SSRF（P2）：探活默认不跟随重定向；若产品必须允许跳转，则逐跳做 URL、DNS/IP、allowlist 检查并限制跳转数，跨源时不转发认证信息。创建、手动探活和周期健康检查共用安全实现。
5. 登录重定向（P3）：三个前端都解析目标 URL 并限定同源，拒绝协议相对路径及反斜杠变体。
6. 密码持久化（P3）：停止将密码写入 localStorage；迁移时清理旧凭据键，交给浏览器密码管理器保存。

### 阶段 3：业务正确性和发布守卫
7. 旅行行程约束（P2）：午餐只在离开时间前可完整放入时插入；否则返回明确的时间不足结果。补齐“不要带孩子/不想带孩子”等否定表达，保留否定后转折正向需求的语义。
8. CI 定向清单（P3）：把已存在的旅行缓冲测试加入 rag_smoke，并确认工作流引用路径、测试选择与运行预算一致。
9. 图表 URL 能力熵（P3）：随机后缀提升为至少 128 bit；验证公开渲染语义和现有目录约束。

## 建议实施工作树

下列为实施阶段建议的独立 worktree；本轮只写计划，不创建这些工作树。每批从实施时最新的已提交 origin/main 建立，确认目录不存在后再执行 git worktree add。

| 批次 | 目录建议 | 分支建议 | 范围 | 优先级 |
| --- | --- | --- | --- | --- |
| W1 | C:\Users\wh\.codex\worktrees\fix-mcp-boundaries\agent | codex/fix-mcp-boundaries | MCP 跨租户授权 + 探活重定向 SSRF | P1/P2 |
| W2 | C:\Users\wh\.codex\worktrees\fix-data-api-boundaries\agent | codex/fix-data-api-boundaries | data API 权限 + 上传路径穿越 | P2 |
| W3 | C:\Users\wh\.codex\worktrees\fix-travel-constraints\agent | codex/fix-travel-constraints | 儿童否定解析 + 午餐离开时间 + rag_smoke 清单 | P2/P3 |
| W4 | C:\Users\wh\.codex\worktrees\fix-login-credential-safety\agent | codex/fix-login-credential-safety | 三端登录重定向 + 主站明文密码持久化 | P3 |
| W5 | C:\Users\wh\.codex\worktrees\fix-report-url-entropy\agent | codex/fix-report-url-entropy | 图表公开 URL 随机熵 | P3 |

依赖与并行建议：W1 和 W2 优先串行完成并分别审查；W3–W5 在各自 worktree 可独立施工。不要在审计工作树或原始共享目录直接修代码。每批遵守 PR 合并门禁；本轮不创建 PR。

## 测试和验证总策略

依照 docs/development/testing-guide.md 选择最小充分验证。以下为后续修复验收计划，不表示本轮已经执行。

| 批次 | 最小定向验证 | 级别 | 额外验证/验收 |
| --- | --- | --- | --- |
| W1 MCP 租户隔离 | grant 创建、加载、授权与执行的跨租户负例；同租户正例；tool ID/tenant 不一致时失败关闭；探活重定向到私网不得触达目标；正常公网可用；跨源认证不转发 | T2 | 数据库约束/迁移测试；受影响后端服务从该 worktree 重建并通过健康检查 |
| W2 data API | 普通用户/无权限角色不能读写；有权限身份只能访问授权范围；enable_write 客户端参数不能越权；路径穿越文件名及解析失败清理不得触碰上传根目录外文件 | T2 | data 路由调用方检查；后端重建与健康检查 |
| W3 travel + CI | 否定儿童短语、正向表达、否定后转折表达表驱动用例；出发时间落在午餐窗口内/前/后边界；工作流包含相关测试且路径有效 | T1/T2 | 运行对应旅行测试和工作流静态/配置校验；如后端代码变化，按仓库门禁重建受影响服务 |
| W4 登录 | 同源内部 redirect 成功；协议相对、绝对外域、反斜杠变体回退默认页；登录/登出前后 localStorage 不含密码；旧键迁移清理 | T1 | 三端定向构建/行为验证，检查 sessionStorage 与密码管理器兼容 |
| W5 report URL | 生成 token 达到至少 128 bit 随机熵；图片仍按设计公开加载；路径穿越仍被限制 | T1/T2 | 后端定向测试与受影响服务重建/健康检查 |

每批验证成功后更新本任务 PROGRESS.md：记录分支、HEAD、改动、实际命令及结果、未覆盖风险。未执行项写“未验证”。安全、授权、租户及数据边界必须有针对性 T2 证据。不要默认跑全量回归。

## 验收与停止条件

- 安全边界修复有拒绝负例与合法正例，失败关闭；无跨租户资源/凭据穿透。
- 代码改动必须来自对应 worktree 重建受影响的本机服务并确认健康；做不到则标 BLOCKED 或 UNVERIFIED。
- 当前阶段仅审计与文档：无代码测试、无服务重建、无提交/合并。完成文档和扫描报告即结束本轮。
- 实施批次完成后先展示 diff 与验证证据；未经用户明确确认不提交、不合并、不清理 worktree。
- 外部配置问题（代理转发头、GitHub 环境、self-hosted runner、真实出口规则）需从部署/平台配置另行验证，不假设仓库源码能证明。
