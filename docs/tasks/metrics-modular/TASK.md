# Task: Tier 13 Observability Metrics 模块化解耦重构 (metrics.py)

## 目标
- 将 1987 行的超大单体 `backend/observability/metrics.py` 进行系统化解耦；
- 按照领域观测维度构建专职子包 `backend/observability/metrics_domain/`；
  - 核心指标注册与工厂
  - RAG / Pipeline 指标收集器
  - 路由 / Router 指标收集器
  - 客服 / CS 与工单指标收集器
  - 流式延迟与 Tracking
  - 系统与通用指标导出（render_metrics）
- 门面 `backend/observability/metrics.py` 瘦身至 200 行以内，削减 >85%；
- 100% 保持所有导出指标对象（Counter、Histogram、Gauge、Summary）、记录函数及 render_metrics 接口完全兼容；
- 编写架构守卫测试 `test_metrics_modular_architecture.py`，加入 CI 清单，全量单测回归通过后合入 main。
