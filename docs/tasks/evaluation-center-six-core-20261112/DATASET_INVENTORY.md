# 六类评测集与管理端资产盘点

盘点日期：2026-11-12。来源包括 `backend/evaluation/datasets/README.md` 与各模块 JSONL/manifest、RAG/客服 fixture 说明、V2 API 契约及 Runner/测试实现，以及本机管理端只读页面。文件数量表示候选题数，不表示已人工核验、已可运行或已通过。

## 六类题集

| 类别 | 仓库题集与现状 | 可复用 | 需重核 | 需补资料/执行能力 |
| --- | --- | --- | --- | --- |
| RAG | `rag/cases.jsonl` 24 条；manifest 实际为 `6.0.0-desktop-candidate.4`，README 仍写 `.1`。每题有参考答案/ground truth 片段/必需文档等；24 条 `ground_truth_verified=false`。桌面 fixture 有 28 个文件；2026-11-13 按文件名+SHA 与共享管理端 Registry 对齐 23/28，具体缺 5 个文件见下文。 | 来源锚点、事实片段、拒答与多文档题的结构可复用；先逐题重核 5 条并组成小集。 | Registry 中已有 23 条均为 active、processing success 且 chunk_count>0；但 active_generation/index_generation 为空，尚不能证明向量、BM25 与 Registry 同代。该包是上传解析压力语料，不是正式业务知识。 | 如要完成上传压力集，应在隔离或版本化测试 KB 补入缺少的 5 份文件并核查完整索引生命周期。把题集从 24 补至至少 30 时须有对应原文，不得生成无来源金标准。 |
| 客服 | `cs/cases.jsonl` 324 条（v2.2）；action/query/faq/multi_turn/complaint/safety/missing 分类齐全。 | 选 30–50 条代表样本，覆盖事实问答、状态查询、多轮、投诉、安全和拒答。 | 40 条 multi_turn 未被决策回放执行；40 条 safety 的风险维度显式 skip；现有 324/324 仅是旧离线口径，管理端 Gate 为 INVALID。逐题重核业务事实与状态变化。 | `cs_eval_kb` 目前 0 文档；受控合成资料在 `backend/evaluation/fixtures/cs_eval_kb`，须显式导入且需要 embedding Provider。客服真实运行/会话上下文、风险裁判能力未接入 V2。需要客服政策、退款/取消时限与权限、订单状态测试数据及可复位账号/订单。 |
| SQL | `sql/cases.jsonl` 15 条；`sql_v2/cases.jsonl` 45 条，含 gold SQL、目标表、拒答标记、状态。 | `sql_v2` 45 条足以选出首期 20–30 条，优先受控 schema、正向结果、聚合/连接和拒答样本。 | 确认 Gold SQL 与业务定义、时间语义、空值/权限拒答、目标数据行的预期结果。现 V2 `sql` 是 contract-only，只校验契约与安全，不执行被测查询或比对结果，Gate 固定 INVALID。 | 需要可复位的脱敏受控 DB 快照、schema/字段字典、业务指标口径、gold 查询结果及写操作/敏感字段规则；将真实 Agent 查询和受控结果核验接入 Runner。 |
| 旅游 | `travel/cases.jsonl` 34 条，`travel_v2/cases.jsonl` 34 条（1.1.0）；分 smoke 4、core 15、regression 3、hard 12。`travel-provider/cases.jsonl` 8 条 fake Provider 探针。 | 选 20–30 条覆盖行程约束、POI、交通、日期/价格及降级策略；保留离线 deterministic 集。 | V2 travel 是离线质量 Runner；Provider 只用模拟服务/故障注入。不能据此认定真实地图、酒店/交通供应商已通过。 | 真供应商凭据/测试沙箱、上下游配额与费用批准、脱敏预订/库存数据、真实失败 Trace、取消/改期规则及端到端预订测试条件。真实供应商脚本为 `backend/scripts/e2e_travel_providers.py`。 |
| Planner / Tool | `planner/cases.jsonl` 20 条，结构要求 capability、禁止词、步数界限和完整边；`tool_selector_eval.json` 67 条单独跑批；`planner_params.json` 8 条 live 参数测试。 | 20 条 Planner 案例达到首期下限；Tool selector 题可用于选择代表性案例。 | 当前统一 V2 API 未支持 `planner`；Tool selector 也不是 V2 `ModuleKind`。已有 Planner legacy Runner 是 live 路径，需确认当前模型与工具配置、输出和 Trace。 | 需要把 Planner 与 Tool 选择/调用的定义、依赖图、权限规则、Tool 真实/沙箱能力和失败注入合入可追溯 V2 Runner；补充可复现 live 配置及 Tool 调用记录。 |
| 产品 E2E | `e2e/cases.jsonl` 25 条；包含 workflow、多步计划/SQL/report、direct skill、故障注入和对抗样本。 | 可挑 10–20 条覆盖主流程、关键副作用边界和故障恢复的契约样本。 | V2 E2E 是 contract-only，故障注入是 app graph 级，不是实际管理端/API/业务链路验收；Gate 固定 INVALID。 | 需要可复位测试租户/用户/订单/DB、真实鉴权、异步任务 worker、应用服务和外部依赖沙箱，定义可观察的产品结果、Trace/审计证据及幂等/撤销规则。 |

