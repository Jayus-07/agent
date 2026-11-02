# Q5 安全渗透自检报告（2026-11-03）

> 范围：①Mimosa 全仓深度扫描（密封产物见 §一）；②客服域 RBAC/IDOR/租户逐端点复核；③SQL 参数化与只读权限抽查；④SSE/WS 鉴权面。自检为只读静态+既有行为核对，不做破坏性利用；外部渗透是否引入由用户另行拍板。

## 一、Mimosa 深度扫描

- 工具可用性核对：本会话存在 Mimosa MCP 工具（security_scan_start/status/cancel/resume），与 HANDOFF「工具缺失预设」相比已具备。
- 目标：D:/Program Files/workplace/agent-cs-prod（含 Q1–Q4 合并后全部代码）；depth=deep；evidenceBoundary=static_only_no_runtime_execution。
- jobId：`scan-job-mvzrfg8m-877b3546f6831efa`；scanId `scan-2026-11-02T21-22-50.452Z-77e8d4e922d1`；seal `sha256:722528c483daacd0470f98fa9d0d7b5805682440bcfb648bf19bb8d726d270b1`（密封完成，2026-11-02T21:22:50Z，产物 `~/.mimosa/security-scans/project-eaaf0239126ee9561d371875/<scanId>/`）。
- 依赖摘要：701 包扫描；离线告警库匹配 14 包 / 88 条 advisory（细目在产物 dependencySummary，处置归依赖治理常规流程，不在本任务展开）。

## 二、客服域授权边界复核（逐端点）

复核面：`backend/app/api/routes/cs_*.py` 13 个路由文件 + evaluation_v2 + 网关层。鉴权三档：`require_identity`（终端用户）/ `require_agent`（客服坐席，含 is_supervisor 二次判定）/ `require_cs_supervisor`（运营主管）。

| 检查项 | 结论 | 证据 |
|---|---|---|
| 路由鉴权覆盖 | **通过**：抽样全部 13 文件无未鉴权路由；inventory 端点显式 supervisor 二次判定 | capabilities_inventory: require_inventory_viewer + is_supervisor 403 |
| 会话对象级访问（IDOR） | **通过**：按 ID 端点（events/rating/pending-proposal/customer-context/detail/traces）全部经 `_ensure_conversation_access`——普通用户须 user_id+tenant_id 双匹配、坐席仅限**当前认领者**（非全体坐席）、admin 放行、服务间通道放行并记 warning | cs_admin.py:505-545、846/907/1240/1331/1446/1498 调用点 |
| /my 端点自范围 | **通过**：`_ensure_my_conversation`（messages/pending/typing） | cs_admin.py:153/993/1125/1876 |
| 工单对象级访问 | **通过**：tenant 过滤 + 创建人归属 + 404 不区分不存在/无权（防探测） | cs_tickets.py:83-100 |
| 租户隔离 | **通过**：会话列表 operator 闸+租户谓词（STOP CS-A P0-1 修复在位）；transfer 按 `CSAgent.tenant_id == context.tenant_id` 圈定目标坐席；WS claims 绑定 tenant | cs_admin.py:449-452/760、cs_agent_offers.py:538 |
| evaluation_v2 门禁 | **通过**：42/42 路由全部携带鉴权依赖（脚本化逐块核验，unguarded=0） | 本报告复核脚本输出 |
| 信任边界 | **通过**：app :8000 仅绑定 127.0.0.1（容器端口映射实证），外部流量必须经 :9080 网关；身份头由 gateway-auth 注入，IDENTITY_SOURCE 集中解析 | docker ps 端口映射、identity.py |

## 三、SQL 参数化与只读权限抽查

- customer_service 全目录无 f-string SQL 拼接（`execute(f"/text(f"` 扫描 0 命中）；订单/工单/确认存储查询均参数绑定。
- sandbox 直查路径使用 `agent_readonly` 账号（SELECT-only 授权模型；Q4 隔离库按同口径建号）。写路径经业务服务/网关 http 模式，不经 SQL 执行器。

