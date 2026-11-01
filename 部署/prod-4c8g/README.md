# 4C8G / 40G 生产配置

目标为 Linux 4 vCPU / 8 GiB RAM，模型推理运行在独立机器或外部服务。模型角色、供应商和凭据继续由管理端数据库管理。此配置不包含在 8 GiB 主机上同时加载 BGE 与 reranker 的容量承诺。

## 资源预算

根 `docker-compose.yml` 定义服务，`docker-compose.local.yml` 是本机与生产共用的资源层（保留文件名以兼容现有入口），`docker-compose.4c8g.yml` 仅覆盖生产运行环境文件。所有常驻容器共享 CPU 0–3，并按 cpu_shares 分配争用时优先级；单服务 CPU 上限之和可以超过 4，但实际执行被限制在这 4 个 CPU 上。

| 服务 | 内存上限 MiB | CPU 上限 | 说明 |
| --- | ---: | ---: | --- |
| PostgreSQL | 1024 | 1.5 | max_connections=120、shared_buffers=256MB、work_mem=4MB |
| Redis 缓存 | 384 | 0.4 | maxmemory=192mb，LRU，给 AOF/分配器留余量 |
| Redis broker/result | 192 | 0.2 | maxmemory=128mb，noeviction |
| Auth Redis | 96 | 0.2 | maxmemory=48mb，noeviction |
| APISIX | 288 | 0.5 | 实测常驻约 228MiB |
| PgBouncer | 64 | 0.2 | client=200、pool=15+3；应用是否经池化仍取既有开关 |
| API | 768 | 1.5 | 单 uvicorn 进程 |
| Agent worker | 768 | 1.0 | 并发 1；真实主图与检查点运行时预留空间 |
| 索引 worker | 768 | 1.0 | 并发 1；外部 embedding 服务 |
| RAG 服务 | 512 | 1.0 | 与当前 remote 请求链路一致 |
| MCP 服务 | 224 | 0.4 | 工具接入 |
| 维护 worker | 384 | 0.3 | 并发 1，常驻实测约 280MiB |
| 报表 worker | 512 | 0.5 | 并发 1；与 Agent worker 调整分配，整机总预算不变 |
| Beat | 128 | 0.2 | 单实例调度 |
| 客服 dispatcher | 192 | 0.3 | 单副本；不保留单机多副本冗余 |
| PostgreSQL 备份 | 96 | 0.2 | 每日基础备份；外部异地备份仍需配置 |
| 三个 Next.js 前端 | 768 | 各 0.5 | 每个 256MiB、生产 standalone；不在服务器运行 next dev |
| **常驻服务合计** | **7168** | 共享 4 核 | 包含全部三个前端和备份服务 |
| **系统预留** | **1024** | — | 合计 8192MiB |

迁移容器 512MiB 是启动期一次性任务，迁移完成后才启动应用 worker。开发业务 mock 额外 128MiB，不在生产启动清单中。Kafka、Ollama、metadata-shadow、外部 MCP 12306 和完整 observability profiles 默认不启动；启用前必须重新核算预算，不能叠加到本档位。

本机其他操作已启动的12306适配器可保留并受128MiB/0.2CPU约束，归入travel-adapter可选profile；它和业务mock不属于7168MiB生产基线。生产启用适配器时须额外预留128MiB或关闭不使用的前端，不能把可选服务算作零成本。

API/worker 的每进程数据库池为 min=1/max=2；请求全局闸门为 12。索引任务复用独立队列、Celery prefetch=1、单索引 worker。普通文档索引批间并发为 1；全量向量重建当前固定最多 8 条/批、串行生成，不受 EMBED_BATCH_CONCURRENCY 控制。容器日志轮转为每份 10MiB × 3。

全量重建使用独立的 EMBEDDING_REBUILD_REQUEST_TIMEOUT=60 秒与 EMBEDDING_REBUILD_BATCH_SIZE=1：候选每次读取最多8条，但向模型逐条发送，避免长短文本混批引起GPU显存和计算放大。SDK层不自动重发离线请求，防止客户端超时而模型仍计算时继续叠加推理。在线检索仍使用原有短超时、批大小和重试策略；这些执行策略不改变模型语义空间或指纹。Celery 全量任务软/硬时限为3600/3660秒，队列可见性设为3900秒，避免仍在执行的消息提前重投。失败后在任务中心重试同一任务；源文本和元数据仍一致的候选会复用，不重复推理已完成部分。

这些是容量基线。必须结合真实重建和并发问答检查 OOM、任务失败与延迟，不能以健康检查代替负载验收。8 GiB 是内存预算；云服务器实际可用内存较小时，应按实测可用量重新缩减预算。

在线时间预算为 BUSINESS_DEADLINE_S=60、WORKFLOW_DEADLINE_S=55、REPORTER_RESERVED_S=5；请求仍受现有全局并发12约束。rag.search沿用45秒策略上限及零自动重试，实际取剩余图预算扣除收尾与45秒中的较小值。真实请求曾出现RAG服务约26秒成功、主助手约19秒提前超时并丢弃答案，因此本档位覆盖默认30/25/5秒配置；不增加模型调用次数或绕过检索证据校验。

