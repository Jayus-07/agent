# -*- coding: utf-8 -*-
"""token 计量对齐 + 限流重试的冒烟验证（不调真实 API）。"""
import sys

sys.path.insert(0, "D:/Program Files/workplace/agent")

from backend.evaluation.generation import (
    EvaluatorTokenCallback,
    add_evaluator_tokens,
    get_evaluator_token_usage,
    reset_token_usage,
)

reset_token_usage()

# 1) 计数器基本行为
add_evaluator_tokens(100, 20)
add_evaluator_tokens(0, 0)
u = get_evaluator_token_usage()
assert u == {"prompt_tokens": 100, "completion_tokens": 20, "llm_calls": 2}, u
print("1. counter ok:", u)

# 2) reset 也清 evaluator 侧
reset_token_usage()
assert get_evaluator_token_usage() == {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
print("2. reset ok")

# 3) EvaluatorTokenCallback：OpenAI 风格 llm_output
cb = EvaluatorTokenCallback().as_handler()


class FakeGen:
    def __init__(self, msg):
        self.message = msg


class FakeRespOpenAI:
    llm_output = {"token_usage": {"prompt_tokens": 11, "completion_tokens": 7}}
    generations = [[FakeGen(None)]]


cb.on_llm_end(FakeRespOpenAI())
u = get_evaluator_token_usage()
assert u["prompt_tokens"] == 11 and u["completion_tokens"] == 7, u
print("3a. openai-style callback ok:", u)

# 4) EvaluatorTokenCallback：ChatOllama 风格 usage_metadata（llm_output 为空）
class FakeMsg:
    usage_metadata = {"input_tokens": 30, "output_tokens": 5}


class FakeRespOllama:
    llm_output = {}
    generations = [[FakeGen(FakeMsg())]]


cb.on_llm_end(FakeRespOllama())
u = get_evaluator_token_usage()
assert u["prompt_tokens"] == 41 and u["completion_tokens"] == 12, u
print("4. ollama-style callback ok:", u)

# 5) 回调异常不外抛（response 缺字段）
class FakeRespBroken:
    llm_output = None


cb.on_llm_end(FakeRespBroken())
print("5. broken response no-crash ok")

# 6) judge._record_judge_tokens（AIMessage 风格 usage_metadata）
from backend.evaluation.judge import _record_judge_tokens


class FakeAIMessage:
    usage_metadata = {"input_tokens": 50, "output_tokens": 10}
    response_metadata = {}
    content = "{}"


reset_token_usage()
_record_judge_tokens(FakeAIMessage())
u = get_evaluator_token_usage()
assert u["prompt_tokens"] == 50 and u["completion_tokens"] == 10, u
print("6. judge token recording ok:", u)

# 7) response_metadata.token_usage 兜底
class FakeRespMeta:
    usage_metadata = None
    response_metadata = {"token_usage": {"prompt_tokens": 3, "completion_tokens": 2}}


_record_judge_tokens(FakeRespMeta())
u = get_evaluator_token_usage()
assert u["prompt_tokens"] == 53 and u["completion_tokens"] == 12, u
print("7. judge meta fallback ok:", u)

# 8) 限流识别
from backend.evaluation.ragas_bridge import _is_rate_limit_error

assert _is_rate_limit_error(Exception("Error code: 429 - Requests rate limit exceeded"))
assert _is_rate_limit_error(Exception("Too Many Requests"))
assert not _is_rate_limit_error(Exception("connection timeout"))
print("8. rate-limit detection ok")

print("\nALL PASS")
