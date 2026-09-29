"""travel/core/intent_signals.py — 会话意图信号（重开 / 取消），Phase 2 迁入

跨层事实源：orchestration 层（travel_graph_node / travel_pending_resolver）
与旅游域图节点共用这里的判定，语义必须逐字一致——搬运零改动。

注意：is_avoid_patch_query **不在本模块**——它内部调用 avoid 抽取
（agents 层），进 core 会造成底座反向依赖业务层；过渡期真身在
travel/slot_filler.py（Phase 5 生命周期重构时重新评估归位，
当前不是最终架构归属）。
"""
from __future__ import annotations

import re

# NEW_RUN 显式信号（STOP F2，单一事实源在本模块）：用户明确推翻当前规划
# 重开。普通补槽/改约束（CONTINUE/PATCH/REPLAN）不含这些词。路由层
# resolver 与 slot_filler/adapter 都以这里为准（context → travel 单向
# 依赖，禁止反向 import 造成循环）。
# 「不想去」必须收紧为「不想去了」（STOP I2）：带地点的「不想去鼓浪屿了」
# 是 PATCH avoid，不是重开规划——裸「不想去」会把点名排除句误判成 NEW_RUN。
_NEW_RUN_RE = re.compile(
    r"重新规划|重新安排|重新来|换个方案|换套方案|换一个方案|不去了|不想去了"
)


def is_new_run_query(message: str) -> bool:
    """消息是否为「重开规划」显式信号（纯函数）。"""
    return bool(_NEW_RUN_RE.search(message or ""))


# CANCEL_RUN 显式信号（STOP G3）：取消**整个**规划任务。保守词表——
# 只认「规划/行程/安排」整体对象的取消；「不去海游馆了」类点名局部
# 景点的排除句是 PATCH avoid，由 _CANCEL_EXCLUDE_RE 先行排除（宁可
# 漏判走正常图处理，不可误取消整趟规划）。
_CANCEL_RUN_RE = re.compile(
    r"取消(整个|这次|本次|当前的?)?(规划|行程(规划)?|安排|旅行|旅游|计划)"
    r"|(规划|行程(规划)?|安排|计划)(不用|别|不)?(取消|取消了?)"
    r"|不(想|要|用)?(做)?(攻略|规划)了|别(规划|安排)了|先不(规划|去)了"
    r"|这次(旅行|旅游|行程)不(想|去)?了?"
)
# 局部排除句：「不去X了 / 不想去X了 / 不打算去X了」（X=具体地点，1~12 字）
# → PATCH avoid 场景，不得识别为取消整个规划（任务书 §16/T16）。
_CANCEL_EXCLUDE_RE = re.compile(r"不(想去|打算去|去).{1,12}?(了|啦)")


def is_cancel_run_query(message: str) -> bool:
    """消息是否为「取消整个规划」显式信号（纯函数，保守）。

    规则（任务书 §16）：
    - 真取消必须表达「取消整个规划 / 不规划了」这类整体意图；
    - 「不去海游馆了」类点名局部景点的排除句返回 False（PATCH avoid）。
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 40:
        return False
    if _CANCEL_EXCLUDE_RE.search(msg):
        return False
    return bool(_CANCEL_RUN_RE.search(msg))
