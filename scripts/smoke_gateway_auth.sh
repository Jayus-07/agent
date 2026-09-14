#!/usr/bin/env bash
# smoke_gateway_auth.sh — P2 网关鉴权冒烟
# 验收标准来源：docs/auth/02-详细架构设计.md §12「网关过滤器」条目
#
# 覆盖：
#   A 路由与白名单：/api/auth/login 经网关全通；refresh 不被网关拦
#   B 身份头协议（需 --with-stub）：注入正确 / 伪造头被剥离 / api-key 打标透传
#   C 拒绝行为（enforce）：无凭据、签名篡改、异密钥、异 issuer、过期、refresh 当 access、登出后黑名单
#   D 影子行为（shadow）：本该拒绝的被放行、不注入身份、would-deny 指标计数
#
# 用法：
#   bash scripts/smoke_gateway_auth.sh shadow               # 影子模式
#   bash scripts/smoke_gateway_auth.sh enforce              # 强制模式
#   bash scripts/smoke_gateway_auth.sh enforce --with-stub  # 额外验证身份头协议
#
# 前置：oa-auth 五容器 healthy、网关在跑、.env 已配 JWT_SECRET / AUTH_REDIS_*
#
# ⚠️ 端口注意：本机若存在占用 127.0.0.1:8080 的原生进程（本项目实测遇到过
#    "腾讯位置服务演示台"），Windows 会更优先匹配这个更具体的绑定，导致
#    curl 127.0.0.1:8080 打不到 Docker 发布的网关（Docker 只监听 0.0.0.0:8080）。
#    本脚本自动探测：探不到网关就改用容器网络内 api-gateway:8080 访问。
set -uo pipefail

MODE="${1:-shadow}"
WITH_STUB=0
for a in "$@"; do [ "$a" = "--with-stub" ] && WITH_STUB=1; done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW_CONTAINER="${GW_CONTAINER:-agent-api-gateway-1}"
STUB_CONTAINER="gw-echo-stub"
STUB_PORT=8099
PASS=0; FAIL=0

# 认证中心仓库（取管理员口令 / JWT 密钥）
ENV_OAUTH="${OAUTH_ENV:-D:/Program Files/workplace/Enterprise_OA/anonymous-rating-system/docker/.env.oaauth}"
PY="D:/Python/python.exe"; command -v "$PY" >/dev/null 2>&1 || PY=python

CJ="$(mktemp)"
cleanup() {
  rm -f "$CJ"
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1
}
trap cleanup EXIT

check() {  # check 名称 结果(0=过) [详情]
  if [ "$2" = "0" ]; then echo "  ✅ $1"; PASS=$((PASS+1));
  else echo "  ❌ $1"; [ -n "${3:-}" ] && echo "     → $(printf '%s' "$3" | head -c 300)"; FAIL=$((FAIL+1)); fi
}

