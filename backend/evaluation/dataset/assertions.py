"""确定性答案断言；不执行上传的代码或表达式。"""

from __future__ import annotations

import re


def is_refusal(answer: str) -> bool:
    """拒答规则探针；属于可校准的启发式，不冒充人工语义金标。"""
    return bool(
        re.search(
            r"无法回答|无法确定|不能确定|没有足够|未提供|未找到|不知道|无相关依据|insufficient (?:information|evidence)|cannot (?:answer|determine)|not (?:provided|enough evidence)",
            answer,
            re.IGNORECASE,
        )
    )


def check_assertions(answer: str, assertions: list[dict]) -> list[bool]:
    checks = []
    for assertion in assertions:
        value = assertion["value"]
        kind = assertion["type"]
        if kind == "contains":
            checks.append(value in answer)
        elif kind == "not_contains":
            checks.append(value not in answer)
        elif kind == "equals":
            checks.append(value.strip() == answer.strip())
        else:
            raise ValueError("不支持的断言类型")
    return checks
