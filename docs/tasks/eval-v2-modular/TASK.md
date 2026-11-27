# TASK: 评测中心接口 evaluation_v2.py 模块化解耦重构 (Tier 9)

## 1. 目标与背景
`backend/app/api/routes/evaluation_v2.py` 当前行数 **2347 行**，包含 45 个端点与 23 个模型/类。它是一个典型的 God Controller，承担了：
- 数据集管理、文件导入校验、候选集构建与评审（Datasets & Candidates）；
- 评测器版本管理与评测套件编排（Evaluators & Suites）；
- 评测运行执行、状态追踪、用例详情与人工复核（Runs & Execution）；
- 实验对比、门禁审批与审计事件追踪（Experiments, Gates & Audit）。

本任务目标：
依据单一职责原则（SRP），将 `evaluation_v2.py` 拆解为领域子路由包 `backend/app/api/routes/evaluation_subroutes/`，并构建具备 100% monkeypatch 穿透能力的纯门面透明代理 `FacadeProxy`，将主路由门面削减至 200 行以内（削减 >90%）。

## 2. 验收标准
1. **零功能回归与接口不变**：所有 45 个 REST 端点路径、入参、响应、HTTP 状态码及鉴权依赖 100% 保持不变；
2. **测试穿透性**：所有通过 `app.api.routes.evaluation_v2.<symbol>` 进行的属性读取或 monkeypatch 100% 穿透至新子模块；
3. **架构守卫**：门面 `evaluation_v2.py` 行数严格 < 250 行，子路由清晰划分；编写专门架构守卫单测；
4. **全量相关测试绿灯**：执行所有关联 API 与评测单元测试，100% 通过；
5. **Git 与 CI 闭环**：独立分支、PR 审查、CI 绿灯、Squash Merge、工作树清理收尾。
