# 质量门禁与发布前检查

> 面向本机直接部署和未来正式上线。开发中的文档变更与真实生产可用性是两回事；本规范**不强制灰度或影子双跑**，但安全、授权、数据和副作用 P0 未关闭时不能宣称生产就绪。

## 1. 必选检查随变更范围增长

| 门禁 | 适用 | 最小证明 |
| --- | --- | --- |
| T0 | 纯文档、脚本格式、静态声明 | 文档链接/路径存在、Lint/类型或声明校验，git diff 与敏感信息检查 |
| T1 | 局部 Bug / 非公共模块 | 定向单元测试 + 原缺陷复现 + 关键失败分支 |
| T2 | 鉴权、租户、公共契约、Router、Tool、SSE、状态机、目录移动 | API/Graph/Skill/DB/客户端关联测试、越权/并发/重试/降级契约 |
| T3 | 发布级改动、业务数据升级、外部 Provider 切换 | 隔离真实集成、端到端、迁移、告警与恢复演练，发布清单完整 |

以 [testing-guide](../development/testing-guide.md) 为准，不因测试数量大每次都全量跑。测试资产基线审计已归档（2026-10-20，Git 历史可检索 `docs/tasks/test-asset-governance/AUDIT.md`）；用例级硬超时以根目录 pytest.ini 配置为准。

## 2. 门禁范围

1. **代码**：语法/类型/Lint，禁绕过已存在的 registry/runtime/schema 和权限；修改的路径无明显死引用。
2. **安全**：服务端认证、RBAC、资源级 IDOR、租户隔离、SQL 只读与参数化；写操作审批/确认/幂等/UNKNOWN 处理。
3. **业务**：成功、业务失败、部分结果、Provider 超时、用户硬依赖、空数据、失败重试与回退不能导致假成功。
4. **API/前端**：REST、SSE、WS 错误和终态一致；多客户端变更覆盖相应调用方；桌面/移动按受影响面验收。
5. **数据库/队列**：迁移目标库、写入版本 CAS、备份/恢复、Worker 投递和重复消费语义；禁止在共享库上跑破坏性测试。
6. **模型/数据**：Prompt 版本、成本/token 归因、引用证据、用户数据泄漏检查；工具/Provider 降级要可解释。
7. **观测/运维**：健康和就绪、Trace/指标/日志、仪表盘/告警可用、成本和业务失败可定位；密钥只走受控配置。
8. **交付**：实际执行命令、结果统计、环境、耗时、未覆盖范围、已知 WARN/BLOCKED、回滚办法、提交与 PR。

CI 只算本次真正执行的工作流结果；条件 SKIP 不能叫“全链路通过”。原有仓库 workflow 如缺 Python/TypeScript 定向检查，应单独排期加入而非文档中假称已有。依赖缓存与浏览器隔离遵循 [commands](commands.md)；不为一次文档更改重新下载 Chrome/Docker 镜像。

## 3. 合并与发布

1. 对照 `main` 最新 SHA、工作树状态和本 PR 变更范围；风险项按 T0–T3 补全。
2. 文档与实际代码/Schema 同步；所需的评审、CI 状态完成后正常合并，不强行绕过受保护分支。
3. 正式发布必须从干净且已提交的工作树构建；`scripts/release.sh` 先用 `docker compose build --print` 解析目标服务的镜像标签，构建后直接检查这些镜像的身份，再启动服务，避免通过旧容器反查旧镜像。运行发布脚本的 Docker Compose 需支持 `build --print`，不支持时会在启动服务前拒绝发布。Release Gate 0 校验运行容器的 commit 与发布 commit 匹配、构建时间已知且 `dirty=false`。本地直接发布可不做灰度/影子双跑；但必须确认依赖就绪、数据库兼容、关键用户路径和回滚方式。
4. 上线后检查错误率、关键流程状态、Tool/Provider 降级、数据库连接和 AI 成本；发现重大异常立刻停止发布/回滚。
5. P0 风险未闭环、没有真实 Provider 凭据或 E2E 未执行，要如实标注 `BLOCKED` / `NOT VERIFIED`；不能给总项目打“生产 READY”。