env_val() { grep "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }
ADMIN_PASS="$(env_val ADMIN_INITIAL_PASSWORD "$ENV_OAUTH")"
JWT_SECRET="$(env_val JWT_SECRET_KEY "$ENV_OAUTH")"
[ -n "$ADMIN_PASS" ] && [ -n "$JWT_SECRET" ] || { echo "[smoke] 无法读取 .env.oaauth 中的口令/密钥"; exit 1; }

NET="$(docker inspect "$GW_CONTAINER" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>/dev/null)"

# ── 访问通道探测 ────────────────────────────────────────────────────────────
# HOST 模式：宿主机 curl；CONTAINER 模式：借一个在网容器内的 curl
if curl -s --max-time 3 http://127.0.0.1:8080/actuator/health 2>/dev/null | grep -q '"status":"UP"'; then
  ACCESS=HOST; CURLER=""
else
  ACCESS=CONTAINER
  CURLER="${CURLER:-agent-rag-service-1}"
  docker exec "$CURLER" curl -s --max-time 3 http://api-gateway:8080/actuator/health 2>/dev/null | grep -q '"status":"UP"' \
    || { echo "[smoke] 两种通道都探不到网关（127.0.0.1:8080 被占用？$CURLER 未运行？）"; exit 1; }
fi
BASE="http://api-gateway:8080"; [ "$ACCESS" = "HOST" ] && BASE="http://127.0.0.1:8080"
echo "═══ P2 网关鉴权冒烟（模式=$MODE，访问通道=$ACCESS）═══"

# gw <path> [curl 参数...] → 输出响应体
gw() {
  local path="$1"; shift
  if [ "$ACCESS" = "HOST" ]; then curl -s --max-time 12 "${BASE}${path}" "$@"
  else docker exec "$CURLER" curl -s --max-time 12 "${BASE}${path}" "$@"; fi
}
# gw_code <path> [curl 参数...] → 输出 HTTP 状态码
gw_code() {
  local path="$1"; shift
  if [ "$ACCESS" = "HOST" ]; then curl -s -o /dev/null -w '%{http_code}' --max-time 12 "${BASE}${path}" "$@"
  else docker exec "$CURLER" curl -s -o /dev/null -w '%{http_code}' --max-time 12 "${BASE}${path}" "$@"; fi
}

json_get() { "$PY" -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: print(''); sys.exit()
v=d.get('$1') if not isinstance(d.get('data'),dict) else d['data'].get('$1')
print(v if v is not None else '')" 2>/dev/null; }

forge() {  # forge <secret> <issuer> <exp偏移秒> <type>
  "$PY" - "$1" "$2" "$3" "$4" <<'PY'
import base64, hashlib, hmac, json, sys, time
secret, iss, off, typ = sys.argv[1:5]
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=')
now = int(time.time())
h = b64(json.dumps({"alg": "HS512", "typ": "JWT"}, separators=(',', ':')).encode())
p = b64(json.dumps({"type": typ, "userId": 999999, "username": "forge", "deviceId": "smoke",
                    "jti": "999999", "sub": "forge", "iss": iss,
                    "iat": now, "exp": now + int(off)}, separators=(',', ':')).encode())
print((h + b'.' + p + b'.' + b64(hmac.new(secret.encode(), h + b'.' + p, hashlib.sha512).digest())).decode())
PY
}

# ── A. 路由与白名单 ────────────────────────────────────────────────────────
echo "── A. 路由与白名单 ──"
LOGIN=$(gw /api/auth/login -X POST -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PASS\",\"deviceId\":\"gw-smoke\"}")
TOKEN=$(printf '%s' "$LOGIN" | json_get token)
[ -n "$TOKEN" ]
check "A1 /api/auth/login 经网关路由到 auth-service 并签发令牌" $? "$LOGIN"

ADMIN_ID=$(printf '%s' "$TOKEN" | "$PY" -c "
import sys,json,base64
t=sys.stdin.read().strip(); p=t.split('.')[1]; p+='='*(-len(p)%4)
print(json.loads(base64.urlsafe_b64decode(p))['userId'])" 2>/dev/null)
[ -n "$ADMIN_ID" ]
check "A2 令牌 claims 可解析出真实 userId=$ADMIN_ID" $? "$LOGIN"

code=$(gw_code /api/auth/refresh -X POST)
[ "$code" != "401" ]
check "A3 /api/auth/refresh 属白名单，未被网关拦成 401" $? "HTTP $code"

# ── B. 身份头协议（回显桩）─────────────────────────────────────────────────
if [ "$WITH_STUB" = "1" ]; then
  echo "── B. 身份头协议（回显桩 :$STUB_PORT）──"
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1
  docker create --name "$STUB_CONTAINER" --network "$NET" python:3.10-slim python /echo_stub.py "$STUB_PORT" >/dev/null
  docker cp "$ROOT/scripts/gateway_echo_stub.py" "$STUB_CONTAINER:/echo_stub.py" >/dev/null
  docker start "$STUB_CONTAINER" >/dev/null
  sleep 2
  # 冒烟专用覆盖：把 AI 路由临时指向回显桩（脚本结束时还原）
  (cd "$ROOT" && AI_SERVICE_URL="http://${STUB_CONTAINER}:${STUB_PORT}" \
      docker compose up -d --force-recreate api-gateway >/dev/null 2>&1)
  for _ in $(seq 1 30); do [ "$(gw_code /actuator/health)" = "200" ] && break; sleep 2; done

  probe_headers() {  # probe_headers [curl 参数...] → 打印下游收到的相关头
    gw /api/echo-probe "$@" | "$PY" -c "
import sys,json
try: h=json.load(sys.stdin)['headers']
except Exception: print('  <无 JSON 响应>'); sys.exit()
for k in ['X-Auth-Type','X-User-Id','X-User-Name','X-API-Key']:
    print(f'  {k} = {h.get(k, \"<缺失>\")}')"
  }

  if [ "$MODE" = "shadow" ]; then
    OUT=$(probe_headers -H "Authorization: Bearer $TOKEN")
    printf '%s' "$OUT" | grep -q '<缺失>'
    check "B1 影子模式不注入身份（行为零改变，仅记指标）" $? "$OUT"
  else
    OUT=$(probe_headers -H "Authorization: Bearer $TOKEN")
    printf '%s' "$OUT" | grep -q "X-User-Id = $ADMIN_ID"
    check "B1 有效令牌 → 下游收到注入的 X-User-Id=$ADMIN_ID" $? "$OUT"
    printf '%s' "$OUT" | grep -q "X-Auth-Type = jwt"
    check "B2 有效令牌 → X-Auth-Type=jwt" $? "$OUT"
    printf '%s' "$OUT" | grep -q "X-User-Name = admin"
    check "B3 有效令牌 → X-User-Name=admin" $? "$OUT"
  fi

  # 核心防伪造：伪造头 + 有效令牌 → 下游只能看到网关注入的真身
  OUT=$(probe_headers -H "Authorization: Bearer $TOKEN" \
        -H "X-User-Id: 99999" -H "X-User-Name: attacker" -H "X-Auth-Type: api-key")
  printf '%s' "$OUT" | grep -qE "99999|attacker|api-key"
  [ $? -ne 0 ]
  check "B4 伪造的 X-User-Id/X-User-Name/X-Auth-Type 全部被剥离" $? "$OUT"

  OUT=$(probe_headers -H "X-API-Key: machine-key-demo")
  printf '%s' "$OUT" | grep -q "X-Auth-Type = api-key"
  check "B5 API-Key 通道被标记 X-Auth-Type=api-key" $? "$OUT"
  printf '%s' "$OUT" | grep -q "X-API-Key = machine-key-demo"
  check "B6 API-Key 原样透传（校验仍在下游）" $? "$OUT"

  # 还原真实上游
  (cd "$ROOT" && docker compose up -d --force-recreate api-gateway >/dev/null 2>&1)
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1
  for _ in $(seq 1 30); do [ "$(gw_code /actuator/health)" = "200" ] && break; sleep 2; done
fi

# ── C / D. 凭据校验 ────────────────────────────────────────────────────────
PROBE="/api/auth/info?userId=$ADMIN_ID"
TOKEN_TAMPERED=$(printf '%s' "$TOKEN" | "$PY" -c "
import sys
h,p,s = sys.stdin.read().strip().rsplit('.',2)
print(f'{h}.{p}.' + s[:-1] + ('A' if s[-1] != 'A' else 'B'))")

if [ "$MODE" = "enforce" ]; then
  echo "── C. 拒绝行为（enforce）──"
  expect_401() { local code; code=$(gw_code "$PROBE" -H "Authorization: Bearer $2"); [ "$code" = "401" ]; check "$1" $? "HTTP $code"; }
  expect_401 "C1 无凭据 → 401" ""
  expect_401 "C2 签名被篡改 → 401" "$TOKEN_TAMPERED"
  expect_401 "C3 异密钥签名 → 401" "$(forge 'another-secret-long-enough-0123456789012345' hongmeng-oa 600 access)"
  expect_401 "C4 异 issuer(attacker) → 401" "$(forge "$JWT_SECRET" attacker 600 access)"
  expect_401 "C5 已过期 → 401" "$(forge "$JWT_SECRET" hongmeng-oa -600 access)"
  expect_401 "C6 refresh 令牌当 access 用 → 401" "$(forge "$JWT_SECRET" hongmeng-oa 600 refresh)"

  code=$(gw_code "$PROBE" -H "Authorization: Bearer $TOKEN"); [ "$code" = "200" ]
  check "C7 有效令牌 → 200" $? "HTTP $code"

  # C8 黑名单：登出后同一 access 令牌立即失效
  # auth-service 的 logout 仅在携带 refresh_token Cookie 时才真正吊销，故必须带 cookie
  if [ "$ACCESS" = "HOST" ]; then
    curl -s -c "$CJ" -o /dev/null -X POST "${BASE}/api/auth/login" -H 'Content-Type: application/json' \
      -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PASS\",\"deviceId\":\"gw-blacklist\"}"
    curl -s -o /dev/null -X POST "${BASE}/api/auth/logout" -b "$CJ" -H "Authorization: Bearer $TOKEN"
  else
    docker exec "$CURLER" curl -s -c /tmp/_gw_cj -o /dev/null -X POST "${BASE}/api/auth/login" \
      -H 'Content-Type: application/json' \
      -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PASS\",\"deviceId\":\"gw-blacklist\"}"
    docker exec "$CURLER" curl -s -o /dev/null -X POST "${BASE}/api/auth/logout" -b /tmp/_gw_cj \
      -H "Authorization: Bearer $TOKEN"
  fi
  sleep 1
  expect_401 "C8 登出后原 access 令牌 → 401（黑名单 auth:blacklist: 生效）" "$TOKEN"

  code=$(gw_code "/api/auth/info?userId=99999" -H "X-User-Id: 99999" -H "X-Auth-Type: jwt")
  [ "$code" = "401" ]
  check "C9 仅伪造身份头不能通过认证 → 401" $? "HTTP $code"
else
  echo "── D. 影子行为（shadow：只记不拦）──"
  code=$(gw_code "$PROBE"); [ "$code" != "401" ]
  check "D1 无凭据 → 放行（影子）" $? "HTTP $code"
  code=$(gw_code "$PROBE" -H "Authorization: Bearer $TOKEN_TAMPERED"); [ "$code" != "401" ]
  check "D2 篡改令牌 → 放行（影子）" $? "HTTP $code"

  METRIC=$(gw /actuator/metrics/gateway_auth_would_deny_total | "$PY" -c \
    "import sys,json;print(json.load(sys.stdin)['measurements'][0]['value'])" 2>/dev/null)
  [ -n "$METRIC" ] && [ "${METRIC%%.*}" -gt 0 ]
  check "D3 gateway_auth_would_deny_total 已计数（count=$METRIC）" $? "$METRIC"
fi

echo
echo "═══ 结果：通过 $PASS / 失败 $FAIL ═══"
[ "$FAIL" = "0" ] || exit 1
