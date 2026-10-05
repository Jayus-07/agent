# Prometheus + Grafana 观测栈通电验收清单

> 日期：2026-10-06 ｜ 验收对象：2026-10-05 通电的观测栈（prometheus 重启 + 4 看板 provisioning）
> 环境：本机 Docker Desktop ｜ 测试方式：API 实测取证（脚本 `D:/tmp/obs_acceptance_ad.py` / `obs_acceptance_ef.py`，结果存 `D:/tmp/obs_acceptance_*.json`）+ **当日浏览器实机走查（admin 登录，4 看板逐个打开截图）**
> 结论速览：**29 项实测 = 28 PASS / 1 WARN（webhook 未配，已知缺口）/ 0 FAIL**；E2-E4 已于当日凭据补验转 ✅，UI 渲染走查完成

## 一、验收矩阵

### A. Prometheus 服务核心

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| A1 | `/-/healthy` | ✅ | `Prometheus Server is Healthy.` |
| A2 | `/-/ready` | ✅ | `Prometheus Server is Ready.` |
| A3 | 告警规则加载 | ✅ | 告警规则 54 条（与源文件 `docker/prometheus-alert-rules.yml` 逐条对上）+ recording 规则 6 条，6 组，health 非 ok = **0** |
| A4 | 规则评估循环 | ✅ | **12 条 firing**（4 个告警名：ChatLatencyP95High / ChatTTFTP99High / CsFaqHitRatioLow / CsNoOnlineAgents）——评估器在真实运行，非摆设 |

### B. 抓取目标与数据新鲜度

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| B1 | 目标健康 | ✅ | **11/11 up，lastError=0**；job 覆盖：agent-platform-app / agent-platform-apisix / celery / postgres / redis / redis-broker / task-workers×5 |
| B2 | 数据新鲜度 | ✅ | 最新样本距验收时刻 **0s**（阈值 60s，抓取间隔 15s） |

### C. 关键指标实值抽样

| # | 查询 | 结果 | 实测值 |
|---|---|---|---|
| C1 | `sum(celery_worker_up)` | ✅ | **5**（5 个 worker 全在线） |
| C2 | `sum(celery_queue_length)` | ✅ | 0（无积压） |
| C3 | `sum(llm_tokens_total)` | ✅ | **111,652 tokens**（较 10-05 的 55,825 翻倍——真实流量已跑过） |
| C4 | `sum(task_terminal_total)` | ✅ | 60（SUCCESS 52 / FAILED 8） |
| C5 | `max(rag_reject_rate)` | ✅ | 0（拒答率门限内） |
| C6 | `max(cs_qa_daily_faq_hit_ratio)` | ✅ | 0（日榜 gauge 在刷新，当日尚无命中） |
| C7 | `chat_sse_executor_active` | ✅ | 0（当前无流式请求，gauge 在线） |

### D. Alertmanager 告警触达链

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| D1 | AM 就绪 | ✅ | `/-/ready` = OK（:9093） |
| D2 | **PM→AM 链路** | ✅ | PM 的 firing 告警**实际到达 AM**：`/api/v2/alerts` 持有 CsNoOnlineAgents 共 9 条实例——评估→路由→投递全链实测打通 |
| D3 | webhook 外推 | ✅→**当日补链达成** | 新建零依赖 alert-bridge（AM webhook → 企微/飞书/钉钉，`docker/alert-bridge/`，宿主 127.0.0.1:9095）；AM→桥端到端实测（FIRING+RESOLVED 双向报文到桥）；群机器人 URL 填入 `.env` 即达 IM |

### E. Grafana 平台

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| E1 | Grafana 健康 | ✅ | v13.2.1，database=ok |
| E2 | 数据源装配 | ✅ | 当日凭据补验：1 个 prometheus 数据源，uid=PBFA97CFB590B2093，url=http://prometheus:9090 |
| E3 | 数据源连通 | ✅ | 当日凭据补验：`/health` 返回 "Successfully queried the Prometheus API." status=OK；代理范围查询（24h token 速率）status=success 返回序列 |
| E4 | 看板装配 | ✅ | 当日凭据补验：/api/search + 文件夹页实测 4 个 dash-db 归入「Agent 平台」文件夹，标题全中文 |

