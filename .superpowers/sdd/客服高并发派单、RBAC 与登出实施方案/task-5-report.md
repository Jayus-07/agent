# Task 5 实施报告（P5 在线状态与多实例 WebSocket）

## 交付范围

两个提交：

1. `00466df feat(cs): add redis agent websocket tickets` —— P5 主体实现。
2. 本次复核修正批次（工作树既有未提交改动，经本轮复跑验证后提交）—— 关闭复核发现的 4 个缺口。

## 冻结契约落实情况

| 契约 | 落点 | 状态 |
| --- | --- | --- |
| ticket 60s TTL、单次核销、原子 GET+DEL | `realtime.py:65` `TICKET_TTL_SECONDS=60`；`redeem_ticket_claims()` 优先 `redis.getdel()`，无该命令时回落等价 Lua `_GETDEL_LUA`（`get`+`del` 在同一脚本内原子执行） | 满足；无进程内 ticket 回落路径 |
| ticket 绑定 `agent_id` + `tenant_id` | `issue_ticket(agent_id=..., tenant_id=...)` 写 Redis payload；`redeem_ticket_claims()` 返回服务端绑定身份，浏览器不提交可信 `agent_id` | 满足 |
| ticket 端点拒绝 API-Key 通道 | `cs_admin.py:_resolve_ws_agent_identity()` 先判 `X-Auth-Type: api-key` → 403；此后才 `resolve_identity` 并 `cs_agents.auth_user_id` 反查 | 本轮修正项 |
| Redis 不可用 fail-closed | 签发失败不落库、WS 无效 ticket 走 4401、Redis 不可用握手后 1013；无内存成功替代 | 满足 |
| presence 45s TTL / heartbeat 15s | `realtime.py:66 PRESENCE_TTL_SECONDS=45`；`cs_agent_ws.py:21 _HEARTBEAT_INTERVAL_SECONDS=15`；客户端 `ping` 同样刷新 | 满足 |
| 定向广播 | `publish()` 读 envelope `target_agent_id`，连接集合按 `(tenant_id, agent_id)` 匹配，仅推同租户同客服；未定向事件保持广播 | 满足 |
| presence key 无拼接碰撞 | `presence_key()` 对 tenant/agent 两段分别 `quote(..., safe='')` 后拼接 —— 租户校验允许 `:`，未编码时 `a:b` + `c` 与 `a` + `b:c` 会撞同一 key | 本轮修正项 |
| 不阻塞事件循环 | `cs_admin.issue_agent_ws_ticket`、`cs_agent_ws` 的核销/presence 刷新全部经 `asyncio.to_thread` 调用（Redis 客户端为同步实现） | 本轮修正项 |
| 网关注入租户头 | `apisix/plugins/gateway-auth.lua` 把 JWT `tenant_id` claim 注入 `X-Tenant-Id`，并加入伪造头剥离清单（八头 → 九头） | 本轮修正项 |

## 红灯/绿灯记录

复核修正批次在提交前复跑：

```text
cd backend && D:/Python/python.exe -m pytest tests/customer_service/test_realtime_ticket.py tests/api/test_cs_agent_ws_ticket.py -q --no-cov
13 passed in 21.16s

cd backend && D:/Python/python.exe -m pytest tests/api/test_cs_dispatch.py tests/customer_service/test_p4_handoff_ingress.py tests/api/test_gateway_tenant_contract.py -q --no-cov
13 passed, 5 skipped in 24.33s

D:/Python/python.exe -m compileall -q backend/customer_service/realtime.py backend/app/api/routes/cs_agent_ws.py backend/app/api/routes/cs_admin.py
OK

D:/Python/python.exe -m ruff check <上述文件 + tests/api/test_cs_agent_ws_ticket.py>
All checks passed!

git diff --check
（无输出，通过）
```

新增回归用例 `test_ws_ticket_rejects_unbound_api_key_channel`：`X-Auth-Type: api-key` + `X-API-Key` 直接请求 ticket 端点，断言 403，锁定「全局服务 Key 不得冒充坐席身份」。

注：`tests/customer_service/test_realtime.py` 在本分支不存在（brief 的「若存在」条件不成立），未执行。

## 未验证项（部署门槛，不宣称通过）

- 真实 Redis 双实例「API-A 签发 / API-B 核销」的端到端验收未在本环境跑（需要两个 API 实例 + 真实 Redis）；单元级证据由 `test_realtime_ticket.py` 的共享 fake Redis 提供。
- APISIX 侧 `X-Tenant-Id` 注入只有 Lua 静态修改，未起网关做真实请求验证；`test_gateway_tenant_contract.py` 的 5 个跳过用例属同类门槛。
- `redis.asyncio` 通道未引入：现有 `backend.infra.redis.client.get_redis()` 是同步客户端，故用 `asyncio.to_thread` 包装；若后续引入异步客户端，此处可简化。
- P5 不实现 dispatcher/assignment，也不改变 handoff 状态权威；用户轮询接口保持。

## 改动文件

- `backend/customer_service/realtime.py`
- `backend/app/api/routes/cs_admin.py`
- `backend/app/api/routes/cs_agent_ws.py`
- `backend/tests/api/test_cs_agent_ws_ticket.py`
- `apisix/plugins/gateway-auth.lua`
- 本报告
