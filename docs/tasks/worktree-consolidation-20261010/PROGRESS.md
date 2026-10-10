# 整合进度

## 已完成

- 已备份 11 个有改动工作目录，逐文件记录 SHA256、时间和状态；保留原 index 与二进制补丁。所有读取时文件均稳定。
- 以 `origin/main=dffc21f` 创建独立 `task/worktree-consolidation-20261010` 分支。
- 合入 `codex/memory-travel-integration`，包含记忆候选及统一门户前端。

## 发现与待处理

- `main` 工作树暂存区与旧 `b178338` 完全一致：851 条暂存差异是直接更新引用后 index 未同步造成的旧内容反向差异，不是 851 条新成果。工作文件相对旧基线只有 RoutingEngine 的 31 个改动，已有 `beec7d2` 分支提交。
- 两版记忆候选共享主体，但较早候选独有可信服务端业务域绑定、游标时间类型修复及测试，需保留这些安全与正确性修复。
- 整合 RoutingEngine、Trace、文档和剩余新成果，执行定向验证后收口原目录。

## 集成审查与验证

- 合入较新门户与记忆候选，同时补回另一候选独有的可信请求域绑定、同时间戳分页游标修复与测试。
- 合入 RoutingEngine P0、Trace 分类/时间线回归、当前工作分支的整合提交以及手机号迁移回归。旧文档治理分支以当前文档为准，保留历史提交但不重新恢复已删除文档。
- 按文件提交日期选择冲突片段；Trace 资源级授权、租户过滤、SQL 问题原文不入 Span、客服 pending_action 清除契约作为不可降级边界保留。新增 SQL 选表埋点只记录问题长度。
- 从未提交快照补入 V1 存储退役保护、旅游会话幂等快照方法和实时查询失败分类；其余旅游代码仅同步静态语法等价的文档引用。保留新版 Reporter 默认开启、事实校验和新版 outbox 指标，未回退到旧副本。
- 修复合并造成的重复前端 import 和旧登录页面残留，采用较新门户提交的完整组件。
- 第一组后端定向测试：195 passed、1 failed（未配置数据库导致 router 导入时 feedback 初始化失败）。随后在独立 PostgreSQL 测试库复验故障用例及记忆/租户/安全边界：137 passed，67.56s，无 skip；包含快照、持久化 outbox、删除屏障、画像演进、分页和 Reporter/RAG 等。两组测试有重叠，不合计为独立用例数。
- 测试库 `agent_consolidation_20261010_1913` 只复制 `agent_memory` 的 schema，不复制业务数据；088/089 仅在该隔离库验证。未在共享业务库执行迁移。
- 前端定向 11 个测试文件：首次 93 passed、1 个 suite 因重复 import 失败；修复后该 suite 5 passed，共 98 项通过。管理端 StepTimeline：10 passed。
- 前端 TypeScript 检查通过；复用本机 node_modules，没有安装依赖。
- Tool 契约生成器 `--check`：IN_SYNC，BREAKING/DEGRADED/COMPATIBLE 均为 0。
- 254 个变更 Python 文件 AST 语法检查通过，Git 未解决冲突为 0，提交范围 `git diff --check origin/main HEAD` 通过。
- 测试保留 FastAPI 弃用与 Windows event-loop 清理警告；未运行全量回归、真实 LLM E2E或部署。

## 归档规则

- 11 个原工作目录在收口前与备份逐文件 SHA256、HEAD、Git 状态再次核验一致。
- 原暂存区与未提交代码保存到永久 `refs/archive/workspace-consolidation-20261010/<source>`，引用清单见备份目录 `archive-refs.json`。原有三份 stash 保留。
- 旧的 V1 删除接口测试、旧模板目录、9 月入库治理/客服试验、旧发布分支和过程报告保存为历史材料，不混入本轮最新主线。历史提交不删除，原工作树和忽略文件不删除。
- 运行时 `data/*.json`、救援 `_salvage` 与截图留在原地，通过本地 Git exclude 排除；不是提交业务数据或删除数据。
- 当前最新成果已通过正常 fast-forward 合入本地 main 和当前工作分支；代码与验证记录停止点为 `4e8001d`，本次不推送远程、不发布服务。

## 最终收口

- 实测全部 39 个 Git 工作目录 `Dirty=0`，无未提交、未暂存或未跟踪待处理文件；运行数据保持原位。
- 11 个永久归档引用全部存在，原有 3 份 stash 内容和顺序保留。
- 当前工作区、本地 main、独立整合分支保持同一 HEAD。远程 `origin/main=dffc21f` 未修改。
- 已删除本次创建的隔离数据库 `agent_consolidation_20261010_1913`；共享数据库未迁移、未删数据。
- 尚未合并的本地分支只剩历史架构/依赖撤销/发布候选/SSE/Router 试验，作为可恢复历史保留，不作为本轮最新成果。没有删除分支或原工作树。
- 备份恢复说明：`D:/tmp/agent-consolidation-20261010-backup/README.md`。
