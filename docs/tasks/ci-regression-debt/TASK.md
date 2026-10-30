# CI 全量回归暗债清偿

> 任务 ID：ci-regression-debt
> 创建：2026-10-30
> 状态：IN_PROGRESS

## 目标

把每周 Full Regression（`.github/workflows/full_regression.yml`，13 suite matrix，约 12545 用例）的失败清单收敛到 0：逐周下降直至绿。

## 背景

基线 v1（run 40948443665，2026-10-29）共 108 失败，分组：

| suite | 失败数 | 初步定性 |
|---|---|---|
| orchestration | 24 | 并发敏感（cs_context KeyError、clarify 流断言——xdist 暴露共享状态问题） |
| misc | 22 | inventory `test_notification` 4 个真回归候选 + 环境 |
| tools-security-obs | 16 | 环境缺失：测试要连 `demo` 库（CI 与本地均未建） |
| sql-infra | 14 | 同上 demo 库 + 真失败 |
| travel | 13 | booking 并发/幂等用例在 xdist 下互踩 |
| services-travelv2 | 10 | 待定性 |
| customer_service | 4 | 并发敏感（幂等竞争、状态时序） |
| prompts-context / evaluation / repo-tests | 2/2/1 | 少量真回归候选 |
| backend-root | 0 | — |
| api | 收集崩溃已根修 | 但挂死用例密集，两轮被 50min job 上限杀掉 |
| rag | 基线未取得 | 同上 |

失败清单以 GitHub artifacts（run 40948443665）为准；本地副本 `d:/tmp/regmap/failures_*.txt` 可能被清理。

## 范围

三个专项按序消化：

1. **挂死专项（最优先，阻塞 api/rag 基线）**：api、rag suite 内挂死用例（未收口的 socket/线程/连接）排查与修复，使两 suite 能在 50min job 上限内跑完并产出失败清单。
2. **108 个已分组失败逐类定性修复**：环境缺失补齐（demo 库）→ 并发敏感（测试隔离）→ 真回归候选（改断言或修实现，各带回归测试）。
3. **rag 基线补取**：挂死修完后 dispatch `suites=rag` 拿失败清单，纳入同流程消化。

## 验收

- 每周 Full Regression step summary 失败数逐周下降至 0。
- api/rag suite 能在 50min job 上限内跑完并出失败清单。
- 台账与 PR 描述如实记录；容器级部署验证 UNVERIFIED 必须显式标注。

## 纪律

- 所有改动一律走 PR 合 main；提交用 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
- 不扩大 mock、不删断言、不 skip/xfail 掩盖失败；未实际执行的验证不得报通过。
- 新迁移必须双登记 `.github/scripts/rag_smoke_path_filter.py` + `scripts/init_db.py` 并跑守卫测试。
- 测试-only 修复无需 rebuild；改了 backend 代码要在主仓库 rebuild 并确认健康。

## 关联

- 前序：PR #126–#132（main 基线漂移修复 + full_regression workflow）、PR #136（台账）。
- 本任务文档：[PROGRESS.md](PROGRESS.md)