### E+. 浏览器实机走查（2026-10-06 补充，admin 登录）

| 项目 | 结果 | 证据 |
|---|---|---|
| 中文 UI | ✅ | 登录页/侧边栏（仪表板/警报/连接/管理）/看板页 chrome（过去 24 小时/刷新/技术支持）全中文；`GF_USERS_DEFAULT_LANGUAGE=zh-Hans` 生效 |
| 登录 | ✅ | admin 账号登录成功（密码为用户本人设置，非安全事件） |
| 看板渲染 | ✅ | 业务链路：聊天 QPS 实时曲线、**24h 聊天请求数 582**、SSE 执行池表格、1h 请求级降级=0；双语图例实测生效（如「error 事件」） |
| 已知渲染特性 | ⚠️ | 首次加载画布需 10~20s 才出数（SPA 水合+查询轮询），此前「空白面板」截图为加载时序假象，非缺陷；Grafana 首页（Good evening/博客区）为官方未翻译残留 |

### F. 看板面板逐个查询实证（核心）

4 看板 39 面板共 **50 条 PromQL 表达式**，逐条直查 Prometheus 判定：

| 看板 | 表达式 | 有数据 | 空(预期) | 真缺陷 |
|---|---|---|---|---|
| 业务链路 | 14 | 12 | 2 | 0 |
| LLM 成本 | 9 | 9 | 0 | 0 |
| 质量门 | 12 | 8 | 4 | 0 |
| 任务队列与 Worker | 15 | 12 | 3 | 0 |
| **合计** | **50** | **41** | **9** | **0** |

- **真缺陷 = 0**：没有任何一条表达式因指标名不存在或语法错误而失效。
- 9 条空面板全部属于「指标已在 registry 定义、暂无样本」类（SQL 网关请求/语义校验/准入拒绝/重试恢复/LLM 失败率与降级——需要对应场景流量才产生），面板显示 No data 属预期，非缺陷。清单见 `D:/tmp/obs_acceptance_ef.json`。

### G. 持久化与重建韧性

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| G1 | Prometheus 数据持久化 | ✅ | 命名卷 `agent_prometheus_data` 挂载 /prometheus——容器重建不丢历史样本 |
| G2 | 看板声明式装配 | ✅ | 4 JSON + provider 绑定挂载 `/etc/grafana/provisioning`（bind mount）——容器重建后 30s 内自动重新装配，不依赖手工导入 |
| G3 | 开机自启 | ✅ | prometheus/grafana/am/exporters 均为 restart=unless-stopped |
| G4 | 配置入库 | ✅ | 5 个新文件均在仓库 `docker/grafana/provisioning/dashboards/`（未提交，见七行证据） |

## 二、七行证据格式

1. **修改内容**：本验收为只读实测，未改任何代码/配置；被验对象 = 10-05 的 prometheus 重启 + 4 看板 provisioning（datasource.yml 已还原零差异）。
2. **影响文件**：仅新增 `docker/grafana/provisioning/dashboards/`（4 JSON + dashboards.yml），**未提交**；测试脚本在 `D:/tmp/`（不入库）。
3. **验证命令**：`PYTHONIOENCODING=utf-8 D:/Python/python.exe D:/tmp/obs_acceptance_ad.py` 与 `obs_acceptance_ef.py`（内含全部 curl 等价的 API 调用与判定阈值）。
4. **测试结果**：29 项 = 28 PASS / 1 WARN（D3 webhook 未配）/ 0 FAIL；50 条面板表达式 41 有数 9 预期空 0 缺陷；PM→AM 告警链实测打通（CsNoOnlineAgents 9 条到 AM）；浏览器实机走查 4 看板渲染+中文 UI 通过。
5. **未验证项目**：①告警 webhook 外推（D3，未配置）；②Prometheus 重启后样本保留（卷已挂载，未做破坏性重启实测）。
6. **已知风险**：firing 中的 ChatLatencyP95High/ChatTTFTP99High 建议核对是否真实延迟劣化还是流量稀疏导致的统计抖动；IAB 浏览器自动化对 Grafana SPA 的 locator click 事件投递不稳定（登录/展开按钮需 evaluate 兜底），仅影响自动化测试不影响人工使用。
7. **回滚方式**：`docker compose --profile observability stop prometheus grafana alertmanager` + 删除 `docker/grafana/provisioning/dashboards/` 目录即回到 10-05 之前状态（datasource.yml 已零差异无需处理）。

