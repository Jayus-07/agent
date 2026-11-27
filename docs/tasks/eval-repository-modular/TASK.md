# 任务契约：评测数据仓储核心（backend/evaluation_v2/repository.py）物理模块化重构

## 1. 目标与背景
- **原状**：`backend/evaluation_v2/repository.py` 单文件长达 **4016 行**，包含 **71 个顶层函数**，是全代码仓行数第一的技术债务巨石。单文件杂糅了 Runs 执行生命周期、Datasets 数据集与版本、Gates 准入门禁与审批流、Ledger 审计流水、Rubric 打分准则与全量导出等 6 种以上异构实体域，严重违反单一职责原则（SRP）。
- **目标**：按照 **Domain Repository（领域仓储模式）**，将 4016 行巨石拆解为专职的子包 `backend/evaluation_v2/repositories/`，同时保留 `repository.py` 作为纯 Facade 协调门面（代码行数限制在 200 行以内）。
- **红线与约束**：
  - 100% 保持向后兼容：所有 71 个公共函数与异常类由 `repository.py` 完整导出，外部无感知，调用点零漂移；
  - 100% 保持事务原子性、数据库连接安全与租户多隔离机制；
  - 通过全量单测回归与 GitHub Actions CI 检查。

## 2. 架构设计与模块划分
- `backend/evaluation_v2/repositories/`:
  - `base.py`: 基础异常定义（`RepositoryConflictError`, `RepositoryNotFoundError`, `RepositoryExportLimitError`）、统一事务装饰器、审计埋点辅助与通用哈希序列化；
  - `runs.py`: 评测运行（Runs & Executions）生命周期、环境校验、状态流转与单次执行持久化；
  - `datasets.py`: 评测集与版本生命周期、覆盖率计算、领域分类与用例（Cases）读写；
  - `gates.py`: Release Gate 门禁准入判定、决策记录与审批流；
  - `rubrics.py`: 人工评审准则、打分量表与指标定义；
  - `ledger.py`: 评测 Ledger 审计流水、事件聚合与归档；
  - `exports.py`: 评测大结果集流式游标导出与格式转换；
  - `__init__.py`: 汇聚导出所有仓储符号。
- `backend/evaluation_v2/repository.py`:
  - 门面层，行数削减至 200 行以内，统一 re-export 所有子包符号。

## 3. 验收标准
1. `backend/evaluation_v2/repository.py` 代码行数 < 200 行；
2. 新建架构守卫测试 `test_repository_modular_architecture.py`，验证 71 个符号导出完整性与行数门禁；
3. `backend/tests/evaluation_v2/` 现有单测 100% 通过；
4. PR CI 门禁全绿通过并 Squash Merge 合入 main。