## 已存在的管理端发布资产（共享本机栈，验收前盘点）

页面为 `http://localhost:3200/evaluations/center`，由当前超级管理员会话查看；这是共享栈资产，不代表指定工作树的新 UI 已运行。

| 管理端资产 | 当前状态 | 判定 |
| --- | --- | --- |
| `cs_locked_v2_2` / 客服决策层锁版评测集 v2.2 | 已发布，324 条 | 有运行记录但 Gate `INVALID`；不能用作质量基线。 |
| `smoke_refuse_mvmdmqfr` / 链路冒烟·拒答样本 | 已发布，1 条 | 可作拒答契约样本，数量不足以形成核心集。 |
| UI 实测·检索方案 | 已发布 RAG 1 条 | 历史运行曾 FAIL/INVALID；不作为新 RAG 基线。 |
| 其余可见 `synthetic_*`、`qa-synth-candidate`、UI 实测空集 | 草稿，0 条 | 不作为已发布基线；核对对应 Trace/候选后才处理。 |

运行记录当前可见客服 324/324 但 Gate INVALID，以及 RAG 1 条的 FAIL/INVALID 运行。禁止把“例数通过”覆盖 Gate 的无效结论。未发现可直接拿来作为新基线的已发布六类资产。

## 本机知识库数量、生产数据边界与生成范围（2026-11-13）

- 只读查看本机共享管理端“知识库工作台”，共列出 14 个知识库、214 份登记文档：RAG 100 文档评测库（兼容）94、RAG 小型评测库（兼容）0、产品知识库 2、企业公共制度知识库 26、合成评测测试库 1、售后知识库 2、客服 FAQ 2、客服评测知识库 0、投诉处理知识库 1、政策知识库 14、旅游知识库 48、旅游评测知识库 0、统一 RAG 评测知识库 23、话术知识库 1。
- 该页面连接的是本机共享开发栈，不是线上生产环境；所以“214 份”不能说成生产文件数量。没有读取生产环境文件清单，也没有对生产文件做上传、切分或检索验收。
- 当前生成抽屉在 `frontend-admin/src/components/workbench/evaluation/DatasetsTab.tsx` 只保留 `audience=test` 的库；仓库 `backend/evaluation/datasets/README.md` 也规定测试库与客服/对客生产库隔离。因此，业务库不出现在生成选择器不是“没有库”，而是现有隔离规则生效。不能为方便测试直接把生产 audience 放进生成入口。
- `qa-synth-test` 目前只有 1 份资料；把它用于验证生成按钮、候选记录和拆批属于小规模链路冒烟，不能代表真实企业 RAG 质量。`rag_eval_kb` 与它曾被同时选择，20 题预览拆为两个数据集和 4 个 5 题任务；首批因预算限制失败，预算修正后尚未重跑。这个尝试没有完成多库真实质量验收。
- 第一阶段的 28 份桌面文件覆盖 6 种格式和清单中 16 类上传场景；数量足够完成这些已列场景的首轮上传/解析/切分检查，但不能证明线上生产资料覆盖充分。当前 23/28 只是 Registry 对照，其中 23 份处理成功且有 chunk，索引代次未核验；其余 5 份未补齐。应先把 28 份完整跑通，再取得脱敏后的生产资料清单（格式、体积区间、扫描比例、表格/版本更新场景）比对，只针对真实缺口补样本。
- 生产知识的后续验证应使用经业务授权的脱敏副本或隔离测试租户：复制到 `audience=test` 知识库后验证上传→解析→切分→索引→按题检索证据；原生产库保持只读。对 Doubao 生成候选时，输入原文会发往模型服务，数据是否可外发需符合企业数据规则。
- 六类数据集都必须能回到可靠来源，但来源不全是知识库：RAG 绑定 KB 快照/文档版本/原文证据；客服需要政策 KB 加订单/会话夹具；SQL 用 schema 和受控数据快照；旅游用业务规则和供应商沙箱；Planner/Tool 用工具目录、权限与调用记录；产品 E2E 用可重置租户及业务数据。

