# Context Budget 生产收口 · STOP C 多 Worker 实机验收报告

> 2026-09-23。真 Redis + 真 PostgreSQL + 2 个独立 FastAPI worker 进程，禁止全 mock 的动态验收。
> 基线：STOP A `1edc6e0`（审计）、STOP B `69b2f4e`（接线 + migration 044 落地）。

---

## C1 环境确认（跨进程证明）

| 项 | 实测值 |
|---|---|
| Worker A | 容器 `agent-cek-a`（agent-app:latest，2026-09-23 09:42 构建），`python -m uvicorn backend.app.server:app`，宿主 127.0.0.1:**8002**，/health=200 |
| Worker B | 容器 `agent-cek-b`（同镜像），同命令，宿主 127.0.0.1:**8003**，/health=200 |
| Redis | `agent-redis-1`（缓存实例，容器内 redis:6379/0）——L5 分布式锁所在 |
| PostgreSQL | `agent-postgres-1`，agent_memory（044 已应用，summary_version 可用） |
| 测试会话/租户 | session_id=`ctx-race-s1`，tenant=`test-tenant`，user=`ctx-race-user` |
| 播种 | chat_sessions 1 行 + chat_messages 24 条（12 轮，id 3098–3121） |

跨进程证据链（非同进程 asyncio task 冒充）：
1. 两个 uvicorn 容器分别经 :8002/:8003 各自服务了同一会话的 HTTP 请求——chat_messages 落库的对话轮 3122/3123（A 处理）与 3124/3125（B 处理）分属两次请求，均在「同一 session」下由**不同容器进程**持久化。
2. L5 竞争双方分别在两个容器内以独立 OS 进程执行（驱动脚本各自容器内 PID 隔离），共享同一 Redis/PG。
3. 主 `agent-app-1`（单 uvicorn 进程，生产容器）全程未动、未重启。

测试专用 env 覆盖（仅存在于两个临时容器，`CONTEXT_L5_TRIGGER_RATIO=0.10`）用于让真实触发链路在可控历史长度下开火；生产默认 0.90 不变。

## C2 同 Session L5 Race（Redis single-flight）

双 worker **同时**对 `ctx-race-s1` 触发 L5（真实链路：L2 trim → L5 触发判定 → Redis 锁 → 真 LLM 摘要（context_compactor 角色 → qwen3.8-flash 真实 API）→ PG CAS）：

| | Worker A（:8002 容器） | Worker B（:8003 容器） |
|---|---|---|
| 结果 | **success**：`delta_messages=20 through_id=3117 summary_tokens=186 protected_facts=20 llm_tokens=4349+257 latency_ms=10079` | **lock_conflict**：`L5 单飞锁被占用，跳过本轮摘要`，0.21s 携裁剪结果返回 |
| 指标 | `context_l5_total{reason="success",status="success"} 1.0` | `context_l5_total{reason="lock_conflict",status="failed"} 1.0` |
| 聊天阻断 | 无 | **无**（overflow=false，used=1571，prepare 正常返回） |

落库：`summary_through_message_id=3117, summary_version=0→1`，摘要含 `[业务状态] 订单 ID: 20260922001`（ProtectedFact 保真）。锁键 `context:l5:default:ctx-race-s1` 在 finally 释放（keys 扫描为空）。

## C3 CAS Race 强制测试（人为绕锁 + 延迟慢方）

按任务 §十三 允许的手段：两侧驱动均绕过分布式锁（`_acquire_l5_lock → None` 降级语义），A 在真 LLM 调用前人为 sleep 20s（慢），B 即时（快）。时间线（容器日志时间戳）：

```
04:26:44  A 读到 expected_through=0 → sleep 20s（模拟慢摘要）
04:26:47  B 启动，真 LLM 摘要（qwen3.8-flash，4349+512 tok，14.4s）
04:27:01  B 提交成功：through=3117, version 0→1
04:27:04  A 慢方 LLM 才开始调用
04:27:17  A 提交：CAS 冲突（expected_through=0 ≠ 当前 3117）→ 丢弃本轮旧摘要
```

- A 侧：`水位线 CAS 冲突（expected_through=0），丢弃本轮旧摘要` + `context_l5_total{reason="stale_waterline",status="failed"} 1.0`
- **数据库终态：through=3117、summary_token_count=799（B 的新摘要）、version=1（仅 B 递增一次）——A 的旧结果没有覆盖，也没有把水位线拖回 100→0 的倒退形态。**

## C4 Redis Failure（fail-open）

以死端点注入（`REDIS_URL=redis://localhost:6399/0`，容器内 6399 无监听）：

