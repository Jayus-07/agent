"""Per-step timing test"""
import sys, time
sys.path.insert(0, ".")

from llm.factory import get_llm_factory
get_llm_factory().set_current("MiniMax-M2.7-highspeed")

print("=== 性能分步计时 ===")
t0 = time.time()

from api.deps import get_rag_pipeline
p = get_rag_pipeline()
print(f"Pipeline Init: {time.time()-t0:.1f}s")

# 预热 (加载模型到内存)
t1 = time.time()
p.ask("测试")
print(f"预热查询: {time.time()-t1:.1f}s")

# 正式测试
for i, q in enumerate([
    "退货流程是什么？",
    "商品上架需要什么条件？",
    "收到差评后怎么处理？",
], 1):
    t = time.time()
    r = p.ask(q)
    e = time.time() - t
    print(f"查询{i}: {e:.1f}s | {len(r)}字符")
