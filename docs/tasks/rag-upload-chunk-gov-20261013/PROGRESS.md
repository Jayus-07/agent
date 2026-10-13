# PROGRESS: 企业 RAG 上传、结构识别、Chunk 切分与元数据治理

- 分支：`task/rag-upload-chunk-governance-20261013`（基线 origin/main @ 2d1d0b1）
- 状态：DONE（PR #55 Codex Review 5/5 已修复、回复并解决；未合并 main）

## 批次对账（REUSE / FIX / BUILD / BLOCKED）

| 批次 | 类型 | 提交 | 内容 | 测试 |
| --- | --- | --- | --- | --- |
| B1 P0 正确性 | FIX | 7cf9d82 | P0-01 FAQ 局部配对（annotate_qa_nodes）；P0-02 覆盖缺口（Structure 根引导块/纯容器跳过/嵌套去重、Step 章节外叶子、QA 保留非 QA 内容）+ coverage_audit；P0-03 apply_index_chunk_identity 同步 parent_chunk_id；P0-04 docx body 序/pdf 事件流表格归属；P0-05 _split_to_budget+token_counter 口径恒定 | 179 passed |
| B2 P0 超长文档 | FIX | eeceb9f | 截断=质量门禁硬异常（不假成功）；CSV dtype=str 前导零；Excel 数值保真+行/单元格上限；OCR 页上限/失败页留痕；修复事件流丢 OCR 内容回归 | 110 passed |
| B3 P1 结构识别 | FIX | 4254c17 | StructureProfile 信号集；Router 强信号豁免；ChunkRouteDecision 审计记录；策略收敛评估（保留 6 策略，Step/Legal 边界保护已公共化） | 50+golden passed |
| B4 P1 元数据 | FIX | b46fd22 | full_text 纯 leaf 拼接；summary_sampling 章节感知采样；模拟问题均匀采样+占位问句停用；chunking_doc_type 审计 | 69 passed |
| B5 发布安全 | REUSE（核查） | — | CAS 续跑/BM25 bundle 原子/去重 active 口径/pending_review 复核位置 全部核查通过，无需改动 | 111 passed |
| B6 验收矩阵 | BUILD | 2def153 | 16 类文档矩阵（15 实测+1 由 cascade 锁定）；Fixed/Recursive leaf 与表格 fallback 挂虚拟 parent；Financial 根引导块；coverage_audit 表格单元格口径 | 329 passed |

## 验收指标达成

- 可索引叶子覆盖率 100%（矩阵全部用例 uncovered==0，表格按单元格判定）。
- 全部 leaf 的 parent_chunk_id 可解析（含 Fixed/Recursive/表格兜底虚拟 parent）。
- Leaf≤500 / Parent≤2000 严格达标（_hard_split_by_budget 整串口径收口）。
- 前导零 SKU（CSV/Excel 文本单元格）与精确数字无损失。
- chunk/解析级表格/OCR 截断 → 硬异常 failed，无部分内容 active 假成功。
- 更新失败不影响上一代 Active（候选隔离存储，候选期主 collection 不可见）。

## 遗留风险 / BLOCKED

1. tiktoken BPE 文件在受限网络不可用时，token 计数进程内降级字符估算（口径恒定已保证）；
   生产镜像内 tiktoken 可用，无影响。
2. BM25 快照 4 文件回退分支（bundle 损坏时）存在理论混代窗口，低风险；生产加载优先 bundle。
3. TXT parser 剥离编号标题前缀（"第一条 合同履行"→section 标题"合同履行"）为存量行为，
   Legal 身份由 section_title 文本承载；如需完整条款号需动 parser（影响 txt 通用标题语义）。
4. PDF 表格线旁文本被标题启发式误判为 section 的存量误判（表格内容不丢，重复入索引概率低）。
5. 超过 MAX_CHUNKS_PER_DOC 的文档以 failed 拒绝（任务书允许的语义），文档内分区批处理
   未实施——5000 上限内文档由既有分批 embedding/写入承担资源控制。
6. 模拟问题停用占位问句后，LLM 不可用文档无 simulated_questions（兼容读取保留，检索不退化）。

## 环境备注

