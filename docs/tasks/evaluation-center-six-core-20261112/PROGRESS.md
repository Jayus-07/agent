# 进度记录

## 2026-11-12 — 接手与基线盘点（交接状态）

- 工作树：`C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`。
- 分支：`codex/evaluation-center-ui-20261112`；接手时 HEAD：`8a85113a78ef0aa31cb8c466aaa9233625a662b8`。
- 保留交接时所有 11 个 tracked dirty files、4 个新评测 UI 文件及 `.playwright-mcp/` 产物；修改前核对 `git status` / `git diff --stat`。未 reset、删除或 stage 现有资产。
- 已读根 `AGENTS.md`、前端设计技能和测试指南；保留四页签及页面现有表格、分页、抽屉、详情。新增配置使用现有白卡、边框、颜色 token 和右侧 Drawer。
- 现有运行服务是共享 Compose project `agent`，主机可用内存约 0.93 GB；不启动第二套栈。浏览器 MCP 默认 profile 报占用，继续使用已登录 super_admin 的 Edge 做管理端只读资产核验。

## 关键发现

- 当前工作树前端：“配置评测”只跳到运行页；运行页仅载入已发布 V2 Suite。V2 API 已有创建 Suite 与发布 SuiteVersion，可由前端补齐闭环。
- RAG canonical 24 条皆未人工标记证据已核验；fixture 28 文件与共享管理端 `rag_eval_kb` 的 23 文档存在差异。管理端文档列表中 `faq_only.docx` 的 chunks 操作反馈没有处理轨迹。本轮不能先宣称有完整索引证据。
- V2 `sql/cs/e2e` 当前 contract-only；Gate 应为 `INVALID`。V2 travel/travel-provider 是离线/模拟 Runner；planner 不在 V2 supported modules。具体逐类缺口见 `DATASET_INVENTORY.md`。
- 共享管理端已发布：客服锁版 v2.2（324 条）、1 条拒答冒烟；另有 1 条 RAG 历史方案。客服虽显示 324/324，Gate INVALID；RAG 历史运行 FAIL/INVALID。大量空草稿均不作为基线。
- 尚未找到可直接用于验收的脱敏失败 Trace。`.playwright-mcp/` 是 UI 验证文件，不作为业务 Trace。

## 2026-11-12 — 对齐 origin/main 并验收 RAG 运行闭环

- 远端 main 快进后 HEAD：`4eaa1ad5089159b81bb622cb47384c6870e167ff`；该提交即 `origin/main`（包含 PR #459）。任务分支相对旧 feature remote ahead 1；没有 push。
- 对齐时保留原有 `.playwright-mcp/` 文件，并新增三张 RAG 运行截图。没有后端改动。
- 修复已批准候选无法发布的前端字段读取：`list_candidates` 返回的核验修订号在 `payload_json.verified_case_revision`，历史页改为兼容该字段并显示发布错误。
- 管理端将经人工核验的 `0901675d` 候选发布为 RAG DatasetVersion `1.0.0`，1 条；DatasetVersion ID `50491ee1-784a-4ef4-a873-057af2de02b1`，SHA256 `489eaa141c406c2f4fe7c8027d37f9cbca686a6903d0b0fc99de25f524b91f87`。来源快照 `099eb64a26f54f5fa57ae185`，证据文档 `UF-04-02`。
- 从“配置评测”发布方案 `从知识库生成 2026/11/12 00:42:27评测方案` v1.0.0，自动进入 Runs；保留 super_admin 权限。
- 首次运行 `6fef6cf3-97cd-5e88-83c4-a0f6d8b9d171` 因本机 8088 专项服务停止，在 retrieval 报 `Connection error`、Gate `INVALID`，未生成答案。按仓库脚本短时启动 BGE 两模型后，宿主与容器 `/healthz` 均返回 `ok`；运行结束后已停止该服务。
- 重跑 `ebe83b8b-fa0f-5e33-bd0e-89ee517f2cfd` 完成，Gate `FAIL`（可判定的质量失败，不是运行无效），模型快照 `doubao-seed-2.0-mini`，Runner `legacy-rag-v1`，语料快照 `72db0b64-3795-5a5c-a49b-e2f4242455e3`。答案正确性与必需事实覆盖均 1.0；Recall@5 1.0、Precision@5 0.3333、context_noise@10 0.6667、citation_accuracy 0.3333。模型回答重复同一事实五遍并给出五个引用，检索噪声和引用准确度导致该小集未通过。费用仍为旧 Runner 未归因。
- 本次 worker 日志中的 Trace `d45d0e271178` 经 Trace 详情 API 返回 404，不能作为可复用脱敏业务 Trace；本次 Run 与逐题证据仍可由 Run ID 查询。
- 浏览器截图：`.playwright-mcp/evaluation-center-rag-run-ebe83b8b-answer-evidence.png`、`...-retrieval-metrics.png`、`...-answer-metrics.png`。

## 2026-11-12 — 多知识库分批实跑与冲突修复

- 浏览器选取 `qa-synth-test` 与 `rag_eval_kb`，预设 20 题；预览均分为每库 10 题、每库 2 个 5 题批次、最终 2 个独立数据集。四个任务分别为 `8a2a377d-44c3-45b9-a0d3-5449378082f7`（成功）、`a26278bf-f5f2-4ac4-86bd-b1b763db3d5a`（失败）、`044dd1dc-97b4-4971-98de-af57afdfcd13`（成功）、`fea225f5-13b2-45f0-ac4c-1c25edef4dff`（失败）。
- 任务中心给出的失败原因均为“同一数据集版本已有不同内容，请增加版本号后再提交”。同一 KB 的拆分任务复用 `dataset_key + 1.0.0`；生成器先写 V2 候选、再写兼容导入资产，第二批因此终态失败但候选已进入待核验池。两库预览共 20 条，按各自 KB 保留来源和证据；没有自动批准或发布。Doubao 计价表缺少生效 LLM 价格行，费用按注册表估算并标记 `price_unknown`。
- 修复前端拆批清单：同一 KB 仍共用数据集键，但按批次生成独立导入版本 `1.0.0`、`1.0.1` 等；失败详情优先展示任务 API 的 `error` 字段。新增回归测试校验相同数据集键、不同版本及 5 题批次。
- 构建重启前另做 QA 单库 10 题复跑，`46712c51-890f-4117-a749-52d21feb35f5` 成功、`630489a1-667c-41f6-86a3-e7e7d82010df` 仍以版本冲突失败，另有 10 条候选进入待核验池。三次生成合计 40 条候选，均未批准或发布。
- 完成目标工作树正式构建后重启 `3211` 开发服务，选 `qa-synth-test` 10 题做 Doubao 实跑：两个 5 题任务 `a18d7340-9295-4685-b3f9-ae1aba9c7485`、`ade17f99-289d-4531-a1c2-75088ab73ac5` 均 SUCCESS。生成 10 条、源文档均为测试库文档 `8ec429fe14`；UI 仍标“必需事实待补充”，只在待核验预览中，未采纳/发布。该运行验证了拆批及版本冲突修复；双库 20 题需在修复后再做一次完整回归。
- 管理端页面逐批显示两个任务 SUCCESS、各自 Task ID、题数和进度；预览按来源库展示问题、参考答案、证据引用与待核验状态。截图已通过本轮 Edge 浏览器验收呈现，未另存为文件。

## 阶段状态

| 阶段 | 状态 | 证据/阻塞 |
| --- | --- | --- |
| 资产与覆盖盘点 | 初版完成，资料缺口已列明 | 题数与 Runner 边界已核对；新增目标 KB 实跑证据见 `DATASET_INVENTORY.md`。六类业务资料缺项仍需业务方补齐。 |
| 方案配置/发布入口 | 已实现并实测 | 选中 DatasetVersion → 配置并发布 Suite → 自动到 Runs；真实创建 DatasetVersion 与 Suite。权限边界未变。 |
| RAG 人工核验及 Doubao 真运行 | 闭环已跑通，质量未通过 | 同一已发布 1 条样本已两次真实在线运行；最新 `b9f82014…` Gate `FAIL`。答案与检索指标分开展示；不作为通过基线。 |
| 多 KB 搜索、预览、拆批 | 功能与单库拆批修复已实跑；双库修复后回归待做 | 双库 20 题第一次真实生成暴露版本冲突，候选来源/证据可见；修复后单库 10 题两批均 SUCCESS。仍需双库 20 题修复后再验一次整体闭环。 |
| 六类质量验收/默认入口 | 未完成 / 不切换 | 本轮只验收 RAG 单题；CS、SQL、Travel、Planner/Tool、E2E 按 `DATASET_INVENTORY.md` 执行边界继续推进。新基线未通过，不改默认入口。 |

## 本轮工程验证与后续实际验证

- 本轮前端：`npm test -- src/components/workbench/evaluation/proto/DatasetsTab.test.tsx`，6/6 通过；`npx tsc --noEmit` 通过；`npm run build` 通过（Next.js 15.5.28，49 个静态页生成完成）。构建后只新增错误详情测试，没有改运行代码。
- 受影响前端由本任务 worktree 的 Next dev server 提供，并在 super_admin Edge 浏览器完成配置、发布、运行和逐题检查；正式构建后重启 `3211` 以实跑生成拆批。当前 `3211` 正在监听，未改后端，因此未重建共享后端栈。
- 后端：若只改前端，不重建后端；若改 Runner/接口则从指定 worktree 运行 V2 定向契约测试并按仓库规则重建受影响服务。内存不足时不并发构建，明确阻塞。
- 后续 RAG：先处理重复生成/引用准确度及检索噪声，再基于有效文档/索引证据把集扩至 30–50；重跑后仍需固定 Suite/数据/KB/模型快照及 Run ID。当前有效证据仅支持这个 1 条 smoke set。
- 提交门禁：最终展示 Diff、HEAD、实际测试/浏览器证据、P0/P1 未验证项与 PASS/BLOCKED/UNVERIFIED；等待用户明确提交确认。

## 2026-11-12 — 九阶段继续实施：Ragas 混合生成与质量审核

- 按用户要求刷新远端 main；PR #460 合并后的 `d5cd19c53363ab9fea8e05f1dd2052a8080d380f` 已快进到任务分支。用 stash 临时保全现有差异和 `.playwright-mcp/`，恢复时无冲突；未改本地 `main`，未提交、未推送。
- RAG 题型契约新增 22 个可导入类型；Ragas 生成面板只开放其原生单跳具体、多跳抽象、多跳具体三类，并设 5/10/20 题预设、按 KB 分配预览与 5 题拆批。生成端逐题核验题型配额、答案和原文证据，不符合计划时整批失败；候选保留快照、KB、题型和计划来源，仍须人工批准。
- 依据 Ragas 0.4.3 的 `TestsetGenerator.generate_with_langchain_docs` 与三类 synthesizer 实现混合生成。官方文档展示了可传入题型分布、Ragas 构造知识图并生成候选后再审阅；管理端将每类题数落成明确整数配额，避免仅靠随机采样导致题型比例漂移。
- 新增 Ragas 候选质量门禁：必须补齐标准答案与必需事实，人工勾选五项质量检查，填写至少 20 字核验理由；多跳候选要求两条不同且服务端核验过的证据锚点。核验项被写入现有 `eval_annotations.changed_fields`，与 reviewer、结论、理由及证据共同追溯；手工、导入、反馈等其他候选审核流程不变。
- 定向验证：数据集/合成/V2 候选与审核 API 后端共 116 项通过（Ruff 整理导入后重跑仍通过）；管理端 `DatasetsTab.test.tsx` 7 项通过；`npx tsc --noEmit`、改动 Python 文件的 `py_compile`、Ruff 与 `git diff --check` 通过。
- 按项目规程执行 `devctl.bat rebuild -y`，由本工作树重建共享 `agent` 栈中后端、业务模拟与前端镜像；backend、business-mock `/health` 均健康，管理端 `/login` 和 Web `/login` 可用，Next.js 两端构建通过。没有新建 Compose project，本轮没有导入知识库或运行模型任务，也没有新增数据库迁移。
- 之后只按 Ruff 重新排序 Python imports；这次没有改逻辑。检查到 Windows 可用内存降至约 1.35 GB，低于项目 2 GB 阈值，因此没有为格式化差异再启动一次重建。若后续继续改后端运行逻辑，应在内存恢复到阈值后再按规程重建。
- 浏览器验收阻塞：MCP Chrome profile `C:\Users\wh\AppData\Local\ms-playwright\mcp-chrome-76a3013` 被另一会话占用，普通 tab 查询和独立 context 启动均返回 `Browser is already in use`。Edge 自动化清单能看到已登录的 `3211/evaluations/center?tab=datasets` 标签，但连接时报 `Debugger unattached`，无法读取页面快照。未尝试关闭或接管任何会话，也没有用账号文件获取凭据；因此本轮新增的审核清单虽然经过 Vitest/生产构建，但登录态页面验收仍为 UNVERIFIED。此前保存的 Run 截图对应旧版逐题结果，不作为本轮新 UI 的验收证明。

### 九阶段状态

