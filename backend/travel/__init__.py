"""backend/travel — 旅游规划域图

与 backend/customer_service 同级的独立子图：
  slot_filler → supervisor → 规则专家（poi/transit/budget/risk）
              → validator → （失败）局部修复 → reporter

对外只暴露：
  - get_travel_graph()  — 编译后的子图单例
  - build_travel_graph_result() — 输出契约（models/graph_result.py）

域的注册由 backend/travel/register.py 完成，经 backend/domains/__init__.py 触发。
"""
