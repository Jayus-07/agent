# ZCode → 下一会话交接：发布门禁已 100%，剩余专项（2026-10-29）

上一会话完成发布门禁 62.5%→**100%（24/24）**并发布 rag.qa v17 + self_correction v2，P95 压测与 browser-use 验收通过。本文是当前完整状态与剩余事项。

## 1. 工作树与 Git 状态

```text
工作树：C:\Users\wh\.codex\worktrees\assistant-roundtable-sse\agent
分支：codex/enterprise-assistant-reliability-fixes（HEAD=本文件提交）
本地 main：D:\Program Files\workplace\agent（已合并全部成果，未推远端）
main 工作区 tmp/ 删除为其他会话残留，未代处理
```

本轮提交（均已合并 main）：d07c410 manifest hash 链、8a97a63 题面纠正、0095621 RRF 加成基准、306d1b1 发布评测模板注入、3bc6811 命名文件收口、aaaa9a5 approved_at、4ed59f9/f5a93de 及末笔 PROGRESS 证据。

## 2. 已完成并验证（本轮）

1. **发布门禁 100%**：rag.qa v17（多来源冲突指引+答所问+指定来源单行示例）+ 修题（UFQ-018/019/021 题面对齐标注）+ 数据链 candidate.4（hash 647a）→ rel-69fd1f686fe04057a025c4d89359cb4c **24/24**（run=2026-10-28T20-15-08-922342）→ approve→publish，六进程快照 rag.qa=17。
2. **self_correction v2**：旧 rel-191e954a 实为 failed（模板注入缺陷受害轮），重评 **24/24** → publish，六进程 self_correction=2。
3. **三大根因修复**：①供应商 temp=0 非确定性（001/004 同 sha 不同答案实锤）→ v16/v17 提示词把作答盆地做大；②RRF 标识符加成在 dense 3.0 权重下刀刃截断（023）→ 加成基准=最强单路第 1 名等效分；③**发布门禁从未评测过候选模板**（bind_prompt_versions 对草稿静默跳过、生成回落已发布版）→ EvalConfig.prompt_templates + bind_prompt_entries 显式注入。④ 查询点名来源文件时融合后只保留该文件候选（RAG_QUERY_FILE_SCOPE 可回滚）——枚举在物理上不可能。⑤ approve 被 ck 约束打 500 → _transition 补写 approved_at/published_at。
4. **P95/小并发**：串行 12 题×5 轮 + 独立会话 3 并发×5 轮全类达标（首反馈 P95≤0.32s；rag 热 P95 总 8.0s/并发 5.4s；sql 2.7/5.5s；web 3.2s），done 率 1.0。
5. **browser-use 验收 PASS**：3100 真实注册→企业助手页面支付问全链路正确（答案+FAQ 出处 0.92），截图 D:/tmp/agent-v17-browser-acceptance-20261029.png。测试账号 13800002029/Aa12345678。
6. **计费复查**：归属完整（eval→tenant=eval、线上→user），9 行无归属=发布预热调用。

## 3. 剩余事项（下一会话从这里做）

1. **发货/发票措辞专项（新发现问题，既有非回归）**：①「客户下单后多久发货？」被领域判断归 general/product_scope 直通 general_chat（trace b15c7a898351）；②带「企业知识库」前缀的进 rag.search 后 rerank 阈值 0.3 截掉 FAQ 块（自纠错改写二检 0 文档，trace d904053b1459）。同措辞自 10-27 19:17 起持续失败。修向：产品域分流的领域判定 + rerank 措辞泛化（multiquery 或阈值策略）。支付问已 PASS 可作对照。
2. **供应商冷启动韧性**：压测首轮 general_chat 122.5s（fa64e192291a，内部重试特征），复测未复现；需供应商超时/重试预算专项。
3. **评测噪声清理**：[EvalGen:capture]/[FusionDebug]/[RerankDebug] 诊断日志（EVAL_TRIGGER 门控）在观测完成后可降级/移除；001/004 供应商非确定性若再抖，按预案题集去重+judge_completeness 入门禁。
4. 阶段 6-8 中仅剩：发货/发票修复复测（属事项 1）、及用户既定的持续验收循环。
5. 环境陷阱沿用：rebuild 后必须核验容器内 DATA-01 链（镜像已烘焙 candidate.4 正确数据，root 手改手段已退役）；devctl.bat 在本机 cmd.exe 通道失效，用 docker compose 直调（COMPOSE_FILE='docker-compose.yml;部署/prod-4c8g/docker-compose.local.yml' COMPOSE_PATH_SEPARATOR=';'）。

## 4. 关键命令与证据

```powershell
# 发布评测（模板注入已修复，草稿版本会被真实评测）
# POST http://127.0.0.1:8000/prompts/{key}/versions/{n}/release
#   {"suite":"desktop_upload_full","evaluation_mode":"semantic","executor":"local","target_env":"production"}
# 认证：X-API-Key（worktree .env API_KEY）；trace 查询走 3200 BFF + JWT
# 评测报告：容器 /app/data/eval_runs/<latest>/per_case/*.json
```

- P95 数据：D:/tmp/agent-p95-battery-20261029.json、agent-p95-concurrent-20261029.json
- 复测：D:/tmp/agent-v17-retest*.json、agent-v17-traces.json、agent-v17-payment.json
- 完整历史：docs/tasks/enterprise-assistant-roundtable-sse/PROGRESS.md（2026-10-29 各节）

## 5. 纪律提醒

- 提交走 `git add -A -- <paths>` + `git commit -m "..." -- <paths>` 双重路径限定。
- 评测数据改动必须走 DATA-01 链（cases.jsonl → suites cases_hash → manifest content_hash 三处同步）。
- prompt 变更走 drafts→release（真实评测候选版）→approve→publish 治理链。
- 阶段可交付先提交合并本地 main（不推远端不发布生产）。
