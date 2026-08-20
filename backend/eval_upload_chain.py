"""上传入库链路测评 — 真实 HTTP 上传 + SSE 进度采集 + 入库验证。

测评内容（用户需求复述）:
  1. 真实上传：通过 FastAPI 的 POST /rag/upload 上传文件（非 mock）
  2. 记录后端处理细节：订阅 SSE 进度流，采集 load→parse→chunk→embed→write
     各阶段事件、阶段耗时、trace_id
  3. 验证入库：通过 GET /rag/documents/{doc_id} 与 /chunks 独立确认
     文档已真实进入向量库（chunk 可查），而非只信接口返回值
  4. 自动化可重复：脚本化执行，输出测评报告（stdout + JSON 文件）

运行前提:
  - FastAPI 服务已启动（uvicorn app.server:app，默认 127.0.0.1:8000）
  - /rag/health 返回 ready（Chroma/Ollama/PostgreSQL 可用）

用法:
  python eval_upload_chain.py [sample_file] [--kb policy_general] [--dept general]
  不传 sample_file 时自动生成临时 .md 样本。
"""
import argparse
import json
import os
import sys
import tempfile
import time
import uuid

import requests

BASE = os.getenv("RAG_API_BASE", "http://127.0.0.1:8000")
DEFAULT_KB = "policy_general"
DEFAULT_DEPT = "general"

# 生成的临时样本内容（带数字/日期/结构，便于后续深度验证）
_SAMPLE_MD = """# 跨境退货与售后 FAQ（上传链路测评样本）

## 退货窗口
跨境订单支持 30 天无理由退货。退货窗口从买家签收之日起计算，签收后第 31 天起不再受理退货申请。

## 退货流程
1. 买家在订单详情页提交退货申请，选择退货原因（商品破损 / 尺码不符 / 不想要 / 其他）。
2. 卖家需在 48 小时内审核退货申请，审核通过后系统自动生成退货单号。
3. 买家将商品寄回指定海外仓，物流签收后 3 个工作日内完成退款。
4. 退款原路返回，预计 3-5 个工作日到账。

## 差评处理
差评处理要求卖家在收到差评后 48 小时内给出具体解决方案。
若涉及产品质量问题，需要同步提交质检报告到客服邮箱 service@example.com。

## 物流时效
- 美国仓发货：标准 5-8 个工作日，加急 2-3 个工作日
- 欧洲仓发货：标准 7-10 个工作日
- 德国仓发货：标准 6-9 个工作日

## 发票与凭证
增值税普通发票在订单完成后 7 个工作日内开具，电子发票发送至买家注册邮箱。
"""


def _sse_events(upload_id: str, timeout: int = 600) -> list[dict]:
    """订阅 SSE 进度流，收集全部阶段事件（附事件到达相对时间）。"""
    events = []
    t0 = time.time()
    url = f"{BASE}/rag/upload/{upload_id}/stream"
    with requests.get(url, stream=True, timeout=timeout) as resp:
        if resp.status_code != 200:
            return [{"stage": "error", "message": f"SSE HTTP {resp.status_code}"}]
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8")
            if line.startswith("data: "):
                evt = json.loads(line[len("data: "):])
                evt["_arrive_ms"] = int((time.time() - t0) * 1000)
                events.append(evt)
                if evt.get("stage") in ("done", "error", "duplicate"):
                    break
    return events


def _verify_in_kb(doc: dict, kb_id: str, dept: str) -> dict:
    """独立验证：文档是否真实入库（detail + chunks 可查）。"""
    doc_id = doc.get("doc_id") or doc.get("id")
    result = {"doc_id": doc_id, "detail_ok": False, "chunks_ok": False,
              "chunk_count": 0, "error": ""}
    if not doc_id:
        result["error"] = "done 事件未返回 doc_id"
        return result
    try:
        detail = requests.get(f"{BASE}/rag/documents/{doc_id}", timeout=30).json()
        inner = detail.get("doc") or detail  # 详情接口返回 {ok, doc: {...}}
        result["detail_ok"] = bool(inner.get("doc_id") or inner.get("id"))
        result["detail"] = {k: inner.get(k) for k in
                            ("doc_id", "id", "file_name", "name", "kb_id",
                             "doc_type", "chunk_count", "confidence",
                             "llm_used", "status", "embedding_model")
                            if k in inner}
    except Exception as e:  # 验证环节失败仅记录，不阻断报告
        result["error"] = f"detail 查询失败: {e}"
        return result
    try:
        chunks = requests.get(f"{BASE}/rag/documents/{doc_id}/chunks",
                              timeout=30).json()
        items = chunks.get("chunks") or chunks.get("items") or []
        result["chunk_count"] = len(items)
        result["chunks_ok"] = len(items) > 0
        result["first_chunk_preview"] = (
            (items[0].get("content") or items[0].get("page_content") or "")[:120]
            if items else ""
        )
    except Exception as e:
        result["error"] += f" | chunks 查询失败: {e}"
    return result


