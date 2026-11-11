# 六类评测集与管理端资产盘点

盘点日期：2026-11-12。来源包括 `backend/evaluation/datasets/README.md` 与各模块 JSONL/manifest、RAG/客服 fixture 说明、V2 API 契约及 Runner/测试实现，以及本机管理端只读页面。文件数量表示候选题数，不表示已人工核验、已可运行或已通过。

## 六类题集

| 类别 | 仓库题集与现状 | 可复用 | 需重核 | 需补资料/执行能力 |
| --- | --- | --- | --- | --- |
| RAG | `rag/cases.jsonl` 24 条；manifest 实际为 `6.0.0-desktop-candidate.4`，README 仍写 `.1`。每题有参考答案/ground truth 片段/必需文档等；24 条 `ground_truth_verified=false`。fixture 有 28 个登记文件，管理端 `rag_eval_kb` 当前可见 23 篇；两者有 5 篇差异。 | 来源锚点、事实片段、拒答与多文档题的结构可复用；先逐题重核 5 条并组成小集。 | 核对文件 hash、有效文档版本、引用原文与业务规则；至少核验目标知识库实际 chunk/检索结果。`faq_only.docx` UI 显示无处理轨迹，不能将活跃状态当作已建立索引证据。 | 对齐 fixture 缺失的 5 篇文件与知识库版本；确认各目标文档已发布/索引成功。把题集从 24 补至至少 30 时须有对应原文，不得生成无来源金标准。 |
| 客服 | `cs/cases.jsonl` 324 条（v2.2）；action/query/faq/multi_turn/complaint/safety/missing 分类齐全。 | 选 30–50 条代表样本，覆盖事实问答、状态查询、多轮、投诉、安全和拒答。 | 40 条 multi_turn 未被决策回放执行；40 条 safety 的风险维度显式 skip；现有 324/324 仅是旧离线口径，管理端 Gate 为 INVALID。逐题重核业务事实与状态变化。 | `cs_eval_kb` 目前 0 文档；受控合成资料在 `backend/evaluation/fixtures/cs_eval_kb`，须显式导入且需要 embedding Provider。客服真实运行/会话上下文、风险裁判能力未接入 V2。需要客服政策、退款/取消时限与权限、订单状态测试数据及可复位账号/订单。 |
| SQL | `sql/cases.jsonl` 15 条；`sql_v2/cases.jsonl` 45 条，含 gold SQL、目标表、拒答标记、状态。 | `sql_v2` 45 条足以选出首期 20–30 条，优先受控 schema、正向结果、聚合/连接和拒答样本。 | 确认 Gold SQL 与业务定义、时间语义、空值/权限拒答、目标数据行的预期结果。现 V2 `sql` 是 contract-only，只校验契约与安全，不执行被测查询或比对结果，Gate 固定 INVALID。 | 需要可复位的脱敏受控 DB 快照、schema/字段字典、业务指标口径、gold 查询结果及写操作/敏感字段规则；将真实 Agent 查询和受控结果核验接入 Runner。 |
| 旅游 | `travel/cases.jsonl` 34 条，`travel_v2/cases.jsonl` 34 条（1.1.0）；分 smoke 4、core 15、regression 3、hard 12。`travel-provider/cases.jsonl` 8 条 fake Provider 探针。 | 选 20–30 条覆盖行程约束、POI、交通、日期/价格及降级策略；保留离线 deterministic 集。 | V2 travel 是离线质量 Runner；Provider 只用模拟服务/故障注入。不能据此认定真实地图、酒店/交通供应商已通过。 | 真供应商凭据/测试沙箱、上下游配额与费用批准、脱敏预订/库存数据、真实失败 Trace、取消/改期规则及端到端预订测试条件。真实供应商脚本为 `backend/scripts/e2e_travel_providers.py`。 |
| Planner / Tool | `planner/cases.jsonl` 20 条，结构要求 capability、禁止词、步数界限和完整边；`tool_selector_eval.json` 67 条单独跑批；`planner_params.json` 8 条 live 参数测试。 | 20 条 Planner 案例达到首期下限；Tool selector 题可用于选择代表性案例。 | 当前统一 V2 API 未支持 `planner`；Tool selector 也不是 V2 `ModuleKind`。已有 Planner legacy Runner 是 live 路径，需确认当前模型与工具配置、输出和 Trace。 | 需要把 Planner 与 Tool 选择/调用的定义、依赖图、权限规则、Tool 真实/沙箱能力和失败注入合入可追溯 V2 Runner；补充可复现 live 配置及 Tool 调用记录。 |
| 产品 E2E | `e2e/cases.jsonl` 25 条；包含 workflow、多步计划/SQL/report、direct skill、故障注入和对抗样本。 | 可挑 10–20 条覆盖主流程、关键副作用边界和故障恢复的契约样本。 | V2 E2E 是 contract-only，故障注入是 app graph 级，不是实际管理端/API/业务链路验收；Gate 固定 INVALID。 | 需要可复位测试租户/用户/订单/DB、真实鉴权、异步任务 worker、应用服务和外部依赖沙箱，定义可观察的产品结果、Trace/审计证据及幂等/撤销规则。 |