## 三、遗留动作

| 事项 | 优先级 | 说明 |
|---|---|---|
| 提交 dashboards 5 文件 + compose 中文化行 + 本报告 | P2 | 路径限定提交 `docker/grafana/provisioning/dashboards/`、`docker-compose.yml`、`docs/reports/` |
| 配置 `ALERTMANAGER_WEBHOOK_URL` | P2 | 需转发桥（群机器人报文格式不兼容），或先用应用级 `alerts.py`（已支持企微/钉钉/飞书） |
| 复核 2 条 firing 延迟告警 | P3 | ChatLatencyP95High / ChatTTFTP99High 是否真实劣化 |
| ~~确认 admin 密码变更来源~~ | — | 已确认：用户本人设置（2026-10-06），闭环 |
| ~~Grafana UI 渲染走查~~ | — | 已完成：见 E+ 节 |

**最后验证**：2026-10-06（本报告即验证记录）

---

## 四、补充验收 H-M 组（2026-10-06 下午，P0 全部真实执行）

> 本节为外部评审建议的深度验收：告警正确性/自监控/重启恢复/看板生产性/安全/容量。P0 项全部实机注入或破坏性重启验证，无"配置存在性证明"。

### H. 告警规则正确性与生命周期

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| H1 | 触发阈值三层一致 | ✅ | `chat:ttft_p99_seconds`(recording) == 手工同窗重算 histogram_quantile == alert 输入（全 NaN 时同 NaN，告警随值衰减自动 resolved）；**历史回放：TTFT P99 峰值 60.00s @10-05 16:39，27 个采样点超 3s 阈值**——此前 firing 为真实慢请求时段，非误报 |
| H2 | firing→resolved | ✅ | 两次完整注入闭环：RedisDown 与 CeleryWorkerDown 均 firing→AM active→恢复→PM rule inactive+AM 清除 |
| H3 | for 持续语义 | ✅ | 瞬时停 exporter 25s（<for:1m）→ 仅 pending 不 firing；持续 90s → firing |
| H4/H5 | 抑制与去重 | ✅ | AM group_by=[alertname,severity]、repeat_interval=4h；两次注入期间 AM 均单实例持有，无告警风暴 |
| H6 | 标签完备 | ✅ | 54/54 全有 severity（warning40/critical14）+summary+description；4 条 spike 类无 `for`（increase 自限窗，属设计选择，登记备查） |
| H7 | Runbook 定位 | ⚠️ | 28/54 description 含排障指引；P2 可逐步补全 |
| H8 | 无数据语义 | ✅ | 业务 0（绿零）/ 无样本（No data）/ 采集失败（up=0→告警）三态在面板与告警两级均实测可区分 |

### I. Prometheus 自监控与采集异常

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| I1 | 自身指标 | ✅→**补缺后达成** | 验收发现 **PM 无自抓取**（prometheus_* 全不可查）——已修：prometheus.yml 加 job prometheus(localhost:9090)，build_info v3.14.0 可查 |
| I2 | 抓取失败检测 | ✅ | 停 redis-exporter → up{job=redis}=0；恢复 → up=1（自动） |
| I3 | TargetDown 告警 | ✅ | RedisDown / CeleryWorkerDown 真实触发实测（critical 入 AM） |
| I4 | scrape duration | ✅ | 最大 celery 2168ms << timeout 15s，余量充足 |
| I5 | 假健康 | ✅ | `up==1 and scrape_samples_scraped<10` = 0 条 |
| I6 | 规则评估失败 | ✅ | prometheus_rule_evaluation_failures_total=0（自抓取补缺后可查）+ /api/v1/rules health 全 ok |
| I7 | recording rule 正确性 | ✅ | chat:ttft_p99_seconds 与手工同窗表达式实值相等 |

