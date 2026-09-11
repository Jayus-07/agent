# 临时测试脚本：对比 qwen3.7-plus 开/关思考模式的元数据提取耗时
import sys, time
sys.path.insert(0, ".")

from backend.infra.llm import llm
from backend.infra.llm.proxy import _last_call_meta_var

PROMPT = """从以下文档内容中提取 10 个关键词，以 JSON 数组返回，不要其他内容。

文档内容：
示例跨境电商公司 跨境电商技术手册 版本：v1.0 生效日期：2026-08-26 文档编号：TECH-001。
本手册涵盖跨境电商系统的架构、运维与安全规范。整体可用性目标 ≥ 99.95%，P99 接口延迟 ≤ 500 ms。
系统支撑面向全球买家的商品展示、交易、履约与售后全链路。包含数据安全规范、访问控制、日志审计等章节。"""

def run(label, **invoke_kwargs):
    t0 = time.time()
    try:
        result = llm.invoke(PROMPT, **invoke_kwargs)
        dt = time.time() - t0
        content = result.content if hasattr(result, "content") else str(result)
        meta = _last_call_meta_var.get()
        print(f"[{label}] OK {dt:.1f}s | tokens={meta.get('total_tokens', '?')} "
              f"(p={meta.get('prompt_tokens', '?')}, c={meta.get('completion_tokens', '?')}) "
              f"| 输出预览: {content[:80]!r}")
    except Exception as e:
        dt = time.time() - t0
        print(f"[{label}] FAIL {dt:.1f}s | {type(e).__name__}: {str(e)[:200]}")

print("模型:", llm)
run("默认(思考开)")
run("思考关", extra_body={"enable_thinking": False})
run("思考关+thinking_budget0", extra_body={"enable_thinking": False, "thinking_budget": 0})
