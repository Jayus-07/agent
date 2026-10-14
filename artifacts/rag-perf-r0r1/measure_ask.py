"""R0/R1-A 真实链路测量脚本（容器内运行，只读真实 KB，不写业务数据）。

用法（容器内）：
  python artifacts/rag-perf-r0r1/measure_ask.py "<问题>" [--kb default] [--repeat 1]

输出 JSON：answer_len / 阶段耗时 / 请求级 embedding 统计（出站次数、复用、唯一输入）。
目的：为 R1-A 提供"修改前后真实数据"，而不是只报缓存命中率。
"""
import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--kb", default="default")
    parser.add_argument("--session", default="rag-perf-r0r1-probe")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--trace-embed", action="store_true",
                        help="打印每次真实 embed_query 的调用栈（不含查询文本）")
    args = parser.parse_args()

    # 专项模型绑定（embedding/rerank）是进程内缓存，独立进程必须先从
    # 注册表刷新一次，否则 embedding 客户端解析不到 provider 凭据。
    import asyncio

    from backend.infra.llm import registry_store

    refreshed = asyncio.run(registry_store.refresh_registry())

    from backend.rag.pipeline import get_rag_pipeline
    from backend.rag.context import get_context

    # 诊断开关：记录每次真实 embed_query 的调用点（只打栈顶 3 帧，不含文本）。
    if args.trace_embed:
        import traceback

        from backend.rag.embedding_singleton import get_embedding

        embedding = get_embedding()
        inner_query = embedding._inner.embed_query

        def _traced(text, _orig=inner_query):
            stack = traceback.extract_stack(limit=30)[:-1]
            frames = [
                f"{f.filename.split('/')[-1]}:{f.lineno}:{f.name}"
                for f in stack
                if "/backend/" in f.filename
            ]
            print("[embed-trace]", " <- ".join(frames[-10:]), file=sys.stderr)
            return _orig(text)

        # OpenAIEmbeddings 是 pydantic 模型，普通赋值被字段校验拒绝；
        # 诊断探针用 object.__setattr__ 绕过（只在 --trace-embed 下生效）。
        object.__setattr__(embedding._inner, "embed_query", _traced)
        print("[embed-trace] instrumented", file=sys.stderr)

    # provider 出站调用计数（与版本无关，可在基线/优化两版上对照）：
    # 直接包住 inner 客户端，统计真正打到 provider 的向量化次数。
    from backend.rag.embedding_singleton import get_embedding

    _probe_embedding = get_embedding()
    _probe_inner = _probe_embedding._inner
    _probe_counter = {"query": 0, "documents": 0}
    _orig_q = _probe_inner.embed_query
    _orig_d = _probe_inner.embed_documents

    def _count_q(text, _o=_orig_q):
        _probe_counter["query"] += 1
        return _o(text)

    def _count_d(texts, _o=_orig_d):
        _probe_counter["documents"] += len(texts)
        return _o(texts)

    object.__setattr__(_probe_inner, "embed_query", _count_q)
    object.__setattr__(_probe_inner, "embed_documents", _count_d)

    pipeline = get_rag_pipeline()
    repeats = []
    for i in range(max(1, args.repeat)):
        t0 = time.monotonic()
        outcome = pipeline.ask_result(
            args.question,
            session_id=f"{args.session}-{i}" if i else args.session,
            kb_id=args.kb,
            subject_type="employee",
            department="",
            permissions=[],
            persist_memory=False,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        # 旧版（基线）没有 R0 埋点字段：getattr 兜底，同一脚本可跑两版。
        stats = getattr(get_context(), "embedding_stats", None)
        chain_stats = getattr(getattr(pipeline, "lc_chain", None),
                              "_last_embedding_stats", None)
        if stats is None and chain_stats is not None:
            stats = chain_stats
        # 同一进程内直接读 trace（远端 Trace 异步落地，进程内读取更可靠）：
        # 给出检索/生成/rerank 等阶段的真实耗时构成。
        stages = {}
        try:
            from backend.observability.tracer import trace_collector

            trace = trace_collector.current()
            if trace is not None and getattr(trace, "spans", None):
                stages["trace_id"] = trace.id
                stages["root_ms"] = trace.spans[0].duration_ms
                stages["spans"] = [
                    {"name": s.name, "type": s.type, "ms": s.duration_ms,
                     "status": s.status}
                    for s in trace.spans
                    if s.duration_ms is not None
                ][:40]
        except Exception as exc:  # noqa: BLE001 — 观测旁路
            stages["error"] = f"{type(exc).__name__}: {exc}"[:200]
        repeats.append({
            "elapsed_ms": round(elapsed_ms, 1),
            "answer_len": len(outcome.answer or ""),
            "answer_status": (outcome.answer_meta or {}).get("answer_status"),
            "confidence": (outcome.answer_meta or {}).get("confidence"),
            "embedding": stats.as_dict() if stats is not None else None,
            "stages": stages,
        })
    print(json.dumps({
        "question": args.question,
        "kb_id": args.kb,
        "binding_refreshed": bool(refreshed),
        "provider_embed_calls": dict(_probe_counter),
        "repeats": repeats,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
