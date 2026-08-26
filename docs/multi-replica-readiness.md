# 多副本水平扩展准备方案（P1-15）

> 状态：**方案文档（未实施）**。当前系统按单副本部署设计；
> 若需水平扩展（HPA / 多实例负载均衡），须先完成本文的三项改造。
> 涉及位置：`backend/app/api/routes/chat.py`、`backend/app/api/routes/rag_upload.py`、
> `backend/app/server.py`、`backend/config/database.py`。

## 1. 现状盘点：进程内状态清单

多副本不一致的根源是**状态落在进程内存**，副本之间互不可见：

| # | 状态 | 位置 | 多副本症状 | 改造项 |
|---|---|---|---|---|
| 1 | SSE 中止信号 `_active_stops: dict[str, Event]` | `chat.py` | 用户对副本 A 发起 abort，请求实际跑在副本 B → 停不掉 | 改造 ① |
| 2 | SSE 输出队列 `queue.Queue` | `chat.py` | 请求经 LB 落到哪个副本，哪个副本才能推流（本身可工作，但与 ① 绑定） | 改造 ① |
| 3 | 上传进度队列（内存 dict + GC loop） | `rag_upload.py` F11 | 上传后轮询 `/progress` 被 LB 转到另一副本 → 404/进度丢失 | 改造 ② |
| 4 | Workflow 定时调度器（APScheduler in-proc） | `server.py` 启动钩子 | 每个副本各跑一次 daily_report / inventory_alert / weekly_eval → 重复报告、重复告警 | 改造 ③ |
| 5 | 文档/索引存储（`data/` 本地盘：chroma、doc_db、chunk_store） | `config/database.py` 路径派生 | 副本各自挂各的盘 → 索引不一致，上传文档对其他副本不可见 | 改造 ④ |
| 6 | Planner LRU 缓存 / rate limiter / 熔断器 | 各模块 | 每副本独立（语义可接受：缓存命中率下降、限流阈值 × N、熔断状态不一致） | 不改，监控覆盖 |

PostgreSQL（memory/business 两库）与 Docker 卷 `app_data` 在单机 compose 下已持久化，
但多副本必须显式共享（见改造 ④）。

## 2. 目标架构

```
                        ┌────────────┐
 Client ──► LB/Nginx ──►│ 副本 × N   │──► PostgreSQL（已有，共享）
                        │ (FastAPI)  │──► Redis（新增：中止信号/进度/锁）
                        └────────────┘──► 对象存储/共享卷（新增：文档与索引）
```

原则：
- **无状态化请求路径**：副本间不共享任何请求级内存状态，任意副本可服务任意请求。
- **恰好一次调度**：定时任务全集群只跑一次。
- **单一数据面**：PG + 对象存储是唯一事实，副本不持有权威数据。

## 3. 改造方案

### 改造 ① SSE 中止信号外置 Redis（chat.py）

现状：`_active_stops[key] = threading.Event()`，abort 端点 `set()` 同进程事件。

方案：
1. Redis key：`chat:stop:{session_id}:{request_id}`，TTL = SSE 超时上限（如 300s）。
2. abort 端点改为 `SET` 该 key（替代 `event.set()`）。
3. worker 侧在生成循环的检查点处 `EXISTS` 轮询（或用 Redis pub/sub `SUBSCRIBE chat:stop:{key}`，
   线程内用 `select`/超时阻塞读）。
4. 兼容路径：无 Redis 时回退现有进程内 Event（本地开发零依赖）。
   抽象为 `StopSignal` 接口（`LocalStopSignal` / `RedisStopSignal`），按 `REDIS_URL` 是否配置选择。

**SSE 连接本身不需要外置**：每条 SSE 长连接固定在受理它的副本上，LB 只需开启
sticky 不必——直接禁止跨副本轮询即可（每连接一个副本，天然成立）。

### 改造 ② 上传进度队列外置 Redis（rag_upload.py）

现状：`_progress_queues: dict[upload_id, Queue]` + 进程内 GC loop。

方案：
1. 进度事件改写为 Redis Stream：`XADD upload:progress:{upload_id} * stage=... pct=...`，
   TTL 由消费者轮询 `XRANGE` 后按 idle 时间清理（`object idle time > TTL` 时删除）。
