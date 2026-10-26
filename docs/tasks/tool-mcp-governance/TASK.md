# Tool / MCP 能力治理改造（单 Agent 施工）

> 任务书：`agent_tool_mcp_implementation_plan_v1.md`（用户已确认，2026-10-27）。
> UI 交互基准：`agent_tool_governance_interactive.html` 原型（演示数据不作为事实源）。

## 目标

按 Batch 0→1→2→3→4 收敛 Tool/MCP 能力治理：统一 ToolSpec 抽象、统一治理执行
路径、三大管理工作台、独立 Discovery Agent、动态配置审核发布闭环。不新建与
现有 Runtime/Trace/预算/Prompt/Checkpointer 并行的系统。

## 范围与红线

- Batch 0（当前）：核查并修复 PR #107（MCP 能力中心）的 Review 问题与 CI
  基线，补强多 Worker 撤权/禁用安全语义；验收通过且获得用户合并授权后合并。
- 未经用户授权：不合并 PR、不动共享生产数据、不执行真实外部写操作。
- 施工 worktree：`D:/Program Files/workplace/agent-wt-mcp`（分支
  `feat/mcp-capability-center` = PR #107）。

## 验收

见计划第 5 节 E01–E14。Batch 0 出口：5 项 Review 问题修复 + 强一致撤权语义
+ CI 基线澄清 + 回归测试证据。
