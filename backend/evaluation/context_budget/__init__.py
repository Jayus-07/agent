"""Phase 4 Context Budget Evaluation 套件（backend/evaluation/context_budget/）

模块：
- driver:       HTTP → APISIX → chat/stream 实机驱动（SSE 解析）
- golden_eval:  L5 事实保真 Golden Cases 双轨评测（Baseline vs After-L5）
- waterline:    水线三轮递进 / 最近4轮边界 / 并发单飞 验证
- run_context_budget_eval.py（backend/evaluation/ 下 CLI 入口）

红线：评测全程使用真实 LLM / 真实 DB / 真实 HTTP，不伪造结果；
原始 chat_messages 不因评测被删除或改写。
"""
