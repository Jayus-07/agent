# 任务进度：Tier 5 - backend/services/model_config.py 模块化解耦重构

## 当前状态
- [x] Step 1: 创建独立 Git Worktree `agent-model-config` (branch: `refactor/model-config-service-modular`)
- [x] Step 2: 初始化 TASK.md 与 PROGRESS.md 任务追踪文档
- [x] Step 3: 分析 `backend/services/model_config.py` 内部结构、符号导出与依赖网络
- [x] Step 4: 建立 `backend/services/model_governance/` 领域子模块并迁移实现
  - `base.py` (205 行): 基础异常、核心常量、数据清洗与 DependencyProxy 透明依赖代理
  - `health.py` (142 行): 健康探活、探针记录与人工状态置失效 (`HealthMixin`)
  - `compatibility.py` (196 行): 向量维度兼容性校验与 Embedding 变更预检 (`CompatibilityMixin`)
  - `roles.py` (281 行): 角色模型映射、Fallback 解析与运行策略 (`RoleConfigMixin`)
  - `specialized.py` (444 行): 专精模型（Embedding / Rerank 专用通道）配置 (`SpecializedMixin`)
  - `providers.py` (836 行): 供应商元数据、凭据加密与网络范围 (`ProviderMixin`)
  - `models.py` (936 行): 模型目录、单价校验落库与能力标签管理 (`ModelCatalogMixin`)
  - `history.py` (339 行): 变更历史审计、快照回滚与配置漂移分析 (`HistoryMixin`)
  - `__init__.py` (88 行): 统一聚合服务门面 `ModelConfigService`
- [x] Step 5: 重构 `backend/services/model_config.py` 为纯 Facade 门面（124 行，削减 96.0%）并保持所有模块级符号与 monkeypatch 100% 兼容
- [x] Step 6: 编写架构守卫测试 `test_model_config_modular_architecture.py` 并补充进入 `.github/workflows/rag_smoke.yml`
- [x] Step 7: 运行全量单元测试与本地服务验证（146 passed in 38.80s）
- [ ] Step 8: 提交 PR，等待 CI 验证，Squash Merge 合入 main
- [ ] Step 9: 清理临时工作树与收尾校验
