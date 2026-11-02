# 审计与修复计划进度

## 当前状态
**审计阶段完成；修复未开始。** 已完成基于指定主线快照的静态审查，并整理 7 项安全发现、2 项业务正确性问题和 1 项 CI 清单缺口。发现与计划已写入 FINDINGS.md 和 TASK.md。Codex Security Standard 扫描已封存，报告见 C:\Users\wh\.codex\state\plugins\codex-security\scans\agent\775cf745f6b7088e062639390dbf688f0be61756_20261102T160137Z_opay35x6\report.md。

## 基线与工作树
- 日期：2026-11-03（Asia/Shanghai）
- 审计仓库快照：origin/main，HEAD 775cf745f6b7088e062639390dbf688f0be61756
- 审计目录：C:\Users\wh\.codex\worktrees\full-repo-audit-plan\agent
- 分支：codex/full-repo-audit-plan
- 原工作树：D:\Program Files\workplace\agent。保留原有未跟踪内容；没有在原工作树改文件。
- 其他已有 worktree：只读检查，未修改。
- 安全扫描：Standard，全仓 inventory 6422 项，scan ID 5dbdc462-5415-4858-9eb2-6de626dc9333。
- 审计工作树 source code：未修改。

## 已完成
- 检查仓库规则和测试分层指南。
- 本机 main 落后 origin/main 17 个提交；为避免过期基线，在隔离审计 worktree 使用最新 origin/main 快照。该 worktree 基线已核验干净。
- 复核安全重点：RAG、SQL、异步任务/Celery、MCP 注册表、共享 data API、文件处理、登录流程、旅游约束、图表公开 URL、CI 工作流。
- 对关键问题追踪入口、授权校验、sink 和影响边界；把条件性问题与已验证发现分开。
- 形成 5 个后续实施 worktree 批次和 T1/T2 定向验证计划。

## 发现计数
- 安全：1 项 P1、3 项 P2、3 项 P3。
- 正确性：2 项 P2。
- CI：1 项 P3。
- 待外部配置确认：代理转发头可信性、GitHub Environment/Runner 隔离、DNS rebinding 与真实出口限制。

## 测试和运行验证
本轮没有运行测试、应用、Docker、数据库或外部服务；没有读取 .env 或秘密。测试表是修复阶段计划，不是已执行证据。已执行文档级尾随空白检查与 git diff --check；项目测试未运行。

## 下一步
下一个会话从最新提交的 origin/main 创建 W1–W5 worktree，先处理 P1，再逐批修复和验证；按仓库规则交付 diff，等待确认后提交/合并。

## 2026-11-03 W1 批次实施完成（未提交，待确认）

- 工作树：C:\Users\wh\.codex\worktrees\fix-mcp-boundaries\agent；分支 codex/fix-mcp-boundaries；基线 origin/main b065431（含 #264/#265/#267）。范围按计划为 S1（P1）+ S2（P2）。
- S1 四层防线：admin_bundles 成员校验绑定租户 + grant 写入按 t.tenant_id 过滤且 0 行显式冲突；store 快照 _SELECT_GRANTS 加 t.tenant_id = g.tenant_id（存量错配行默认拒绝）；load_snapshot 排除工具↔连接租户错配；新增迁移 130_mcp_grant_tenant_consistency.sql（组合唯一索引 + 存量错配 grant 失效 + 组合外键 ON DELETE CASCADE NOT VALID），已登记 init_db MIGRATION_TARGETS（memory 库）。写路径 verify_write_authorization 原本已有租户过滤（复核确认）。
- S2：probe_http_api follow_redirects 改 False（3xx 仍按可达语义）；mcp_client 默认不跟随（核实）；eval_dispatch.py 不在审计范围未动。
- T2 测试：test_admin_bundles_api.py 增 TestGrantTenantBoundary 4 例（登记端拒绝/授权端 409/错配行读快照默认拒绝/组合外键拒绝直插）；新增 test_mcp_probe_redirect.py 2 例（重定向目标零触达/正常端点可达）。
- 验证：独立 PG（pgvector/pgvector:pg16，容器 agent-test-pg-w1，端口 55432，与主栈隔离）init_db rc=0（120 迁移含 130）；定向 pytest 12 passed（17.46s）；注册表既有回归 test_mcp_dynamic_e2e + test_mcp_health_monitor + test_consistency_dynamic 24 passed（48.27s）。
- 未覆盖风险：本机共享栈未从该工作树重建（devctl rebuild 会进入前端构建，按 HANDOFF §5 需跨会话协调，标 UNVERIFIED）；生产代理/出口策略与 GitHub 环境保护仍属仓库外补证项；CI 全量在 PR 门禁后执行。测试容器 agent-test-pg-w1 保留待复核用。
