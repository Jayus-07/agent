"""P1 端到端测试 — LLM 自报拒答 + Self-Correction

真起 RAGPipeline，根据问题关键词切换 StubChatModel 状态：
  Case 5 — 含 _LLM_REJECT_ 的问题 → stub 自报 can_answer=false → generation 层拒答
  Case 6 — 含 _SELF_CORRECT_ 的问题 → stub 第一次 false、第二次 true → Self-Correction 救活

依据文档: docs/architecture/rag-evidence-gate.md §0.4 + §3.3
"""
from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# === LLM 替身（先于 import）===
sys.modules["langchain_openai"] = MagicMock(ChatOpenAI=MagicMock())

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult, ChatGeneration


# ============================================
# 可切换状态的 StubChatModel
# ============================================
class StatefulStubChatModel(BaseChatModel):
    """通过 call_count 切换响应，用于 e2e 验证 self-correction 路径。"""
    model_name: str = "stateful-stub-llm"
    mode: str = "pass"        # 当前模式：pass | reject | self_correct_ok | self_correct_fail
    call_count: int = 0
    last_user_text: str = ""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.messages import HumanMessage, SystemMessage
        # 取最后一条 human message 的内容作状态切换依据
        user_texts = [m.content for m in messages
                      if isinstance(m.content, str) and m.content]
        user_text = user_texts[-1] if user_texts else ""
        self.last_user_text = user_text[:80]
        self.call_count += 1

        # 关键词触发切模式
        if "_SELF_CORRECT_OK_" in user_text and self.mode == "pass":
            self.mode = "self_correct_ok"  # 第一次拒绝
        elif "_SELF_CORRECT_FAIL_" in user_text and self.mode == "pass":
            self.mode = "self_correct_fail"  # 第一次拒绝 + 重写后仍拒绝
        elif "_LLM_REJECT_" in user_text:
            self.mode = "reject"  # 直接拒答

        # 根据当前模式返回
        if self.mode == "reject":
            content = "资料未提及（stub 模拟）。<!--META{\"can_answer\":false,\"reason\":\"no_evidence\",\"confidence\":0.05}-->"
        elif self.mode == "self_correct_ok" and self.call_count == 1:
            # 第一次：拒答
            content = "资料未提及。<!--META{\"can_answer\":false,\"reason\":\"low_relevance\",\"confidence\":0.1}-->"
        elif self.mode == "self_correct_ok" and self.call_count >= 2:
            # 改写 query 后：成功
            content = "改写后命中资料 [1]。<!--META{\"can_answer\":true,\"citations\":[1],\"confidence\":0.85}-->"
        elif self.mode == "self_correct_fail" and self.call_count == 1:
            content = "资料未提及。<!--META{\"can_answer\":false,\"reason\":\"insufficient\",\"confidence\":0.05}-->"
        elif self.mode == "self_correct_fail" and self.call_count >= 2:
            content = "改写后仍未找到。<!--META{\"can_answer\":false,\"reason\":\"no_evidence\",\"confidence\":0.0}-->"
        else:  # pass
            content = "stub 通过路径 [1]。<!--META{\"can_answer\":true,\"citations\":[1],\"confidence\":0.95}-->"

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    @property
    def _llm_type(self):
        return "stateful-stub"


# 替换 build_deepseek
import backend.infra.llm.providers.deepseek as deepseek_mod
import backend.infra.llm.proxy as proxy_mod
deepseek_mod.build_deepseek = lambda model: StatefulStubChatModel()
proxy_mod.build_deepseek = lambda model: StatefulStubChatModel()