- 基线存量失败：`test_quality_gate.py::TestRegistryPermissionColumn::test_register_and_read`
  在未改动基线（git stash 后）同样失败，属本机 PG 环境项，与本任务无关。

## PR #55 Codex Review 收口（2026-10-14）

- 核对分支：`task/rag-upload-chunk-governance-20261013`，起始 PR HEAD
  `9dc4e4740d911e02496323f0ab95a12e69233b35`；PR base 为 `2d1d0b1`，当前
  `origin/main` 为 `888738365666706488492c90ee8b97d87b173d02`。没有同步、合并或改写 main。
- Codex Review 5 条意见均经源码与新增回归确认成立：CSV 完整载入后截断；Excel
  额度逐 Sheet 重置且触顶仍继续扫；Financial 超长根正文缺少关联 Leaf；Coverage
  Audit 把 Parent 当作 Leaf 且表格抽样单元格检查会假通过；coverage 没有进入质量门禁，
  审计失败可 fail open。
- 先在未修改 PR 基线上运行新回归，10 项失败，复现上述缺陷。另有
  `test_quality_gate.py::TestRegistryPermissionColumn::test_register_and_read` 在未改动
  基线上因本机 PostgreSQL `localhost:5432` 未提供密码而失败；该项 BLOCKED，不作 PASS。
- 本轮改动：CSV 改为逐行读取，限额内保留数据、继续流式计数以准确记录行/单元格截断，
  保留 UTF-8 BOM/GBK 与原始字符串；Excel 对整个 Workbook 共享额度、触顶停止后续读取，
  报告下界并显式标记总量未知；Financial 根正文补齐预算内且关联 Parent 的 Leaf；覆盖审计
  只读实际 Leaf，表格行/单元格检查重复项数量，抽样标记 sampled；覆盖缺口/审计异常和
  解析级截断经现有 QualityGate 写入记录/问题/Trace 并拒绝部分内容 active。
- `rag.md` 已同步上述完整性与截断状态语义。无 partial coverage active，因此无需 UI 把部分
  索引标成完整的例外展示路径。
- 初次修复后定向集发现 1 条旧 Excel 断言仍预期读取上限外第 6 行（35 passed、1 failed）；
  已更新断言以匹配“触顶即停止”，后续整组回归通过。
- 定向 RAG 集（含 16 类矩阵、Parent 关联率/Token 上限、CSV/Excel/PDF OCR、质量门禁）：
  最终复跑 `125 passed, 1 skipped, 1 deselected in 49.11s`。另一次独立质量门禁集：
  `6 passed`；最终 `py_compile` 与 `git diff --check` 通过。
- PR 免费 Smoke 门禁本机复现：`python -m backend.evaluation.preflight --module rag
  --selection pr_smoke` 输出 `EVAL_PREFLIGHT_PASS=true`，8 个用例；对应 4 个 CI 合约/门禁
  测试 `28 passed`。文档中的离线评测命令因本机缺 `PGPASSWORD` 且未显式指定 fixture
  失败；该次不是 Smoke，不报告为通过。

## 最终收口结果（2026-10-14）

- 修复提交：`5a61a097b31a6e85911f28bb8f7270be11ed8926`，已推送至现有 PR 分支；工作区干净。
- 修复代码 HEAD 与可信 Smoke 验证 HEAD 均为 `5a61a09`；PR API 返回 `MERGEABLE / CLEAN`。
  五条 Codex Review Thread
  均已逐条回复并解决（5/5）。
- 最终核验时远端 `main` 为 `76334163a82f2fa9a969155da2800f7918476e5b`；PR 记录的 base 为
  `2d1d0b173f50128429d9c868301ee05b38bcd85b`。当前 `main` 相对分支有 28 个独有提交，
  PR 分支有 8 个独有提交。收口期间 main 曾从 `8887383` 前进到 `7633416`；按任务指令
  没有同步、合并或改写 main。
- 定向 RAG 回归：`125 passed, 1 skipped, 1 deselected`；独立质量门禁集 `6 passed`；
  16 类文档矩阵、Leaf/Parent 关联与 Token 上限均包含在定向集内。失败前新增回归在干净基线
  复现 10 项失败；修复后通过。`py_compile` 与 `git diff --check` 通过。
