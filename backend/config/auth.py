"""config/auth.py — app 侧身份来源模式机（P3，docs/auth/03 五之二）

网关（AuthenticationGlobalFilter）验完 JWT 后向下游注入身份头：

    X-Auth-Type: jwt | guest          # 认证方式
    X-User-Id / X-User-Name / X-User-Dept

app 侧不再自行验 JWT，按 IDENTITY_SOURCE 决定信谁：

    legacy = 显式 opt-in 的兼容模式：请求体 user_id 优先，TRUST_USER_HEADER=true
             时头次之。请求体身份可伪造，仅限本地直调调试；生产环境启动校验
             （config/startup.py）直接拒绝该模式。
    header = 网关权威：只认身份头，请求体身份字段一律忽略；
             未认证降级 guest（user_id=""）。
    strict = header + 未认证直接 401（由调用方据 auth_type 判断抛出）。

默认 header：网关 enforce/guest 模式验完 JWT 后注入身份头，前端所有流量
（dev 走 Next rewrite → APISIX:9080，生产同拓扑）都经网关进入。

信任边界即网络边界：header/strict 模式的前提是 8000 端口不对宿主机外
暴露（docker-compose 已收口 127.0.0.1），否则任何人都能伪造身份头。
"""
import os

# ── 网关注入契约（与 api-gateway .../AuthenticationGlobalFilter.java 对齐）──
AUTH_TYPE_HEADER = "X-Auth-Type"
USER_ID_HEADER = "X-User-Id"
USER_NAME_HEADER = "X-User-Name"
USER_DEPT_HEADER = "X-User-Dept"
# 2026-09-16 起：JWT roles claim（数组）经网关注入为逗号分隔串，
# resolve_operator_role 消费（prompts RBAC / 审批角色校验）。客户端不可伪造。
USER_ROLES_HEADER = "X-User-Roles"

_AUTH_MODES = ("legacy", "header", "strict")
IDENTITY_SOURCE: str = os.getenv("IDENTITY_SOURCE", "header").strip().lower()
if IDENTITY_SOURCE not in _AUTH_MODES:  # 防呆：写错模式宁可启动期兜回 header
    IDENTITY_SOURCE = "header"

# 旧开关（TRUST_USER_HEADER / USER_ID_HEADER 定义在 config/__init__.py），
# 仅 legacy 模式继续消费；header/strict 不再看它。


def identity_source() -> str:
    """当前身份来源模式（legacy/header/strict）。"""
    return IDENTITY_SOURCE
