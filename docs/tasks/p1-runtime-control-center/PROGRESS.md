# P1-G 管理端总计划进度

> 更新：2026-10-15。最新 main：`24a0182e918fd7a963e5dcf9a8cd2550f3365ce4`，已含 PR #53（计划）与 PR #68（依赖安全修复）；PR #54 也已合并。PR #63 head `3ca4906` 开放，自动化管理端及权限门禁 PASS，真实授权 E2E BLOCKED。PR #65 head `5d9ef18` 开放，Review 已修复/解决；定向验证、frontend-admin CI（含 build）、High/Critical audit、结构检查、secret-scan 均 PASS，真实隔离 API E2E BLOCKED。
> 本表对账仓库源码、PR 元数据、Review Threads 和本机实际命令。未执行项保持 BLOCKED/UNVERIFIED。

## 九模块进度

| # | 模块 | 状态 | 证据及下一步 |
|---|---|---|---|
| 1 | 运行总览 | **MERGED #54 / 部分验收** | 使用现有 Trace 样本与工作台。真实管理员 API 曾在 #54 证实；资源范围/Viewer 权限本轮网关无登录态，仍须补验。 |
| 2 | 执行记录与 Trace | **MERGED #54；分页/统计 IN PR #65** | #65 head `5d9ef18` 增加留存范围内服务端分页、时间/状态筛选和授权统计，前端兼容最多 200 条样本展示；统计不代表全量历史。Review 两项修复并解决；后端 11、Vitest 10、tsc、CI build、安全 audit PASS；真人登录 E2E BLOCKED。 |
| 3 | Agent 与工作流 | **IN PR #63** | head `3ca4906` 使用既有 registry/Workflow 元数据；没有已验证的 Workflow Run 历史契约，不展示虚构数据。代码门禁 PASS，真实登录与权限 E2E BLOCKED。 |
| 4 | Tool 与 Capability | **EXISTS / ADAPT** | 页面、API、manifest/registry 已存在；#63 修复 editor 不应看到 admin-only Tool 入口。 |
| 5 | 模型与成本 | **EXISTS / ADAPT / 未验收** | 预算、价格、模型角色、Token 页面/API 已存在；读数需与现有账本、权限、币种口径对账。 |
| 6 | Prompt 版本 | **EXISTS / ADAPT / 未验收** | 页面和 Prompt API/发布路由已存在；发布/CAS/回滚/审计依赖原有服务。 |
| 7 | 权限与租户 | **EXISTS / 未验收** | 安全/RBAC/会话页面和路由已存在；跨租户资源范围真实验收未完成。 |
| 8 | 审批与审计 | **EXISTS / 未验收** | `/approvals` 和既有审核/审计来源存在；敏感操作 L3 需隔离环境补验。 |
| 9 | 服务健康与告警 | **IN PR #63 / 自动化 PASS** | 复用系统健康、降级日志、Skill 健康 API；新增端点管理员守卫及安全聚合展示。真实 Admin/Viewer/Editor 与租户/资源验收 BLOCKED。 |

## 三个交付包

| 包 | 范围 | 状态 | 完成证据/下一步 |
|---|---|---|---|
| **A 运行监控闭环** | #54 基线 + #63 Agent/Workflow、服务健康、降级事件、Skill 健康、Tool 链接角色修复 + #65 Trace 分页、筛选和授权统计 | **#54、#53、#68 MERGED；#63、#65 OPEN** | #63 管理端 CI、管理员守卫、secret scan PASS，真实角色/租户/资源 E2E BLOCKED。#65 两条 Review 已修复并解决；定向回归、tsc、audit、CI build PASS；隔离真实 API E2E BLOCKED。 |
| **B 能力与成本治理闭环** | 接入已有 Tool/Capability、预算/价格/模型角色/账本、Prompt | **未开工；现有页面/API 已盘点** | 不重复建 Registry、统计、账本或 Prompt 状态机。按真实差距同批交付页面及必要 API 适配。 |
| **C 安全运维与统一验收** | 既有 RBAC/租户、审批/审计、权限及依赖安全门禁、统一 RC 验收 | **未开工；权限事实源已定位** | 必须完成跨租户/资源 L2、可用写路径 L3、回滚与适用漏洞门禁，才允许对外开放。 |

## 本轮已运行验证

| 等级/范围 | 命令或观察 | 结果 |
|---|---|---|
| 后端局部权限 | `pytest backend/tests/observability/test_admin_observability_reads.py backend/tests/observability/test_skill_health_severity.py` | **PASS：12 passed**；覆盖 admin/super_admin 与 viewer/editor 对降级告警及 Skill 聚合端点的守卫。 |
| 前端局部展示 | 三个受影响 Vitest 文件：serviceHealth、告警页、skills 页面 | **PASS：10/10**；含小写/驼峰诊断码、隐私字段投影、旧降级/Skill 统计恢复、Tool 入口角色。 |
| TypeScript | `npx tsc --noEmit` | **PASS**。 |
| Next 构建 | `npm run build`，独立 `npm ci --offline` 的 node_modules | **PASS：44 个页面生成完成**，未出现 Junction 文件追踪警告。 |
| PR #65 Review 修复验证 | 后端授权/分页/Tool 批读 11 passed；受影响 Vitest 10 passed；TypeScript 与 `git diff --check` PASS；CI run #10 安全 audit、npm ci、Vitest、tsc、Next build 均 PASS | **自动化 PASS。** 隔离真实 API E2E **BLOCKED**；未连接不明数据库或读取凭据。 |
| 依赖漏洞 | PR #68 已合并 main | **PASS。** Next.js 15.5.28；run #6 的 `npm ci`、Vitest、tsc、build 和 High/Critical audit 全通过；secret-scan 与 RAG PR Smoke PASS。#65 run #10 的 High/Critical audit 也 PASS。 |
| 本机服务/只读 API | 本轮只探测本机服务监听；未重试无凭据 API 请求 | 前端和网关未监听；仅观察到 8000、5432、6379。8000 与数据库是否隔离未知，未连接或写入。 |
| 真实管理员 API/E2E | 未从文件、环境、进程或浏览器导出/读取/复制任何 API_KEY、Cookie 或 Token | **BLOCKED**：当前只有未认证请求；没有可用于浏览器的正常管理员会话证据。不能把接口 401 报为服务故障或 E2E PASS。 |
| L3 敏感写入 | PR #63 无新增敏感写接口 | **不适用本 PR；未验证，不报 PASS。** 对后续现有写路径需隔离租户、审计和回滚。 |

