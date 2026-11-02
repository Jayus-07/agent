# 审计发现与修复验证方案

审计基线：origin/main HEAD 775cf745f6b7088e062639390dbf688f0be61756。本清单区分已确认源码问题、业务正确性问题、门禁缺口和依赖仓库外证据的待确认项。P1 最高；安全项与普通质量问题分开统计。

## 已确认安全发现

### S1. MCP capability grant 未绑定 runtime 所属租户
- **优先级/置信度：** P1 / 高
- **类别：** 跨租户授权绕过，CWE-862
- **代码证据：** backend/app/api/routes/admin_bundles.py:119-136 检查 MCP capability 是否存在，但未核对 runtime 的 tenant_id；同文件 392-408 按 capability slug 查 tool 后，把 grant 写入当前租户，tool 选择缺少同租户约束。backend/infra/mcp_registry/store.py:112-117 grant 查询通过 tool ID 关联但没有强制 tool tenant 与 grant tenant 一致；363-367 用 grant tenant 构造授权结果，423-449 后续按 slug 取 runtime 并解密 connection credential。backend/core/product_context.py:222-225 对动态 MCP 的授权依赖产品 capability grant；backend/skills/mcp_dynamic/skill.py:40-56,73-104 解析 runtime 并带凭据调用 upstream。backend/sql/migrations/114_mcp_capability_center.sql:124-137 grant 到 tool 的外键没有组合 tenant 一致性约束。
- **触发前提与影响：** 租户 B 管理员能编辑自己的 bundle，并知晓租户 A 已批准可读工具的全局唯一 slug；B 的产品域检查通过后可能加载 A 的 runtime、connection 和凭据，造成跨租户数据/凭据使用。不是匿名漏洞。
- **修复方向：** 授权写入时以 tool_id + tenant_id 同时约束；读取和执行时再次要求 grant、tool、connection tenant 一致，默认拒绝不一致旧数据；评估数据库组合外键/唯一键及历史数据迁移策略；检查缓存快照撤销时效。
- **验证计划（T2）：** 两租户 fixture 下创建 B grant 指向 A tool 必须失败；执行端对错配历史记录拒绝且不解密/请求 upstream；同租户合法授权成功；撤销后按契约失效。若加迁移，测合法数据迁移和错配数据处置。

### S2. MCP HTTP API 探活自动跟随未经逐跳检查的重定向
- **优先级/置信度：** P2 / 高
- **类别：** SSRF，CWE-918
- **代码证据：** backend/infra/mcp_registry/probe.py:26-41 使用 HTTPX follow_redirects=True；只对初始地址请求 HEAD，没有对跳转目标重新校验。backend/app/api/routes/admin_mcp.py:215 起有初始 URL 检查及可选 allowlist，只覆盖配置的 host；手动探活入口约 807-846。backend/infra/mcp_registry/health.py:76-99,141-153 周期探活也复用该行为。
- **触发前提与影响：** 有权配置/探测连接的租户管理员，或被信任的外部 upstream，可让公开地址重定向到私网目标；服务端会发出 HEAD 请求。当前探测只返回成功/耗时，源码未证明响应正文泄露。
- **修复方向：** 默认关闭 redirect；若业务需要，手动限制跳转数量，并逐跳校验 scheme、主机、解析 IP、出口 allowlist；跨源跳转去除认证头。手动与后台探活共用实现。
- **验证计划（T2）：** 本地 stub 公网地址重定向到私网监听器时，私网端不得收到请求；正常允许的公网地址成功；跨源跳转不能收到原始认证头；过多跳转被拒绝。

### S3. 共享 data API 缺少服务端角色/资源授权
- **优先级/置信度：** P2 / 高
- **类别：** 缺少授权校验，CWE-862
- **代码证据：** backend/app/api/routes/data.py:133-157 的 POST /data/generate 可查询白名单 staging 表并返回数据预览；同文件 371-403 的 POST /data/collect 接受客户端 enable_write 并将数据追加到 staging。backend/app/api/router.py:106 挂载 data 路由；网关有身份认证，但 apisix/plugins/gateway-auth.lua:251-321 未给该路径配置角色门禁；路由处理函数也未声明 FastAPI 权限依赖。
- **触发前提与影响：** 任何能取得有效 API 身份但不应拥有该共享数据功能的低权限用户可尝试读取预览或触发写入。读写范围受现有表/代码逻辑约束；不据此推断任意 SQL 或用户任意数据写入。
- **修复方向：** 先按现有授权标准确认 read/collect 所需角色和资源范围，再在 FastAPI 服务端加入依赖/租户范围；写操作走已批准的管理权限，客户端 enable_write 不作为授权依据。
- **验证计划（T2）：** 未认证、普通用户、无权限租户、获授权身份分别覆盖；未授权读/写均拒绝；获授权身份只访问批准表/租户；直接调用 FastAPI 路由也受保护。

