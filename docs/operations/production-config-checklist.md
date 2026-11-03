# 生产配置检查清单（audit-remediation-r1 P3/批次 C）

> 来源：全仓审计（`docs/tasks/audit-remediation-r1/`）收尾项。范围 = 审计明确要求落档的
> 4 项部署层检查。每项含 检查方法 / 期望值 / 当前状态；**未实际执行的验证标注 UNVERIFIED，
> 不得视为已通过**。复查周期：随每次生产发布走 release-quality 门禁。

## 1. GATEWAY_SESSION_CHECK（网关会话闸，APISIX 容器 env）

- **语义**：`apisix/plugins/gateway-auth.lua:245`——`off` / `audit`（默认，只记日志）/
  `enforce`（无会话键即 401）。app 进程读不到该 env，属部署层配置。
- **检查方法**：
  1. ECS 上 `docker inspect agent-apisix --format '{{json .Config.Env}}' | grep GATEWAY_SESSION_CHECK`；
  2. 交叉验证：管理端 `/api/admin/sys/security/overview` 的
     `modes.gatewaySessionCheck` 固定返回 `mode: null`（部署层值 app 读不到），**不可**以此判断运行值。
- **期望值**：`enforce`（会话撤销后下一次请求即被网关 401；audit 灰度期已结束）。
- **当前状态**：
  - 仓库口径：`docker-compose.yml:102` = `${GATEWAY_SESSION_CHECK:-enforce}`（缺省即 enforce）。
  - 本机共享栈 `.env:222` = `enforce`（**VERIFIED**，2026-11-04）。
  - **生产 ECS 实际值：BLOCKED**（2026-11-04：SSH 主机密钥于 11-03~11-04 间整体变更
    （ED25519/ECDSA 双变更），且 publickey 对 root/ubuntu × id_ed25519/agent 四组合
    全部 403——判定服务器重装后 authorized_keys 未恢复。**无法登录即无法核实**。
    恢复路径二选一：① 控制台/VNC 把本机 `~/.ssh/id_ed25519.pub` 追加进
    `/root/.ssh/authorized_keys` 后告知会话，本会话完成三项核实并回填；
    ② 用户自行在控制台执行本节三条检查方法并把输出交给会话回填。核实时请顺带
    比对 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`（当前=89hddvrj…）。）。

## 2. JWT_SESSION_GUARD_MODE（后端 Bearer 会话闸，第二道校验）

- **语义**：`backend/app/api/middleware/auth.py:122` + `backend/services/sys_config.py:65-69`——
  `off` / `audit`（默认，只记日志）/ `enforce`（401）；**DB 覆盖值 15s 内生效，免重启**，
  回滚 = `PUT /sys/config/{key}` 写回旧值。
- **检查方法**：
  1. 管理端 `/api/admin/sys/security/overview` → `modes.jwtSessionGuard`（含 DB 覆盖层实时值）；
  2. DB 直查：`SELECT value FROM sys_config WHERE config_key='JWT_SESSION_GUARD_MODE'`
     （无行 = 用代码默认 audit）。
- **期望值**：`enforce`（与网关闸构成纵深；off/audit 仅用于回滚窗口）。
- **当前状态**：**BLOCKED**（同第 1 项 SSH 访问丧失；该开关动态可变（DB 覆盖 15s 生效），
  恢复访问后必须现场读取，不能用历史记录替代）。

## 3. 9080（APISIX 入口端口）不出宿主机 / 安全组

- **语义**：9080 是网关 HTTP 入口。生产消费方 = 前端 BFF 与运维调试；不应向公网发布。
- **检查方法**：
  1. ECS 上 `ss -tlnp | grep 9080`——期望仅 `127.0.0.1:9080`；
  2. `docker port agent-apisix`——期望 `9080/tcp -> 127.0.0.1:9080`；
  3. 云控制台安全组——期望 9080 无 0.0.0.0/0 入站规则（或仅运维跳板 IP）。
- **期望值**：宿主机 loopback 绑定 + 安全组不放行公网。
- **当前状态**：
  - 仓库口径：`docker-compose.yml:88` = `"127.0.0.1:9080:9080"`；生产覆盖层
    `部署/prod-4c8g/docker-compose.local.yml` 未改写该绑定（仅前端端口 `127.0.0.1:*` 发布）。
  - 本机实测：`netstat` 显示 `127.0.0.1:9080 LISTENING`（**VERIFIED**，2026-11-04）。
  - **生产 ECS 实机绑定与安全组：BLOCKED**（同第 1 项 SSH 访问丧失；恢复后执行上述三条并回填）。

## 4. 依赖豁免记录（A1 分诊结论的豁免项与升级策略）

- **事实源**：`docs/tasks/audit-remediation-r1/evidence/osv_dedup_result.json`
  （2026-11-04 生成；方法学与逐包结论见同目录 PROGRESS.md「P3 批次 A」节）。
- **基线**：后端 requirements-lock 341 包 → OSV 全量审计（去 withdrawn + 别名去重）
  43 包 / 287 条独立公告；前端 ×3 npm audit 0 漏洞。
- **豁免/延期项（含理由）**：
  | 项 | 处置 | 理由 |
  |---|---|---|
  | pywin32==312（PYSEC-2021-112） | **豁免（疑似误报）** | 公告 fix=301 < 现版本 312，OSV 区间匹配异常；复核原文前按误报对待 |
  | pip==25.1.1 / setuptools==80.3.1 | 延期（随基础镜像） | 容器基建层，升级走 Dockerfile 基础镜像更新而非业务 lock |
  | filelock→4.x / oauthlib→4.0 / cryptography→50 / datasets→5.0 / setuptools→84 | 延期（跨主版本） | 有破坏面，需独立升级批次 + 全量回归，不与安全小修混批 |
  | nltk / PyJWT / chromadb / transformers 各 1 条无修复公告 | 挂起（上游无 fix） | 跟踪上游公告；其中 PyJWT 的 GHSA-gvp8-978c-rx2q 为 options-dict 变异类，规避路径见公告原文 |
- **待办**：33 个同主版本可升级包（pillow/pypdf/GitPython/aiohttp/PyJWT 主线等）
  **升级批次未立项**——升级需独立分支 + 定向回归 + 全量回归，作为 audit 后续工作项。
  **2026-11-04 更新**：第一批已落地（PR #310，24 包 + 连带 yarl，9 项延期理由见
  PROGRESS「P3 补遗批次」）；同日修正两点——①「可升级」结论自本日起一律先对目标
  版本跑 OSV 清零验证（pillow 12.3.0 实测仍命中 54 条，真正清零需 13.0.0 跨主版本，
  归入延期批）；② **生产 app 镜像不消费 lock**（Dockerfile 浮动解析 pyproject），
  生产暴露面需按容器内 `pip freeze` 另行审计（见 PROGRESS「P3 收尾修正」）。
- **复查方法**：重跑 A1 脚本（OSV querybatch，方法见 PROGRESS「P3 批次 A」）对照本表；
  npm 侧 `npm audit --package-lock-only` 三前端各一次。

## 附：本清单自身的维护规则

- 每项 UNVERIFIED 在实际验证后回填「当前状态」并注明验证日期与执行人/会话。
- 新增部署层安全开关（env/网关/DB 覆盖三类）时同步在本清单登记。
- 与 `docs/operations/release-quality.md` 的关系：本清单是其 §2「门禁范围-观测/运维」
  的生产落地细化，不替代发布门禁。
