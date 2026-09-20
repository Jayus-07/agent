-- gateway-auth.lua — APISIX 入口认证插件（B2）
--
-- 行为合同：1:1 平移 Java AuthenticationGlobalFilter（B0 审计 docs/gateway-apisix-audit-report.md §3）
--   ① 无条件剥离入站伪造身份头（九头：X-Auth-Type/X-User-Id/X-User-Name/X-User-Dept
--      /X-User-Roles/X-User-Permissions/X-Tenant-Id + X-Operator-Role/X-Operator-Id；
--      X-Operator-* 为 operator 身份族，B4 未开、py 不消费，
--      此处置为剥离是提前堵「客户端伪造 operator 头」的洞，与 X-User-* 同理；
--      X-Tenant-Id 由 JWT tenant_id claim 注入，客户端不可伪造）
--   ② X-Trace-Id 不剥离：有则透传复用，无则生成
--   ③ OPTIONS 预检放行（路由级白名单由路由配置承担：auth/sys 路由不挂本插件）
--   ④ 带 X-API-Key → 打标 X-Auth-Type: api-key 透传（服务级 Key 仍由 FastAPI 校验）
--   ⑤ Bearer 提取 → Redis 黑名单（EXISTS auth:blacklist:<token>，fail-closed，先于验签——SCG 顺序）
--   ⑥ 验签（HS256/384/512 按密钥长度，JJWT hmacShaKeyFor 语义；issuer；exp+skew；previous-secret 轮换）
--   ⑦ claim type=access（refresh 令牌拒绝）
--   ⑦.5 会话闸（方案 A，2026-09-16）：EXISTS auth:session:{userId}:{jti}，
--      缺失 = 已吊销；GATEWAY_SESSION_CHECK=off/audit/enforce（默认 audit），
--      Redis 故障 fail-closed（session-timeout/session-unavailable）
--   ⑧ 策略阶梯 open/shadow/guest/enforce（默认读 env GATEWAY_AUTH_MODE）
--   ⑨ 通过后注入 X-Auth-Type: jwt + X-User-Id/X-User-Name/X-User-Dept
--   ⑨.5 角色闸（正向清单 + 方法感知，403 role-insufficient；shadow 只记不拦）
--   ⑩ 401 体：{"error":"Unauthorized","detail":"未认证：<reason>"} + X-Trace-Id 头
--      （FastAPI 错误风格 + SCG 冒烟判别符 `未认证：` 兼顾）
--
-- 配置一律来自环境变量（apisix.yaml 是 Git 文件，密钥禁止落盘）：
--   JWT_SECRET / JWT_SECRET_PREVIOUS / JWT_ISSUER(默认 hongmeng-oa)
--   GATEWAY_AUTH_MODE(默认 enforce，fail-closed；观测复测显式设 shadow) / GATEWAY_AUTH_CLOCK_SKEW(默认 60)
--   AUTH_REDIS_HOST / AUTH_REDIS_PORT / AUTH_REDIS_PASSWORD
--   GATEWAY_AUTH_REDIS_CMD_TIMEOUT_MS(300) / GATEWAY_AUTH_REDIS_POOL_SIZE(20) / GATEWAY_AUTH_REDIS_POOL_IDLE_MS(10000)
--
-- 安全红线：本插件任何日志不得出现 token、secret、Cookie 明文。

local core            = require("apisix.core")
local jwt_lib         = require("resty.jwt")
local blacklist       = require("apisix.plugins.auth_blacklist")

local ngx_utctime     = ngx.utctime
local ngx_time        = ngx.time
local math_random     = math.random
local tostring        = tostring
local find            = string.find
local concat          = table.concat

