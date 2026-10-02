# 旅游上线与并发修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复已审计的首屏、隔离、请求资源治理问题，提供可重复验收流程。
**Architecture:** 保留旅游图与 SSE，独立入口统一有界执行与身份 namespace。取消使用上下文控制并在节点、Tool、Provider 边界检查；不声称可强制杀死 Python 同步线程。生产多副本使用 Redis 执行租约并故障关闭。
**Tech Stack:** FastAPI、LangGraph、Redis、Prometheus、React、pytest、Vitest。
**Spec:** docs/reports/2026-10-02-旅游首屏与上线并发审计.md（用户已批准范围）

## 约束和验证关注

- 不覆盖其他会话未提交内容；不重建共享容器，不在共享环境进行容量压测。
- 不新增旅游域图、不修改主图 builder、不迁移交互链路到 Celery。
- 已执行的同步调用不可安全强杀；超时或取消后执行许可到实际执行结束才释放。
- 两用户同 conversation_id、同用户并行同会话、Redis 故障、事件队列满、取消后迟到结果必须覆盖。
- 首屏日期与消息同源；重复点击不得多次执行；取消后不能污染新行程。

## 任务

- [ ] 1. 用真实配置构造器验证独立 REST/SSE 用户隔离；先失败，再修复两入口与 namespace。
- [ ] 2. 首屏示例改为结构化条件同源生成，新增动态日期、连点、取消测试；运行前端测试和 tsc。
- [ ] 3. 新增可测试的请求执行治理：无待执行队列的本地有界池、同会话互斥、deadline、上下文取消、真实结束释放、指标；Redis 原子多副本租约故障关闭。
- [ ] 4. REST/SSE 共用执行治理；事件队列满必须输出错误终帧；节点/Tool/Provider 边界取消，Provider 池有界且 single-flight 锁回收。
- [ ] 5. 补生产配置、部署验收与压测工具、文档；回归与独立审查；路径限定提交。
- [ ] 6. 查服务归属后部署同一提交并实机跑三示例；在独立环境压测。涉及共享服务需报告影响，等待确认后执行，不虚报未执行步骤。

## 决策记录

默认本地执行上限 16，不据此宣称 100 并发已达标；生产 Redis 模式设置共享上限，容量用独立环境测量。既有非商用车票源生产配置必须关闭，不能用模拟结果冒充真实供应商验收。
