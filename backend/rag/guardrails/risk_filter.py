"""
risk_filter.py — 高风险 Claim 筛选

只将包含数字/金额/政策/规则/流程的 claim 送入 NLI 验证，
跳过纯描述性语句，减少 NLI 推理次数。
"""

import re
from typing import List, Tuple

# 高风险标记词
_HIGH_RISK_PATTERNS = [
    # 数字 + 单位
    re.compile(r'\d+\s*(?:元|美元|美金|块|毛|分)'),
    re.compile(r'\d+\s*(?:天|日|小时|分钟|工作日|周|月|年)'),
    re.compile(r'\d+\s*(?:次|件|个|张|笔|KG|kg|克|g|mm|cm|m|km)'),
    re.compile(r'\d+\s*%|百分之\d+'),
    # 金额/费用
    re.compile(r'(?:费用|金额|价格|成本|预算|报销|退款|扣费|罚款|赔偿)'),
    # 规则/限制
    re.compile(r'(?:必须|禁止|不得|应当|需要|要求|规定|允许|可以|不能|只能|最多|最少|至少|不超过|不低于)'),
    # 政策/流程
    re.compile(r'(?:政策|流程|步骤|审批|审核|提交|注册|申请|备案|登记)'),
    # 权限/资格
    re.compile(r'(?:权限|资格|条件|门槛|前置|资质|认证|授权)'),
    # 期限
    re.compile(r'(?:期限|截止|到期|过期|时效|有效期|在.*内|之前|之后)'),
]


def filter_claims(claims: List[str]) -> Tuple[List[str], List[str]]:
    """将 claims 分为高风险和可跳过两组。

    Args:
        claims: claim_extractor 的输出

    Returns:
        (high_risk_claims, skip_claims)
    """
    high_risk = []
    skip = []

    for claim in claims:
        if any(p.search(claim) for p in _HIGH_RISK_PATTERNS):
            high_risk.append(claim)
        else:
            skip.append(claim)

    return high_risk, skip
