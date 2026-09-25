# Platform Runbook（G9 最小运维手册）

> 配套：`docs/2026-09-24-Platform-ProductionReadiness-完成报告.md`
> 唯一启停入口：`devctl.bat`（本机）；发布入口：`scripts/release.sh`

## 1. 发布（stop-the-world，唯一发布形态）

```bash
cd 仓库根
export RELEASE_PASSWORD=<e2e_domain口令>
bash scripts/release.sh            # preflight → 全量 build → up → 12 门 Gate
bash scripts/release.sh --skip-build   # 仅重新 up+Gate（镜像未变时）
```

- 发布顺序强制：**先 migration（db-migrate 门）后应用**；compose depends_on 已固化。
- Gate 红 = 不得发布（P0 Blocker）；P1 需书面 waiver；P2 可登记后发布。
- `GIT_COMMIT=unknown` = Gate0 直接拦（发布流程违规）。

## 2. 新增迁移

1. 写 `backend/sql/migrations/0XX_xxx.sql`（纯 expand：新列 nullable/default、新表、IF NOT EXISTS）
2. **必须登记** `scripts/init_db.py::MIGRATION_TARGETS`（漏登记=重建时 rc=2 整栈拒启）
3. 对象由应用运行时建立的（如 tasks 表）：登记进 `RUNTIME_MANAGED_MIGRATIONS`（执行态 skipped）
4. 跑 `cd backend && PYTHONPATH=.. python scripts/verify_migration_state.py`，三层全 OK 才继续

## 3. 健康与指标

```bash
curl -s localhost:8000/health | jq '.status, .build, .migrations'   # app
curl -s localhost:9080/health -o /dev/null -w "%{http_code}"        # 网关
curl -s localhost:8000/metrics | grep -E "^routing_domain_total|^chat_request_total"
```

- `/health` 恒 200（观测面）；`migrations.status="drifted"` = 镜像内迁移领先 DB → 重建 db-migrate。
- Prometheus :9090 → targets 全 up；Grafana :3001。

## 4. 日志

```bash
docker logs -f agent-app-1          # 主应用
docker logs -f agent-agent-worker-1 # 任务 worker
[Build] service=app commit=xxx      # 启动身份行：确认运行版本
```

## 5. 任务 stuck

```bash
# 状态权威
docker exec agent-postgres-1 psql -U postgres -d agent_memory -c \
  "SELECT id,status,worker,recovery_count FROM tasks ORDER BY created_at DESC LIMIT 10"
# 触发僵尸接管 sweep（生产同路径）
docker exec agent-agent-worker-1 python -c \
  "from backend.tasks.celery_app import celery_app;celery_app.send_task('tasks.stale_execution_recovery',queue='maintenance')"
# 手动 stop 过的 worker 不会被 unless-stopped 拉起（崩溃才会）——必须 docker start
```

## 6. 依赖故障速查（详见 STOP D/E 报告）

| 故障 | 现象 | 处置 |
|---|---|---|
| Redis down | 会话上下文降级 memory fallback，chat 仍可用 | `docker restart agent-redis-1` |
| PG down | chat 降级可用（LLM 外呼+会话降级，实测 2026-09-25 F3）；任务路径连接池曾毒化——已修（task_executor 连接池 checkout 探活，5007b43）；compose 启动门仍 fail-closed | `docker restart agent-postgres-1` → wait_healthy → 提交新任务验证 SUCCESS（修复后无需重启 worker；旧镜像行为需重启 worker） |
| APISIX down | 网关不可达；app 仅环回可达无外部暴露 | `docker start agent-apisix` |
| Worker 崩溃 | unless-stopped 自动重启；RUNNING 任务经 lease 回拨+sweep recovery | 崩溃自动恢复；**手动 stop 需手动 start** |
| LLM provider | Governance fallback 链；llm_failures/fallback 指标 | 管理端 /sys/model-health 排查 |
| Embedding/RAG | RAG 拒答兜底，路由规则层不受影响 | rag-service /readyz 排查 |

## 7. 回滚

```bash
# 镜像在本地保留（无 registry）：回退到上一版
docker tag <上一版镜像ID> agent-app:rollback
docker compose up -d app   # 或全量
```

- 边界：044-049 纯 expand，新 schema 兼容旧 app——**当前无 ROLLBACK_BLOCKED 迁移**；
- 引入破坏性迁移（DROP/改型）时必须在本文件登记 `ROLLBACK_BLOCKED_AFTER_MIGRATION_0XX`。

## 8. 账号口径（演练/验收）

`e2e_domain`(editor, default) / `e2e_travel`(viewer, default) / `e2e_tenantb`(editor, tenant-b)。非 default 租户登录须直连 app + 显式可信租户头（网关 auth 路由固定注入 default）。
