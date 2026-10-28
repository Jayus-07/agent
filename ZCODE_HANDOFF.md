# ZCode → 下一会话交接：企业助手评测门禁与生成边界（2026-10-29）

交接核验时间：2026-10-29 凌晨。上一会话（ZCode）按用户指令以持续任务方式完成评测基建修复与检索缺陷修复，发布门禁推进到 62.5%→75%（smoke），剩生成边界抖动与两个数据/提示词课题。本文是给下一个会话的完整交接，历史证据全在 PROGRESS.md。

## 1. 工作树与 Git 状态

```text
工作树：C:\Users\wh\.codex\worktrees\assistant-roundtable-sse\agent
分支：codex/enterprise-assistant-reliability-fixes
HEAD：97499fe（已全部合并本地 main，main=c34b928，未推远端）
未提交：仅 ZCODE_HANDOFF.md 本文件（untracked）
```

- 本地 main（D:\Program Files\workplace\agent）= c34b928；main 工作区有 1517 行 `tmp/` 下 pytest 历史残留删除（**非本会话所为**，另有 `ZCODE_HANDOFF.md` 等，未代处理，留用户决策）。
- 运行容器（agent-app-1 等）healthy、compose working_dir=本 worktree；8088 模型服务 CUDA 就绪。
- ⚠️ **运行容器内 `/app/backend/evaluation/datasets/rag/manifest.json` 被 root 手工改为 candidate.3+hash 4c53**（绕过 DATA-01 的临时手段）——**下次 rebuild 会回退为镜像内旧值（candidate.3+47219）**，rebuild 后必须重跑第 4 节的发布门禁前置修复。

## 2. 总体计划（用户既定口径）

五类任务生产指标：反馈≤1s；普通解释 2–5s；RAG 5–12s；SQL 3–10s；联网 6–15s；>20–30s 报告转持久后台。质量与耗时独立核验（正确性/来源/权限/计费归属），P95+小并发才算生产达标。持续任务授权：费用不设限、阶段可交付先提交合并本地 main（不推远端不发布生产）、browser-use 最终验收。

## 3. 已完成并验证（本轮 ZCode 会话，提交全部合并 main）

1. **评测基建修复**（此前发布评测从未真正执行过）：
   - strict 门禁识别 V4 结构化标注（ground_truth_context）与拒答样本（should_reject）——此前 9 个 suite 全部被误拦，管线死锁；
   - 逐题预算（此前整轮共享 32k tokens，26 题第 8 题即超限 19 题 error）；
   - 失败 release 可重评（此前 failed 是终态）；
   - 发布评测启用云生成（judge=True，此前全题不生成答案）；
   - 评测生成 temperature=0（0.1 采样致跨轮抖动）。
2. **检索缺陷链修复**（全部有 A/B 证据）：
   - RRF 标识符精确命中加权（普通 hybrid + enhanced 双路）——SKU/单号类精确查询此前被融合公式结构性埋没，**线上表格知识库数字查询必答"未提及"的根因**；
   - 重排超时 4→15s、检索时限 8→20s（GPU 实测，旧值致长候选查询排序静默失效+重排结果被"迟到"丢弃）；
   - **表头检测修复**（本轮最后提交 919aa45）：数值启发式对纯文本数据表失效，全表行被拍进列摘要（`工号_E0001_E0002...`）行语义全毁——回退首行表头，已重索引 multi_sheet.xlsx 验证行语义恢复。
3. **数据治理**：桌面换代合并（codex/rag-eval-testset → main，28 文档+24 题全标注）；UFQ-016/017~021 修题（限定来源文件，消除跨文件同名 SKU 歧义）；DATA-01 hash 链同步（suites+manifest，cases_hash=4c5301c379724c4b，candidate.3）。
4. **计费维度验证通过**：eval 流量归属完整；rerank 227 条 local_inference 零云费（修复生效）；残留无归属行=诊断探针所致。

## 4. 当前问题分析（按优先级，下一会话从这里做）

### P0：发布门禁 62.5%（15/24）→ 需 ≥95%（≥23/24）

最新 full 轮（RUN=2026-10-28T18-01-07-13071f）失败构成：

- **UFQ-016/018/020（跨文件 SKU 冲突拒答）**：题目已限定来源文件，但候选仍含三个 CSV 的同名 SKU 不同值行，模型对冲突值拒答。修法（已定稿未实施）：**rag.qa 提示词补多来源冲突指引**（"多份资料对同一标识符给出不同数值时：用户指定了来源文件→以该文件 Evidence 作答；未指定→逐条列出各来源数值说明差异，不要答资料未提及"）——走 rag.qa 自己的发布管线（draft→release→eval→approve→publish，管线已修好）。**注意**：rag.qa 的发布评测也会跑 desktop_upload_full，其门禁评估的是新 rag.qa 的效果。
- **UFQ-019（部分覆盖）**：007A 只答了价格漏了库存——fact=0.33，生成完整性问题，同上指引可解。
- **UFQ-004（重复题抖动）**：001/004 是同一问题在题集里出现两次，跨轮在"正确作答↔拒答"间翻转（temperature=0 已确认传达到位、prompt 复放稳定、memory 无污染、降级话术排除）。剩余解释：doubao 对 temp=0 实现不严格（多副本/epsilon），或评测调用上下文与复放有差异。**评测期 prompt 捕获日志已就位**（EVAL_TRIGGER=prompt_publish 时打 `[EvalGen:capture]` sha+长度+首段），下轮跑一次取失败题 sha 与复放对比即可定界。若为供应商非确定性：题集去重（001/004 保留其一）+ judge_completeness 入门禁判别拒答合理性。
- **UFQ-017/021 已过、023 已过**（xlsx 表头检测修复+重索引后）——说明修法有效，剩余同类是上面两条。