-- 与 SCG AuthenticationGlobalFilter 一致的伪造头剥离清单（九头，不含 X-Trace-Id）
-- 含 X-Operator-Role / X-Operator-Id：operator 身份族，客户端不可伪造（B4 未开、py 不消费 X-Operator-*，
-- 此处置为剥离属提前防御；大小写变体无需单列——ngx.req.set_header 对头名大小写不敏感，会一并清除）
-- X-User-Roles/X-User-Permissions/X-Tenant-Id 由本插件注入（JWT claim → 逗号分隔/原值），
-- 同样禁止客户端伪造
local FORGED_HEADERS  = { "X-Auth-Type", "X-User-Id", "X-User-Name", "X-User-Dept", "X-User-Roles",
                          "X-User-Permissions", "X-Tenant-Id", "X-Operator-Role", "X-Operator-Id" }

local HEADER_AUTH_TYPE = "X-Auth-Type"
local HEADER_USER_ID   = "X-User-Id"
local HEADER_USER_NAME = "X-User-Name"
local HEADER_USER_DEPT = "X-User-Dept"
local HEADER_USER_ROLES = "X-User-Roles"
local HEADER_USER_PERMISSIONS = "X-User-Permissions"
local HEADER_TENANT_ID = "X-Tenant-Id"
local HEADER_TRACE_ID  = "X-Trace-Id"

-- 进程级配置缓存（init_worker 构建；必须先于 _M.access 声明，否则 access 引用全局 nil）
local CONF = nil

-- ── 工具 ──────────────────────────────────────────────────────

local function env(name, default)
    local v = os.getenv(name)
    if v == nil or v == "" then
        return default
    end
    return v
end

local function trace_id(ctx)
    local existing = core.request.header(ctx, HEADER_TRACE_ID)
    if existing and existing ~= "" then
        return existing  -- 透传复用（SCG 语义）
    end
    return ngx_utctime() .. "-" .. tostring(ngx.worker.pid()) .. "-" ..
           string.format("%04x", math_random(0, 65535))
end

-- JJWT Keys.hmacShaKeyFor 的算法选择语义（按密钥字节长度）
local function expected_alg(secret)
    local n = #secret
    if n >= 64 then return "HS512" end
    if n >= 48 then return "HS384" end
    if n >= 32 then return "HS256" end
    return nil
end

-- 可观测计数：经 prometheus 插件的 exporter 单例记自定义 counter，失败仅日志。
-- ⚠️ 3.13 接口（旧写法 require "apisix.plugins.prometheus.metrics" 在 3.13 不存在，
-- pcall 静默失败导致自定义指标从未生效——2026-09-16 实测修复）：
--   exporter.get_prometheus() 返回 lua-resty-prometheus 对象；
--   APISIX 初始化 prometheus 对象时带 metric_prefix="apisix_"，
--   故最终指标名为 apisix_gateway_auth_denied_total / apisix_gateway_auth_would_deny_total。
-- ⚠️ 必须惰性初始化（不能在 init_worker 注册）：APISIX 按 priority 降序执行各插件
--   init_worker，gateway-auth(2500) 先于 prometheus(500)——那时 exporter 单例还是 nil。
--   access 阶段首次计数时 prometheus 对象必已就绪（lua-resty-prometheus 的 counter
--   注册无相位限制，inc 经 parent._counter 回退到 init_worker 期建好的 worker 计数器）。
local METRICS = nil  -- {denied = counter, would_deny = counter}

local function init_metrics()
    local ok, exporter = pcall(require, "apisix.plugins.prometheus.exporter")
    if not ok or type(exporter) ~= "table" then
        return false
    end
    local ok2, prom = pcall(exporter.get_prometheus)
    if not ok2 or type(prom) ~= "table" then
        return false  -- prometheus 插件未启用/未就绪：保持 nil，下次再试
    end
    local ok3 = pcall(function()
        METRICS = {
            denied = prom:counter("gateway_auth_denied_total",
                                  "gateway-auth rejected requests",
                                  {"route", "reason"}),
            would_deny = prom:counter("gateway_auth_would_deny_total",
                                      "gateway-auth shadow mode would-reject",
                                      {"route", "reason"}),
        }
    end)
    if not ok3 then
        METRICS = nil
        core.log.warn("[gateway-auth] 自定义 counter 注册失败，降级为仅日志")
        return false
    end
    return true
end

