# 归档：Java 网关时代的登录链路残留（2026-09-15）

## 为什么归档

2026-09-15 09:02~14:50 的工作区里有一批未提交改动，经溯源属于 **Java 网关时代**的登录工作
（提交 `3056d17 | 09-15 04:36 | feat: 登录链路接入网关` 那条线），而 Java 组件已在
**8.5 小时后的** `5438813 | 09-15 23:22 | refactor!: 项目分离——移除 Java 组件，py 自建用户体系`
中被移除。这批改动因此成为**孤儿**：它的前提（8080 Java 网关 + auth-service/system-service）已不存在。

本目录保存原始内容，仅供追溯，**不要直接恢复**。

## 溯源证据

| 证据 | 内容 |
|---|---|
| 时间线 | `3056d17`(04:36, 登录链路接入网关) → `84ffe87`(08:15, 任务模式) → **本批 mtime 09:02~14:50** → `5438813`(23:22, 移除 Java) |
| 内容自证 | `next.config.js` 新增注释写明「→ Java 网关(8080)：JWT 登录/注册走 auth-service(8006)/system-service(8002)」 |
| 代码同源 | `AuthGate.tsx` 复用 `3056d17` 引入的 `@/lib/auth` 的 sessionStorage 约定 |
| 排除 auth 会话 | auth 会话产出为 `73c67c9`(23:33) 起的后端 `auth_local.py`，不含这些前端文件 |

## 归档内容

| 文件 | 原始改动 | 处置 |
|---|---|---|
| `2026-09-15-next.config.java-rewrite.patch` | `/api/auth/**`、`/api/sys/**` → `AUTH_GATEWAY_URL \|\| http://localhost:8080` | **已回退**。理由：compose 已无 8080/Java 服务；且当前已提交的通用 rewrite `/api/:path*` → `:8000/:path*` 已能正确映射到 py 端点 |
| `AuthGate.tsx.orig` | 全局客户端路由守卫（54 行） | **已认领进主干**（与 py 契约兼容），见下 |

## 为什么通用 rewrite 就够（无需 Java 专用规则）

py 侧 `backend/app/api/routes/auth_local.py` 定义 `APIRouter(prefix="/auth")` + `APIRouter(prefix="/sys")`，
挂载于 `backend/app/api/router.py:41-42`。前端 `lib/auth.ts` 调 `/api/auth/login`，
经通用 rewrite `/api/:path*` → `http://localhost:8000/:path*` 得 `:8000/auth/login` —— 精确命中。
`/api/sys/users/register` → `:8000/sys/users/register` 同理。

即：**Java 专用 rewrite 是多余且有害的**，保留会让登录打到死端口 8080。

## 后续

`AuthGate.tsx` 已作为 P1 基线提交，其测试义务随 P1-5（`RoleGate`）一并补齐；
登录前端（`src/app/login/page.tsx`、`src/lib/auth.ts`）自 `3056d17` 起即为已提交状态，与 py 后端契约成对。
