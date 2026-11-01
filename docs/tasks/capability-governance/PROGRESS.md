# PROGRESS — capability-governance（平台能力治理优化）

- 任务文件：同目录 TASK.md（唯一事实源）；治理面复查证据：`docs/tasks/cs-workstation-v2/REVIEW.md` §7/§8
- 更新时间：2026-11-01
- 当前状态：**计划已落稿，等待执行会话接手**（启动清单 TASK §8；G1 阻塞点=向用户索取 owner 映射）

## 阶段状态总表

| 期 | 主题 | 状态 | 分支 / PR | 证据 |
|---|---|---|---|---|
| G1 | capabilities/domains 加 owner 字段（fail-fast + 对账 + 管理端列） | **已完成**（PR #198 合入 @180284c） | feat/cap-gov-g1 / #198 | 本文件日志 2026-11-01（二） |
| G2 | 运行时对账告警（未注册工具指标 + 失败率突增规则） | 进行中（先探明执行器现状） | — | — |
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

### 2026-11-01（二）—— G1 实施与合入（执行会话）

- **owner 映射决策**：用户授权统一占位 `platform-team`（27 条全部），yaml 头部标注「待按团队实况替换」，替换走独立小 PR。
- **交付**（PR #198，feat/cap-gov-g1 → main@180284c，10 文件 +125/-5）：
  - capabilities.yaml：domains(10)+capabilities(17) 每条 `owner: platform-team` + 头部 owner 约定注释；
  - manifest.py：DomainDecl/CapabilityDecl 加 owner 字段，`load_manifest()` 缺失/空白 fail-fast（与 yaml 同 PR 原子合入）；
  - /capabilities 每条返回 owner；frontend-admin /skills 能力清单加「负责人」列；
  - 测试：缺 owner 启动拒绝 ×2 + TestManifestOwnership 对账断言 + API owner 断言 + page.test 列断言；既有 manifest fixture（test_registry_consistency / test_layer_consistency）连带补 owner；
  - 文档：tool-skill-guide.md 快速接入清单第 4 条落 owner 约定。
- **验证记录**（全部实际执行）：
  - G1 定向（test_registry_consistency + test_registry_overview_api）28 passed；
  - 治理批四件套 37 passed（基线保持）；
  - frontend-admin vitest 83 files / 582 passed（首跑基线，含新增 1 例）、tsc --noEmit 0 错；
  - 容器 agent-app-1 / agent-frontend-admin-1 自 agent-gov worktree 重建（compose 等价原命令，非 devctl）healthy，容器内换血标记（yaml 27 / manifest 3 / api 1）全在位；
  - live 直连 :8000 `GET /capabilities`：200，owner 17/17 非空，count=17/routed=14/skills=12 与基线一致零漂移；
  - 主链路冒烟 :8000/health、:9080、:3200/login、:3100 全 200；
  - CI 逐项核对：RAG Smoke (8 cases) / Detect RAG changes / admin-observability-auth / frontend-admin-functional / frontend-admin-security / scan 全 pass；skipping ×2（RAG Smoke Evaluation real API、preflight）= 非 RAG 变更预期跳过。
- **环境备注**：worktree 无 .env → 从主仓库拷贝（真库 5433 指向正确）；frontend-admin node_modules 以 Windows junction 复用主仓库依赖（依赖输入未变）；测试窗口有并行会话 travel booking 定向 pytest（无 HTTP 依赖，零冲突）。
- 待办：G2 探明执行器未注册工具行为与 stats 数据链；G3 探明 admin_mcp 连接流程。

### 2026-11-01（一）—— 计划落稿（审查会话产出）

- 产出来源：cs-workstation-v2 九期独立审查收尾后的治理面复查（治理测试全绿 + 六份治理文档判定 + 两类测试基建缺口已修复并入 PR #193）；业界实践对照（Agent 365/Entra owner 模式、MCP allowlist/HITL/审计、能力目录运营化、运行时漂移检测）。
- 关键核验（免重探，详见 TASK §2）：capabilities 17/14 routed/skills 12 对账零漂移；admin tools 40+lock_git_sha；MCP 中心 3 连接 0 动态工具；trace_records 有 workflow_name（按域归因 join 可行）；客服域零游离 Tool/MCP。
- 用户决策记录：优化分五项、按「平台小改先行（G1/G2/G3）→ 客服试点（G4）→ 协议化（G5）→ 报表验证（G6）」排期；不冻结 travel-v2 生产化等在途工作。
- 待办：执行会话接手后从 G1 开始；G1 前置=owner 映射。