local function metric(kind, route, reason)
    if not METRICS and not init_metrics() then
        return  -- 降级为仅日志（deny/would_deny 的 core.log.warn 仍在）
    end
    local m = METRICS[kind]
    if m then
        pcall(function() m:inc(1, {route or "unknown", reason or "unknown"}) end)
    end
end

-- 401/403（合同体：FastAPI 错误风格 + `未认证：` 判别符；403 用于角色不足）
local function deny(ctx, route, reason, count_it, status)
    if count_it then
        metric("denied", route, reason)
    end
    core.log.warn("[gateway-auth] deny route=", route, " reason=", reason, " status=", status or 401)
    -- 响应也带 X-Trace-Id，便于客户端凭响应头直接上报排障
    -- （core.response.exit 不支持 headers 参数，须先 set_header 再 exit）
    core.response.set_header(HEADER_TRACE_ID, trace_id(ctx))
    core.response.exit(status or 401, {
        error = status == 403 and "Forbidden" or "Unauthorized",
        detail = "未认证：" .. reason,
    })
end

-- 观测型策略：只记不拦
local function would_deny(ctx, route, reason)
    metric("would_deny", route, reason)
    core.log.warn("[gateway-auth] shadow would-deny route=", route, " reason=", reason, "（已放行）")
end

local function deny_or_shadow(ctx, route, reason, policy, status)
    if policy == "shadow" or policy == "open" then
        would_deny(ctx, route, reason)
        return
    end
    deny(ctx, route, reason, true, status)
end

-- ── JWT 验签（对齐 HmacJwtVerifier.java）──────────────────────

-- 返回 claims table 或 nil, reason
local function verify_jwt(token, conf, alg_expected)
    local obj = jwt_lib:load_jwt(token)
    if not obj.valid then
        return nil, "malformed"
    end
    if (obj.header.alg or "") ~= alg_expected then
        -- 与 JJWT 不同算法的令牌一律拒绝（不允许 alg 混淆）
        return nil, "signature"
    end

    -- lua-resty-jwt 签名：verify_jwt_obj(secret, jwt_obj, claim_specs)，
    -- leeway 经 legacy options 的 lifetime_grace_period 传入（时钟偏差容忍）
    local vobj = jwt_lib:verify_jwt_obj(conf.jwt_secret, obj,
                    { lifetime_grace_period = conf.clock_skew })
    if not vobj.verified then
        local r = tostring(vobj.reason or "")
        if find(r, "signature", 1, true) then
            -- 双密钥轮换：主密钥签名失败才用 previous 再试（仅验签，不放宽其他校验）。
            -- ⚠️ 失败后 lib 会把 reason 写回 jwt_obj 并在下次短路返回，必须重新 load（B2 实测）
            if conf.jwt_secret_previous then
                local obj2 = jwt_lib:load_jwt(token)
                if obj2.valid then
                    local vobj2 = jwt_lib:verify_jwt_obj(conf.jwt_secret_previous, obj2,
                                        { lifetime_grace_period = conf.clock_skew })
                    if vobj2.verified then
                        return vobj2.payload
                    end
                end
            end
            return nil, "signature"
        end
        if find(r, "exp", 1, true) then
            return nil, "expired"
        end
        return nil, "invalid"
    end

    return vobj.payload
end

local cjson_null = require("cjson").null

local function to_str_or_nil(v)
    -- JSON null 会被解码为 cjson.null（truthy userdata），必须视同缺失（B2 实测）
    if v == nil or v == cjson_null then return nil end
    local s = tostring(v)
    if s == "" then return nil end
    return s
end