### S4. 上传文件名可路径穿越并在错误清理时删除目录外 CSV/JSON
- **优先级/置信度：** P2 / 高
- **类别：** 路径穿越，CWE-22
- **代码证据：** backend/app/api/routes/data.py:46-59 将用户提供的 file.filename 拼接至 UPLOAD_DIR 后打开；解析异常分支 89-91 删除该路径。扩展名限制为 CSV/JSON，但文件名的相对路径分隔符未消除。
- **触发前提与影响：** 已认证用户可上传构造文件名；随机 file ID 前缀不能保证路径留在上传目录。可覆盖进程有权写入的 CSV/JSON，或利用错误清理删除相应目标；未证明任意文件类型写入或代码执行。
- **修复方向：** 不把原始文件名用于磁盘路径；使用服务端生成 basename，路径 resolve 后确认位于上传根目录；错误清理只删除本请求创建的文件。可设置上传大小上限，但不要扩大修复范围。
- **验证计划（T2）：** 使用临时上传目录和路径穿越文件名，验证外部目标不创建/覆盖；无效 CSV 清理不能删除目录外文件；合法文件可解析、可按现有生命周期删除。

### S5. 报告图表公开 URL 后缀只有 32 bit 熵
- **优先级/置信度：** P3 / 中
- **类别：** 不足随机值，CWE-330
- **代码证据：** backend/business_report/chart_generator.py:137-144 只取 UUID hex 的 8 位作为图片后缀；backend/app/api/routes/report_files.py:27-40 图片可匿名读取，路径限制在报告目录；网关限制每 IP 请求速率。
- **触发前提与影响：** 若该 URL 被当作访问控制能力，且 slug 可知/可猜，32 bit 空间不足以长期保护可能含敏感信息的图表。未核实图表实际敏感度，不宣称已发生泄露。
- **修复方向：** 使用至少 128 bit CSPRNG token 或完整 UUID；若图表不应公开，改用短时签名 URL 或认证代理；保留现有路径规范化约束。
- **验证计划（T1/T2）：** 断言生成随机后缀长度与熵要求；公开图表按产品预期可加载；路径穿越仍拒绝；必要时确认缓存/CDN 不扩大公开范围。

### S6. 三端登录页允许协议相对外部重定向
- **优先级/置信度：** P3 / 中
- **类别：** 开放重定向，CWE-601
- **代码证据：** frontend/src/app/login/page.tsx:45,84-85；frontend-admin/src/app/login/page.tsx:61,79-80；frontend-cs/src/app/login/page.tsx:31,52-53。三处只检查 redirect 是否以 / 开头，//attacker.example 也符合。登录成功后跳转外站，可用于钓鱼；当前证据未证明 token 会随跳转泄露。
- **修复方向：** 以当前站点 origin 解析目标，只接受同源路径；拒绝协议相对、绝对外域和反斜杠变体，异常时使用应用默认页。
- **验证计划（T1）：** 同源内页正常跳转；协议相对、外域绝对 URL、反斜杠和编码变体回退默认页；三端行为一致。

### S7. 主站默认把可复用明文密码写入 localStorage
- **优先级/置信度：** P3 / 高
- **类别：** 明文凭据存储，CWE-312
- **代码证据：** frontend/src/app/login/page.tsx:57,67-76,103-113 默认启用记住并读写已保存密码；frontend/src/lib/auth.ts:355-377 将 username/password JSON 写入 localStorage；logout() 187-209 不清除此键，只有显式忘记/取消选择路径会移除。
- **触发前提与影响：** 任意同源脚本或能读取浏览器 profile 的主体可获取长期保存的可复用密码；当前审查未发现可确认的 XSS 入口，账号密码复用情况未知。
- **修复方向：** 不保存密码，只保留非敏感用户名偏好；默认关闭旧记住密码选项；升级时清理历史存储键。让浏览器密码管理器承担凭据保存。
- **验证计划（T1）：** 登录后 localStorage 不包含密码；登出不会留下密码；旧键在升级/首次初始化迁移时清除；用户名偏好按产品预期保留。