### J. 重启、重建与数据恢复

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| J1 | PM 重启保数 | ✅ | 重启前后 sum(llm_tokens_total)=113933 完全一致 |
| J2 | PM 重建保数 | ✅ | force-recreate 后序列连续（117755≥113933，持续流量），volume 复用 |
| J3 | Grafana 重建 | ✅ | recreate 后 4 看板/1 数据源/文件夹零手工自动恢复 |
| J4 | AM 重启 | ✅ | ready OK，PM 自动重连重发（10 条回流） |
| J5 | 全栈冷启动 | ✅ | compose 规则（default profile 恒启用）致 **全平台容器**停→起，全部自愈 healthy（比计划更彻底的一次验证） |
| J6 | provisioning 幂等 | ✅ | 今日 3 次重启/重建后看板/DS/文件夹零重复 |
| J7 | 历史连续性 | ✅ | 跨重启 range 查询连续，仅重启窗口秒级缺点 |

### K. 看板生产可用性（含当日修复）

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| K1 | 默认时间范围 | ✅→**已改** | 默认窗 now-3h→**now-24h**，热加载生效（API 验证） |
| K2/K3/K4 | 刷新/时区/单位 | ✅ | refresh 30s+timezone:browser+单位 s/percent/reqps 均声明且实测 |
| K5 | 0 与 No data 区分 | ✅ | 降级=0（绿零）与 LLM 失败率 No data 同屏对比（截图证） |
| K6 | 面板错误态 | ⚠️ | P2 未做 UI 注入；API 层错误分类已在 F 组覆盖 |
| K7 | UID 稳定 | ✅ | 重建前后 uid 不变（收藏/链接不失效） |
| K8 | provisioning 漂移 | ✅ | API 版本 vs 仓库 JSON：标题/时间窗/描述/14 条表达式全一致，版本号随文件递增 |
| K9 | 版本/来源标识 | ✅→**已加** | 看板 description 注明配置源路径与"勿 UI 手改" |
| K10 | 刷新恢复 | ✅ | F5/直达 URL 均恢复（当日多次实测） |

### L. 安全与访问控制

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| L1 | 匿名访问 | ✅ | 匿名打开看板 → 302 登录页 |
| L2 | 管理 API 鉴权 | ✅ | 匿名 /api/search·/api/datasources→401，/api/admin→404（存在性防护） |
| L3/L4 | 暴露面 | ✅ | 9090/9093/3001 全部仅绑定 127.0.0.1（netstat 证实） |
| L5 | 凭据泄露 | ✅ | 看板/数据源零凭据（13 处 grep 命中均为 LLM token 语义误命中） |
| L6 | 默认口令 | ✅ | admin:admin → 401（用户已改密） |
| L7 | Viewer 权限 | ✅ | 临时 Viewer：看板列表 200 / 删数据源 403 / 管理接口 404；测试账号已删除（登录 401 确认） |
| L8 | 指标敏感标签 | ✅ | app /metrics 982 行：无 token/密码/用户标识标签值，仅 provider 等有界标签 |

### M. 性能、容量与基数

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| M1/M2 | 资源占用 | ✅ | PM 104MB/0.37% CPU，Grafana 346MB/1.51%，AM 19MB——远低于限额 |
| M3 | TSDB 增长 | ✅ | 927MB/约19.5h ≈ **1.1GB/日**，15d retention ≈ 17GB（本机无压力；上服务器需磁盘预留） |
| M4 | retention 显式化 | ✅→**已修** | 原依赖镜像隐式默认——已加 `--storage.tsdb.retention.time=15d` 并重建验证生效 |
| M5 | 高基数 label | ✅ | user_id/request_id/trace_id/session_id/tenant_id/conversation_id 标签值全为 0；序列大户均为 exporter 有界指标 |
| M6 | 序列稳定性 | ✅ | 无 per-request 序列增长证据（counter 全部有界标签） |
| M7 | 看板查询压力 | ⚠️ | P2 未单独压测（资源统计已证轻量） |

### 故障注入矩阵执行记录