本档位 TOOL_INLINE_MAX_TOKENS=2048。真实5条搜索结果约833 token，默认768会将结果裁成无法完整展示的预览；2048保留常规结果，但超限仍走既有压缩协议，模型调用仍受8192总上下文预算及输出/安全预留约束。该调整不改变容器并发或内存限额。

## 生产启动

镜像在开发机或 CI 顺序构建，避免在 4C8G 生产服务器同时运行多个 Next.js 构建。前端 Dockerfile 使用 Node 22，依赖层使用既有 npm 缓存，运行时只向 BFF 注入 API_KEY，不编译到浏览器。

```bash
cp 部署/prod-4c8g/env.production .env.production
# 替换全部占位凭据，确认生产网关鉴权启用；文件只允许部署账号读取。
chmod 600 .env.production
export COMPOSE_FILE=docker-compose.yml:部署/prod-4c8g/docker-compose.local.yml:部署/prod-4c8g/docker-compose.4c8g.yml
# CLI 插值与容器 env_file 都读取生产配置；不依赖开发 .env。
docker compose --env-file .env.production build app rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher db-migrate
for service in frontend frontend-admin frontend-cs; do
  docker compose --env-file .env.production build "$service"
done
docker compose --env-file .env.production up -d postgres redis redis-broker auth-redis apisix pgbouncer
docker compose --env-file .env.production run --rm db-migrate
docker compose --env-file .env.production up -d app rag-service mcp-service agent-worker rag-index-worker maintenance-worker report-worker beat cs-dispatcher pg-backup frontend frontend-admin frontend-cs
```

`RUNTIME_ENV_FILE` 默认是 .env.production；只有本机演练才可显式指向 .env。生产层用 !override 替换根服务 env_file，避免误读开发凭据。前端端口默认仅绑定 127.0.0.1:3100/3200/3300，由已有 HTTPS 入口反向代理；本配置不自动扩大公网暴露。

客服业务/退款通道开关 `CS_BUSINESS_GATEWAY_MODE` 与 `CS_REFUND_REQUEST_MODE` 默认 disabled（退款写入默认关闭，见 docs/domains/customer-service.md），env.production 未显式配置即保持关闭，属安全默认。如需启用客服资金链路（退款审核闭环、商城订单/物流契约），两个开关必须同启为 `http` 且 business-service 可达：compose 已定义 `business-mock`（docker-compose.4c8g.yml，演示/联调事实源，重启清空内存态），但不在上方启动清单内——启用时须将其加入 build 与 up -d 列表；生产收单/真实支付属商城域另立项，不得把 mock 当生产支付。

模型供应商需由管理端登记为可从生产服务器访问的外部地址。host.docker.internal:8088 是本机联调地址，不能直接视为生产模型地址。登记/切换模型继续经过预检、身份围栏和重建确认。

## 本机演练与恢复

Windows 的 devctl 在未指定 COMPOSE_FILE 时默认加载根 Compose 与共享资源层。已有前端容器时，start/stop 操作容器；没有容器时仍启动本机 Next dev，需要另行配置该前端的服务端 API_KEY/API_URL。4C8G验收与生产均使用上述前端容器，不依赖开发进程的凭据或内存占用。

```powershell
.\devctl.bat rebuild /y
# 前端顺序构建，停止占用相同端口的旧本机前端后再启动。
docker compose -f docker-compose.yml -f 部署/prod-4c8g/docker-compose.local.yml build frontend-admin
```

运行后确认 docker inspect 中 cpuset=0-3、实际内存/并发配置、容器健康与 OOM/重启计数；检查 8000/health、8090/readyz、8091/health、9080/health，以及三个前端。检查 PostgreSQL 当前参数与连接数，确认备份日志成功，并用 pg_restore -l 验证备份可读。恢复演练需要隔离数据库，禁止覆盖共享数据库。

随后从管理端“模型与供应商”点击“检查并重建全部向量”，预检显示实际模型、集合数量及向量数量后确认提交。跟踪任务到终态，核对集合模型/供应商/指纹与状态，再发起一次企业助手退款问答。失败时先读取任务/Trace 根因，旧向量保留；不能跳过发布校验强行标 ready。

## 磁盘与回滚

40GB 磁盘需同时容纳系统、镜像、业务数据、Redis AOF、日志和备份；各目录占用以实际 df/du 为准，超过 85% 停止新增构建/导入并扩容或按既有保留策略处理。备份默认保留 7 天，仍需外部异地副本。不要删除 volume 或自动清理其他任务镜像。

回滚应用使用已知可用镜像并重新创建对应服务，保留数据卷。配置回滚恢复对应 Compose 与环境文件后重建；数据库结构回滚走迁移治理，不能 DROP 已应用迁移。资源限制回滚不能代替已发布向量数据的恢复。
