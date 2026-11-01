# Prometheus / Grafana 开发与看板验收规范

> **两套职责**：Prometheus 负责采集、聚合、规则和告警；Grafana 负责展示和业务排障入口；Trace/PG 留存逐请求证据。看板不等同于完整业务状态数据库。

## 1. 事实源和配置

| 环节 | 事实源 |
| --- | --- |
| Python 指标定义与埋点 | `backend/observability/metrics.py`、对应业务模块 |
| Prometheus 抓取和记录/告警规则 | `docker/prometheus.yml`、`docker/prometheus-alert-rules.yml` |
| 告警规则触发验证（promtool unit test，可复现） | `docker/prometheus-alert-rules.test.yml` |
| Dashboard Git 资产 | `docker/grafana/provisioning/dashboards/` |
| 自动生成工具 | `docker/grafana/gen_dashboards.py` |
| Grafana 连接/Provisioning | `docker/grafana/provisioning/datasources/`、`dashboards.yml` |
| 产品级 SLO | [slo.md](slo.md) |
| 逐请求排查与权限 | [trace-model.md](trace-model.md) |

当前目录内存在 `agent-01-platform-overview.json` 至 `agent-07-infra-async.json` 七类 Dashboard 资产；`gen_dashboards.py` 的历史头注释仍称“生成 4 个”，**不视作当前数量事实**。修改生成器或手动 JSON 前确认实际生成模式、导出 diff 和自动装配方式，禁止生成脚本覆盖另一批已维护的看板。

## 2. 看板信息架构

| 业务主题 | 关键问题 | 推荐指标形态 |
| --- | --- | --- |
| Platform / API | 系统可用吗？哪些入口失败？ | 请求数、错误率、P50/P95/P99、SSE 终态 |
| Router / Runtime | 请求去了哪里？在哪个节点慢或中断？ | 决策来源、路由兜底率、节点时延、超时 |
| Tool / Provider | 哪个能力、供应商失败？是降级还是 BLOCKED？ | 调用率、timeout、rate limit、fallback、熔断 |
| RAG / SQL / Domain | 用户目标完成率、可引用/拒答质量如何？ | 有效检索、拒答、SQL 安全阻断、领域业务结果 |
| Model Cost / Context | token 与人民币成本来自哪里？预算是否失控？ | 调用量、token、费用、estimated、额度阻断、压缩 |
| Infra / Async | 数据库、Redis、Celery 和告警是否健康？ | 队列长度/年龄、任务重试/死信、连接和饱和度 |

**每个 Panel 都必须追得回来源**：使用已经在 `/metrics` 或实际 Recording Rule 中存在的指标名/标签；没有底层埋点先建立指标与测试再画看板。SLO 阈值定义和告警 PromQL 必须一致，不允许“随便设一个绿色阈值”。

## 3. 数据可信性和权限

- **No data ≠ 0**：没有抓取、服务/label 未出现、查询错误和真实零值需要区分；跨服务计数器重置不能当流量下降。
- **PromQL**：分母为零、采样窗口过短、缺标签、histogram bucket 边界、counter 与 gauge 语义都必须检查；单位、时间范围、时区、图例与统计周期注明。
- **高基数与敏感数据**：不放 user_id/tenant_id/trace_id 等进 Prometheus label；从看板跳 Trace/成本明细必须走受控 API 并做租户与资源级授权。
- **指标链路**：埋点 → `/metrics` → target up → Prometheus 时序 → panel query → Grafana 渲染 → 告警；任一段失败，不能因页面能打开就算“通过”。
- 告警 webhook/通知渠道未配置时单独 WARN/BLOCKED，不把“规则已加载”当作“收到通知”。

## 4. 修改流程及验收

1. 定位产品问题，核对已有指标及标签；明确 Label 基数与 PII 风险。
2. 新增/复用指标前写定向回归：成功、失败、降级和错误恢复是否计数准确。
3. 设计查询表达式和单位；在 Prometheus API 对真实时序运行；检查空数据、counter reset 和不同窗口。
4. Git 中提交 source/export/JSON，确认 provisioning 可装配且 Dashboard UID 稳定；不单纯在 UI 手动改完便结束。
5. 按业务入口模拟成功/失败/限流/预算阻断；在 Grafana 实际看板与 Trace 对应验证数字一致。
6. 告警规则执行配置校验、真实触发与恢复、通知出口审查；提供截图/Prometheus 查询/配置 SHA 证据。

| 编号 | 验收 | 预期 |
| --- | --- | --- |
| OB-01 | Prometheus targets 和 scrape | 必需 target 为 up，有真实样本 |
| OB-02 | Panel PromQL 与原始指标 | 查询有效，语义与单位正确 |
| OB-03 | 7 类 Dashboard（按当前部署配置核定） | 配置 UID 不冲突，页面渲染，无无效数据源 |
| OB-04 | 成功、失败、降级、预算拒绝注入 | 面板能区别，Trace/明细可下钻 |
| OB-05 | No data、计数器重置、低流量窗口 | 不将缺失值误报 0 / 100% |
| OB-06 | 规则加载、实际触发、恢复与通知 | 真正送出（无 webhook 时标 WARN） |
| OB-07 | 用户角色与敏感数据 | 租户/身份正确，图例无 PII |
| OB-08 | 交付/回滚 | Git 中有稳定配置、查询与版本，改动可回退 |

验证数据是环境依赖项。仅有 JSON 静态校验不代表 Grafana UI、Prometheus 实际数据或告警通知已经通过；运行验收需要本地或 CI 可用的对应服务。
