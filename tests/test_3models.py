"""3模型对照测试 — 实时输出"""
import sys, time
sys.path.insert(0, ".")

def log(msg):
    print(msg, flush=True)

from llm.factory import get_llm_factory

S = "退款审核时间是多少？"
C = "对比分析退款政策和换货政策的区别？"
MODELS = ["qwen2.5:3b", "deepseek-v4-flash", "MiniMax-M3"]

log("=" * 60)
log("Pipeline 初始化...")
t0 = time.time()
from api.deps import get_rag_pipeline
p = get_rag_pipeline()
log(f"Init: {time.time()-t0:.1f}s")
log("=" * 60)

for model in MODELS:
    log(f"\n{'#'*50}")
    log(f"# {model}")
    log(f"{'#'*50}")

    r = get_llm_factory().set_current(model)
    if not r.get("ok"):
        log(f"  FAIL: {r.get('error')}")
        continue

    for label, q in [("SIMPLE", S), ("COMPLEX", C)]:
        log(f"\n  [{label}] {q}")
        t = time.time()
        try:
            result = p.ask(q)
            e = time.time() - t
            log(f"  TOTAL: {e:.1f}s | {len(result)}chars")
            log(f"  >>> {result[:120].replace(chr(10),' ')}")
        except Exception as ex:
            log(f"  FAIL: {time.time()-t:.1f}s | {str(ex)[:100]}")

log(f"\n{'='*60}")
log("Done")
