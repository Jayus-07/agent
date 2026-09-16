# 动态配置 Lite 与 API Key 多 Key 化方案

> 2026-09-16 起草。背景：安全运营页（commit `195be08`）落地后，剩余两个缺口为
> 灰度开关一键切换与 API Key 多 Key 化。本文给出前者的小步实现方案（lite 版）
> 与后者的独立立项设计。

## 一、灰度开关动态化（Lite 版，几小时量级）

### 1.1 现状与边界

三个开关目前全部走 env + 重启：

| 开关 | 取值 | 读取位置 | 归属层 |
|---|---|---|---|
| `JWT_SESSION_GUARD_MODE` | off / audit(默认) / enforce | `app/api/middleware/auth.py` | app 进程 |
| `SENSITIVE_API_GUARD_MODE` | enforce(默认) / audit | `app/api/deps.py`、`app/api/routes/observability.py` | app 进程 |
| `GATEWAY_SESSION_CHECK` | audit / enforce | APISIX 容器部署层 env，app 进程读不到 | 网关（部署层） |

**本期范围只覆盖前两个 app 进程开关。** 网关开关属部署层 env，动态化需改
APISIX 侧读取方式，不在 lite 范围（overview 接口继续返回 null 不猜值，现状不变）。

### 1.2 设计要点（企业做法的最小不变量）

企业级配置中心（Nacos/Apollo/Consul 等）的核心不变量是四条；lite 版全部拿到，
但不引入任何新中间件组件：

1. **开关存 DB 而非 env**：新建 `sys_config` 表；env 值降级为兜底默认值。
2. **免重启生效**：进程内 TTL 缓存（10~30s）；可选 Redis pub/sub 做即时失效。
3. **变更留审计**：复用现有 audit 机制记录 谁/何时/从什么值改成什么值；旧值
   保留即可一键回滚（把 value 写回去就是回滚）。
4. **权限收口**：写接口挂 `require_admin_user`，service 凭据通道不开放（与
   `/sys/security/*` 同一策略）。

不采用 Nacos 的理由：仅 2 个开关、单实例后端，Nacos 的多服务分发/服务发现
能力完全用不上，且 Python SDK 非官方一等公民、引入独立 Java 组件的运维成本
远超收益。待后端拆多服务/多实例时再评估配置中心。

### 1.3 数据模型

```sql
CREATE TABLE sys_config (
    key         VARCHAR(64)  PRIMARY KEY,   -- 如 jwt_session_guard_mode
    value       VARCHAR(128) NOT NULL,
    description VARCHAR(256),
    updated_by  VARCHAR(64),                -- 操作人（审计用）
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

合法值校验放在服务层（白名单，如 `jwt_session_guard_mode ∈ {off, audit, enforce}`），
不接受任意写入。

### 1.4 代码改造点

- 新增 `app/services/sys_config.py`：
  - `get(key) -> str`：先查进程内缓存（带 TTL，默认 15s），miss 落 DB，
    DB 无记录回退 `os.getenv(key_upper, default)`（env 兜底语义不变）。
  - `set(key, value, operator)`：白名单校验 → 写 DB → 主动失效缓存
    （可选：publish 到 Redis `sys_config:changed` 频道，进程订阅后即时清缓存）。
- 改 `middleware/auth.py` 的 `JWT_SESSION_GUARD_MODE` 读取、`deps.py` 与
  `observability.py` 的 `SENSITIVE_API_GUARD_MODE` 读取：从 `os.getenv` 改为
  `sys_config.get`。**冷启动/Redis/DB 异常时必须回退 env 兜底值，不能 fail-open**
  ——守卫读取失败按 `enforce` 或上次已知值处理，宁可误拦不可漏放。
- 新增 admin 路由（挂 `require_admin_user`，与 `/sys/security/*` 同组）：
  - `GET  /sys/config`：列出全部配置项（含当前生效值、来源 db/env-default）。
  - `PUT  /sys/config/{key}`：改值，写审计。
- 前端 `/security` 页开关状态卡：状态值后加「一键切换」按钮（三态枚举切换），
  调 PUT 后本地刷新。仅改 `frontend-admin/src/app/security/page.tsx` 一处。

### 1.5 测试

- 单测：白名单校验、TTL 缓存过期、DB 异常回退 env、PUT 审计落库、
  viewer/service 通道 403。
- 回归：`test_security_ops.py`、`test_session_guard_and_actor_kind.py`、
  `test_gateway_auth_metrics.py`（monkeypatch env 的用例需兼容：monkeypatch
  改为清空 sys_config 表记录或提供测试态注入点）。

## 二、API Key 多 Key 化（独立立项，不进本轮）

### 2.1 现状

`app/api/middleware/auth.py`：单一 `API_KEY` env 常量、`secrets.compare_digest`
常量时间比较、fail-closed。仅支持一个服务级凭据，无轮换/吊销/归属概念。

### 2.2 企业标准设计

**数据模型 `api_keys`：**

| 字段 | 说明 |
|---|---|
| `id` / `name` / `owner` | 标识与归属 |
| `key_hash` | SHA-256(明文)，库中**不存明文** |
| `key_prefix` | 明文前缀（如 `sk-ab12…`），供界面识别 |
| `scopes` | 按 Key 限权（JSON 数组，最小权限） |
| `status` | active / grace（宽限） / revoked |
| `expires_at` / `last_used_at` | 有效期与使用追踪 |
| `created_by` / `created_at` | 签发审计 |

**关键规则：**

- **明文只在签发响应中展示一次**，之后任何接口不可再取。
- **中间件查找路径**：`X-API-Key` → SHA-256 → 先查 Redis 缓存 → miss 落 DB →
  命中且 `status=active`（或 grace）且未过期 → 放行并异步更新 `last_used_at`。
- **吊销即时生效**：置 `status=revoked` + 删 Redis 缓存条目，无需重启。
- **轮换不停服**：签发新 Key（active）→ 旧 Key 转 grace 并设宽限截止时间 →
  调用方迁移 → 到期自动停用。新旧并行期间两把 Key 都能过。
- **env `API_KEY` 保留为应急 root key**：中间件先查表、表查不到再走原
  常量时间比较（过渡期双通道；表内 Key 全灭时留一条救援通道）。
- **配套（可二期分期）**：按 Key 限流、用量统计（复用访问日志按 Key 聚合）。

### 2.3 牵动面（立项时需一并评估）

- 网关（APISIX）侧 Key 分发与转发链路；
- 管理端点：签发/列表/吊销/轮换（`require_admin_user`）+ 前端管理页；
- 缓存一致性：吊销后的缓存失效窗口；
- 安全基线：hash 不可逆、响应脱敏（只回 prefix）、限流防爆破。

### 2.4 排期建议

P0：表 + 中间件双通道（表优先、env root 兜底）+ 签发/吊销端点。
P1：grace 轮换 + Redis 缓存 + 前端管理页。
P2：按 Key 限流 + 用量看板。
