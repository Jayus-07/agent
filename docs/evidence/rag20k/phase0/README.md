# RAG 20k 阶段 0 证据索引

> 每项证据给出一条重建/校验命令；外部材料只记路径与状态，不入 Git。
> 出口门：`D:/Python/python.exe backend/scripts/check_rag20k_phase0.py docs/evidence/rag20k/phase0`（0=通过，2=有缺口，1=schema 错误）。

## 0.1 环境基线（已完成）

- 证据：[baseline/8ad9e77f1b4fd9f4/baseline-manifest.json](baseline/8ad9e77f1b4fd9f4/baseline-manifest.json)（capture_id = 内容指纹前 16 位，阻断项 0）
- 重建：`D:/Python/python.exe backend/scripts/collect_rag20k_baseline.py --repo-root .`
- 性质：同一环境重复采集得到相同 capture_id（幂等已验证）；证据目录自身的未跟踪文件不参与指纹（排除策略记录在 `git.fingerprint_excludes`）。已提交的证据经 commit 哈希参与后续指纹。

## 0.2 决策记录 Q1—Q10（6/10 confirmed，4 项 blocked 待外部材料）

- 证据：[decisions/Q1-Q10.json](decisions/Q1-Q10.json)（权威）｜ [decisions/Q1-Q10.md](decisions/Q1-Q10.md)（镜像）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_decisions.py docs/evidence/rag20k/phase0/decisions/Q1-Q10.json`（当前退出 2 = 存在 blocked）
- 2026-09-19：签字类 Q1—Q4/Q8—Q9 已由项目所有者 Jayus-07 按暂定假设冻结确认；Q5/Q6/Q7/Q10 的确认前提是外部材料或执行结果（20k 清单、供应商配额函、获授权语料、100 文档双跑），到位前保持 blocked，不得以暂定假设冒充已核实结论。

## 0.3 100 文档双跑（run-1 有效，run-2 被外部阻断）

- 已完成：比较器（`eval_reproducibility.py` + `compare_rag_baselines.py`）与适配层（`eval_report_adapter.py`，run_id 不入指纹、NaN 缺席化、fixture_set 回退）。
- **命令实证修正**：cloud 模式需显式 `--judge` 才生成答案（`_allow_cloud = judge or ragas`），锁定命令已增补；否则 169 个答案全空、citation_accuracy 结构性缺席（run-1 早期尝试实测）。
- 索引指纹：`ac633326d7c41bfe`（95 active docs / 734 向量 / BM25 hash 209a4732f0f9433e）。
- run-1（有效）：git `2f2ef44`，169 case = 107 有答案 + 62 RD 拒答（设计使然），报告与 meta 在 `run-1/`。
- run-2 失败留档：`run-2/eval-rag-20260919-072150.md`——07:00 起 DashScope 欠费（400 Arrearage），169 case 全 error；此前一次尝试因并行会话中途提交（R11）被比较器正确判不可比。
- 待解阻：DashScope 充值 + 约 25 分钟无提交窗口；之后重跑 run-2 → `compare_rag_baselines.py` 出 reproducibility.json → 出口门刷新。
- 失败夹具：`table_customer_satisfaction_2026`（161 字节 CSV，内容过薄被质量门禁+中文占比过滤 → ChunkingEmptyError），不修改评测数据设计，如实计入索引失败。

## 0.4 20k 语料清单（校验器就绪，blocked）

- 证据：[corpus/validation-summary.json](corpus/validation-summary.json)（status=blocked：`D:/rag20k/manifests/corpus-20000.jsonl` 未提供）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_corpus.py <清单路径> --expected-count 20000 --output docs/evidence/rag20k/phase0/corpus/validation-summary.json`
- 摘要只含分布/分位数/复合键不可逆哈希/错误行号；校验器绝不读取文档正文。

## 0.5 500 条黄金查询（校验器就绪，blocked）

- 证据：[golden/validation-summary.json](golden/validation-summary.json)（status=blocked：500 条双审标注未提供）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_golden.py <标注路径> --output docs/evidence/rag20k/phase0/golden/validation-summary.json`
- 九类配额与互斥规则定义在 `backend/audit/rag20k/golden_manifest.py` 模块文档；`backend/tests/audit/fixtures/rag20k_golden_valid.jsonl` 是合成夹具，不是业务标注。

## 0.6 风险登记册（owner 已指定，2026-09-19）

- 证据：[risk-register.md](risk-register.md)
- 11 项风险（P0×5 / P1×6）的 trigger/mitigation/rollback 取自审计报告 §7.2；owner 已全部指定为 Jayus-07，due_date 以阶段/里程碑锚点表示（审计报告无日历时间表）。

## 外部依赖状态

| 材料 | 约定路径/来源 | 状态 |
|---|---|---|
| 20k 语料清单 | `D:/rag20k/manifests/corpus-20000.jsonl` | 未提供（落位约定见 `D:/rag20k/manifests/README.md`） |
| 500 条黄金标注 | `D:/rag20k/manifests/golden-500.jsonl` | 未提供 |
| Embedding/LLM 供应商书面配额 | 供应商函件 | 未取得（Q6 blocked，Embedding 并发固定 1） |
| Q1—Q4/Q8—Q9 签字 | 业务/运维/安全负责人 | 已签字（2026-09-19，Jayus-07） |