-- 插件配置：全部行为参数来自环境变量（密钥不进 Git / apisix.yaml），
-- 进程内只构建一次；init_worker 时校验密钥就绪状态并 fail-fast 报警。
local function build_conf()
    local jwt_secret = env("JWT_SECRET", "")
    local prev = env("JWT_SECRET_PREVIOUS", "")
    return {
        jwt_secret = jwt_secret,
        jwt_secret_previous = (prev ~= "" and prev) or nil,
        issuer = env("JWT_ISSUER", "agent-platform"),
        clock_skew = tonumber(env("GATEWAY_AUTH_CLOCK_SKEW", "60")),
        alg_expected = expected_alg(jwt_secret),
        secret_ready = #jwt_secret >= 32,
        redis = {
            host = env("AUTH_REDIS_HOST", "host.docker.internal"),
            port = tonumber(env("AUTH_REDIS_PORT", "16379")),
            password = env("AUTH_REDIS_PASSWORD", ""),
            blacklist_prefix = env("GATEWAY_AUTH_BLACKLIST_PREFIX", "auth:blacklist:"),
            cmd_timeout_ms = tonumber(env("GATEWAY_AUTH_REDIS_CMD_TIMEOUT_MS", "300")),
            pool_size = tonumber(env("GATEWAY_AUTH_REDIS_POOL_SIZE", "20")),
            pool_max_idle_ms = tonumber(env("GATEWAY_AUTH_REDIS_POOL_IDLE_MS", "10000")),
        },
        -- 方案 A 会话闸（2026-09-16）：签名有效 ≠ 会话有效。
        -- GATEWAY_SESSION_CHECK：off / audit（默认，只记日志）/ enforce（无会话即 401）
        session_check = env("GATEWAY_SESSION_CHECK", "audit"),
        session_prefix = env("GATEWAY_SESSION_PREFIX", "auth:session:"),
    }
end


-- ── 角色闸（2026-09-16：path 前缀硬闸，与后端 _ROLE_RANK 同构）──
-- 设计约束：
--   * 正向清单：只有列出的前缀才被闸，未列出的一律放行（新路由默认不误伤）；
--   * 方法感知：GET/HEAD 算读，其余算写；某维度的最低角色缺省 = 不设限；
--   * 粗粒度网关闸 + 后端细粒度矩阵（prompts._check_permission）双层分工；
--   * roles 缺省（旧令牌/服务 key 通道）不拦，交给后端判定；
--   * 匹配用**原始请求 URI**（request_uri，proxy-rewrite 只改 upstream 路径），
--     所以前缀必须写成 /api/... 形态。
local ROLE_RANK = { viewer = 0, editor = 1, admin = 2 }
local READ_METHODS = { GET = true, HEAD = true, OPTIONS = true }
local ROLE_GATE_PREFIXES = {
    ["/api/approvals"] = { write = "admin" },   -- 审批处置权（后端已同语义 403，网关是外层硬闸）
    ["/api/prompts"]   = { write = "editor" },  -- draft/publish/rollback 的粗闸；高风险仍由后端收紧为 admin
    -- 方案 A（2026-09-16）：网关审计类端点外层硬闸（前后缀精确覆盖
    -- /gateway-auth 与 /gateway-access-logs 两个 GET 端点；后端
    -- require_admin_operator 仍保留，双层分工）。api-key 通道无 roles，
    -- 按既有设计不在此拦，由后端统一守卫判定
    ["/api/observability/gateway"] = { read = "admin", write = "admin" },
}

local function role_gate(uri, method, roles)
    -- 命中清单且角色不足 → 返回拒绝 reason；否则 nil 放行
    local rule, best_len = nil, 0
    for prefix, r in pairs(ROLE_GATE_PREFIXES) do
        if #prefix > best_len and string.sub(uri, 1, #prefix) == prefix then
            rule, best_len = r, #prefix
        end
    end
    if not rule then
        return nil
    end
    -- ⚠️ 不能写 `READ_METHODS[method] and rule.read or rule.write`：
    -- Lua 的 and-or 在 rule.read 为 nil 时会落到 rule.write，把读也拦掉（矩阵实测踩坑）
    local need
    if READ_METHODS[method] then
        need = rule.read
    else
        need = rule.write
    end
    if not need then
        return nil
    end
    local have = -1
    for _, r in ipairs(roles or {}) do
        local rr = ROLE_RANK[r]
        if rr and rr > have then
            have = rr
        end
    end
    if have >= ROLE_RANK[need] then
        return nil
    end
    return "role-insufficient"
end


-- ── 主流程 ────────────────────────────────────────────────────

