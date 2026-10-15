# P1-G 管理端总计划进度

> 更新：2026-10-15。最新 main：`5df8b0603355566e7fa1eb87906129c11a8211ab`（PR #70 与 P1-G 无关）。PR #54 已合并（merge commit `c40685d`）；PR #53 Ready for Review、未合并；PR #63、#65 开放；安全 PR #68 已更新至 Next.js 15.5.28，head `85edd1b` 的 frontend-admin CI、secret-scan、RAG PR Smoke 均 PASS，尚未合并。
> 本表对账仓库源码、PR 元数据、Review Threads 和本机实际命令。未执行项保持 BLOCKED/UNVERIFIED。

## 九模块进度

| # | 模块 | 状态 | 证据及下一步 |
|---|---|---|---|
| 1 | 运行总览 | **MERGED #54 / 部分验收** | 使用现有 Trace 样本与工作台。真实管理员 API 曾在 #54 证实；资源范围/Viewer 权限本轮网关无登录态，仍须补验。 |
| 2 | 执行记录与 Trace | **MERGED #54；分页/统计 IN PR #65** | #54 列表、详情与业务终态基础已交付；#65 增加留存范围内服务端分页、时间/状态筛选和授权统计，前端兼容最多 200 条样本展示；统计不代表全量历史。定向测试/tsc/build PASS，真人登录 E2E BLOCKED。 |
| 3 | Agent 与工作流 | **IN PR #63** | 使用既有 registry/Workflow 元数据；没有已验证的 Workflow Run 历史契约，不展示虚构数据。 |
| 4 | Tool 与 Capability | **EXISTS / ADAPT** | 页面、API、manifest/registry 已存在；#63 修复 editor 不应看到 admin-only Tool 入口。 |
| 5 | 模型与成本 | **EXISTS / ADAPT / 未验收** | 预算、价格、模型角色、Token 页面/API 已存在；读数需与现有账本、权限、币种口径对账。 |
| 6 | Prompt 版本 | **EXISTS / ADAPT / 未验收** | 页面和 Prompt API/发布路由已存在；发布/CAS/回滚/审计依赖原有服务。 |
| 7 | 权限与租户 | **EXISTS / 未验收** | 安全/RBAC/会话页面和路由已存在；跨租户资源范围真实验收未完成。 |
| 8 | 审批与审计 | **EXISTS / 未验收** | `/approvals` 和既有审核/审计来源存在；敏感操作 L3 需隔离环境补验。 |
| 9 | 服务健康与告警 | **IN PR #63 / 修复处理中** | 复用系统健康、降级日志、Skill 健康 API；新增端点管理员守卫及只显示安全聚合的页面修复。真实浏览器验收 BLOCKED。 |

## 三个交付包

| 包 | 范围 | 状态 | 完成证据/下一步 |
|---|---|---|---|
| **A 运行监控闭环** | #54 基线 + #63 Agent/Workflow、服务健康、降级事件、Skill 健康、Tool 链接角色修复 + #65 Trace 分页、筛选和授权统计 | **#54 MERGED；#63、#65 实现在 PR 中** | #63 两条 Review 线程已解决；#65 T0/T1/T2 PASS。真实登录和租户/资源权限验收 BLOCKED；依赖 #68 安全门禁后复验。 |
| **B 能力与成本治理闭环** | 接入已有 Tool/Capability、预算/价格/模型角色/账本、Prompt | **未开工；现有页面/API 已盘点** | 不重复建 Registry、统计、账本或 Prompt 状态机。按真实差距同批交付页面及必要 API 适配。 |
| **C 安全运维与统一验收** | 既有 RBAC/租户、审批/审计、权限及依赖安全门禁、统一 RC 验收 | **未开工；权限事实源已定位** | 必须完成跨租户/资源 L2、可用写路径 L3、回滚与适用漏洞门禁，才允许对外开放。 |

## 本轮已运行验证