2. 轮询端点改为读 Stream（任意副本可读）。
3. GC loop 改为 `SCAN upload:progress:*` + idle 判断（每副本都可跑，幂等删除无害）。
4. 同样提供 `LocalProgressBus` 回退实现。

### 改造 ③ 定时任务分布式锁（server.py:337）

现状：`register_workflows_and_schedules` 在每个副本启动时 `sched.start()`，
daily_report(9:00) / inventory_alert(8:00) / weekly_eval(周日 2:00) 会被跑 N 次。

方案（按成本从低到高三选）：
- **方案 a（推荐，零新依赖）**：调度任务体入口加 **PG advisory lock**：
  ```sql
  SELECT pg_try_advisory_lock(hashtext('workflow:daily_report'));
  ```
  拿到锁的副本执行，其余副本直接跳过本轮；任务结束 `pg_unlock`。
  优点：不引入新组件，PG 已是共享设施；缺点：任务超过下一轮触发周期时需防重入（锁粒度按任务名）。
- **方案 b**：Redis `SET job:lock:{name} <token> NX EX <ttl>` + token 校验释放（防误删）。
- **方案 c**：调度器整体外移为独立单副本 Deployment（`scheduler` 服务，仅它跑 APScheduler），
  app 副本不再注册定时任务（环境变量 `ENABLE_SCHEDULER=false`）。
  优点：职责清晰、无锁；缺点：多一个部署单元。

> weekly_eval 这类重任务建议直接用方案 c；report/alert 类轻任务方案 a 即可。

### 改造 ④ 文档存储上对象存储/共享卷

现状：`RAG_DATA_DIR`（默认 `backend/data`）下的 chroma / doc_db / chunk_store /
docs 全在副本本地盘。

方案（按部署形态）：
- **K8s**：`RAG_DATA_DIR` 指向 RWX PVC（`ReadWriteMany`，NFS/CephFS）——改动为零，
  仅配置变更。注意 chroma sqlite 在多写入方下有锁竞争，建议写入串行化（上传接口已有并发限制）。
- **对象存储（S3/COS/OSS）**：原始文档（`docs/`）上对象存储，索引仍放共享卷。
  blob 路径记录进 `doc_registry.db`（已结构化，改造集中在 fetcher 层）。
- **chroma 集中式**：如写入冲突不可接受，切换 Chroma 的 client/server 模式
  （独立 chroma Deployment），app 副本全部作为 client 访问。

## 4. 配置面新增

| 变量 | 用途 | 默认 |
|---|---|---|
| `REDIS_URL` | 中止信号/进度/锁（空 = 全部回退进程内实现） | 空 |
| `ENABLE_SCHEDULER` | 副本是否注册定时任务（方案 c） | true |
| `RAG_DATA_DIR` | 指向共享卷路径（已有，复用） | backend/data |

启动校验（`config/startup.py`）可追加：`REDIS_URL` 非空时连通性探测（warning 级）。

## 5. 分阶段落地路径

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1（0.5 天） | 改造 ③ 方案 a：advisory lock 包住三个定时任务 | 双副本起服务，9:00 报告只产生一份 |
| M2（1 天） | 改造 ① StopSignal 抽象 + Redis 实现 | 双副本下 abort 打到非受理副本也能停流 |
| M3（1 天） | 改造 ② 进度总线 Redis Stream | 上传后从另一副本轮询进度可见 |
| M4（0.5 天） | 改造 ④ 共享卷/对象存储 + chroma client/server | 文档上传后任意副本可检索 |

## 6. 不改但需监控的多副本行为

- rate limiter：每副本独立限流 → 全局阈值 ≈ N × 单副本值，需按副本数换算或改 Redis 令牌桶。
- 熔断器状态：各副本独立跳闸，`/metrics` 聚合后按 label `instance` 区分。
- Planner 缓存：命中率随副本数稀释，仅影响延迟不影响正确性。
- 并发上限中间件：同上，全局并发 = N × 配置值。

## 7. 结论

当前单副本形态**无需**立即实施本方案；触发条件（出现任一）：
1. 单副本 CPU/内存达到容量告警阈值；
2. 可用性要求需要滚动发布零停机；
3. 流量峰值需要 > 1 实例消化。

届时按 M1 → M4 顺序落地，总投入约 3 人日。