## 桌面原始资料包核对（2026-11-12）

位置：`C:\Users\wh\Desktop\企业ai平台\测试文件\上传解析测试\testset`。

- `rag_eval_kb` manifest 共列 28 份原始文件、16 类上传场景；逐文件 SHA256 同时匹配 manifest 和本任务工作树中的 fixture，28/28 一致。格式数量为 PDF 7、TXT 9、DOCX 5、CSV 3、Markdown 3、XLSX 1。该包可用于重建隔离 RAG 测试语料和验证文档处理，不应当作新的正式业务知识。
- 桌面包的 RAG `cases.jsonl`、manifest、snapshot 和两个 suite 与仓库当前版本均不同。桌面包是 `6.0.0-desktop-candidate.1`，本仓库 canonical 是 `6.0.0-desktop-candidate.4`；24 个 ID 相同，UFQ-016–021 六条 SKU 问题在仓库版增加了来源文件限定，以消除 CSV 之间的歧义。不得用桌面旧版覆盖仓库 canonical，也不能据文件校验结果宣称 Golden 已核验。
- `上传验收规则.json` 包含 16 类文件场景和 6 类生命周期操作。跨页表格完整基线仍待人工固化；长 PDF 暂无固定性能基线；OCR Provider、隐藏 Sheet/公式策略、近重复与版本策略都要求明确运行策略。`HTTP 200` 不算导入成功，必须核对任务终态、索引/Registry/BM25 同代和 Active 发布。
- 辅助样本中的两个 HTML 是来源文本，`source_text.pdf` 是扫描件 OCR 对照而非扫描件证据，`sku_mangled_by_excel.xlsx` 是损坏前导零诊断样本；按 README/验收规则它们不在 28 份正式清单内，不能直接当 RAG Gold 证据。
- 该包 README 说明它用于上传/解析验收，不是生产业务 Golden。2026-11-13 只读对照桌面 manifest 与共享 Registry 的 `file_name + SHA256`：28 份中 23 份精确匹配 Registry，且这 23 条均为 `status=active`、`processing_status=success`、`chunk_count>0`；但 `active_generation` 与 `index_generation` 为空，向量、BM25 与 Registry 的代际一致性仍未核验。缺少的 5 份为：`UF-09-01 report.pdf`、`UF-13-01 doc_cn.pdf`、`UF-14-01 progit_en_501p.pdf`、`UF-14-02 progit_zh.pdf`、`UF-15-01 scanned_partial_fail.pdf`。本轮没有写共享数据库；补齐时应使用隔离或版本化测试 KB，并检查导入终态、索引代际和逐文件 chunk。

## 指定工作树管理端实跑（2026-11-12）