| 阶段 | 当前状态 | 下一步 |
| --- | --- | --- |
| 1 原始资料清点与受控入库 | 已完成 28 份原始文件 SHA256 和 fixture 核对；资料目录、验收规则已盘点。受控知识库重建未做，不能用共享 KB 试错。 | 准备隔离知识库并逐文件核对文档、代次、chunk 和检索证据。 |
| 2 仓库/管理端资产盘点 | 已盘点六类仓库题集、管理端已发布资产、历史运行和执行边界；具体缺件见 `DATASET_INVENTORY.md`。 | 业务资料和运行沙箱到位后逐类抽样复核。 |
| 3 统一题型/用例契约 | RAG 的 22 个类型统一至导入/API/静态校验，并补充多跳双证据约束。 | 与非 RAG 模块的运行输入契约对齐时再扩展，不把 RAG taxonomy 误称为六模块共用 Runner 契约。 |
| 4 Ragas 混合生成 | 真实 Doubao/Ragas 生成 5 条候选，三种题型配额都落地；但两条标为多跳的题不能证明需要组合独立事实，已拒绝。 | 继续用有效、非重复资料验证混合生成质量；“配额含多跳”不等于产出合格多跳。 |
| 5 生成效果审核 | 已核对旧批准/拒绝历史；新事实—证据关联和引用留存通过自动化测试，审核历史旧记录也会如实标注缺少原文引用。新门禁尚未用真实候选完成管理端闭环验收。 | 新门禁需从本工作树重建后，再用真实 Ragas 多跳候选核验服务端拒批、批准及审核历史回查。 |
| 6 六类核心集与执行 | 只完成 RAG 单题真实运行，Gate `FAIL`；Ragas 混合生成有 3 条单跳候选通过审核、2 条多跳质量失败并拒绝。SQL、客服、旅游、Planner/Tool、E2E 未完成真实质量验收。 | 先取得有效多跳资料并补齐 RAG；其他模块按 `DATASET_INVENTORY.md` 补受控数据和真实 Runner。 |
| 7 管理端运行和追溯 | RAG 的 DatasetVersion→Suite→Run→逐题/Trace 页面可打开；但复核发现逐题结果是 Trace 收尾前快照（6 Span/40,930ms，Trace API 8 Span/70,208ms）。已修代码并通过定向测试，尚未部署复跑。 | 共享资源恢复后重建并复跑；核对结果 API 与 Trace API 的 Span 数量、ID、时长一致且默认摘要不含正文。其余五类运行链路仍未验收。 |
| 8 新基线与安全切换 | 未完成；目前没有全部通过的新基线，不切换默认入口。 | 六类门槛通过后，再验证可回滚软弃用/归档和历史可查。 |
| 9 训练/微调格式导出 | 未开始，保留为最后阶段。 | 只对批准并发布的用例实现导出和目标接口格式校验。 |

## 2026-11-12 — 阶段 5 链路复审：审核记录可查询

- 复审发现：Ragas 五项人工核验、理由和核验时的证据此前已写入 `eval_annotations`，但只有写入路径，没有管理端读取路径；因此页面上无法事后追溯批准或拒绝的依据。
- 改动：新增 `GET /evaluation/v2/candidates/{candidate_id}/reviews`；接口要求 admin 及以上，仓储查询同时使用当前 `tenant_id` 与 `candidate_id`。不改表结构，读取追加式审核记录中的审核人、结论、候选修订、核验项、证据、理由和时间。
- 管理端数据集编辑页增加已批准/已拒绝候选的审核记录面板；逐条展开后可查回五项 Ragas 检查、拒绝原因和当次原文证据。候选列表按 `dataset_key` 在服务端过滤并继续拉取所有分页；读取失败会明确显示，不再把接口错误显示成“没有记录”。候选批准仍只代表进入可发布列表，不代表已经发布 Golden。
- 链路复核：页面查询 → admin API → 租户与数据集范围 SQL → `eval_annotations` → 页面逐项呈现。后端 API/仓储定向测试 37 项通过，前端 `DatasetsTab.test.tsx` 8 项通过，`npx tsc --noEmit`、改动 Python 文件 `py_compile`、Ruff 和 `git diff --check` 通过；新增审核与质量测试已放入 `rag_smoke.yml`。
- 实际运行边界：本轮 Playwright 打开 `3211/evaluations/center` 后被重定向至 `/login`，`/api/auth/refresh` 返回 401；没有可用的 super_admin 会话，页面实测仍为 UNVERIFIED。Windows 可用内存约 1.42 GB，低于 2 GB，不能安全重建本次改动涉及的共享后端服务；当前运行镜像尚未包含新查询接口。
- 下一步：内存恢复至至少 2 GB 后，从此 worktree 重建受影响服务并检查健康；super_admin 会话可用后，实测批准/拒绝记录展开与 API 结果。完成以上两项前，阶段 5 仅代码和自动化验证通过，不标记为完整验收。

## 2026-11-12 — 阶段 6 链路复审：RAG 多跳证据覆盖

- 复审发现：V2 到旧 RAG Runner 的适配器没有接受 Ragas 新增的 `multi_hop_abstract` / `multi_hop_specific`，会把它们降级成 `single_doc`；旧多跳指标只认 `multi_doc` / `reasoning` 和文档级 `required_docs`，而 V2 Golden 的依据是审核过的 evidence quote。这样题型和“两个锚点都找回了吗”都没有进入可追溯逐题评分。
- 改动：V2 adapter 使用共享 RAG 题型集合，保留新题型；旧 Runner 对多跳按已核验 snippets 计算 `multi_hop_success`，无 snippet 的旧题继续兼容 chunk/doc 回退。检索门仍按现有证据完整度判定；V2 绑定题的 `chunk_recall` 必须满分才可通过，不会只因一个锚点命中就过门。
- 链路验证：测试真实执行 `_legacy_dataset` 的投影和旧导入校验，再走 `normalized_cases`，确认 `multi_hop_specific` 与两段 evidence quote 到了 Runner 输入；另外验证只召回一个锚点得 0.5、两个都召回得 1.0。此项验证不等于真实 Doubao 多跳 Run。
- 定向验证：RAG 多跳投影与评分、Runner fail-closed、审核质量/API、数据集扩展/Schema 与 CI workflow 契约合并运行，128 项通过；加强适配链测试后再次运行 2 项通过；非 RAG Runner、模块 Golden 契约和 Gate 无效状态另有 19 项通过。改动 Python 文件 Ruff、`py_compile`、`git diff --check` 通过。前端 travel / fake Provider 执行边界提示已有 9 项定向测试通过，RAG 多跳修复没有改前端。
- 运行范围复审：SQL、客服、E2E 仍为 contract-only 且 Gate `INVALID`；旅游是离线质量 Runner；travel-provider 只测 fake Provider；Planner/Tool 尚未接入 V2。配置页对 contract-only 显示 INVALID 提示，对旅行/供应商 fake 服务说明执行边界；新版镜像已重建，登录后 UI 实测仍待做。
- 服务重建：内存回升至 2.23 GB、共享栈检查完成后，从指定工作树执行 `devctl.bat rebuild -y`。backend、business-mock health 均为 `ok`，管理端和 Web 构建/启动成功，`/evaluations/center` 纳入 Next.js 构建。OpenAPI 含审核历史查询路由；容器内 Python 确认支持 `multi_hop_specific`，两段证据召回分数为 1.0。
- 浏览器验收：重建后重新访问 `http://localhost:3200/evaluations/center?tab=datasets`，实际跳转 `/login?redirect=/evaluations/center`，`POST /api/auth/refresh` 返回 401；保留的快照与控制台证据位于 `.playwright-mcp/page-2026-11-12T05-21-14-212Z.yml` 和 `.playwright-mcp/console-2026-11-12T05-21-10-865Z.log`。没有 super_admin 登录态，登录后的新审核历史和逐题结果页仍为 UNVERIFIED。服务和镜像问题已解除，下一步只需可用 super_admin 会话完成受保护页面验收。
- 下一步：继续逐类确认六个 Runner 的输入、快照、运行语义、逐题结果和 Trace；优先处理可在隔离数据上实证的 SQL/客服能力，缺少规则、受控数据或供应商沙箱时继续标注 INVALID / 待验收，不能用契约通过代替业务质量通过。

## 2026-11-12 — 阶段 7 链路复审：题型和多跳分数逐题呈现

- 复审确认：生成器把 Ragas synthesizer 题型写入候选 metadata；V2 Runner 从 metadata 读取后写入逐题 `actual.pipeline.query_type`；`multi_hop_success` 已进入逐题 metrics，详情抽屉会把它归入检索质量评分。
- 发现的展示缺口：详情抽屉能看分数，却没有显示这道题是单跳还是哪种多跳，审核人难以把生成题型与检索覆盖分数对应起来。
- 改动：详情抽屉新增 RAG 题型标签，把三个 Ragas 类型显示为“单跳具体 / 多跳抽象 / 多跳具体”；保留原始未知题型文本。RunsTab 回归测试同时断言“多跳具体”和 `multi_hop_success=0.5000` 可见。
- 定向验证：`RunsTab.test.tsx` 5 项通过，`npx tsc --noEmit` 通过。当前宿主可用内存约 1.57 GB，低于 2 GB 重建要求；该前端改动尚未进入生产管理端镜像，等待内存回升后只重建 admin，再进行受保护页面验收。
- Ragas 混合生成默认配额本身已包含多跳：5 题预设为单跳 3、多跳抽象 1、多跳具体 1；10 题为 5/3/2；20 题为 10/5/5。此处确认的是代码默认配额，不代表新配额已被真实 Doubao 运行验证。

## 2026-11-12 — 阶段 7 构建与浏览器复核

- 内存恢复到 2.1 GB 后，使用任务工作树的 Compose 配置只构建并重建共享 `agent` 的 `frontend-admin`，没有另建栈或重启其它服务。Next.js 15.5.28 构建通过，`/evaluations/center` 生成，49 个静态页完成；容器健康为 `healthy`，`/login` 返回 200。容器静态 bundle 中可查到新标签“RAG 题型”。
- Playwright 重新打开 `http://localhost:3200/evaluations/center?tab=runs`，登录保护将其转到 `/login?redirect=/evaluations/center`；`POST /api/auth/refresh` 返回 401。新页面快照与认证请求记录保存于 `.playwright-mcp/evaluation-center-login-after-admin-rebuild-20261112.yml` 和 `.playwright-mcp/evaluation-center-auth-after-admin-rebuild-20261112.yml`。
- 因无 super_admin 会话，题型标签和多跳分数的受保护页面实测仍为 UNVERIFIED；代码的 RunsTab 5 项测试与 TypeScript 检查通过，生产构建已通过。新 Ragas 配额尚无真实 Doubao 混合生成结果，不能声称运行中已经产出多跳题。

## 2026-11-12 — 阶段 7 链路复审：RAG Trace 入库与逐题索引

- 复审发现两处断点：RAG Runner 在逐题结果中生成了 `actual.trace.trace_id`，但没有调用 `TraceCollector.finish()`，因此 Trace 没有进入异步持久化写队列；V2 逐题索引只读取顶层 `actual.trace_id`，数据库索引和详情抽屉链接会丢失 RAG Trace ID。
- 改动：每题成功或异常结束后都收尾 Trace；运行异常写入 error span，质量不通过仍保持正常执行 Trace。Trace ID 写入数据库索引时兼容嵌套与原有顶层形状；对应测试加入 `rag_smoke.yml`。
- 链路复审：RAG Runner → `TraceCollector.finish()` → Trace Writer 异步队列；V2 Runner 从逐题 `actual.trace.trace_id` 取 ID → `eval_case_results.trace_id` → 逐题详情 API → 管理端详情抽屉 Trace 链接。测试用内存队列验证完成记录入队和错误状态，不触碰共享数据库。
- 定向验证：`backend/tests/evaluation/test_rag_eval_trace_finalization.py`、`backend/tests/evaluation_v2/test_runner_token_accounting.py`、`backend/tests/evaluation_v2/test_rag_query_type_preservation.py` 共 9 项通过（24.44s）；`py_compile`、Ruff 与 `git diff --check` 通过。
- 运行环境：共享 `agent` 栈为 21 个运行容器，Windows 可用内存 1.89 GB，低于仓库 2 GB 重建门槛。后端唯一生效方式是 `devctl.bat rebuild`，本轮未重建；容器内仍是旧代码，因此部署和真实 Trace API 查询记为 UNVERIFIED。不得将旧 Run 的 `d45d0e271178` 404 当成修复验证。
- 浏览器状态：此轮没有前端代码改动；既有 Playwright 证据表明评测中心会被登录保护转至登录页，refresh 返回 401。没有 super_admin 登录态，逐题 Trace 链接页面仍未实测。
- 下一步：可用内存恢复至至少 2 GB 后，从本工作树按 `devctl.bat rebuild -y` 重建并检查 backend health；随后用隔离 KB 生成一次新 RAG Run，检查逐题索引 ID 与 Trace 详情 API。登录态页面验收仍需 super_admin 会话。

## 2026-11-12 — 阶段 7 采样保留边界复审

