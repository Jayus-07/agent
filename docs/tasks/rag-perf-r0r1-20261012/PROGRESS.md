# PROGRESS — RAG 检索性能专项优化（R0 + R1）

## 当前状态

`DONE（R0/R1 范围，含 PR #41 审查整改）`——R0 + R1-A + R1-B 已实现、已测、已提交；
R2/R3 仅设计（报告 §9），未实施。等待用户/GPT 复核后再进入后续批次。

## 提交

- `0caf5c0` perf(rag): R0 观测 + R1-A 向量复用 + R1-B 兜底统一，并修复 PR#41 P0/P1
- 分支历史已重写（原 `1e948a7`/`21216bc` 两个提交带明文 Redis 连接串日志，已整体替换为单一干净提交）

## 已完成

- R0：embedding 出站/复用/时长/错误计数 + `embedding` stage + Trace event + 请求级 `embedding_stats`。
- R1-A：请求级去重 + 进程级有界 LRU + 固定条纹锁单飞 + 失败用量审计 + 请求总账跨线程汇总。
- R1-B：兜底与主分类器单规则化（删除业务关键词平行规则与不可达的强信号降级分支）。
- P0：Redis 连接串脱敏（`safe_url_for_log`）+ 删除带凭据日志 + `scripts/scan_secrets.py`
  + `.github/workflows/secret-scan.yml`；凭据轮换登记为 BLOCKED（需用户执行）。

## 证据

- 定向回归：282 passed（20 个文件）。
- 真实链路：provider 向量化 3 次问答 15 → 4；请求总账 requested=1/computed=1/
  kinds={query:1} 与真实出站一致（`artifacts/rag-perf-r0r1/evidence/`）。
- 敏感扫描：`evidence/secret_scan_after.txt`（本分支树与历史均无凭据；
  69 处为既有测试/示例占位命中）。

## 未闭环（挂账）

- 凭据轮换（Redis 口令、`origin/main` 三个 travel 脚本中的真实 API_KEY）需用户执行。
- 父工具→rag-service 的 ~4s 前置缺口未证实（该区间无服务端埋点，属 R3-B）。
- RAG MultiQuery 直调 LLM 不入账：独立 P0，仍未解决。
## 第二轮审查（2026-10-12）

- P0 扫描漏检修复（artifacts 不再整目录豁免、空用户名 URL 命中、--fail-on 分级阻断）。
- P1 CI 历史扫描改为新增提交区间；P1 self-correction 总账移到 ask 生命周期；
  P1 非法 Provider 返回只留一条 error 用量行。
- 定向回归 291 passed；RAG PR Smoke（head 2807e6c）success。
- 凭据轮换仍 BLOCKED（需用户执行），main 三脚本 API_KEY 另立安全债。
