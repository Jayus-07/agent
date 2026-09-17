"""selection_funnel/stages — 漏斗业务层（建池 / 初筛 / 验证 / 利润 / 排序）

分层纪律（与 travel experts 同源）：
  - 各层只做本层的事，产出结构化结果 + 淘汰原因，不悄悄吞掉候选
  - 判定阈值只读 backend/config/selection_funnel.py
  - 除 verifier 复用 selection.scoring 外，本包零 LLM —— 漏斗必须可复现
"""
