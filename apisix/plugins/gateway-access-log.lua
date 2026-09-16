-- gateway-access-log.lua — 访问审计日志出口插件（log 阶段）
--
-- 职责：每请求一行 JSON XADD 到 Redis Stream agent:gw:access-log，由后端
-- gateway_log_ingest 消费落库（ai.gateway_access_logs），管理端
-- /observability/gateway「访问日志」区块查询展示。
-- 字段语义与 stdout 访问日志（apisix/config.yaml access_log_format）一致，
-- 两路互为冗余：stdout 由 docker logs 人工排查，本插件供结构化查询。
--
-- 选用 Redis Streams 而非 http-logger 的原因：standalone apisix.yaml 不支持
-- ${{ENV}} 替换，http-logger 的鉴权头只能明文落 Git（红线）；Redis 连接凭据
-- 已经由 compose 注入（AUTH_REDIS_*），与 auth_blacklist 同源。
--
-- fail-open：日志链路任何故障只丢弃（限频告警），绝不影响转发主链路；
-- 审计完整性以 stdout 日志兜底。
--
-- 安全红线：条目只含网关侧可信变量（验证后注入的 user_id / 网关看到的 IP），
-- 不含 token / secret / Cookie。auth/sys 白名单路由的 X-User-Id 是客户端
-- 自带头（未验签），消费侧展示时按 auth_type 区分可信性。
--
-- 配置全部来自环境变量（不落 Git）：AUTH_REDIS_HOST / PORT / PASSWORD，
-- 可选 GATEWAY_ACCESS_LOG_STREAM（默认 agent:gw:access-log）。

local redis          = require("resty.redis")
local cjson          = require("cjson")
local core           = require("apisix.core")

local ngx_utctime    = ngx.utctime
local ngx_var        = ngx.var
local tostring       = tostring
local tonumber       = tonumber

local _M = { _VERSION = "0.1.0", version = 0.1, priority = 100 }

local function env(name, default)
    local v = os.getenv(name)
    if v == nil or v == "" then
        return default
    end
    return v
end

-- log 阶段无路由级配置（global_rules 挂载 {}），参数在首次使用时从 env 构建
local CONF = nil

local function build_conf()
    return {
        host = env("AUTH_REDIS_HOST", "host.docker.internal"),
        port = tonumber(env("AUTH_REDIS_PORT", "16379")) or 6379,
        password = env("AUTH_REDIS_PASSWORD", ""),
        stream = env("GATEWAY_ACCESS_LOG_STREAM", "agent:gw:access-log"),
        -- 每请求一次 XADD（log 阶段 cosocket）；超时对齐黑名单插件（fail-open 语义）
        cmd_timeout_ms = tonumber(env("GATEWAY_ACCESS_LOG_REDIS_TIMEOUT_MS", "300")) or 300,
        pool_max_idle_ms = 10000,
        pool_size = 20,
        -- Stream 容量上限：单条 ~300B，10 万条约 30MB，超出自动截断旧日志
        max_len = 100000,
    }
end

-- 限频告警（每 worker 30s 一条）：Redis 故障时避免日志风暴
local last_warn_ts = 0

local function warn_limited(msg, err)
    local now = ngx.now()
    if now - last_warn_ts < 30 then
        return
    end
    last_warn_ts = now
    core.log.warn("[gateway-access-log] " .. msg .. ": " .. tostring(err))
end

-- 取变量，空串/nil 统一为空串（JSON 侧不出现 null）
local function v(name)
    local val = ngx_var[name]
    if val == nil or val == "" then
        return ""
    end
    return tostring(val)
end

-- Redis 连接 + XADD：只能在 ngx.timer 上下文执行——log_by_lua 禁用 cosocket
-- （"API disabled in the context of log_by_lua*"，实测），timer 内可正常建 socket。
-- 注意 timer 回调首参是 premature（OpenResty 注入）
local function push_entry(premature, entry, cmd_timeout_ms)
    if premature then
        return
    end
    local red = redis:new()
    red:set_timeout(cmd_timeout_ms)

    local ok, err = red:connect(CONF.host, CONF.port)
    if not ok then
        red:close()
        return warn_limited("Redis 连接失败，访问日志丢弃", err)
    end
    if CONF.password and CONF.password ~= "" then
        local ok_auth, err_auth = red:auth(CONF.password)
        if not ok_auth then
            red:close()
            return warn_limited("Redis 认证失败，访问日志丢弃", err_auth)
        end
    end

    -- MAXLEN ~ 近似截断（性能优于精确截断，Redis 官方推荐）
    local ok_add, err_add = red:xadd(CONF.stream, "MAXLEN", "~", CONF.max_len, "*",
                                     "data", entry)
    if not ok_add then
        red:close()
        return warn_limited("XADD 失败，访问日志丢弃", err_add)
    end

    -- 成功：连接回池（对齐 auth_blacklist 池化语义）
    red:set_keepalive(CONF.pool_max_idle_ms, CONF.pool_size)
end

function _M.log(_, _)
    if not CONF then
        CONF = build_conf()
    end

    -- 变量读取/时间戳必须在 log 阶段（依赖请求上下文）；IO 挪进 timer
    -- 字段与 stdout access_log_format 一一对应（见 apisix/config.yaml 注释）
    local entry = cjson.encode({
        time        = ngx_utctime(),
        client_ip   = v("remote_addr"),
        user_id     = v("http_x_user_id"),
        auth_type   = v("http_x_auth_type"),
        trace_id    = v("http_x_trace_id"),
        method      = v("request_method"),
        uri         = v("uri"),
        query       = v("args"),
        status      = tonumber(v("status")) or 0,
        bytes       = tonumber(v("body_bytes_sent")) or 0,
        -- request_time 单位是秒（含小数），统一转 ms 与字段名对齐
        duration_ms = math.floor((tonumber(v("request_time")) or 0) * 1000),
        ua          = v("http_user_agent"),
    })

    -- IO 挪进 timer：log 阶段禁 cosocket；timer 创建失败只告警（fail-open）
    local ok_timer, err_timer = ngx.timer.at(0, push_entry, entry, CONF.cmd_timeout_ms)
    if not ok_timer then
        warn_limited("timer 创建失败，访问日志丢弃", err_timer)
    end
end

function _M.init_worker()
    CONF = build_conf()
end

_M.schema = {
    type = "object",
    properties = {},
    additionalProperties = false,
}

return _M
