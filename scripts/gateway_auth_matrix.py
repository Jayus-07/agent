#!/usr/bin/env python
"""gateway_auth_matrix.py — B2 认证 12 场景矩阵测试（纯标准库，无外部依赖）

对 APISIX 测试实例（9081，enforce 模式）逐场景验证 gateway-auth 行为，
产出结构化 JSON 报告（.workbuddy/gateway_auth_matrix_report.json）。

场景清单（任务书 B2 §4）：
  1  valid_access        合法 access JWT → 200，echo 可见注入身份头
  2  no_credential        缺失 JWT → 401 no-credential
  3  expired              过期 JWT → 401 expired
  4  wrong_signature      签名错误 → 401 signature
  5  wrong_issuer         issuer 错误 → 401 issuer
  6  missing_user_id      claims 缺失 → 401 missing-user-id
  7  refresh_type         refresh 当 access → 401 token-type-mismatch
  8  rotation             双密钥轮换（previous 签名）→ 200
  9  blacklist_hit        黑名单命中 → 401 blacklist（需测试 redis：--redis-host）
  10 forged_headers        伪造身份头 → 剥离 + 正确注入；X-Trace-Id 透传保留
  11 whitelist_passthrough login 白名单 → Java 业务响应（非网关 401）
  12 downstream_key_intact FastAPI X-API-Key 行为不变（有效 JWT + 无 Key → 下游 401）
故障注入（独立运行期，bash 编排容器 env）：
  R1 redis_refused         → 401 blacklist-unavailable（fail-closed）
  R2 redis_timeout         → 401 blacklist-timeout（fail-closed）
  R3 redis_down_burst      20 并发 × 拒连 → 全部 401 fail-closed（池耗尽退化形态）

用法：
  python scripts/gateway_auth_matrix.py --base http://127.0.0.1:9081 \
      --secret "$SECRET64" --previous "$SECRET64_OLD" \
      [--redis-host 127.0.0.1 --redis-port 16380] \
      [--expect-fail-reason blacklist-unavailable]   # 故障注入相位专用

退出码：任一场景失败 → 1。失败时输出具体预期/实际/原因，无 except: pass。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib import error, request

UA = "gateway-auth-matrix/1.0"


# ── JWT 生成（纯标准库；与 JJWT HS 系对齐）──────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign_hs(alg: str, secret: str, signing_input: str) -> str:
    algo = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}[alg]
    return _b64url(hmac.new(secret.encode(), signing_input.encode(), algo).digest())


def make_jwt(secret: str, claims: dict, alg: str = "HS512") -> str:
    header = {"alg": alg, "typ": "JWT"}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
                    _b64url(json.dumps(claims, separators=(",", ":")).encode())
    return signing_input + "." + _sign_hs(alg, secret, signing_input)


def access_claims(**over) -> dict:
    now = int(time.time())
    c = {"userId": 10001, "username": "matrix-user", "dept": "dept-42",
         "type": "access", "deviceId": "matrix-dev",
         "iss": "hongmeng-oa", "iat": now, "exp": now + 3600}
    c.update(over)
    return c


def valid_token(secret: str, **over) -> str:
    return make_jwt(secret, access_claims(**over))


# ── 微型 RESP 客户端（仅测试黑名单种子数据用，无密码实例）────────

def redis_cmd(host: str, port: int, *args: str) -> str:
    payload = f"*{len(args)}\r\n".encode()
    for a in args:
        b = a.encode()
        payload += f"${len(b)}\r\n".encode() + b + b"\r\n"
    s = socket.create_connection((host, port), timeout=3)
    try:
        s.sendall(payload)
        buf = b""
        while b"\r\n" not in buf:
            chunk = s.recv(1024)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="replace").strip()
    finally:
        s.close()


# ── HTTP ─────────────────────────────────────────────────────────

def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
         timeout: float = 15.0):
    req = request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    req.add_header("User-Agent", UA)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def bearer(tok: str) -> dict:
    return {"Authorization": "Bearer " + tok}


def echo_headers(body: str) -> dict:
    """回显桩返回 headers dict（大小写不敏感读取）。"""
    try:
        hs = json.loads(body).get("headers", {})
    except Exception:
        return {}
    return {k.lower(): v for k, v in hs.items()}


# ── 场景 ─────────────────────────────────────────────────────────

def run_scenarios(args) -> list[dict]:
    results: list[dict] = []
    base = args.base.rstrip("/")
    secret, previous = args.secret, args.previous
    tok_valid = valid_token(secret)
    tok_valid_prev = valid_token(previous or secret)

    def record(name, expect, actual, ok, note=""):
        results.append({"name": name, "expect": expect, "actual": actual,
                        "pass": bool(ok), "note": note})

    # 故障注入相位：只验证"合法 JWT 也必须 401 fail-closed 且 reason 正确"
    if args.expect_fail_reason:
        st, _, body = http("GET", base + "/echo", bearer(tok_valid))
        ok = st == 401 and f"未认证：{args.expect_fail_reason}" in body
        record(f"redis_fault_{args.expect_fail_reason}",
               f"401 + detail 含 未认证：{args.expect_fail_reason}",
               f"{st} + {body[:120]}", ok)
        return results

    # 1 合法 access JWT → 200 + 注入头正确
    st, _, body = http("GET", base + "/echo", bearer(tok_valid))
    hs = echo_headers(body)
    record("valid_access", "200 + 注入 x-auth-type=jwt/x-user-id=10001/x-user-name=matrix-user/x-user-dept=dept-42",
           f"{st} + {hs}", st == 200 and hs.get("x-auth-type") == "jwt"
           and hs.get("x-user-id") == "10001" and hs.get("x-user-name") == "matrix-user"
           and hs.get("x-user-dept") == "dept-42")

    # 1.5 roles claim → X-User-Roles 注入（逗号分隔，顺序保持）
    st, _, body = http("GET", base + "/echo",
                       bearer(valid_token(secret, roles=["admin", "editor"])))
    hs = echo_headers(body)
    record("roles_header_injection", "200 + 注入 x-user-roles=admin,editor",
           f"{st} + {hs}", st == 200 and hs.get("x-user-roles") == "admin,editor")

    # 1.6 角色闸：/api/approvals 写操作（非 GET）要求 admin
    st, _, body = http("POST", base + "/api/approvals/req-1/approve",
                       bearer(valid_token(secret, roles=["viewer"])), body=b"{}")
    record("role_gate_viewer_403", "403 + 未认证：role-insufficient",
           f"{st} + {body[:120]}",
           st == 403 and "role-insufficient" in body)

    # 1.7 角色闸：admin 放行（上游 echo 桩 200）；viewer 的 GET 读不受限
    st, _, body = http("POST", base + "/api/approvals/req-1/approve",
                       bearer(valid_token(secret, roles=["admin"])), body=b"{}")
    ok_admin = st == 200
    st2, _, _ = http("GET", base + "/api/approvals/list",
                     bearer(valid_token(secret, roles=["viewer"])))
    record("role_gate_admin_pass_read_open",
           "admin POST 200；viewer GET 读放行 200",
           f"admin={st} viewer-get={st2}", ok_admin and st2 == 200)

    # 2 缺失 JWT
    st, _, body = http("GET", base + "/echo")
    record("no_credential", "401 + 未认证：no-credential", f"{st} + {body[:120]}",
           st == 401 and "未认证：no-credential" in body)

    # 3 过期 JWT（exp 过去 120s > skew 60s）
    st, _, body = http("GET", base + "/echo", bearer(valid_token(secret, exp=int(time.time()) - 120)))
    record("expired", "401 + 未认证：expired", f"{st} + {body[:120]}",
           st == 401 and "未认证：expired" in body)

    # 4 签名错误（异密钥同长度同算法）
    st, _, body = http("GET", base + "/echo", bearer(valid_token("x" * 64)))
    record("wrong_signature", "401 + 未认证：signature", f"{st} + {body[:120]}",
           st == 401 and "未认证：signature" in body)

    # 5 issuer 错误
    st, _, body = http("GET", base + "/echo", bearer(valid_token(secret, iss="evil-issuer")))
    record("wrong_issuer", "401 + 未认证：issuer", f"{st} + {body[:120]}",
           st == 401 and "未认证：issuer" in body)

    # 6 claims 缺失（无 userId）
    st, _, body = http("GET", base + "/echo",
                       bearer(make_jwt(secret, access_claims(**{"userId": None, "iss": "hongmeng-oa"}))))
    record("missing_user_id", "401 + 未认证：missing-user-id", f"{st} + {body[:120]}",
           st == 401 and "未认证：missing-user-id" in body)

    # 7 refresh 当 access
    st, _, body = http("GET", base + "/echo", bearer(valid_token(secret, type="refresh")))
    record("refresh_type", "401 + 未认证：token-type-mismatch", f"{st} + {body[:120]}",
           st == 401 and "未认证：token-type-mismatch" in body)

    # 8 双密钥轮换：previous 签名的合法令牌
    st, _, body = http("GET", base + "/echo", bearer(tok_valid_prev))
    hs = echo_headers(body)
    record("rotation", "200 + 注入身份（previous 密钥签名）",
           f"{st} + x-user-id={hs.get('x-user-id')}",
           st == 200 and hs.get("x-user-id") == "10001",
           note="primary 签名失败 → previous 验签通过" if st == 200 else "")

    # 9 黑名单命中（写入测试 redis，只允许无密码测试实例）
    if args.redis_host and args.redis_port:
        try:
            redis_cmd(args.redis_host, args.redis_port, "SET", "auth:blacklist:" + tok_valid, "1")
            st, _, body = http("GET", base + "/echo", bearer(tok_valid))
            ok = st == 401 and "未认证：blacklist" in body and "blacklist-timeout" not in body
            record("blacklist_hit", "401 + 未认证：blacklist", f"{st} + {body[:120]}", ok)
            redis_cmd(args.redis_host, args.redis_port, "DEL", "auth:blacklist:" + tok_valid)
        except Exception:
            record("blacklist_hit", "401 + 未认证：blacklist", "redis 操作异常", False,
                   note=traceback.format_exc()[-300:])
    else:
        results.append({"name": "blacklist_hit", "expect": "-", "actual": "skipped（未提供 --redis-host）",
                        "pass": None, "note": "需测试 redis"})

    # 10 伪造身份头：剥离 + 正确注入 + X-Trace-Id 透传保留
    #    B5/S0-1 扩展：伪造头新增 X-Operator-Role / X-Operator-Id，
    #    二者必须同样被网关剥离 —— 响应头中不应出现（断言 is None）
    st, _, body = http("GET", base + "/echo",
                       {**bearer(tok_valid), "X-User-Id": "hacker", "X-Auth-Type": "admin",
                        "X-User-Name": "hacker-name", "X-User-Dept": "hacker-dept",
                        "X-Operator-Role": "superadmin", "X-Operator-Id": "op-999",
                        "X-Trace-Id": "matrix-trace-123"})
    hs = echo_headers(body)
    record("forged_headers",
           "200 + x-user-id=10001/x-auth-type=jwt（伪造值被剥离）+ x-operator-role/x-operator-id 不存在 "
           "+ x-trace-id=matrix-trace-123（透传保留）",
           f"{st} + {hs}",
           st == 200 and hs.get("x-user-id") == "10001" and hs.get("x-auth-type") == "jwt"
           and hs.get("x-user-name") == "matrix-user" and hs.get("x-user-dept") == "dept-42"
           and hs.get("x-trace-id") == "matrix-trace-123"
           and hs.get("x-operator-role") is None and hs.get("x-operator-id") is None)

    # 11 白名单透传：login 不带 JWT → Java auth-service 业务响应（非网关 401）
    st, _, body = http("POST", base + "/api/auth/login",
                       {"Content-Type": "application/json"}, json.dumps({}).encode())
    record("whitelist_passthrough", "非网关 401（Java 业务校验响应，如 400/422）",
           f"{st} + {body[:120]}",
           not (st == 401 and "未认证：" in body),
           note="网关白名单语义：auth 路由不挂插件")

    # 12 下游 X-API-Key 行为不变：有效 JWT + 无 API Key → FastAPI 自身 401（detail 无 未认证： 前缀）
    st, _, body = http("POST", base + "/api/chat/stream",
                       {"Content-Type": "application/json", **bearer(tok_valid)},
                       json.dumps({"question": "matrix"}).encode())
    record("downstream_key_intact", "401（下游 FastAPI）+ detail=无效或缺失 X-API-Key（无 未认证： 前缀）",
           f"{st} + {body[:120]}",
           st == 401 and "无效或缺失 X-API-Key" in body and "未认证：" not in body)

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:9081")
    ap.add_argument("--secret", required=True, help="测试用 JWT_SECRET（≥32B；与测试容器 env 一致）")
    ap.add_argument("--previous", default=None, help="轮换测试用旧密钥")
    ap.add_argument("--redis-host", default=None, help="测试 redis（无密码）主机，用于黑名单种子")
    ap.add_argument("--redis-port", type=int, default=16380)
    ap.add_argument("--expect-fail-reason", default=None,
                    help="故障注入相位：只验证合法 JWT → 401 fail-closed + 指定 reason")
    ap.add_argument("--report", default=".workbuddy/gateway_auth_matrix_report.json")
    args = ap.parse_args()

    if args.previous is None:
        args.previous = args.secret + "-previous-pad-to-64-bytes-xxxxxxxxxxxxxxxxxxxxxxxxxx"
        # previous 也必须满足 ≥32B；签名时用与主密钥不同的内容

    try:
        results = run_scenarios(args)
    except Exception:
        # 单场景崩溃（如上游桩死亡）不得吞掉整体报告
        results = [{"name": "runner_crash", "expect": "-", "actual": "矩阵执行中断",
                    "pass": False, "note": traceback.format_exc()[-400:]}]
    passed = [r for r in results if r["pass"] is True]
    failed = [r for r in results if r["pass"] is False]
    skipped = [r for r in results if r["pass"] is None]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "mode": "redis-fault:" + args.expect_fail_reason if args.expect_fail_reason else "matrix",
        "total": len(results), "passed": len(passed), "failed": len(failed), "skipped": len(skipped),
        "results": results,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ("base", "mode", "total", "passed", "failed", "skipped")},
                     ensure_ascii=False))
    for r in results:
        mark = {True: "PASS", False: "FAIL", None: "SKIP"}[r["pass"]]
        print(f"[{mark}] {r['name']}: {r['actual'][:140]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