def main():
    parser = argparse.ArgumentParser(description="上传入库链路测评")
    parser.add_argument("sample", nargs="?", default=None,
                        help="样本文件路径；缺省时自动生成临时 .md")
    parser.add_argument("--kb", default=DEFAULT_KB)
    parser.add_argument("--dept", default=DEFAULT_DEPT)
    args = parser.parse_args()

    sample = args.sample
    cleanup_sample = False
    if not sample:
        fd, sample = tempfile.mkstemp(
            suffix=f"_测评上传入库_{uuid.uuid4().hex[:6]}.md")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_SAMPLE_MD)
        cleanup_sample = True

    name = os.path.basename(sample)
    ext = name.rsplit(".", 1)[-1].lower()
    mime_map = {"md": "text/markdown", "txt": "text/plain",
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    mime = mime_map.get(ext, "application/octet-stream")

    print(f"== 上传入库链路测评 ==")
    print(f"样本: {sample} ({os.path.getsize(sample)} bytes, MIME={mime})")
    print(f"目标: kb={args.kb}, dept={args.dept}, API={BASE}\n")

    # 0. 健康检查（服务偶发卡顿，重试 3 次）
    health_ok = False
    for attempt in range(3):
        try:
            health = requests.get(f"{BASE}/rag/health", timeout=30).json()
            if health.get("status") == "ready":
                health_ok = True
                break
        except Exception as e:
            if attempt == 2:
                print(f"[FAIL] 服务不可达: {e}")
                sys.exit(1)
            print(f"[RETRY] health 超时（{attempt + 1}/3），5s 后重试...")
            time.sleep(5)
    if not health_ok:
        print("[FAIL] 服务未就绪")
        sys.exit(1)
    print("[OK] /rag/health ready\n")

    # 1. 真实上传（multipart）
    t_http0 = time.time()
    with open(sample, "rb") as f:
        resp = requests.post(
            f"{BASE}/rag/upload",
            files={"file": (name, f, mime)},
            data={"kb_id": args.kb, "department": args.dept},
            timeout=120,
        )
    http_elapsed_ms = int((time.time() - t_http0) * 1000)
    body = resp.json()
    if not body.get("ok"):
        print(f"[FAIL] 上传失败: {body.get('error')}")
        sys.exit(1)
    upload_id = body["upload_id"]
    print(f"[OK] 上传接口返回: upload_id={upload_id} "
          f"(HTTP 耗时 {http_elapsed_ms}ms)\n")

    # 2. 订阅 SSE，采集后端处理细节
    print("-- 后端处理进度（SSE 实时事件）--")
    events = _sse_events(upload_id)
    for evt in events:
        stage = evt.get("stage", "?")
        msg = evt.get("message", "")
        prog = evt.get("progress")
        line = f"  [{stage}]"
        if prog is not None:
            line += f" {prog}%"
        if msg:
            line += f" {msg}"
        line += f" (+{evt.get('_arrive_ms', 0)}ms)"
        print(line)

    terminal = events[-1] if events else {}
    if terminal.get("stage") in ("error",):
        print(f"\n[FAIL] 索引失败: {terminal.get('message')}")
        sys.exit(1)

    # 3. 汇总后端处理的阶段耗时
    stage_elapsed = terminal.get("stage_elapsed") or {}
    total_ms = terminal.get("total_ms") or 0
    trace_id = terminal.get("trace_id") or ""
    doc = terminal.get("doc") or {}
    print("\n-- 阶段耗时（后端 trace 实测）--")
    for key, ms in stage_elapsed.items():
        print(f"  {key:<12} {ms}ms")
    print(f"  {'total':<12} {total_ms}ms")
    print(f"  trace_id: {trace_id or '(duplicate 无 trace)'}")
    if doc:
        print(f"  doc_id: {doc.get('doc_id')}  chunk_count: {doc.get('chunk_count')}  "
              f"doc_type: {doc.get('doc_type')}  confidence: {doc.get('confidence')}  "
              f"llm_used: {bool(doc.get('llm_used'))}")

    # 4. 独立验证入库
    print("\n-- 入库验证（独立查询）--")
    verify = _verify_in_kb(doc, args.kb, args.dept)
    print(f"  detail 可查: {verify['detail_ok']}")
    print(f"  chunks 可查: {verify['chunks_ok']} ({verify['chunk_count']} chunks)")
    if verify.get("first_chunk_preview"):
        print(f"  首个 chunk 预览: {verify['first_chunk_preview'][:80]}...")

    # 5. 测评报告（JSON）
    report = {
        "sample": {"path": sample, "size_bytes": os.path.getsize(sample), "mime": mime},
        "target": {"kb_id": args.kb, "department": args.dept, "api": BASE},
        "upload": {"ok": True, "upload_id": upload_id, "http_elapsed_ms": http_elapsed_ms},
        "stages": events,
        "stage_elapsed": stage_elapsed,
        "total_ms": total_ms,
        "trace_id": trace_id,
        "doc": doc,
        "verify": verify,
        "passed": bool(verify["detail_ok"] and verify["chunks_ok"]),
    }
    os.makedirs("logs", exist_ok=True)
    report_path = os.path.join("logs", f"upload_chain_report_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n== 测评结论: {'PASS' if report['passed'] else 'FAIL'} ==")
    print(f"报告: {os.path.abspath(report_path)}")

    if cleanup_sample:
        try:
            os.remove(sample)
        except OSError:
            pass
    sys.exit(0 if report["passed"] else 2)


if __name__ == "__main__":
    main()