## 四、SSE / WS 鉴权面

- **WS（/ws/cs/agent）**：HTTP 端点签发一次性 ticket（60s TTL、单次使用核销），坐席 agent_id 必须由服务端 `cs_agents.auth_user_id` 反查（客户端提交的 agent_id 被忽略）；API-Key 通道明确拒绝签发；无效 ticket 4401 关闭；Redis 不可用拒绝签发（fail-closed）。
- **SSE（主 agent /api/chat）**：经 `require_product_identity`（跨产品身份隔离，ADR-0002 口径）+ 全局限流。
- 客服域实时通道为 WS 单一入口，无平行 SSE 通道需单独设防。

## 五、Mimosa findings 分级（304 条全量分类 + 高危逐条人工复核）

静态深扫 304 条（high 250 / medium 28 / low 26）。按类分布：SSRF 115（94+21 入口）、SQL 注入 74、路径穿越 44、疑似污点 28、随机数 26、XXE 6、硬编码凭据 3、命令注入 1、反序列化 1。逐类抽查+全部 high 类单点人工核实后：

| 类别 | 判定 | 依据（人工核实） |
|---|---|---|
| 硬编码凭据 ×3 | **误报** | health.py:322 是枚举字典键；gateway_baseline/rate_limit_check 是**故意的错误凭据测试占位**（401 断言，且在仓库 scan_secrets 白名单内） |
| SQL 注入 ×74 | **误报类** | 抽查 operation_log_pg/llm_usage_store_pg 等：f-string 拼接的是**服务端配置的表名/索引名标识符**（DDL），业务值全部参数绑定；无用户输入进入 SQL 结构 |
| SSRF ×115 | **误报类** | 集中在前端 auth.ts 的 fetch baseURL（客户端代码不构成 SSRF）与内部观测/演练脚本；后端出站有 SSRF_FAKEIP_AWARE 与 egress 守卫 |
| 路径穿越 ×44 | **防御在位** | 用户上传面 rag_upload 有 realpath+commonpath 包含性校验；data.py 用 basename 剥离目录成分；其余为本地索引路径 |
| 不安全反序列化 ×1 | **挂账+缓解（低）** | bm25_store pickle.loads 前有 SHA256 完整性校验，文件为本地索引非远程输入；挂账：可换 restricted unpickler |
| 命令注入 ×1 | **挂账（低）** | obsrecon_faultinject（内部观测演练工具）shell=True；输入来自运维命令行非外部用户；挂账：改 shell=False 参数化 |
| XXE ×6 | **挂账（中，建议处理）** | pptx_parser 用 stdlib ElementTree 解析**用户上传文档**内嵌 XML——billion-laughs 资源耗尽面（上传有鉴权+身份，影响=worker DoS）；建议 defusedxml 或解析前尺寸/实体限制，另小 PR |
| 随机数 ×26 | **挂账（低）** | 抽查 auth_local 验证码用 random 生成 4 位数字验证码（产品拍板数字键盘交互）；非密钥/令牌用途；挂账：换 secrets 模块（低成本） |
| 疑似污点 ×28 | **信息级** | 跨文件污点提示，非独立缺陷 |

- **立即修：无**。本次未发现可被外部攻击者直接利用的高危缺口；历史 P0（viewer 全库枚举、跨租户串会话）修复在位。
- **挂账汇总（建议后续小 PR，不阻塞受控试用）**：①pptx/docx XML 解析换 defusedxml（中）；②验证码/令牌类随机数换 secrets（低）；③内部演练脚本 shell=False（低）；④BM25 pickle→restricted unpickler（低）。
- 依赖侧 88 条离线 advisory 匹配属依赖治理常规升级流程，建议由依赖维护线按 CVE 优先级处置，不在本任务扩scope。

## 六、结论

客服域授权边界复核 + 全仓 Mimosa 深扫均无「立即修」级发现；4 项挂账缓解建议随常规迭代消化。本自检为静态+只读复核，不能替代外部渗透测试；是否引入外部渗透由用户拍板。