local _M = { _VERSION = "0.1.0", version = 0.1, priority = 2500 }  -- priority：认证先于其他插件（对齐 jwt-auth 量级）

function _M.access(_, ctx)
    if not CONF then
        CONF = build_conf()  -- 惰性构建（init_worker 兜底失败时保证可用）
        if not CONF.secret_ready then
            core.log.error("[gateway-auth] JWT_SECRET 未配置或长度不足 32 字节")
        end
    end
    local route = ctx.route_id or "unknown"
    local policy = env("GATEWAY_AUTH_MODE", "enforce")

    -- ① 无条件剥离伪造头（白名单放行前也执行；remove 语义）
    for _, h in ipairs(FORGED_HEADERS) do
        core.request.set_header(ctx, h, nil)
    end

    -- ② Trace ID：透传复用或生成（不做信任判定，仅链路关联）
    local tid = trace_id(ctx)
    core.request.set_header(ctx, HEADER_TRACE_ID, tid)

    -- ③ OPTIONS 预检放行
    if ngx.req.get_method() == "OPTIONS" then
        return
    end

    -- ④⑤ Bearer 提取（Bearer 优先于 API-Key 通道）：浏览器流量必须过完整 JWT 流——
    -- 若 X-API-Key 优先，带 Key 的请求会绕过黑名单（2026-09-15 拆分实测发现的设计缺陷）。
    -- 仅当**没有** Bearer 时，X-API-Key 才走服务级透传（网关不校验，下游负责）。
    local authz = core.request.header(ctx, "Authorization") or ""
    local token = nil
    if #authz > 8 and find(authz:sub(1, 7):lower(), "bearer ", 1, true) then
        token = authz:sub(8)
        token = token:match("^%s*(.-)%s*$")  -- trim
        if token == "" then token = nil end
    end

    if not token and core.request.header(ctx, "X-API-Key") then
        core.request.set_header(ctx, HEADER_AUTH_TYPE, "api-key")
        return
    end

    if not token then
        if policy == "guest" then
            core.request.set_header(ctx, HEADER_AUTH_TYPE, "anonymous")
            core.request.set_header(ctx, HEADER_USER_ID, "anonymous")
            return
        end
        return deny_or_shadow(ctx, route, "no-credential", policy)
    end

    -- ⑥ 黑名单（先于验签，SCG 顺序；三类故障 fail-closed）
    local hit, bl_err = blacklist.is_blacklisted(CONF.redis, token)
    if bl_err then
        return deny_or_shadow(ctx, route, bl_err, policy)  -- blacklist-timeout / blacklist-unavailable
    end
    if hit then
        return deny_or_shadow(ctx, route, "blacklist", policy)
    end

    -- ⑦ 验签
    local payload, reason = verify_jwt(token, CONF, CONF.alg_expected)
    if not payload then
        return deny_or_shadow(ctx, route, reason, policy)
    end

    -- ⑧ issuer（SCG：requireIssuer）
    if tostring(payload.iss or "") ~= CONF.issuer then
        return deny_or_shadow(ctx, route, "issuer", policy)
    end

    -- ⑨ userId（必填，数字会转字符串）
    local user_id = to_str_or_nil(payload.userId)
    if not user_id then
        return deny_or_shadow(ctx, route, "missing-user-id", policy)
    end

    -- ⑩ token 类型（refresh 不可当 access）
    local token_type = to_str_or_nil(payload.type)
    if token_type ~= "access" then
        return deny_or_shadow(ctx, route, "token-type-mismatch", policy)
    end

    -- ⑩.2 会话闸（方案 A，2026-09-16）：py 签发时写 auth:session:{userId}:{jti}，
    -- 登出/强制下线删键 → 已签发令牌即时全链路失效，不等 30min TTL。
    -- 语义：签名有效但会话键缺失 = 已吊销。audit 只记不拦（灰度），enforce 401。
    -- 旧令牌（无 jti）audit 放行记日志；enforce 拒绝（重新登录即得新令牌）。
    -- Redis 故障与黑名单同语义 fail-closed（session-timeout / session-unavailable）。
    local session_mode = CONF.session_check
    if session_mode ~= "off" then
        local jti = to_str_or_nil(payload.jti)
        local session_key = nil
        if jti and user_id then
            session_key = CONF.session_prefix .. user_id .. ":" .. jti
        end
        if not session_key then
            if session_mode == "enforce" then
                return deny_or_shadow(ctx, route, "missing-jti", policy)
            end
            core.log.warn("[gateway-auth] session-audit 放行无 jti 旧令牌 route=", route)
        else
            local alive, s_err = blacklist.exists(CONF.redis, session_key)
            if s_err then
                -- 黑名单组件 reason 归一为 session-*，告警口径可区分
                local reason = (s_err == "redis-timeout") and "session-timeout" or "session-unavailable"
                return deny_or_shadow(ctx, route, reason, policy)
            end
            if not alive then
                if session_mode == "enforce" then
                    return deny_or_shadow(ctx, route, "session-revoked", policy)
                end
                core.log.warn("[gateway-auth] session-audit 放行已吊销会话 route=", route)
            end
        end
    end

    -- ⑩.5 角色闸（正向清单 + 方法感知；shadow 模式只记不拦）
    local claims_roles = {}
    if type(payload.roles) == "table" then
        for _, r in ipairs(payload.roles) do
            local rs = to_str_or_nil(r)
            if rs then
                claims_roles[#claims_roles + 1] = rs
            end
        end
    end
    local raw_uri = ngx.var.request_uri or ""
    local gate_reason = role_gate(string.match(raw_uri, "^([^?]*)") or "",
                                  ngx.req.get_method(), claims_roles)
    if gate_reason then
        return deny_or_shadow(ctx, route, gate_reason, policy, 403)
    end

    -- 观测型策略到此放行但不注入（SCG shadow 语义：行为保持不注入）
    if policy == "shadow" or policy == "open" then
        return
    end

    -- ⑪ 注入可信身份头（仅验证全通过后）
    core.request.set_header(ctx, HEADER_AUTH_TYPE, "jwt")
    core.request.set_header(ctx, HEADER_USER_ID, user_id)
    local uname = to_str_or_nil(payload.username)
    if uname then
        core.request.set_header(ctx, HEADER_USER_NAME, uname)
    end
    local dept = to_str_or_nil(payload.dept)
    if dept then
        core.request.set_header(ctx, HEADER_USER_DEPT, dept)
    end
    -- roles claim（数组）→ 逗号分隔注入，后端 resolve_operator_role 消费
    -- （prompts RBAC / 审批角色校验）。缺省或非数组不注入，后端按无角色处理
    if type(payload.roles) == "table" and #payload.roles > 0 then
        local parts = {}
        for _, r in ipairs(payload.roles) do
            local rs = to_str_or_nil(r)
            if rs then parts[#parts + 1] = rs end
        end
        if #parts > 0 then
            core.request.set_header(ctx, HEADER_USER_ROLES, concat(parts, ","))
        end
    end
    -- permissions claim（数组）→ 逗号分隔注入；缺省不注入，后端按未声明处理。
    if type(payload.permissions) == "table" and #payload.permissions > 0 then
        local parts = {}
        for _, p in ipairs(payload.permissions) do
            local ps = to_str_or_nil(p)
            if ps then parts[#parts + 1] = ps end
        end
        if #parts > 0 then
            core.request.set_header(ctx, HEADER_USER_PERMISSIONS, concat(parts, ","))
        end
    end
    -- tenant_id claim → X-Tenant-Id；客服/RBAC 等租户敏感端点缺失时 fail-closed。
    local tenant = to_str_or_nil(payload.tenant_id)
    if tenant then
        core.request.set_header(ctx, HEADER_TENANT_ID, tenant)
    end
end

function _M.init_worker()
    CONF = build_conf()
    if not CONF.secret_ready then
        core.log.error("[gateway-auth] JWT_SECRET 未配置或长度不足 32 字节，" ..
            "enforce 模式将全部拒绝（keystore-unavailable）")
    end
end

_M.schema = {
    type = "object",
    properties = {},
    additionalProperties = false,
}

return _M