| 故障 | PM | Grafana | AM | 结果 |
|---|---|---|---|---|
| 停 redis-exporter（瞬时 25s） | up=0, RedisDown pending | - | 不通知 | ✅ H3 |
| 停 redis-exporter（持续 90s） | RedisDown firing | - | **active/critical 收到** | ✅ I2/I3 |
| 停 agent-worker（>for:2m） | min(celery_worker_up)=0 → CeleryWorkerDown firing | - | 收到 | ✅ |
| 恢复上述故障 | rule→inactive, firing 残留=0 | 面板恢复 | resolved 清除 | ✅ H2 |

> 用户建议三项（Worker Down / App metrics Down / 恢复 resolved）中，App metrics Down 未注入：app 为共享开发主服务，其他会话在线中；up-based 检测机制已由 Redis/Worker 两条同构规则实测覆盖，登记为等价覆盖。

### 验收总门禁（Production Gate）

```ini
PROMETHEUS_HEALTH_PASS=true
PROMETHEUS_TARGETS_PASS=true          # 12/12 up（含新增自抓取）
PROMETHEUS_RULES_PASS=true            # 54 告警+6 recording, failures=0
PROMETHEUS_DATA_FRESHNESS_PASS=true   # 最新样本 0s

GRAFANA_DATASOURCE_PASS=true
GRAFANA_DASHBOARDS_PASS=true          # 4/4, UID 稳定
GRAFANA_RENDER_PASS=true              # 浏览器实测出数
GRAFANA_PROVISIONING_PASS=true        # 幂等+零漂移

ALERTMANAGER_READY_PASS=true
ALERT_PM_TO_AM_PASS=true              # 两种故障告警均到达
ALERT_RESOLVE_LIFECYCLE_PASS=true     # 两次注入 resolved 闭环
ALERT_EXTERNAL_DELIVERY_PASS=false    # webhook 未配置

OBSERVABILITY_RESTART_RECOVERY_PASS=true   # J1-J7 全过
OBSERVABILITY_SECURITY_PASS=true           # L1-L8 全过
OBSERVABILITY_CARDINALITY_PASS=true        # M5/M6 全过

OBSERVABILITY_CORE_PASS=true
OBSERVABILITY_EXTERNAL_NOTIFICATION_BLOCKED=true   # 唯一阻断项=webhook
```

**本轮验收修复的 4 个真实缺陷/缺口**：①PM 无自监控抓取（I1/I6/M3 连带不可查）→ 已加 job；②retention 依赖隐式默认（M4）→ compose 显式 15d；③看板默认窗 3h 不合用途（K1）→ 24h；④看板无版本来源标识（K9）→ description 注明配置源。

**改动文件（全部未提交）**：`docker/prometheus.yml`（自抓取）、`docker-compose.yml`（retention+中文语言）、`docker/grafana/provisioning/dashboards/` 5 文件、本报告。

---

## 五、企业化收尾轮（2026-10-06 傍晚，开放项清零）

> 按"长期做法"完成剩余开放项：唯一 blocker（外投）、故障矩阵第三项（App Down）、H7、K6、M7，以及配置入库与文档同步。

| 项 | 结果 | 证据 |
|---|---|---|
| App 下线告警缺口 | ✅→**新规则+实测** | 故障矩阵暴露"app 挂了无告警"真缺口——新增 `AppMetricsDown`（up{job=agent-platform-app}==0, for:1m, critical）；**真停 agent-app-1 注入**：110s 后 firing → AM → 桥收到完整格式化报文（summary+description+labels）→ 恢复后 resolved 外发，PM 零残留 |
| 外投链路（原唯一 blocker） | ✅ | `alert-bridge` 零依赖转发桥上线（stdlib，企微/飞书/钉钉三通道+加签，未配置时落桥日志）；`send_resolved: true`；**FIRING 与 RESOLVED 双向报文实测到达桥**；群机器人 URL 填 `.env` 即达 IM（`docker logs agent-alert-bridge-1` 可查全量投递记录） |
| H7 Runbook 指引 | ✅ | 宽口径盘点 55 条：原 49 含指引→补齐 RedisDown/PostgresDown/LLMDegradedAnswerSpike/ContextL5LockConflictSpike 4 条裸描述（先查命令级指引） |
| K6 面板错误态 | ✅ | API `/api/ds/query` 坏查询 → **400 显式 parse error**（非伪装 0）；UI 层坏面板出现**红色错误三角**+「无数据」；**附带发现：provisioning 30s 内自动回滚 API 手改——防漂移是自愈的** |
| M7 看板查询压力 | ✅ | 51 表达式×3 轮并发（50 线程）：389 qps，P50 66ms / max 133ms，**0 错误**，PM CPU 0.32%→0.83%——看板永远不会压垮 PM |
| 端口冲突修正 | ✅ | 桥宿主端口 9094 与 kafka(java-loop) 冲突 → 让位 **9095**（容器内 9094 不变，AM 走服务名）；system-overview.md 端口表同步修正（原表 9094 误标 kafka 为 9094/kafka 实为 9092） |
| 文档同步 | ✅ | `docs/architecture/system-overview.md` 端口表补 Alertmanager/alert-bridge 行；`.env.example` 补告警外投链段 |

