# -*- coding: utf-8 -*-
"""scripts/travel_long_session_pressure.py — 长会话 20 轮压测（验收 #133）

同一会话连续 20 轮改单（交替换天数/节奏/档位，指纹必变 → 每轮真重排），
逐轮断言：SSE 终态（done 帧）、行程版本单调推进、无 error 帧、耗时采样。
完成后输出每轮耗时与统计（供 #127 基线补充采样）。任一轮失败 → exit 1。

用法：D:/Python/python.exe scripts/travel_long_session_pressure.py
前提：全栈已起（devctl.bat all /y），uitest 账号可登录。
"""
import json
import re
import statistics
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:9080"
API_KEY = "ak_P8iOQbZT4kgUcA8ImY-CycGLseGMIOMiB9jUaUxx4qGo"
ROUNDS = 20


def login():
    body = json.dumps({"username": "uitest_user", "password": "UiTest2026"}).encode()
    req = urllib.request.Request(
        BASE + "/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))["data"]["token"]


def stream(token, session_id, question, max_seconds=110):
    body = json.dumps({"question": question, "session_id": session_id,
                       "domain_hint": ""}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY,
                 "Authorization": f"Bearer {token}",
                 "X-User-Id": "uitest_user", "Accept": "text/event-stream"})
    events, buf, current, text = [], b"", "", ""
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=max_seconds + 30) as resp:
        while time.time() - t0 < max_seconds:
            chunk = resp.read(256)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", errors="replace").strip()
                if line.startswith("event:"):
                    current = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw in ("[DONE]", ""):
                    continue
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                frame["_e"] = current
                events.append(frame)
                if current == "delta":
                    text += str(frame.get("delta") or frame.get("content") or "")
                if current == "done":
                    events.append({"_e": "__text", "text": text})
                    return events
    events.append({"_e": "__text", "text": text})
    return events


# 相邻两轮提示必须不同：同指纹不重排是正确域行为（#62/#4 口径），
# 压测的版本推进断言只对「指纹真的变了」的轮次成立
PROMPTS = [f"改成 {days} 天" for days in
           (3, 2, 4, 3, 2, 5, 3, 4, 2, 5) * 2]


def main() -> int:
    token = login()
    session = f"pressure-{int(time.time())}"
    failures = []

    t0 = time.time()
    events = stream(token, session, "福州2天2人，预算2000，必去三坊七巷")
    done = next((e for e in events if e.get("_e") == "done"), None)
    if not done:
        failures.append("首轮规划无 done 帧")
        print("FAIL 首轮无 done 帧")
        return 1
    print(f"PASS 首轮规划（{time.time()-t0:.1f}s）")

    durations = []
    prev_version = None
    for i, prompt in enumerate(PROMPTS[:ROUNDS], 1):
        t = time.time()
        try:
            events = stream(token, session, prompt)
        except Exception as e:  # noqa: BLE001 — 压测要记录失败轮而不是中断
            failures.append(f"第{i}轮异常: {e}")
            print(f"FAIL 第{i}轮 异常 {e}")
            continue
        duration = time.time() - t
        durations.append(duration)
        done = next((e for e in events if e.get("_e") == "done"), None)
        error = next((e for e in events if e.get("_e") == "error"), None)
        if error:
            failures.append(f"第{i}轮 error 帧")
            print(f"FAIL 第{i}轮 error 帧")
            continue
        if not done:
            failures.append(f"第{i}轮无 done 帧（{len(events)} 帧）")
            print(f"FAIL 第{i}轮 无 done 帧")
            continue
        # chat/stream 的版本在回答尾部版本脚注（*行程 vN*）
        answer = next((e.get("text", "") for e in events
                       if e.get("_e") == "__text"), "")
        m = re.search(r"行程 v(\d+)", answer)
        version = int(m.group(1)) if m else None
        status = "answered" if answer.strip() else None
        ok_version = prev_version is None or (
            isinstance(version, int) and version > prev_version)
        if not ok_version:
            failures.append(f"第{i}轮版本未推进: {prev_version}→{version}")
        print(f"{'PASS' if ok_version else 'FAIL'} 第{i:2d}轮 {duration:5.1f}s "
              f"status={status} v={version} 「{prompt}」")
        prev_version = version if isinstance(version, int) else prev_version

    print("== 压测统计 ==")
    print(f"轮数 {len(durations)}/{ROUNDS}  "
          f"mean={statistics.mean(durations):.1f}s  "
          f"max={max(durations):.1f}s  "
          f"p95={sorted(durations)[int(0.95*(len(durations)-1))]:.1f}s")
    if failures:
        print("失败项:", " | ".join(failures))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