## 已存在的管理端发布资产（共享本机栈，只读盘点）

页面为 `http://localhost:3200/evaluations/center`，由当前超级管理员会话查看；这是共享栈资产，不代表指定工作树的新 UI 已运行。

| 管理端资产 | 当前状态 | 判定 |
| --- | --- | --- |
| `cs_locked_v2_2` / 客服决策层锁版评测集 v2.2 | 已发布，324 条 | 有运行记录但 Gate `INVALID`；不能用作质量基线。 |
| `smoke_refuse_mvmdmqfr` / 链路冒烟·拒答样本 | 已发布，1 条 | 可作拒答契约样本，数量不足以形成核心集。 |
| UI 实测·检索方案 | 已发布 RAG 1 条 | 历史运行曾 FAIL/INVALID；不作为新 RAG 基线。 |
| 其余可见 `synthetic_*`、`qa-synth-candidate`、UI 实测空集 | 草稿，0 条 | 不作为已发布基线；核对对应 Trace/候选后才处理。 |

运行记录当前可见客服 324/324 但 Gate INVALID，以及 RAG 1 条的 FAIL/INVALID 运行。禁止把“例数通过”覆盖 Gate 的无效结论。未发现可直接拿来作为新基线的已发布六类资产。

## 脱敏失败 Trace 与证据

本轮已确认工作树保存的 `.playwright-mcp/` 是 UI 验证产物，不是失败 Trace。仓库 README/fixture 提到 Trace 回流机制，但尚未得到一条可用、脱敏且含完整输入/输出/业务上下文的运行 Trace 样本；管理端“失败 Trace”列表需在目标工作树与现有 super_admin 会话下继续核验。只有 Trace 作为问题线索，不能直接充当参考答案或事实证据。

## 需业务方/运行环境补齐的具体资料

1. RAG：对齐 28 个 fixture 与实际 23 篇文档的文件名/hash、有效文档版本、索引/chunk 处理轨迹；确认首期新增 6 条候选题可引用的原文。
2. 客服：确认退款/取消/发货/投诉/安全规则版本，准备 `cs_eval_kb` 的合成受控语料及订单状态夹具；提供真实会话上下文执行与风险判定方式。
3. SQL：提供脱敏、可重置、只读测试 schema/数据快照，业务指标口径、Gold SQL 结果以及允许/拒绝的表列规则。
4. 旅游：提供供应商 sandbox、测试凭据注入路径、测试商品/库存和真实第三方调用授权/预算；指定预订、取消、改期结果证据。
5. Planner/Tool：确认接入 Runner 的工具集合、稳定版本、沙箱端点、调用/失败记录和副作用防护边界。
6. 产品 E2E：提供专用测试账号/租户、隔离业务数据、可重置流程、外部依赖沙箱及预期审计/Trace 断言。
7. 脱敏失败 Trace：提供上述边界下可读取的成功/失败成对 Trace 或确认管理端现有候选可用于回流；去除用户、订单、凭证及私密字段。

## 入口切换判定

当前存在旧版历史版本与运行记录，但没有确认通用“归档并退出默认入口”的管理能力。新基线验收前不改默认选择；届时优先采用可回滚的软弃用/入口过滤（保留 DatasetVersion/Suite/Run/Audit 原记录），先导出目录/hash 与运行快照作为回滚清单，再单独验收入口默认项。若服务端没有支持状态，则暂不写共享 DB 默认字段，先交付管理端显式选择和回滚步骤。
