# PROGRESS — capability-governance（平台能力治理优化）

- 任务文件：同目录 TASK.md（唯一事实源）；治理面复查证据：`docs/tasks/cs-workstation-v2/REVIEW.md` §7/§8
- 更新时间：2026-11-01
- 当前状态：**计划已落稿，等待执行会话接手**（启动清单 TASK §8；G1 阻塞点=向用户索取 owner 映射）

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| G1 | capabilities/domains 加 owner 字段（fail-fast + 对账 + 管理端列） | 计划中（阻塞点：owner 映射需用户输入） | — | — |
| G2 | 运行时对账告警（未注册工具指标 + 失败率突增规则） | 计划中（先探明执行器现状） | — | — |
| G3 | MCP 出口白名单（connect 前置 allowlist + 部署注记） | 计划中（先探明连接流程） | — | — |
| G4 | 客服意图目录试点（inventory 端点 + 管理端只读视图） | 计划中 | — | — |
| G5 | 域清单协议化（tool-skill-guide 约定 + travel 适配器） | 计划中（依赖 G4） | — | — |
| G6 | 成本按域归因报表（join 验证 → 只读报表） | 计划中（先验证覆盖率） | — | — |

状态取值：计划中 / 进行中 / 待验收 / 已完成 / BLOCKED（注明原因）/ PAUSED（注明现场）。

每期完成后必须回填：分支与 PR、实际测试命令与 PASS/FAIL、live 验证证据、验收矩阵对应项、遗留事项。

## 环境

- 基线：origin/main@16378db（PR #195 合并点）；执行 worktree `D:\Program Files\workplace\agent-gov`（新建，命令见 TASK §8）
- 备用：`agent-cs-v2` worktree 停靠 standby/origin-main（审查会话遗留，本任务不用）
- 测试解释器：`D:\Program Files\workplace\agent\.venv\Scripts\python.exe`（worktree 无 .venv）；真库 5433
- 治理测试基线：治理批 37 passed（test_layer_consistency + test_admin_mcp_api + test_admin_tool_failure_probe + test_mcp_health_monitor）
- live 口径：X-API-Key（容器 printenv，不回显）+ X-User-Id/X-Tenant-Id/X-Auth-Type: jwt/X-User-Roles: admin → :8000
- 环境坑与预案：TASK §2.4（devctl 静默失效 / Mimosa hook / GitHub 重试 / vpnkit connect_timeout / 冻结纪律）——开工必读

## 日志

### 2026-11-01（一）—— 计划落稿（审查会话产出）

- 产出来源：cs-workstation-v2 九期独立审查收尾后的治理面复查（治理测试全绿 + 六份治理文档判定 + 两类测试基建缺口已修复并入 PR #193）；业界实践对照（Agent 365/Entra owner 模式、MCP allowlist/HITL/审计、能力目录运营化、运行时漂移检测）。
- 关键核验（免重探，详见 TASK §2）：capabilities 17/14 routed/skills 12 对账零漂移；admin tools 40+lock_git_sha；MCP 中心 3 连接 0 动态工具；trace_records 有 workflow_name（按域归因 join 可行）；客服域零游离 Tool/MCP。
- 用户决策记录：优化分五项、按「平台小改先行（G1/G2/G3）→ 客服试点（G4）→ 协议化（G5）→ 报表验证（G6）」排期；不冻结 travel-v2 生产化等在途工作。
- 待办：执行会话接手后从 G1 开始；G1 前置=owner 映射。