- 候选 `0901675d`（加班申请事实）通过服务端人工审核并发布为 RAG DatasetVersion `1.0.0`，1 条。该题引用冻结快照 `099eb64a26f54f5fa57ae185` 中的 `UF-04-02`，必需事实为事先申请及部门负责人确认。
- DatasetVersion ID：`50491ee1-784a-4ef4-a873-057af2de02b1`；SHA256：`489eaa141c406c2f4fe7c8027d37f9cbca686a6903d0b0fc99de25f524b91f87`。
- 从 DatasetVersion 配置并发布 RAG Suite `从知识库生成 2026/11/12 00:42:27评测方案` v1.0.0，答案评分与证据评分分别绑定。运行页保留超级管理员权限，选择 `rag_eval_kb` 和在线模式。
- Run `6fef6cf3-97cd-5e88-83c4-a0f6d8b9d171`：初次执行时宿主本地模型服务停止，retrieval 报连接错误，Gate `INVALID`。不能将这次 INVALID 当作模型质量失败。
- Run `ebe83b8b-fa0f-5e33-bd0e-89ee517f2cfd`：本地 BGE 服务恢复后真实执行，模型快照 `doubao-seed-2.0-mini`，Gate `FAIL`，有效检索及答案指标已生成。Recall@5=1.0、Precision@5=0.3333、context_noise@10=0.6667；答案 correctness=1.0、必需事实覆盖=1.0、citation_accuracy=0.3333。答案正确但重复五遍，且引用准确度和检索精度不足，属于质量失败，不是通过基线。
- 第二次运行语料快照 ID：`72db0b64-3795-5a5c-a49b-e2f4242455e3`。旧 Runner 成本未归因。逐题 UI 分开展示检索与答案评分。
- 三张浏览器证据保存在任务工作树 `.playwright-mcp/`，分别展示答案/来源、检索指标和答案指标。

## 多知识库生成与拆批实跑（2026-11-12）

- 从生成面板选择 `qa-synth-test`（1 篇）和 `rag_eval_kb`（23 篇），使用 20 题预设。提交预览为每库 10 题、每库拆 2 个 5 题任务、产生两个独立数据集；没有跨库混合。
- 首轮四任务：QA `8a2a377d-44c3-45b9-a0d3-5449378082f7` 成功、`a26278bf-f5f2-4ac4-86bd-b1b763db3d5a` 失败；RAG `044dd1dc-97b4-4971-98de-af57afdfcd13` 成功、`fea225f5-13b2-45f0-ac4c-1c25edef4dff` 失败。任务中心给出的失败信息为同一数据集版本已有不同内容。代码顺序是先创建 V2 候选、后写兼容导入资产，因此失败批次也留下候选；两个失败任务均不可重试，未发布。
- 20 条候选仍在人工核验池。来源清楚分为 `qa-synth-test` 文档 `8ec429fe14` 与 RAG 知识库中的 `UF-01-01`、`UF-02-01`、`UF-04-01/02`、`UF-06-01`；全部显示“必需事实待补充”，不能直接作为 Golden。
- 根因是同一 KB 的拆分批次共用相同 `dataset_key` 和 `version=1.0.0`，但内容哈希不同。前端现为同库批次分配 `1.0.0`、`1.0.1` 等导入版本，同时保留同一个 V2 `dataset_key`；批次失败时优先展示 API 的详细错误。
- 构建重启前 QA 单库 10 题复跑的两个任务 `46712c51-890f-4117-a749-52d21feb35f5`（SUCCESS）和 `630489a1-667c-41f6-86a3-e7e7d82010df`（版本冲突失败）又形成 10 条待核验候选。包括首轮双库 20 条、这次 10 条及修复后 10 条在内，目前本轮新生成 40 条候选，均未批准、未发布。
- 正式构建并重启指定工作树管理端后，`qa-synth-test` 的 10 题实跑拆为两个批次，Task `a18d7340-9295-4685-b3f9-ae1aba9c7485`、`ade17f99-289d-4531-a1c2-75088ab73ac5` 均 SUCCESS。10 条预览均保留同一测试库来源和原文证据，但须人工补齐必需事实；本轮没有采纳、批准或发布它们。
- 生成模型为 `doubao-seed-2.0-mini`。日志显示缺少该模型已生效 LLM 价格行，按注册表估价放行并标 `price_unknown`。双库 20 题组合在版本修复后尚未重跑。

## RAG 多跳题型与运行指标链路复审（2026-11-12）

