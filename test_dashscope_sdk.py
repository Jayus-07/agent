"""Quick Test for DashScope TextReRank API (Official SDK)"""
import sys
sys.path.insert(0, 'backend')

import dashscope
from http import HTTPStatus

# 配置 SDK
dashscope.api_key = "sk-ws-H.EYXPRPR.IUcr.MEUCIAly7Ro_fvao0woVZ7YhsBg7c9s_upEVpDkw2DbWMkWLAiEA3edsXUP13M0w5EX2P9FrDKVbkX8BzUdeoN1aAnkyD1o"
dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'

print("=" * 60)
print("Testing DashScope TextReRank SDK")
print("=" * 60)

try:
    resp = dashscope.TextReRank.call(
        model="qwen3-rerank",
        query="什么是重排序模型",
        documents=[
            "重排序模型广泛应用于搜索引擎和推荐系统，用于按相关性对候选文本排序",
            "量子计算是计算科学的前沿领域",
            "预训练语言模型的发展为重排序模型带来了新的突破"
        ],
        top_n=2,
        return_documents=True
    )

    print(f"\nResponse status: {resp.status_code}")
    
    if resp.status_code == HTTPStatus.OK:
        print("[SUCCESS]!")
        print(f"\nResult output:\n{resp.output}")
        
        # 解析结果
        if hasattr(resp, 'output') and hasattr(resp.output, 'results'):
            print(f"\nResults ({len(resp.output.results)} items):")
            for i, r in enumerate(resp.output.results, 1):
                print(f"{i}. Index={r.index}, Score={r.relevance_score:.4f}")
                if r.document:
                    doc_text = r.document.text[:80] + "..." if len(r.document.text) > 80 else r.document.text
                    print(f"   Document: {doc_text}")
    else:
        print(f"[ERROR] [status={resp.status_code}]")
        print(f"Message: {resp.message}")

except Exception as e:
    print(f"[EXCEPTION]: {e}")

print("=" * 60)