### GitHub Actions 生产发布

本仓库新增两段式发布流水线：

1. PR 检查通过后仍需合入 `main`；只有合入的变更触及应用或部署文件时，`.github/workflows/publish-images.yml` 才构建后端和三个前端、推送到 GHCR，并保存带四个镜像 digest 的 `production-image-digests` Artifact（保留 90 天）。纯文档变更不发镜像。
2. 镜像发布成功后，`.github/workflows/deploy-production.yml` 自动验证源码 SHA、Artifact 和四个 digest，再进入 `production` Environment 等待审批；审批通过后再次确认 `main` 仍指向这次镜像的 SHA，避免较早的发布排在后面覆盖新版本。该工作流也保留手动入口，用于重新部署或回滚到此前成功发布的 run ID。
3. 部署 job 必须由生产主机上的专用 Linux x64 self-hosted runner 执行。它拉取并检查镜像身份，调用 `scripts/release.sh --skip-build`；该模式先从已拉取的后端镜像运行 Tool 契约检查并执行数据库迁移，再校验迁移状态，随后强制 `docker compose up --no-build`，最后做健康检查和 Release Gate。镜像按 digest 固定，不使用可变 `latest`。

首次启用前，仓库管理员需要配置：

| 位置 | 必填项 | 用途 |
| --- | --- | --- |
| GitHub Actions Environment `production` | Secret `RELEASE_PASSWORD`；启用指定审批人/保护规则，并只允许 `main` 部署 | 在生产 job 启动前人工批准，并运行发布质量门禁 |
| GitHub Actions Environment 或 Repository Variables | `PRODUCTION_RELEASES_DIR`、`PRODUCTION_ENV_FILE`、`PRODUCTION_EVAL_RUNS_HOST_DIR`、`PRODUCTION_PG_BACKUP_HOST_DIR` | 覆盖默认生产路径；默认值见工作流 |
| Repository Variables（按需） | `PRODUCTION_ADMIN_URL`、`PRODUCTION_CS_URL`、`PRODUCTION_CS_WS_ORIGIN` | 构建前端时设置正式站点地址；未配置时使用当前 `anyanyan.me` 域名 |
| `main` 分支保护规则 | 要求 PR 合并和所需 CI 状态通过，禁止绕过保护直接推送 | 保证只有通过仓库 PR 门禁的 main 提交触发生产流水线 |
| 生产主机 | Actions Runner `v2.327.1+`；专用 runner 标签 `agent-production-deploy`；Docker CLI/Compose、GHCR 出网；runner 账号可读生产 env 文件、写版本目录、访问 Docker daemon 和应用数据目录 | 当前 Action 使用 Node 24；迁移检查和健康检查都访问本机服务，因此 runner 必须与生产 Docker 主机同机 |

runner 拥有 Docker daemon 权限，等同于生产主机高权限账号；应使用专用账号和 runner group，只允许这个仓库和受保护的部署工作流使用，不复用通用 CI runner。不要把管理员 SSH 私钥或生产环境文件复制到 GitHub Secrets。当前版本只在工作流里创建版本目录，不创建或改权限生产数据目录；主机路径、目录属主和 `.env.production` 需由管理员按现有备份与数据盘规则预先准备。

部署完成后的回滚方式是手动运行部署工作流并选择此前成功的镜像发布 run ID。Artifact 仅保留 90 天，过期后需重新发布对应源码。应用镜像可回退不代表数据库结构一定兼容；迁移采用前向兼容与独立恢复流程，禁止把旧容器启动成功当成数据库回滚完成。该流水线不承诺零停机，首次真实发布前仍需单独做生产验收和恢复演练。