- 继续检查持久化链后发现：Trace Writer 会按 `TRACE_SAMPLING_RATE` 丢弃部分成功 Trace；若部署采样率低于 100%，RAG Run 的逐题结果可能仍引用一个被抽样丢弃的 Trace。
- 改动：`workflow_name="rag_eval"` 的 Trace 现在始终入队，普通成功 Trace 仍按全局采样率；错误 Trace 原本就始终保留。补充 Trace 模型文档，并加入采样率为 0 时评测 Trace 留存、普通 Trace 仍可被采样的回归测试；测试已列入 `rag_smoke.yml`。
- 定向验证：Trace sampling、Trace Writer consumer group、RAG Trace 收尾、V2 嵌套 Trace ID 和多跳适配五个测试文件共 11 项通过（24.56s）；Python `py_compile`、Ruff 与 `git diff --check` 通过。
- 镜像状态：采样保留代码是在上一轮 rebuild 后追加的，当前 `app`/`agent-worker` 镜像尚不含此变更。宿主可用内存已降至 1.77 GB，按共享栈规则不再启动构建；Trace 采样变更部署标为 `UNVERIFIED`。上一轮容器探针只验证旧镜像中的收尾和嵌套 ID 取值。
- 下一步：内存恢复到 2 GB 以上后，从此工作树重建并核对 app/worker health 与代码版本；super_admin 会话和隔离 KB 可用后再执行新 RAG Run，验证 Trace 真正入库、可查且与逐题索引一致。

## 2026-11-12 — 阶段 7 Trace 修复重建与容器复核

- 补充更正上一条记录中的运行镜像状态：随后从本工作树完成 `devctl.bat rebuild -y`，返回码 0；构建标记为 commit `d5cd19c`、`dirty=true`。后端与管理端镜像均更新，共享 `agent` 栈未新建。
- 低内存操作记录：Compose `--parallel 1` 令容器更新逐项进行，但 BuildKit 仍并行处理多目标镜像（本次目标均命中缓存）。构建前宿主可用内存 1.99 GB，重建后 1.82 GB；所有服务启动后恢复健康。后续应在内存更充足时再做需要重新执行代码层的全组构建。
- 服务证据：`app /health` 返回 `status=ok`、migrations/schema/RAG/Redis 健康；`business-mock /health` 返回 `ok`；管理端 `/login` 返回 200。`devctl` 输出 backend health-200、business-mock health-200、admin/web 已就绪。
- 容器代码探针：在 `app` 和 `agent-worker` 容器内分别调用 RAG Trace 收尾 helper 与 V2 结果索引 helper；两边均确认异常 Trace 会标 `error`，嵌套 `actual.trace.trace_id` 会变成结果索引 ID。测试进程内用 Mock 截断 Trace 写入，不触碰共享数据库。
- 浏览器与业务边界：此前 Playwright 仍显示无 super_admin 登录态，认证 refresh 为 401。当前 rebuild 后只检查了登录页可访问，没有运行受保护的结果页；也没有在本轮运行新 RAG Run。因此“Trace 已持久化并可被详情 API 查询”仍为 `UNVERIFIED`，不能以容器探针代替真实 Run。
- `dev-svc.bat` 的串行参数只为此次调用临时加入，已全部恢复；`git diff -- dev-svc.bat` 为空。下一步需要登录态和隔离 KB：新跑一个 RAG 样本后核对结果表 Trace ID、Trace 详情 API 与管理端详情链接。

## 2026-11-12 — 阶段 5/6 运行复审与多跳审核补强

- 使用现有 super_admin 隔离浏览器，在 `http://localhost:3200/evaluations/center` 复核真实 Ragas 任务 `c414594c-60c5-4858-819c-39f99b1384af` 的审核记录：5 个候选中 3 个单跳通过人工核验，2 个标为多跳的候选被拒绝。两条多跳题都引用重复业务规则，问题可由单句作答，未被作为合格多跳题发布。
- 浏览器复核先发现拒绝记录没有引用，再确认旧页面缓存了重建前响应；刷新后 API `GET /candidates/{candidate_id}/reviews` 返回服务端从候选快照找回的原始两条引用，页面显示“拒绝时的证据引用（不表示证据已通过核验）”及明确拒绝理由。既有审核注释未被改写；这是读时回退。快照记录在 `.playwright-mcp/page-2026-11-12T09-50-43-617Z.yml`、`.playwright-mcp/page-2026-11-12T09-51-06-812Z.yml`。
- 复审结论：Ragas 默认题型配额确实生成了多跳标签，但这次两条多跳候选质量均失败；3 条单跳候选仅是已核验候选，仍未发布成版本，也不是混合基线。当前目标知识库的重复/碎片化内容不足以证明多跳生成有效，必须继续使用独立事实资料验证。
- 新增的候选审核契约要求 Ragas 每条必需事实都关联至少一条证据数组引用；服务端先按租户权限核验每条引用，再将事实下标解析成不可变锚点 ID 并写入审核记录。多跳需至少两条去空白后不同的必需事实，且覆盖至少两个服务端核验锚点；前端在批准按钮启用前逐条收集关联。审核历史现在可显示事实到锚点的对应。服务端还把通过锚点核验的引用片段随批准注释保存，便于事后查看。拒绝分支不执行证据核验，继续仅记录原始引用。
- 没有新增表或迁移；普通手工、导入与非 RAG 审核不要求该字段。旧批准记录既有数据没有引用文本，本轮不回填、不改写；管理端改为展示锚点并说明缺失，避免把锚点元数据说成原文引用。
- 定向验证：`test_api_contract.py` + `test_review_quality.py` 共 43 项通过；`DatasetsTab.test.tsx` 10 项通过；`npx tsc --noEmit`、改动 Python 文件 `py_compile`、Ruff、`git diff --check` 均通过。测试首次发现重复事实空格归一化边界，修正后重跑全过。
- 管理端实际页面已验收上一段“拒绝引用可查回且标记未核验”；本次新增事实—锚点表单仍未部署/浏览器验收，因为代码修改发生在当前镜像构建之后。最近宿主可用内存约 1.82 GB，低于仓库 2 GB 构建门槛，不启动下一次重建。当前健康镜像仍不含本段新增绑定门禁。
- 下一步：内存恢复到 2 GB 以上后，从本工作树重建受影响后端和管理端；用真实 Ragas 多跳候选验证缺少映射时服务端拒批、映射后审核历史可查。此验证不得批准或发布当前已拒绝的候选。

## 2026-11-12 — 阶段 5 审核证据映射门禁部署与链路复审

- 资源检查：停止本任务临时 Next 预览服务 `3211` 后确认端口已释放；宿主空闲内存回到 2.62 GB。共享 Compose 仍为唯一运行栈，构建前核对 21 个容器和内存使用，没有停止或清理其他工作树服务。
- 部署：从指定工作树执行 `devctl.bat rebuild -y` 成功。后端受影响镜像和两个前端镜像均重建；Next.js 生产构建、类型检查、49 个静态页生成完成。管理端 `3200/login`、Web `3100/login`、后端 `/health`、business-mock `/health` 均返回 200；Compose 中 21 个容器运行，所有配置了 healthcheck 的容器为 healthy。重建后空闲内存约 2.02 GB。
- super_admin 浏览器复核：真实打开 `3200/evaluations/center` 和“统一 RAG 评测知识库”数据集编辑页；页面列出既有 3 条批准与 2 条拒绝记录。批准的旧审核记录正确标注“已核验锚点；本记录未保存原文引用文本”，拒绝记录显示审核理由和“拒绝时的证据引用（不表示证据已通过核验）”。页面没有执行批准、发布或生成操作。快照为 `.playwright-mcp/page-2026-11-12T10-21-49-904Z.yml`、`.playwright-mcp/page-2026-11-12T10-24-09-909Z.yml`、`.playwright-mcp/page-2026-11-12T10-24-14-692Z.yml`。
- API 链路：super_admin 页面认证请求下，候选列表与审核历史查询返回 200；使用不带认证头的只读诊断请求返回 401，之后附带页面令牌重试成功。两条控制台 401 来自该诊断探针；页面加载及受认证 UI 请求未报错。
- 代码门禁验证：后端审核 API/质量校验测试 43 项、前端 `DatasetsTab.test.tsx` 10 项通过；`npx tsc --noEmit`、相关 Python 文件 `py_compile`、Ruff 与 `git diff --check` 通过。工作树仍有未提交差异，未提交、未推送。
- 链路评审结论：租户内读审核记录、拒绝引用回退的“不可信”标识、旧批准记录缺少引用文本的如实提示均已在运行管理端复核。事实—锚点的新增审核表单、拒绝映射时的服务端阻断以及新批准记录保存原文引用尚无未审核 Ragas 候选可供真实 UI 操作；目前由 API/质量单测与生产构建覆盖，真实候选映射/批准审计仍为 `UNVERIFIED`。现有 5 条 Ragas 候选仍是 3 批准、2 拒绝，均未发布，质量基线仍不通过；不得切换默认入口。

## 2026-11-12 — 阶段 6/7 真实 RAG 运行与 Trace 复审

- 使用已登录 `super_admin` 管理端运行已发布 RAG 小集版本 `50491ee1-784a-4ef4-a873-057af2de02b1`，Run `5b76c3eb-86a1-5810-8a95-67b22146c6f0`，真实模型 Doubao、真实本地 BGE embedding/rerank。运行完成 1/1，Gate 为 `FAIL`，不得作为新基线或默认入口。
- 逐题结果与 Trace API 均通过管理端链路读取（结果详情和 Trace 详情 API 均 200）。题目预期文档为 `no_title_oneline.txt`，最终 Rerank Top 5 未包含它；指定文档召回 `recall@5=0`、`chunk_recall=0`。但返回的其他文件也含有相同事实，答案事实覆盖和忠实度均为 1.0；答案把事实重复五遍，按当前“必须引用指定文件”的严格口径，引用准确度和完整度均为 0。该资料盘的 README 将其定义为 28 份、16 类的上传/解析压力样本，其中包含刻意近重/重复材料；因此本次 `FAIL` 能证明运行链路和严格来源检查在工作，不能单凭它判定真实知识库的综合回答质量。后续需先核实业务场景是否要求精确指定来源，再评审去重语料上的引用口径。Rerank 对 35 个候选成功返回 Top 5，用时约 12.2 秒，之前 4 秒超时问题已解除；全题仍耗时约 77.7 秒并违反 10 秒 SLA。
- 浏览器证据：运行详情快照 `.playwright-mcp/page-2026-11-12T11-34-26-005Z.yml`、逐题结果快照 `.playwright-mcp/page-2026-11-12T11-34-31-151Z.yml`、Trace 详情快照 `.playwright-mcp/page-2026-11-12T11-35-06-862Z.yml`。页面显示 `1/1 · 通过 0`、答案与检索指标以及 Trace `293a226c9cf4`。Trace 汇总 `LLM=1`、检索/重排 `4`；Doubao 用量只归属生成调用，BGE Rerank Provider 单独展示。
- 链路复审还发现一个代码缺陷：完整 `FAIL` 结果已进入当前用例的 `result_holder`，断点保存却误读尚未由外层 Runner 收集的 `results[-1]`，引发 `IndexError`。RAG Runner 随后又补记一条泛化的 `retrieval` 错误，造成“有完整质量结果却显示执行异常”、Trace 多一个错误 Span；根因不在检索阶段。已改为仅从当前用例结果 holder 写入 checkpoint，`fail` 质量结果照常保存，`error` 运行无效结果不保存，并加回归测试。
- 定向验证：`backend/tests/evaluation/test_rag_eval_trace_finalization.py` 5 项通过（24.17 秒）；改动 Python 文件 `py_compile`、Ruff 均通过。该测试文件已列入 `.github/workflows/rag_smoke.yml` 定向清单；尚未重建包含此修复的镜像。
- 资源/部署：共享 `agent` Compose 栈仍为 21 个运行容器。此刻 Windows 可用内存约 1.95 GiB，低于项目 2 GiB 重建门槛，按仓库规程暂不执行 `devctl.bat rebuild -y`，不另起隔离栈、不停其他工作树服务。当前共享镜像含 Rerank timeout 15 秒与此前 Trace DTO 修复，但不含本条 checkpoint 修复。
- 下一步：可用内存恢复到安全阈值后，从本工作树重建并核对健康状态；然后用同一已发布小集复跑一次，确认结果仍如实为质量 `FAIL`、不再冒出执行错误 Span，且结果、Trace、审计关联一致。之后先整改检索证据和答案引用质量，再扩展 RAG 题集；六类新基线和默认切换仍未通过，训练导出保持最后阶段。

## 2026-11-12 — 阶段 7 评测判分耗时 Trace 复审

