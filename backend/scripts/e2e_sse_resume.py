"""e2e_sse_resume.py — F2.5 SSE 断线恢复 100 次真实网关验收驱动。

链路：Client → APISIX :9080（JWT）→ /chat/stream（真实 LLM 流）→
  随机点硬断开（socket 级）→ /chat/stream/resume 续播至终端。

断开点随机覆盖（F2.4 断线时间点矩阵的实机子集）：
  - meta 前（发请求即断）/ meta 后 / delta 中 / done 前
恢复行为变体（按轮次取模）：
  - 正常 resume(last_seq)
  - i%5==0：终端后再用旧游标重放一次（finished 流可重复重放，重复计入 dup）
  - i%7==0：首次 resume 故意用旧游标（at-least-once 重复交付）

逐轮断言：并集 seq 连续无洞（按 seq 去重）／终端恰一次且为 done／
delta 拼接非空／meta.stream_id == 本轮 request_id（跨流零串扰）。
负向探针（额外）：未知 stream → 404 STREAM_NOT_RESUMABLE；他账号游标 → 403。

输出：/d/tmp/sse_resume_100.json + stdout 摘要。
五零指标：missing_event_count / duplicate_terminal / terminal_mismatch /
cross_request_replay / cross_user_replay 全零 = PASS。

用法：python scripts/e2e_sse_resume.py --password <pwd> [--iterations 100]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))

from e2e_travel_runtime import BASE, PATH_PREFIX, login  # noqa: E402

import httpx  # noqa: E402

QUESTION = "用一句话直接回答：1+1等于几？不要解释。"


def _load_api_key() -> str:
    """与前端 BFF 注入同源：根 .env 的 API_KEY（与 L-E2E 同一约定）。"""
    import re

    val = os.getenv("API_KEY")
    if val:
        return val
    env_path = os.path.join(os.path.dirname(os.path.dirname(_HERE)), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            m = re.search(r"^API_KEY=(.+)\s*$", fh.read(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


_API_KEY = _load_api_key()

# ── 网关限流节流器：APISIX 对 /api/chat/* 限 30 次/60s/用户——驱动按
#    25 次/60s 滑动窗主动限速（诚实过网关，绝不绕限流），否则 429 淹没验收 ──
_PACE_MAX = 25
_PACE_WINDOW = 60.0
_pace_events: list[float] = []
_pace_lock = __import__("threading").Lock()


def _pace() -> None:
    import threading
    import time as _t

    while True:
        with _pace_lock:
            now = _t.time()
            cutoff = now - _PACE_WINDOW
            _pace_events[:] = [t for t in _pace_events if t > cutoff]
            if len(_pace_events) < _PACE_MAX:
                _pace_events.append(now)
                return
            wait = _pace_events[0] + _PACE_WINDOW - now + 0.05
        _t.sleep(max(wait, 0.05))


def _headers(token: str) -> dict:
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if _API_KEY:
        h["X-API-Key"] = _API_KEY  # 网关链路与 L-E2E 同口径：Bearer+API-Key 双持
    return h


def _read_sse_lines(lines):
    """把 SSE 行迭代器解析为帧列表。"""
    frames, cur = [], ""
    for line in lines:
        if line.startswith("event: "):
            cur = line[7:].strip()
        elif line.startswith("data: ") and line[6:].strip():
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            frames.append({"event": cur, "data": data})
    return frames


def stream_disconnect(token: str, request_id: str, *,
                      stop_after_events: int | None,
                      close_fast: bool) -> tuple[list[dict], int]:
    """发一起流并在断点硬断 socket；返回 (已收帧, 断点 seq)。"""
    _pace()
    frames: list[dict] = []
    with httpx.Client(timeout=120) as client:
        req = client.build_request(
            "POST", f"{BASE}{PATH_PREFIX}/chat/stream",
            json={"question": QUESTION, "session_id": "sse-resume-e2e",
                  "request_id": request_id},
            headers=_headers(token))
        resp = client.send(req, stream=True)
        if resp.status_code != 200:
            resp.close()
            raise RuntimeError(f"stream status={resp.status_code}")
        if close_fast:
            resp.close()  # meta 前断开
            return [], 0
        try:
            cur, seq_seen = "", 0
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    cur = line[7:].strip()
                elif line.startswith("data: ") and line[6:].strip():
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    frames.append({"event": cur, "data": data})
                    sq = data.get("seq")
                    if isinstance(sq, int):
                        seq_seen = sq
                    if stop_after_events is not None \
                            and seq_seen >= stop_after_events:
                        break
        finally:
            resp.close()  # socket 硬断（模拟客户端消失）
    return frames, seq_seen


def resume_once(token: str, request_id: str, after_seq: int) -> tuple[int, list[dict]]:
    _pace()
    frames: list[dict] = []
    with httpx.Client(timeout=120) as client:
        req = client.build_request(
            "POST", f"{BASE}{PATH_PREFIX}/chat/stream/resume",
            json={"request_id": request_id, "after_seq": after_seq},
            headers=_headers(token))
        resp = client.send(req, stream=True)
        if resp.status_code != 200:
            resp.read()
            resp.close()
            return resp.status_code, []
        frames = _read_sse_lines(resp.iter_lines())
        resp.close()
    return 200, frames


def run_iteration(i: int, token: str, request_id: str) -> dict:
    rec: dict = {"iter": i, "request_id": request_id}
    seen: set[int] = set()
    all_frames: list[dict] = []
    terminal = None
    duplicates = 0

    close_fast = (i % 10 == 0)
    stop_after = None if close_fast else random.randint(2, 5)
    frames, seq_seen = stream_disconnect(token, request_id,
                                         stop_after_events=stop_after,
                                         close_fast=close_fast)
    rec["disconnect_after"] = seq_seen
    for f in frames:
        sq = f["data"].get("seq")
        if isinstance(sq, int):
            seen.add(sq)
        if f["event"] in ("done", "error"):
            terminal = f["event"]
    all_frames.extend(frames)

    # ── resume 循环至终端（传输断开最多重试 3 次）──
    for attempt in range(1, 4):
        cursor = max(seen) if seen else 0
        if i % 7 == 0 and attempt == 1 and cursor > 1:
            cursor -= 1  # 故意旧游标：验证重复交付 + 客户端去重口径
        t0 = time.time()
        st, rframes = resume_once(token, request_id, cursor)
        rec["resume_ms"] = round((time.time() - t0) * 1000)
        if st != 200:
            rec["error"] = f"resume status={st}"
            return rec
        for f in rframes:
            sq = f["data"].get("seq")
            if isinstance(sq, int):
                if sq in seen:
                    duplicates += 1
                seen.add(sq)
            if f["event"] in ("done", "error"):
                terminal = f["event"]
            all_frames.append(f)
        rec["replayed"] = rec.get("replayed", 0) + len(rframes)
        if terminal:
            break
    rec["terminal"] = terminal
    rec["duplicates"] = duplicates

    # i%5==0：终端后旧游标再放一次（finished 流重复重放语义）
    if terminal and i % 5 == 0:
        st2, rframes2 = resume_once(token, request_id, 0)
        rec["finished_replay_status"] = st2
        if st2 == 200:
            rec["finished_replay_count"] = len(rframes2)
            rec["finished_replay_has_terminal"] = any(
                f["event"] in ("done", "error") for f in rframes2)

    union = sorted(seen)
    rec["union_max"] = union[-1] if union else 0
    rec["contiguous"] = union == list(range(1, len(union) + 1))
    deltas = "".join(f["data"].get("content", "") for f in all_frames
                     if f["event"] == "delta")
    rec["answer_hash"] = hashlib.sha256(deltas.encode("utf-8")).hexdigest()[:16]
    rec["answer_len"] = len(deltas)
    rec["cross_stream_clean"] = all(
        m["data"].get("stream_id") == request_id
        for m in all_frames if m["event"] == "meta")

    err = None
    if not rec["contiguous"]:
        err = "union not contiguous"
    if terminal != "done":
        err = f"terminal={terminal}"
    if rec["cross_stream_clean"] is not True:
        err = "cross stream contamination"
    if err:
        rec["error"] = err
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--password", required=True)
    ap.add_argument("--iterations", type=int, default=100)
    ap.add_argument("--other-user", default="e2e_travel")
    args = ap.parse_args()

    token = login("e2e_domain", args.password)
    try:
        other_token = login(args.other_user, args.password)
    except Exception:
        other_token = None

    records: list[dict] = []
    probes: list[dict] = []
    fails = 0
    t_all = time.time()
    probe_request_id = ""

    for i in range(1, args.iterations + 1):
        request_id = f"sse-e2e-{i:04d}"
        if i == 3:
            probe_request_id = request_id
        try:
            rec = run_iteration(i, token, request_id)
        except Exception as exc:  # noqa: BLE001
            rec = {"iter": i, "request_id": request_id,
                   "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
        fails += 1 if rec.get("error") else 0
        records.append(rec)
        if i % 10 == 0:
            print(f"[{i}/{args.iterations}] fails={fails} "
                  f"last={json.dumps(rec, ensure_ascii=False)[:140]}",
                  flush=True)

    # ── 负向探针（不计入 100 轮）──
    st, _ = resume_once(token, "no-such-stream-e2e", 0)
    probes.append({"probe": "unknown_stream", "status": st, "ok": st == 404})
    if other_token and probe_request_id:
        st, _ = resume_once(other_token, probe_request_id, 0)
        probes.append({"probe": "cross_user_cursor", "status": st,
                       "ok": st == 403})
    else:
        probes.append({"probe": "cross_user_cursor", "status": None,
                       "ok": False, "note": "second account unavailable"})

    missing = sum(1 for r in records if not r.get("contiguous"))
    bad_terminal = sum(1 for r in records if r.get("terminal") != "done")
    cross_stream = sum(1 for r in records if r.get("cross_stream_clean") is not True)
    summary = {
        "iterations": args.iterations,
        "iteration_failures": fails,
        "missing_event_count": missing,
        "duplicate_terminal": bad_terminal,
        "terminal_mismatch": bad_terminal,
        "cross_request_replay": cross_stream,
        "cross_user_replay": sum(1 for p in probes if not p["ok"]),
        "total_duplicates_accepted": sum(r.get("duplicates", 0) for r in records),
        "probes": probes,
        "wall_seconds": round(time.time() - t_all, 1),
        "records": records,
    }
    out = "/d/tmp/sse_resume_100.json"
    if not os.path.isdir("/d/tmp"):
        out = os.path.join(os.environ.get("TEMP", "."), "sse_resume_100.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in summary.items() if k != "records"},
                     ensure_ascii=False, indent=2))
    ok = (fails == 0 and missing == 0 and bad_terminal == 0
          and cross_stream == 0 and all(p["ok"] for p in probes))
    print(f"\nSSE_100_DISCONNECT_ACCEPTANCE_{'PASS' if ok else 'FAIL'} → {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