- 已核实候选进入 V2 Golden 后，Runner 会将每个经过审核的 evidence anchor quote 投影为旧 RAG 数据格式的 `expected.evidence`；兼容数据集绑定再将 quote 变成 `relevant_snippets`，供检索 Recall 与逐题结果使用。
- 修复前，V2 adapter 不认识 Ragas 的 `multi_hop_abstract` 和 `multi_hop_specific`，会把题型改成 `single_doc`；旧 `multi_hop_success` 又只认 `multi_doc` / `reasoning` 与文档级字段，无法表示同一文档内的两个独立事实锚点。
- 当前代码保留 Ragas 题型，并按审核 evidence snippets 计算 `multi_hop_success`。两条锚点命中一条得 0.5、两条都命中得 1.0；旧题型可回退到 chunk/doc 级分数。V2 已管理绑定的回答题仍要求全部 `relevant_snippets` 命中才能通过检索门。
- 定向链路测试已通过 adapter → `validate_upload` → `normalized_cases`，同时断言 query type 与两条引用原文保持不变；单测覆盖 0.5/1.0 召回分值。真实模型的多跳检索/回答效果尚未重新运行，不能算多跳质量验收通过。
- 六类执行边界复核：V2 RAG 是可在线调用模型与知识库的质量路径；SQL、客服和产品 E2E 当前仅做契约检查并固定 Gate `INVALID`；旅游为离线质量路径；旅游供应商仅 fake Provider；Planner/Tool 尚未接入 V2。业务数据、真实 Runner 与 sandbox 缺件仍按六类表格记录。

## 脱敏失败 Trace 与证据

`.playwright-mcp/` 保存的是浏览器截图/验证产物，不是业务 Trace。新 Run 的 worker 日志引用 `d45d0e271178`，但 `GET /api/observability/traces/d45d0e271178` 返回 404；不能据此批准 Trace 回流。仍未得到可复用、脱敏且带完整输入/输出/业务上下文的失败 Trace。只有 Trace 是问题线索，不能直接充当参考答案或事实证据。

## 需业务方/运行环境补齐的具体资料

1. RAG：在隔离或版本化测试 KB 补齐上述 5 个上传压力文件，核对索引代际和 chunk；当前业务 Gold 仍只核验 1 条，新增候选须逐条对原文并补 29 条以上才能达到首期范围。
2. 客服：确认退款/取消/发货/投诉/安全规则版本，准备 `cs_eval_kb` 的合成受控语料及订单状态夹具；提供真实会话上下文执行与风险判定方式。
3. SQL：提供脱敏、可重置、只读测试 schema/数据快照，业务指标口径、Gold SQL 结果以及允许/拒绝的表列规则。
4. 旅游：提供供应商 sandbox、测试凭据注入路径、测试商品/库存和真实第三方调用授权/预算；指定预订、取消、改期结果证据。
5. Planner/Tool：确认接入 Runner 的工具集合、稳定版本、沙箱端点、调用/失败记录和副作用防护边界。
6. 产品 E2E：提供专用测试账号/租户、隔离业务数据、可重置流程、外部依赖沙箱及预期审计/Trace 断言。
7. 脱敏失败 Trace：提供上述边界下可读取的成功/失败成对 Trace 或确认管理端现有候选可用于回流；去除用户、订单、凭证及私密字段。

## 入口切换判定

当前存在旧版历史版本与运行记录，但没有确认通用“归档并退出默认入口”的管理能力。新基线验收前不改默认选择；届时优先采用可回滚的软弃用/入口过滤（保留 DatasetVersion/Suite/Run/Audit 原记录），先导出目录/hash 与运行快照作为回滚清单，再单独验收入口默认项。若服务端没有支持状态，则暂不写共享 DB 默认字段，先交付管理端显式选择和回滚步骤。

## 桌面文件测试资料与本轮真实 RAG Run 适用范围（2026-11-12）

- `C:\Users\wh\Desktop\企业ai平台\测试文件\上传解析测试\testset\README.md` 将资料定义为 28 份正式上传样本、16 类解析场景和 24 条事实问答，另有辅助样本；验收目标主要是解析完整性、来源页、表格、编码、OCR 和重复上传行为。README 明确指出它不是覆盖完整业务问答的 Golden 集，也尚不能替代实际上传/索引验收。
- 本轮真实运行使用的 `rag_eval_kb` 案例以 `no_title_oneline.txt` 为标注来源；Rerank Top 5 未命中此文件，但结果详情里的 `preamble.md`、`no_title.txt`、`interleaved.docx`、`v2_revised_2024.txt` 等 chunk 都重复含有该句。原始资料本身包含重复/近重压力样本，因此指定文档召回失败是有效的文件级检索信号；但它不能单独证明答案事实错误，按单一 `source_doc` 计算的引用分数也不能直接当成业务引用质量结论。
- 结论：当前运行确认了管理端真实运行、逐题结果、检索/答案指标和 Trace 可追溯；没有验收出可用 RAG 基线。后续需给每道业务题明确来源约束（指定文档/版本，或允许的等价证据集合），审核去重后的事实与证据，再分开报告“目标文档召回”和“引用文本是否支持答案”。如没有真实业务知识库原文，不得把上传压力资料或合成 fixture 宣称为生产业务真值。