- 上一条新 Run `c0e54b02-3339-50fb-b192-0b0981cf9067` 已确认修复 checkpoint 后不再产生“RAG 评测执行异常”Span；但 Trace 的约 55.8 秒落在检索 Span 之外，详情页只能显示成未埋点区间，无法区分答案生成与判分处理。
- 改动：新增 `SpanKind.EVALUATION`，RAG Runner 在检索 Span 收口后开始“答案生成与评测判分”Span，覆盖生成、指标计算及逐题结果整理；用例正常完成时该阶段为 success，运行异常才标 error，质量 `FAIL` 不会被误记成执行错误。同步更新 Trace 模型文档并加入定向测试。
- 定向验证：`backend/tests/evaluation/test_rag_eval_trace_finalization.py` 6 项通过（24.37 秒）；`py_compile`、Ruff、`git diff --check` 通过。测试文件已在 `.github/workflows/rag_smoke.yml`。
- 构建前确认共享 `agent` Compose 栈 21 个容器在运行、宿主可用内存 2.09 GB；从本任务工作树执行 `devctl.bat rebuild -y` 成功，构建标记 `d5cd19c`、`dirty=true`。backend、business-mock 和管理端登录页均返回 200；app 与 agent-worker 容器都能导入新增的 Span helper 和 `SpanKind.EVALUATION`。
- super_admin 管理端再次运行同一已发布 DatasetVersion `50491ee1-784a-4ef4-a873-057af2de02b1` 和 Suite `38addde3-2bde-415e-a893-d9ffc6c7cf31`。Run `b9f82014-a173-5259-a2f0-ac2ba34ca06f` 完成 1/1，Gate 仍为 `FAIL`，Run 94 秒、逐题 70,199ms；答案仍重复五遍，required fact coverage/faithfulness 为 1.0，检索 `recall@5` 与 `chunk_recall` 为 0，citation accuracy/completeness 为 0。这些是当前压力语料和来源约束下的质量失败，不作为业务基线。
- Trace API `GET /api/observability/traces/a6322bf400ef` 返回 200：Trace 状态 `success`（表示执行无运行异常），总耗时 70,208ms，8 个 Span；检索阶段 13,601ms、Rerank 12,095ms、Doubao 2,098ms，新增“答案生成与评测判分”56,394ms，剩余未埋点仅 217ms（页面显示 0%）。浏览器详情页显示新阶段、`SLA 10000ms` 违反和逐题 Trace 链接。证据见 `.playwright-mcp/rag-trace-evaluation-span-20261112.yml` 与 `.playwright-mcp/rag-trace-evaluation-span-20261112.png`。
- 评审结论（第二轮逐题摘要核对后修正）：Trace API 和管理端 Trace 详情链已闭合，但逐题结果中缓存的 Span 摘要仍是 Trace 最终化前的快照；当时逐题 API 返回 6 个 Span / 40,930ms，而 Trace API 为 8 个 Span / 70,208ms。因此不能声称结果与 Trace 摘要已闭合。运行状态 `success` 与评测 Gate `FAIL` 分开，SLA 仍未达标。该 span 只归因“答案生成与评测判分”整体，尚不能把 56.4 秒拆到某个具体指标函数。六类质量基线未通过，不切换默认入口；训练/微调导出保持第 9 阶段。
- 已补代码：Trace 最终化后同步逐题结果里的 Span 摘要，并把断点检查点移到同步之后，避免恢复文件仍保存旧快照。8 项定向测试、Ruff、`py_compile` 和 `git diff --check` 通过；该补丁尚未重建、也未用新 Run 验收，当前状态为 `UNVERIFIED`。
- 部署阻塞复核：共享 Compose 栈仍有 21 个容器；Windows 可用内存约 1.57 GB，`agent-worker` CPU 约 98%，负载仍活跃。未重建、未触发新 Run，以免与共享任务争用资源。已有镜像只包含前一轮 `d5cd19c` 构建，不含本次 Trace 摘要同步和 checkpoint 顺序修复。
- 下一步：待共享 worker 空闲且资源恢复后，从本工作树按仓库命令重建并检查健康状态，再运行同一已发布样本；核对结果 API 与 Trace API 的 Span 数量、ID、时长一致且默认摘要不含正文。通过后再用可去重且来源约束明确的资料复核 RAG 检索与引用质量；并按 `DATASET_INVENTORY.md` 为客服、SQL、旅游、Planner/Tool、产品 E2E 补齐真实受控资料或沙箱，再逐类接入/验收 Runner。缺业务材料的部分保持 `INVALID` / `UNVERIFIED`，不造题凑数。

## 2026-11-13 — 共享栈重建故障诊断

- 资源恢复后从指定工作树执行 `devctl.bat rebuild -y`。11 个后端镜像构建成功，使用了依赖缓存；Compose 启动阶段因 `agent-redis-1` 不健康退出，命令返回失败。最新 Runner/审核代码已进入本机镜像，但 app 和 worker 容器停留在 `Created`，因此尚未运行新代码验收。
- Redis 日志明确报 `/data/appendonlydir/appendonly.aof.65.incr.aof` 格式损坏。先将共享卷完整复制到 Docker 备份卷 `eval-redis-aof-backup-20261113`；修复前原卷 9 个文件的 SHA256 与备份逐项一致。再将备份复制到隔离检查卷，`redis-check-aof` 确认基础 AOF RDB 有效（211 个键），增量 AOF 仅末尾 93 字节损坏；在隔离副本上修复后 AOF 校验通过，临时 Redis 能正常启动并响应 `PONG`。
- 共享卷修复：先停止处于重启循环的 Redis，只对 `/data/appendonlydir/appendonly.aof.manifest` 执行经副本验证的 `redis-check-aof --fix`，将增量文件从 1,633,900 字节截到最后有效偏移 1,633,807 字节；修复后对共享卷再运行无 `--fix` 检查，所有 AOF 文件和 manifest 均有效。备份卷保留，未删除。
- 恢复后 `agent-redis-1`、PostgreSQL、RAG service、MCP service、business-mock、redis-broker、auth-redis、app、agent/report/maintenance/rag-index workers、beat、CS dispatcher 和三个前端均健康；`db-migrate` 退出码 0。`devctl.bat start backend` 成功，后端与 business-mock `/health`、admin/web `/login` 返回 200；app 与 agent-worker 均能导入 `_sync_eval_trace_result`，确认启动的是本轮新构建镜像。
- 有效配置复核：主 Redis `maxmemory=192 MiB`、`allkeys-lru`、AOF `everysec`，并启用 `save 3600 1 300 100 60 10000` RDB 快照。恢复后 `aof_last_write_status=ok`、`rdb_last_bgsave_status=ok`。配置适用于可重建的开发热状态，不代表生产容量/高可用/备份方案。
- 下一步尝试启动本地 BGE embedding/rerank，预热成功；新 Playwright context 跳转管理员页面时被重定向至登录页。按交接要求未重读账号文件，未触发新的评测 Run；BGE 服务已停止释放内存。结果 API 与 Trace API 的新版本一致性仍为 `UNVERIFIED`。
- 安全记录：诊断时曾误将容器环境中的 Redis 凭据输出到工具结果；没有把凭据写入文档或再次复述。凭据轮换尚未执行，应安排轮换并重启依赖它的服务。
- 当前状态：共享栈已恢复，最新代码已部署且服务健康；阶段 7 真实逐题/Trace 复验、阶段 5 新证据门禁浏览器验收和多库拆批双库复跑仍待 super_admin 登录态。按 `TASK.md` 当前排期推进。

## 2026-11-13 — Redis 调优与恢复复核

- 只读指标确认主 Redis `maxmemory=192 MiB`、容器限额 `384 MiB`、`allkeys-lru`；峰值用量达到 `191.43 MiB`。拆分 Stream 后发现 Trace 写队列约 9,904 条且 Consumer Group `pending=0/lag=0`，网关日志流 79,800 条；客服补偿 Stream 本身为空，但 Consumer Group 累积约 150,691 个消费者元数据，均无 pending。主 Redis 曾累计 `evicted_keys=200`，且 Trace Stream 后来不再存在；由于上一轮该 Stream pending/lag 为 0，结合消费者只在 PostgreSQL 持久化成功后 ACK，当前证据支持消息已落库后队列副本被淘汰，但无法从累计计数还原 200 个键逐一淘汰顺序。
- 根因修复：客服补偿默认使用稳定 consumer name，避免 15 秒定时任务每次制造新消费者；Trace 写入 PostgreSQL 成功并 ACK 后删除 Stream 队列副本；网关日志在 PostgreSQL 批次提交后先写消费位点，再按位点裁掉旧 Stream 副本。三处变更均有定向测试并加入 `rag_smoke.yml`。
- 安全回收：只删除闲置至少 2 分钟且 `pending=0` 的 150,690 个旧消费者，实际删除的 pending 消息数为 0；保留稳定消费者。Redis 用量从约 54.34 MiB 降到 32.63 MiB。因网关消费位点曾被全键 LRU 淘汰，服务从保留的流开始幂等回放；新逻辑确认游标追到流尾，流长度从 79,800 收敛到 12。
- 配置调优：4C8G 主 Redis 改为 `volatile-lru`，只允许带 TTL 的缓存参与淘汰，保护无 TTL 的 Stream/位点；仍保持 `192 MiB` 上限、`appendonly yes`、`appendfsync everysec` 和现有 RDB 保存计划。broker/auth Redis 继续使用 `noeviction`。备份卷 `eval-redis-aof-backup-20261113` 保留。
- 一次单服务重建曾漏带 4C8G Compose 覆盖文件，发现后已立即使用仓库根 Compose 加 4C8G 覆盖文件重建 Redis；最终核对生效策略 `volatile-lru`、`maxmemory=192 MiB`、容器限额 `384 MiB`，未使用 `--remove-orphans` 或删除数据卷。AOF 重写成功：当前文件约 22,852 字节、基线 8,735 字节，`aof_last_write_status=ok`、`aof_last_bgrewrite_status=ok`、`rdb_last_bgsave_status=ok`；Redis `used_memory≈1.54 MiB`、RSS≈12.32 MiB，重启后淘汰数为 0。
- 定向验证：客服 outbox、Trace writer、网关 ingest 共 17 项通过；`py_compile`、Ruff、`git diff --check`、合并后的 Compose 配置校验通过。按 `devctl.bat rebuild -y` 从本工作树重建后端并重启 Redis；Redis、PostgreSQL、RAG/MCP、业务模拟、Worker、Beat、Dispatcher 和前端共 21 个容器运行，配置了健康检查的容器均 healthy；后端/业务模拟健康 API 与管理端/Web 登录页返回 200。
- 评测排期未停：Playwright 当前仍在 `/login?redirect=/evaluations/center`，未读取账号文件，也没有触发新 Run。因此阶段 7 的结果/Trace 新 Run 一致性、阶段 5 新门禁浏览器操作和双库拆批复跑仍为 `UNVERIFIED`；Redis 凭据轮换也仍待安排共享服务配置窗口。新 RAG Run 不得用 17 项工程测试或服务健康代替。

## 2026-11-13 — Redis 后续排期复审

- `git fetch origin` 后 `origin/main` 仍为 `d5cd19c53363ab9fea8e05f1dd2052a8080d380f`，与当前 HEAD 一致，没有新的 main 提交需要对齐。任务分支的 5 个本地提交、未提交实现和 `.playwright-mcp/` 均保留；未提交、未推送。
- 复核共享栈：显式根 Compose + 4C8G 覆盖配置通过；21 个容器运行，配置了健康检查的容器均 healthy；backend、business-mock、管理端 `/login` 返回 200。通过容器内已配置的认证方式查询 Redis、未输出凭据：`volatile-lru`、`maxmemory=192 MiB`，当前用量约 1.61 MiB、历史峰值约 190.92 MiB；AOF 写入/重写和 RDB 最近保存状态均为 `ok`。
- 复审混合生成、人工质量审核、V2 API 与题型适配：后端定向测试 92 项通过；Trace 收尾/采样与 RAG 题型适配测试 11 项通过；管理端 `DatasetsTab`/`RunsTab` 定向测试 15 项通过。初跑发现审核列表的 React duplicate-key 警告，根因是测试 mock 将相同候选返回给待审核、已批准和已拒绝查询；已按状态修正夹具，重跑 15 项后警告消失。Vite 仍输出既有 `configLoader: native` 兼容性提示，不影响测试结果。
- 受保护页面的两个 Playwright context 仍被转到 `/login?redirect=/evaluations/center`。因此新证据审核、逐题结果↔Trace 新 Run 一致性以及双知识库 20 题拆批重跑仍是 `UNVERIFIED`；以上 118 项工程测试和服务健康均不替代管理端真实验收。下一步需要可用的 `super_admin` 管理端登录态后完成这些操作，再复核固定 Run ID、逐题证据和 Trace API。

## 2026-11-13 — Ragas 生成预算超限诊断

- 管理端选择 `qa-synth-test` 与 `rag_eval_kb`，预设 20 题（单跳具体 10、多跳抽象 5、多跳具体 5），预览拆成 4 个各 5 题任务、两个独立数据集。首个任务 `14922805-1b90-4e81-b48b-15f69b3395b8` 失败，接口报 `budget_exceeded` / `request_fallbacks`；其余 3 批没有提交，候选没有批准或发布。此状态归为运行无效，不是题目质量失败。
- 预算账本显示全局单请求上限为 32,000 Token、¥0.50、重试 2 次、模型回退 1 次；Ragas 生成任务把重试上限覆写为 4 次，fallback 上限仍为 1。该请求累计约 15,191 Token、估算 ¥0.043466；Token 和费用均未超限。拒绝发生在模型代理尝试预占第二次 fallback 时，说明当前请求此前已用过一次模型回退；预算拦截发生在新的备用模型调用之前。不要通过提高全局限额掩盖故障。
- Trace `a2b06ec86791` 记录 Doubao 的 `OpenAIConnectionError`；Trace `4f6f3be5c472` 记录 BGE embedding 多次失败。失败记录没有 HTTP 状态或底层 socket 信息，因此可确认当时上游连接不稳定，不能进一步断言是断网、超时还是供应商故障。embedding 错误本身不增加 LLM fallback 计数，但也会使生成任务无法可靠完成。
- 任务失败后按仓库脚本启动本地 BGE 服务，`/healthz` 当前返回 `ok`、embedder/reranker 已加载；随后对同一 `eval_gen` Doubao 模型执行管理员单次连通性探测，结果 `healthy`、1,517ms。该探测只证明探测时点能连通，不能证明上游持续稳定或补救已失败任务；未重提整批任务。
- 下一步：`rag_eval_kb` 的 23/28 文件差异已按文件名和 SHA 映射，缺失 5 份均为上传/解析压力样本；`qa-synth-test` 只有 1 篇。两者都不能单独支撑可信业务多跳基线，不从中盲目补造正式候选。先在隔离或版本化测试 KB 补齐压力样本并核验索引代际；业务 RAG 基线仍需目标业务原文。合格 KB 就绪后，用默认 5 题混合配额和独立 request ID 受控生成，保留人工核验门。该批尚未重新提交，真实候选/发布/评测闭环仍待完成。

