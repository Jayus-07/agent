# PROGRESS: admin-governance-p1p2

> Worktree: `D:\Program Files\workplace\agent-wt-gov-p1p2`
> Branch: `refactor/admin-governance-p1p2-20261110`（基线 origin/main 6a560ac）

## 已完成（含验证证据）

| Commit | 内容 | 验证 |
| --- | --- | --- |
| b7e44b3 | P1-1 后端去重（shared/admin_llm）+ P1-2 列表裁剪（_tool_list_view）+ semantic 审计 model 留痕 | 定向 pytest 64 passed + test_mcp_dynamic_e2e 16 passed |
| f197abd | P1-3 errMessage 下沉 + P1-4 registry Runtime 改名 + McpToolSummary 类型拆分 | tsc 干净；vitest 87 文件 590 用例全绿 |
| f01e02c | P2-5 tools/mcp 页面拆分（18 文件，纯移动） | tsc 干净；590 用例全绿（page.test 零改动通过） |
| d4c85dd | 载荷契约测试（TestToolListPayload）+ P2-6 react-query 样板（2 section）+ fixture 收紧 + mountPage 包 Provider | test_admin_mcp_api 23 passed；tsc 干净；590 用例全绿（含时序修正后连跑复核） |

测试补充说明：

- `TestToolListPayload` 4 用例：摘要字段齐全 / 大字段不随列表下发 / 列表视图 ⊂ 详情视图一致性。
- mcp 页 fixture 收紧为 `McpToolSummary` 精简形状（消费端锁定裁剪契约）。
- `waitForText` 助手：react-query 异步解析时序修正，断言保留未删。
- 探查中发现的历史事实：HTTP 工具编辑表单的 Schema 预填在列表数据流下历来恒为默认值（snapshots 仅详情端点返回），本次保持既有行为并注释说明。

## 文档同步

- `docs/development/frontend-guide.md`：新增「5. 服务端数据获取（react-query 增量迁移）」——根 Provider 常驻、增量边界、queryKey/invalidate/双形状/测试包裹约定、errMessage 与 Runtime 前缀命名约定；原「验收」顺延为第 6 节。
- 本任务文档 TASK.md / PROGRESS.md 新建并随 PR 提交。

## 剩余步骤

1. 全量定向验证（后端 6 个测试文件 + e2e；前端 tsc + vitest + next build）。
2. devctl 用本 worktree 重建受影响服务（app/frontend-admin）并健康检查。
3. push 分支 → PR → 合并（用户已授权）。

## 遗留/风险

- **遗留清理项（非本轮范围）**：`api/mcp.ts#getConnectionTools` 前端无消费方（连接抽屉用本地 mcpTools 过滤），后续可评估删除或接线。
- **风险**：列表载荷裁剪是内部 admin API 契约变化，消费方仅本仓两页（已同 PR 适配 + 契约测试锁定）；若有仓外脚本直连该接口（未发现），需同步。
- 本地验证注意事项见 TASK.md「环境/复现备注」（.env 干扰、测试库迁移）。
