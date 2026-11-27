# TASK: Tier 8 - backend/app/api/routes/travel.py 模块化与单一职责重构

## 1. 目标与背景
`backend/app/api/routes/travel.py` 是旅游业务域的核心 API 控制器，原始代码量为 **2423 行**。
该单体混合了：
- 复杂流式 SSE 事件循环（`_travel_plan_stream_impl` 等 400+ 行）；
- 行程计划增删改查与状态流转；
- POI 搜索与推荐协调；
- 多天路线编排、日程预算与时序校验；
- 意图分析与上下文组装；
- 异常映射、重试与降级机制。

**重构目标**：
1. 建立专职子模块包 `backend/app/api/routes/travel_subroutes/`；
2. 抽离 Schema、基础常量、流式调度、计划规划、POI 处理与子路由集合；
3. 门面保留在 `backend/app/api/routes/travel.py`，降级为超轻量代理门面（目标 < 250 行，削减 > 90%）；
4. 通过 `FacadeProxy` 确保历史导入与外部 monkeypatch（如对函数、生成器、配置常量的补丁）100% 穿透生效；
5. 编写架构守卫测试，并确保 100% 通过现有 travel 相关测试套件；
6. 创建 PR，通过 CI 门禁并 Squash Merge 合入 main。

## 2. 验收标准
- [x] 原文件 `travel.py` 代码量削减至 222 行（原 2423 行，削减率 **91.1%**）；
- [x] 存量 `router` 契约、所有 HTTP endpoints、状态码、响应体 100% 保持不变；
- [x] `FacadeProxy` 支持外部 monkeypatch 动态覆盖；
- [x] 新增 `test_travel_modular_architecture.py` 架构守卫测试（5/5 passed）；
- [x] 运行关联测试 13 个测试套件：**153 passed in 46.59s**（100% 绿灯通过）；
- [ ] GitHub PR 通过全部 CI 检查并 Squash Merge 合入 main。
