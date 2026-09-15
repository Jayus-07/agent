-- auth_blacklist.lua — Redis 黑名单独立检查组件（只读，fail-closed）
--
-- 契约（B0 审计 + 方案 v2.0 §3）：
--   key = <blacklist_prefix><token>（前缀 auth:blacklist:，写侧是 Java auth-service PermissionCache）
--   本组件只做 EXISTS，禁止任何写命令。
--   故障分类：拒连/连接耗尽 → "blacklist-unavailable"；超时 → "blacklist-timeout"。
--   两者都必须 fail-closed（调用方收到 err_reason 时必须 401，不得放行）。
--   绝不记录 token 明文或 Redis 密钥。
--
-- 配置全部来自环境变量（由调用方读取后传入 conf），不落 Git：
--   host, port, password, connect_timeout_ms, pool_max_idle_ms, pool_size

local redis = require("resty.redis")

local _M = { _VERSION = "0.1.0" }

function _M.is_blacklisted(conf, token)
    local red = redis:new()

    -- set_timeout 覆盖连接 + 命令两个阶段（lua-resty-redis 无独立连接超时）；
    -- 显式配置，禁止无界等待。
    red:set_timeout(conf.cmd_timeout_ms or 300)

    local ok, err = red:connect(conf.host, conf.port)
    if not ok then
        -- 连接失败：拒连（connection refused / DNS）或连接超时
        red:close()
        if err and tostring(err):find("timeout", 1, true) then
            return false, "blacklist-timeout"
        end
        return false, "blacklist-unavailable"
    end

    if conf.password and conf.password ~= "" then
        local ok_auth, err_auth = red:auth(conf.password)
        if not ok_auth then
            red:close()
            -- 认证失败同样视为不可用（fail-closed，且不得泄露失败细节）
            if err_auth and tostring(err_auth):find("timeout", 1, true) then
                return false, "blacklist-timeout"
            end
            return false, "blacklist-unavailable"
        end
    end

    local ok_exists, err_exists = red:exists(conf.blacklist_prefix .. token)
    if not ok_exists then
        red:close()  -- 命令失败不回池（连接状态未知），直接丢弃
        if err_exists and tostring(err_exists):find("timeout", 1, true) then
            return false, "blacklist-timeout"
        end
        return false, "blacklist-unavailable"
    end

    -- 成功：连接回连接池（显式上限；池满时 lua-resty-redis 会退化为新建连接，
    -- 不存在"池满阻塞"形态 —— 连接建立失败统一走 fail-closed 路径）
    red:set_keepalive(conf.pool_max_idle_ms or 10000, conf.pool_size or 20)

    return ok_exists == 1, nil
end

return _M
