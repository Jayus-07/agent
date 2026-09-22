"""e2e_sql_closure.py — SQL Agent 生产收口 APISIX 真入口 E2E（STOP C）

验证完整链：JWT → APISIX :9080 验签/剥伪造头/重注入 → FastAPI →
Principal → AuthorizationContext → SQLPolicyContext → SQLPolicyGuard →
readonly DB（+ audit 落库）。

Case 列表（规格 §十五）：
  C1  viewer      查 shared           → HTTP 403（sql.read 预检）
  C2  editor/hr   查 shared           → 200 且 status ∈ {success,no_data}
  C3  editor/hr   查 finance          → 200 且 status=permission_denied
  C4  admin/all   查 finance          → 200 且 status ∈ {success,no_data}
  C5  admin/all   查 ai               → 200 且 status ∈ {success,no_data}
  C6  self scope  查 order.orders     → （D3 角色映射下 HTTP 层无此组合；
                                          customer_id 注入由 Guard 单测锁定）
  C7  editor/hr   查 order_items      → 200 且 status=permission_denied
  C8  viewer JWT + 伪造 X-User-Roles: admin → 403（客户端不能声明身份）
  C9  kill switch 关闭 → 全入口 executor 零调用（单测覆盖；
                                          共享容器不重启，见验收报告）
  C10 policy deny 不重试：audit 表 DENY_TABLE/DENY_PERMISSION 记录可查

--setup：直连权威库（127.0.0.1:5433，agent_memory）幂等创建三个专用账号
  e2e_sqlv(viewer) / e2e_sqle(editor,dept=hr) / e2e_sqla(admin)。

用法（仓库根或 backend/，网关在跑时）:
    cd backend && python scripts/e2e_sql_closure.py --setup --password <统一密码> \
        && python scripts/e2e_sql_closure.py --api-key <API_KEY> --password <统一密码>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 支持从仓库任意位置运行：backend 包位于仓库根下，把仓库根加入 sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import urllib.error
import urllib.request

BASE = os.environ.get("SQL_E2E_BASE", "http://localhost:9080")


def _request(method: str, url: str, *, headers=None, body=None, timeout=60):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


def login(username: str, password: str) -> str:
    """经 APISIX 登录拿 JWT（链路①：login 本身就走 :9080）。"""
    status, body = _request(
        "POST", f"{BASE}/api/auth/login",
        body={"username": username, "password": password})
    if status != 200:
        raise RuntimeError(f"login {username} 失败: {status} {body}")
    token = (body.get("data") or {}).get("token")
    if not token:
        raise RuntimeError(f"login {username} 响应缺 token: {body}")
    return token


def sql_query(api_key: str, token: str, question: str, *,
              spoof_roles: str | None = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-API-Key": api_key,
    }
    if spoof_roles:
        headers["X-User-Roles"] = spoof_roles  # Case 8：客户端伪造角色头
    return _request("POST", f"{BASE}/api/sql/query",
                    headers=headers, body={"question": question})


# ── Case 实现（返回 (ok, 详情)）─────────────────────────────

def c1_viewer_shared(api_key, pwd):
    token = login("e2e_sqlv", pwd)
    status, body = sql_query(api_key, token, "查询商品列表")
    return status == 403, f"HTTP {status} body={str(body)[:120]}"


def c2_editor_shared(api_key, pwd):
    token = login("e2e_sqle", pwd)
    status, body = sql_query(api_key, token, "查询商品列表")
    return (status == 200 and body.get("status") in ("success", "no_data"),
            f"HTTP {status} status={body.get('status')} rows={body.get('row_count')}")


def c3_editor_finance(api_key, pwd):
    token = login("e2e_sqle", pwd)
    status, body = sql_query(api_key, token, "查询财务费用支出")
    ok = (status == 200 and body.get("status") == "permission_denied"
          and "finance" not in json.dumps(body, ensure_ascii=False))
    return ok, f"HTTP {status} status={body.get('status')} error={body.get('error')}"


def c4_admin_finance(api_key, pwd):
    token = login("e2e_sqla", pwd)
    status, body = sql_query(api_key, token, "查询财务费用支出")
    return (status == 200 and body.get("status") in ("success", "no_data"),
            f"HTTP {status} status={body.get('status')} rows={body.get('row_count')}")


def c5_admin_ai(api_key, pwd):
    token = login("e2e_sqla", pwd)
    status, body = sql_query(api_key, token, "查询 agent 任务记录")
    return (status == 200 and body.get("status") in ("success", "no_data"),
            f"HTTP {status} status={body.get('status')} rows={body.get('row_count')}")


def c7_editor_order_items(api_key, pwd):
    token = login("e2e_sqle", pwd)
    status, body = sql_query(api_key, token, "查询订单明细")
    ok = status == 200 and body.get("status") == "permission_denied"
    return ok, f"HTTP {status} status={body.get('status')} error={body.get('error')}"


def c8_spoof_roles_header(api_key, pwd):
    """viewer JWT + 伪造 X-User-Roles: admin —— 身份只能来自网关重注入。"""
    token = login("e2e_sqlv", pwd)
    status, body = sql_query(api_key, token, "查询商品列表",
                             spoof_roles="admin")
    return status == 403, f"HTTP {status}（伪造角色头未提权）body={str(body)[:80]}"


def c10_audit_records(password: str) -> tuple[bool, str]:
    """直连权威库（5433）验证 audit 落库：http 通道 ALLOW 与 DENY 均存在。"""
    import psycopg2

    conn = psycopg2.connect(host="127.0.0.1", port=5433,
                            dbname="agent_memory", user="postgres",
                            password=os.environ.get("PGPASSWORD_SUPERUSER",
                                                    "postgres"))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT decision, count(*) FROM sql_query_audits "
                        "WHERE source_channel='http' GROUP BY decision")
            rows = dict(cur.fetchall())
    finally:
        conn.close()
    has_allow = any(k.startswith("EXECUTION_") for k in rows)
    has_deny = any(k.startswith("DENY_") for k in rows)
    return (has_allow and has_deny), f"audit(http)={rows}"


CASES = [
    ("C1 viewer→shared 403", lambda ak, pw: c1_viewer_shared(ak, pw)),
    ("C2 editor→shared allow", lambda ak, pw: c2_editor_shared(ak, pw)),
    ("C3 editor→finance deny", lambda ak, pw: c3_editor_finance(ak, pw)),
    ("C4 admin→finance allow", lambda ak, pw: c4_admin_finance(ak, pw)),
    ("C5 admin→ai allow", lambda ak, pw: c5_admin_ai(ak, pw)),
    ("C7 editor→order_items deny", lambda ak, pw: c7_editor_order_items(ak, pw)),
    ("C8 伪造角色头不提权", lambda ak, pw: c8_spoof_roles_header(ak, pw)),
]


def setup_accounts(password: str) -> None:
    """幂等创建三个 E2E 账号（直连权威库 5433——显式端口，勿用 5432）。"""
    import psycopg2

    from backend.security.local_jwt import hash_password

    conn = psycopg2.connect(host="127.0.0.1", port=5433,
                            dbname="agent_memory", user="postgres",
                            password=os.environ.get("PGPASSWORD_SUPERUSER",
                                                    "postgres"))
    accounts = [
        ("e2e_sqlv", "viewer", ""),
        ("e2e_sqle", "editor", "hr"),
        ("e2e_sqla", "admin", ""),
    ]
    try:
        with conn.cursor() as cur:
            for username, role, dept in accounts:
                cur.execute("SELECT 1 FROM auth.users WHERE username=%s",
                            (username,))
                if cur.fetchone():
                    cur.execute(
                        "UPDATE auth.users SET role=%s, dept=%s, status=1 "
                        "WHERE username=%s", (role, dept, username))
                    print(f"  [setup] {username}: 角色更新为 {role}")
                else:
                    cur.execute(
                        "INSERT INTO auth.users (username, password_hash, "
                        "real_name, role, dept, tenant_id) "
                        "VALUES (%s, %s, %s, %s, %s, 'default')",
                        (username, hash_password(password),
                         username, role, dept))
                    print(f"  [setup] {username}: 创建（{role}, dept={dept or '-'}）")
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="幂等创建 E2E 账号")
    ap.add_argument("--password", default=os.environ.get("SQL_E2E_PASSWORD", ""))
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", ""))
    args = ap.parse_args()

    if args.setup:
        if not args.password:
            print("--setup 需要 --password")
            return 2
        setup_accounts(args.password)
        return 0

    if not args.api_key or not args.password:
        print("需要 --api-key 与 --password（或对应环境变量）")
        return 2

    print(f"目标网关: {BASE}\n")
    passed, failed = 0, []
    for name, fn in CASES:
        try:
            ok, detail = fn(args.api_key, args.password)
        except Exception as e:
            ok, detail = False, f"异常: {e}"
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}: {detail}")
        (passed := passed + 1) if ok else failed.append(name)
    # C10：audit 落库验证（只读查询）
    try:
        ok, detail = c10_audit_records(args.password)
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] C10 audit 落库（ALLOW+DENY）: {detail}")
        (passed := passed + 1) if ok else failed.append("C10")
    except Exception as e:
        print(f"  [FAIL] C10 audit 落库: 异常 {e}")
        failed.append("C10")

    print(f"\n结果: {passed} passed, {len(failed)} failed")
    if failed:
        print("失败项:", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
