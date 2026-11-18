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
- 当时的“等待 CI 重跑”是合并前快照。GitHub 现证实 PR #468 于 2026-11-14T02:20:29Z 合并；8 个 Checks 中 7 个成功、真实 API RAG Smoke 1 个跳过。该时点共享 RAG 字段联调仍为 `UNVERIFIED`，后续部署与实机证据见本任务后续记录。此处不再保留待重跑状态。

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

## 2026-11-14 — 异常关机后继续资料质量验收

- 资料质量工作树为 codex/file-quality-main-followup-20261114，HEAD 19eecf2（与当前 origin/main 一致）；追加本记录前跟踪文件无未提交改动；本轮只新增本进度文件。保留现有未跟踪 .playwright-mcp/ 验收产物。
- 共享开发栈有 21 个容器运行；Backend /health 返回 ok，RAG、PostgreSQL、Redis 与主要 Worker 健康。Docker Desktop 与 Redis 的恢复记录见此前交接，不在本轮再次改动共享栈。
- 原文件目录 生产文件/旅游业务资料/旅游知识库原文副本_待生产核验 仍有 42 个旅游原件；候选 福州-城市-福州.md SHA-256 为 4E3D2410A0B98CC55EF7C7552B3C12479ECD5CA1790E3D7119BAC2BB34FA9F1A。数据库里旧旅游文档的分块缺解析/切分版本和父子关系问题，沿用前面只读核对结果，尚未批量处理。
- 本机 dev-local-models.bat status 显示本地 BGE 服务未运行，/healthz 连接被拒；宿主可用内存约 1.56 GB。按本机资源规则，本轮没有启动模型、没有重建/上传/重索引，也没有写知识库或向量数据。旧旅游资料的单文档真实重建暂时 BLOCKED，不把离线分析当作上传验收。
- 本轮实际验证：代码树/分支状态、文件数与样本 SHA、Docker 健康和 Backend /health。本轮没有新增浏览器验收；此前管理端证据仍以各节保存的截图/YAML为准。
- 下一步：宿主可用内存回到 2 GB 以上后，先按脚本状态检查启动现有 BGE 服务并确认真实预热成功；再选一份旧旅游原件放入隔离或版本化测试库，经过管理端上传/重建，核对分类、原件 SHA、Chunk/父子关系与版本元数据、向量/BM25、Trace 和实际检索。单份全链通过并留存前像后，才评估余下资料的批量修复；不直接批量改生产式 travel 库。

## 2026-11-14 — 文件质量任务收尾与下会话交接口径

- 用户问“测试文件覆盖测试面、生产文件覆盖业务、切分和 Trace 是否都没问题”：结论是否定的。已有部分原件哈希、入库样本、切分关系和管理端页面通过核验，不等于资料覆盖完整或全库验收通过。
- 本机资料根目录：C:\Users\wh\Desktop\企业ai平台。测试资料在“测试文件”，生产候选资料在“生产文件”；项目数据库/向量库存放在共享 Docker/PostgreSQL/Chroma 服务，不在这个桌面目录。不可把本地副本数量当作项目库数量。
- 28 份 RAG 上传样本位于 测试文件\RAG上传解析测试\testset\backend\evaluation\fixtures\rag_eval_kb\files；28 份原件 SHA 与清单相符。此前共享开发库记录为 23 份 active、4 份 failed、1 份未登记，尚未全部通过上传解析验收。百文档压力资料在 测试文件\检索问答测试资料\百文档压力测试库；旅游公开测试资料在 测试文件\旅游公开资料测试\维基百科来源文档；客服及业务夹具在 测试文件\客服与业务测试夹具。
- 生产候选资料根目录为 生产文件。旧旅游候选在 生产文件\旅游业务资料\旅游知识库原文副本_待生产核验（42 份）；静态旅游资料在 生产文件\生成业务资料\旅游助手（6 份）；制度副本在 生产文件\企业制度与内部资料\公共制度知识库副本_待生产核验；生产目录还包含待确认合同/案例、失败员工手册和停用知识库资料。客服正式业务政策目前不足；不能把虚构客服演练资料或演示种子政策当作生产事实。
- 关键资料说明：C:\Users\wh\Desktop\企业ai平台\README.md；知识库映射：C:\Users\wh\Desktop\企业ai平台\知识库与文件位置映射.md；验收和缺件记录在 测试文件\测试计划与验收文档\资料评审，包括 RAG评测资料充分性与生产资料缺口交接_2026-11-13.md、文件阶段验收_2026-11-13.md、生产资料验收_2026-11-13.md 和两份入库状态 CSV。README 中的开发库统计是 11/13 快照；后续数据库记录已变化，新的验收必须先只读重对账，不能照抄旧数字。
- 已知切分与 Trace 边界：62 份抽样中原件、分块、向量可对应；旧旅游资料仍有解析/切分版本及父子关系缺口。管理端新分块详情和部分新 Trace 已验证；历史 Trace 有缺失，不能说全部可追溯。当前可见管理端/Registry 数量和 README 旧快照不一致，需重对账。
- 新会话验收顺序：先只读重对账文件目录、SHA、项目 Registry 与管理端数量；按类型判定可复用/需重核/需补资料；逐项检查测试文件的上传解析和生产业务覆盖；资源允许后先在隔离/版本化测试库重建一份旧旅游文件，核对分类、切分、元数据、向量/BM25、Trace 和实际检索，再决定批量修复。不得直接批量重传生产式 travel 库。
- 当前环境门槛仍是可用内存约 1.56 GB、本地 BGE 未运行。恢复到安全资源且模型健康前不启动新上传/索引；已有共享服务可用不代表 BGE 可用。本轮不提交进度文档改动。

## 2026-11-14 — 本轮验收复核（本机资料、当前开发库、管理端 Trace）

- 继续使用工作树 `C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`、分支 `codex/file-quality-main-followup-20261114`、HEAD `19eecf2`。保留开始前已有的 PROGRESS 未提交内容和 `.playwright-mcp/`；本轮只追加本段记录，并修改管理端 Trace 对缺失计数的显示。未提交、合并或部署。
- 本机文件和项目库分开核对。测试样本 SHA：RAG 上传夹具 28/28 与 manifest 相同；项目 `rag_eval_kb` 当前 23 active（20 pass、3 soft_warning）、4 failed、`scanned_partial_fail.pdf` 未登记。4 份失败文件为 `doc_cn.pdf`、`progit_en_501p.pdf`、`progit_zh.pdf`、`report.pdf`；当前登记没有保存失败原因，不能推断根因。故 28 份样本覆盖了测试面，但不是 28/28 上传通过。
- 94 份百文档压力资料的 SHA 均能匹配项目 `rag_100_docs` 的 94 个 active 文档；当前均为 success/soft_warning。它们能支撑压力/解析覆盖检查，不代表生产业务正确。
- 旅游公开测试目录的 106 份正文是测试材料；与项目 `travel` 没有同 SHA 的原件，不能当成生产来源。客服专用语料 15 份正文中 13 份与当前客服 KB 同 SHA；`cs_eval_kb` 为空，夹具及虚构演练材料不能据此认定为正式客服政策。虚构物流 SOP 当前仍在 `policy_general` active/soft_warning，数据治理问题未修复。
- 生产候选：旧旅游目录 42 份原件 SHA 均与 `travel` 记录匹配，其中 41 active、1 pending_review；静态旅游 6 份均匹配 active/pass。`travel` 当前 47 active 中有 41 soft_warning、6 pass，另有 1 pending_review。六份公共制度副本与 `policy_general` 同 SHA，但均为 soft_warning；失败员工手册、待确认合同/案例、停用库候选不能视为已核准业务来源。合同/案例目录没有在本轮查询的非 deleted 项目记录中找到匹配原件 SHA。当前 auth KB 状态为 15 active、5 inactive；inactive `biz_product`、`policy_finance`、`policy_hr` 下仍有 18 条 active registry 记录，管理端 active 列表不显示；桌面对应副本只有 16 份，HR 本机副本比库记录少 2 份。
- 生产资料整体仍不足。缺件依据桌面《生产资料缺件清单》：正式制度及生效版本、经批准的客服服务规则/SLA、实际旅游服务范围/供应商/合同、生产 SQL schema 与数据/访问矩阵、角色及测试账号等。不能用测试问答、演示种子、虚构协议或本机副本补造事实。
- 切分抽查：测试 KB `travel_eval_kb` 的 `厦门-城市-厦门.md` 原件 SHA 与 Registry 一致，状态 success/pass；解析输出 145 段，语义切分保留 131 块；Registry 与 chunk_store 数量一致，23 Parent/108 Leaf、无孤儿 Leaf、空元数据为 0；版本为 parser `v2` / `StructureChunkStrategy:v3`。DB 有 131 条 `chroma` 向量记录，处理步骤记载 embedding、vector_write、bm25_write 均 131/131；本轮没有做实际 BM25/向量检索查询，故只验了索引写入记录与向量数量，不验检索效果。
- 旧旅游抽样 `福州-城市-福州.md` 本机 SHA `4E3D2410A0B98CC55EF7C7552B3C12479ECD5CA1790E3D7119BAC2BB34FA9F1A` 与交接记录一致；DB 111 个块（36 Parent/75 Leaf），数量吻合且无孤儿 Leaf，但 parser/chunk policy 版本均为空、质量为 soft_warning。当前 47 个 active travel 文档有 41 个缺 parser 或 chunk policy 版本；1 个文档没有 Parent 块，0 个 Leaf 缺 parent 指针。不能将少数结构通过外推为旧库全量通过。
- Trace 持久化重新计数：active KB 中管理端可见 213 份 active 文档；22 份没有非空 Trace ID，174 份有 ID 但 `trace_store` 无详情，17 份有持久化 Trace 详情。更正此前交接口径中的 16/191 旧计数；以上是当前 DB 快照，内存缓存可能使个别记录仍可打开，但未逐一打开 213 条，不能说全量可追溯。
- 已登录管理端此前实际打开 `travel_eval_kb` 的新 Trace `da4d80d49636`，文件名 `厦门-城市-厦门.md`、状态 SUCCESS、131 chunks、15 spans；Trace 与 run ID `d6eeb608-023d-4815-816d-cf865f8ce1f5` 对应。页面的 embedding 计数显示 0/0，但处理步骤及 131 条向量记录均为 131。根因是前端将 Trace 中缺失的 `attempted/succeeded` 当成数字 0。本轮已修正为缺字段时显示“Trace 未记录计数”，真实记录的零仍显示 0；这避免误报，但没有补造 Trace 缺失的计数。
- 修复验证：`frontend-admin` 的 Trace 指标定向测试 10 passed；匹配 `page.test.tsx` 的 10 个页面测试 54 passed；`npm exec tsc -- --noEmit --pretty false` 通过；`git diff --check` 通过。当前 Playwright 默认会话打开管理端被重定向到登录页，匿名 Trace API 返回 401，因此**没有**在本轮修复后用已登录浏览器确认新文案；页面运行态验收仍为 UNVERIFIED。此前成功打开的浏览器 Trace 是修复前证据。
- 本轮没有上传、重建、重索引、写数据库或改线上生产。模型服务上次状态检查为本地 BGE 未运行；曾见宿主内存低于 2 GB，后续检查回升，但没有启动模型。当前也没有对旧旅游库做批量修复或对隔离库重建。
- 本轮结论：测试文件可复用作上传/压力测试素材，但上传结果未全通过；生产资料不足；新样本结构抽查通过、旧旅游版本元数据不足；新 Trace 单样本可追溯，历史 Trace 不完整。后续需先补齐并核准业务原件，再在资源和模型健康、可回滚隔离 KB 满足条件时做单份重建/检索验收；Trace 页面修复需在已登录管理端复验。当前验收总体为 PARTIAL / UNVERIFIED，不得报告全库 PASS。

## 2026-11-14 — 跨类别样本与逐文件 Trace 复核

- 对五类文件再次逐一对了本机原件 SHA 与当前 Registry：RAG 测试 PDF `contract_gf2017_0201_cn.pdf`=`7FAA2DF1B4B2BB05501CB5E003F3F7110152F1F5A87C8C3E71F8983FC86B0E96`；压力 PDF `contract_办公室租赁合同.pdf`=`244121407CCB4882C5F21B990CB53D510CDB07DF6F89002F76B8C86DE086843D`；客服夹具 `aftersales-details.md`=`8077E1CBC8240B3076B7DA43CE046BF7962BA6267AF9B2085E2786621D9A4168`；虚构夹具 `logistics_物流SOP.docx`=`497939550A9192E40B9D4774EF5D6E16415956698712F6B5041C4EC6F8CE102A`；静态旅游候选 `厦门-鼓浪屿历史背景.md`=`6F6DE59DFF9F9A14A358B5FD738977C90DBCBD8CD25F302190AF205DCB225994`。五份本机 SHA 均与对应项目记录相同。
- RAG PDF 当前 active/soft_warning，parser v3、切分策略 v3，511 Registry 块=511 chunk_store=511 Chroma 向量；95 Parent/416 Leaf、无孤儿 Leaf、BM25 写入步骤 511/511。解析 629 段、OCR 9 页；元数据抽取为 fallback。该文件 Registry 行没有可供管理端文件按钮使用的非空 Trace ID；run 中虽有 Trace ID，但没有持久化 Trace 详情。
- 百文档压力 PDF 当前 active/soft_warning，1 个 Registry 块=1 chunk=1 向量，BM25 步骤 1/1；parser/chunk 版本为空。单块没有 Parent 属于该样本结构。Trace ID `4acab375ffee` 在 Registry 操作记录中存在，但 `trace_store` 无详情。
- 客服夹具 `aftersales-details.md` 当前误存在 `cs_policy` active/pass：33 块（8 Parent/25 Leaf）、33 向量，块和向量均有文件来源字段；Trace `f61d76481b0f` 已持久化，详情文件名准确，Trace 阶段均为 success。当前处理步骤未记录 BM25 写入阶段，BM25 实际条目未验；这仍是测试夹具，不证明正式客服政策覆盖。
- 虚构物流 SOP 当前在 `policy_general` active/soft_warning：5 块（1 Parent/4 Leaf）、5 向量、BM25 步骤 5/5，但 parser/chunk 版本为空；Trace `430745d71a7e` 没有持久化详情。它仍误留在制度库，本轮未改库：按任务约束，修复前要备份并在隔离/版本化测试库先跑通；本地 BGE 未运行，当前不做重处理。
- 静态旅游候选 `厦门-鼓浪屿历史背景.md` 当前 `travel` active/pass：parser v2、切分策略 v3，10 块（4 Parent/6 Leaf）、10 向量、BM25 写入 10/10；Trace `a79c91d92d59` 已持久化，Trace 标签文档名与 Registry 文件名相同，阶段均为 success。此处结构与处理通过不构成旅游业务事实审批。
- 旧旅游样本 `福州-城市-福州.md` 当前 Trace ID `fd7a2d2d172e`，但 `trace_store` 无详情；详情页无法依靠持久化记录打开。旧旅游版本元数据问题仍待隔离重建验收。
- 本轮生成逐文件 Trace ledger：`D:\tmp\evaluation-center-trace-ledger-20261114-225450441.csv`，共 213 行，对应当前 active KB 的每份 active 文档，列出 KB、文件名、doc_id、最近非空 Trace ID、持久化详情状态及 Trace 名称匹配结果。汇总为无 Trace ID 22、ID 无持久化详情 174、详情已存 17。17 条详情的 Trace `tags.doc_id` 全部匹配当前 Registry；其中 13 条标题文件名匹配、4 条 `travel` 标题是上传时生成的临时名（`厦门-鼓浪屿建筑风格.md`、`杭州-世界遗产主题.md`、`杭州-西湖与西溪主题线路参考.md`、`福州-城市文化主题.md`）。这四条 Trace 阶段成功且 doc_id 正确，但页面标题不对应当前文件名。
- 已修正文件列表的 Trace 跳转：附带该行 Registry 文件名，详情页优先显示它；直接从通用 Trace 列表打开且没有文件名参数时仍显示 Trace 自身标题。指标修正与文件名修正的定向测试 11 passed，TypeScript `--noEmit` 通过。
- 管理端仍未完成修复后浏览器验收。当前 `mcp__playwright` 会话进入 `/login`，匿名 Trace API 返回 401；本机用户已登录的浏览器标签不在当前可控浏览器会话中。共享 `agent` 栈已按 7.57 GB 预检恢复，当前 21 容器健康；宿主可用内存降至约 2.18 GB。仓库 `docs/operations/commands.md` 要求共享环境不擅自重建服务；因此不重建共享 `frontend-admin` 镜像，也不把工作树源代码改动说成已上线。浏览器运行态仍 UNVERIFIED。
- 当前可完成项：跨类别样本 SHA/切分/向量和处理步骤抽查、213 条 Trace 持久化台账、Trace 标题和缺计数的前端修正及定向测试。仍不能通过安全写入解决的项：缺失的正式业务原件、旧旅游库版本/切分修复（需健康模型和隔离试点）、Trace 历史详情缺失（不从摘要伪造完整历史）、共享管理端修复后浏览器验收。

## 2026-11-14 — 总目标收口前资源门槛复查

- 用户重申总目标仅为本机资料覆盖、项目知识库切分质量、管理端 Trace 详情，本轮按此收口。
- 最后资源复查：宿主可用内存 1.56 GB；既有 `agent` 共享栈 21 个容器运行；本地 BGE 显示 not running。按任务门槛，不启动模型、不重建/索引、不重建共享管理端服务。
- 当前验收结论仍为 PARTIAL / UNVERIFIED：本机 SHA、项目 DB、跨类切分样本和 213 行 Trace 台账已核对；前端缺计数/错标题修正仅在工作树，定向测试 11 passed、TypeScript 检查通过，但当前 Playwright 会话仍跳登录，修复后的管理端运行态未验。正式业务资料、历史 Trace 持久化缺口不能由本机样本或摘要补造。

## 2026-11-15 — 浏览器续验、生产政策补齐与验收收口

- 工作树仍为 `C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`，分支 `codex/file-quality-main-followup-20261114`，HEAD `19eecf2`。保留了既有进度内容和 `.playwright-mcp/`；本次仅追加本记录与本任务的 Trace 页面改动，不提交、不合并、不部署。
- 按用户授权生成四份可直接作为生产运行规则使用的政策文件：知识库准入与事实答复、客服答复与升级、旅游动态信息核验、内部知识访问与敏感资料发布。四份正文均有版本、生效日期、执行规则和发布检查，不含虚构的企业专属制度、退款数字、SLA、供应商、SQL Schema 或交易事实。桌面生产目录现有 95 个文件；登记表 12 行，逐行相对路径均存在且 SHA-256 全匹配。四份新增政策 SHA 分别为 `4dba63a6dd1f8e3a8b3cff1f473377b1b8e60932ec9d92d2a9a7a8fd44a32de8`、`dcb3256af9c524666314054e0589ba0473973585fe47c42ee1aa7eb7f84ee14a`、`f24773817bb372f842f8b7ccdcd96b79b6008bd2bfda84c2b76054e7aacd9e59`、`c4699546860de878acdee40f5ef6932528271f5bac2eec567403a1ba2add7960`。当前项目 Registry 对这四个哈希均无记录；本机源文件可用不等于项目库已覆盖。
- 测试资料复核结论不变：28 份 RAG 原件 SHA 均与清单相符；项目 `rag_eval_kb` 为 23 active（20 pass、3 soft_warning）、4 failed、1 未登记，因此覆盖了上传测试素材面，但不是 28/28 通过。94 份压力资料均能匹配项目记录，状态全为 soft_warning。测试旅游文章、客服夹具和虚构演练材料仍不作为生产业务原文。
- 生产候选仍不足以覆盖项目业务事实：42 份旧旅游候选中 41 active/soft_warning、1 pending_review；6 份静态旅游资料为 active/pass。新政策补齐了助手行为规则，但正式 HR/财务制度、实际商家合同与 SLA、退款/配送/退改条件、旅游服务范围与供应商、生产 SQL Schema/指标口径/访问矩阵仍缺。线上生产库未访问、未写入。
- 切分仍是分层结论：测试 `travel_eval_kb` 厦门样本为 131 chunks（23 Parent、108 Leaf），无孤儿 Leaf；131 向量记录，BM25 写入步骤 131/131。旧 `travel` 库最近一次成功数据库快照为 1483 chunks（383 Parent、1100 Leaf），1058 Leaf 没有 parent 指针，358 Parent 没有子块，0 个带非空 parent 指针的孤儿 Leaf；47 active 文档中 41 份缺解析器或切分版本元数据。快照来自前一节记录的 2026-11-14 数据库只读核对；本次无数据库写入。
- 真实浏览器在本工作树单独启动的管理端开发实例 `http://127.0.0.1:3201` 上验证；没有重建挂有多个工作树配置的共享 Compose 管理端。Trace `666de09032f8` 打开后，详情页正确显示文件 `杭州-世界遗产主题.md`，文件 doc_id 与 Trace tags 匹配，状态 SUCCESS、17 spans、11 chunks，向量嵌入 11/11；原 Trace 的 question 是临时上传名，页面使用文件列表传入的真实文件名。浏览器快照为 `.playwright-mcp/page-2026-11-14T15-56-17-088Z.yml`。
- 逐文件浏览器/API 盘点见 `.playwright-mcp/trace-entry-audit-ops-admin-2026-11-14.json`：当前本机 `ops_admin` 按权限可见 94 份文件、8 个业务 KB；其中 4 个 Trace API 详情能打开并匹配文件身份，90 个返回 404；其余 90 个里 75 份能打开 doc_id/run_id 均匹配的真实处理运行记录，15 份没有可用运行记录。文件 `厦门-鼓浪屿历史背景.md` 的 Trace `a79c91d92d59` 在数据库存在且 doc_id 匹配，但缺 tenant_id，管理员 Trace API 按既有租户规则返回 404；页面正确转为“处理运行记录（非 Trace）”，显示匹配的运行 ID、文件 SHA 和 11 个已保存阶段，截图见 `.playwright-mcp/page-2026-11-14T15-49-05-373Z.yml`。不可把这 75 份运行记录称为完整 Trace，也不补造历史归属。该 API 盘点只覆盖此账号可见的 94 份，不代表测试库或其他租户可见。
- Trace 页面修复补上了三项核验：文件入口同时传文件名、doc_id、run_id；Trace 的文档身份缺失或不匹配时不把它绑定到当前文件；Trace 不存在时只展示经 API 按 doc_id/run_id 校验的实际处理运行记录，并清楚标为“非 Trace”。Trace 计数缺失显示“未记录”，不再当成 0。定向测试 15 passed，`npm exec tsc -- --noEmit --pretty false` 退出码 0，`git diff --check` 通过。
- 未执行任何上传、重建、重索引或数据库写入。备份 `D:\tmp\rag-policy-pilot-backup-20261114-232740\agent_memory.dump`（432,736,719 bytes，SHA-256 `F10AD5E64AE6D3BD63303731BD02EC9488EC2DFD134176BF095AC30696B7B371`）已在此前创建并通过 `pg_restore --list` 校验；本次没有使用它回滚，因为没有写入。上传试点仍未完成：本机账号为 `ops_admin`，创建隔离 KB 的项目 API 明确要求 `super_admin`；现有 `cs_eval_kb` 是 audience=test，也不能由该账号绕过权限上传。资源复查时宿主空闲内存约 3.25 GB，本地 BGE 未运行；未启动模型。后续 Docker 环境更新后 Docker named pipe 不可用，本次没有尝试启动共享栈。
- 总体结论保持 PARTIAL / UNVERIFIED：政策源文件和管理端可访问的 Trace/运行记录已按用户角度实测；测试上传并非全通过，生产事实缺件仍在，旧旅游切分存在大量父子关系缺口，15 份管理端可见文件无可打开的历史 Trace 或运行阶段，新增政策尚未入项目库。下一步必须由具备本机 super_admin 权限的受控会话创建隔离/版本化试点 KB，并在重新确认资源与 BGE 健康后用一份已生成政策文件上传验解析、切分、向量/BM25、检索和新 Trace；未完成此项前不批量修复 travel，也不把生产政策源文件说成项目运行时已覆盖。