## 阶段 7 判分耗时追溯补充（2026-11-12）

- 新 Run `b9f82014-a173-5259-a2f0-ac2ba34ca06f` 使用同一已发布一题样本。Trace `a6322bf400ef` 详情 API 返回 200、8 个 Span；管理端 Trace 详情显示“答案生成与评测判分”56.394 秒，剩余未埋点为 217 毫秒。新 Span 将之前约 55.8 秒无法归类的区间纳入评测处理阶段，但尚未拆分各项评分函数耗时。
- 逐题结果仍为质量 `FAIL`：答案重复五遍、答案事实覆盖和忠实度 1.0、`recall@5` / `chunk_recall` 0、引用准确度/完整度 0；Run 94 秒，Trace 的 10 秒 SLA 仍违反。运行状态 `success` 只代表评测代码正常执行，与 Golden Gate `FAIL` 是不同维度。
- 本次 UI 快照与截图：`.playwright-mcp/rag-trace-evaluation-span-20261112.yml`、`.playwright-mcp/rag-trace-evaluation-span-20261112.png`。此处证明运行和 Trace 详情页可打开，不证明测试资料适用于生产回答质量基线；逐题结果中的 Span 摘要一致性当时尚未核验。

## 阶段 7 逐题结果与 Trace 摘要一致性复审（2026-11-12）

- 对同一 Run `b9f82014-a173-5259-a2f0-ac2ba34ca06f` 复查逐题 API 后发现：逐题 `actual.trace` 保存的是 Trace 收尾前快照，只有 6 个 Span、40,930ms；Trace 详情则有 8 个 Span、70,208ms，缺答案生成/评测处理摘要。UI 中的 Trace 链接可打开不等于逐题摘要完整。
- 已在 RAG Runner 中于 `TraceCollector.finish()` 后刷新逐题结果里的 Trace ID、Span 数量、总时长和 Span 摘要，并将 checkpoint 写入移至该刷新之后。普通模式仍不序列化 Span 的 input/output/events/errors；完整 Trace 模式保留原有行为。同步异常仅记 warning，不覆盖已得出的评测结果。
- 定向测试覆盖摘要刷新、默认脱敏、full-trace 保留，以及“finish → sync → checkpoint”顺序；8 项通过，Ruff、`py_compile`、`git diff --check` 通过。补丁尚未重建或真实运行验证，结果↔Trace 一致性仍为 `UNVERIFIED`。
- 真实验收条件：新 Run 的逐题 `actual.trace.total_spans` 和 `spans[].span_id` 集合对应 Trace API 的 8 个最终 Span；`total_trace_ms` 与同口径 Span 耗时一致；默认逐题 API 不含 Span 正文；浏览器 Run 详情、逐题详情和 Trace 详情均可打开。

## 本机 Registry 源文件归拢与索引差异（2026-11-13）

- 详细清单和按库汇总位于 C:\Users\wh\Desktop\企业ai平台\测试文件\测试计划与验收文档\知识库盘点资料\活动文档清单.csv、知识库汇总.csv、盘点说明.md。逐条记录 Registry 哈希、本地哈希、实际文件大小、登记文件大小、KB、doc_id、后台可见性、原始路径、托管路径和只读索引成员状态。
- 管理端目前列出的 214 份活动文档都找到哈希一致的本地文件。另有 22 条 Registry 活动登记没有出现在这份管理端列表中，其中 18 份是 biz_product、policy_finance、policy_hr 的真实本地资料，4 份是 pytest 测试占位；不能把后台未展示的资料混进评测生成。
- 本机完整 Registry 活动文档快照共 236 条，232 条有匹配 SHA-256 的源文件，4 条通用 kb 测试占位没有有效源文件。测试占位需备份后通过受控的软删除/归档方式清理，不能直接删历史行。
- 整理前从原始来源归拢 110 份代码/测试文件副本、移动两份桌面业务文件、从 app 容器命名卷恢复 92 份精确源文件。整理前上传解析压力测试语料在桌面项目旧路径；24 条登记映射实际只对应 23 个物理文件。2026-11-13 起本机文件按测试/生产分类，当前目录见本文末的整理记录。
- 发现 policy_general 有 4 组同库同名同哈希的重复登记；help_center_mixed.md 被 rag_eval_kb 与 qa-synth-test 共用。另有 7 条登记 file_size 为 0，但实际文件有 98–200 字节且哈希相同，是登记大小字段异常。
- 向量、chunk 与 BM25 只读对账为 231/231/可读取；没有孤儿向量或 chunk/vector ID 差异，但缺 5 个登记 ID。员工手册原文件已找回，索引尚未建立；另外 4 个缺索引记录是临时测试占位。
- 结论仍是“源文件可追溯接近完成，向量索引未通过”。先完成本机数据库备份和占位登记归档、员工手册重建索引及索引代际核验；随后确认未展示业务 KB 是否应登记进管理端、是否保留为活动知识库。没有访问线上生产数据库。


