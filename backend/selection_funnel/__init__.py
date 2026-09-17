"""selection_funnel — 智能选品漏斗域图

六层漏斗（定赛道 → 建池 → 初筛 → 竞品验证 → 利润测算 → 评分排序）实现为
LangGraph 独立域图，与 customer_service / travel 同级接入方式。

与其他选品资产的分工（勿混淆）：
  - backend/selection/scoring.py   五维潜力分（纯函数）—— 本域 verifier 直接复用
  - backend/selection/trends.py    快照趋势统计 —— 后续接入报告层
  - selection_decision workflow    单品决策（值不值得做）—— 本域是其上游候选供给
  - 本域（selection_funnel）       候选池 → 层层淘汰 → 推荐 Top-N，全程可解释
"""