## 2026-11-15 — 规则优先与 LLM 切分兜底设计

- 用户确认的方向是：已识别结构优先使用确定性切分规则；只有原文已可解析但结构无法识别或规则质量不足时，才使用 LLM 提供切分边界。LLM 兜底频率需按格式、路由原因和策略版本统计；同类问题反复出现或超过按风险设定的预算时，进入解析器/规则升级与回归验证，不能长期把模型兜底当常规路径。
- 已将设计目标写入 `docs/domains/rag.md`，并明确标记“当前尚未实现”。设计包含原文边界引用、覆盖/父子/来源定位门禁、失败不得发布、独立 Trace 与版本/用量字段，以及按分类型基线驱动的升级闭环。没有设未经验证的通用兜底率阈值。
- 该设计没有更改切分实现、模型配置或数据库；没有上传或启动模型。企业文档参考了结构识别后再分块的布局管线、逐文件调试/复验及索引运行错误和告警监控做法。当前项目的 LLM 切分兜底仍未实现，需后续在隔离测试 KB 完成单文件试点后再评估启用。

## 2026-11-15 — 规则优先兜底实现、统计和管理端复验

- 本节更新上一节“仅设计、尚未实现”的历史结论：当前工作树已实现规则优先的 LLM 边界兜底。只有路由未识别结构，或规则切分覆盖审计发现缺口时才尝试 LLM；LLM 只分组解析器已有 AST 节点 ID，不生成或改写正文；分组必须保持原顺序、无缺漏重复且不跨章节，失败时保留规则结果。Parent/Leaf 仍由确定性规则代码生成。
- 处理阶段 `chunking_llm_fallback` 会记录格式、触发原因、规则策略/版本、解析器版本、调用/采用/失败/跳过和输出块数。管理员统计接口按账号可见的租户、知识库、权限范围和部门汇总最近 30 天及每日趋势；默认同类调用达到 5 次只提示评估升级规则，可由 `RAG_LLM_CHUNK_RULE_REVIEW_THRESHOLD` 调整，不自动修改规则。管理端已增加“规则切分后的 LLM 兜底记录”面板。
- 真实浏览器复验使用本工作树源码版前端 `http://127.0.0.1:3201` 和源码版 API。临时本机适配器仅允许 GET/HEAD；它先用既有共享网关验证当前 super_admin Bearer，再向源码 API 注入其可信身份，直接读取当前开发数据库；没有写数据库、上传、索引或重建。页面统计接口 `GET /api/rag/chunking/llm-fallback-stats?days=30` 和手动刷新均返回 200；响应为候选/调用/采用/失败/跳过/输出块全 0，`by_type`、`daily` 为空，与数据库 `chunking_llm_fallback` 阶段 0 条一致。没有实际调用模型，所以真实 LLM 输出、Provider 故障和阈值告警仍未验收。
- 共享管理端 `http://127.0.0.1:3200` 以真实登录态请求同一统计接口返回 404“请求的资源不存在”，证明共享后端尚未加载本工作树代码。本轮未重建共享 Compose。源码版页面和 API 验证不等于共享版已部署。
- Trace 入口真实浏览器复验两类记录：`厦门-城市-厦门.md` 的入口带入文件名、doc_id、SHA 和 run_id，详情显示 SUCCESS、131 块和 15 spans；页面将未写入的计数显示为“Trace 未记录计数”。`faq-exchange-repair.md` 的历史 Trace 详情不存在，页面明确标为“处理运行记录（非 Trace）”，用相同 SHA 找到 run `95bf4228-6f9f-4c9b-9ec9-1f32cd98d99f` 和 11 个阶段，并说明不能还原缺失 Span。浏览器证据已追加到 `.playwright-mcp/`：`page-2026-11-14T23-35-35-628Z.yml`、`page-2026-11-14T23-40-15-012Z.yml`、`page-2026-11-14T23-41-10-823Z.yml`、`page-2026-11-14T23-48-08-885Z.yml`、`source-stats-api-response-20261115.json`、`source-stats-network-20261115.txt`、`source-trace-network-20261115.txt`、`source-policy-registry-check-20261115.json`、`shared-stats-runtime-check-20261115.json`。旧 `.playwright-mcp/` 产物均未覆盖。
- 生产资料事实核对：已生成的六份静态旅游资料所引用的福州政府、杭州市政府、杭州市文广旅游局线路 PDF、UNESCO 来源与正文关键事实相符。它们只支持列出的历史/文化/主题线路事实，不证明本平台服务、供应商、价格或实时运营覆盖。权威来源：[福州城市文化资料](https://www.fuzhou.gov.cn/zgfzzt/lswhmcgwh/zwgk/ztzl/bhqy/202403/t20240315_4792675.htm)、[三坊七巷官方介绍](https://www.fuzhou.gov.cn/zgfzzt/zjrc/mdfc/lswh/202508/t20250806_5058387.htm)、[杭州世界遗产政策](https://zfgb.hangzhou.gov.cn/10/107220263/t110220263074/530579.shtml)、[杭州西湖与西溪官方线路 PDF](https://wgly.hangzhou.gov.cn/module/download/downfile.jsp?classid=0&filename=c3ccb4250d3143a09047ee20fbe1170c.pdf)、[UNESCO 鼓浪屿页面](https://whc.unesco.org/en/list/1541/)。
- 为使授权生成的客服政策可安全使用，发现 v1.0 在“无规则”场景默认声称“已转交人工核实”，与真实转接结果未确认时不得承诺的规则冲突。保留原件，生成并登记 v1.1 `客服知识答复与工单升级政策_v1.1.md`（SHA-256 `402d382778ecc87190bf88b17bccbd16dfe0c259ec4eafb3c73f950b91829591`）：只有接口确认成功时才报告已转接；状态未知时标为未知。桌面 `资料登记表.csv` 13/13 路径存在且哈希匹配，映射文件补充了 v1.1 当前版本及 v1.0 历史版本关系；两份登记文件的写前备份在 `D:\tmp\资料登记表-before-policy-v1.1-20261115.csv` 和 `D:\tmp\知识库与文件位置映射-before-policy-v1.1-20261115.md`。
- 源码版管理端按文件名查询四类现行政策及客服 v1.0，5 次 API 请求均为 200、结果均为 0；新生成的政策文件仍不在项目 Registry。桌面生产目录现有 96 个文件；42 份旧旅游候选和 6 份静态旅游资料的数据库状态沿用前文只读对账：旧资料 41 active/soft_warning、1 pending_review，静态资料 6 active/pass。测试资料对账不变：28 份 RAG 原件 SHA 全匹配，但项目 `rag_eval_kb` 是 23 active（20 pass、3 soft_warning）、4 failed、1 未登记；94 份压力文件均匹配项目记录且全为 soft_warning。正式 HR/财务制度、适用商家的合同/SLA、真实退款配送/退改条件、旅游服务范围与供应商、生产 SQL Schema/指标口径/权限矩阵仍缺，生成的行为政策不能替代这些事实资料。
- 切分与索引结论不变：`travel_eval_kb` 厦门样本的 131 块（23 Parent、108 Leaf）结构和父子关系通过；该次 131 条向量及 BM25 写入计数通过，但没有实际向量/BM25 检索。旧 `travel` 库仍有大量 Leaf/Parent 关联缺口和 41 份活动文档缺解析/切分版本；没有批量修复。
- 实现的定向验证此前已实际执行：后端相关测试 74 passed、前端相关测试 66 passed、TypeScript `--noEmit`、`py_compile` 和 `git diff --check` 通过。本轮没有改运行时代码；最后仍会对新增记录复跑 `git diff --check`。开发浏览器、源码 API 和本地只读适配器均已停止。
- 资源复核：可用内存约 2.92 GB；本地 BGE `127.0.0.1:8088/health` 不可达；共享 `agent` 栈的 app/worker healthy，但 Redis 容器正在重启。没有启动 BGE、上传、重建或写数据库。下一步只有在模型健康且资源重新核对后，才可用备份和隔离/版本化测试 KB 对一份政策文件跑完整解析、切分、向量/BM25、检索和新 Trace。当前总体仍为 `PARTIAL / UNVERIFIED`：规则兜底代码和统计页面源码已验，真实 LLM 兜底、共享运行时统计、生产业务事实覆盖和历史 Trace 补齐仍未通过；不批量改 `travel`，不把本机政策说成项目已发布。

## 2026-11-15 — LLM 兜底真实调用失败定位与重复调用修复

- 补充上一节记录后的运行验证：只用 6 段虚构设备故障文本在临时目录调用当前工作树 `parse_and_chunk_full`，未上传文档、未写知识库或向量索引。路由确认为 `RecursiveChunkStrategy`，触发 `no_recognized_structure`；解析器产出 6 个原文节点。真实调用前执行 `registry_store.refresh_registry()` 成功，切分角色继承已登记的 `doubao-seed-2.0-mini`；权限上下文使用本机真实 super_admin 登录身份。模型健康端点返回 healthy。
- LLM 请求失败后，原规则结果安全保留：2 块（1 Parent、1 Leaf），覆盖缺口 0；本次没有 `chunking_llm_fallback` 持久化阶段，当前数据库此阶段总记录仍为 0，不能把临时内存记录冒充管理端累计数据。
- 失败账务明细出现同一 request 的两条 `primary` 记录，均为 `error:APIConnectionError`，无 HTTP 状态码；预算日志对两次失败均按 `call_failed_possibly_billed` 保守结算，预留金额各 0.5（项目预算口径），实际供应商是否计费无法从本机证据确认。主机与 worker 容器到该 HTTPS Provider 的 TCP 443 均可达；这不能证明 API 层请求成功。为避免继续产生可能费用，本轮不再发真实模型请求。
- 已定位重复请求原因：`invoke_metadata_llm` 原来把 `invoke()` 放在 thinking 能力检查的 `try/except` 内；对于当前模型，能力检查确实返回 thinking 可关闭参数。Provider 异常被错误当作能力检查异常捕获，随后又用普通参数再次请求。现已将调用移出该捕获范围，只有能力解析异常才改走普通参数；Provider 错误直接向上抛，由切分兜底保留规则结果。新增回归测试锁定“第一次 Provider 错误只调用一次，不发第二个请求”。定向测试 `backend/tests/infra/test_llm_role_resolution.py`：7 passed；`py_compile` 通过；代码修复后及本节文档追加后的 `git diff --check` 均通过。
- 调用前数据库备份：`D:\tmp\rag-llm-fallback-live-test-backup-20261115\agent_memory.dump`，366,902,659 bytes，SHA-256 `29E39C3A6C6AB5929ECDF7C6FA2199FFB8F292D8E89D26ABAB7EEC9073290730`，`pg_restore --list` 列出 1,378 项。备份早于本次自动账务记录；没有恢复它，以免抹掉模型调用的真实账务留痕。
- 统计功能仍是源码版通过、共享管理端未上线：源码 API 与页面先前只读复验为 200、当前 0 条；共享管理端接口为 404。LLM 调用没有生成或伪造统计阶段。
- 最新资源检查：可用内存约 3.46 GB，本地 BGE 仍不可达；共享 `agent-redis-1` 仍在 Restarting，app 与 RAG worker healthy。本轮不上传、不重建、不索引、不改共享运行服务。全目标仍为 `PARTIAL / UNVERIFIED`；后续真实模型成功路径需在获授权的当前代码运行环境内用隔离单文件再验，完整索引试点还需 BGE 健康且 Redis 稳定；正式企业业务事实和历史 Trace 缺口仍需真实来源，不能生成虚构事实补齐。
## 2026-11-15 — 当前库复核与验收结论更正

- 本节以当前只读数据库、桌面文件 SHA、管理端浏览器/API 重新对账；它更正此前计数，不覆盖历史记录。工作树仍为 codex/file-quality-main-followup-20261114，HEAD 19eecf23302103f8431b9f2d59df43f6d9a0d202。
- 上传夹具：桌面 RAG 28 份原件与仓库 manifest 为 28/28 SHA 匹配。当前 rag_eval_kb 有 27 个同名且哈希匹配的登记：23 active/success（20 pass、3 soft_warning）、4 failed；scanned_partial_fail.pdf 有一条处理运行但未进 Registry，运行错误为 request token budget 超限。4 个失败文件是 doc_cn.pdf、progit_en_501p.pdf、progit_zh.pdf、report.pdf；失败阶段和门禁错误见当前 rag_processing_runs。英文 ProGit 原件的 Poppler 只读检查报无效 XRef、缺失 endstream/trailer；本轮没有替换或重传。故测试文件覆盖了 PDF、DOCX、Markdown、TXT、CSV、XLSX 等上传类型，但 28 份处理结果并非全部通过，且这 5 份失败没有在 Registry 形成 5 个可用文档。
- 压力样本：桌面 94/94 SHA 匹配 rag_100_docs 的 94 个 active 文档；94 个全为 soft_warning，94 个缺 parser 或 chunk policy 版本。不能按登记存在推成压力测试通过。
- 旅游公开测试目录含 106 份文章正文和 1 份 资料说明.md（共 107 个 Markdown）；只用于测试。客服/制度虚构夹具也按测试资料处理，不得作为正式业务政策。
- 生产候选：42 份旧旅游候选加 6 份静态旅游资料共 48 份，SHA 全部匹配当前开发库 travel。其中旧候选 41 active/soft_warning、1 pending_review；6 份静态资料 active/pass，静态资料处理版本为 parser v2 / StructureChunkStrategy:v3。这 6 份在 travel，不在 travel_eval_kb；travel_eval_kb 的 131 块厦门城市样本是另一份测试文件。桌面目前有 5 份授权生成的生产行为政策文件（客服 v1.1 为当前版，v1.0 保留历史），它们均未进入项目 Registry；它们可规定答复和升级行为，但不能补造正式 HR/财务制度、真实客服 SLA/退款配送条件、旅游服务范围/供应商/合同或 SQL 指标与权限事实。
- 本节更正旧库父子关系结论：之前记录称福州旧样本无孤儿 Leaf，与当前 chunk_store.parent_chunk_id 原始行不符。现在 travel 共 1,614 块（406 Parent、1,208 Leaf），1,058 个 Leaf 的父块指针为空，358 个 Parent 没有任何 Leaf 指向；福州-城市-福州.md 的 111 块含 36 Parent、75 Leaf，75 个 Leaf 的父块指针和 chunk metadata 中的父块指针均为空。数据库里的块数和向量数相等不代表父子结构可靠。travel 47 个 active 文档中 41 个仍缺 parser 或 chunk policy 版本；本轮没有改写旧库。
- 新样本结构仍有正向证据：travel_eval_kb/厦门-城市-厦门.md 为 131 块（23 Parent、108 Leaf、无空父指针 Leaf）、131 条 Chroma 向量，处理步骤记录 embedding/vector_write/BM25 为 131/131；上传测试 PDF contract_gf2017_0201_cn.pdf 为 511 块（95 Parent、416 Leaf、无空父指针 Leaf）、511 条向量、BM25 写入步骤 511/511。上述是解析/结构/索引写入证据，没有执行实际向量或 BM25 检索，不能据此宣称召回质量通过。
- Trace 逐文件 API ledger .playwright-mcp/trace-entry-audit-superadmin-all-213-20261115.json 覆盖管理端可见的 213 份文件、3 页列表：191 份有 Trace ID；17 个 Trace API 详情返回 200 且 doc_id/kb_id 对应，其中 13 个标题文件名相符、4 个原始 question 仍是上传临时文件名；其余 196 份有 doc_id 和文件 SHA 匹配的持久化处理运行记录，未解析出 Trace Span；ledger unresolved 为 0。此结论是 17 份可看完整 Trace、196 份只能看处理运行记录，不是 213 份完整 Trace。
- 已登录 super_admin 浏览器在源码前端 127.0.0.1:3201 实际打开了 杭州-世界遗产主题.md Trace：页面文件名正确、SUCCESS、11 chunks、17 spans；同时打开 help_center_mixed.md 的运行详情，显示 11 个阶段并标为 处理运行记录（非 Trace）。共享管理端仍使用旧运行代码，文件 Trace 03ff220fd5ed 返回 404 后可由匹配的 run fbe58d24-1a34-4d38-b2fb-c7ca148fe70c 打开非 Trace 运行详情。没有逐一用浏览器导航 213 个页面；全量是 API 对账，页面只对代表性成功 Trace 与运行记录做了真实点击/打开验证。
- 规则优先、LLM 兜底的源码实现及统计口径已定向测试：每个 processing run 统计候选、调用、采用、Provider 失败、边界校验拒绝、未采用、跳过、输出块及格式/原因/版本和每日趋势；重复处理同一文件会重复计 run。LLM 只分组既有 AST 节点，规则仍生成 Parent/Leaf；同类次数达到阈值只提示人工升级规则，不自动改规则。当前 DB 兜底阶段 0 条。管理端源码对共享 API 请求 /api/rag/chunking/llm-fallback-stats?days=30 返回 404，因为共享后端未运行本工作树版本；本轮没有部署或重建共享服务。之前真实模型调用失败为 APIConnectionError，Provider 重复请求已修复；为避免重复产生可能费用，没有再次调用。
- 最近一次资源复核：宿主可用内存约 3.29 GB；本地 BGE 127.0.0.1:8088 不可达；共享 app/worker healthy，但 Redis 容器正在重启。本轮只读，没有上传、重建、重索引、启动模型或写数据库。已有数据库备份仍可供后续受控回滚；完整单文件隔离试点需待模型/索引服务健康后再做。
- 代码复验（本节之前已实际运行）：后端 LLM 兜底/统计相关 19 passed；管理端文件面板测试 6 passed；TypeScript --noEmit 通过。当前总体仍为 PARTIAL / UNVERIFIED：上传样本有失败、生产运行库未收录新生成政策、旧 travel 父子指针有大量缺失、没有真实检索验收、共享管理端统计接口未更新、历史完整 Trace 仅 17/213；不报告全目标 PASS。
- 统计接口运行态补充：源码前端启动初期曾有一次请求返回 200；热更新后及后续重复请求多次返回 404，最终状态未稳定通过。根因尚未定位；不能以单次 200 宣称管理端持续统计可用。

## 2026-11-15 — 统计漏记修复与当前管理端复验

- 本轮重新检查宿主内存为 1.96 GB，低于既定 2 GB 门槛；Docker 仍是共享 `agent` 栈（20 个 running、1 个 restarting），BGE `/healthz` 为 200。没有重试模型或索引任务，也没有启动或重建服务。
- 定位并修复统计漏记：原 SQL 通过 `EXISTS doc_registry` 限制处理记录，失败/未发布文档没有登记行时会从统计中消失。现改为按处理阶段保存的 tenant、KB、permission_scope、department 快照过滤，并由当前请求的 KB 授权、用户权限及部门范围收窄；不再要求 Registry 已发布。这样失败的 LLM 切分调用也进入规则升级统计，同时不展示文档名或正文。
- 定向验证 `backend/tests/api/test_rag_processing_lineage_api.py`、`backend/tests/rag/test_processing_lineage_pg.py`、`backend/tests/rag/test_llm_fallback_chunking.py`：12 passed；相关文件 `py_compile` 和 `git diff --check` 通过。使用真实开发库和本工作树 API 路由的只读 TestClient 请求返回 200：最近 30 天候选 1、LLM 调用 1、Provider 失败 1、采用 0；分类为 `txt / no_recognized_structure / RecursiveChunkStrategy v2 / parser v1`，阈值 5 尚未提示升级。该记录来自真实失败试点，不是模拟统计。
- 隔离试点运行 `llm-pilot-1016f26d450e4773b36d1a1008f636b0` 曾因进程内覆盖 embedding URL 导致 `INDEX_EMBEDDING_MISMATCH`，处理阶段虽成功并记录了真实 LLM Provider 失败，但索引未发布。已用试点专属状态门禁把遗留 `indexing` 标为 `failed/publish_guard`，避免管理端误显示仍在执行。项目 Registry 和 chunk_store 仍为 0；该试点的两个候选向量集合为 0 行；Trace `7708a4b7b978` 未持久化，管理端 Trace API 为 404，文档处理详情因没有 Registry 行返回“文档不存在”。没有伪造完整 Trace 或已发布文件。
- 写入状态前复核备份 `D:\tmp\rag-llm-fallback-pilot-backup-20261115T0215\agent_memory.dump`，SHA-256 `9FD0D9113EB66525A0B4E5114586DE0026B5F0155783EAE6A06F123750A6B2AE`，`pg_restore --list` 1,378 项。已修复候选 BM25 路径计算与清理不一致的问题；新回归测试 `test_cleanup_candidate_by_generation_removes_bm25_sibling_stage` 通过。仅归档本次试点生成的 staging 到 `D:\tmp\rag-llm-fallback-pilot-20261115\archive-staging-d48e671f1ddf413c`，活动 BM25 `PUBLISHED.json` 保留。
- 当前真实 super_admin 浏览器/API 证据：刷新会话 200；隔离 KB 文档查询 200、0 份文件；共享管理端 `/api/rag/chunking/llm-fallback-stats?days=30` 为 404，页面也没有“规则切分后的 LLM 兜底记录”面板，说明 3200 运行栈仍不是本工作树代码。此环境未获部署授权，未更新共享栈。当前源码 API 路由配真实 DB 的进程内验证是 200，不能等同于已上线页面。
- Trace 实际页面复验：现有 `666de09032f8` 的管理端 Trace API 为 200，doc_id `e5d81e77f9`、KB `travel`、18 spans；数据库 Registry 文件名是 `杭州-世界遗产主题.md`。共享管理端页面仍显示临时名 `f8a045c71621.822e90b3aec84c7c.md`，即使 URL 带入 `documentName`、doc_id、SHA 和 run_id，证实文件名修复尚未进入 3200。试点 Trace 返回 404。浏览器快照为 `.playwright-mcp/page-2026-11-15T08-56-47-763Z.yml`、`.playwright-mcp/page-2026-11-15T08-57-38-114Z.yml`、`.playwright-mcp/page-2026-11-15T08-57-58-303Z.yml`；均保留。
- 当前阶段结论仍为 `PARTIAL / UNVERIFIED`。可确认统计代码已能按失败调用/类型/每日趋势统计，但共享管理端尚未运行它；全程索引隔离试点未发布且没有持久 Trace；试点重试需可用内存至少达到 2 GB 后保持 DB embedding 配置不变，只用 socket 别名修正本机容器主机名。生产候选业务缺口、旧 `travel` 父子指针及历史 Trace 详情缺失仍按前节处理，不因本轮统计修复而改变。

## 2026-11-15 — 持续统计、候选切分运行态和 Trace 复核

- 继续在工作树 `C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`、分支 `codex/file-quality-main-followup-20261114`、HEAD `19eecf23302103f8431b9f2d59df43f6d9a0d202` 验收。仅追加本节；保留先前未提交进度和 `.playwright-mcp/` 产物；没有提交、合并或部署。
- 文件对账结论不变：28 份 RAG 上传样本原件 SHA 28/28 与 manifest 相符，但当前 `rag_eval_kb` 是 23 active（20 pass、3 soft_warning）、4 failed、1 个解析运行未登记；94 份压力资料 SHA 94/94 与 `rag_100_docs` 相符，但均为 soft_warning 且缺版本字段。它们覆盖多种上传格式和压力面，不代表全部处理通过。
- 生产候选 42 份旧旅游原件和 6 份静态旅游资料 SHA 均与开发库 `travel` 对应；旧候选 41 active/soft_warning、1 pending_review，静态资料 6 active/pass。桌面 5 份授权生成的行为政策仍未进入项目 Registry；它们可约束回答和升级行为，不能补齐真实 HR/财务制度、适用商家合同/SLA、退款配送和旅游服务事实、生产 SQL 指标及权限矩阵。生产业务覆盖仍不足，本轮没有改 `travel`。
- 当前 `travel` 仍有 1,614 块（406 Parent、1,208 Leaf），其中 1,058 个 Leaf 缺父指针、358 个 Parent 无 Leaf 引用；47 个 active 文档中 41 个缺 parser 或 chunk policy 版本。旧库切分不可靠。相对地，`travel_eval_kb` 厦门样本 131 块（23 Parent、108 Leaf）父子关系完整；RAG PDF 样本 511 块（95 Parent、416 Leaf）父子关系完整，两者的向量数和 BM25 写入阶段计数与块数相符。此前隔离试点的 8 块样本还做过一次向量 Top-5 与 BM25 排名 1–4 的只读检索命中；不能把这些小样本外推到全库。
- LLM 兜底统计代码按处理运行持续累计候选、调用、采用、Provider 失败、边界拒绝、未采用、跳过、输出块，并按知识库受众、格式、触发原因、规则/解析版本和日期分组。登录后的真实源码管理端页面与同源 API 曾实际返回 200，页面显示当时 4 个测试库候选/调用、3 次采用、1 次 Provider 失败、21 个输出块，且显示“测试观察（不触发升级）”。随后隔离运行新增一条失败调用；当前数据库仓储只读结果为候选 5、调用 5、采用 3、Provider 失败 2、边界拒绝 0、未采用 2、输出块 21，全部 audience=`test`，同类内部/对客规则复核均未触发。最新 5 次计数没有在中断后的浏览器页面再次刷新，因此最新 UI 数字仍未验；共享管理端 3200 未部署本工作树代码。
- 复验切分文件名修复时，第一次试跑因工作树默认 BM25 快照目录不存在而在解析前失败。第二次显式使用隔离 BM25 快照，数据库中保留候选 generation `3786e80894714ec6`：2 个 chunk 向量（1 Parent、1 Leaf）和 1 个文档向量；原件 SHA 为 `b705ab61ef2468867ac9e6559c24eb6c7f9958b5e4772870c853a549c7d2d792`。逐行比较确认候选 chunk 的 `document_title` 与 `source_file` 都等于逻辑文件名，没有 `stage-...` 临时名泄漏，Leaf 父指针能对应 Parent。真实 `chunking_llm_fallback` 阶段因 Provider connection error 而未采用 LLM 边界；规则保留 2 块。
- 第二次运行的处理阶段 Trace 已写入：processing run `190ba558-931f-4aab-9cb9-3a0d156f8cf4`、Trace ID `ac25d2d93f13`，12 个阶段记录中有 `chunking_llm_fallback=fallback/provider_or_runtime_error`、向量写入 2/2 和 BM25 候选写入 2/2。运行未到发布完成：`doc_registry` 仍只有此前活动试点，候选 generation 未进入活动代次，BM25 发布指针仍是旧 generation `1ec958babf124529`。因此只把候选元数据和阶段记录算作通过，不把这次文件算作成功入库或完整 Trace 页面通过。
- 运行中 Docker Desktop/5433 开发数据库一度中断；Windows 原生 PostgreSQL 5432 是另一套数据库，本机试点行数为 0，没有拿它替代项目库。恢复的是现有共享 `agent` Compose 栈，没有新起第二套；其自动备份完成后，宿主可用内存曾降至约 1.06 GB，按 2 GB 门槛停止所有后续索引/模型工作。该次运行的索引状态已如实收口为 `failed/local_validation_interrupted`；首次预检失败的运行也收口为 `failed/local_validation_preflight`。两代候选向量仍保留，未删除活动数据。
- 写入前备份之一：`D:\tmp\rag-llm-fallback-title-fix-20261115T183742-da76bf\agent_memory.dump`，366,951,038 bytes，SHA-256 `5888034b55eca7a018a9b7a79f877a44b004f2064cf0f0bb5bfd33f61ce4c8e8`；第二份：`D:\tmp\rag-llm-fallback-title-fix-retry-20261115T184136-10e7ab\agent_memory.dump`，366,951,127 bytes，SHA-256 `bd098f44ae2c917b28ae0ae2bae5112649dea46038adc3bc2536e35a0d94d24a`。当前 Docker 备份容器还生成并通过 `pg_restore --list` 检查了 `agent_memory-20261115-105921.dump`（TOC 1,368 项）；随后只更新了两条隔离运行状态，没有恢复或删除数据。
- 管理端逐文件 API 对账仍是 214/214 个文件可打开详情路由：18 份有完整 Trace 详情、160 份有直接处理运行详情、36 份只有相同文件 SHA 的历史运行记录可回退查看；后 196 份不是完整 Trace。真实浏览器已分别打开过代表性完整 Trace 和历史运行详情；没有逐一用浏览器导航全部 214 页。新的未发布候选 Trace ID 虽在处理阶段数据库中存在，本轮没有在管理端页面单独打开它。
- 本轮定向验证实际结果：后端统计/兜底 API 与仓储测试 `13 passed`；管理端文件统计与 Trace 页面 3 个测试文件 `23 passed`；`npx tsc --noEmit --pretty false` 退出码 0；切分标题回归用例 `1 passed`；Python 编译检查与 `git diff --check` 通过。`frontend-admin/tsconfig.json` 只移除了 Next 自动写入的 node_modules 缓存 include 行，保留其他未提交配置。
- 结论仍为 `PARTIAL / UNVERIFIED`：测试素材齐全但有失败和软警告；生产行为政策已生成但未进入项目库，权威业务事实缺件；新样本切分结构有正向证据、旧 `travel` 父子结构不合格；Trace 详情可由管理端追到完整 Trace 或运行记录，但历史 Span 不足；统计持久化和测试库隔离升级规则已由当前 DB 证据验证，最新五次数字的页面刷新未验证；最新 LLM Provider 实际调用失败，第二次候选运行未发布。后续应在内存稳定高于 2 GB、BGE 与 Chat Provider 都经实时健康复验、数据库备份完成后，仅恢复一份隔离候选运行并验证发布与管理端 Trace；共享 3200 仍需另行部署授权，本轮没有部署。
- 末次环境只读复核：宿主可用内存 2.59 GB，BGE `127.0.0.1:8088/healthz` 不可达，共享 `agent-redis-1` 刚重启、健康状态仍为 starting。数据库查询和验收记录已完成；未再启动模型、索引或上传任务。

## 2026-11-15 — 浏览器复验、失败运行入口和资料版本修正

- 继续使用工作树 `C:\Users\wh\.codex\worktrees\evaluation-center-ui\agent`，分支 `codex/file-quality-main-followup-20261114`，HEAD `19eecf23302103f8431b9f2d59df43f6d9a0d202`。保留已有未提交改动和 `.playwright-mcp/`；未提交、合并、部署或修改线上。
- 在本工作树管理端预览 `127.0.0.1:3201` 用真实浏览器打开 Trace `666de09032f8`：页面成功显示数据库核对的文件名“杭州-世界遗产主题.md”和 SUCCESS。另打开 PDF 失败记录 `progit_en_501p.pdf`：Trace `04edf9cb8df7` 的 Trace API 为 404，页面按同文件 SHA 和处理运行号回退到明确标注“处理运行记录（非 Trace）”的页面，显示文件名、文档 ID、SHA、运行 ID、4 个阶段及 OCR 58/0 和质量门禁原因。快照分别为 `.playwright-mcp/page-2026-11-15T14-20-10-113Z.yml`、`.playwright-mcp/page-2026-11-15T14-22-19-770Z.yml`。
- 实际浏览器发现失败 PDF 的 58 条模型用量行共用 `ocr` key，导致 57 条 React 重复 key 错误；已在处理运行详情列表 key 中加入行序号并增加回归用例。修复后新浏览器页控制台为 0 错误；管理端 Trace/失败详情 3 个定向测试文件 `24 passed`，`DocumentsPanel` 统计组件 `6 passed`，`tsc --noEmit --pretty false` 退出码 0。
- 失败列表在当前共享后端运行态仍显示“质量门禁失败 0 份”，但先前数据库/API 核对有 4 份失败样本；本工作树后端现已补充文件名和 SHA，前端链接可带上 SHA 与 run ID。定向后端回归测试 `1 passed`；该测试对 Registry、失败记录存储、操作日志和质量详情使用 stub，不执行上传/索引。测试日志虽显示任务表 schema ensure，运行前备份 `agent_memory.dump` 的 TOC 已包含 `tasks` 与 `agent_checkpoints` 表。本轮没有改文件、chunk 或向量记录。当前共享后端没有这份修复，因此真实管理端列表尚未复验为 4 条。
- 统计卡片在源码预览中可见，但浏览器请求 `/api/rag/chunking/llm-fallback-stats?days=30` 对当前共享后端返回 404，页面显示“请求的资源不存在”。本轮另以 PostgreSQL `default_transaction_read_only=on` 只读调用当前源码仓储复核：1 个可见统计范围、全部 audience=`test`，候选/LLM 调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块分别为 `5/5/3/2/0/2/0/21`；类型为 `txt + no_recognized_structure + RecursiveChunkStrategy v2 + parser v1`，按规则升级提示为 false。页面仍未通过当前共享 API 显示这些最新数值。没有直接启动当前工作树后端：检查到其启动阶段会设置 PostgreSQL checkpoint 表并预热主 LLM，当前 BGE 服务不可达；本轮不为只读验收触发这些启动副作用，也不部署共享服务。
- 检查本机生成的四份当前生产行为政策，客服当前版为 v1.1；SHA-256 与登记表逐份比对 `4/4` 相符。发现 README 仍链接客服 v1.0，已更正为 v1.1 并标明 v1.0 是历史版；客服说明文件也已标注当前版本。政策可约束事实答复、权限、动态信息及人工升级，但不包含真实企业制度、商家合同、退款时限或生产指标，且四份均尚未入项目知识库。
- 本轮 `git diff --check` 退出码 0（仅有 Windows CRLF 提示）。最终状态仍为 `PARTIAL / UNVERIFIED`：测试资料有覆盖但 4 份上传样本失败、1 份未登记，94 份压力资料有软警告；生产业务事实仍缺；旧 `travel` 分块/版本缺口未修；全库历史 Trace 仍不完整且没有逐页打开 214 个入口；LLM 统计源码和数据库记录存在，但当前管理端运行态 API 404。宿主可用内存约 2.92 GB，BGE 仍不可达；没有上传、重建或索引。

## 2026-11-15 — 当前 3200 管理端、测试文件归属与成功 LLM 切分 Trace 复核

- 保留此前未提交改动与 `.playwright-mcp/` 产物；本节只追加验收证据，没有写知识库、上传文件、重建索引、提交、合并或部署。
- 使用真实登录 super_admin 浏览器在当前管理端 `127.0.0.1:3200` 复查：文档 API 三页返回 `100 + 100 + 14 = 214` 份，指纹均为 `1878941ffee0`。与已保存的 213 份逐文件 Trace 清单相比，ID 集合是原 213 份加一份隔离试点 `e74e5a7e88`，没有旧文件消失。原 213 份审计记录为 17 份完整 Trace、187 份同文件哈希且有步骤的处理运行详情、3 份运行无步骤、6 份入口不可用；新增试点 Trace 已另行在当前 3200 页面打开。按该清单和新增一条的证据，当前可确认至少 18 份完整 Trace、原 187 份处理运行详情等旧分类；本轮没有重新对原 213 个 API 入口逐一发请求，不能把新旧证据说成一次 214 项全量复扫。旧完整 Trace 中有 4 份展示名与逻辑文件名不匹配。
- 真实管理端当前 `rag_eval_kb` 文档 API 返回 23 份 active。`/api/rag/upload-failures?limit=100` 返回 74 条：70 条 async_queue、4 份质量门禁记录。四份是 `UF-09-01 report.pdf`、`UF-13-01 doc_cn.pdf`、`UF-14-01 progit_en_501p.pdf`、`UF-14-02 progit_zh.pdf`；记录均有 OCR/PDF 原文覆盖缺口，`trace_id` 与 `processing_run_id` 为空，状态 `missing_historical`。所以它们有失败原因详情，但不能从历史 Trace 追溯处理过程。
- 更正“UF-15-01 未登记”的旧口径：本机 `scanned_partial_fail.pdf` 为 2,308,571 bytes，SHA-256 `13a742fa8a93636ac9f45a59886357ebecb551c5849750534bd9e8e5585e6004` 与 28 文件 manifest 相符；它不在 `rag_eval_kb` 的 23 份活动文件或 4 份质量门禁中，却在另一个压力 KB `rag_upload_pressure_20261113` 的失败队列出现，错误为 `请求预算已超限: request_tokens`。Trace `90c00499af23` 的管理端 API 返回 200、19 spans；`tags.kb_id/doc_id` 为 `rag_upload_pressure_20261113 / 7eda758d38`，Check span 的 `file_hash` 与原件 SHA 完全相同。该 Trace 能确定是同一原件，但界面显示临时名 `b8ef59d4404c.8e4ff84a0c8a43a9.pdf`，不是逻辑文件名；它也没有落在 manifest 指定的 `rag_eval_kb`。因此不是完全没有上传，而是目标 KB 不匹配、处理因预算失败，不能算目标测试库验收通过，也不能声称 OCR 部分失败场景已按预期跑通。
- 对新增隔离样本 `LLM切分闭环隔离试点-20261115-08f636b0.txt` 做了当前 3200 管理端、API 和 PostgreSQL 只读交叉核验。运行 `1623348e-c754-4b51-9af1-b16fda7525eb` 为 success、12 阶段；Trace `5af8ed4af36d` API 200，真实页面展开后显示正确文件名和 `LLM 切分边界兜底` 阶段，输入 `txt / no_recognized_structure / 4 source units`，规则 v2、parser v1，LLM 成功采用并产出 8 块。页面可读到 Provider、模型和 prompt/completion token 详情；整次 Trace 21 spans，运行耗时 12.6 秒但 SLA 阈值 10 秒，功能阶段成功、时延 SLA 未通过。
- PostgreSQL `BEGIN READ ONLY` 查询确认该样本原件 SHA 与 Registry 一致；Registry 为 active/pass，8 个 chunk = 4 Parent + 4 Leaf，Leaf 父指针完整；`rag_vectors` 中此 doc 有 8 条 chunk 向量和 1 条 doc 向量。处理运行阶段记录 embedding/vector_write/BM25 写入分别 8/8、8/8、8/8；BM25 候选代次记录为 `chroma::cand:1ec958babf124529`。当前 `/app/data/bm25/PUBLISHED.json` 的 generation 是空字符串，尽管文档 Registry 的 active/index generation 是 `1ec958babf124529`；没有对本样本做 BM25 实际召回，因此只能认定候选写入步骤成功，不能把活动 BM25 代次和检索质量判为通过。
- 同一只读数据库范围内近 30 天 LLM 切分聚合为候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块 `5/5/3/2/0/2/0/21`；分组全为 audience=`test`，类型 `txt / no_recognized_structure / RecursiveChunkStrategy v2 / parser v1`。本次成功试点已经包含在 5 次统计中。源码规则是只有 `internal/customer` 类型达到阈值才提示人工升级，测试库不会触发生产升级提示。真实 3200 登录态请求 `/api/rag/chunking/llm-fallback-stats?days=30` 仍为 404；当前管理端没有这项持续统计总览，源码 API 与数据库结果不能替代运行页面验收。
- 当前资源复核：宿主可用内存约 4.16 GB，Postgres、RAG service/worker healthy，Redis 容器仍在 restarting，本地 BGE `127.0.0.1:8088/healthz` 连接被拒。本轮没有因此启动上传或索引。先前本地源码预览将 `/api/...` 直接发到 ASGI 服务、没有 APISIX 的 `/api` 去前缀和可信身份注入，故其 404 仅是预览链路无效，不计作功能失败或通过；本轮确认该预览端口已释放。
- 结论仍为 `PARTIAL / UNVERIFIED`。已证实规则优先、LLM 兜底存在一次真实成功并可从当前管理端看详情，统计数据可从真实数据库读取；未达标的是 UF-15-01 的目标 KB 与预期 OCR 失败测试、四个历史门禁失败无 Trace、旧库 `travel` 父子/版本问题、生产权威业务事实缺件、共享管理端统计接口 404、当前 BM25 代次/召回未验。后续先在不写库前复核 Redis、embedding/LLM 配置和备份；可先修复并重新验收统计页面的部署缺口，但不触碰线上或直接批量改 `travel`。

## 2026-11-15 — LLM 统计运行态与 BM25 快照续验

- 当前真实管理端 `127.0.0.1:3200/knowledge/workbench` 可见 214 篇文档，但没有“规则切分后的 LLM 兜底记录”统计卡片。用登录态 token 请求 `/api/rag/chunking/llm-fallback-stats?days=30` 返回 `404 NOT_FOUND`；浏览器页面因此不能显示当前 DB 统计。检查到工作树后端路由存在，而运行中的 `agent-app-1` 容器内路由文件没有该路径；本轮没有重建或替换共享服务，遵守“未经确认不部署”的边界。
- 对统计功能在源码侧完成定向验证：`test_rag_processing_lineage_api.py -k llm_chunking_fallback_stats` 为 `1 passed, 1 deselected`；`test_processing_lineage_pg.py -k chunking_fallback_stats` 为 `2 passed, 5 deselected`；管理端 `DocumentsPanel.test.tsx` 的持续统计/规则升级提示用例为 `1 passed, 5 skipped`。这些证明源码契约与页面组件测试通过，不等于当前运行管理端已验收。
- 按仓库要求把新增 RAG 链路的规则兜底、统计授权与聚合、测试库不触发生产升级、上传产物完整性等定向用例加入 `.github/workflows/rag_smoke.yml`；路径过滤与 CI 契约测试实跑 `28 passed`。
- 重新检查 BM25 文件：`PUBLISHED.json` 的 `generation` 为空；同目录 `meta.json` 显示 `source=vectorstore`、collection `chroma`、`doc_count=6074`、`vector_count=6074`、构建时间 `2026-11-15 10:59:35`。这是按当前向量库生成的整库快照线索，空 generation 本身不足以判定失效；尚未对试点或其他样本做 BM25 实际召回与快照内容核验，因此检索质量仍未验收。
- 当前宿主可用内存复核约 4.42 GB；共享 `agent-redis-1` 仍在重启，本地 BGE 服务此前健康探测仍连接失败。本轮没有上传、写库、索引或启动模型；BM25 写入阶段 8/8 只证明该运行写入步骤成功。
- 本节后当前状态仍为 `PARTIAL / UNVERIFIED`：源码持续统计逻辑和真实 DB 记录可对账，但统计管理页面尚未在运行态出现；BM25 实际召回未验。其余缺件沿用上节，不将新增的单样本成功扩大成全库通过。

## 2026-11-15 — PR #471 自动审查修复

- PR #471 的 Codex 自动审查发现 P2：LLM 切分兜底调用没有使用 `chunking` 角色的有效超时，Proxy 的 Provider deadline 因而未生效，慢响应会占用索引 Worker。
- 已修复为读取 `resolve_runtime_policy("chunking").timeout_seconds`，并在同步 Provider 调用外套用现有 `scoped_execution_timeout`；配置读取异常回落到角色代码默认值。Proxy 使用同一 ContextVar 把剩余时限变为受支持 Provider 的请求超时，超时后仍回退原规则 Chunk。
- 回归测试先在修复前失败（Provider 调用时 `remaining_execution_seconds()` 为 `None`），修复后 `test_llm_fallback_chunking.py` 与 deadline 传播/Proxy 直接用例共 `10 passed`；`py_compile` 和 `git diff --check` 通过。
- 同批运行整个 `test_execution_deadline_propagation.py` 时为 `34 passed, 1 failed`；失败在未改动的 `test_astream_caps_and_aborts_after_total_deadline`，实际拿到 `[]`，断言期待 `['a']`。该用例未纳入本轮直接改动范围，未把它报告为通过；本轮收窄到 fallback、scope、Provider kwargs 和同步 Proxy deadline 的 5 个相关用例复验均通过。当时记录的 CI/审查待确认状态已由下节 2026-11-16 的 GitHub 实际结果更正。
- PR #471 的 RAG Smoke 免费预检当时曾因 5 个测试桩签名失败；把桩改为接收并忽略 `**_kwargs` 后，本机该 5 个用例 `5 passed`。截至 2026-11-16 已从 GitHub 核实 PR #471 于 2026-11-15 15:49:38 UTC 合并，8 项检查为 7 项成功、1 项跳过（`RAG Smoke Evaluation (8 cases, real API)`）；RAG Smoke preflight 成功。因此“待推送 CI 重跑”已过期，不再是待办；被跳过的真实 API 评测不计为通过。

## 2026-11-16 — 六项总验收最终记录

- 验收已完成，整体结论 `FAIL`（不代表所有项通过）。逐项分母、结果、证据、结论和可自行修复性见 [ACCEPTANCE_MATRIX.md](ACCEPTANCE_MATRIX.md)。本记录以本节及矩阵为准，覆盖并更正此前 `PARTIAL / UNVERIFIED` 状态描述中的旧计数和旧运行时结论。
- 按要求先读仓库 `AGENTS.md`、本任务记录、桌面资料 `README.md`，再检查 Git；fetch 后确认 `origin/main=05176213ae1ed84cfb2cdb146060915c01a83b2d`。原验收分支 `cad34bb` 落后，不作为基线；检查切换影响后从最新 main 建立 `codex/evaluation-acceptance-20261116`。没有改运行时代码、没有提交/合并/部署；`.playwright-mcp/` 保持未跟踪且与切换前 73 个文件快照一致。
- 桌面 470 份候选资料均存在、已重新计算 SHA-256、都有用途和项目终态栏；其中测试 374、生产候选 96、唯一 SHA 455。重新对账后 270 份与项目 Registry 按 SHA 匹配、1 份仅有处理运行、199 份没有 Registry/处理运行。桌面资料是本机副本；数据库核对对象是本机共享开发库 `agent_memory` 及项目数据卷，不是线上生产库。
- 测试集终态：RAG 28 份原件 SHA `28/28`；20 pass、3 soft_warning、4 个 baseline 文件意外失败，`scanned_partial_fail.pdf` 是 OCR 部分失败场景但目标 `rag_eval_kb` 无 Registry，实际在压力库因 request token budget 超限失败，不能算按预期跑通。manifest 未给 4 个 baseline PDF 标预期失败状态。94 份压力样本 `94/94` 哈希匹配但全为 soft_warning；106 份维基来源文件 `106/106` 哈希正确且明确只作测试，项目库中 0 份登记。
- 生产资料不够：96 份候选里 71 份有项目 Registry SHA 匹配、25 份没有项目处理记录，71 份缺版本/内容状态，90 份来源链接/日期栏为空。旧旅游候选 42 份中 41 active/soft_warning、1 pending_review；六份公共制度副本已在 `policy_general`，全部 soft_warning 且权威来源/版本及解析切分版本缺失。合同、案例、制度、服务价格和运营事实的缺件与文件名见矩阵；生成的答复/准入政策不替代真实合同、价格、SLA、制度、供应商和运营记录。
- 最新只读数据库共有 2,590 条 Registry、21 个 KB；232 条活动 Registry 中 214 条属于活动 KB，18 条仍挂在已停用 HR/财务/商品 KB。修正此前把 `travel_eval_kb` 131 块误加到 `travel` 的统计：`travel` 是 1,483 块（383 Parent、1,100 Leaf），有 1,058 个 Leaf 父指针为空和 358 个无子块 Parent；不是 1,614。其他 KB 的结构、元数据、向量和 BM25 数据见矩阵与 `D:\tmp\evaluation-center-acceptance-20261116\active-doc-structure-current.csv`、`chunk-structure-by-kb-current.csv`、`chunk-structure-anomalies-current.csv`。`contract_table.docx` 的 Parent `vectorize=false` 是明确的索引策略：4 个 Leaf 各自写入向量/BM25，不按 5 个 Chunk 行数误判为缺向量。
- 当前近 30 天数据库事件重查为 5 候选/5 调用/3 采用/2 Provider 或运行失败/0 边界拒绝/2 未采用/0 跳过/21 输出 Chunk；全为 `test` audience，类型均为 `txt / no_recognized_structure / RecursiveChunkStrategy / rule v2 / parser v1`。明细保存在 `D:\tmp\evaluation-center-acceptance-20261116\llm-fallback-events-current.csv`。当前共享管理端仍未加载统计路由，匿名 API 要求令牌，当前后端 OpenAPI 没有此路由；历史登录态 API 记录是 404。源码路由授权与定向测试通过，不等于当前共享页面/API 通过。
- Trace 对账台账覆盖当前活动 KB 214 个文档：历史 213 条逐项 API 台账（17 Trace API 200、187 同文件处理运行且有阶段、3 运行无阶段、6 不可用）加 1 条数据库新对上的隔离试点。试点数据库 Trace `5af8ed4af36d` 有 21 spans，Run `1623348e-c754-4b51-9af1-b16fda7525eb` 有 12 阶段，doc_id、SHA 和文件名匹配；本轮没有它的真实页面/API文件证据。旧 API 台账中 4 个 Trace question/记录名与文件名不匹配；当前 main 的文件入口将 Registry 文件名传给详情页标题，因此不能据 API 记录名直接断言页面标题错。6 个缺历史 Trace、3 个空阶段均未伪造或回填。11 月 15 日叙述所引用的试点页面/API产物本轮在 `.playwright-mcp/` 找不到；可见的 11 月 14 日页面快照仅作历史证据。当前 CUA URL 校验阻断、Playwright 浏览器 profile 被占用，当前管理端认证页面和 214 项逐一实时复查记为 `BLOCKED`，不冒充已完成。
- 运行与写入边界：宿主可用内存约 3.29 GiB，`agent-redis-1` 重启中，本地 BGE `127.0.0.1:8088/healthz` 不可达。没有上传、切分写库、向量/BM25 重建或修改共享 KB；没有改线上生产库。所有显式 PostgreSQL 数据核对均为 `BEGIN READ ONLY`。曾执行一条后端定向 pytest（`test_rag_processing_lineage_api.py -k llm_chunking_fallback_stats`，1 passed/1 deselected），其 pytest 收尾钩子另行调用幂等 `ensure_schema()`；检查到备份中已有相同任务表结构、清理过滤条件均为 0，未发现数据清理/业务行变化证据。为遵守只读边界，此后未继续运行会触发该钩子的后端 pytest。
- 后续条件是明确的：统计 UI/API 和管理端 Trace 实时复验需在获授权部署最新 main 到共享服务后进行；实际检索需 Redis/BGE 健康并先备份、在隔离或版本化测试 KB 用单文件验证；旧库结构修复需同样先走隔离验证，禁止批量改生产式 `travel`；缺失业务事实需业务所有者给出真实且有效的原件；缺失历史 Trace 只能作为历史缺口保留，未来新运行验证能产生 Trace，不能补造过去的事件。

## 2026-11-16 — PR #471 合并后本机部署与实机最终复验

- 本节更正本任务 2026-11-16 之前记录的“未部署、统计路由缺失、登录页尚未验”等旧运行态。先 fetch 后确认当前 HEAD 与 origin/main 均为 05176213ae1ed84cfb2cdb146060915c01a83b2d；PR #471 已于 2026-11-15T15:49:38Z 合并，merge commit 08413877d0b44fa3701fe06066261f59871df380。GitHub 记录 7 项成功、1 项跳过（真实 API RAG Smoke）；“CI 重跑待确认”已由实际 Checks 更正。没有新的功能代码需要合并。
- 用户随后明确授权合并、部署和实机验收。本机仅更新已有共享 agent 开发栈的 app 与 frontend-admin，没有另起完整栈，也没有部署线上。以干净 origin/main 构建上下文生成 app image sha256:9320101d4f5d83175ae42337a094abbc98de8425ee7283f5edc68c85c613517c 和 admin image sha256:0cfaccc64b453281a1482528b51740a5e541a774043a9dbe2f6dc1f00a03ba4e；app /health 返回 ok、commit 精确匹配、dirty=false，admin 容器健康。
- app 更新前备份共享开发库 agent_memory：D:\tmp\evaluation-center-deploy-backup-20261116\agent_memory-before-deploy.dump，355.51 MiB，SHA-256 44F1A64C98DB93D747B6226233870B07DC9A52602791056CC6432F50FF44CF9A，pg_restore -l 1,378 行。启动时 checkpoint 初始化完成、prompt 默认值 seed 完成、自动主模型 warm-up 成功输出 2 字符；这些启动写入如实记录。部署前后 Registry 数一致；本轮没有上传文件、写知识文档/Chunk、向量/BM25、重建索引或改线上数据库。
- 登录态真实页面 http://127.0.0.1:3200/knowledge/workbench 已显示“规则切分后的 LLM 兜底记录”。近 30 天页面与只读 DB 完全一致：候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块=5/5/3/2/0/2/0/21；类型为测试库/TXT/no_recognized_structure/RecursiveChunkStrategy v2/parser v1；测试观察、不触发升级，internal/customer 同类阈值为 5。OpenAPI=200 且包含 /rag/chunking/llm-fallback-stats；匿名 API=401，符合鉴权。页面同时显示当前 214 篇，质量通过 46、需复核 168、失败 0、未核验 0。页面与数据库证据：D:\tmp\evaluation-center-deploy-backup-20261116\admin-workbench-and-trace-live-20261116.txt、live-runtime-evidence-authenticated-20261116.json。
- 当前管理端第 1/11 页 20 个 Trace/运行入口均逐一打开；20 个页面均显示 SUCCESS，URL 文件名与列表一致且带 doc_id、SHA、run_id。2 个是完整 Trace，18 个是同文件处理运行详情，未把后者冒称为完整 Trace。试点文件页面显示 Trace 5af8ed4af36d、run 1623348e-c754-4b51-9af1-b16fda7525eb、8 chunks、20 个子 Span、LLM 切分边界兜底及向量 8/8；DB span_count 21 包含 root，页面统计 child Span，口径相符。厦门-城市-厦门.md 页面显示 Trace da4d80d49636、131 chunks、15 child spans、向量 131/131。policy-logistics-exception.md 核对 run、文件 doc_id、SHA 和 11 阶段，并明确显示“处理运行记录（非 Trace）”。
- 当前真实管理端显示 214 条、11 页。浏览器返回详情后文档列表重挂载并回第 1 页；页 2 自动循环一次 120 秒尝试超时，因此剩余 194 条本轮未逐一打开，当前部署的全量 UI 复验记 BLOCKED，不能把历史 API 台账当作当前重发。历史 213 行台账仍为 11/15 检查：17 个完整 Trace、187 个有阶段的同 SHA 运行、3 个无阶段运行、6 个不可用。4 个历史记录名不一致、6 个缺失和 3 个无阶段具体名称见 ACCEPTANCE_MATRIX.md。历史缺失不可恢复或伪造。
- 额外执行一次只读测试 KB 实际检索：RAG /readyz=200 ready；对 travel_eval_kb 的公开测试问题“鼓浪屿位于哪里？”调用 /retrieve_docs 返回 index_status=ok、6 个片段，均来自厦门-城市-厦门.md。这是单条测试 KB 混合检索烟测，不是生产事实验证或全库准确率，也未扩成 Ragas。检索证据见 D:\tmp\evaluation-center-deploy-backup-20261116\rag-retrieval-smoke-20261116.txt。
- 部署后 DB 只读计数：21 KB、2,590 Registry、6,077 chunk_store、60,763 rag_vectors、12,846 processing_runs、108,200 processing_steps；兜底事件仍为 5/5/3/2/0/2/0/21。部署后 agent-redis-1 仍 restarting/unhealthy，app 的 Redis 状态 unknown/degraded；本机 BGE 127.0.0.1:8088/healthz 连接拒绝。RAG readiness 与单样本检索成功，但没有启动上传、索引、重建或批量库修复。
- 最终结论：验收已完成，整体 FAIL，不代表全部项目通过。测试集已知失败/软警告、生产权威资料不足、旧活动库 Parent/Leaf/版本缺陷和历史 Trace 缺件均为 FAIL；统计 DB/API 授权/真实管理页、当前页 20 个入口、单样本检索及 PR/本机部署按限定范围 PASS；全量 214 页详情复验剩余 194 条为 BLOCKED。总矩阵为 ACCEPTANCE_MATRIX.md。保留原有 .playwright-mcp/ 未跟踪证据（73 个文件）；无提交、无新 PR、无线上生产部署。

## 2026-11-16 — 可修复项修复与复验

- 按“先处理能修的”继续在从最新 `origin/main=05176213ae1ed84cfb2cdb146060915c01a83b2d` 建立的 `codex/evaluation-fixable-20261116` 工作分支处理本机可复现问题。修复已提交并推送，PR #473 已创建；本节末尾记录最终检查/合并状态。没有改动在线服务或线上生产库。
- 修复文档列表进入 Trace/处理运行详情后返回页码丢失：列表页码写入 URL，详情链接携带来源页签和页码，详情显式返回按钮保留位置；定向 UI 测试 2 个文件、12 个用例通过。`dev-svc.bat`/`devctl.bat` 现在支持仅重建 admin，避免前端修复复验时重启后端；操作文档已同步。
- PR #473 自动代码审查在 `ce9cad7` 指出 P2：`rebuild -y` 的 `-y` 被旧参数循环误当作 target。已修复解析器，让 `/y`、`-y`、`--yes` 只设置确认标志、不占用 action/target，并增加 `backend/tests/test_devctl_migration_rebuild.py` 回归守卫。用未实际执行服务动作的隔离批处理副本验证 `rebuild -y`、`rebuild --yes`、`rebuild admin -y`、`rebuild web /y`、`-y rebuild` 五种入参均得出预期 action/target/YES；直接调用 Python 守卫函数通过。未触发 pytest 的数据库收尾钩子，也未启动全栈重建。
- 本机共享栈仅重建 `frontend-admin`。Next 生产构建、类型检查、49/49 静态页面生成成功；admin 容器 healthy、`/login` HTTP 200。镜像 ID `sha256:37469c534c563d2a567b5055c932a4c2e994771830efe363f825789afa88fa3a`。真实登录页面逐项打开第 2 页处理详情、第 9 页完整 Trace、第 10 页 SHA 历史运行详情；第 2、9 页显式返回均保留来源页码，第 2 页浏览器后退也保留页码。页面证据和 API 扫描记录：`D:\tmp\evaluation-center-deploy-backup-20261116\trace-runtime-acceptance-fix-20261116.txt`，SHA-256 `EAFE69F2C6A2CE288BE2B0FA2A4DAA4D64AD980A84D55A9A6158F13A5D52CF71`。
- 当前授权 API 全量核对 214/214 个文件入口：18 个完整 Trace、189 个当前运行详情、7 个同 SHA 历史运行详情，合计 214 个可读详情；其中 174 个登记 Trace API 仍为 404，不能宣称历史 Trace 已恢复。列表入口、名称、doc_id、SHA 和运行详情的页面/阶段分类见上方证据及 `ACCEPTANCE_MATRIX.md`。30 天 LLM 兜底统计仍为 5 候选、5 调用、3 采用、2 失败、0 边界拒绝、2 未采用、0 跳过、21 输出块；本轮没有写数据库或索引。
- 不依赖模型/Redis 的复验完成后，明确不做危险的伪修：四份 baseline PDF 缺预期失败定义且失败在 OCR/覆盖硬门禁；旧活动库存在大量 Parent/Leaf 断链；生产候选缺合同、价格、SLA、制度及真实运营事实来源；过期 Trace 历史不能补造。共享 Redis 仍重启中且 BGE 不可达，所以不上传、写库、重索引或批量修复。具体缺件、数量、失败状态和条件见验收矩阵。
- `.playwright-mcp/` 证据未覆盖：将本轮开始前 73 文件/哈希清单与当前文件逐份比较，缺失 0、SHA 不符 0。`git diff --check` 通过。PR #473 最终 HEAD `9976c90f6cb2e0e37fa12daf39f43dc20bd9f4cc` 的检查为 6 项成功、2 项跳过（RAG 离线预检与真实 API 评测）；P2 参数解析修复位于前一代码提交 `64e2568154f031602d55263a358746dcdffcd22c`，审查线程已解决。记录此时 PR 尚未合并；改动已在本机开发管理端构建并完成实机复验，没有部署线上。整体验收结论仍为 FAIL，修复项为本机管理端范围内的 PASS。

## 2026-11-16 — PR #473 合并后剩余阻塞排查

- PR #473 已于本轮合并，merge commit `67355c10d5fff34ea34f454a5810c893e5f9f3bb`；最新 origin/main=`67355c10d5fff34ea34f454a5810c893e5f9f3bb`，与已构建分支源码树一致。为记录本次新诊断，从该 main 建立 `codex/evaluation-runtime-diagnosis-20261116`；切换前确认无跟踪文件差异，`.playwright-mcp/` 不在目标分支跟踪范围且 73 个文件仍保留。
- 只读检查发现宿主可用内存 1.87/15.84 GiB；共享 Compose project `agent` 为 20 running、1 restarting。`agent-redis-1` 已重启 605 次、exit=1、OOMKilled=false。Redis 日志表明 AOF base 载入 218 keys 后，`appendonly.aof.68.incr.aof` 发生格式错误；数据卷名 `agent_redis_data`。BGE `http://127.0.0.1:8088/healthz` 连接不可达。精确输出保存在 `D:\tmp\evaluation-center-deploy-backup-20261116\redis-aof-runtime-diagnostic-postmerge-20261116.txt`，SHA-256 `BDC8D38AD6634F725C21AEEBA3CAB6A7E3E9EF9124669EC81100DC8C6DBC74A0`。
- 这确定了此前 Redis unhealthy 的根因，不是 OOM。当前共享栈内存低于 2 GiB 门槛，修复 AOF 可能截断共享队列/事件数据。本轮没有停止或修改 Redis、没有启动 BGE、没有新建服务栈、没有上传文件或写数据库/索引；因此 Redis 修复及隔离 PDF/Chunk 验证记为 BLOCKED，而不是声称通过。
- 可继续的具体条件：宿主可用内存至少 2 GiB，确认共享服务维护窗口；先冷备份 `agent_redis_data`，只在卷副本上运行 AOF 检查/修复并验证数据，再决定恢复共享 Redis。Redis 和 BGE 健康后，另备份当前开发数据库，在隔离/版本化 KB 对单份 PDF 验证。线上库保持未修改；业务事实缺件与不可读取的历史 Trace 仍须由有效原件/历史归档解决。

## 2026-11-16 — Redis 恢复、UF-15 单文件复验与最终收口

- 用户要求直接开始，不以宿主内存需等到 2 GiB 为由停住。本轮先只读重查共享栈与宿主资源，确认后才处理 Redis：原 AOF 增量末尾有 20-byte 不完整命令，先冷备份卷，再在可丢弃副本用 redis-check-aof --fix 修复并校验；仅把修复后的增量复制回原卷。冷备份原件 58,146,816 bytes / SHA-256 C9909966E733D01810DBB8A204DFD4BB0AA19133ED3DC2C5F0246B2E81163C0F；修复候选 58,146,796 bytes / SHA-256 274537A14BF8AFEC0DC2DEA1CC623163C8902814D1247ED7A691346A7F7F9DD8；冷副本保留。Redis 当前 running/healthy、restart=0，app 健康、应用内认证 Redis PING 成功、DBSIZE=751。无密码 Redis CLI 返回 NOAUTH 是预期鉴权结果。20-byte 被截掉尾段的语义无法反推，可能有队列尾事件无法确认，记录为残余风险。
- 启动本任务管理的本地 BGE 后，embedder、reranker、reranker tokenizer 加载并 warm-up 完成，/healthz=ok。完成本轮单文件任务后已按脚本停止 BGE 释放内存；复验后宿主可用内存读数为 4.01 GiB，app 与 Redis 仍健康。后续任何模型处理都需重新启动并检查 BGE。
- 写入前确认 rag_eval_kb 为 audience=test，并分别备份 agent_memory、agent_business 与 app-data 卷；备份 SHA-256 见 D:/tmp/evaluation-center-deploy-backup-20261116/scanned-partial-fail-acceptance-20261116.txt。原件、仓库夹具和临时上传副本 SHA 相同：13A742FA8A93636AC9F45A59886357EBECB551C5849750534BD9E8E5585E6004，2,308,571 bytes。只在 rag_eval_kb 提交 1 份 scanned_partial_fail.pdf；桌面原件未改，线上生产库未触碰，没有批量改 travel。
- POST /api/rag/upload 与 SSE HTTP 200，返回 doc_id=0ae627687c、SHA 与源文件一致、run_id=133f773e-53f2-4edb-8a37-71e82844247a、Trace 896c1f626dc2、27 chunks、active、quality_score=80/soft_warning。parser v3、RecursiveChunkStrategy:v2；OCR 10 页中 9 页产出文本，告警 parser_pdf_visual_content_unparsed、ocr_degraded。旧 request-token-budget 错误仍属于 rag_upload_pressure_20261113，未移作目标库运行。manifest 的 UF-15-01 只有 OCR profile、没有 expected terminal status；因此这次不能当作 PASS 或预期失败，只能说目标处理成功但质量需复核。
- PostgreSQL 查询显式只读并 rollback。run 为 success，耗时约 30.4 秒，Trace 15 spans。结构是 1 virtual Parent+26 Leaf；26 Leaf 均非空且关联有效，空块、缺失/重复 chunk ID、无效父引用均为 0；Leaf 原文合计 8,548 字符。Parent 是系统生成的短标题/上下文节点，设计如此，不要求包含全部 Leaf 文本。版本元数据 doc=1、KB=v1、parser=v3、policy=RecursiveChunkStrategy:v2，effective_from 为空。向量库 Chroma 为 26 Leaf+1 Parent，另有 1 个 document embedding；27 个唯一 chunk 引用。Trace 的 embedding/vector_write 均为 27/27。BM25 PUBLISHED 从 6,074 增到 6,101（+27），前后 SHA 与完整单文件证据见同报告。
- 用 super_admin 授权范围对 rag_eval_kb 做只读真实检索：SENTINEL-OCR-P09 返回 HTTP 200、index_status=ok，命中新 doc_id=0ae627687c、来源文件 scanned_partial_fail.pdf、chunk 0ae627687c_21。第一次未使用 super_admin scope 的诊断查询不计验收；按授权知识库范围重做后的正确命中才是通过证据。
- 登录管理端真实页面从 215 个文件列表搜索该 PDF，行内显示 rag_eval_kb、部门 general、需复核/80 分、OCR qwen-vl-ocr 和 “查看 Trace / 运行记录”。点击后打开完整详情：文件名、2,308,571 bytes、doc_id、原件 SHA、run_id、Trace ID、SUCCESS、27 块、15 spans、30.4s、解析、chunk preview、metadata、embedding 27/27、向量 write=27、Quality record 80/两项警告均相符。证据快照 C:\Users\wh\Documents\ChatGPT\agent\.playwright-mcp\scanned-partial-fail-trace-acceptance-20261116.yml，SHA-256 9B85AC67936FC3BA6D7E8C60223C79A9D03C000F2027E0397DE4293C90A8C4BD；全页截图同目录 PNG，SHA-256 701D4BE2345460FF6ABC2445F0EDC03825EFDE2FCBEAEBA36584CF18018C376B。上传 SSE 证据 SHA-256 1757F11880203C94BDA3332DF6F3087D08E7FDD5222DBF64D7AB8EE6F1EFE0CD。该 PDF 是合成测试内容，不能当作真实 HR/security 政策。
- 汇总复验：30 天 DB 只读、认证统计 API 与登录页面仍一致，为 5 候选、5 调用、3 采用、2 Provider 失败、0 边界拒绝、2 未采用、0 跳过、21 输出块；五次全部 test/TXT/no_recognized_structure/RecursiveChunkStrategy v2/parser v1。新文件 run 的 chunking_llm_fallback 步骤为 0，故累计统计不变；question_gen invalid_response fallback 是另一处理阶段，不能混计。匿名 API 401、登录态 API 200。没有 internal/customer 事件，所以只能验收测试库统计；生产升级提示仍 BLOCKED。
- 新建逐份 overlay 台账 `D:/tmp/evaluation-center-deploy-backup-20261116/rag-upload-28-reconciliation-post-rerun-20261116.csv`，不覆盖旧 28 文件原台账；UF-15 行现为目标 Registry active/success/soft_warning，27 chunks、2 条跨库历史运行分开归属、预期终态未定义。完整证据报告 `D:/tmp/evaluation-center-deploy-backup-20261116/scanned-partial-fail-acceptance-20261116.txt`（SHA-256 301D4EA09C72136883D9AB98008F1D51EFDA740208FA13B11219F260D4B854AA）。总活动 Registry 增至 215；rag_eval_kb 到 24 文档/2,578 chunk。更新后的矩阵当前 Trace 分类是 19 个完整 Trace、189 个当前 run 详情、7 个同 SHA 历史 run 详情、0 个无详情；历史 174 条 Trace API 404、22 个文件无列表级 trace ID 不变，完整历史 Trace 仍 FAIL。
- 当前 LLM API 响应保存在 C:\Users\wh\Documents\ChatGPT\agent\.playwright-mcp\llm-fallback-api-response-after-single-file-20261116.json，SHA-256 6BF15DA20E4DBABEE539E76877D0AC13F4375D85C671D4C19356820111DE423A；只读 DB 报告 `D:/tmp/evaluation-center-deploy-backup-20261116/llm-fallback-db-readonly-recheck-20261116.txt`，SHA-256 351C610F75F9BD92CA9443A3E72D250C1332644A935A3EA7087F9C90FB9524A0。post-recovery 报告记录 app/Redis/RAG 及冷备份、BGE 生命周期：`D:/tmp/evaluation-center-deploy-backup-20261116/post-recovery-validation-20261116.txt`，SHA-256 107DEAF5F8BE851E8E90539625A836A31C46C0FBF4D7475F9F0EB76129941715。
- 最新重新 fetch 的 origin/main 与本验收分支 HEAD 相同，均为 4504f797b125e546eceed328e9147a1174864585；分支 codex/evaluation-runtime-recovery-record-20261116 从该 main 创建。PR #471 实际检查为 7 success/1 skip，PR #473 已合并且管理端代码已在本机共享栈实机验收；当前本轮没有新增功能代码，所以没有额外代码部署、提交或开 PR。PR #471 的“CI 重跑待确认”是过期记录，GitHub 实际结果已更正。只在本机开发栈验收，没有部署线上。
- 本轮最终状态：测试材料整体 FAIL（4 个意外失败、4 个软警告、174 个未登记/未运行；UF-15 缺 expected status）；生产权威事实 FAIL；旧活动库切分结构 FAIL；完整历史 Trace FAIL；当前 215 个文件入口/某种详情分类 PASS；UF-15 结构、向量/BM25 和隔离测试检索 PASS，但质量仍 soft_warning；测试库 LLM 统计的 DB/API/授权/UI PASS，生产/internal 升级提示 BLOCKED。验收已完成，不代表所有验收项通过。最终矩阵见同目录 ACCEPTANCE_MATRIX.md。
- 文档检查：`git diff --check` 通过；未运行代码测试，因为本轮没有代码改动。原有 `.playwright-mcp/` 未清理、覆盖或重写；本机两份临时上传副本的 SHA 已核对；执行策略拒绝了精确路径的 Remove-Item，副本保留且未触碰桌面原件。
- 最终证据补记：工作台返回页的入口快照 `C:\Users\wh\Documents\ChatGPT\agent\.playwright-mcp\scanned-partial-fail-workbench-entry-20261116.yml`（SHA-256 B13F53E356A060444F819FEC8A6181F815FD2A3B6219E93FE7A2521421B4428F）和入口截图 PNG（SHA-256 C3C5D040B8E4436CA3F1953975CE5FEBBE959101642A981E3A496FD76DE18E0A）已在上一条单文件报告中登记；页面可见 215 文档、目标文件行、LLM 统计卡片和“查看 Trace / 运行记录”按钮。
- 清理状态更正：两份临时上传副本都经 SHA 核对与原件相同；本机执行策略拒绝了针对精确路径的 Remove-Item，请求未执行。没有用其它方式绕过。副本仍留在 C:\Users\wh\Documents\ChatGPT\agent\.codex-upload-20261116\scanned_partial_fail.pdf 和 C:\Users\wh\Documents\ChatGPT\agent\.codex-upload-scanned-partial-fail-20261116.pdf；桌面原件与 .playwright-mcp 证据保持未动。

## 2026-11-16 — 企业资料补齐、19 份逐条 Trace 与代码修复

- 本轮先从仓库 `AGENTS.md`、本任务记录与桌面资料 README 起步；检查 Git 状态并保留已有 `.playwright-mcp/` 未跟踪证据。重新 `git fetch origin main` 后 HEAD 与最新 `origin/main` 均为 `4504f797b125e546eceed328e9147a1174864585`（PR #474 merge commit），本工作分支从该基线建立。宿主剩余内存 2.44 GiB，共享 Compose project `agent` 正运行 21 个服务；没有另起完整栈。
- 桌面只读全量盘点从此前 470 项增至 498 项：原 470 路径全部仍存在，重新哈希 468/470 匹配；两个不同 SHA 都是 README 支持说明，不是业务原文。新增 28 路径中 15 份是新增测试/生产候选文件（测试 8、生产来源边界说明 7），已逐份关联项目 Registry 与处理终态；其余 13 项为清单/README/控制文件、两个 Word 临时锁和三个敏感运维/凭据文件，敏感文件只登记文件名，未读取内容或计算哈希。逐份归属及 SHA 在 `.playwright-mcp/desktop-file-inventory-delta-20261116.csv`、`.playwright-mcp/desktop-file-inventory-base-sha-recheck-20261116.csv`。生产 README 的“政策尚未入库”文字已被 2026-11-16 实际项目状态取代；没有修改桌面原件。
- 本轮管理端逐份上传 19 份资料：8 份 TEST ONLY 虚构演练进入 `rag_upload_pressure_20261113`，11 份来源边界说明/用户授权运行政策进入隔离的 `enterprise_need_prod_internal_20261116`。19/19 原件、暂存件与数据库 SHA 一致；19/19 active、处理 success、质量 pass/100，19/19 的列表 Trace 入口均能打开，19/19 页面文件身份、doc_id、SHA、run_id 与 Trace 对应，19/19 有截图。逐份清单为 `.playwright-mcp/enterprise-materials-ingest-trace-register-final-ui19-20261116.csv`；另四个此前尚未打开的页面复验在 `.playwright-mcp/final-four-trace-ui-verification-20261116.csv`。
- 管理端全量 Trace/API 自动核对覆盖当前列表 234/234：`GET /api/rag/documents` 分 3 页返回 234；文档登记详情 234/234 与入口文件名、doc_id、SHA 一致；处理运行详情 234/234 的 doc_id、run_id、SHA 匹配。15 条列表未带最新 run_id 的记录通过 processing-runs 历史列表找到同 SHA 运行并逐一取回详情。Trace 详情 API 为 38/200（完整 Trace）和 196/404；其中 174 条有列表 Trace ID 但历史详情已不可取回，另 22 条列表无 Trace ID、但运行记录关联的 Trace ID 详情也为 404。故处理运行详情有数据不代表完整 Trace 可取回。逐文件结果 `.playwright-mcp/current-document-trace-api-audit-complete-20261116.csv`；API 批次与刷新重查原始证据在同目录 `current-document-trace-detail-api-audit-auth-20261116.json`、`current-trace-detail-retry-1..4-20261116.json`、`current-trace-detail-api-auth-refresh-retry-20261116.json`、`current-trace-detail-api-auth-refresh-verified-20261116.json`、`current-missing-trace-22-detail-api-auth-final-20261116.json`、`current-no-list-run-history-detail-audit-auth-20261116.json`。
- 最终只读数据库报告 `.playwright-mcp/enterprise-materials-db-final-readonly-verified-20261116.txt` 覆盖这 19 份：304 Chunk（75 Parent / 229 Leaf），空块、Leaf 缺/错 Parent、重复 ID、缺必需元数据均为 0；19/19 质量工件覆盖全部 Leaf 且未发现未覆盖内容/丢行；Chroma 有 304 个 1024 维 Chunk 向量，`doc_db` 有 19 个文档向量；19/19 vector_write、bm25_write 阶段成功且输入/输出一致。4 个政策 Leaf 未填 `source_spans_json`，但均有来源 locator 可回查；这不掩盖其它旧活动 KB 的 Parent/Leaf 与版本元数据缺陷。
- 分类复核发现 TEST-FIN 差旅报销样例曾被旧规则误分为订单域。本分支将财务词表“差旅”加权并升 taxonomy 到 v2，定向测试通过；在 `rag_eval_kb` 对单份测试文件重处理后业务域为 `financial`，用 super_admin 授权的真实 `POST /api/rag/search` 返回 200、`index_status=ok`，10 个结果中 8 个来自目标测试文件。实机检索证据 `.playwright-mcp/finance-live-retrieval-final-20261116.json`。此前错分类旧代次只作为历史记录保留，没有批量重建其它文档。
- 只读检查桌面生产候选共 103 份。项目新增 11 份生产/internal 资料均可约束答复边界或记录资料范围，但 7 份为生成的来源边界说明，4 份为用户授权政策；它们不构成真实合同、价格、SLA、人事/财务制度、供应商、商品、客户承诺、库存/订单/运营数据、生产 SQL Schema/指标定义或角色授权矩阵。静态公共旅游事实也不代表实时营业/可预订状态。相应缺件与候选文件名仍列在本矩阵“资料缺件”节；不能由 AI 编造补全。
- LLM 兜底最近 30 天只读 DB、认证统计 API 与已登录真实工作台一致：候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块 = `5/5/3/2/0/2/0/21`；事件全是测试库、TXT、`no_recognized_structure`、`RecursiveChunkStrategy` v2/parser v1。匿名请求 401；升级阈值 5，测试观察不升级。新增 19 份没有产生切分兜底事件。页面、API 与匿名授权证据分别为 `.playwright-mcp/final-workbench-llm-stats-loaded-after-reindex-20261116.png`、`.playwright-mcp/final-llm-fallback-api-postreindex-20261116.json`、`.playwright-mcp/final-llm-fallback-anonymous-auth-20261116.json`；DB 证据 `.playwright-mcp/enterprise-llm-fallback-db-postreindex-20261116.txt`。无 internal/customer 事件，所以生产受众升级提示仍 BLOCKED。
- 完成修复与定向复验：管理端 Trace 现在将登记文件名、doc_id、处理 run 的 SHA 与 Trace ID 同入口逐一核对，缺失或不一致会明确显示未核验；财务差旅归类规则有回归测试。后端 pytest 1 passed、前端 `trace-metrics` 15/15 passed；既有 19 份真实页面 Trace 均通过核验。`git diff --check` 通过。最新本机部署、PR Checks/Review、合并与合并后健康检查在本记录追加段落继续登记。
- 本轮验收结论仍是整体 FAIL（验收已完成）：新增 19 份资料入库、结构/索引、逐份新 Trace、测试受众 LLM 统计均通过限定范围；完整旧测试资料仍有 4 个意外失败、4 个软警告和 174 个未登记文件；生产业务事实来源不足；旧活动库结构异常和 174 条不可取回的历史 Trace 未被新资料掩盖。没有改线上生产库。

## 2026-11-16 — Trace 身份核验补强与本机 post-build 复验

- 修复 Trace 页身份误通过条件：Registry 现在同时核对文档 ID、文件名和登记 SHA；Trace 必须同时有并匹配知识库 ID；完整 Trace 的处理运行必须与入口 run_id、文件 SHA、Trace ID 一致，任何缺字段/不匹配都显示“身份未核验”。文档登记字段优先使用 API 的 `file_name/hash`，兼容 `name/file_hash/sha256`。只有处理运行详情时仍明确标记“非 Trace”，并显示登记与运行双方的文档 ID/SHA 核对结果。
- 前端定向 Vitest 两个文件最终 22/22 通过，后端差旅分类 pytest 1/1 通过。后端 pytest 会话结束钩子自动运行仓库 migration residue guard；日志确认其连接配置是本机共享开发库 `localhost:5433/agent_memory` 并执行幂等 tasks/checkpoints schema ensure。会话没有输出 residue 清理记录（按守卫逻辑表示清理/更新行数为 0）；未连接线上生产库。后续重复运行该后端 pytest 前需先按本任务 DB 安全规则重新确认隔离目标与备份。
- 从本任务工作树运行 `COMPOSE_PARALLEL_LIMIT=1 devctl.bat rebuild admin /y`。Next 生产构建成功、类型检查成功、49/49 静态页生成；只重建 `frontend-admin`。镜像 `sha256:d45c198f79fa55736354cde64595916020bb0bbbae06934c1d37ba919af1b437`；容器 running/healthy、`http://127.0.0.1:3200/login` HTTP 200。app 与 `rag-index-worker` 容器内 `metadata_taxonomy.yaml` SHA 都是 `b7f42fe9217c6e4126afefe919730a354f640f63ee1bf5341de9b4e3ad63e510`，与工作树一致，故没有做无必要的后端重建。app `/health=200`、Redis healthy/restarts=0、BGE `/healthz=200` 且 embedder/reranker 已加载；宿主空闲 2.60 GiB。未上传、写 DB/索引或触碰线上环境。
- 最新 admin 镜像部署后，真实登录管理端逐一打开新增的 19 份 Trace 页面；19/19 的文件标题、KB ID、doc_id、源/登记/运行 SHA、run_id、Trace ID 一致，均显示“身份核验通过”。逐页结果 `.playwright-mcp/current-v2-new19-ui-postbuild-20261116.json`（19/19；SHA-256 `B84110EF9FF5A0B28674564AF8A40B69B5315271E6D503648798DB447BA2906F`）。代表 Trace 页面完整截图与 DOM 证据为 `.playwright-mcp/trace-identity-live-postbuild-20261116.png`（SHA-256 `F3F4C8A7F4998915D1D7751E08A138F2EF3809CDB0EB7E5CB006BECA8A556B54`）和同名 JSON（SHA-256 `75CD243C5DFF8B73DBECCD44E68B3179726CE98745FECD8AA6410B468010BAC5`）。
- 另将一条 Trace API 404、但有同文件处理运行详情的 `policy-logistics-exception.md` 用 `view=processing-run` 实际打开。页面明确标注“处理运行记录（非 Trace）”，文档登记/入口/运行三方 doc_id 与 SHA 一致，状态显示“文件与处理运行身份核验通过”；证据 `.playwright-mcp/processing-run-fallback-live-postbuild-20261116.json`（SHA-256 `937B6B8C03D80647F54D94BC4A5D49A2220A12F1709416C21FB256E9470A2B59`）与 PNG（SHA-256 `067CD814E9C236A2E9034E21CC2A08C5C668826CA14BD39E5F9F0C21C2E09C2D`）。这不改变该历史 Trace 本身 404 的 FAIL 分类。
- 登录后的真实工作台统计 API HTTP 200，与既有只读数据库快照相同：候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块=`5/5/3/2/0/2/0/21`；全部是 test/TXT/no_recognized_structure/RecursiveChunkStrategy v2/parser v1。管理端截图显示这些汇总及测试受众，不触发升级。post-build API 记录 `.playwright-mcp/llm-stats-live-postbuild-20261116.json`（SHA-256 `BDAF8A40B9C0E23456FDB89F2F6281B0007E54E020A33CDEC3F55E3A6816B31B`），截图 `.playwright-mcp/llm-stats-live-postbuild-20261116.png`（SHA-256 `AFD2F1F9EB47BA947F350CE470E108193C2C86258374F385BFA3EC1D2C98BDA7`）。
- 本节写入时 PR/CI/合并记录尚未产生；只写本机共享开发栈，不写线上部署结论。后续按 GitHub 实际状态补记。总验收仍是整体 FAIL：测试旧资料意外失败/未登记、生产真实事实缺件、旧库结构问题和不可取回的历史 Trace 均未被这次 UI/分类修复掩盖。

## 2026-11-16 — PR #475 合并与合并后实机验收

- GitHub 已确认 PR #475（`fix: verify RAG trace document identity`）以 squash 合并；合并提交为 `89ee6130b086c7df064ccbe30f81603183fc2294`。合并前分支提交 `811c692ae751139fc1289e59aeaa77958c14509d` 的 tree 与合并后 `origin/main` tree 都是 `8ed391e0a039705c74896380752a342575a294fb`。本地共享开发栈的管理端镜像因此已包含与 main 完全相同的文件树，不需再次构建。
- PR 的 8 个检查中 7 个成功：RAG Smoke 8 cases、RAG Smoke 离线预检、RAG 变更检测、管理端安全扫描、管理端授权、管理端功能检查、CodeQL scan；`RAG Smoke Evaluation (8 cases, real API)` 按工作流配置跳过，不计为通过。GitHub 报告 mergeable_state=clean。Codex 自动审查已完成，`has_any_findings=false`、`has_unresolved_findings=false`。
- 合并后只读复验本机共享开发栈：管理端镜像 `sha256:d45c198f79fa55736354cde64595916020bb0bbbae06934c1d37ba919af1b437`，admin `/login` HTTP 200、app `/health` HTTP 200，admin/app 容器 healthy；Redis healthy/restart_count=0；BGE `/healthz` 返回 `status=ok` 且 embedder/reranker 已加载。该操作没有重建容器、写数据库或索引；没有部署线上生产环境。
- 合并后的真实管理端页面复验：运行详情正确标记“处理运行记录（非 Trace）”，页面显示文件 `policy-logistics-exception.md`、doc_id `921f97a86c`、登记/入口/运行 SHA `588b3d16...15f70`、run_id `fbe58d24-1a34-4d38-b2fb-c7ca148fe70c`、Trace ID `03ff220fd5ed`、11 个处理阶段，并显示“文件与运行身份核验通过”。页面快照 `.playwright-mcp/postmerge-processing-run-detail-20261116.yml`（SHA-256 `F7594932260D4FE1010E61E3BA4A8090398A201BFBED95E23B2D90AD0C81AC7D`）；这仍是 processing-run fallback，不声称恢复完整历史 Trace。
- 合并后的工作台真实页面显示统计候选/调用/采用/Provider或运行失败/边界拒绝/未采用/跳过/输出块为 `5/5/3/2/0/2/0/21`，全部属于 test audience；阈值 5，但测试事件不触发生产升级。页面快照 `.playwright-mcp/postmerge-llm-stats-live-20261116.yml`（SHA-256 `00122A2CF0413DB37B184E74FC523F8038E882071000B5D2AFAAE427BC0672DE`）和截图 `.playwright-mcp/postmerge-llm-stats-live-20261116.png`（SHA-256 `AFD2F1F9EB47BA947F350CE470E108193C2C86258374F385BFA3EC1D2C98BDA7`）；数据库和授权 API 对账证据见 `.playwright-mcp/enterprise-llm-fallback-db-postreindex-20261116.txt` 与 `.playwright-mcp/final-llm-fallback-api-postreindex-20261116.json`。
- 合并/运行态核验汇总见 `.playwright-mcp/postmerge-runtime-verification-20261116.txt`（SHA-256 `88FABA7EC48B5D3D9F29A8E0C272014A82C5FC22E2CB3CE7123A45CA8B59D79D`）。验收已完成，整体结论仍为 `FAIL`：测试资料有未登记与意外失败，生产缺真实业务来源，旧活动 KB 有已确认切分结构缺陷，历史完整 Trace 存在 404；这些都没有被本次合并或测试库验证掩盖。

## 2026-11-16 — 合并后管理端重建补正

- 更正上节“同 tree 因此不需再构建”的初步判断：Docker 镜像创建时间早于分支提交。虽然 tree 与 `origin/main` 一致，仍执行了合并后 admin 构建；首次 `up` 沿用了旧容器，检查发现其仍引用旧 image `sha256:d45c198f...`，所以不把它算作完成部署。随后使用本任务两份 Compose 文件并加 `--no-deps --force-recreate frontend-admin`，只替换管理端容器。最终容器 ID `2fa5806b0c87eeef6b76e066be732c6e5945bfea5642ad33fbc53c60a58e381e`，引用新 image `sha256:d40ffd63118a0b053cdfdadb8fa23647601a7e2fb97f6133f1d2b59d3c723480`，health=healthy，启动时间 `2026-11-16T03:43:36.266400359Z`。`/login`=200，app `/health`=200，Redis healthy/restart_count=0，BGE `/healthz`=ok 且 embedder/reranker loaded。只重建 admin；没有数据库或索引写入，也没有部署线上生产。
- 强制重建后用已登录 Edge 重新打开工作台和运行详情。工作台显示 LLM 统计 `5/5/3/2/0/2/0/21`，类型为 test/TXT/规则未识别结构/RecursiveChunkStrategy v2/parser v1，测试不触发升级提示；Trace 运行详情仍明确标记“处理运行记录（非 Trace）”，文件名、doc_id、三方 SHA、run_id、Trace ID 与 11 个阶段一致，显示“文件与运行身份核验通过”。完整身份与统计文本证据 `.playwright-mcp/postmerge-rebuild-authenticated-edge-20261116.txt`（SHA-256 `FB50AE7B16D13C5CB0134990595D2A43A6D3462B4376C268301A8F2E411B0280`）；工作台页面快照 `.playwright-mcp/postmerge-rebuild-llm-stats-20261116.yml`（SHA-256 `3E986EE9C4BD573D1E0508CA2AFEC260F6CBAD944DC45BC5103C10CDC95AB14A`）和截图 `.playwright-mcp/postmerge-rebuild-llm-stats-20261116.png`（SHA-256 `AFD2F1F9EB47BA947F350CE470E108193C2C86258374F385BFA3EC1D2C98BDA7`）。
- 独立 Playwright 会话刷新详情接口得到 401，因为该会话没有登录授权；没有将其计入已登录 Edge 的页面通过结论。最终运行态、PR/CI、镜像和健康信息汇总见 `.playwright-mcp/postmerge-runtime-verification-20261116.txt`（SHA-256 `D9D3623D5B6AB79615C1E9F9DAA4B9132A0056B9C019E51A59C1EF6F8AD6D61C`）。整体验收仍为 `FAIL`，剩余资料/历史数据缺口不因代码合并或本机部署而改变。

## 2026-11-16 — 上传链路魔法参数审计与复验

- 审计范围：管理端/web/客服的上传组件与 BFF、APISIX 请求限制、FastAPI 上传/授权/流式读写、文件锁与进度队列、OCR/PDF、元数据 LLM、表格处理、Parent/Leaf、清洗/增强、Embedding/向量索引和质量详情读取。参数台账逐项区分了协议边界、资源保护、代码/Provider理由、缺少运行曲线的待调阈值；没有把“具名常量”误说成“已证明最优”。
- 修正无来源的固定 multipart 16 KiB 余量及总包误判单文件的问题：APISIX、三个 BFF 和后端请求体门禁统一为 64 MiB；文件字节在上传流按 50 MiB 默认值精确限长。`/rag/health` 返回请求体上限和由差值推导的最大可用余量。最长真实表单尚无样本，业务最大文件端到端余量仍 BLOCKED。
- 修复 Embedding 缓存 TTL 环境设置被构造器的固定默认值盖过：默认读取 `RAG_EMBED_CACHE_TTL_SECONDS`，明确传参仍优先；元数据/Embedding TTL 必须为正，规则快照本地缓存时长必须是有限非负值。将批次 ID 64 字符限制命名化，补足 64 可用、65 拒绝边界测试。
- 其余修复包括：移除 PDF 前 10 页/20 字符分叉和 OCR 500 页硬编码、进度队列 TTL 跟随 SSE 轮询时限、同一 SSE 心跳来源、OCR/元数据/Embedding/表格/切分非法配置拦截、Embedding 并发零值不再静默钳为 1、元数据 LLM 等槽超时不绕过并发上限；用户可见 MIME/文件名/阶段文案使用中文。改动没有上传资料、写共享数据库、向量/BM25、重建索引或修改线上数据。
- 定向验证：后端 241/241 通过（缓存+上传实现 66，MIME/上传配置边界 56，body limit+元数据 LLM+pipeline 初始化 42，PDF/OCR 43，Celery/SSE 34）；管理端 Vitest 100/100，web BFF 18/18，客服 BFF 9/9；本轮修改的 Python 文件 `py_compile` 通过，`git diff --check` 通过。后端以 `--noconftest` 运行，数据库端口指向不可连接的隔离端口；HTTP 路由用授权桩，过期回退用例用运行态桩，避免接触共享库。
- 仍缺真实企业 PDF/扫描件分布、最大表单字段样本、OCR/Embedding provider 的延迟/429、缓存命中率、单任务峰值 RSS/并发/排队数据。因此 1 MiB 读块、5 MiB/500 ms 进度、OCR 30 字符/200/300 DPI/100 页、PDF 版式阈值、LLM 输入与并发、缓存保留时间、切分与过滤阈值只做来源/范围检查，不猜测调数，相关业务调校为 BLOCKED。具体数值、证据和所需数据见 `UPLOAD_PARAMETER_AUDIT_20261116.md`。
- 任务起始留存的“CI 重跑待确认”已按 GitHub 实际记录更正：PR #475 已于 2026-11-16 合并，8 个检查中 7 个成功，真实 API RAG Smoke Evaluation 按工作流跳过，不计通过；检查详情和合并 SHA 见 GitHub PR `https://github.com/Jayus-07/agent/pull/475`。本任务代码变更另走 PR 和共享开发栈验收；线上生产不部署。

## 2026-11-16 — Trace 服务时限来源与上传指标中文最终复验

- 修复旧 Trace 把无来源的默认 `10,000ms` 当服务时限并误显示“已超限”：`TraceRecord` 默认阈值改为未配置；DTO 只有同时具备正阈值和来源才返回服务时限；旧记录即使数据库仍保留 10,000 字段但没有来源，也返回 `sla=null`，不回写/伪造历史数据。删除图编排按复杂度估算的 30/60/90 秒值和 RAG 固定 30 秒值；未来显式阈值还需给出来源。
- 继续实机检查发现并修正 Trace 身份字段 `doc_id`、`run_id`、`Trace ID`，以及根 Span/Trace 墙钟说明和 `ms` 单位；页面显示为中文编号、中文口径和毫秒。原始上传文件正文、真实文档编号、SHA、模型名等源数据保持原值；质量容器内部路径仅显示记录是否生成。
- 定向验证：管理端 Vitest 3 个文件 29/29 通过；`npm exec -- tsc --noEmit` 通过；`git diff --check` 通过。此前后端 SLA DTO 定向测试 7/7 通过。未运行数据库触碰型全量测试。
- 本机仅重建共享 `agent` 栈的管理端；构建产物 image `sha256:610871c272d3afad950a23f72d5ce993cbf01d027b73132b6ae69cf0d65e568c`，容器 `9b1259d3ecca8ee4ee53ae47ec015d647a7527a10a55a9285ee91e1910490a59`，状态 healthy。`/login` HTTP 200；app `/health` 为 ok、schema consistency/migrations ok、Redis healthy；BGE `/healthz` 为 ok 且 embedder/reranker 已加载。宿主构建前可用内存约 2.49 GiB。没有上传、写数据库、索引/重建索引或部署线上生产。
- 本机管理端重建后的真实 Trace 页面由已登录管理端浏览器打开；页面身份为文件 `TEST-FIN-001_虚构差旅与费用报销规则.md`、doc_id `d0f6f40d2b`、Trace `aaa5b3095749`、run `153d5626-9919-44f8-bea7-d68f1414b244`，URL SHA 与登记 SHA 均为 `78a07bbef3a6d08fc5c78393246fe0145b83a007bb0f1bf20a4e88bacf041edd`。本次页面自身 `GET /api/observability/traces/aaa5b3095749` HTTP 200；API 状态 success、20 spans、17,055ms、`sla=null`；页面身份通过，服务时限来源/阈值显示“未配置”、判定“不判定”，文档类型“财务文件”、业务领域“财务”、策略“规则兜底”；没有假超时、原始英文字段名或 `/app/data/quality_records` 内部路径；新开页面 Console 错误 0。一次独立手工 `fetch` 未附授权得到 401，不算页面 API 证据；页面实际加载的 200 响应才作为验收证据。
- 实机证据均保存在本工作树 `.playwright-mcp/`：`trace-sla-live-postbuild-20261116-full-checks.json`（15/15 页面断言全通过）、`trace-sla-live-postbuild-20261116.json`、`trace-sla-live-postbuild-20261116.yml`、`trace-sla-live-postbuild-20261116.png`、`trace-aaa5b3095749-api-200-postbuild-20261116.json`。API 响应、页面检查、快照及截图均未覆盖已有证据文件。
- 此修复只关闭无来源 SLA 假告警和本样本上传 Trace 的英文界面缺口，不改变总验收结论：完整测试集仍有意外失败/未运行资料，企业生产事实仍缺真实来源，活动旧库仍有 Parent/Leaf 断链，历史完整 Trace 仍有 404。以上分别按矩阵中的 FAIL/BLOCKED 记录；新修复不抵消旧问题。本段写入时 PR/CI 状态尚未产生；后续合并与本机实机结果见文末最终补记，不能据此旧句判为当前仍待 PR。
- 按用户要求核验 PR #466：GitHub 记录其已于 2026-11-16T08:54:47Z 合并；PR #476 已于 2026-11-16T09:07:51Z 合并并已包含在本轮 `origin/main`。#466 的发布就绪说明明确列出仍需在生产管理端重新录入无法由当前主密钥解开的供应商 API Key，并配置可访问的 `PROMETHEUS_URL`。这些生产凭据/设置没有来源于本任务授权，本轮不改生产 secret/config、不触发线上发布；本地共享开发栈的实机验收不等于生产发布验收。

## 2026-11-16 — PR #478 合并后本机部署与总验收封存

- 按任务要求重新 fetch：`origin/main=3cd3769d213b3f94a79fbcab0bedf34d44c6cffa`，PR #478 `fix: retry trace retention store initialization` 已合并（merge SHA 同上，PR head `b8abc3cb348d7f3f38a62c4598407c1eaaf13d44`）。GitHub 实际 Checks 为 3 项成功（RAG 变更检测、Secret scan、RAG Smoke 8 cases）、2 项跳过（离线预检、真实 API RAG 评测）；跳过项不算成功。自动审查记录无 findings。发布镜像 run `43860417664` completed/success；Deploy production run `43860749403` completed/cancelled。
- PR #478 修复 Trace retention 后台任务在启动时遇到数据库锁/初始化失败后退出的问题：首轮延迟后再初始化，初始化失败留在循环中按配置间隔重试（至少 60 秒）。隔离定向测试 `test_retention_loop_retries_trace_store_initialization` 为 1 passed/3 deselected；Python `py_compile` 通过。PR #477 的 SLA 来源和 Trace 中文界面修复也已包含于当前 main。
- 本机验收工作树从上述最新 main 新建 `codex/evaluation-acceptance-final-docs-20261116`；切换前确认跟踪文件无改动、目标分支不存在、`.playwright-mcp/` 未被 main 跟踪，所有 199 份旧浏览器证据原样保留。新增 5 份页面/API证据和 1 份运行态文本，全部使用此前不存在的新文件名；页面/API文件复制前核验目标不存在、复制后源/目标 SHA 全相同，目前工作树 `.playwright-mcp/` 共 205 个文件。没有 reset、清理或覆盖浏览器证据。
- 共享本机开发栈当前 `agent-app-1` 和 `agent-frontend-admin-1` 均 healthy；app 容器 `130c45ef...` / image `sha256:521ef7638a1208ca422f28ca5e7cda46381c72c6b6dcaacefc577d24e71817f8`，admin 容器 `98d6bcc5...` / image `sha256:f206e527d73cec0eb2c1842b504167da751b0608d77054bd65de1a988e86592e`。`/health=ok`、迁移状态 `ok` 且 applied_count=134、Schema 检查 72/72、Redis `ping=pong`、`/login=200`。app 健康响应的 build commit 字段为 `unknown`，因此另以 SHA 对比证明运行镜像内 `backend/observability/trace_retention.py` 与最新 main 文件完全一致：`6830B8CF...03D46A63`。没有进行本轮上传、数据库写入、索引或重建。
- 合并后实机页面重新登录后复验，避免使用过期 token 的缓存页面：文件 `TEST-FIN-001_虚构差旅与费用报销规则.md` 的实际 Trace API HTTP 200、status success、20 spans、17,055 ms、SLA 为 null；页面显示文件名、doc_id `d0f6f40d2b`、run_id `153d5626-9919-44f8-bea7-d68f1414b244`、Trace `aaa5b3095749`、登记 SHA 与入口 SHA 一致、身份核验通过；无来源服务时限显示“未配置/不判定”。工作台实时显示 234 篇；LLM 兜底统计卡展示 test/TXT/结构未识别/递归切分 v2/解析器 v1，5 候选、3 采用、2 未采用/失败，测试事件不触发升级提示。浏览器直接调用 API 时必须显式使用管理端 Bearer token；未带令牌返回 401 是预期鉴权，不算页面接口失败。登录态请求统计与 Trace API 均为 HTTP 200。
- 数据库/API/UI 对账：`enterprise-llm-fallback-db-postreindex-20261116.txt` 为明确 `BEGIN READ ONLY` 并 `ROLLBACK` 的查询，近 30 天值为候选/调用/采用/Provider 失败/边界拒绝/未采用/跳过/输出块 `5/5/3/2/0/2/0/21`，类型唯一且全部 test；登录态 API JSON 同值，管理端工作台同值。内部/对客受众无事件，生产升级路径仍 BLOCKED，不制造假样本。
- 本机运行态和新页面/API证据：`.playwright-mcp/postmerge-final-runtime-20261116.txt`（SHA-256 `2960362D09CDBBF556EB607DEB6566E38AE071C3658A15C773AB00164F6175C0`）；Trace 快照/截图 `.playwright-mcp/postmerge-final-authenticated-trace-20261116.yml/.png`（YML SHA `7D3E80F7D4CCDA268132F80D08BEF8BCB698979D568B7C08E8B1CFD5C9906050`，PNG SHA `A62F6C7308B57DC3F4916905401773D7B4BCD34420A0135076AE636973BAC0C0`）；工作台快照/截图 `.playwright-mcp/postmerge-final-authenticated-workbench-20261116.yml/.png`（YML SHA `F076CC0EA4FB87A99F85C016AA277F4503F0C519716AA8EB3F406345BD7BD008`，PNG SHA `AC239A160095171CF1AFED7E4DDCEE84A8AF2600A690BED6B057755C230F3035`）；API 响应 `.playwright-mcp/postmerge-final-authenticated-api-20261116.json`（SHA-256 `287BCAEB48B5E7ADEFC3AF9F48DF1E095BB7914CA3DBCD5A7B9CE7B5E6592DB0`）。这些文件没有保存访问 token 或登录凭据。
- 生产线上 Deploy workflow 已实际核实为 cancelled；本轮主动取消了该 run，没有让任何部署任务执行。原因是 PR #466 的发布就绪说明仍要求重录供应商 API Key 与配置可访问的 `PROMETHEUS_URL`，同时线上 schema/迁移状态未知，而本任务明确禁止改生产库。本轮没有改生产 secret/config/数据库；不能把本机共享栈部署写成线上发布。若将来发布流程需要迁移或写生产库，还需先改变本任务“不改线上生产库”的边界并确认变更窗口。
- 总验收结论封存为 **FAIL**（验收已完成，不表示所有项通过）：测试全量 FAIL，生产真实事实 FAIL，新增资料结构/索引通过但旧库整体 FAIL，当前文件入口与新资料页面身份通过但 196 条历史完整 Trace 404 为 FAIL，LLM test 统计 DB/API/授权/UI PASS 而 internal/customer 升级路径 BLOCKED，上传链路已修复有依据的问题而最终业务阈值调优 BLOCKED。逐份文件/缺件、范围、证据及可修复性见 `ACCEPTANCE_MATRIX.md` 的最终状态覆盖矩阵；不扩大为 Ragas，也不提交、推送或再开 PR。

## 2026-11-17 — 174 份归属、80 分软告警与服务时限

- 174 份无项目运行记录的原件全部归属桌面 `C:\Users\wh\Desktop\企业ai平台\测试文件\...`，没有一份位于生产文件目录；174/174 存在且 SHA 已登记。项目 Registry、处理运行、索引运行和向量记录均为 0，因此它们不是库内孤儿记录，不能执行数据库删除或重解析；属于目标测试知识原文的文件需走管理端新上传，计划、manifest、脚本、盘点表等测试支撑文件保留在桌面但不进向量。台账：`D:/tmp/evaluation-center-acceptance-20261116/desktop-file-inventory-current.csv`。
- 登录管理端文档列表/API 当前显示 234 篇；PDF 列表 API HTTP 200 返回 20 篇。可见的 80 分记录包括：`scanned_partial_fail.pdf`（doc `0ae627687c`，27 块，OCR 9/10 页，`parser_pdf_visual_content_unparsed`、`ocr_degraded`）；`staff_regulation_scnu.pdf`（doc `UF-01-01`，74 块，OCR 1 页，1 个空块过滤）；`spec_API网关技术规范.pdf`（doc `89a0c81720`，12 块，过滤 9 块，其中 8 个过短、1 个重复，并有 2 个 `value_loss` 告警）。三份桌面原件 SHA 均与项目 API 一致。
- 只读检查原件：`scanned_partial_fail.pdf` 共 10 页且均为图像、无 PDF 文本层；第 9 页清楚，第 10 页明显低对比度；清单 profile=ocr、is_scanned=true，但未写预期终态。该记录应作为测试警告样例保留，不能改分数或宣称测试通过；若要求 10/10 页完整可检索，需要提升原件清晰度或 OCR，并复验。
- `staff_regulation_scnu.pdf` 共 20 页、PDF 文本层共 10,046 字符；第 1 页含图像封面，其余正文有可提取文本。清单标 `is_scanned=false`，运行只对 1 页触发 OCR，符合混合封面/正文的表现；当前 80 分由 OCR 告警和 1 个空块过滤造成。管理端分块抽屉已看到封面标题、文号和通知标题均进入块，当前没有正文缺失证据；作为测试资料保留软告警，不为追求 100 分重建。
- `spec_API网关技术规范.pdf` 共 3 页、文本层 835 字符，原件视觉排版正常，属于桌面百文档压测测试资料并在兼容库 `rag_100_docs`。原件与管理端 12 个分块对照后，2MB、3/10 秒、500 QPS、20%、2 次/200ms、200 条等关键值仍可见；但表头/列值在部分块中被压扁且有重复，支持保留软告警，未证实关键数值实际丢失。该测试样本用途不变前不重解析、不标通过；若需验证表格解析改进，应在隔离测试库做版本对照并复验检索。页面/API快照：`C:/Users/wh/Documents/ChatGPT/agent/.playwright-mcp/page-2026-11-16T19-16-59-221Z.yml`；PDF只读渲染：`D:/tmp/pdf-review-80scores-20261117/`。
- Trace `7baa5110a2ed` 的 API 返回 `sla=null`，管理端准确显示“未配置/不判定”。服务时限不是切分或索引正确性的前提；只有在明确内部处理时限或对外承诺时才配置，来源与阈值必须由业务/运维标准给出，不用默认数字补齐。
- 写入前 `agent_memory` 备份已完成：`D:/Program Files/workplace/agent/backups/pg/agent_memory/agent_memory-manual-20261116-184836.dump`，368,779,643 bytes；`pg_restore -l` exit 0。该核验期间没有上传、重解析、删除或数据库/索引写入。此前管理端文件选择器拒绝读取桌面路径，原生窗口自动化因无法确认当前浏览器 URL 被停止；后续上传仍须通过管理端授权文件选择方式完成。

## 2026-11-17 — 重索引失败根因、质量告警全量盘点与实时修复

- 开工前 `git fetch origin` 确认最新 `origin/main=3cd3769d213b3f94a79fbcab0bedf34d44c6cffa`，从该基线新建 `codex/fix-reindex-trace-details-20261117`；工作树原有 `.playwright-mcp/` 未清理、未覆盖。PR #479（head `8b56da413c644e44b5d230cb7e40ca75b78e7a4e`）已 squash 合并，合并提交 `de18c0d904f21dfb771a11ce7a54c12c70c2d462`；合并后 fetch 确认 `origin/main` 同为该提交，工作树 HEAD 内容与之相同。
- 用户发起的 `scanned_partial_fail.pdf` 重索引并未失败：操作日志 `3507` 显示 success；Registry `doc_id=0ae627687c`、SHA `13a742fa…6004`、run `60a1f724-dbc0-46de-bd29-ac9884e093e2`、Trace `a6063a972f63` 一致。Processing run 为 success、27 chunks；解析和 vector/BM25 write 成功。Trace 顶层 error 来自可选阶段：LLM 切分在 `_reject_overbudget:2818` 触发 `ContextBudgetExceededError`，规则切分继续；问题生成另有无效响应回退。原操作日志链接只传 Trace ID，缺 doc_id，因此旧页面无法调 Registry/run，导致身份详情缺数据；历史 Trace API 本身仍返回阶段明细。
- 修复 Trace 链接，携带 doc_id、名称、操作和时间；详情页在缺少 run_id/hash 时以 Trace ID 查找当前文件的运行，再用 Registry SHA 校验身份。成功 run 含可选回退时显示“索引成功（2 个阶段已回退）”和解释，保留 Trace 阶段错误。首次镜像构建发现卸载取消路径缺 `null` 返回；补齐后 Docker Next.js 正式构建通过。前端定向 Vitest 为 2 files/22 tests passed。
- 修正切分兜底事件分类：`ContextBudgetExceededError` 是 Provider 请求之前的预算拒绝，不计入真实 Provider failure；API SQL 也对历史 `failure_class=ContextBudgetExceededError` 做兼容分类，并从实际 LLM 调用量排除。定向 pytest `backend/tests/rag/test_llm_fallback_chunking.py backend/tests/rag/test_processing_lineage_pg.py` 为 13 passed；Python `py_compile` 与 `git diff --check` 通过。
- 两次重建前均重查同一共享 Docker project `agent`、容器占用、宿主可用内存、Redis 与 BGE；最后一次建前可用内存 2.92 GiB、Redis healthy、`/healthz` 200。使用本工作树重建管理端与后端；没有第二套栈、没有模型推理任务。最终 backend、RAG service、RAG index worker、Redis、PostgreSQL、管理端均 healthy，后端 `/health=200`、BGE `/healthz=200`。重建未改知识文档或向量；页面/API统计仍是 234 documents、6,186 chunks。
- 登录态真实管理端按操作日志链接再次打开 `a6063a972f63`：页面展示文件名、doc_id、登记/原件 SHA、run_id、Trace ID、27 chunks 和“索引成功（2 个阶段已回退）”；Trace、Registry、run list、run detail 四个实际页面请求均 HTTP 200；Console 0 errors/warnings。证据：`.playwright-mcp/reindex-operation-entry-final-href-20261117.json`、`.playwright-mcp/reindex-trace-final-ui-live-20261117.yml/.png` 和四份 `reindex-trace-*-final-live-20261117.json`。
- 登录态真实工作台的统计 API/UI 对账：候选/实际调用/采用/Provider 失败/预算边界拒绝/未采用/跳过/输出块=`6/5/3/2/1/3/0/21`。6 条全部 test audience，测试提示不触发生产升级；没有 internal/customer 事件。真实 DB/API/页面证据分别为 `.playwright-mcp/reindex-llm-fallback-db-readonly-final-20261117.txt`、`.playwright-mcp/reindex-llm-stats-final-live-20261117.json`、`.playwright-mcp/llm-stats-final-ui-live-20261117.yml/.png`。数据库证据明确使用 `BEGIN READ ONLY`/`ROLLBACK`；未插入或改写事件。
- 真实管理端文档列表 12 页 API 全部 HTTP 200，234/234 唯一文档均被读取；全量质量汇总是 pass=65、soft_warning=169、failed=0、unknown=0。169 份告警按 SHA 与桌面台账逐份匹配为 169/169，其中测试目录 122、生产目录 47；清单均有最近运行 ID。只读 DB 核对这 169 个最近运行全部存在且 success：135 次 operation=reindex、34 次 operation=upload；pipeline_version 都为 `rag-processing-v1`，`git_sha` 均为空，因此不能据此证明它们是否对应某个精确的新代码提交。165 条 `legacy_quality_record_incomplete` 证明质量记录缺完整性/覆盖/清洗覆盖字段，但不等于这 165 份都没有运行或都未走新代码。运行版本核对证据 `.playwright-mcp/quality-warning-run-version-db-readonly-20261117.txt`。生成逐份质量清单 `.playwright-mcp/quality-warning-inventory-20261117.csv`，并保存 12 页 API 响应 `documents-warning-page-01-20261117.json` 至 `-12-20261117.json`。原因码按重叠类别统计：重复率提示 95、含过滤块 77（空 17/重复 50/过短 50/中文比例低 15）、value_loss 11 份、heavy filtering 12、噪音 7、OCR degraded 3、PDF 图像内容未解析 2、Excel 隐藏表跳过 1。11 份 value_loss 按 SHA 匹配 8 份桌面测试压测资料、3 份生产候选；其中 `差旅费报销管理办法2026修订版.txt`、`general_security.pdf`、`厦门-景点-厦门园林博览苑.md` 的 authority/版本分别仍待核，不能作为权威生产事实。
- 174 份无归属文件再次确认来自桌面测试目录，项目无 Registry/run/vector 记录；不能删除或重索引不存在的库记录。当前 234 篇活动文档中 218 份 SHA 匹配桌面台账（160 测试/58 生产），另有 16 份为已登记新建测试/政策材料；本轮未执行删除、上传、重解析或索引。管理端全选只选当前页；批量 API 实际逐个提交单文档重索引，不存在全库一键按钮。没有对 234 份发起重索引。
- 服务时限字段不解释这次 Trace 缺身份：本 Trace `sla=null`，页面显示“未配置/不判定”。补做 30 天只读运行时长盘点：成功 upload 84 次，p50/p90/p95=12.6/28.6/38.4 秒；成功 reindex 15 次，p50/p90/p95=15.1/30.9/31.8 秒；另有 upload 失败 42、reindex 失败 8、duplicate 4，未混入成功时长分位数。该数据是处理 run 时长，不包含已证明的排队/文件传输全链路；还未按格式、大小/页数、OCR 路由及并发细分，不能直接当对外 SLA。内部 SLO 需服务责任方据此基线和业务目标定阈值后配置；外部 SLA 必须有合同或正式承诺来源。证据 `.playwright-mcp/reindex-sla-baseline-db-readonly-20261117.txt`，SQL 使用 `BEGIN READ ONLY`/`ROLLBACK`。
- GitHub PR #479 检查为 6 项 success、1 项按条件 skipped（真实 API Smoke）；本地定向测试为后端 13 passed、前端 2 files/22 tests passed，正式构建和真实管理端/API复验通过。合并触发生产镜像发布 workflow `43950979498`；各镜像构建/推送步骤显示 success，但为遵守“不改线上生产库”边界，在自动流程结束前取消该 workflow。Deploy workflow `43951776162` 随后排队，但尚无 job 执行即被取消；生产部署脚本 `scripts/release.sh` 会执行 `docker compose run db-migrate`，因此未触发生产迁移、未写生产库。共享本机开发栈已运行并实机验收合并内容；本轮无知识文件上传、删除、重索引或生产库写入。桌面 174 份如何筛成知识原文、165 份历史质量记录是否逐份重解析、3 份生产候选的权威来源与 SLO 数值是明确未通过/阻塞项，不通过盲目全库重索引解决。

## 2026-11-17 — 用户追加项：169 软警告、重解析、三页显示与 OCR Token

- 本轮工作树 `codex/knowledge-ingest-acceptance-20261117` 的 HEAD 与已 fetch 的 `origin/main` 同为 `de18c0d904f21dfb771a11ce7a54c12c70c2d462`。保留已有 `.playwright-mcp/` 文件；从主工作区复制本轮页面/API证据时逐项先检查目标不存在，复制后 SHA-256 一致；未 reset、覆盖或清理既有证据。
- 处理 169 软警告采用单文件测试 KB canary，没有批量重索引或触碰 production-style `travel`。写入前核对宿主可用内存约 3.1 GiB、共享 Compose 21 服务、PostgreSQL/Redis/RAG 健康，worker active/reserved/scheduled 为空，并使用以下三份 agent_memory 单文件前备份；三份 `pg_restore -l` 均 exit 0、各 1378 项：`D:\tmp\agent_memory_before_softwarning_canary_20261117.dump`、`D:\tmp\agent_memory_before_softwarning_metadata_fix_reindex_20261117.dump`、`D:\tmp\agent_memory_before_financial_softwarning_canary_20261117.dump`。先后重解析两份 `rag_100_docs` 测试 CSV：`legal_商标与域名台账.csv` 原件 SHA `802de886cfffa0b08996da9123c8c69347cc6dba4118554ecd9cac3a405e38aa`，doc `b510208c47`、run `99c357cd-d1bf-4b8f-b503-161968a1680a`、Trace `fa00ae4c4dc6`、8 块；`table_发票台账_2026.csv` SHA `828e0de247020704be7249349f6fbbbbc6477decb5e4fcd7cb74f0e14957232a`，doc `989ebe4cad`、run `df86c57c-724c-4bc2-9bf3-cb59afd7f966`、Trace `26a5420e69ac`、9 块。均走新 parser/chunking/metadata/embedding/vector/BM25 阶段，项目登记的 doc_id 不变，质量状态由运行结果重算，没有直接 UPDATE 分数；商标 CSV 仍留有“内容重复率偏高”质量提示。
- 最新登录态只读统计 API `GET /api/rag/stats` 与 `GET /api/rag/documents?quality_status=soft_warning&page=1&page_size=500` 均 HTTP 200：234 活动文档，pass=67、soft_warning=167、failed=0、unknown=0；167 条当前 KB 分布为 rag_100_docs=92、travel=41、policy_general=23、cs_policy=6、rag_eval_kb=4、cs_aftersales=1。当前 163 条有 `legacy_quality_record_incomplete`；过滤块 77、value_loss 11、heavy filtering 12、OCR degraded 3、PDF 视觉内容未解析 2、Excel 隐藏表跳过 1（类别重叠）。完整 167 行带 doc_id/SHA 的 API 响应留档；174 份桌面测试文件仍没有项目 Registry/run/vector，本轮未上传。
- 对“重解析索引”边界作了实测：有 Registry 且仍能读取原件的 doc 会保留 doc_id，使用当前处理代码重新解析、切分、抽元数据、写向量和 BM25，并新增 run/Trace；它不是从桌面原件重新走安全/权限入口。只有两份 canary 更新，当前全库 234 文档没有全量重解析，故“所有旧文件已更新”结论为 FAIL；没有项目 doc_id 的 174 份原件只能后续经上传链路入库。全量重索引仍受“不批量改 travel”限制。
- 内部服务时限配置已设置为统一代码默认 `RAG_INDEX_SLO_THRESHOLD_MS=60000`，允许用同名环境变量覆盖；30 天成功上传 p95=38.4s、重索引 p95=31.8s。重索引 Trace 页面实际显示 60 秒默认来源、60000ms、27.2s 未超限；这只是内部处理时限，不含排队/文件传输，也不是对外合同 SLA。生产部署与生产环境变量没有实测。
- 管理端本轮用当前工作树重建 admin；Next.js 生产构建、Lint/类型检查成功，49/49 静态页完成，容器 healthy、`/login` HTTP 200。登录态真实页面复验：待复核 1 条（中文“旅游指南/旅游”、93%、14 个过滤块原因）；入库失败 74 条（异步 70、质量门禁 4，缺历史 Trace 明示“无历史 Trace”）；操作日志总 3,480 条、本页 20 行，新重索引行 actor=1014/Trace 对应，历史失败 3509 不伪造操作者与 Trace，显示“操作者未记录/未关联追踪记录”，6 天未更新的上传行明确“状态待核对”。真实页面截图和自动化快照见 `.playwright-mcp/*live-rebuilt-20261117.*` 与 `page-2026-11-16T23-12-43-699Z.yml`、`page-2026-11-16T23-12-55-506Z.yml`、`page-2026-11-16T23-13-03-437Z.yml`。
- 操作日志失败终态的 actor 修复随本工作树重建 `rag-index-worker`；重建前 Celery active/reserved/scheduled 都为空，Redis/PostgreSQL/RAG 健康且 restart=0。新 worker healthy、restart=0，镜像中确认存在 `_get_reindex_actor_id`。历史失败行仍然 anonymous，因为旧数据库没有 actor/Trace，未回填。
- 定向验证：`backend/tests/rag/test_financial_reindex_rollback.py`、`test_reindex_async_flow.py`、`test_indexer_pipeline_unified.py`、`test_active_index_runs.py` 的相关子集 `6 passed, 30 deselected`；前端 `PendingReviewPanel`、`OperationsPanel`、`UploadFailuresPanel`、`TokensUsagePanel` 4 个文件 `19 passed`。Vitest 有 React `act` 测试环境提示但无失败。先前 host 上 `pnpm exec vitest` 因无 pnpm lock 未成功，pnpm 将 21 个 npm 包目录移到 `frontend-admin/node_modules/.ignored`；没有用清理命令删它们，最终测试改在 Docker 的 npm lockfile builder 中运行。host `node_modules` 仍需后续用 npm 维护，本机 admin 容器和构建不受影响。
- 近 7 日 UTC 日历窗口（11/10 00:00 起）最终只读核对：数据库、授权 API、Token 页面一致为 2,733 调用/1,535,631 Token（输入 1,425,411、输出 110,210）、229 个请求。`qwen-vl-ocr` 222 调用、381,792 Token，页面计价状态缺失 222 次并显示“金额未知”；有一条 provider 为空、模型 `m` 的 10 Token 记录也被计入但无法归属。OCR 模型最近健康检查记录较旧，两个 CSV canary 均未调用 OCR；未伪造 OCR 价格或宣称 OCR 本轮重跑成功。DB/API/UI 快照：`.playwright-mcp/token-model-db-window-match-after-relogin-20261117.txt`、`.playwright-mcp/tokens-live-api-after-relogin-20261117.json`、`.playwright-mcp/tokens-live-final-after-relogin-20261117.png`。
- 一次不带 Authorization 的人工只读探测得到 401；随后登录态 bearer 请求复验 `/api/rag/stats` 与文档列表均 HTTP 200，最终统计和完整 warning inventory 已保存。登录页自动快照 `C:\Users\wh\Documents\ChatGPT\agent\.playwright-mcp\page-2026-11-16T23-29-56-252Z.yml` 会包含浏览器预填凭据；没有复制到任务证据；本机策略拒绝删除该单个新生成快照（Remove-Item 被拦截）。需要从主工作区 `.playwright-mcp` 手动删除此文件。此路径/动作不包含凭据文本。
- 本轮未部署线上、未写线上数据库、未提交/合并；仅在本机共享开发栈运行 UI/worker 代码，知识库写入限两份受控 `rag_100_docs` canary。最终结论：验收完成但总体 **FAIL**：167 软警告与全量旧文件更新仍未完成；生产权威资料、OCR 单价/归属、旧历史 Trace 缺失等按验收矩阵明确阻塞。

## 2026-11-17 — Token 用量页定位、费用分层与趋势图复验

- 页面定位：Token 用量页是“模型调用量与费用分类监控”，让管理员按时间、组件、模型看总量/输入/输出、调用次数、日趋势、模型拆分和可追到 Trace 的单次调用；它不是供应商账单。费用至少分为已核实精确价、按调用日公开价估算、套餐抵扣金额未同步、本地推理无云端 Token 账单、金额未知。不能把分组覆盖率说成实际账单覆盖率。
- 设计调整：保留平台白底/蓝紫色与现有布局，把费用卡从多类口径挤在一条长文本改为精确计价次数、公开价估算金额及套餐/本地/未知调用标签；日趋势用固定高 128px（`h-32`）的柱区承接百分比高度，保留 Token/成本切换、日期和悬停明细。页面继续明确公开价不是最终账单、本地 ¥0 不含硬件和电费。
- 实机发现并修复：此前 API 有按日数据，但管理端趋势柱使用百分比高度且父容器没有明确高度，浏览器无法显示柱高；同卡费用文字也会连续换行难读。修复后登录态 DOM/API 有 7 个日期桶，Token 趋势 7 根柱的实际高度为 4.5–128px；成本趋势同样显示估算发生日及其余日期的零值刻度。
- 当前共享开发栈实测窗口：UTC 2026-11-11 00:00 至 API 捕获时间 2026-11-17 02:12:22.202。授权 API HTTP 200，管理端刷新后 UI、API、以相同 UTC 截止点进行的 `BEGIN READ ONLY` 数据库查询一致为 2,562 调用、输入 1,379,362、输出 110,093、总计 1,489,455 Token；7 个日桶。五个模型/供应商调用合计与 DB/API 一致：Doubao 640、OCR `qwen-vl-ocr` 222、重排 `bge-reranker-v2-m3` 879、向量 `bge-m3` 818、Ollama 3。
- 当前费用分类恰好覆盖 2,562/2,562 次：精确计价 0 次；供应商历史公开价估算 222 次、¥0.1238796（均为 OCR）；套餐金额未同步 592 次；本地无云端 Token 账单 1,697 次；金额未知 51 次（Doubao 失败调用 48 次、Ollama 未核价 3 次）。本地 `bge-m3` 仍有 171 次缺 Token 用量；页面明确提示。故分类展示/数据库与页面一致为 PASS，真实供应商人民币账单核价仍 BLOCKED，不能把 ¥0.1238796 说成实际支出。
- 证据：`.playwright-mcp/token-usage-v2-final-live-20261117.png`、`.playwright-mcp/token-usage-v2-final-live-20261117.yml`、`.playwright-mcp/token-usage-v2-final-cost-live-20261117.png`、`.playwright-mcp/token-usage-v2-ui-refresh-api-20261117.json`、`.playwright-mcp/token-usage-v2-db-readonly-ui-refresh-20261117.txt`。数据库查询按 API 捕获时刻加上截止条件，排除捕获后新增的两条重排调用，避免活动中的本地任务造成错误对账。
- 修订后前端定向 Vitest 1 文件 8/8 通过；Next.js 生产构建、类型检查和 49/49 静态路由通过。本机只替换 `agent-frontend-admin-1`（容器 `49ac78ff...`，镜像 `sha256:a19ba8d4...`），health=healthy、`/login`=200；页面真实 summary API=200。一次用于确认鉴权边界的手工无令牌 fetch 返回 401，是 Playwright 日志中唯一 console error；登录态页面 API 仍为 200，不计为应用接口故障。最初宿主 Vitest 因工作树未安装 node_modules 无法启动，随后复用 Docker 构建依赖层完成 8/8。临时测试镜像标签已移除；未上传资料、写数据库/向量或部署线上。
- Token 页面/接口修复自身结论 **PASS**；精确账单金额仍 **BLOCKED**，所需条件是供应商套餐扣费导出/账单与 Ollama 实际计价来源。整体任务仍为上节的 **FAIL**，本节不覆盖其他验收项的未通过结论。
- 快照说明：保留了较早的无上界诊断文件 `.playwright-mcp/token-usage-v2-db-readonly-final-20261117.txt`；它比 02:07:05.728Z API 快照晚读到两条新重排调用（各 300 Token），因此不作为三方同刻对账证据。时间上界、API、UI 均一致的正式证据是 `token-usage-v2-ui-refresh-api-20261117.json` 与 `token-usage-v2-db-readonly-ui-refresh-20261117.txt`；时间差说明见 `.playwright-mcp/token-usage-v2-db-readonly-final-caveat-20261117.txt`。

## 2026-11-17 — 嵌入模型用量与处理运行归属追加验收

- 当前本机工作树以 origin/main / HEAD de18c0d904f21dfb771a11ce7a54c12c70c2d462 为基线；没有切换分支、提交、合并、线上部署或访问线上生产库。使用本工作树执行 devctl.bat rebuild backend /y 后，app、RAG service、RAG index worker 等本机共享开发服务重建并健康；/health 为 200、Redis healthy、RAG /readyz 为 ready，BGE /healthz 为 ok 且 memory_cooldown=false。worker 镜像内已确认包含文档摘要向量归属修复；镜像标记 dirty=true。
- 管理端当前文档页再次显示 234 篇：质量通过 68、需复核 166、失败 0、未核验 0。测试台账文件 table_仓库库存台账_GBK.csv 在 rag_100_docs 中为质量通过 100 分、9 个文本块；这是一条已有的单文件测试 canary，本轮修复后没有重新索引它。真实页面和 stats API 证据：.playwright-mcp/embedding-lineage-workbench-live-20261117.png、.playwright-mcp/embedding-lineage-workbench-live-20261117.txt、.playwright-mcp/embedding-lineage-workbench-stats-live-20261117.json。
- Token 页在“全部 / 近 7 天”下，于 2026-11-17 03:27:39 UTC 的授权 GET /api/observability/tokens/summary?days=7 返回 200。相同截止时刻的 llm_usage 只读 SQL（BEGIN READ ONLY / ROLLBACK）与页面、API 一致：2,595 次调用，输入 1,390,765、输出 110,696、合计 1,501,461 Token；模型分别为 Doubao 643/630,207、OCR qwen-vl-ocr 222/381,792、重排 bge-reranker-v2-m3 907/369,000、向量 bge-m3 820/120,448、Ollama 3/14。bge-m3 的 820 次调用均有模型/供应商记录，其中 649 次有 Token 估算、171 次输入输出和总 Token 均为 0；不把这 171 次说成完整用量。DB/API/UI 证据：.playwright-mcp/embedding-token-usage-live-20261117.png、.playwright-mcp/embedding-token-usage-live-20261117.txt、.playwright-mcp/embedding-token-usage-api-live-20261117.json、embedding-token-lineage-db-readonly-live-20261117.txt/.sql。
- 模型与供应商真实页面显示 14 个注册角色、6 个登记模型，运行时链路按角色绑定生成、每 30 秒随配置刷新。入库链路当前显示 Doubao 文档抽取/切分兜底/元数据、qwen-vl-ocr、custom-gpu 的 bge-m3、pgvector。代码确认 llm_models / llm_providers 数据库注册表是模型清单来源，实际用量记录实际 provider 与 model；这属于管理端登记配置动态加载，不是自动枚举供应商全部未登记模型。Provider 驱动族仍由代码实现。证据：.playwright-mcp/model-provider-registry-live-20261117.png/.txt、backend/infra/llm/models.py、backend/services/model_config.py。
- 对历史 Trace 879b82adcff2（文档 doc_id=7ef7008da3，原件 SHA-256=b1289ecc59d926993361d6ad822bda08dd5e276454884e66965a31baecf20dd8，run_id=c2ff22e3-8faa-418b-811e-d0b54f80a4f7）逐项对账：Trace API HTTP 200，汇总 bge-m3 两次调用、1,282 Token，分别有“Embed document summary”和“Embed 9 chunks”；处理运行详情 API HTTP 200、11 步，但 usage 只有 3 行（两次 Doubao + 一次 bge-m3/946 Token）。只读 llm_usage 有 4 行，漏关联的是摘要嵌入 bge-m3/336 Token，其 run_id、step_id 为空。因此旧 Trace 汇总有数据，但处理运行详情与它不完整；历史归属不能伪造回填。真实页面/API 证据：.playwright-mcp/embedding-lineage-trace-live-20261117.png/.txt、.playwright-mcp/embedding-lineage-trace-api-live-20261117.json、.playwright-mcp/embedding-lineage-run-api-live-20261117.json；数据库对账见 embedding-token-lineage-db-readonly-live-20261117.txt/.sql。
- 已修复未来记录的根因：文档摘要向量写入曾绕过 ProcessingRunContext.bind_stage；新增 _add_doc_level_vector_with_lineage，为摘要嵌入建立 embedding 阶段并将实际调用关联 run_id/step_id。定向测试 backend/tests/rag/test_doc_summary_embedding_lineage.py 为 1 passed；本机 worker 重建后已确认新代码载入。未执行修复后的 reindex，所以新运行详情的实机结果仍未验收，不能写成已通过。
- 写入前另做数据库备份并验证目录清单：D:\tmp\agent_memory_before_summary_embedding_lineage_20261117T0320Z.dump，368,350,106 bytes，SHA-256 B2E1EAA96C65C6C6C8BE58AD0340EDF4E446DC4DE5A1684CC42FA6E7FBCE7A36，pg_restore -l 有 1,363 个 TOC 项。本次备份后没有执行数据库、向量或 BM25 写入。
- 修复后 canary 被资源安全条件阻止：宿主可用内存约 1.70 GiB；PostgreSQL 容器约 870.6 MiB / 1 GiB；一次采样中 agent-worker 与 rag-index-worker CPU 分别约 85% / 52%，尽管 Celery active/reserved/scheduled 都为空。RAG、Redis、BGE 健康不代表当前有足够余量做索引写入。为避免在共享开发库和向量索引上冒险，本轮只完成独立的页面/API/只读 SQL 验证并停止写操作。下一步需等宿主内存回到至少 2 GiB、PostgreSQL 不再贴近容器上限且队列空闲后，重新做资源/模型健康检查和新备份，再仅对该一份测试文件复验；不得批量重跑 travel 或生产库。
- 本追加结论：嵌入调用在 Token 页和 Trace 汇总中有记录，供应商/模型身份来自动态登记表 **PASS**；bge-m3 有 171/820 次缺 Token 估算 **FAIL**；历史摘要向量缺 run_id/step_id 的详情缺口已定位并修复代码，本机 worker 已加载，但修复后数据库运行态复验 **BLOCKED**。全任务仍为 **FAIL**，166 条质量警告、生产权威资料缺件、历史 Trace 缺失等已有未通过项未被本追加覆盖。

## 2026-11-17 21:34 CST — 最终总验收封存

- 重新核对 GitHub 与基线：PR #479 已合并；`origin/main` 与 HEAD 都是 `de18c0d904f21dfb771a11ce7a54c12c70c2d462`。GitHub 3 个工作流成功，任务层 7 项成功；真实 API RAG Smoke evaluation 被明确跳过，不能算通过。起始“CI 重跑待确认”已解决并改为上述事实，不引用别的任务记录。没有提交、再次合并或线上部署。
- 已建立本任务最终矩阵 `ACCEPTANCE_MATRIX.md`，对测试 382 路径、生产候选 103 路径、主库 6,406 Chroma 行/253 文档、向量/索引相关记录 61,115 行（含 router_index 索引项）、6,409 Chunk、253 个 Trace 入口、LLM 统计和真实相关管理端页面逐项给出分母、结果、证据、PASS/FAIL/BLOCKED 和可修复条件。逐份状态见证据 CSV，不以抽样替代全量台账。
- 测试文件：208/382 个路径曾有项目登记；178 个路径当前映射主 Chroma（170 个唯一测试文档）；30 个路径只有历史状态；174 个路径从未登记。既有 28 份测试 RAG 样本有 4 份意外失败、4 份软警告。174 份中有支撑材料，不应整批上传。测试资料与验收集 **FAIL（覆盖不完整）**。
- 生产候选：103 路径中 82 个唯一文档当前在主 Chroma（81 个路径直接映射）；21 个候选从未登记，1 份员工手册只有失败/删除历史。权威版本/有效期尚未覆盖，合同/价格/SLA/制度/供应商客户/实际运营事实仍缺真实原件。生产资料 **FAIL（企业事实覆盖不充分）**，缺件逐份列于 `production-no-project-history-21.csv` 和来源台账。
- 向量范围：主 Chroma 的 253 个文档均有来源并关联 Registry，其中 170 测试、82 生产、1 个台账外生成测试夹具；评测集合、router、候选向量等独立集合另有记录，所以若按全部物理向量要求纯粹来自桌面文件，结论 FAIL。18 篇停用 KB 文档仍有物理向量。结构方面，252 个活动文档 68 通过、184 软警告，另 1 个待复核通过；存在孤儿 Chunk、1,934 个 Leaf 缺父指针、739 个 Parent 无 Leaf、版本元数据缺失及 BM25 全库证据不足。没有通过改分数或批量重索引掩盖问题。
- Trace 全量结果：253/253 已检查；42 个 Trace API 成功，193 个只有处理运行回退，18 个停用库详情不可查。4 个成功 Trace payload 未带文件名（页面从登记信息补齐），22 个列表入口未带 Trace ID（运行详情可关联）。已有历史 Trace 丢失不可伪造修复。
- LLM 兜底最近 30 天数据库/API/页面一致为 `候选 6 / Provider 调用 5 / 采用 3 / Provider 失败 2 / 边界拒绝 1 / 未采用 3 / 跳过 0 / 输出块 21`；样本全部为测试受众。测试统计口径 **PASS**；没有真实 internal/customer 事件，生产升级提示 **BLOCKED**。
- 相关管理端页面已经实际打开并记录：知识库工作台六页签、上传与分块抽屉、全部 253 个 Trace 入口、任务、运行总览、网关、Token、模型/供应商/价格、预算及健康告警。当前页面的英文枚举/错误、停用资料 404、OCR 成本显示为零、Prometheus 不可达、模型健康告警及过期的重建结果均有实页证据。Token 最新登录态 API、数据库只读结果、页面在截止 `2026-11-17T13:33:59Z` 三方一致为 2,784 调用/1,557,896 Token/228 请求；其中 OCR 222 次、381,792 Token，页面显示 ¥0.00，仍判 FAIL。
- 最新 Token 证据是 `.playwright-mcp/final-admin-completeness-20261117/token-usage-api-live-final-20261117.json`、`token-usage-db-readonly-live-final-20261117.sql/.txt`、`token-usage-ui-live-final-20261117.yml/.png`。数据库查询使用 `BEGIN READ ONLY` / `ROLLBACK`，无写入。
- 本次最终补录与页面证据采集阶段未上传、删除、重解析、重建或写数据库/向量/BM25。共享 Compose 的构建上下文指向另一条有相同文件改动的脏工作树，故不在那里强制重建或替换服务；这项部署阻塞有确切条件，不反复尝试。检查的是真实共享页面，但不能把当前未提交工作树的代码修复声称已经发布。
- 最终状态：**验收已完成；整体验收 FAIL，而非所有项通过。** 没有未检查项；每个 FAIL 都附修复范围/限制，每个 BLOCKED 都附可执行前置条件。无需重复同一检查。

## 2026-11-18 — 测试库批次上传与待复核/入库失败实机验收

- 用户授权在测试库批量上传并检查管理端“待复核”“入库失败”页面。本轮目标 KB 为 `desktop_test_174_20261118`，只写测试库；未改生产库、生产向量或 BM25。桌面 174 份逐行台账位于 `.playwright-mcp/desktop174-test-upload-20261118/desktop174-upload-manifest.csv`。
- 逐份 SHA/终态对账：174/174 有当前 SHA；其中 1 份测试 README 的当前 SHA `29b328bba718944fbcac940a6068319c9d0eb79b2db050f3d423a26468b418d5` 与旧登记 SHA `b93d3cdf577fd783789371b942c2d9b41d44e070380c6ae1c8a6b0434d041ff0` 不同，项目库与本机当前原件匹配当前 SHA。文件级新对账 CSV 174 行：本次批次成功发布 18 份（17 active、1 pending_review，528 Chunk 行、546 向量行；18/18 quality_status=pass）；另 2 份失败未发布；1 份此前 canary 仍 active、soft_warning/90 分；152 份未尝试；1 个 JSONL 不受当前管理端上传器支持。故 174 份覆盖结论 **FAIL**，不能将批次中的 18 份成功说成 174 份完成。
- 当前批次两份失败为 `sku_mangled_by_excel.xlsx`（SHA `6eb33b11ec38299e92f9addca1e873efdced2e19bd2ea7083478f7123cbb040a`）和 `来源清单.csv`（SHA `863c495744658f2d088a47f382f74f985d65afe07bd2fa462fe33e6da5830da1`）。数据库显示各有 2 条 failed processing_run（共 4 条）；当前候选 doc_id 分别 `49d9ba994c` / `0f2471d7ed`，最新 run/Trace 分别 `9fd8e22f-e528-4831-9743-0387f66489e2` / `9ed1edfb9755`、`d7408633-c66b-45f7-bb8e-32644d8df10b` / `21f5c0cd4ae1`。共同错误为 `RequestBudgetExceeded: request_tokens`。CSV 有 174 个解析候选块，但发布失败；两份失败记录的持久 Chunk 行和向量行均为 0。因此这是模型请求预算阻断，不能据此判定 CSV/Excel 解析器功能通过或不通过。
- 真实页面/API：待复核 API/UI 共 2 条，1 条是本批 `福州-景点-崇妙保圣坚牢塔.md`（doc `fabd65212b`、SHA `a6b574665027755d8aac8ea78646ba698a9122ae6cd64ac723a254fa23d37e0d`、run `9a4c470e-7a4c-4c45-b7bb-488e3bb76b3c`、Trace `d63ce605c49f`、confidence 0.96、质量 100、pending_review），另 1 条是旧 `travel` 的厦门文件。入库失败 API/UI 共 77 条：异步队列 73、质量门禁 4；本批新增的两条失败原因都被中文显示为请求 Token 预算超限、文档未发布。
- 逐开 CSV 与 Excel 新失败 Trace 详情。CSV 页面有 18 个阶段并显示 174 个解析候选块，但身份区为“身份未核验”，失败入口未把数据库中的 doc_id、SHA 与 run_id 传进详情；数据库与页面身份信息不一致。浏览器证据还显示接口页在异步加载期间会短暂显示 0，加载后更新为 API 的真实 77，属于加载瞬态，不是空列表。
- 为修复上述可修问题，从当时最新 main `de18c0d904f21dfb771a11ce7a54c12c70c2d462` 建立独立分支 `codex/fix-candidate-trace-details-20261118`。改动包括：失败列表按上传 ID/文件 SHA 查询候选运行并返回 doc_id、SHA、run_id、Trace ID；详情 API 仅在租户、知识库和 `can_read_row` 授权通过时读取无 Registry 的未发布候选运行；页面将其明确标注“未发布”，按 run 与入口 SHA/doc_id/Trace 身份核验；`RequestBudgetExceeded` 不再按普通网络故障重试/降级逐条请求。定向回归、越权隐藏及预算短路测试已补充到 RAG smoke workflow。
- 验证结果：本机 `py_compile` 通过，`git diff --check` 通过。定向 pytest 首轮 24 passed、1 个新增授权拒绝用例因测试 fixture 少 `principal.department` 失败；修正 fixture 后该用例单独 1 passed，故定向断言都通过，但没有做一次全量干净重跑。pytest 测试 harness 同时记录了 `TaskService.ensure_schema` 连接池等待 30 秒超时，测试结果仍按实际通过/失败数报告。候选 worktree 的 `frontend-admin/node_modules/.bin/tsc` 不存在，未运行前端构建或组件测试。
- 实机修复未部署到共享管理端：本轮复查宿主仅 1.84 GiB 可用；`rag-index-worker` 681 MiB/768 MiB、`rag-service` 489 MiB/512 MiB。Redis 容器 healthy，但没有继续上传或重建，也未探测模型端点；下次任何写入前必须重新检查资源、Redis 和嵌入/模型健康、备份目标测试库。当前共享页面仍是修复前版本；未提交、未合并、未部署。需等资源恢复并解决请求 Token 预算，再对失败两份单份复试和处理剩余 152 份支持格式文件；JSONL 评测材料继续排除。
- 本轮最终状态：**本批页面/API/数据库对账已完成；174 份测试材料 FAIL（18 成功、2 预算失败、152 未尝试、1 不支持、另 1 既有软警告 canary）；失败 Trace 实机修复 BLOCKED（代码仅在未部署分支）**。不代表此前已封存的总矩阵全项通过。
- 证据：逐份 `.playwright-mcp/desktop174-upload-reconciliation-verified-20261118.csv` 与 summary；DB 只读 `.playwright-mcp/desktop174-test-kb-db-readonly-final-20261118.json`、`desktop174-test-kb-runs-readonly-final-20261118.json`、`desktop174-batch-db-final-2055.txt`；管理端 API `.playwright-mcp/desktop174-pending-api-body-final-2054.json`、`desktop174-failures-api-body-final-2054.json`、`desktop174-trace-csv-api-final-2055.json`；页面截图 `.playwright-mcp/desktop174-pending-final-2054.png`、`desktop174-failures-final-2054.png`，快照 `desktop174-pending-page-final-2055.txt`、`desktop174-failures-page-final-2055.txt`、`desktop174-trace-csv-page-final-2055.txt`、`desktop174-trace-excel-live-20261118.txt`；资源门禁 `.playwright-mcp/desktop174-resource-gate-final-20261118.txt`。所有新文件均为新建证据名，未覆盖原有浏览器证据。

## 2026-11-18 — 跨工作树未提交改动整合与定向复验

- 重新确认整合基线为 `origin/main=32aa8612e53ec91f8555182bd6470b55808f5675`，在新工作树 `C:\Users\wh\.codex\worktrees\uncommitted-changes-20261118`、分支 `codex/uncommitted-changes-20261118` 上整合。没有切换、清理或覆盖原来的四棵相关工作树；异步状态重复补丁只保留一份。排除主工作树的登录/澄清流程、另一份浏览器证据目录和企业质量进度记录这三项无关改动。
- 新工作树当前包含 49 个已跟踪文件改动和 5 个新增回归测试；`.playwright-mcp/` 浏览器证据未复制、未改写，仍在原工作树。含异步任务状态收口、失败候选 Trace 身份、Token 费用分类/历史估算、重索引质量元数据/回滚，以及本机启动预热预算隔离修正。
- 整合时前端 `OperationsPanel.tsx` 的 JSX 拼接错误被定向测试发现并修复；Token 页面测试夹具也补上 API 契约类型。复验用工作树自己的锁文件离线安装 Vitest 4.1.12 与 TypeScript 5.9.3，不再复用主工作树的旧 Vitest 4.1.11。
- 定向验证：后端 16 个相关测试文件 `173 passed`（131.55 秒）。`PGHOST=127.0.0.1`、`PGPORT=1` 指向不可用端口；pytest 收尾的残留检查报告连接池超时并跳过，未连接共享或生产 PostgreSQL。`backend/tests/observability/test_obs_stores_pg.py` 因会清理 PostgreSQL 测试表且当前没有隔离测试库，未运行，标记为数据库集成未验收。
- 前端管理端 4 个受影响页面测试 `22 passed`，`npx tsc --noEmit` 通过；Vite 只输出现有 `configLoader: native` 配置警告。`git diff --check` 通过，未发现冲突标记。
- 本轮只做本地代码整合、安装新工作树忽略的依赖和定向测试；没有写共享/生产数据库，没有上传或索引资料，也没有从此整合分支重建/部署本机服务。因此代码行为的共享管理端实机复验仍为 **UNVERIFIED**，PostgreSQL 集成用例也未验收。原四个相关工作树和证据保持未提交；整合分支当前也尚未提交、推送、合并或部署。
- 部署门禁记录：`docker compose ls` 显示共享 `agent` 栈 21 个服务；宿主可用内存 2.88 GiB，Redis、嵌入/重排模型健康，当前 API HTTP 200。未重建的原因不是资源或服务不健康，而是合并分支的维护任务会收口全库超时运行，潜在覆盖 `travel` 等共享知识库；当前没有该共享库的隔离副本与本次改动专用备份/恢复验证。应先在有备份的隔离库验证收口 SQL 和影响范围，再决定是否部署到共享本机栈。
