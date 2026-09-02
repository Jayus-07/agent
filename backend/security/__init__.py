"""security — Agent 请求链路安全层

三层防护边界（单向依赖，职责不重叠）：

  User Query
    ↓
  Input Guard（backend/security/input_guard）— 本包当前实现
    输入侧准入：格式检查 / Prompt Injection / 有害请求 / 业务范围 /
    敏感域预判。产出 GuardResult（allow/clarify/block/degrade）。
    注意：Input Guard 不承担真正的权限控制，只做
    domain / sensitivity / risk 预判（GuardResult.needs_permission）。
    ↓
  Router → Planner → Supervisor
    ↓
  Tool Guard（规划中）— 在 Tool 执行层做二次权限与参数校验
    现有事实上的 Tool 层防护：backend/sql（6 层 SQL 校验 + 行级安全 +
    敏感列拦截）、backend/tools/url_guard（SSRF）。
    即使 Input Guard 判定 ALLOW，DELETE/UPDATE/发邮件/外部 API 等
    有副作用操作必须在 Tool 层再次校验，禁止仅凭输入侧判定执行。
    ↓
  RAG / SQL / Email / Web / Other Tools
"""