## 业务正确性发现

### Q1. 旅行最后一天午餐可能排到离开时间之后
- **优先级：** P2
- **代码证据：** backend/travel/services/transit_service.py:108-112 将最后一天的 day_end_min 设为离开时间；_place_lunch() 125-130 会无视此上限插入完整午餐；193-195 的尾部补午餐也未检查截止时间。
- **影响示例：** 景点 12:10 结束、12:30 离开，可能仍安排 12:10–13:10 午餐。
- **修复方向：** 午餐完整时间必须小于等于 day end 才插入；否则返回明确的“剩余时间不足/请调整离开时间”结果，不要静默生成超时计划。
- **验证计划（T2）：** 离开时间在午餐开始前、午餐窗口内但不足完整用餐时长、以及足够安排完整午餐三类边界。

### Q2. 常见否定儿童短语被误识别为同行儿童
- **优先级：** P2
- **代码证据：** backend/travel/agents/requirement_agent.py 的 _RE_NEGATED_CHILD_CONTEXT 覆盖部分“不带/不需要”等短语，但未覆盖“不想带孩子”“不要带孩子”；掩码被 extract_party_size、party_size_source、extract_adults_children、extract_preferences 使用，未遮蔽时 _RE_COMPANION 的“带孩子”会错误匹配。现有 backend/tests/travel/test_negated_child_context.py 未覆盖这两种表达。
- **影响：** “苏州两日游，不想带孩子”可能被推断为多人/家庭游并触发家庭偏好。
- **修复方向：** 扩充并规范否定范围解析，避免单纯增加含糊子串；覆盖“否定后转折为正向需求”等上下文。
- **验证计划（T2）：** 表驱动覆盖无儿童、不带娃、不想带孩子、不要带孩子、正向带孩子，以及“不要带孩子，但考察亲子酒店”等对比句。

## CI 覆盖缺口

### C1. 行程缓冲测试未纳入 rag_smoke 定向工作流
- **优先级：** P3
- **代码证据：** .github/workflows/rag_smoke.yml 已列入 backend/tests/travel/test_negated_child_context.py，但没有列入 backend/tests/travel/test_schedule_travel_buffer.py；后者已存在并覆盖行程缓冲逻辑。
- **风险：** 测试虽存在，但该高频定向门禁未必执行，回归更晚才会暴露。按仓库 AGENTS.md，新功能/链路测试需同步进入 rag_smoke。
- **修复方向：** 添加测试路径，检查 workflow 的路径引用和现有运行预算；不改测试断言来绕过失败。
- **验证计划（T1）：** 运行工作流实际调用的对应 pytest 命令或等效定向命令，确认新增路径被收集执行；对 YAML/命令配置做静态检查。

## 需从仓库外补证的条件项

这些问题不计入已确认安全发现，也不应在没有运行配置证据时宣称为漏洞：

1. BFF 客户端 IP 转发头：BFF 读取 X-Forwarded-For/X-Real-IP 作为限流 IP；仓库部署评论要求入口代理清理/覆盖，但真实代理链未知。检查公网入口是否剥离客户端自带头并可靠设置真实 IP。
2. 手动 GitHub Actions 与环境密钥：评测 workflow 可能从当前分支运行并关联环境；需确认 GitHub Environment 分支限制、审批规则和 self-hosted runner 隔离。静态仓库无法推定这些设置。
3. DNS rebinding / 二次解析：URL 检查与 HTTP 客户端连接之间可能再次解析 hostname；需结合出站策略和可控 DNS 的验证确认是否可达私网。
4. 运行时 MCP/provider 注册项、秘密注入、真实容器网络和报告图表敏感级别均未读取或核实。

## 复核范围说明

RAG 上传/搜索/授权和过滤、SQL policy/executor/validator、任务状态与幂等、MCP 凭据静态加密与调用链均已针对重点路径检查。本轮静态审查未发现这些范围的额外可报告问题；该结论不等同动态测试通过，也不覆盖生产配置。
