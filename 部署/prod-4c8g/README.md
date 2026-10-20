# 4C8G / 40G 生产配置（上线用）

> 目标机：**4 vCPU / 8 GB RAM / 40 GB SSD**，Docker Compose 单机部署。
> 本目录只放「额外覆盖」，主定义仍是仓库根 `docker-compose.yml`。

## 1. 内存预算（合计 ≤ 6.2 GB，留 ≥1.8 GB 给系统与 Next.js）

| 服务 | 限制 | CPU | 说明 |
| --- | --- | --- | --- |
| postgres | 1200m | 1.5 | 唯一状态权威，优先保 |
| backend | 3800m | 3.0 | 单容器后端：supervisord 托管 app / rag-service / mcp-service / 4 个 Celery 池 / beat / 双 dispatcher（见 docker/backend-supervisord.conf） |
| apisix | 224m | 0.5 | 网关 |
| redis（缓存） | 384m | 0.5 | maxmemory 320mb |
| redis-broker | 384m | 0.3 | maxmemory 320mb |
| auth-redis | 128m | 0.2 | maxmemory 96mb |
| pg-backup | 96m | 0.2 | 每日备份 |
| **合计** | **≈ 6.2 GB** | 6.2（上限，非峰值） | 4 核按需调度 |

**不启动**：`kafka`、`ollama`、`business-mock`、`mcp-12306`、observability 全套（prometheus/grafana/loki/tempo/promtail/exporters）。
>
> 2026-10-20 容器合一后 rag-service / mcp-service 进程随 `backend` 容器常驻（空闲约 300–500 MB，已计入 backend 限额；RAG 走云端 embedding/rerank，不加载本地模型）。内存吃紧时可 `docker exec agent-backend supervisorctl stop rag-service mcp-service` 临时停掉，`start` 恢复。

**前端（3 个 Next 应用）不在 compose 内**，在宿主机用 `next start` 跑，每个约 150–250 MB：
- 必须：`frontend`（用户端）、`frontend-admin`（管理端）
- 按需：`frontend-cs`（客服坐席）
- 建议 `next build` 时加 `output: 'standalone'`，只用 `node .next/standalone/server.js` 起，省 30% 内存。

## 2. 首次部署

```bash
# 1) 环境变量
cp 部署/prod-4c8g/env.production .env.production
#    逐项替换 <...>；生成随机串：
#    openssl rand -hex 32
#    python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # SECRETS_ENCRYPTION_KEY
#    AI_INTERNAL_TOKEN / API_KEY 同样用 openssl rand -hex 32
chmod 600 .env.production

# 2) 构建（首次约 10–15 分钟；40G 磁盘只留一份镜像）
docker compose --env-file .env.production -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.4c8g.yml build

# 3) 起核心栈（顺序：PG/Redis → 迁移 → 应用 → Worker）
docker compose --env-file .env.production -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.4c8g.yml up -d \
  postgres redis redis-broker auth-redis apisix
docker compose --env-file .env.production -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.4c8g.yml run --rm db-migrate
docker compose --env-file .env.production -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.4c8g.yml up -d \
  backend pg-backup
```

> `up` 必须显式列服务：不列就不会启动 kafka/ollama 等重服务。

## 3. 上线核对（真实命令）

```bash
docker compose ps                                    # 期望：全部 healthy，无 kafka/ollama
curl -s localhost:9080/health                        # APISIX → 200
curl -s localhost:8000/health                        # FastAPI → 200
curl -s -H "X-API-Key: $API_KEY" localhost:8000/sys/providers | head -c 200   # source=db
docker exec agent-postgres-1 psql -U postgres -d agent_memory -tAc \
  "select count(*) from pg_stat_activity"            # 期望 < 90（max_connections=120）
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"  # 峰值 RSS 不得贴近 limit
```

**必须先在管理端「模型与供应商」里**：新增供应商 → 填 base_url → 填 API Key → 测通 → 绑定角色（main/eval_gen/embedding/rerank）。模型密钥只写数据库，不写 .env。

## 4. 40 GB 磁盘规划（硬约束）

| 项 | 预算 |
| --- | --- |
| 系统 + Docker Engine | 6 GB |
| 镜像（python/torch 基础镜像最大） | 10 GB |
| `pg_data` 卷 | 4 GB |
| `app_data` 卷（docs/索引/BM25） | 6 GB |
| Redis AOF + 备份卷 | 2 GB |
| 日志（已轮转 50m×3/容器） | 2 GB |
| 余量 | 10 GB |

上线前设置并加进 cron：

```bash
# 每周清理 dangling 镜像/构建缓存（不要动 volume）
docker image prune -f && docker builder prune -f --filter until=168h
# 每天检查磁盘，>85% 告警
df -h / | awk 'NR==2{print $5}'
```

## 5. 4C8G 上的已知取舍

- **RAG 用云端 embedding/rerank**（`EMBEDDING_PROVIDER=cloud`）：本地 BGE 模型会多占 800MB–1.2GB，8G 机器与 app/workers 抢内存。
- **APISIX + backend（单容器） + PG + 3 Redis** 已是这台机器的上限；再加服务请先加内存。
- `DIST_MAX_CONCURRENT_REQUESTS=24`：超过即 503 + Retry-After，保护 PG 与模型配额。
- 观测建议外置：Prometheus/Grafana 放另一台，本机只保留 `/metrics`。
- `max_connections=120` 与 `DB_POOL_MAX_CONN=2` 是配套的；**只调其中一个会把 PG 打满**（2026-10-17 开发环境即因 103/100 打满导致管理端误报「DB 未就绪」）。

## 6. 回滚

```bash
# 只回滚应用镜像（不动数据）
docker compose --env-file .env.production -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.4c8g.yml \
  up -d --no-deps --force-recreate backend
```

迁移回滚按 `scripts/init_db.py` 的迁移台账反向执行；**不要 DROP 已应用迁移**，用新迁移修正。

## 7. 本机按同档运行（Docker 只有 8 GiB 时）

本机开发栈也可按同一档位收敛，使用 `docker-compose.local.yml`（保留本机特有服务与开发鉴权）：

```bash
# PG 参数用 ALTER SYSTEM 落库（免绑定挂载，重启即生效）
docker exec agent-postgres-1 psql -U postgres -d agent_memory -c \
  "ALTER SYSTEM SET max_connections='120'"
# ……shared_buffers=384MB / effective_cache_size=1024MB / work_mem=8MB 等同理
docker restart agent-postgres-1

# 其余服务套用本机覆盖层（backend 单容器已含 rag-service / mcp / 双 dispatcher）
docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml up -d --force-recreate \
  postgres redis redis-broker auth-redis apisix backend pg-backup
```

实测（2026-10-20，本机 6C/12T + Docker 8 GiB，隔离栈 fresh 库）：backend 单容器 10 进程全部 RUNNING（supervisorctl 总览），整栈（backend + apisix + postgres + 3 redis）内存约 **2.0 GiB**（backend 单容器约 1.5 GiB），迁移链 fresh 库全通过，三个 HTTP 端口 /health 与网关别名链路（apisix→app:8000）均正常。

进程级运维：`docker exec agent-backend supervisorctl status`（总览）/ `tail -f app`（单进程日志）/ `restart agent-worker`（只重启某进程）。
