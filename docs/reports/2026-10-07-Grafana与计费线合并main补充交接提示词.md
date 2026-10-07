# 交接提示词：Grafana 观测线 + 计费线 → main 合并会话（补充篇）

> 交接时间：2026-10-07 ｜ 交接人：Grafana 观测重构会话 ｜ 数字均为实查
> **定位**：本文是 `docs/reports/2026-10-07-分支合并main交接提示词.md`（主篇，失败语义线交接）的**补充**——门禁命令、合并流程、纪律红线以主篇为准，本文只补两件事：①观测重构线合并相关状态与验证；②计费线原分配给 Grafana 会话、实际未做的挂账（合并前处理最合适）。

---

## 一、观测重构线合并状态卡

**三个提交（全部在 `feat/model-billing-closure` 分支上）**：

| 提交 | 内容 |
|---|---|
| `b88a557` | 7 张 Dashboard 替换旧 5 张（179 Panel）、3 个新指标、路由 6 死指标复活、rag-server /metrics 补链、告警 55→60、PM 4 个新抓取 job |
| `7ae4505` | 补验三缺口闭合：classifier→embedding 层归并修复、L5 失败路径受控测试、UI 恢复实测 |
| `bf003f9` | UI 走查四缺陷修复：hit_rate 聚合、双逗号非法 PromQL、07 页布局重叠、**Grafana 容器内存 512m→1g** |

验收报告：`docs/reports/2026-10-06-Grafana企业级观测重构验收报告.md`（含补验记录章节）、审计 `2026-10-06-Grafana观测体系重构前审计.md`。终态 OBSERVABILITY_DASHBOARD_PRODUCTION_READY=true，199 条 PromQL INVALID=0，浏览器 7/7 走查 + 8 项数值双向对照一致（`bf003f9` 后复验）。

**合并涉及的文件面**（PR 描述与冲突预判用）：
- `docker/grafana/provisioning/dashboards/`（删 5 旧 JSON 增 7 新）+ `datasources/datasource.yml`（仅注释）
- `docker/prometheus.yml`（+4 job：rag-service/alertmanager/loki/tempo）、`docker/prometheus-alert-rules.yml`（+5 告警）
- `backend/observability/metrics.py`、`backend/orchestration/router/engine.py`、`backend/rag/chain.py`、`backend/rag/reranker.py`、`backend/travel/graph_builder.py`、`backend/services/rag_server.py`、`docker-compose.yml`（仅 grafana limits）
- ⚠️ 冲突高发预警：`rag/chain.py` 与 main 的 eval 线有交叠（主篇 §三已预警）——我的改动只在 `_timed_stuff`/`ask()`/`compress_documents` 加了 `record_rag_stage` 埋点共 5 挂点，与 eval 线降级语义无冲突，解冲突时**保两边**即可；`metrics.py` 与并行线 `travel_tool_degraded_total`（自洽死定义，其接线在其线）已同文件共存，勿删。
- 受控测试/演练脚本（已提交）：`backend/scripts/obsrecon_e2e*.py`、`obsrecon_faultinject.py`、`obsrecon_ui_walk.mjs`；回归测试 `backend/tests/observability/test_stage_metrics.py`（6 条）。

## 二、计费线转交挂账（原分配给 Grafana 会话，未做——**建议合并 PR 前做掉，随 PR 进 main**）

来源：`0a340d0`（计费线交接词 `2026-10-07-计费收口交接Grafana会话提示词.md`）。该交接词的任务 1 本计划由 Grafana 会话随提交带上，实际未做（实测：AGENTS.md 零处计费口径、DATABASE.md 079 未写入 llm_usage 段）。**转交合并会话执行**，要点照抄计费线原文：

1. **`docs/DATABASE.md`**：llm_usage 表补 migration 079——新列（billing_schema_version/native_cost/native_currency/billed_cost_cny/fx_rate/price_version/pricing_source/usage_source/分项 cost 与单价）、旧列语义（cost_usd=原生 USD 审计口径 deprecated；currency=供应商报价币种；total_cost=记账 CNY）、历史规则（CNY 回填 26975 行升 V2；USD/空币种 1078 行保 legacy）。
2. **`AGENTS.md`**：预算/治理平面附近补 2~4 行：唯一入口 `backend/infra/llm/pricing.py::price_usage()`、BillingResult 契约、migration 079、`llm_usage_store_pg.py::cost_cny_sql_expr()` 唯一折算出口、红线「禁止任何一层重新算钱或二次换汇」。
3. **`README.md`**：迁移总数若记录则 +1 到 079；无数量变化则只更新「最后验证」日期。
4. **`docs/API.md`**：顺手核对 trace DTO/done 帧 `cost_cny` 字段是否需补说明（没记录就跳过）。
5. **P1-08 可选三面板**（拍板后做，不做就在 PR 描述里注明挂账）：unpriced 数（需在 cost_gauge.py 旁加投影 gauge 或明确不补）/ price_unknown（`budget_request_total{result="price_unknown"}` 已有指标直接加面板）/ needs_review ratio。红线：不造高基数 label。
6. **明确不做**：不改 `pricing.py/proxy.py/quota.py/llm_usage_store_pg.py`（有 12 条黄金契约测试守护）；legacy USD 1078 行回收归计费线拍板。

## 三、合并后观测专项验证清单（部署/重建时用）

1. **Grafana**：重启后 7 张自动 provisioning 恢复（`/api/search` 计数=7）；**红线：勿给存量 Prometheus 数据源补 uid**——实测会让 provisioning 报 data source not found 卡死 Grafana 启动，Dashboard JSON 引用的是自动生成的确定性 uid `PBFA97CFB590B2093`；UI 手改 40s 内被 provisioning 自动恢复属正常。
2. **Prometheus**：`/api/v1/rules` 应 60 alerts + 6 recording；`/api/v1/targets` 16 目标全 up（含新 job rag-service/alertmanager/loki/tempo）。
3. **rag-service**：`curl http://127.0.0.1:8090/metrics` 应有 `rag_query_total`/`rag_stage_duration_seconds`（remote 模式 RAG 指标唯一归属进程）；改共享代码后 **app 与 rag-service 是同 Dockerfile 不同镜像 tag，要分别 build+up**。
4. **rebuild 三镜像源 build-arg**（缺省必挂 apt/pip）：`--build-arg DEBIAN_MIRROR=mirrors.aliyun.com --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ --build-arg EXTRA_INDEX_URL=https://mirrors.aliyun.com/pytorch-wheels/cpu/`（与主篇 §五一致，worker 族清单见主篇）。
5. **Grafana 容器内存**：compose 已调 1g（实测 512m 时 493MiB/96.5% 会导致 HTTP 挂起、health 超时但进程活着）——「Grafana 打不开」先 `docker stats` 看内存占比。
6. 快速冒烟：`cd backend && D:/Python/python.exe -m pytest tests/observability/test_stage_metrics.py tests/observability/test_cost_gauge.py -q --no-cov`；实机看 `http://localhost:3001/d/agent-01-platform-overview`（Grafana admin 密码为卷内历史值，勿重置勿入库）。

## 四、验收输出要求

按仓库七行证据格式回复，并明确区分：真实已有样本 vs 验收注入样本（受控流量 session_id 前缀 `obsrecon-test`，账号 uitest_user）。若执行了 §二文档同步，`grep -rn "price_usage" AGENTS.md` 与 `grep -n "079" docs/DATABASE.md` 留痕。
