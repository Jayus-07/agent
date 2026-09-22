"""verify_identity_headers.py — APISIX :9080 可信身份头真入口验证（P6/P10）

授权生产收口（2026-09-23）验收脚本。验证走真实网关入口：

  ① 正常登录（login 经 APISIX）→ JWT 签发
  ② 有效 JWT 访问业务端点 → 200（网关验签 + 注入身份头链路通）
  ③ 伪造身份头（无 Bearer）：X-Auth-Type/X-User-Id/X-User-Roles 全套伪造
     → 401（gateway-auth 无条件剥离伪造头，伪造值不构成认证）
  ④ 越权伪造（viewer JWT + X-User-Roles: admin）→ 管理端 403
     （角色只能来自 JWT roles claim 重注入，客户端伪造无效）
  ⑤ 篡改 JWT（改一位签名）→ 401（验签失败）
  ⑥ 无 JWT 访问受保护端点 → 401

说明：app 侧 api_key_middleware 对业务路径要求 X-API-Key（浏览器链路
由 Next BFF 注入），脚本经 --api-key 或环境变量 API_KEY 提供同一密钥。

用法（仓库根，网关在跑时）:
    cd backend && python scripts/verify_identity_headers.py \
        --username <viewer用户名> --password <密码> --api-key <API_KEY>

只读验证：不改任何数据（只用登录 + GET 端点）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error


def _request(method: str, url: str, *, headers=None, body=None, cookies=None):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if cookies:
        req.add_header("Cookie", cookies)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _jwt_payload(token: str) -> dict:
    p = token.split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:9080")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--api-key", default=os.getenv("API_KEY", ""),
                    help="app 侧 api_key_middleware 密钥（与 .env API_KEY 同源）")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    api_key = args.api_key

    results: list[tuple[str, bool, str]] = []

    # ① 经网关登录（验证 auth 链路 + 网关注入 X-Tenant-Id）
    status, text = _request("POST", f"{base}/api/auth/login",
                            body={"username": args.username,
                                  "password": args.password})
    ok = status == 200
    token = ""
    dept_claim = roles_claim = None
    if ok:
        data = json.loads(text)["data"]
        token = data["token"]
        payload = _jwt_payload(token)
        dept_claim = payload.get("dept", "")
        roles_claim = payload.get("roles", [])
    results.append(("① 经 APISIX 登录", ok, f"status={status} dept={dept_claim!r} roles={roles_claim}"))

    def _auth_headers(extra=None):
        headers = {"Authorization": f"Bearer {token}"}
        if api_key:
            headers["X-API-Key"] = api_key
        headers.update(extra or {})
        return headers

    # ② 有效 JWT → 业务端点（网关验签后注入身份头，后端 require_principal 通过）
    status, _ = _request("GET", f"{base}/api/rag/knowledge-bases",
                         headers=_auth_headers())
    results.append(("② 有效 JWT 访问业务端点", status == 200, f"status={status}"))

    # ③ 无 Bearer + 全套伪造身份头 → 401（剥离伪造头，伪造值≠认证）
    forged = {
        "X-Auth-Type": "jwt", "X-User-Id": "1", "X-User-Name": "hacker",
        "X-User-Dept": "finance", "X-User-Roles": "admin",
        "X-User-Permissions": "admin.users.write", "X-Tenant-Id": "default",
    }
    if api_key:
        forged["X-API-Key"] = api_key
    status, _ = _request("GET", f"{base}/api/rag/knowledge-bases", headers=forged)
    results.append(("③ 伪造身份头（无 Bearer）被拒", status == 401, f"status={status}（期望 401）"))

    # ④ viewer JWT + 伪造 X-User-Roles: admin → 管理端 403（角色来自 JWT 重注入）
    status, _ = _request("GET", f"{base}/api/sys/rbac/users",
                         headers=_auth_headers({"X-User-Roles": "admin"}))
    is_viewer = isinstance(roles_claim, list) and "admin" not in roles_claim
    expect = 403 if is_viewer else 200
    results.append(("④ viewer JWT + 伪造 admin 角色头",
                    status == expect,
                    f"status={status}（JWT 角色={roles_claim}，期望 {expect}）"))

    # ⑤ 篡改签名的 JWT → 401
    h, p, s = token.split(".")
    bad_sig = s[:-2] + ("AA" if s[-2:] != "AA" else "BB")
    status, _ = _request("GET", f"{base}/api/rag/knowledge-bases",
                         headers=_auth_headers()
                         | {"Authorization": f"Bearer {h}.{p}.{bad_sig}"})
    results.append(("⑤ 篡改签名 JWT 被拒", status == 401, f"status={status}"))

    # ⑥ 无凭据访问受保护端点 → 401
    status, _ = _request("GET", f"{base}/api/rag/knowledge-bases")
    results.append(("⑥ 无凭据访问被拒", status == 401, f"status={status}"))

    print("=" * 64)
    all_ok = True
    for name, ok, detail in results:
        all_ok &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}")
    print("=" * 64)
    print("GATEWAY_IDENTITY_SPOOF_TEST=" + ("PASS" if all_ok else "FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