## 本机源文件复核补充（2026-11-13）

- 桌面“企业文档”新增找到 18 份未登记文件，移动前后 SHA-256 一致。它们尚未进入 Registry，也没有作为评测集证据；2026-11-13 按已确认的测试样本、待确认样本和业务候选分放在 测试文件/客服与业务测试夹具、测试文件/待确认测试样本、生产文件/待业务确认资料。
- Registry 同名文件搜索找到 27 个外部精确副本，但对应的中心副本已存在。23 个在旧 WorkBuddy 测试语料，4 个是旅游爬虫导出文件；均已记录原路径与中央路径，没有重复搬运。源清单记录 231 个当前活动 ID，另将员工手册和 4 个无源 pytest 占位单独列为失败态。
- 修复缺索引前备份两个本机 PG 数据库和 197 行登记前像，修复审计 ID 45。只读复核：231 活动 Registry ID / 231 向量 / 231 chunk / BM25 可读，缺失、孤儿及 ID 集合差异均为 0。索引文本代际仍未验证；员工手册尚待受控重建。
- 管理端当前可见活动资产约 213 份，后端另有 18 份业务资料未展示（biz_product 2、policy_finance 5、policy_hr 11）；需要解决 KB 映射/权限后再把它们用于评测生成。18 份新候选和 18 份未展示业务资料是不同集合，不能混为已发布资产。
- 第一阶段仍需核对压力集 5/28 未映射、员工手册受控索引、索引内容代际、隐藏业务 KB 的管理端映射，以及经批准的生产脱敏文件样本。Ragas 混合生成与用例质量评审继续排在第一阶段通过之后。


## 隐藏业务 KB 状态核验（2026-11-13）

- biz_product（2）、policy_finance（5）、policy_hr（11）的 18 份本地原文均能与登记 SHA 对上，但本机 auth.knowledge_bases 状态为 inactive、audience=internal。三条审计记录由本机测试超级管理员 user_id=279 于 2026-10-22 执行 deactivate，理由字段为空。默认 KB 列表和评测语料冻结接口都会过滤停用 KB；生成抽屉又限定 audience=test，所以目前不能选。
- 没有自动恢复。要将它们作为 RAGas 评测源，需要确认恢复这三个内部 KB，并在 super_admin 快照和对客不可见边界下验证。否则阶段 2 先用当前 active、可授权的库，不能绕过停用状态。


## RAG 上传样本映射差额（2026-11-13）

- 正式 fixture 清单 28 条全部在本机且 SHA-256 匹配；当前 rag_eval_kb 仅 23 条活动映射，另外 5 条仍未上传/映射：UF-09-01、UF-13-01、UF-14-01、UF-14-02、UF-15-01。缺口正好覆盖跨页表格、超长 PDF 和扫描/OCR。
- 文件齐全不代表链路验收完成。需要通过管理端上传和切分流程补齐后，核对文档处理状态、chunk 数、版本和索引代际，再以这些真实文件支撑 RAG 评测基线。

## 2026-11-13 — 桌面资料双目录整理