### 真实 API 补验入口

1. 在服务维护者正常启动的本机网关/后端环境中，运行现有 `frontend-admin`，使用正常浏览器登录页建立已有管理员登录态；不向验收人员导出凭据。
2. 在管理端打开系统健康与告警、Agent/Workflow、执行记录/Trace、Skill 页面；浏览器 Network 记录管理员只读 API 的状态、数据时间/来源与页面结果。
3. 用现有 Viewer/Editor 正常账号直接打开 admin-only Tool、全局告警/Skill 聚合端点及跨租户资源，确认页面不误导且服务端返回 403/404。若没有现成账号或租户夹具，保持 BLOCKED，由系统维护者提供隔离验收环境后补验。
4. 敏感写操作单列 L3；只对已存在写接口使用隔离租户和可回滚数据，检查允许/拒绝、CAS/幂等、审计、查证与回滚。本 PR 不执行写操作。

## CI 与安全门禁

- `.github/workflows/frontend-admin.yml` 已随 `62a8fad` 推入 PR #63：仅管理端及相关权限测试/workflow 变化触发；Node 20、npm/pip 缓存，安全 audit 与功能 job 独立运行，Vitest、TypeScript、Next 构建，不运行无关 RAG/Travel 评测。
- `ca7ea48` 将触发范围扩展到观测管理员路由/权限测试及 Python 依赖，增加带 pip 缓存的定向后端权限 job；不执行无关 RAG/Travel 测试。
- npm 镜像不支持 audit API，本机用官方 registry 才得到可用审计结果；workflow 明确指定官方 registry。
- 早期 `frontend-admin CI` run #1/#2 曾因旧依赖漏洞门禁失败，后由 #68 修复并合入 main；最新 #63 run #8 与 #65 run #10 中管理员权限（#63）、安全 audit、依赖安装、Vitest、TypeScript、Next build 全部 **PASS**。
- PR #68 run #6（`38693533755`）的 `npm ci`、Vitest、TypeScript、Build 和 High/Critical audit 均 **PASS**；secret-scan run #145（`38693533739`）及 RAG PR Smoke run #298（`38693533875`）也 **PASS**。安全修复现已进入 main。
- 门禁发现的既有依赖漏洞会让当前检查失败，这是有效的阻断信号。适用 high/critical 漏洞修复并验证前，不能标记“对外开放就绪”。本 job 覆盖观测只读端点管理员守卫；跨租户/资源级权限、其他敏感写路径和统一发布门禁仍须单独完成，不能由前端 CI 替代。
- 只运行管理端和受影响的后端测试；RAG/Travel 无关套件不是施工阻断条件。

## PR 依赖、阻断项和下一步

1. **#54 MERGED**：导航及总览/Trace 基线。
2. **#53 MERGED**：权威三包计划已生效。
3. **#68 MERGED**：安全依赖修复及 High/Critical 门禁已进入 main。
4. **#65 OPEN，head `5d9ef18`**：两条 Review 已修复并解决；后端 11、Vitest 10、tsc、run #10 的 High/Critical audit 和 Next build PASS。真实隔离 API E2E BLOCKED。共享文件：`backend/app/api/routes/observability.py`、`frontend-admin/src/api/observability.ts`。
5. **#63 OPEN，head `3ca4906`**：run #8 的管理员权限、High/Critical audit、Vitest、TypeScript、Build 全 PASS；真实 Admin/Viewer/Editor、跨租户/资源 E2E BLOCKED。若 #65 先合并，#63 应更新到新 main 后重跑受影响检查。
6. **B → C**：A 的只读闭环后按现有缺口成组交付 B；权限/敏感写与对外开放门禁在 C 统一验收。

**当前阻断**：#68 安全修复已合并；#63 和 #65 所需的隔离服务、测试账号、租户/资源 fixtures 均未就绪，真实 API E2E BLOCKED。#65 自动化门禁全绿，统计仅代表授权范围和最多 5,000 条 TraceStore 留存。APISIX、Prometheus/Grafana、LLM Provider、Workflow Run history 没有接口覆盖时继续标为未覆盖。

**建议顺序**：#53 → #68 → #71（计划/状态同步；#53/#68 已合并）→ #65（隔离真实只读 API 验收完成）→ #63（更新到最新 main，再完成角色/租户/资源授权验收）。当前不建议合并 #63/#65。对外开放仍须通过权限、安全、适用依赖漏洞与必要 L3 门禁。