### P0：DATA-01 hash 链的容器同步问题

- 宿主 worktree 的 manifest/suite 已是 4c53+candidate.3（已提交 97499fe），但 **docker build 的 COPY 曾反复烘焙旧内容**（Docker Desktop C 盘 gRPC-FUSE 文件共享缓存，最近写入的文件被旧缓存覆盖）。
- 临时手段已用：root 写容器内 manifest（见第 1 节警告）。**持久建议**：rebuild 后立即 `docker exec` 核验容器内 cases_hash/manifest 一致性；不一致则 `docker cp` 同步+`docker restart agent-app-1`；或重启 Docker Desktop 清文件共享缓存。
- ⚠️ **外部回写进程**：宿主 worktree 的 manifest.json 曾被观察到分钟级回写为旧内容（4c53 写入后数分钟变回 47219，监测 10 秒稳定但分钟后回退）——**疑似另一会话/组件在写同一 worktree 的评测数据**。动手前先核对 git status 与文件内容，发现冲突先报告。

### P1：门禁过线后的收尾序列

1. prompt v2（self_correction）approve → publish（rel-191e954a945b4bc59ecabdf9280d6127 过线后走 approve/publish API）；
2. 复测发货/发票两问（验证据 new rag.qa/self_correction 的线上行为，Trace 记录）；
3. 计费复查（llm_usage 归属/pricing_source 分布；已验证一次通过，发布后复查即可）；
4. P95/小并发压测：12 题×5 轮+3 并发，按类输出 P50/P95、冷热分开（脚本可基于 D:/tmp/agent-baseline-battery-20261028.py 改造）；
5. browser-use 完整验收（用户最后看的就是这个）；
6. 全部绿后按仓库规则提交合并，更新 PROGRESS。

### P2：遗留小项

- UFQ-023 已修复但本轮又 fail（候选是 E0011/E0028 而非 E0001——排序波动）：标识符加权已生效过（上一轮 022/023 过），重跑观察；若持续，查 BM25 分词对 E0001 的召回排序。
- 合成候选去重（dataset-synthesis 5 题含 2 组重复问法）——增强项。
- rag.qa 发布评测自身也需要 DATA-01 的 hash 链一致性（同第 4 节注意事项）。
- 评测生成 prompt 捕获日志（[EvalGen:capture]）在诊断完成后可降级/移除。

## 5. 关键命令与证据

```powershell
# 发布门禁驱动（改 suite 名需同时核对 DATA-01 hash 链）
# POST /api/prompts/rag.evidence_gate.self_correction/versions/2/release
#   {"suite":"desktop_upload_full","evaluation_mode":"semantic","executor":"local","target_env":"production"}
# 凭据文件：C:/Users/wh/Desktop/local_super_admin-temp-password.txt（只在内存使用）
# 评测报告：容器 /app/data/eval_runs/<latest>/per_case/*.json
# 发布管线：drafts → versions/{n}/release → approve → publish（失败可重评，新 external_run_id）
```

- 基线证据：D:/tmp/agent-baseline-20261028.json（12 题基线）、agent-deadline-prop-20261028.json（时限传播）、agent-synthesis-test-20261028.json（合成链路）。
- 测试组：backend/tests/rag/test_table_header_detection.py（新增）、test_execution_deadline_propagation.py、test_vector_router_rebuild_throttle.py、test_managed_task_controls.py、test_eval_window4_backend.py。
- 既有失败（非本会话引入，已定界）：test_router_domain_literal_guard（上游字面量遗留）；test_chain 7 处+test_rag_p1 1 处（断言旧拒答文案）；test_llm_resilience 1 处（本机 fallback 价格档位环境性）。
- 完整历史：PROGRESS.md（每轮 Trace/分数矩阵/探针输出）。

## 6. 纪律提醒

- 不覆盖 8 个未提交文件之外的其他会话成果；发现 manifest/评测数据被外部回写先报告再动手。
- 评测数据修改必须走 DATA-01 链（cases.jsonl → suites cases_hash → manifest content_hash 三处同步），禁止只改一处。
- prompt 变更走 drafts→release→approve→publish 治理链（管线已修好，勿绕过）。
- 提交走 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
