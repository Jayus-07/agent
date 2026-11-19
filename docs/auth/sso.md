# 企业扫码登录

## 登录和授权规则

飞书、企业微信或钉钉只负责验证“当前扫码者是谁”。系统随后必须在同一租户内找到管理员事先绑定的本地账号，再检查账号状态、当前入口准入和本地角色，并复用本地登录的 JWT、会话、刷新 Cookie 与客服角色处理。

- 不按手机号自动匹配账号，不自动创建用户，也不从外部部门、职位或职级推导本地角色。
- 外部身份未绑定、本地账号停用、企业标识不匹配或授权码/state 失效时拒绝登录。
- SSO 用户仍受本地 `viewer/editor/admin/super_admin` 和客服坐席准入规则约束。
- `must_change_password` 账号须先完成本地首次改密，SSO 不绕过该要求。
- 应用密钥只以 `SECRETS_ENCRYPTION_KEY` 加密后写入配置表；不要写入日志或客户端响应。

## 部署前准备

1. 在目标环境应用 `backend/sql/migrations/151_auth_sso.sql`。
2. 为服务端配置稳定的 `SECRETS_ENCRYPTION_KEY`。缺失时管理接口会拒绝保存凭证。
3. 在企业助手前端域名配置回调地址：`https://<企业助手域名>/login/sso/callback?provider=<feishu|wecom|dingtalk>`。平台后台与本服务配置的回调地址必须完全一致。
4. 管理员调用 `PUT /api/sys/sso/providers/{provider}` 配置每个企业自己的应用凭证，再通过 `POST /api/sys/sso/identities` 显式绑定外部身份与本地账号。读取配置和绑定列表分别使用 `GET /api/sys/sso/providers`、`GET /api/sys/sso/identities`；解绑使用 `DELETE /api/sys/sso/identities/{id}`。这些接口仅允许真实本地平台管理员，并按可信租户隔离。
5. 登录页展示三个平台及各自配置状态；只有当前租户已启用的平台可点击。发起登录时，浏览器生成一次性随机值并保存在当前标签页的 `sessionStorage`，服务端只把它的 SHA-256 摘要与一次性 `state` 一起存入短时状态。回调必须同时通过 `state` 和当前浏览器随机值校验，再用授权码换取身份并签发本地会话，避免把其他浏览器发起的回调链接直接用于本机登录。

配置请求示例：

```json
{
  "client_id": "企业应用 ID；企业微信填写 CorpID",
  "client_secret": "企业应用密钥",
  "external_org_id": "该企业在对应平台的组织 ID",
  "redirect_uri": "https://assistant.example.com/login/sso/callback?provider=feishu",
  "agent_id": null,
  "enabled": true
}
```

更新时 `client_secret` 可以省略以保留现有密钥；管理接口不会回显明文或密文。首次配置必须提供密钥。

## 平台差异与权限

- **飞书**：网页登录授权使用 OAuth 2.0；请求用户基础身份读取权限，服务端读取 `tenant_key` 和 `open_id`。授权请求使用 PKCE S256。部门、职位和手机号不用于平台角色授权。
- **企业微信**：二维码扫码登录要求企业自建应用具备“企业微信授权登录”能力，应用回调域需与站点域名一致。该扫码方式仅适用于企业内部，不适用于第三方服务商代登录；每家企业需管理员创建/配置自己的应用。登录仅使用组织标识和 `UserId`。
- **钉钉**：网页登录 OAuth 获取授权码和用户令牌，组织范围请求 `corpid`，读取登录者基础标识需要申请 `Contact.User.Read`。不申请手机号权限，不同步部门或职位。

## 当前部署边界

本机 APISIX 的 `/api/auth/*` 路由将可信租户固定注入为 `default`，所以当前开发入口只会读取 `default` 租户的 SSO 配置。要给多个平台租户开放登录，必须先按受控域名/站点配置把登录请求映射到可信租户；不能把浏览器自行发送的 `X-Tenant-Id` 当作租户选择依据。

本次提供了配置和绑定 API，没有新增管理员图形配置页，也没有真实企业应用凭证。因此代码和离线测试通过不代表扫码已在真实企业环境验收。上线前需由企业管理员提供应用 ID、密钥、组织 ID、回调域名和测试成员，并验证授权、账号绑定、本地角色、停用账号和登出/刷新流程。