- PR 免费 Smoke、`RAG Smoke (8 cases)` 总门禁、Secret Scan 均通过。Secret Scan 的
  工作树、diff、提交区间本机扫描均为 0 命中；GitHub `secret-scan` run 通过。
- 按用户要求人工触发可信真实 RAG Smoke：run `38480805885` 成功，报告 `8/8` 通过、
  0 失败/错误、`EVAL_GATE_VERDICT=PASS`；`recall@5=0.9286`、`dept_leak=0`。
  隔离账本有 106 次调用价格未登记（`cost_verification=UNVERIFIED`），不影响 Smoke 检索门禁，
  但费用核对未验证。
- 本机 PostgreSQL 用例在未修改基线复现 `fe_sendauth: no password supplied`，维持
  `BLOCKED`，未计入 PASS。
- 代码与现有 PR 检查状态为 PASS；GitHub 当前标记 `MERGEABLE / CLEAN`。任务按指令没有
  合并 main。考虑当前 main 相对 PR base 的提交差距，合并前应确认仓库是否要求把这些 main
  更新纳入 PR 后再跑组合基线；本任务未做该同步。

## 最终集成基线验证（2026-10-14）

- 在原 PR worktree `D:/Program Files/workplace/agent-wt-rag-chunk-gov-20261013` 执行
  `git fetch origin` 前确认分支为 `task/rag-upload-chunk-governance-20261013` 且工作树干净。
  普通 `git merge origin/main` 无冲突完成，集成提交为
  `230387b52c3d5ccb2a0d4a13997781105f4fdde7`，第二父提交为
  `76334163a82f2fa9a969155da2800f7918476e5b`；未 rebase、force push 或触碰其他 worktree。
- 对比 `origin/main` 新增变更：Travel 写入链路、Business DB 只读角色的初始化/迁移屏障，
  以及 Secret Scan 扫描器。未发现 RAG 上传、Indexer、QualityGate、Provider 或 RAG 迁移
  注册冲突；RAG keyword store 使用的配置与只读角色配置隔离。Business DB 迁移排序、失败时
  保持角色禁用和迁移注册契约由 `test_startup_validation.py`、RAG migration registration
  定向覆盖。未执行真实 PostgreSQL 迁移。
- 集成后定向回归：`184 passed, 1 skipped, 1 deselected`。覆盖 CSV/Excel/OCR 解析、Financial
  根正文 Leaf、Coverage Audit/质量门禁、16 类文档矩阵、Parent 关联与 Token 上限、启动及 RAG
  迁移契约。唯一 skip 是近重复矩阵用例因当前 MinHash 接口不可用（测试注明由 metadata
  cascade 覆盖）；PostgreSQL 本机用例按已知基线认证故障排除，仍为 `BLOCKED`，不是 PASS。
- 本次 main 修改后的 Secret Scan 在此 Git worktree 实际运行：tree、`origin/main..HEAD` diff、
  history 三种模式均 0 命中；扫描器回归 `12 passed`。这三项均非“非 Git 仓库跳过”。
- 保留此前可信 RAG Smoke `38480805885`（8/8 PASS）；未重新运行付费 Provider 评测。费用核算
  仍为 `UNVERIFIED`（106 次调用价格未登记）。
- 推送后 GitHub 在 `c40ed5bc8fd5a868cc2e6e1ac03c9f41d7f96c0c` 的检查：RAG Smoke preflight、
  `RAG Smoke (8 cases)`、Detect RAG changes、Secret Scan 均 PASS；真实 API Evaluation 按 PR
  策略 SKIPPED。PR 为 `OPEN`、`MERGEABLE / CLEAN`、未合并，Codex Review Thread 5/5 resolved。
- 最终进度记录推送后，GitHub 自动检查复核为：RAG Smoke preflight、`RAG Smoke (8 cases)`、
  Detect RAG changes、Secret Scan 均 PASS；真实 API Evaluation 按 PR 策略 SKIPPED。PR 保持
  OPEN 且 MERGEABLE / CLEAN，Review Thread 5/5 resolved。本任务不触发付费评测，也不合并 PR。
