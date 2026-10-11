# 仓库目录、文件归属与清洁规范

> **目标布局与整理规则，不要求立即大搬家。** 路径是否活跃由 import、路由/注册、动态发现、脚本、CI、配置、容器卷和历史任务依赖共同判定。目录名称相似不等于重复。

## 1. 当前稳定目录的职责

| 目录 | 归属 | 规则 |
| --- | --- | --- |
| `backend/app/api/` | 接入层 | 路由聚合、DTO、会话/认证入口；业务状态落领域服务 |
| `backend/orchestration/` | 主图、路由、运行控制 | workflow/workflows 区分后才能合并；注册机制不得悄悄丢失 |
| `backend/core/`、`backend/infra/` | 节点/Tool 执行底座、协议基础设施 | 公共能力单一事实源；避免反向 import |
| `backend/{travel,travel_v2,customer_service,rag,sql,selection,selection_funnel}/` | 业务领域 | 多子域可并存；只因目标边界明确才合并 |
| `backend/providers/`、`mcp_servers/` | 外部适配器 | 查询与行程卡/领域服务可共用 Provider；MCP 不复制业务规则 |
| `backend/tests/`、`tests/` | 自动化回归 | 有效契约测试不能凭名称或耗时删除；旧测试须用替代覆盖证据换出 |
| `frontend/`、`frontend-admin/`、`frontend-cs/` | 用户端、管理端、客服端 | 可以抽取经过验证的共享客户端契约；暂不强行做 monorepo |
| `data/` | **混合路径**：种子/fixture 与运行数据 | 分类后迁移或停止跟踪；不可整目录删除 |
| `tmp/`、`artifacts/`、`gui-test-screenshots/` | 临时调试与测试证据候选 | 先判引用和备份目的，再迁移/取消跟踪；默认不作为源码 |
| `scripts/`、`docker/`、`apisix/`、`.github/` | 工具脚本、部署、网关、CI | 修改引用路径时必须同步 Docker/Actions/Windows 脚本 |
| `docs/architecture`、`development`、`domains`、`operations`、`tasks` | 现行规范、领域、运维、任务 | [文档标准](documentation-standard.md) 是唯一维护规则 |

不要新建平行的 `common/`、`utils2/`、`legacy2/`、`tool_registry_v2/` 来绕过现有分层。新模块进入最近的业务/基础设施所有者；无法判断归属时先建立设计记录。

## 2. 文件分类必须有证据

| 标签 | 判据与动作 |
| --- | --- |
| KEEP | 活跃 import/动态注册/迁移/有效测试/部署或业务依赖 |
| FIX | 职责正确但参数、错误、授权、日志或契约不一致：原地修正 |
| MERGE | 两份真正重复的逻辑且有可替代调用点：抽出唯一实现，再改引用 |
| MOVE | 归属错位，所有静态+动态入口可同步调整；使用有记录的 rename/迁移 |
| DELETE | 明确无人引用的生成物/失效文件；已有原件/备份可恢复 |
| REVIEW / BLOCKED | 证据不全、其他工作树施工中、涉及数据或发布依赖：只记录不删除 |

按**是否 Git 跟踪**区分 `gitignore` 与已跟踪文件：ignore 不能自动让已提交对象消失。对数据类文件默认不做 `git rm` / `git rm --cached`；先查来源、合规性、被测依赖和存放方案，由用户确认批量清理。历史里已存在的敏感内容即使从 HEAD 删除，也需要独立安全处置。

## 3. 整理优先级

1. **低风险**：索引入口、重复 import、文档断链、明确的临时生成物清单、脚本名称与说明；不触碰数据库或业务。
2. **中风险**：重复 DTO、前端 API 客户端、公共错误映射、报表/缓存/Provider 辅助函数。先做行为一致性测试，再迁移。
3. **高风险**：Domain Graph/Router、Checkpoint、审批/幂等、数据库迁移、workflow 注册、用户身份/租户、跨进程资源。先出 ADR/依赖图/回滚，再分批实施。

**不在大规模目录移动时顺手做业务优化**。每批只调整一个清晰边界，保留行为和路径兼容，确保能够通过单次 revert 回滚。执行细则见 [安全重构手册](refactoring-playbook.md)。