| 等级/范围 | 命令或观察 | 结果 |
|---|---|---|
| 后端局部权限 | `pytest backend/tests/observability/test_admin_observability_reads.py backend/tests/observability/test_skill_health_severity.py` | **PASS：12 passed**；覆盖 admin/super_admin 与 viewer/editor 对降级告警及 Skill 聚合端点的守卫。 |
| 前端局部展示 | 三个受影响 Vitest 文件：serviceHealth、告警页、skills 页面 | **PASS：10/10**；含小写/驼峰诊断码、隐私字段投影、旧降级/Skill 统计恢复、Tool 入口角色。 |
| TypeScript | `npx tsc --noEmit` | **PASS**。 |
| Next 构建 | `npm run build`，独立 `npm ci --offline` 的 node_modules | **PASS：44 个页面生成完成**，未出现 Junction 文件追踪警告。 |
| PR #65 定向验证 | API TestClient 授权/分页/统计 23 passed；Vitest 11 passed；npx tsc --noEmit、npm run build、git diff --check | **PASS：T0/T1/T2**；真人登录与真实前后端 E2E **BLOCKED**，未连接不明数据库或读取凭据。 |
| 依赖漏洞 | PR #68：High/Critical audit 与功能 CI | **main 等待安全 PR 合并。** #68 已升至 Next.js 15.5.28；head `85edd1b` 的 run #6：`npm ci`、Vitest、tsc、build 和 High/Critical audit 全部 PASS；secret-scan 与 RAG PR Smoke PASS。 |
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
- GitHub `frontend-admin CI` run #1（`38550334257`）和 run #2（`38550889421`）的前端 job 均在依赖漏洞门禁失败，Vitest/TypeScript/构建步骤被跳过；run #2 的后端权限 job **PASS**（锁定依赖安装、管理员边界测试和 pip 缓存均成功）。前端局部测试/TypeScript/构建在本机通过，不能把 CI 中被跳过的步骤记为通过。
- PR #68 在 Next.js 15.5.28 head `11eedc4` 的 run #5（`38693004314`）中，High/Critical audit **PASS**，但功能 job 因锁文件依赖边仍指向 15.5.27 而在 `npm ci` 失败。提交 `85edd1b` 修正后，run #6（`38693533755`）的 `npm ci`、Vitest、TypeScript、Build 和 High/Critical audit 均 **PASS**；secret-scan run #145（`38693533739`）及 RAG PR Smoke run #298（`38693533875`）也 **PASS**。
- 门禁发现的既有依赖漏洞会让当前检查失败，这是有效的阻断信号。适用 high/critical 漏洞修复并验证前，不能标记“对外开放就绪”。本 job 覆盖观测只读端点管理员守卫；跨租户/资源级权限、其他敏感写路径和统一发布门禁仍须单独完成，不能由前端 CI 替代。
- 只运行管理端和受影响的后端测试；RAG/Travel 无关套件不是施工阻断条件。

## PR 依赖、阻断项和下一步

1. **#54 MERGED**：保留导航及总览/Trace 作为既有基线。
2. **#53**：四份计划文档现在对账 main `5df8b06`、#63/#65 与 #68 状态；该 PR 在合并前仍是候选版本，文档检查通过后按建议顺序先处理。
3. **#68（安全）**：head `85edd1b` 修复 Next.js 15.5.28 锁文件边；frontend-admin 功能/安全门禁、secret-scan、RAG PR Smoke 均 PASS，进入建议合并次序的第二位。
4. **#63**：两条 Review threads 已解决，定向权限/前端验证 PASS；真实 Admin/Viewer/Editor、跨租户和资源 E2E BLOCKED。#68 进入 main 后再更新基线和复验；在真实授权验收前不建议合并。
5. **#65（A 包）**：7f52c2b 已交付 Trace 分页/筛选/授权统计和现有页面接线；T0/T1/T2 PASS，T3 BLOCKED。与 #63 有两个共享文件但改动区域分离，依 #63 合并后复核文件级差异。
6. **B → C**：A 的只读整合后按现有缺口成组交付 B；权限/敏感写和对外开放门禁在 C 统一验收。

**当前阻断**：main 上 frontend-admin 依赖安全修复待 #68 合并；#68 候选版本的适用 CI 已全绿。隔离服务、测试账号和真实租户/资源 fixtures 未就绪，Admin/Viewer/Editor E2E BLOCKED。Trace 分页及授权统计在 #65，定向验证已过但未合并；其统计受授权范围和 TraceStore 留存窗口限制。APISIX、Prometheus/Grafana、LLM Provider、Workflow Run history 没有接口覆盖时，继续标为未覆盖。

**停止条件**：四份计划准确反映 main/PR 证据；#68 安全与功能 CI 全绿后才进入合并次序，#63 还须取得真实租户/资源授权验收。对外开放仍须等权限、安全、适用依赖漏洞与必要 L3 门禁 PASS。
