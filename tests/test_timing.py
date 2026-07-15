"""DeepSeek V4-Flash 真实API测试"""
import sys, time, os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
sys.path.insert(0, ".")
os.environ["LLM_REQUEST_TIMEOUT"] = "60"

from api.deps import get_rag_pipeline

QUESTIONS = ["退货流程是什么？", "商品上架需要什么条件？", "收到差评后应该怎么处理？"]
DEADLINE = 60

print("=" * 60)
print("Pipeline 初始化...")
t0 = time.time()
p = get_rag_pipeline()
print(f"初始化: {time.time()-t0:.1f}s")

def ask(q):
    return p.ask(q)

for i, q in enumerate(QUESTIONS, 1):
    t = time.time()
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(ask, q)
            r = fut.result(timeout=DEADLINE)
        e = time.time() - t
        ok = "[OK]" if len(r) > 50 else "[SHORT]"
        print(f"  {ok} Q{i}: {e:.1f}s | {len(r)}字符 | {r[:80].replace(chr(10),' ')}")
    except FutureTimeout:
        print(f"  [DEAD] Q{i}: {DEADLINE}s超时")
    except Exception as ex:
        print(f"  [ERR] Q{i}: {time.time()-t:.1f}s | {str(ex)[:80]}")

print("Done")
