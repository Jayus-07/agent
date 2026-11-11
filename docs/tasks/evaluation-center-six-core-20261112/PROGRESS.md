# 进度记录

## 2026-11-12 — 接手与基线盘点

- 工作树：`C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`。
- 分支：`codex/evaluation-center-ui-20261112`；HEAD：`8a85113a78ef0aa31cb8c466aaa9233625a662b8`。
- 保留交接时所有 11 个 tracked dirty files、4 个新评测 UI 文件及 `.playwright-mcp/` 产物；修改前核对 `git status` / `git diff --stat`。未 reset、删除或 stage 现有资产。
- 已读根 `AGENTS.md`、前端设计技能和测试指南；保留四页签及页面现有表格、分页、抽屉、详情。新增配置使用现有白卡、边框、颜色 token 和右侧 Drawer。
- 现有运行服务是共享 Compose project `agent`，主机可用内存约 0.93 GB；不启动第二套栈。浏览器 MCP 默认 profile 报占用，继续使用已登录 super_admin 的 Edge 做管理端只读资产核验。

## 关键发现

- 当前工作树前端：“配置评测”只跳到运行页；运行页仅载入已发布 V2 Suite。V2 API 已有创建 Suite 与发布 SuiteVersion，可由前端补齐闭环。
- RAG canonical 24 条皆未人工标记证据已核验；fixture 28 文件与共享管理端 `rag_eval_kb` 的 23 文档存在差异。管理端文档列表中 `faq_only.docx` 的 chunks 操作反馈没有处理轨迹。本轮不能先宣称有完整索引证据。
- V2 `sql/cs/e2e` 当前 contract-only；Gate 应为 `INVALID`。V2 travel/travel-provider 是离线/模拟 Runner；planner 不在 V2 supported modules。具体逐类缺口见 `DATASET_INVENTORY.md`。
- 共享管理端已发布：客服锁版 v2.2（324 条）、1 条拒答冒烟；另有 1 条 RAG 历史方案。客服虽显示 324/324，Gate INVALID；RAG 历史运行 FAIL/INVALID。大量空草稿均不作为基线。
- 尚未找到可直接用于验收的脱敏失败 Trace。`.playwright-mcp/` 是 UI 验证文件，不作为业务 Trace。

## 阶段状态

| 阶段 | 状态 | 证据/阻塞 |
| --- | --- | --- |
| 资产与覆盖盘点 | 已形成初版，待对失败 Trace、知识库 hash/索引和业务原文逐项补证 | 题数与 Runner 边界已核对；缺件列于 `DATASET_INVENTORY.md`。 |
| 方案配置/发布入口 | 未开始修改 | 已确认只需复用 V2 create/publish API；需加选中 `dataset_version_id` 的 Drawer 流程和 RunsTab 预选。 |
| RAG 人工核验及 Doubao 真运行 | 未验收 | 需要目标工作树 UI/API 真链路及可核验的已发布 KB 文档证据；当前 28/23 差异未解决。 |
| 多 KB 搜索、预览、拆批 | 未开始修改 | 旧生成 API 每请求 1–5 条并绑定单个 corpus snapshot；目标题集需要按 KB 拆成互相独立的数据集/批次。 |
| 六类质量验收/默认入口 | 未开始 | 多类 V2 运行能力不足；暂不切默认资产。 |

## 后续实际验证

- 前端：运行既有 Vitest 指定文件、TypeScript 检查、受影响前端从本 worktree 启动并浏览器检查配置/发布/运行导航。
- 后端：若只改前端，不重建后端；若改 Runner/接口则从指定 worktree 运行 V2 定向契约测试并按仓库规则重建受影响服务。内存不足时不并发构建，明确阻塞。
- 真实 RAG：固定 DatasetVersion、SuiteVersion、模型/Prompt/KB 快照及 Run ID；核对每题检索证据、答案质量、有效性/Gate。只有真实 Provider 调用和结果可见后才算完成。
- 提交门禁：最终展示 Diff、HEAD、实际测试/浏览器证据、P0/P1 未验证项与 PASS/BLOCKED/UNVERIFIED；等待用户明确提交确认。