- 本机资料现分为 C:\Users\wh\Desktop\企业ai平台\测试文件 和 C:\Users\wh\Desktop\企业ai平台\生产文件 两个主目录，子目录使用中文分类名称。原文件内容保留；项目 Docker 登记路径、向量库和分块库没有移动。
- 上传解析测试包位于 测试文件\上传解析测试；94 份文档量测试库、客服测试夹具、演示资料、评测计划及盘点清单分别位于测试文件的对应子目录。
- 旅游、公共制度、停用内部 KB 原文副本及 10 份未登记业务候选放在生产文件的业务分类目录，但都标注“待核验/待确认”。本机没有一批已获业务负责人生产验收的文件。
- 知识库源文件映射清单已更新本机副本路径。231 条活动登记的新路径全部存在且逐条 SHA-256 与清单一致；项目登记路径和原始来源字段保留原值。
- 本次只移动/更新本机文件资料和任务文档路径说明，没有改项目代码、运行代码测试或修改项目数据库。


## 2026-11-14 — 入库源文件、索引与管理端现状复核

- 当前共享开发库有 237 条活动 Registry 记录，按 14 类统计如下；管理端当前列出 219 条，其余 18 条属于已停用的内部商品、财务和人事库。

| 文档类型 | 活动登记数 | 文档类型 | 活动登记数 |
|---|---:|---|---:|
| compliance | 5 | contract_template | 1 |
| customer_data | 3 | faq | 16 |
| financial | 23 | general | 25 |
| legal | 23 | listing | 4 |
| policy | 46 | product_spec | 9 |
| security | 8 | sop | 21 |
| training | 4 | travel_guide | 49 |

- `travel` 知识库实际为 54 篇，桌面旧清单少登记了最近新增的 6 篇；新增原件已逐个核对 SHA-256、文件大小、chunk 数和成功 Trace，并写入本机明细清单。新 Trace ID：`a79c91d92d59`、`3eb8df94e1ca`、`3285cded4d38`、`666de09032f8`、`bf4a52b09ea5`、`363fa4cd94ca`。这只确认文件与索引结构，不代替事实核验或问答评测。
- 14 类每类最多抽 5 条，共 62 条。原件哈希 62/62 匹配；Registry/chunk/向量数量和关联 ID 一致；chunk 正文与对应向量文本一致；没有空 chunk、抽样到的错误 doc/kb/type/path 元数据或 chunk 数差异。抽样不等于 237 篇全量内容质量验收。
- 全库当前质量状态为 `pass=44`、`soft_warning=193`、`unknown=0`；管理端可见 219 篇中 44 通过、175 需复核、0 失败、0 未核验。62 条样本中 54 条软警告、8 条通过。软警告包含旧质量字段缺失、重复 chunk、历史过滤、质量异常、结构/标题不足、短文及噪音；原因可能重叠，不能算通过。
- 旧索引追溯字段存在系统性缺口：2628 条 `chunk_store` 记录没有 `chunk_id` 和 `chunk_metadata`，相应向量 metadata 缺 `source_format`；Registry 里另有 189 条 `source_format` 为空。62 条样本目前仍能证明分块和向量正文相同，但要恢复完整解析来源信息，需要先备份后设计补元数据或受控重建。
- Trace 留存：样本中 55 个旧 Trace ID 查不到详情，3 条测试夹具没有 Trace ID；近期 6 条旅游文档的成功 Trace 仍可查看。当前采样配置为 1.0，不能据现状认定丢失是采样造成，也不能用历史 ID伪造 Trace 详情。
- 发现一项未解决的索引孤儿：`policy_general` 有 2 条 `doc-existing` chunk_store 记录，无活动 Registry/向量/BM25 对应项，来源待查，未删除。另有 `UF-08-01` 的 parent chunk 使 chunk_store 比向量多 1 条，属于计数口径，不是索引缺失。
- 本机已把两份 HR 系统测试样例归到测试资料，并将 `ZZZ-s10-20261006033449.md` 标回测试文件路径；但 ZZZ 在项目里仍是 active、对客可见的旅游 KB 文档，需数据库备份后经正式归档流程处理。未修改共享知识库。
- 当前管理端浏览器证据：`.playwright-mcp/quality-audit-documents-2026-11-14.yml`、`.playwright-mcp/quality-audit-documents-dom-2026-11-14.json`。桌面明细表、KB 汇总表和盘点说明已备份后更新；细项与历史限制见“企业ai平台/测试文件/测试计划与验收文档/知识库盘点资料/盘点说明.md”。
