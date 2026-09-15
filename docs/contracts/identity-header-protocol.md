# 身份头协议（网关 → 上游服务共享契约）

> 版本：1.0（2026-09-15，B2）
> 适用范围：APISIX 独立入口（9080）与所有 Python 上游服务；与 Java SCG `AuthenticationGlobalFilter` 行为对齐。
> 变更流程：本文件是网关与上游的共同依赖，任何修改走 PR 并需双方（网关维护者 + FastAPI 维护者）确认。

## 1. 身份头清单

| 头 | 含义 | 来源 | 允许为空 | 注入规则 |
|---|---|---|---|---|
| `X-Auth-Type` | 认证方式 | 网关注入 | 否 | `jwt`（JWT 验证通过）/ `api-key`（请求自带 X-API-Key，网关只打标不校验）/ `anonymous`（guest 策略注入） |
| `X-User-Id` | 当前用户标识 | JWT claim `userId`（数字会转字符串） | 否（jwt 通道必有） | 验证全通过后才注入；guest 通道固定 `anonymous` |
| `X-User-Name` | 当前用户名称 | JWT claim `username` | 是 | 有则注入，无则不带该头 |
| `X-User-Dept` | 用户部门上下文（dept_code） | JWT claim `dept` | 是 | 有则注入。**仅身份上下文，不是权限结论**——部门隔离、资源权限、SQL 权限、MCP 工具授权一律由业务服务校验 |
| `X-Trace-Id` | 全链路请求标识 | 透传复用或网关生成 | 否 | **不剥离**：入站有值则沿用（SCG 同语义），无则网关生成 UTC时间- workerPID- 随机 |

## 2. 伪造头处理（P0）

- 入站请求携带 `X-Auth-Type / X-User-Id / X-User-Name / X-User-Dept` 时，**网关在白名单判定之前无条件剥离**（remove 语义，非覆盖追加）。
- 客户端在任何路径（含白名单路径）都无法通过伪造身份头进入可信上下文。
- `X-Trace-Id` 不在剥离清单内（链路关联用途，无信任语义）。

## 3. 网关注入规则

- 仅当：黑名单未命中 + 签名/issuer/exp（60s skew）/userId/type=access 全部通过后，才注入四头。
- 注入前先剥离 → 不存在"伪造值与真实值并存"的中间态。
- 策略模式（`GATEWAY_AUTH_MODE`）：`shadow`/`open` 全部校验但**不注入不拦截**（只记 `gateway_auth_would_deny_total`）；`guest` 无凭据注入 `X-Auth-Type: anonymous, X-User-Id: anonymous`；`enforce` 按上述全量行为。

## 4. 上游消费规则（FastAPI）

- `IDENTITY_SOURCE=header|strict` 模式下只信任本协议头（`backend/config/auth.py`）；请求体中的身份字段一律忽略。
- header/strict 模式的信任边界 = 网络边界：上游端口（8000 等）禁止外网可达，否则任何人可伪造身份头直连。
- `strict` 模式：头缺席（未认证）由路由按语义抛 401。

## 5. 失败响应合同（网关 401）

```json
{"error": "Unauthorized", "detail": "未认证：<reason>"}
```
- Header：`X-Trace-Id: <trace>`（响应头，供排障关联）。
- reason 枚举（与 Java HmacJwtVerifier/过滤器对齐）：`no-credential` / `blacklist` / `blacklist-timeout` / `blacklist-unavailable` / `malformed` / `signature` / `expired` / `invalid` / `issuer` / `missing-user-id` / `token-type-mismatch` / `keystore-unavailable`。
- 判别符：网关自身 401 的 detail 恒以 `未认证：` 开头——与下游服务自身的 401（如 FastAPI `无效或缺失 X-API-Key`）可区分。
- Redis 故障（`blacklist-timeout` / `blacklist-unavailable`）一律 401 fail-closed，**不得被误判为"用户未登录"**（reason 可区分）。

## 6. 安全红线

- 网关与插件日志禁止输出 token、JWT secret、Cookie、X-API-Key 明文。
- 黑名单 Redis 只读（EXISTS）；写入仅归 Java auth-service。
- FastAPI 的 X-API-Key 服务级认证不在本协议范围内，网关不接管。