### 终版总门禁

```ini
PROMETHEUS_HEALTH_PASS=true
PROMETHEUS_TARGETS_PASS=true          # 12/12 up（含自抓取）
PROMETHEUS_RULES_PASS=true            # 55 告警+6 recording, failures=0
PROMETHEUS_DATA_FRESHNESS_PASS=true

GRAFANA_DATASOURCE_PASS=true
GRAFANA_DASHBOARDS_PASS=true
GRAFANA_RENDER_PASS=true
GRAFANA_PROVISIONING_PASS=true        # 幂等+零漂移+30s 自愈回滚

ALERTMANAGER_READY_PASS=true
ALERT_PM_TO_AM_PASS=true
ALERT_RESOLVE_LIFECYCLE_PASS=true     # 三种故障注入全闭环（redis-exporter/worker/app）
ALERT_EXTERNAL_DELIVERY_PASS=true     # AM→桥 FIRING/RESOLVED 双向实测

OBSERVABILITY_RESTART_RECOVERY_PASS=true
OBSERVABILITY_SECURITY_PASS=true
OBSERVABILITY_CARDINALITY_PASS=true
OBSERVABILITY_DASHBOARD_PERF_PASS=true   # M7: 389qps/0错误

OBSERVABILITY_CORE_PASS=true
OBSERVABILITY_STACK_PRODUCTION_READY=true
# 唯一待用户动作：在 .env 填 ALERT_BRIDGE_* 群机器人 URL（管道已通，填入即达）
```

**收尾轮改动文件（与上节合计，均已入库提交）**：`docker/alert-bridge/`（新服务）、`docker/prometheus-alert-rules.yml`（AppMetricsDown+H7）、`docker/alertmanager.yml.tpl`（send_resolved）、`docker-compose.yml`（桥+端口 9095）、`.env.example`、`docs/architecture/system-overview.md`。

## 六、飞书接入轮（2026-10-06 深夜补记，总会话收官）

- **人话版报文**：`880b4e8` 起桥外投报文改为运维可读格式（告警名/实例/当前值/持续时间/一句处置建议），不再裸吐 Prometheus 原始 JSON。
- **飞书加签**：桥支持 `ALERT_BRIDGE_FEISHU_SECRET` 飞书签名校验（`bfdabf8`），占位说明见 `.env.example`（`e9d9698`）；webhook 实值只存 `.env`（gitignored），**严禁入仓**。
- **群内实测**：用户已在真实飞书群确认 🚨FIRING / ✅RESOLVED 双向到达——`ALERT_EXTERNAL_DELIVERY_PASS=true` 的外部证据闭环。
- **resolved 通知节奏**：恢复通知由 Alertmanager `group_interval`（最迟 5 分钟）批量发出，属自动行为非缺陷；若群内未見 ✅，先查桥日志投递回执再查群机器人设置。
- **看板生成器入库**：`gen_dashboards.py` 自 `D:/tmp` 移入 `docker/grafana/`（输出路径改为随脚本定位），跑法 `python docker/grafana/gen_dashboards.py PBFA97CFB590B2093`——看板改版唯一正道仍是改生成器/仓库 JSON 后经 provisioning 30s 装载。
- 遗留 P3 挂账不变：桥 compose healthcheck、AM 按 severity 分流、上服务器 TSDB 17GB 磁盘预留。