## 2026-11-13 — Ragas 请求回退范围修正

- 进一步查 worker 与 app 日志，没有找到 `OpenAIConnectionError` 底层 DNS、connect/read timeout 或 HTTP 状态；后续管理员 Doubao 探针单次通过（1,517ms），说明探测时点可连通，不能确定原故障根因或持续稳定性。
- 复审确认 fallback 机制本身按主模型错误触发，预算错误来自“一整个 Ragas 任务共用一个 request_id 和 fallback 计数”：默认仅允许一次回退，批内后续调用再次需要 fallback 时整批失败。Ragas 原已将重试额度明确调为 4，但漏调任务级 fallback 额度，属于批任务预算粒度过紧。
- 修正：新增任务级 `max_fallbacks` 有界覆写能力；Ragas 单批最多允许 2 次回退，第三次仍硬拦截。全局请求 fallback 上限仍为 1；32,000 Token 和 ¥0.50 预算不变；局部覆写禁止设为 0（全局配置中的 0 代表不限）。补充 scoped budget、保留已计数的恢复路径与 Ragas 调用参数测试，并更新预算治理说明。相关测试已列入 `rag_smoke.yml`，无需新增 CI 清单。
- 定向验证：`backend/tests/test_llm_budget.py` 与 `backend/tests/evaluation/test_dataset_enterprise_extensions.py` 共 69 项通过（48 秒）；四个改动 Python 文件 `py_compile` 通过；Ruff 除既有 `RequestBudgetExceeded` 命名规则 N818 外无新告警（`ruff check --ignore N818` 通过）；`git diff --check` 通过。
- 从本任务 worktree 按 `devctl.bat rebuild -y` 重建成功（11 个后端镜像与管理端前端；构建标记 `d5cd19c`、`dirty=true`）。backend 与业务模拟健康 API、管理端登录页均返回 200；21 个共享容器仍在运行，配置了健康检查的服务均 healthy。app 与 agent-worker 导入检查均显示 Ragas `retries=4 / fallbacks=2`，确认新预算代码已部署。
- 未重跑真实生成：`rag_eval_kb` 是解析压力资料，28 个 manifest 文件中 23 个已按文件名/hash 对应 Registry、5 个缺失；`qa-synth-test` 仅 1 篇。两者都不能作为可信业务混合质量基线。待补齐业务原文后再做一批 5 题实跑、人工核验；主模型连接层根因仍为 `UNVERIFIED`。

## 2026-11-13 — RAG 原始资料与知识库只读映射补核

- 直接读取桌面 fixture manifest，并只读查询共享 Registry `rag_eval_kb`；按文件名与 SHA256 联合匹配，28 份中 23 份唯一对应，避免 `base_v1.txt`/`exact_copy.txt` 同哈希时仅按哈希产生歧义。
- 缺少的 5 份：`UF-09-01 report.pdf`、`UF-13-01 doc_cn.pdf`、`UF-14-01 progit_en_501p.pdf`、`UF-14-02 progit_zh.pdf`、`UF-15-01 scanned_partial_fail.pdf`。现有 23 条 Registry 均 `active`、处理成功并有 chunk；`active_generation` / `index_generation` 为空，所以没有宣称索引代际已完整验收。
- 没有写共享知识库。原始资料在桌面不等于全部已经入库；本轮之前只做静态文件/哈希盘点，未执行这 5 个文件的上传。该资料包目标是格式解析压力测试，不是正式业务知识；后续应在隔离或版本化测试 KB 补齐并核验任务终态、索引代际和 chunk，再另用业务原文建立 RAG 质量基线。

## 2026-11-13 — 管理端当前基线只读验收

- 使用现有 `super_admin` 管理端会话，只读打开评测中心的数据集与运行记录页；没有提交生成、批准、发布或新运行。
- 生成抽屉显示四个可选 KB：`rag_eval_kb` 23 篇、`qa-synth-test` 1 篇、`cs_eval_kb` 0 篇、`travel_eval_kb` 0 篇；仍须遵守前述结论：`rag_eval_kb` 是解析压力资料，不能代表业务 Gold。
- 数据集页显示共 17 项；可见已发布资产包括 RAG 1 条、客服 324 条、拒答冒烟 1 条，其他候选/空集是草稿，不作基线。页内还可看到多跳/单跳勾选、5/10/20 题预设和每批 5 题说明。
- 运行记录最新 RAG Run `b9f82014-a173-5259-a2f0-ac2ba34ca06f` 为完成、1/1、通过 0、Gate `FAIL`、94 秒；RAG Run `6fef6cf3-97cd-5e88-83c4-a0f6d8b9d171` 为 `INVALID`。客服 Run `1a2ae7da` 页面显示 324/324 通过，但 Gate `INVALID`，不能当作质量通过。
- 浏览器证据：`.playwright-mcp/admin-current-baseline-2026-11-13.png`、`.playwright-mcp/admin-runs-current-2026-11-13.png`。最新 Trace 收尾代码尚无新的真实 Run 验收；双 KB 20 题也尚未在预算修复后重跑。

## 2026-11-13 — 生产文件缺口与知识库使用方式复核

- 只读查看本机共享管理端知识库工作台：14 个库合计 214 份文档，其中 RAG 100（兼容）94、企业公共制度 26、旅游 48、统一 RAG 测试库 23、产品 2、合成测试库 1，其余客服/售后/政策/话术库为 0–14 份不等。该环境不是线上生产，因此不能用这 214 份冒充生产文件，也不能据此声称生产文件已经验证。
- 代码确认生成抽屉仅显示 `active && audience === 'test'` 的 KB；仓库 README 规定测试库与客服/对客生产库隔离。这解释了为何现有业务库没有直接出现在生成列表。`qa-synth-test` 只有 1 份，适合打通按钮/任务流程，不适合作为主要生成语料；把它放在测试重点是优先级判断错误。
- 第一阶段结论：桌面 28 份资料覆盖 6 种格式、16 类列明的文件场景，足以做这批场景的首轮文件链路检查；目前仅 23/28 对上 Registry、5 份缺失，且索引代次未核实，故尚未完成首轮验收。即使 28/28 通过，也只能证明测试包覆盖，不能证明生产文件已覆盖。
- 正确的后续做法是先取得生产文件的脱敏清单或隔离副本，按文件格式、大小、扫描件比例、表格和更新方式与 28 份样本对照；只为真实缺口补样本。需要真实内容生成候选时，先经数据授权并复制到 `audience=test` 隔离库，再在管理端多选；不放宽生产库保护过滤，也不把不同 KB 的来源合并成一个数据集。
- 未访问线上生产环境，未上传、复制或发送任何业务 KB 内容到 Doubao；本轮仅做只读清点与文档修正。

## 2026-11-13 — 阶段 1 本机源文件归拢与向量只读核对

- 先复核目标工作树：C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent，分支 codex/evaluation-center-ui-20261112，HEAD d5cd19c53363ab9fea8e05f1dd2052a8080d380f。工作树原有未提交改动保留；本阶段只追加任务盘点文档，没有改动产品代码、启动新 Compose 栈或操作线上数据。
- 管理端 super_admin 只读清单为 14 个库、214 条活动文档。后端 Registry 另有 22 条活动文档不在这份管理端清单中：biz_product 2、policy_finance 5、policy_hr 11、通用 kb 测试占位 4。
- 把本机可还原的源文件集中到 C:\Users\wh\Desktop\企业ai平台\知识库源文件：从代码/测试夹具复制 110 份；把桌面“企业文档”中两份哈希匹配资料移入；从本机 app 容器数据卷复制 92 份。24 条原有登记映射仍留在桌面项目的测试语料路径中，实际是 23 个文件；其中一个文件被两个测试库引用。账号密码文件未读取、未复制。
- 按 Registry 的 SHA-256 复核后，236 条活动文档登记中 232 条有字节完全匹配的本地文件；4 条通用 kb pytest 占位（doc.md、doc2.md、active.md、doc-no-bm25.md）没有有效文件，哈希值是占位值。管理端可见的 214 条全部已有精确源文件。隐藏的 18 份业务资料已放在“后台未展示KB”目录，不能当作管理端已发布资产。
- 发现 7 条登记 file_size=0，但本地源文件实际为 98–200 字节且哈希匹配；这是登记大小字段错误，不是空文件。发现 4 组 policy_general 同库同名同哈希登记，以及一个源文件被 qa-synth-test 和 rag_eval_kb 两库共同引用。清单按 doc_id 逐条保留记录，并让同一字节文件共享一个托管路径。
- 只读执行 backend.rag.indexing.reconcile.run_reconcile：活动文档 ID 236 个、向量 231 个、chunk 231 个、BM25 可读；没有孤儿向量，chunk 与向量 ID 集合相同。5 个活动登记在向量、chunk、BM25 均缺失：员工手册_2026版.pdf 一条，以及 4 条 pytest 占位。该核对只证明文档 ID 成员，不证明向量内容与当前源文件哈希属于同一索引代际。
- 本阶段没有写 PostgreSQL、BM25 或 Registry；92 份从 Docker 命名数据卷的复制通过本地 SHA-256 校验。文档清单和按库汇总放在桌面目标目录，列出来源、托管路径、后台可见性、哈希与索引状态。
- 阶段结论：文件来源已经为管理端可见的 214 条本机活动文档补齐；整个 Stage 1 仍未通过。需要先备份并通过受控流程处理 4 条 pytest 登记残留、给员工手册补齐索引，再确认 3 个后台未展示业务库的归属/权限映射，并验证索引代际。线上生产知识库和生产文件没有读取。
- 本阶段未改 UI，因此没有新增浏览器验收；管理端 14 库/214 文档来自此前同一 super_admin 会话的只读页面/API清单。没有新增测试；文件处理使用 SHA-256、路径存在性和计数校验。


## 本机源文件二次搜索与向量 ID 对账修复（2026-11-13）

- 按 Registry 文件名在用户目录、Public、D 盘 workplace 工作区、E 盘用户/备份/下载/Git 等范围复查，并对同名候选逐个比较 SHA-256。发现 27 个目标目录外的精确副本，但各自的目标托管副本均已存在：23 个是 WorkBuddy 历史测试语料副本，4 个是 travel-crawler 导出输出；前者已在“不同文件测试文档”，后者已在“知识库源文件/travel”。因重复且仍由原工作流使用，记录路径和哈希，不再额外复制或移动。代码工作树中的大量 fixture 不作为业务原件。
- 从桌面“企业文档”找到另外 18 份未登记资料，移动到“知识库源文件/未登记候选/企业文档”；逐个核对移动前后哈希，18/18 一致。原目录已无文件。均未匹配 Registry 哈希；两个同名项 faq_常见问题FAQ.md、legal_采购合同.md 与登记内容哈希不同，当前仅列候选，没有入库或进入评测范围。
- 5 个缺索引活动 ID 已通过官方修复流程将 197 条路径行转为 failed 并保留历史。PG 全库 dump、逐行前像和 SHA 已归档，修复审计 ID 45。只读重对账返回 231 个活动 ID、231 向量、231 chunk、BM25 可读取、0 缺失、0 孤儿、0 ID 差异，consistent=true。
- 当前源清单仅保留 231 个活动 ID，5 个失败 ID 单独列出，修复前快照另存；18 份新候选单独管理。成员 ID 对账不证明索引文本代际一致；员工手册原文 SHA 精确，但索引缺失且处于失败态。未走可能向云模型发送 HR 文本的索引路径。
- 管理端可见活动文档从修复前 214 调整为 213；另有 18 份后端活动业务资料未展示，KB 权限/映射仍需查清。第一阶段还差隐藏 KB 归属确认、员工手册受控重建、压力语料 5/28 映射，以及生产脱敏资料覆盖核对。第二阶段 Ragas 混合生成和题目质量验收尚未开始。
- 目标目录清单：活动文档清单.csv 为当前活动态，已失败登记清单.csv 为 5 个失败 ID，修复前活动文档清单.csv 保留修复前快照，知识库汇总.csv 已按修复后活动态重算，另有候选与外部副本清单。没有访问线上生产数据。


## 后台未展示业务 KB 的原因（2026-11-13）

- 已查询本机 auth.knowledge_bases 与停用审计：biz_product（2 份）、policy_finance（5 份）、policy_hr（11 份）均为 audience=internal、目录状态 inactive、版本 1；其所属商品部、财务部、人事部在 default 租户均启用。
- 三条 auth.knowledge_base_audits 记录显示本机测试超级管理员 user_id=279 于 2026-10-22 执行 deactivate；审计未记录理由。管理端 KB 列表默认排除 inactive，评测冻结接口 require_kb 也只允许 active；生成抽屉当前只允许 active + audience=test。因此这是目录停用状态与生成 UI 受众过滤共同导致，不是源文件没找着。
- 未恢复 KB 状态：此前有明确停用审计，而且 HR/财务是内部资料。后续如要用这些真实资料生成评测集，需要先批准恢复目录状态，并验证只有 super_admin 可生成快照、内部内容不能进入对客检索。
- 管理端 213 可见活动文档数是修复前 214 列表减去失败员工手册所得，当前尚未浏览器复核；汇总 CSV 已加入目录启停、audience、部门、文档数和可选状态，供审阅。


