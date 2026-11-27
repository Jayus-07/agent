# 任务规划：Tier 5 - backend/services/model_config.py 模块化解耦重构

## 1. 目标与背景
`backend/services/model_config.py` 当前为 3094 行的超大文件，作为全平台大模型与 Provider 的治理中心，承担了过度密集的异构职责：
1. **Provider 治理与凭据管理**：Provider 登记、配置读写、API Key 加密/掩码/解密、连通性探测
2. **角色模型映射（Role Mapping）**：多角色（主对话、旅游、客服、SQL、评测等）模型绑定、环境隔离、动态切换与版本历史
3. **模型兼容性与变更影响分析**：Embedding 模型切换的向量维度校验、索引兼容性预警、受影响知识库/向量集合探测
4. **模型健康巡检与部署探活**：在线状态定期探活、延迟测量、降级备选路由
5. **模型预算、配额与策略约束**：模型支持的能力标签（function_call, vision, json）、输入/输出 context window 规格、定价与配额策略

本任务依据马斯克五步骤（质疑需求 -> 删除多余 -> 简化优化 -> 加快周期 -> 自动化）以及 Domain Service / Facade 模式，将其解耦为专职子模块，并将原文件精简为轻量级统一 Facade 门面（控制在 250 行以内），同时 100% 保持对外公共契约和测试 Monkeypatch 的兼容性。

## 2. 改造范围
- 目标文件：`backend/services/model_config.py`
- 重构后结构：
  - `backend/services/model_config/` (专职子领域包)
    - `base.py`: 异常定义、公共类型定义、基础辅助函数与通用事务/数据模型
    - `providers.py`: Provider 登记、CRUD、凭据加密存储与解密掩码
    - `roles.py`: 角色模型映射、Fallback 解析、版本发布与激活
    - `compatibility.py`: Embedding 切换影响范围分析、知识库维度兼容性校验
    - `health.py`: 模型连通性检测、延迟探测与健康状态管理
    - `catalog.py`: 模型目录、规格参数（context window、能力标志）与定价策略
    - `__init__.py`: 导出公共接口与统一 Facade 组装
  - `backend/services/model_config.py`: 作为向后兼容的门面（Facade），保留现有 `ModelConfigService` 及模块级公共函数/常量
- 架构守卫测试：
  - `backend/tests/services/test_model_config_modular_architecture.py`

## 3. 验收标准
1. `backend/services/model_config.py` 单文件行数由 3094 行瘦身至 250 行以内（>90% 降幅）；
2. 现有引用 `from backend.services.model_config import ModelConfigService, ...` 100% 零改动正常运行；
3. 全仓现有涉及 `model_config` 的测试全部通过（包含 Monkeypatch 场景）；
4. 架构守卫测试防止出现循环导入或职责倒退；
5. 新建 PR 并通过全套 CI 门禁合入 main。
