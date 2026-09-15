# APISIX 迁移最终报告（B0~B4 汇总）

> 日期：2026-09-15 · 迁移提交：`43fc2a9` · 版本 tag：`apisix-gateway-b3`
> 结果：**迁移成功。前端认证/业务流量已切至 APISIX(9080)，Java SCG(8080) 原样保留为回滚路径。**

## 1. 各阶段结果

| 阶段 | 结果 | 证据 |
|---|---|---|
| B0 基线 | ✅ | 审计报告 `docs/gateway-apisix-audit-report.md`；基线 `.workbuddy/baseline_fastapi_direct.json` |
| B1 最小部署 | ✅ | 9080 基线比对除已知 404 语义差异外全绿；SSE 经网关逐块 |
| B2 认证硬闸门 | ✅ | 12/12 场景矩阵 + 3 类 Redis 故障 fail-closed + 20 并发拒连风暴 |
| B3 专项测试 | ✅ | SSE 对比 4/4 + 并发 3 流 + 断连；上传 6/6；只读 SQL；上游故障瞬时失败无重试；重启/回滚/双副本 |
| B4 切流 | ✅ | 提交 + tag；发布/回滚演练（tag 检出→重启→恢复 10.8s）；前端切流双向实证；24h 观察自动化已挂 |

## 2. 最终运行拓扑

```
浏览器 → Next.js(3100) 同源代理
           ├─ /api/auth/*、/api/sys/* → APISIX(9080) → auth-service(8006)/system-service(容器)
           └─ 其余 /api/*            → APISIX(9080) → app 容器(FastAPI)
/internal/* 不经网关（X-Internal-Token，容器网络直连）
Java SCG(8080)：原样保留，回滚路径
```

- 切流方式：前端启动环境变量 `AUTH_GATEWAY_URL=http://127.0.0.1:9080`（dev 模式启动时生效；standalone 构建形态需重建镜像——B0 审计 R2）
- **回滚方式：把该环境变量改回 `http://localhost:8080` 并重启前端即回到 SCG**（已双向实证：9080 → APISIX 命中 +1；8080 → APISIX 零命中）

## 3. 验收清单勾验

P0 安全与兼容：
- [x] Java 零改动、FastAPI 业务代码零改动（git 范围核对）
- [x] 伪造身份头剥离 + 合法 JWT 注入（矩阵场景 1/10/12）
- [x] 认证失败符合基线合同（reason 枚举 + `未认证：` 判别符）
- [x] 业务权限仍在 FastAPI（X-API-Key 中间件行为不变，场景 12）
- [x] `/internal/*` 不经 APISIX
- [x] 网关不重试非幂等请求（上游停机 → 503 瞬时失败 5.4s 单次超时）
- [ ] 外网不可达四上游端口：本机为开发环境（回环绑定已验证），生产部署时须按 §2.2 网络层收口

P0 功能：
- [x] 全部实际路由转发正确（基线比对，唯一差异为非 /api 路径的网关自身 404）
- [x] SSE 事件完整/顺序一致/无缓冲（直连 vs 网关逐块对比）
- [x] 断连不崩溃；长连接语义已文档化
- [x] 上传限制与迁移前一致（50MB / 413 双保险）
- [x] Trace ID 可关联（X-Trace-Id 透传复用）
- [x] 现有前端零代码修改（仅启动 env 切换）

P1 运维：
- [x] 配置进 git（提交 43fc2a9 + tag）
- [x] 发布前校验（坏配置 → 加载失败，门禁实证）
- [x] 回滚演练（tag 检出 → 重启 → 恢复，10.8s）
- [x] 双副本一致性（9080/9082 行为一致；生产 LB 编排留部署时执行）
- [x] Prometheus 指标 + 观察自动化（每小时巡检 → `.workbuddy/b4_watch.log`，至 09-16 15:00）

## 4. 已知问题与遗留

| # | 问题 | 影响 | 处置 |
|---|---|---|---|
| 1 | 宿主机 `127.0.0.1:8000` 有 Docker 残留僵尸绑定（pid 21172，无法杀） | 宿主机裸跑 uvicorn 无法绑 8000；不影响网关链路（容器名直连） | 择机重启 Docker Desktop |
| 2 | system-service（oa-auth-system）未常驻 | `/api/sys/*` 转发配置就绪未实测 | 启动 oa-auth 栈后补测 |
| 3 | app 容器重启后 APISIX 有 ~1-2min 502 窗口 | 容器重启期短暂 502 | 已配 `dns_resolver_valid: 5` 缩短；runbook：`docker compose restart app apisix` |
| 4 | Docker 引擎当日曾崩溃重启（连带 app/auth 容器 137） | 环境级，非迁移引入 | 已恢复；观察自动化将持续盯守 |
| 5 | 前端 standalone 构建形态切流需重建镜像 | 仅生产构建形态 | 部署时按 §2 流程 |

## 5. 交付物索引

| 交付物 | 位置 |
|---|---|
| APISIX 配置 | `apisix/apisix.yaml`、`apisix/config.yaml` |
| 认证插件 | `apisix/plugins/gateway-auth.lua`、`auth_blacklist.lua` |
| compose 变更 | `docker-compose.yml`（apisix 服务） |
| 前端切换 | 启动 env `AUTH_GATEWAY_URL`（运行态，无代码变更） |
| 基线/矩阵/专项脚本 | `scripts/gateway_baseline_check.py`、`gateway_auth_matrix.py`、`gateway_b3_special.py` |
| 测试报告 | `.workbuddy/baseline_fastapi_direct.json`、`gateway_auth_matrix_report.json`、`matrix_redis_*.json`、`b3_*.json` |
| 身份头协议 | `docs/contracts/identity-header-protocol.md` |
| 审计/方案 | `docs/gateway-apisix-audit-report.md`、`docs/gateway-apisix-migration-plan.md`（含部署/发布/回滚 §8） |
| 观察期 | 自动化「APISIX 网关 24h 观察期巡检」→ `.workbuddy/b4_watch.log`（至 09-16 15:00） |