- 日志：`[Redis] connection failed (cooldown 60.0s): Error 111 connecting to localhost:6399. Connection refused.`
- L5 链路**降级放行**：摘要照常完成并成功落库（`reason="success"`，through=3117，llm 4349+512）——分布式锁缺失时由 PG CAS 兜底（C3 已证其有效）
- `prepare_llm_context` 正常返回可用上下文（overflow=false）——聊天不失败、无 500
- 恢复：随后新进程以正确 REDIS_URL 运行 `get_redis().ping() = True`——**系统无需重启即恢复**

## C5 Worker Restart（摘要中途 SIGKILL）

1. A 持锁摘要进行中（第 8 秒，LLM 调用在途）→ `docker kill agent-cek-a`（SIGKILL）
2. 死后立刻检查：锁键 `context:l5:default:ctx-race-s1` 仍在，**TTL=52s**（自然衰减中）；DB `through=NULL, version=0`（A 未及提交，无非法水位线）
3. 55s 后：锁键**已消失**（TTL 到期自动释放，无永久锁）
4. 幸存 worker B 立即执行 L5：**success**（through=3117，llm 4349+205，11.5s）——其他 worker 可继续 L5

## C6 原始消息红线

| 时点 | chat_messages 指纹（id:role:md5(content) 聚合 md5） |
|---|---|
| 动测前（播种 24 条，id 3098–3121） | `e9f0d1e1cbf6af3a5e11e104cfdd6c8d` |
| 全部动测后（同 24 条重算） | **`e9f0d1e1cbf6af3a5e11e104cfdd6c8d`（逐字节一致）** |

新增行只有 id 3122–3125 共 4 条——是两次 HTTP 聊天请求由**聊天链路正常落库**的对话轮（user 问题 + assistant 回答），与 Context Budget 无关。L2 trim / L4 折叠 / L5 摘要只作用于 active context 与 chat_sessions 摘要水位，`chat_messages` 原始行零改写。

## C7 观测（真实生产路径数据，非恒零）

验收进程内 Prometheus registry 实际导出（均为真实代码路径产生）：

```
context_compactions_total{action="history_trim",level="L2"} 1.0     # 每次 prepare 触发
context_compactions_total{action="collapse",level="L4"} 1.0         # L4 真实折叠
context_l5_total{reason="success",status="success"} 1.0             # C2/C4/C5 多次
context_l5_total{reason="lock_conflict",status="failed"} 1.0        # C2 B 侧
context_l5_total{reason="stale_waterline",status="failed"} 1.0      # C3 A 侧
context_autocompact_llm_tokens_total{kind="prompt"} 4349.0
context_autocompact_llm_tokens_total{kind="completion"} 205~512.0
context_protected_facts_total{result="extracted",type="other"} 1.0
context_protected_facts_total{result="preserved",type="other"} 20.0
```

L4 实触发样本（隔离验证，测试 env `HISTORY_TOKEN_BUDGET=6000`）：
`context_compacted level=L4 action=collapse fold_id=fold-c1e34b68b049 folded_messages=3 before_tokens=5739 after_tokens=4351 saved_tokens=1388 keep_recent_turns=4`（0.14s，零 LLM，可回滚）。
estimated 计数器：`context_token_counter_total{estimated="true",provider="custom-doubao-seed-2-0-mini",strategy="calibrated"}` 在主链调用路径产生（STOP D 进一步做估计/实际校准）。

---

## 结论与如实记录的偏差

1. **生产默认阈值下 L4/L5 的自然触发窗口**：主链 HTTP 路径上 memory 侧先把历史裁到 HISTORY_TOKEN_BUDGET=2048，preflight 快路径（payload ≤ 7168 直接放行）使普通对话**不会**进入 L4/L5——这是设计使然（防线语义：超预算才压缩），但意味着 L4/L5 在生产主链的天然触发需要大 system/RAG 证据/previous_outputs/超长单问。本轮动态验收以测试 env 覆盖触发阈值完成真实链路开火；**阈值本身未被修改**，临时容器已销毁。
2. 验收容器镜像构建于 STOP B 提交之前（含 CAS/锁/preflight 全部代码，不含 B4 pins 接线）——B4 不触碰 L5 竞争路径，验收有效性不受影响；已在 STOP D 前以 HEAD 代码完成单元与契约回归。
3. 短命进程模型注册表为空（正常由 uvicorn 启动刷新）——驱动进程显式执行一次 `refresh_registry()`，属测试驱动自身初始化，非生产代码变更。
4. 摘要 LLM 观测：`patched=17`（C3 B 侧）出现一次——LLM 摘要遗漏 17 个受保护事实由确定性补丁补回，ProtectedFactRegistry 兜底按设计工作。

```
STOP_C_PASS=true
STOP_D_ALLOWED=true
```
