# Progress: Tier 13 Observability Metrics 模块化解耦重构 (metrics.py)

- [x] 独立 Git 工作树 `agent-metrics` 创建完成（分支 `refactor/metrics-modular`）
- [x] 配置环境与测试隔离准备
- [x] 存量测试基线确认（34 项指标与运行时测试 100% 通过）
- [x] 领域子包与指标分类设计（8 个专职模块，覆盖 247 个导出符号）
- [x] 物理拆解与指标收集器抽离（`metrics_domain/{base, stream_tracker, core_sli, rag_metrics, router_metrics, cs_metrics, infra_metrics, exporter}.py`）
- [x] 门面瘦身与契约 100% 重导出（`backend/observability/metrics.py` 1987 行 -> 58 行，削减 97.1%）
- [x] 架构守卫测试编写与 CI 清单更新（`test_metrics_modular_architecture.py` 5/5 通过，加入 `rag_smoke.yml`）
- [x] 本地全量回归验证（34 存量 + 5 架构守卫 = 39 项测试 100% 绿灯）
- [ ] PR 提交、CI 门禁与合并收尾