## 上传压力语料逐条对照（2026-11-13）

- 不同文件测试文档中的 RAG fixture manifest 有 28 条正式样本；逐条检查源文件路径和 SHA-256，28/28 存在且哈希匹配。目录其余文件包含评测题、规则、辅助样本及 2 个 Office 临时锁文件，不按正式 fixture 计数。
- 当前 rag_eval_kb 的活动登记为 23 条；按 manifest SHA 对照，28 条里 23 条已有活动登记、5 条未映射。缺口为 UF-09-01、UF-13-01、UF-14-01、UF-14-02、UF-15-01，分别覆盖 PDF 跨页表格、100 页长文、两份 300–500 页长文、扫描/OCR 局部失败。原件都在本地，但没进入当前 KB，因此不能算已完成上传/切分验收。
- 逐题对照与 source hash 清单已写入桌面项目的 知识库源文件/压力语料对照.csv。下一步应先用正式上传链路补这 5 份并核对状态、页数/切块、文档版本与索引代际；未执行上传或重建。

## 2026-11-13 — 桌面资料归类到测试文件与生产文件

- 按用户确认，仅在 C:\Users\wh\Desktop\企业ai平台 下建立 测试文件、生产文件 两个主目录，子目录使用中文分类。原来的不同文件测试文档、上线准备包和知识库源文件已按内容归类；账号文件留在根目录，不读不搬。
- 项目代码、Docker 数据卷、Registry、向量和分块数据库未改。只更新本机活动文档清单的 managed_relative_path、候选清单的托管路径、外部副本清单的中央副本路径和相关说明链接；项目 registered_path 与 origin_or_volume_path 不变。
- 当前逐文件映射文档为桌面根目录 知识库与文件位置映射.md。它把项目登记路径、项目原始来源、本机副本分列，并列出 14 个 KB 的 231 条活动登记。
- 分类原则：明确来自代码/测试夹具、虚构或演练资料放测试文件；Docker 中 customer/internal 文件作为业务来源副本待生产核验；停用 KB 保持单独；未登记业务候选放待业务确认。当前没有已审批的生产资料集。
- 路径与哈希核验：231 条活动登记均可在新本机路径找到且 SHA-256 匹配；正式上传 fixture 的 28 份 hash 与 manifest 复核为 28/28 一致；候选资料清单共 18 条，路径和 SHA-256 全部核对通过。未运行代码测试，因为无代码修改。


## 2026-11-13 — 异常关机恢复与入库抽样收尾

- 异常关机后恢复检查：共享 Compose 中 app、rag-index-worker、rag-service、PostgreSQL、业务 Redis、broker Redis 与 auth Redis 均 healthy；后端 `/health` 和本地 BGE 服务 `:8088/healthz` 均返回 200。没有删除数据卷，也没有清理其他工作树的服务。
- 远端对齐：`origin/main` 更新到 `4f1ec9f`，比原 HEAD 多 11 个发布/CI 提交。快进时 Git 发现部署 README 同时有本地未提交改动；先把本地补丁备份到 `D:\tmp`，快进后再原样应用。README 本地 Redis 调整保留，远端发布说明也保留；当前没有提交。
- 本机 Registry 有 237 条活动记录；管理端“文档”页实际显示 219 篇，差额 18 条都在 `biz_product`（2）、`policy_finance`（5）、`policy_hr`（11）三个已停用的 `audience=internal` 知识库中。它们仍保留原件和登记，不纳入生成器可选库。活动记录没有零 chunk 项；但 189 条 `quality_status=unknown`，表示 Registry 没有可用的质量结论，不能把它们算成“质量通过”。只读核对发现这 189 条最近一次处理均为 `success` 且都有 Trace，所以这是质量结论缺失，不是最近运行失败；后续应抽查 Trace 中是否有质量记录，再决定是否补登记。
- 按 14 类抽样核对的 62 份原件哈希现在全部匹配；鼓浪屿和三坊七巷进一步核了本机源文件、Trace、Registry、chunk_store、Chroma 向量及整篇 doc_db 向量。鼓浪屿：10 chunks/10 唯一 ID、0 空块、10 个 Chroma 向量和 1 个整篇向量，Trace `a79c91d92d59` 成功；文档类型 `travel_guide`、业务域 `travel`、正式文件路径一致。三坊七巷此前整篇向量元数据误留随机 staging 路径；通过管理端重新解析修复，Trace `3eb8df94e1ca` 成功，13 chunks/13 唯一 ID、0 空块、两类向量均使用正式文件路径，哈希 `c7e01059...0872df8` 与本机原件一致。
- 两条 Trace 的最终入库结构质量记录均为 100 分、无空块/重复 chunk ID/无效父块引用；这只代表切分和索引结构检查通过，不代表资料事实或 RAG 答案质量为 100 分。鼓浪屿此前因本机模型停机产生的失败重建 Trace 留在历史中，之后的成功运行已替换当前索引。
- 管理端复核：待复核页显示 0；失败页保留 63 条旧失败记录；文档页显示 219 篇，鼓浪屿与三坊七巷均为活跃 `travel_guide`。数据库还留有一条疑似旧测试占位 `doc_a_1`：Registry 标记 `pending_review` 且 `chunk_count=2`，但源文件不存在、chunk_store 和向量表均无记录；管理端不显示它。本轮未擅自删除或修正这条历史记录。
- 浏览器快照：`.playwright-mcp/page-2026-11-13T12-10-35-383Z.yml`（待复核）、`.playwright-mcp/page-2026-11-13T12-10-42-255Z.yml`（文档列表）、`.playwright-mcp/page-2026-11-13T12-10-58-896Z.yml`（鼓浪屿 Trace）、`.playwright-mcp/page-2026-11-13T12-14-39-940Z.yml`（三坊七巷新 Trace）。索引元数据修复的定向测试此前为 1 passed、5 deselected；最终 `git diff --check` 通过。
- 剩余事项：62 份是按类别抽样，不等于把 237 份逐份验完；189 条活动记录仍无登记质量结论；18 条内部停用 KB 尚未纳入管理端；`doc_a_1` 占位记录需后续做受控归档/修正评估。失败页的 63 条历史失败需要另行逐类清点是否可重试，不能因本次抽样通过而清空。


## 2026-11-13 — RAG 上传压力包失败项复审

- 5 份本机源文件均与桌面 manifest 的 SHA-256 一致。Registry 已有 4 份失败记录且 `chunk_count=0`：`report.pdf`（`ocr_coverage_partial`）、`doc_cn.pdf`（`coverage_gap, ocr_coverage_partial`）、`progit_en_501p.pdf`（PDF 页面未解析、原文审计缺口、OCR/页面覆盖不完整）、`progit_zh.pdf`（PDF 页面未解析、OCR/页面覆盖不完整）。它们没有发布成 active 文档；旧处理记录的 Trace ID 在管理端均显示“不存在或已过期”。`scanned_partial_fail.pdf` 尚无 Registry/处理记录。
- 两个管理端列表口径不同：文档页按状态筛选能看到上述 4 条 failed 行；“入库失败”页显示 63 条 `rag_index_runs` 待处理任务，这 4 个旧失败不在该队列里，也没有对应 `rag_index_runs` 行。没有删除或改写这些历史失败；新证据见 `.playwright-mcp/ingest-failures-doc-filter-2026-11-13.yml` 和 `.playwright-mcp/upload-failures-current-2026-11-13.yml`。
- 暂未重传这 5 份。桌面包 `testset/README.md` 标记为 `6.0.0-desktop-candidate.1`，说明尚未真实上传/OCR/检索，并要求在独立评测环境以整套 manifest、题库、源文件及快照重建，不与旧 baseline/数据库混用；其上传规则还要求跨页表格人工固化行基线、扫描件明确 OCR Provider 与部分文档发布策略。当前 `rag_eval_kb` 是共享开发库，直接补传会改变既有评测检索范围，因此本轮没有向该库上传。
- 资源并非本轮阻塞：宿主可用内存约 2.60 GB，主要服务与本地 BGE healthy。下一步要先建立/指定独立的版本化评测 KB，再逐份运行这 4 个旧失败样本和 1 个扫描件；跨页表格基线与 OCR 策略未确认前，只能记录失败/阻塞，不能报 PASS。

## 2026-11-14 — 五类上传质量问题修复与管理端复核

- OCR 预算与费用：DashScope OCR 每页调用前进入统一预算预占；有用量时按价格账本记费，没有用量时标记未核算并走待对账，不再把费用写成 0。原 32,000 Token / ¥0.50 请求上限未提高。定向用例确认预算拒绝发生在 Provider 请求前。
- 表格漏行：质量报告为未覆盖行增加脱敏单元格样例和源页码；完整性门禁仍拒绝缺页/缺行，不会把不完整文档发布。管理端读取长 PDF 报告上限从 2 MiB 调到 5 MiB，覆盖实际 3.95 MiB 的 `UF-14-02` 报告。浏览器已显示 154 页需求、100 页上限和未处理页数；旧 `doc_cn.pdf` 记录仅留有“少 2 行”计数，没有旧 OCR 文本/缓存，不能倒推出具体缺行，本轮未改写历史质量报告。
- Trace：整篇向量入库新增独立 `index_doc_embed` 阶段；异步上传失败按 `upload_id + tenant_id` 找回最新 Trace。当前失败页中扫描件预算失败可打开 Trace；历史质量记录没有原始 Trace 时明确显示“无历史 Trace”，不伪造补链。
- 分类：元数据规则增加“鼓浪屿”旅游指南映射。管理端搜索复核鼓浪屿历史背景为“旅游 / 旅游指南”，三坊七巷街区为“旅游 / 旅游指南”；两份资料均在旅游库，最新记录结构质量通过。
- 页面：失败页区分异步队列和质量门禁，分别统计；现在显示 68 条异步记录、4 份质量失败。可读提示包含 OCR 页数、PDF 原文缺页、表格缺行数及可用 Trace 链接。旧失败仍保留，失败条目没有被伪装成成功。
- 定向验证：此前本轮后端相关测试 29 项通过（711.88 秒）、前端组件 4 项通过、CI 路径清单 22 项通过；本轮新增“大于 2 MiB 的 PDF 质量报告仍展示页数缺口”回归用例 1 项通过（28.67 秒）。修改文件 `py_compile` 与 `git diff --check` 通过。
- 按本工作树两次执行 `devctl.bat rebuild -y` 均成功；最后一次部署包含 5 MiB 报告读取修正。后端健康、管理端构建和容器健康检查通过。浏览器证据为 `.playwright-mcp/quality-failures-after-fix-2026-11-14.yml`、`.playwright-mcp/knowledge-documents-gulangyu-classification-2026-11-14.yml`、`.playwright-mcp/knowledge-documents-sanfang-classification-2026-11-14.yml`；失败页为 72 项，长 PDF 的页数缺口已出现，鼓浪屿和三坊七巷分类正确。
- 离线 RapidOCR 复核 `report.pdf` 14/14 页、`doc_cn.pdf` 11/11 页，两个样本在本机解析结果中均无表格覆盖缺口；本轮没有再次调用云 OCR，也没有写共享知识库。历史云端结果仍保持失败：`progit_en_501p.pdf` 第 13 页原件损坏需替换源文件，超出页数上限的中文长 PDF 需按批准策略拆批或明确拒绝，不能靠放宽完整性门槛发布。首次冷启动时失败页接口曾短暂返回授权服务不可用，随后刷新返回 200；根因未定位，需在后续冷启动复核中继续观察。

## 2026-11-14 — 全库抽样复核与本机清单纠正

