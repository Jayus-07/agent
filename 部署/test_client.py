"""本地连通性测试：验证服务器 vLLM 接口可用
用法: python test_client.py
前提: 已建 SSH 隧道  ssh -L 8000:localhost:8000 user@服务器IP
"""
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key=os.getenv("VLLM_API_KEY", "把deploy.sh输出的Key填这里"),
)

resp = client.chat.completions.create(
    model="Qwen/Qwen3-8B",          # 和服务器 --model 一致
    messages=[
        {"role": "user", "content": "用一句话介绍你自己"},
    ],
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # 关闭思考模式，RAG 测试更快
    max_tokens=100,
)
print(resp.choices[0].message.content)
