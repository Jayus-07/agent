# 认证与授权架构

> 本文是当前认证链路的开发导航，不记录实施进度。身份头字段、网关注入和上游信任边界以[身份头协议](../contracts/identity-header-protocol.md)为准；当前行为以代码、配置与测试为准。

## 请求身份链路

1. APISIX 入口由 `apisix/plugins/gateway-auth.lua` 执行身份验证与头部治理；客户端传入的身份头不得作为可信身份。
2. 后端由 `backend/config/auth.py` 选择 `legacy`、`header` 或 `strict` 身份来源模式；生产启动校验不允许使用可伪造的 legacy 请求体身份。
3. 路由通过 `backend/app/api/identity.py` 统一解析请求身份，资源级授权和租户范围仍由相应服务/仓储执行。
4. API Key 是服务级认证凭据，不等同于用户身份，也不替代资源级授权。

## 会话与角色

本地登录、刷新、登出与管理接口位于 `backend/app/api/routes/auth_local.py`。平台角色和客服坐席角色分属不同权限域；网关和后端均按各自允许的角色集合解释，不把客服角色提升为平台角色。

用户与租户标识必须来自服务端验证后的请求上下文。对用户、会话或业务资源的读取和写入均须执行资源级授权；不得信任请求体、查询参数或客户端自带的身份/租户头。

## 修改与验证

- 修改网关注入字段、验证策略或失败语义：同步检查 `docs/contracts/identity-header-protocol.md`、APISIX 插件和后端身份解析。
- 修改登录/刷新/登出或角色管理：核对 `backend/app/api/routes/auth_local.py` 及其 session/RBAC 测试。
- 网关配置和服务入口见 `docs/architecture/system-overview.md`、`docs/operations/commands.md`。
- 网关鉴权探针：`scripts/smoke_gateway_auth.sh`；相关测试位于 `backend/tests/api/`、`backend/tests/security/`。