- 本轮只读核对共享开发库，并整理本机资料；未上传、删除或改写 Registry、向量、chunk、BM25、Trace，也没有重跑 OCR。写入前备份了桌面活动清单、知识库汇总和盘点说明；文件移动前像另存于 `D:\tmp\evaluation-center-local-audit-20261114-064804`，清单修改前像另存于 `D:\tmp\evaluation-center-local-audit-20261114-065146-ledger-before`。
- 当前 Registry 为 237 条活动文档、14 个 `doc_type`；按类型每类最多抽 5 条，共 62 条。62/62 原件 SHA-256 匹配；Registry、chunk、向量数量和 ID 对得上；抽样 chunk 文本与对应向量文本逐条一致；无空 chunk、错误文档/知识库/类型/路径元数据或登记 chunk 数差异。该结果不证明解析覆盖完整，也不等于答案质量通过。
- 当前质量状态是 `pass=44`、`soft_warning=193`、`unknown=0`；此前“189 unknown”的记录已经过时。62 条样本中 54 条为 `soft_warning`、8 条为 `pass`。主要警告是旧质量字段缺失、重复 chunk、历史过滤 chunk、质量异常标记、标题/结构不足、内容短或噪音偏高；多种原因会重叠，软警告不得报成通过。管理端显示 219 篇，其中通过 44、需复核 175、失败 0、未核验 0；另 18 条在已停用的内部库，不显示在此列表。
- 当前 `travel` Registry 与管理端均为 54 篇，旧桌面清单曾记 48 篇。本机活动文档清单已从 231 行补到 237 行；新增 6 篇旅游原件均在生产资料目录，哈希、登记大小、chunk 数均核对一致，成功 Trace 仍可查：`a79c91d92d59`、`3eb8df94e1ca`、`3285cded4d38`、`666de09032f8`、`bf4a52b09ea5`、`363fa4cd94ca`。结构评分 100 分只代表入库结构门禁，不代表旅游事实或回答正确。
- 本机已将“并发竞态.md”“在线刷新-31059.md”两份系统测试样例从生产资料移到测试资料，文件哈希未变；Registry 路径仍保持项目原路径。测试探针 `ZZZ-s10-20261006033449.md` 的本机副本也已标回测试目录，但项目 Registry 仍将其作为活跃文档放在对客 `travel` 库。管理端当前显示“合规 / 活跃 / 需复核·90分”；它不应作为旅游业务证据，后续需先备份，再通过正式归档流程处理并保留历史审计。
- 62 条样本中的 55 条旧 Trace 只剩编号，详情已不在当前留存库；3 条测试样本的操作记录从未留 Trace ID。近期新增的 6 条旅游成功 Trace 可查。当前采样率为 1.0，现有证据不能把历史详情缺失归因于采样，也不能凭 ID 还原历史 Trace。
- 全库 2628 条旧 `chunk_store` 记录的 `chunk_id` 和 `chunk_metadata` 为空，对应向量 metadata 也缺 `source_format`；Registry 的 `source_format` 另有 189 条为空。样本中的向量文本和关联关系可对上，但这些旧字段会削弱来源与解析过程追溯，后续需在备份后评估受控补元数据或逐文档重建，不得全库直接重传。
- 对账工具唯一报告一项孤儿：`policy_general` 下有两条 `doc-existing` chunk_store 记录，没有活动 Registry、向量或 BM25 对应项；来源待查，本轮未删除。`UF-08-01` 的 chunk_store 比向量多一条是 parent chunk 口径差异，BM25/向量按 leaf chunk 计数，不是缺索引。
- 浏览器复核证据：`.playwright-mcp/quality-audit-documents-2026-11-14.yml` 和 `.playwright-mcp/quality-audit-documents-dom-2026-11-14.json`。页面显示旅游库 54 篇、当前可见 219 篇（44 通过、175 需复核）；`ZZZ-s10-20261006033449.md` 仍在对客旅游库且显示“合规 / 活跃 / 需复核·90分”。
- 本机明细清单和汇总已更新，README 增加本次更正与抽样结果。共享库中的测试探针归档、孤儿 chunk 处理、旧元数据补齐、Trace 留存策略以及 193 条软警告复核仍未完成；这些操作必须先备份并保留 Registry/Trace/审计历史。

## 2026-11-14 — 元数据回填复核与管理端分块详情

- 远端对齐：重新 fetch 后，`origin/main` 仍是 `c605e2e76c59d0cea194a12a68467bffd9e7cae3`，与当前任务分支基点相同，没有新提交需要合并。
- 写共享开发库前完成 PostgreSQL 全库备份：`D:\Program Files\workplace\agent\backups\pg\rag-metadata-before-repair-20261114-0845\agent_memory.dump`，430,434,539 bytes，SHA-256 `71CE64DE5020DEAC2630889C175ED1C16E0C872DC18F21C39F171ADAD29B7C9B`。同时保留 3 份行级前像：`metadata-before.jsonl`（`7B1E701F95817327622F3AA21E631FA8553E283690C6C22CAC1DC811E090B057`）、`chunk-index-before.jsonl`（`B303E9A44D2F462531F3A3E7FB4BA92E8578AB8B49AB9076C185A37731B5BA0D`）、`parent-link-before.jsonl`（`FBF0A8567D4580420D92A5620479D39D0F4C01EEF2FF0AB3F79E0CC32DA6AAD1`）。
- 已完成且可从前像回退的元数据修复：184 条活动 Registry 的 `source_format` 按原件扩展名补齐；2,619 条 chunk/vector 的稳定 ID、层级及来源元数据逐条精确配对；1,875 条向量的 `chunk_index` 与对应 Chunk Store 对齐；1,578 条在当前分块中不存在目标 Parent 的旧引用在向量和 Chunk Store 两侧同时清空，未按标题猜测重连。没有改正文、Embedding、Trace 或操作审计。
- 最新只读对账：活动文档 231 篇、Chunk Store 5,886 行、正式 Chroma 向量 5,885 条，5,885 条向量均有唯一 chunk 对应且索引号一致；Registry/chunk/vector 的来源格式缺失数、chunk ID 缺失数、向量孤儿数和叶子 Parent 悬空数均为 0。唯一未向量化的是 `UF-08-01_0`，granularity=`parent`；这是上下文 Parent 仅存 Chunk Store、不单独参与检索向量化的既定口径。当前查询没有未登记 doc_id 的孤儿 Chunk Store 行。`ZZZ-s10-20261006033449.md` 已是 `deleted`，不再计作有效旅游资料。
- 管理端修复“查看分块”误跳 Trace：现在有独立的“查看分块”和“查看处理 Trace”按钮，点击表格行不会再误入可能过期的 Trace。分块抽屉展示正文、稳定 ID、父子关系、知识库/类型/部门、章节、关键词、模拟问题以及白名单来源/切分元数据（含格式、解析/切分版本、索引代次、页码/表格行、质量标记和 Token 数）。详情 API 保留原有文档读取授权与发布状态门，不返回服务器本地路径或租户内部标识；请求失败会显示具体错误。
- 真实管理端浏览器使用 `super_admin` 登录态，在本工作树的本机预览端口 3202 验收：文档列表显示 213 篇（44 通过、169 需复核）；点文件名留在文档页，点分块按钮打开 `policy-logistics-exception.md` 的 6 条真实分块正文。共享后端容器由另一个工作树 `assistant-review-fixes` 构建，响应尚未包含本轮新增的 `metadata` 字段；页面已明确标注“本次接口没有返回来源或切分信息”，因此这次浏览器验收没有证明元数据 UI 与新后端契约已连通。截图与快照为 `.playwright-mcp/chunk-details-management-2026-11-14.png`、`.playwright-mcp/page-2026-11-14T01-30-54-714Z.yml`、`.playwright-mcp/page-2026-11-14T01-31-01-930Z.yml`、`.playwright-mcp/page-2026-11-14T01-31-05-275Z.yml`。
- 定向验证（2026-11-14 复验）：后端分块详情与 Parent 映射两个测试文件 24 项通过，覆盖父块排在子块之后、旧父块编号撞上新编号；管理端 API/分块抽屉 21 项通过，Smoke 路径守卫 22 项通过；TypeScript 检查、Python `py_compile` 和 `git diff --check` 通过。`ruff --select F821` 报 `indexer.py:2339` 的 `qwen_kws_per_chunk` 未定义；同一代码已存在于 `origin/main`，不在本次改动范围，本轮未修。Next.js 15.5.28 生产构建及 49 个静态页此前通过。未运行全量回归、真实 Doubao 评测或新 RAG Run。
- 部署状态：后端代码变更按仓库规则必须从本任务工作树重建受影响服务；但当前共享栈镜像归其他工作树，不能安全替换。本轮未重建/重启共享容器，服务端元数据响应与浏览器联调标记 `UNVERIFIED`。下一步应在该共享栈不再由其他工作树使用后，从本工作树重建 RAG 服务，再用同一 super_admin 页面确认来源/切分字段实际出现；随后继续核验测试资料和生产资料的上传、切块与 Trace，不把未验证状态算通过。

- 最终复验（2026-11-14）：本工作树的 Next.js 15.5.28 生产构建退出码 0，49 个静态页生成完成；构建自动改动的 frontend-admin/tsconfig.json 已还原。使用 super_admin 浏览器再次打开 policy-logistics-exception.md 的 6 个真实分块，详情抽屉显示正文、知识库、章节、关键词和模拟问题；GET /api/rag/chunks/921f97a86c/detail 返回 200，但当前共享后端响应仍无 metadata，页面如实提示来源/切分暂不能核对。最终证据：.playwright-mcp/chunk-details-management-final-2026-11-14.png、.playwright-mcp/chunk-details-management-final-2026-11-14.yml、.playwright-mcp/chunk-detail-api-response-final-2026-11-14.json。本地预览已停止；后端重建仍因共享栈由其他工作树使用而未执行，故后端部署与字段联调继续标记 UNVERIFIED。

## 2026-11-14 — PR #468 付费前父子引用预检复核

- RAG Smoke 首轮发现一处本次改动回归：索引 ID 映射先清空了悬空 Parent 引用，后续付费前完整性预检看不到原始错误。已将 ID 映射移到预检之后，使无效 Parent 先被拒绝；完整性预检、分块详情和 Parent 映射定向测试本机复跑 29 项通过。
- 修复已准备推送到原 PR 分支，等待 CI 重跑；共享 RAG 服务仍由其他工作树持有，本地真实服务联调继续标记 `UNVERIFIED`。

## 2026-11-14 — 文件与切分质量复核、旅游代次元数据补齐

- 本机 `生产文件` 共 91 个文件；6 份已验收旅游资料在当前 `travel` 库均为 active/pass，合计 67 个 chunk，6 条最新成功 Trace 均仍可查。另有 42 份旧旅游资料仍是 soft_warning，合计 1,497 个 chunk 缺少解析器版本和切分规则版本；不把它们算作生产资料验收通过。
- 六份已验收资料中，鼓浪屿历史背景和三坊七巷街区的 23 个 Chunk Store 行及 23 个 Chroma 行缺少 `index_generation`，但 Registry 有明确的 `active_generation`。先保存 46 行元数据前像：`D:\tmp\rag-index-generation-backfill-20261114-104507\metadata-before.jsonl`，SHA-256 `626BD043F5F19E24CA84A9F9A745CDE07CAC0D9F477E8584FCB56A2F86482A7D`；随后在单一事务中只补 `index_generation`，没有改正文、向量值或 chunk 数。复核六份资料的 chunk 与向量均为 67/67，来源格式、解析器版本、切分规则版本和代次与 Registry 一致。
- 28 份 RAG 上传夹具原件仍全部在本机且 SHA 与 manifest 相符。当前 `rag_eval_kb` 有 23 份 active（20 pass、3 soft_warning）和 4 份 failed；另 5 份失败文件已在测试受众隔离库 `rag_upload_pressure_20261113` 走过最新上传尝试，隔离库仍为 0 篇，未发布任何失败文档。
- 五次最新 PDF 尝试均在管理端“入库失败”页有可点开的 Trace，Trace Store 中 5 条均为 error：`report.pdf` 与 `doc_cn.pdf` 为 coverage_gap；`progit_en_501p.pdf` 为 PDF 原文核对缺口；`progit_zh.pdf` 为 OCR 覆盖不全；`scanned_partial_fail.pdf` 因请求 Token 预算超限失败。五条合计记录 222 次模型调用、381,792 Tokens，费用都显示“未核算”；不在预算和费用口径查清前重跑这些大 PDF。更早的 4 条质量门禁失败记录仍显示“无历史 Trace”，没有伪造补链。
- 管理端当前只读页面：可见 213 篇（通过 44、需复核 169、失败 0），待复核 0；失败页显示 72 项（异步任务 68、质量门禁 4）。浏览器证据：`.playwright-mcp/page-2026-11-14T02-35-42-705Z.yml`、`.playwright-mcp/page-2026-11-14T02-36-04-501Z.yml`、`.playwright-mcp/page-2026-11-14T02-38-41-082Z.yml`、`.playwright-mcp/page-2026-11-14T02-41-24-326Z.yml`。
- PR #468 已通过必需 CI 并合并到 main，当前分支 HEAD `768e11d` 与 `origin/main` 一致；下面的共享管理端旧版现象是未重建部署造成的验收阻塞，不是把 PR 合并回滚。
- 运行中的共享管理端仍表现为旧版：按钮标题为“查看Chunks”，点后跳转到 Trace，没有打开 PR #468 新增的分块详情抽屉；因此最新 UI/API 部署与联调仍是 `UNVERIFIED`。首次打开知识工作台时接口短暂出现 502/503，刷新后健康检查、文档、统计和知识库接口均返回 200；容器健康、无 OOM、无重启，本轮没有重建共享服务。
- 本轮没有改代码、没有重新上传、没有重跑 OCR；数据库改动仅为上述可由元数据前像回退的 46 个 JSON 字段。下一步先查清 OCR 请求预算为什么允许多页调用、费用为何未核算，再处理 42 份旧旅游资料的逐类质量与来源追溯；等共享管理端可由本任务工作树安全重建后，再验收分块详情页面。

## 2026-11-14 — OCR 预算失败快速停止修复

