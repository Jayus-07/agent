# PROGRESS: 企业 RAG 上传、结构识别、Chunk 切分与元数据治理

- 分支：`task/rag-upload-chunk-governance-20261013`（基线 origin/main @ 2d1d0b1）
- 状态：IN_PROGRESS（PR #55 审查意见收口中，未合并 main）

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
  测试 `28 passed`。文档中的离线评测命令另试一次失败：本机缺 `PGPASSWORD`，并且未显式
  指定 fixture 导致 `fixture_set` 为空；不报告为 Smoke 通过。RAG Smoke workflow 设计为
  PR 免费预检、人工可信 dispatch 才跑 8 条真实 API 评测，待提交后按用户要求触发该入口。
- 仍待 Secret Scan、现有分支 push、5 条 Review Thread 回复/解决，以及可信 RAG Smoke 结果。
- 不合并 main；若真实 API Smoke 或运行环境门禁未通过，最终状态标记 BLOCKED/UNVERIFIED，
  不宣称具备合并条件。
