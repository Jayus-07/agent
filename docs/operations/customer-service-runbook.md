# 客服域运行手册

> 范围：客服域（cs_graph_node）问答主链的**运行期**操作手册——已知风险处置、
> FAQ 双轨运维节奏、告警响应、缺口闭环流程。当前域边界见
> [客服域边界](../domains/customer-service.md)；未完成部署验收见
> [客服派单验收进度](../tasks/cs-dispatch-rollout-acceptance/PROGRESS.md)。
> 平台级启停/网关/部署见 [常用命令](commands.md)。

## 一、链路与依赖速览

```
用户消息(domain_hint=cs) → CS 域图
  → 知识问答: FAQ 精准层(ai.cs_faq, agent_memory PG 5433) ──未命中──→ RAG 链(rag-service)
  → 其他子 Agent（query/action/complaint）
```

FAQ 层依赖：`ai.cs_faq` + `ai.cs_faq_query_log`（agent_memory 库 ai schema）。
RAG 链依赖：rag-service（pgvector + BM25 + EvidenceGate）。

## 二、已知运行风险与处置（上线 Runbook 四条）

### 1. FAQ 层 PG 故障 → 自动降级 RAG（fail-open，不阻断）

- **现象**：app 日志 `[CSKnowledge] FAQ 层故障（降级走 RAG 链）`；TTFT 回到秒级
  （FAQ 命中本来是 <100ms 直答）。
- **观测**：告警 `CsFaqLayerFailureSpike`（10 分钟 >5 次）；
  指标 `cs_faq_layer_failures_total`（app 进程 /metrics）。
- **处置**：查 agent_memory PG 连通性（`docker ps | grep postgres`、
  app 容器内 `python -c "from backend.customer_service.faq import get_faq_store; print(get_faq_store().stats())"`）。
  FAQ 层故障**不需要停问答服务**——用户仍经 RAG 链得到回答。

### 2. 过期知识过滤的 60s 进程内缓存窗口

- **口径**：文档设 `valid_until`/deprecated 后，rag-service 的过期过滤集最长
  **60s** 后才生效（进程内缓存 TTL）。
- **管理面例外**：`/retrieve_docs`（文档管理接口）**不做**状态过滤，会返回
  已过期/已废弃文档——这是管理口径（管理员需要看到全量），不是缺陷；
  对客问答面（`/retrieve`、知识问答链）才做过滤。
- **处置**：紧急下线某文档后若 60s 内仍有命中告警，属缓存窗口内正常现象；
  超过 60s 仍命中再排查（`docker exec agent-rag-service-1 grep -c list_expired /app/backend/rag/retrieval/retrievers.py` 确认部署版本）。

### 3. app 冷启动 ~1 分钟内首批 FAQ 查询可能偶发降级

- **现象**：容器重启/发布后约 1 分钟内，首批 FAQ 查询偶发走 RAG 链
  （依赖池未热）。实测复现一次，热后恢复。
- **处置**：发布后先 `curl -s http://localhost:8000/health`，再发一两条
  热身问题（如「退款多久能到账」）确认 `faq_hit=True` 再放开流量。
  该现象由 `CsFaqLayerFailureSpike` 兜底可见，偶发 1-2 次不算事故。

### 4. C9 gate 阈值偏严（已知待优化，非事故）

- **口径**：rerank 面生产默认阈值假拒 24%/漏拒 13%，网格无达标点，
  生产阈值**冻结**（2026-10-04 实测定案）。
- **影响**：拒答率**不会**随索引修复线性下降——看到拒答率高于预期，
  先查 `gate_calibration_report_rerank.json` 对比基线，不要盲目调 EvidenceGate。
- **任何阈值/reranker 变更必须过决策门 + PR 门禁**，禁止线上直接改。

## 三、FAQ 双轨运维节奏

### 周聚合（已进质检日报）

质检日报任务 `cs.qa_daily_report`（每日 06:10 UTC，beat `cs-qa-daily-report`）
的 `metrics.faq` 段现在包含（滚动 7 天）：

- `published`：FAQ published 条目数（gauge `cs_qa_daily_faq_published`）
- `queries_7d` / `hits_7d` / `hit_ratio_7d`：承接占比（gauge `cs_qa_daily_faq_hit_ratio`）
- `avg_hit_latency_ms`：命中时延均值（口径 ≤500ms）
- `top_miss`：近 7 天未命中问题 Top10（缺口闭环输入，见下）

### 告警（docker/prometheus-alert-rules.yml，agent-platform-cs-faq 组）

| 告警 | 触发 | 响应 |
|---|---|---|
| `CsFaqHitRatioLow` | 承接占比 <50% 持续 1h | 跑缺口周检（下节）补条目/变体；或评估匹配阈值（改阈值=行为变更，须复测+评审） |
| `CsFaqLayerFailureSpike` | FAQ 层故障 10 分钟 >5 次 | 按风险 #1 处置 PG 连通性 |

### 缺口闭环周检（C6）

```bash
# 输出近 7 天缺口清单（控制台 + 可选 JSON 落盘）
PGPORT=5433 D:/Python/python.exe -m backend.scripts.cs_faq_gap_review
PGPORT=5433 D:/Python/python.exe -m backend.scripts.cs_faq_gap_review --out d:/tmp/cs_gap.json
```

流程（每问）：① 判定 KB 是否有答案（查 chunk_store/doc_registry）→
② 有 → `upsert_faq` 补条目或在既有条目 `variants` 补变体（kb_refs 指回源 doc_id）→
③ **复测必须命中**（fail-closed 纪律：补完不命中等于没补）→
④ KB 无答案 → 登记待补知识清单，不硬造答案。

Celery beat 挂载（maintenance 队列）**待混线解锁**：`backend/tasks/celery_app.py`
与 `queue_router.py` 当前被其他未提交线占用，任务名 `cs.faq_gap_review` 登记
落库前以手动/管理端触发为准（提交信息与本节已显式标注）。

## 四、FAQ / 词表内容变更纪律

- FAQ 任何内容变更（新条目/变体/下线）必须**复测命中**后才能发布；
- 词表（vocab）变更必须过 `vocab_gate`（fail-closed，坏变更拒绝落盘）；
- 两者都是行为面：批量变更走 PR，禁止容器内手工 DML `ai.cs_faq`
  （手工 DML 不会失效进程内匹配索引，且绕过复测）。

## 五、回滚

- FAQ 双轨整体回滚需按部署迁移方案撤销新增表并回退对应代码；操作前核对目标数据库与当前迁移状态。FAQ 层关闭后问答自动全量走 RAG 链。
- 词表与告警规则回退前核对当前提交及 `docker/prometheus-alert-rules.yml`，按版本控制记录恢复。

## 六、关联文档

- 客服域边界：[domains/customer-service.md](../domains/customer-service.md)
- 当前部署验收：[tasks/cs-dispatch-rollout-acceptance/TASK.md](../tasks/cs-dispatch-rollout-acceptance/TASK.md)
- C9 实验台：`python -m backend.scripts.rag_gate_lab report --face rerank`
- 平台级运维：[operations/commands.md](commands.md)