- 复核 42 份旧旅游资料时确认：Registry 和 1,497 条 chunk 都没有 `parser_version` / `chunk_policy_version`；只有通用 `pipeline_version=rag-processing-v1`，不足以证明实际解析器与切分规则版本，不能凭空回填。需要从原始本机文件受控重建，再逐份对照 Trace、chunk 与向量。
- OCR 预算超限原先会被 PDF 解析器当成普通单页识别失败，继续尝试剩余页；现在会立即向上抛出硬预算错误，避免预算已拒绝后继续渲染和空转。新增两页 PDF 回归测试，证明第一页预算失败后第二页不会再调用 OCR。
- 定向验证：`D:\Python\python.exe -m pytest backend/tests/rag/test_pdf_ocr.py -q` 为 42 passed；`git diff --check` 通过。没有重新上传 PDF，也没有发生模型调用。
- 运行时复核发现管理端共享 RAG Worker 当前因 Redis 用户名/密码不匹配而处于 unhealthy，尚不能做真实上传复验；容器由 `assistant-review-fixes` 工作树构建，本轮没有重启或改动它。五条 PDF Trace 的 381,792 Tokens 在 `llm_usage` 中都有记录，但费用字段为空；数据库无法证明实际账单，需从运行该版本的服务/供应商用量确认。恢复真实链路前不重跑大 PDF。
- 当前状态：OCR 超限后的空转代码已修并通过定向测试；42 份旧旅游资料仍待受控重建；真实 Worker 上传、账单核对及新一轮管理端验收受共享运行环境阻塞。
- 继续核对这 42 份资料：生产文件夹中的原件 42/42 与 Registry SHA-256 相同；逐份文档核对 Registry、Chunk Store、向量表后，1,497 个 chunk 数量全部一致，空 chunk、重复 chunk ID 均为 0。但发现更重要的缺陷：1,114 个 Leaf 的 `parent_chunk_id` 全为空，383 个 Parent 没有任何子块关联，向量元数据也一样；不是“断到不存在的父块”，而是整个父子关系没有保存，当前无法用 Leaf 命中后扩展 Parent 上下文。需要从原件重建，先备份再做单文档试点，确认新旧检索行为后批量处理。
- 管理端可信上传会先提交 Celery；共享 Worker 未恢复前，没有安全的管理端同步索引旁路可用，本轮不绕过队列直接写共享索引。
- 对 42 份原件做了本地、无模型调用、无数据库写入的切分预演：新版本预计 2,650 chunks（旧库 1,497），42/42 的块数和正文顺序都会改变；新结果有 2,272 个 Leaf 父块关联，旧库为 0；预演的清洗/源内容覆盖缺口均为 0。新分类有两份与 Registry 不同：`厦门旅游攻略.md` general→travel_guide，`厦门特色指南.md` travel_guide→contract_template；批量前需在单文档真实运行中核对最终分类和答案检索。此预演只用于估算影响，不是入库验收。

## 2026-11-14 — 旅游资料隔离试上传与切分复核

- 选用生产资料文件夹中的 `厦门-城市-厦门.md` 做单文件试点。测试副本 SHA-256 与生产资料原件一致；只上传到测试受众知识库 `travel_eval_kb`，没有改动 `travel` 生产知识库。试点文件 13,394 字节。
- 首次上传因宿主机 BGE 服务重启后未运行而失败，管理端最初记录为 `provider_error / APIConnectionError`，Trace `d480c3a27563` 状态为 ERROR；恢复 BGE 后同文件重试一次，管理端显示活跃、质量通过 100 分，成功 Trace `f26155b7e8d9`。成功 Trace 记录解析 145 块、过滤 14 块、写入 131 块；Trace 记录 108 个叶块、23 个父块、108 条有效父引用，空块、缺 ID、重复 ID、无效父引用均为 0，覆盖检查 122/122。
- PostgreSQL 只读核对：该文档 Chunk Store 131 行，向量表 chunk collection 131 行，另有 1 行文档级向量；131 个 chunk 向量均带有解析器版本、切分规则版本、索引代次、源格式、来源定位和来源跨度元数据。用本地 BGE 生成 1024 维查询向量，直接在该测试库做只读向量检索；查询“厦门高崎国际机场距离厦门市中心有多远”时第一名命中机场原文，明确包含“距厦门市中心10公里”，第二名是对应父块。此项只证明向量写入和相似度检索，不等于完整混合 RAG 或答案质量评测。
- 管理端浏览器复核：文档页显示 `travel_eval_kb / 通用 / 旅游 / 旅游指南`、活跃、质量通过；待复核页为 0。失败页当前 72 项，但不再列出这次已恢复的文件；首次失败 Trace 仍可直接用 ID 打开。失败 Trace 页面标题显示临时随机文件名而不是原文件名。浏览器记录见 `.playwright-mcp/page-2026-11-14T06-59-39-071Z.yml`、`.playwright-mcp/page-2026-11-14T06-59-43-205Z.yml`、`.playwright-mcp/page-2026-11-14T07-01-43-320Z.yml`。
- 仍有两个验收缺口：分块详情抽屉显示来源/切分元数据为 0 项，但只读数据库证明 131 条向量行和 Chunk Store 都有这些字段；当前 API 容器没有把元数据返回给页面。目标工作树的 `backend/rag/indexing/chunk_detail.py` 已有映射实现，但共享 app 与 RAG Worker 实际来自 `assistant-review-fixes` 工作树；为保护其他会话，本轮没有替换或重建共享服务，因此目标工作树版本部署状态为 UNVERIFIED。Trace 页面显示的 ¥0.0127/¥0.0085 也只是当前价格登记下的台账值，运行日志有 `price_unknown`，不能当作供应商实际账单。
- 目标工作树定向验证：`D:\Python\python.exe -m pytest backend/tests/rag/test_chunk_detail_metadata.py -q --no-cov` 为 4 passed（24.13 秒）。这证明本地映射函数测试通过；共享管理端仍跑着另一工作树代码，所以页面验收状态仍是 UNVERIFIED。
- 下一步：等共享服务可安全重建时，从本任务工作树部署 app 和 RAG Worker，重验分块详情 API/页面来源元数据；再选一份旧旅游资料做单文档重建，逐项比对原文、分类、Chunk Store、向量、Trace 和真实检索，确认无误后才评估 42 份旧资料的批量修复。生产库不做批量写入。

## 2026-11-14 — 上传 Trace 正式文件名修正与复验

- 候选文件上传时，索引器会从随机暂存路径读取文件，但原始逻辑路径仍在 `identity_path`。此前 Trace 标题和阶段 Span 名称错误地取暂存文件名；现在改为显示逻辑路径中的正式文件名，内部读取路径和文档 ID 逻辑不变。
- 新增候选索引 Trace 回归用例，并对预算硬停止用例一起复验：`py_compile`、`git diff --check` 通过；两个定向测试 `2 passed in 74.34s`。Ruff 仍报告目标文件既有导入排序、未使用导入、未定义旧变量及 pytest fixture 命名告警；新增用例已对同名 fixture 做局部标注，没有顺手改动旧告警。
- Docker 只读复核显示共享 `agent` 项目由多个工作树共同组成，app 与 RAG Worker 实际镜像来自 `assistant-review-fixes`；本任务没有重建、重启或覆盖这些服务。因而 Trace 标题改动尚未在管理端部署验收，仍为 `UNVERIFIED`。当前宿主可用内存约 3.16 GB；不为此另起完整栈，也不改共享数据库。
- 测试没有上传新文档或写向量库。此前上传试点的两个完全同哈希临时副本仍在 `.playwright-mcp/ingest-pilot/` 与 `.playwright-mcp/ingest-pilot-20261114/`；原始生产资料未动。清理这两个副本的精确删除请求曾被自动审核拒绝，本轮没有改用其他方式绕过。
- 下一步：在共享服务可安全切换到本任务工作树时，重建 app 与 RAG Worker，并用 `super_admin` 浏览器确认失败 Trace 标题显示正式文件名、分块抽屉返回来源/切分元数据；再做一份旧旅游资料的隔离重建试点，确认层级、向量、Trace 和检索都正确后，才决定是否修复余下 42 份资料。

## 2026-11-14 — 管理端目标前端与现有 API 联调复核

- 为避免覆盖共享前端，只在本工作树启动 Next 开发预览 `localhost:3202`，沿用现有网关和 API；浏览器使用已有 `super_admin` 会话。联调后已停止该预览进程。
- 页面实际显示 214 篇活动文档（45 通过、169 需复核）。`travel_eval_kb` 的 `厦门-城市-厦门.md` 显示活跃、质量通过 100 分。点“查看分块”停留在文档页并打开 131 块抽屉；点“查看处理 Trace”则打开对应 Trace，两个入口已分开，按钮不会冒泡跳错页。浏览器快照：`.playwright-mcp/page-2026-11-14T07-37-15-494Z.yml`、`.playwright-mcp/page-2026-11-14T07-38-16-648Z.yml`。
- 分块详情 GET 返回 200、131 行正文和父子关系，但每行都没有 `metadata` 字段；抽屉明确显示“来源与切分元数据（0 项）”。容器内 `chunk_detail.py` 仍是旧映射器，未把审计元数据放进响应；本工作树版本已有安全白名单映射。因此页面控件本身已验收，服务端来源/切分字段尚未通过真实链路验收。
- 从页面打开成功 Trace `f26155b7e8d9`，标题仍为随机暂存名 `68947716e6ae.c3c54cbbb7f24baf.md`；此前失败 Trace `d480c3a27563` 也仍显示随机暂存名。只读核对当前 RAG Worker 容器不含本工作树的 `display_name` 修正；这次 Trace 标题修复尚未部署。
- 当前管理端页面来自本工作树源码，API 与 RAG Worker 仍由共享服务提供；服务端新代码和新上传行为不能据此报为通过。后续必须先安排共享服务安全切换到本工作树，再复核 API 元数据和重新产生一条正式文件名 Trace。生产库及已有向量本轮未改。

## 2026-11-14 — 默认管理端与目标前端差异确认

- 追加检查默认管理端 `localhost:3200`：仍显示旧按钮名“查看Chunks”，点击按钮会触发行级点击并跳到 Trace；本工作树前端 `localhost:3202` 则显示独立的“查看分块 / 查看处理 Trace”，点分块按钮只开详情抽屉。说明目标前端交互正确，但用户默认入口尚未部署本次已合入的 UI。
- 3202 预览经共享网关读真实 `travel_eval_kb` 文档和 Trace，权限会话正常，页面/API请求均为 200；但 API 容器返回的 131 个块没有 `metadata`，预览明确显示 0 项来源与切分信息。该差异已定位到共享 app 容器的旧 `chunk_detail.py` 映射器；本工作树映射器含安全白名单，单测此前 4 项通过。
- 当前验收拆分：目标 UI 按钮行为 PASS；试点上传与数据库分块/向量关系 PASS（前述只读证据）；默认 UI 部署、API 来源元数据、Trace 正式文件名均 UNVERIFIED。共享 `agent` 栈由多个工作树共同组成，app/worker/frontend 容器仍属其他工作树，因此不在本轮切换或重建。
- CI 补齐：把 PDF 预算硬停止测试和候选 Trace 正式文件名测试加入 `.github/workflows/rag_smoke.yml`。RAG Smoke 路径过滤与工作流契约定向测试 `28 passed`；两条新 smoke 用例 `2 collected`；`py_compile` 与 `git diff --check` 通过。没有运行付费 Provider 或全量回归。

## 2026-11-14 — 对齐工作树与重索引追溯复验

- 工作树对齐：资料质量工作树从 `768e11d` 出发，与最新 `origin/main` 一致；企业助手工作树的原始补丁先备份至 `D:\tmp\assistant-review-fixes-preserve-20261114`（SHA-256 `DA156F90DF0B93566B94B27A049CDC784F7AC5F22A9BFCD02AFEE6B372495D95`），再成功 rebase 到 `dc3faac`。两边各自改动和 Playwright 产物均保留，没有合并功能分支。
- 为普通重索引补上 Registry 当前 `active_generation` 到新 Chunk/Registry 元数据的传递；候选发布仍使用候选集合自身代次。定向测试 `backend/tests/rag/test_indexer_trace.py` 中两条相关用例 `2 passed in 111.15s`。
- 使用默认管理端 `super_admin` 对测试库 `travel_eval_kb` 的 `厦门-城市-厦门.md` 重索引并复核：131 个 Chunk、23 Parent、108 Leaf（均有 Parent），每条均有代次 `c3c54cbbb7f24baf`、格式 `md`、解析版本 `v2`、切分版本 `StructureChunkStrategy:v3` 和原文定位；Chunk/向量哈希与登记原件一致，质量分 100。最新成功 Trace 为 `da4d80d49636`，16 个 Span、阶段标题显示正式文件名；操作日志 API 的最新记录和文档页按钮均指向该 Trace。复核后重新批准了测试库资料；生产库 `travel` 中的待复核条目保持未动。
- 重索引开始时还复现管理端过早显示“完成”：状态接口返回新任务 `RUNNING`，却同时带有上一次任务的 `progress.stage=done`。前端现在优先使用任务终态；只有没有任务记录时才接受提交后更新的终态进度。新增 `reindex-status` 定向测试 `3 passed`。
- 已按 `devctl.bat rebuild -y` 串行重建共享栈，后端代次修复已在默认管理端实测；前端过早完成修复尚未重建和浏览器复验。原因是宿主可用内存降到约 1.8 GB，低于 2 GB；遵守本机资源规则，等内存回升后再重建。本轮没有提交。
- 原资料质量任务仍未整体验收：42 份旧旅游原件的切分/父子关系需要从原件重建；193 条历史软警告、旧 Trace 留存缺口、孤儿 chunk 和 28 份上传样本的 5 份缺件仍需按前述计划处理。当前测试库单文件成功不能替代这些全量验收。

## 2026-11-14 — 接手后的合并前定向验证

- 普通重索引代次、候选 Trace 正式文件名、Chunk 来源元数据、PDF 预算硬停止相关后端定向测试 `8 passed`；管理端重索引状态 helper `3 passed`。对应共享测试库试点的 131 个 Chunk、23 Parent/108 Leaf、向量、原件哈希和 Trace 已在上节记录。
- `git diff --check` 通过。重索引提示修复尚未在默认管理端重新构建和浏览器实测；42 份旧旅游资料仍未做生产批量重建，原件、历史 Trace 与质量警告的全量对齐仍未验收。
