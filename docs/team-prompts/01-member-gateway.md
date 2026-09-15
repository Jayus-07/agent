# 成员 1 · 网关写手（δ 线）

**可写路径**：`apisix/**`（建议只动 `apisix/plugins/gateway-auth.lua`）
**禁止**：`backend/**`、`frontend/**`、任何 compose 文件、任何 rebuild/down

---

## 任务 B5（S0-1）：剥离伪造的 `X-Operator-*` 头

### 背景

`gateway-auth.lua` L35 的剥离清单目前**只有四个头**：

```lua
local FORGED_HEADERS  = { "X-Auth-Type", "X-User-Id", "X-User-Name", "X-User-Dept" }
```

L201 的剥离循环**无条件执行**（不受 `GATEWAY_AUTH_MODE` 门控）：

```lua
for _, h in ipairs(FORGED_HEADERS) do
    core.request.set_header(ctx, h, nil)
end
```

→ 所以**只要把头加进这个表，它就会在入口被剥掉**。但 `X-Operator-Role` / `X-Operator-Id`
不在表里，客户端可以伪造它们直达上游 —— 这是 S0-1 要堵的洞。

### 要做的事

1. 在 `FORGED_HEADERS` 里补上 `"X-Operator-Role"` 与 `"X-Operator-Id"`（保持现有四头不动）。
2. 检查 Lua 文件里是否还有**同族头**遗漏（例如 `X-Operator-*` 的其它变体、大小写变体），
   一并说明你的判断依据（**不要瞎加**，加错会剥掉合法头）。
3. `docker restart agent-apisix`（**只这一个容器**）。

> ⚠️ **`apisix/**` 是 ro 挂载进容器的 → 改宿主文件后 restart 即生效，绝对不要 rebuild。**

### 通过标准（必须两条都拿到证据）

**① 静态证据**

```bash
cd "D:/Program Files/workplace/agent"
grep -n "FORGED_HEADERS" -A 2 apisix/plugins/gateway-auth.lua
# 期望：6 个头 = X-Auth-Type / X-User-Id / X-User-Name / X-User-Dept / X-Operator-Role / X-Operator-Id
git show --stat HEAD        # 只应出现 apisix/ 下的文件
```

**② 运行时证据（回显桩实测）**

```bash
# 起回显桩（本会话新增的标准库实现）
python scripts/echo_headers_stub.py --port 8099
# 自测：桩本身能把请求头回显出来
curl -s http://127.0.0.1:8099/echo -H "X-Operator-Role: admin"

# 经 APISIX 测试实例（9081，enforce）带伪造头请求，确认上游**收不到**该头
# 台架：apisix-test/apisix.yaml（路由 /echo，upstream 指 host.docker.internal:8099）
# 扩展验收脚本场景 10：scripts/gateway_auth_matrix.py，把 x-operator-role / x-operator-id
# 也纳入「不应出现在响应头中」的断言，然后：
python scripts/gateway_auth_matrix.py --base http://127.0.0.1:9081 --secret "$SECRET64"
```

> ⚠️ **不要用行为观察法验收**（发个伪造头、看接口行为变不变）：
> py 侧 `resolve_operator_role()` 本轮**只认 `X-Internal-Token`**，根本不消费 `X-Operator-*`
> （这是 B4，**故意不开**）→ **行为不变不能证明剥离生效**。必须用回显桩。
> ⚠️ 9081 测试实例与 8099 桩的启动脚本**历史会话未留存**（只留了配置）。
> 若重建耗时，可退而先交付静态证据 + 已扩断言未跑，并在汇总里**明确写清「运行时证据未取得」** ——
> 不要把没跑过的说成通过。

### 提交

```bash
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
cd "D:/Program Files/workplace/agent"
git diff --cached --name-only          # 先看有没有别人 staged 的东西
git add apisix/plugins/gateway-auth.lua        # 新文件才需要；已跟踪文件可跳过
git commit -m "fix(gateway): 剥离伪造的 X-Operator-Role/X-Operator-Id（S0-1）

- FORGED_HEADERS 由四头扩到六头；L201 剥离循环无条件执行，不受 GATEWAY_AUTH_MODE 门控
- apisix/plugins 为 ro 挂载 → restart agent-apisix 即生效，无 rebuild
- 验收：<贴实测输出>" -- apisix/plugins/gateway-auth.lua
git log --oneline -1 && git show --stat HEAD
```

**回报格式**：改了什么文件 / 新增哪几个头 / 静态证据原文 / 运行时证据原文（或明确写「未取得」）/ 未决问题。
