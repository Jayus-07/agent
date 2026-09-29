# Platform Readiness STOP F — Performance / SLO / Observability Baseline

> 日期：2026-09-24 ｜ 工具：`backend/scripts/e2e_platform_benchmark.py`（真实网关采样，幂等可重跑）
> 口径：**BASELINE（基线），非 SLO 承诺**；样本量 6 域 × 4 次（本地资源合理规模）

---

## 1. Initial Production Baseline（经 APISIX 真实链路实测）

| 域 | ok/样本 | total p50 | total p95 | TTFT p50 | 备注 |
|---|---|---|---|---|---|
| General | 4/4 | ~9s | ~10s | 8.5s | 单轮 LLM 直答 |
| RAG | 4/4 | 4.0s | 8.4s | 4.0s | clarify 兜底路径偏快、检索路径居中 |
| SQL | 4/4 | 0.8s | 0.9s | 0.8s | **快路径=workflow 权限拒绝快速失败**（行级 scope 生效）；全量报表成功路径参考 task 均值 |
| CS | 4/4 | 6.7s | 14.9s | 6.7s | 域图+专家链 |
| Travel | 4/4 | 0.7s | 1.2s | 0.7s | 快路径=补槽/追问轮；全量出单 13-31s（Domain Runtime STOP C 实测） |
| Selection | 4/4 | 0.6s | 0.6s | 0.6s | 快路径=建池空淘汰轮 |

- **Task 侧**：近 2 日 SUCCESS 任务平均时长 **147.8s**（tasks 权威库统计）。
- **Token/Cost 计量**：done.usage 结构在本次采样脚本中未对齐提取口径（脚本按 models/by_model 猜测），**token/cost 权威在 usage store 与管理端 LLM 用量页**（llm_usage 表 17k+ 行持续累积）——脚本口径登记 P2-21，不影响平台计量本身。

## 2. Context Budget 观察（F5，只读，零调参）

`context_compactions_total{level,action}`、`context_tokens_saved_total`、`context_budget_overflow_total` 指标族在册（E14 抓取核验）；本轮采样为短会话，L2/L5 触发未进入稳态窗口——**触发基线以管理端指标页为准**（既有约束测试族覆盖触发逻辑）。

## 3. Model（F6）

role→provider 解析=DB registry 快照（启动日志可证：3 厂商/7 模型/9 凭据/7 角色覆盖）；fallback 计数=llm_fallback_total；成本=llm_usage 单价快照（046 迁移）。

## 4. Error Rate（F7）

chat_request_total{status}（本轮 ok=27→28 增长）、llm_failures_total、task_terminal_total{status}（worker 侧）、sql_agent_denied_total、degradation_alerts_total——各族在册（E14/G12 核验）。

## 5. Resource（F8）

容器内存限额在册（app/rag 4g、worker 3-4g、pg 2g、redis 768m）；PG 连接池 min2/max10；worker 并发 4/2/1/1/2（compose 声明）。不展开 capacity planning。

## 6. Observability Persistence（F9）

Trace 权威=app 容器内 SQLite（P2-10：PG 镜像 0 行）。多实例/滚动发布下的查询面=容器卷（单实例语义）；**判定**：单机部署现状可接受（trace 经 trace_id 在本容器可查；gateway 访问日志另入 PG gateway_access_logs 17k 行持久留存）；多实例化前需统一 exporter——登记 P2-22，不在本轮重写。

## 7. Prometheus（F10）

- 抓取目标 **10 个 up**（app/apisix/redis×2/postgres/celery/task-workers×4(+metadata-shadow 88e917d 补齐)/…）。
- **multiproc ghost**：app /metrics 检出 237 个重复 TYPE 行——与 phase2-g 已知 multiproc 聚合残留同源（9a4c030 修复过静默丢失；残留重复行不影响 Prometheus 抓取与查询语义，prom target up 实证），登记 P2-23 持续观察。
- Prometheus 配置文件已修（P2-13 修复提交 88e917d）。

## 8. Alert（F11）

现有 alertmanager+规则文件在册（prometheus-alert-rules.yml）；建议基于在册指标启用：LLM failure 率（llm_failures_total 增速）、task recovery 失败（task_recovery_total{result}）、context overflow（context_budget_overflow_total）、DB/Redis down（exporter up==0）。**只建议不代建**——告警路由属运营配置。

```text
STOP_F_PASS    = true
STOP_G_ALLOWED = true
```
