# RAG 20k 阶段 0 证据索引

> 每项证据给出一条重建/校验命令；外部材料只记路径与状态，不入 Git。
> 出口门：`D:/Python/python.exe backend/scripts/check_rag20k_phase0.py docs/evidence/rag20k/phase0`（0=通过，2=有缺口，1=schema 错误）。

## 0.1 环境基线（已完成）

- 证据：[baseline/8ad9e77f1b4fd9f4/baseline-manifest.json](baseline/8ad9e77f1b4fd9f4/baseline-manifest.json)（capture_id = 内容指纹前 16 位，阻断项 0）
- 重建：`D:/Python/python.exe backend/scripts/collect_rag20k_baseline.py --repo-root .`
- 性质：同一环境重复采集得到相同 capture_id（幂等已验证）；证据目录自身的未跟踪文件不参与指纹（排除策略记录在 `git.fingerprint_excludes`）。已提交的证据经 commit 哈希参与后续指纹。

## 0.2 决策记录 Q1—Q10（blocked：0/10 confirmed）

- 证据：[decisions/Q1-Q10.json](decisions/Q1-Q10.json)（权威）｜ [decisions/Q1-Q10.md](decisions/Q1-Q10.md)（镜像）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_decisions.py docs/evidence/rag20k/phase0/decisions/Q1-Q10.json`（当前退出 2 = 存在 blocked）
- 状态流转规则见 Q1-Q10.md；无签字结论前不得改 confirmed。

## 0.3 100 文档双跑（未开始）

- 证据：`evaluation/reproducibility.json`（待产出）
- 前置：`codex/rag-eval-kb-unification` worktree 合并；实施计划 Task 5（比较器 `backend/scripts/compare_rag_baselines.py` 尚未创建）。

## 0.4 20k 语料清单（校验器就绪，blocked）

- 证据：[corpus/validation-summary.json](corpus/validation-summary.json)（status=blocked：`D:/rag20k/manifests/corpus-20000.jsonl` 未提供）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_corpus.py <清单路径> --expected-count 20000 --output docs/evidence/rag20k/phase0/corpus/validation-summary.json`
- 摘要只含分布/分位数/复合键不可逆哈希/错误行号；校验器绝不读取文档正文。

## 0.5 500 条黄金查询（校验器就绪，blocked）

- 证据：[golden/validation-summary.json](golden/validation-summary.json)（status=blocked：500 条双审标注未提供）
- 校验：`D:/Python/python.exe backend/scripts/validate_rag20k_golden.py <标注路径> --output docs/evidence/rag20k/phase0/golden/validation-summary.json`
- 九类配额与互斥规则定义在 `backend/audit/rag20k/golden_manifest.py` 模块文档；`backend/tests/audit/fixtures/rag20k_golden_valid.jsonl` 是合成夹具，不是业务标注。

## 0.6 风险登记册（部分完成：owner 待指定）

- 证据：[risk-register.md](risk-register.md)
- 11 项风险（P0×5 / P1×6）的 trigger/mitigation/rollback 取自审计报告 §7.2；owner 与 due_date 待负责人指定。

## 外部依赖状态

| 材料 | 约定路径/来源 | 状态 |
|---|---|---|
| 20k 语料清单 | `D:/rag20k/manifests/corpus-20000.jsonl` | 未提供 |
| 500 条黄金标注 | `D:/rag20k/manifests/golden-500.jsonl` | 未提供 |
| Embedding/LLM 供应商书面配额 | 供应商函件 | 未取得（Q6 blocked，Embedding 并发固定 1） |
| Q1—Q4/Q8—Q9 签字 | 业务/运维/安全负责人 | 未签字 |