def main():
    print("=" * 60)
    print("E2E P1: LLM 自报拒答 + Self-Correction")
    print("=" * 60)

    # ── 临时 trace_store 隔离 ──
    from backend.rag import trace_store as ts_mod
    tmp_db = Path(tempfile.gettempdir()) / f"e2e_p1_{int(time.time()*1000)}.db"
    tmp_store = ts_mod.TraceStore(db_path=str(tmp_db))
    ts_original = ts_mod._trace_store
    ts_mod._trace_store = tmp_store

    try:
        # ── 起 RAGPipeline ──
        print("\n[起 RAGPipeline] 首次 10-15s")
        from backend.rag.pipeline import RAGPipeline
        t0 = time.time()
        rag = RAGPipeline()
        print(f"  RAGPipeline 就绪（{time.time()-t0:.1f}s）")
        # RAGChain 通过 backend.infra.llm.llm proxy 调用真实 LLM，
        # 但 proxy._resolve_active_llm() 走我们替换的 build_deepseek，
        # 因此实际上每次调用都会新建一个 StatefulStubChatModel。
        # 所以这里需要替换 cache 强制复用同一个 instance 让 call_count 跨调用递增。
        # 用 module attr 全局缓存 stub
        import backend.infra.llm.proxy as proxy_mod
        # 重置 _default_llm 强制重新调 build_deepseek
        if hasattr(proxy_mod, "_default_llm"):
            proxy_mod._default_llm = StatefulStubChatModel()
        stub_global = proxy_mod._default_llm
        # 同时覆盖 deepseek 模块的 build_deepseek，让后续调用还返同一个
        deepseek_mod.build_deepseek = lambda model: stub_global
        proxy_mod.build_deepseek = lambda model: stub_global

        # 取最新 trace
        def latest_trace():
            rows = ts_mod._trace_store.list(limit=10)
            return rows[0] if rows else None

        # ── Case 5: LLM 自报拒答 ──
        print("\n" + "─" * 60)
        print("[Case 5] LLM 自报拒答 → generation 层拒答 + trace.metadata.rejection 写入")
        print("─" * 60)
        stub_global.mode = "pass"
        stub_global.call_count = 0
        question5 = "数据治理包括哪些 _LLM_REJECT_ 内容？"
        answer5 = rag.ask(question5, session_id="e2e-p1-case5")
        latest5 = latest_trace()
        rej5 = (latest5 or {}).get("metadata", {}).get("rejection") or {}
        print(f"  问题: {question5}")
        print(f"  调用 LLM 次数: {stub_global.call_count}")
        print(f"  answer: {answer5[:100]}")
        print(f"  rejection: {rej5}")
        case5_pass = (
            rej5.get("rejected") is True
            and rej5.get("layer") == "generation"
            and rej5.get("reason") == "no_evidence"
        )
        print(f"  {'✓' if case5_pass else '✗'} Case 5 {'通过' if case5_pass else '失败'}")

        # ── Case 6: Self-Correction 救活 ──
        print("\n" + "─" * 60)
        print("[Case 6] Self-Correction — 第 1 次拒答 → 重写 query → 第 2 次通过")
        print("─" * 60)
        stub_global.mode = "pass"
        stub_global.call_count = 0
        question6 = "_SELF_CORRECT_OK_ 数据治理的目标是什么？"
        answer6 = rag.ask(question6, session_id="e2e-p1-case6")
        latest6 = latest_trace()
        rej6 = (latest6 or {}).get("metadata", {}).get("rejection") or {}
        print(f"  问题: {question6}")
        print(f"  调用 LLM 总次数: {stub_global.call_count}")
        print(f"  answer: {answer6[:100]}")
        # self-correction 成功 → 没有 rejection 字段（因为 _finalize_llm_rejection 不被调）
        case6_pass = (
            "改写后命中" in answer6
            and stub_global.call_count >= 2
        )
        print(f"  {'✓' if case6_pass else '✗'} Case 6 {'通过' if case6_pass else '失败'}")

        # ── 总结 ──
        print("\n" + "=" * 60)
        print(f"  Case 5 (LLM 自报拒答): {'✓' if case5_pass else '✗'}")
        print(f"  Case 6 (Self-Correction): {'✓' if case6_pass else '✗'}")

        if case5_pass and case6_pass:
            print("\n✓ P1 端到端通过")
            return 0
        else:
            print("\n⚠ 部分 Case 失败")
            return 1
    finally:
        ts_mod._trace_store = ts_original
        try:
            tmp_db.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
