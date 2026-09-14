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
#   bash scripts/smoke_gateway_auth.sh shadow               # 影子模式（当前上线态）
#   bash scripts/smoke_gateway_auth.sh enforce              # 强制模式
#   bash scripts/smoke_gateway_auth.sh enforce --with-stub  # 额外验证身份头协议
#
# 前置：oa-auth 五容器 healthy、网关在跑、.env 已配 JWT_SECRET / AUTH_REDIS_*
#
# 两个关键实现约定（都是踩坑后定下来的，别改回去）：
# 1. 断言必须认"网关自己吐的 401"（响应体含 `未认证：`）。只判 HTTP 401 不够——
#    下游 auth-service 自己也会 401（例如 shadow 模式不注入身份时），会把
#    "网关放行了"误判成"网关拦住了"。
# 2. 凡是把宿主路径交给 docker（-v/-f/--env-file/cp）都必须先 cygpath -w，
#    否则 Git Bash 的 MSYS 转换会产出 `D:\d\Program Files\...` 这种畸形路径。
set -uo pipefail

MODE="${1:-shadow}"
WITH_STUB=0
for a in "$@"; do [ "$a" = "--with-stub" ] && WITH_STUB=1; done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GW_CONTAINER="${GW_CONTAINER:-agent-api-gateway-1}"
STUB_CONTAINER="gw-echo-stub"
STUB_PORT=8099
PASS=0; FAIL=0

ENV_OAUTH="${OAUTH_ENV:-D:/Program Files/workplace/Enterprise_OA/anonymous-rating-system/docker/.env.oaauth}"
PY="D:/Python/python.exe"; command -v "$PY" >/dev/null 2>&1 || PY=python
to_win() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

# cookie jar 放工作区（mktemp 在 Git Bash 下返回混合路径，交给 rm 会触发 safe-delete 告警）
CJ="$ROOT/.smoke_cookies.tmp"
cleanup() {
  rm -f "$CJ" 2>/dev/null
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1
}
trap cleanup EXIT

check() {  # check 名称 结果(0=过) [详情]
  if [ "$2" = "0" ]; then echo "  OK   $1"; PASS=$((PASS+1));
  else echo "  FAIL $1"; [ -n "${3:-}" ] && echo "       -> $(printf '%s' "$3" | head -c 300)"; FAIL=$((FAIL+1)); fi
}

env_val() { grep "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }

# 模式：显式传参优先；否则问**运行中的容器**实际生效的环境变量。
# 不能拿 .env 当依据：改 .env 不 force-recreate 是不会生效的，两者会错位
# （脚本判 enforce、网关实际跑 shadow → D1~D4 全假红，极易被误读成网关坏了）。
if [ $# -ge 1 ] && [ "${1#--}" = "$1" ]; then
  :   # 第一个参数是模式，MODE 已在上面赋值
else
  MODE="$(docker inspect "$GW_CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
          | grep '^GATEWAY_AUTH_MODE=' | head -1 | cut -d= -f2- | tr -d '\r')"
  MODE="${MODE:-$(env_val GATEWAY_AUTH_MODE "$ROOT/.env")}"
  MODE="${MODE:-shadow}"
fi
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
    || { echo "[smoke] 两种通道都探不到网关（127.0.0.1:8080 被别的进程占用？f$CURLER 未运行？）"; exit 1; }
fi
BASE="http://api-gateway:8080"; [ "$ACCESS" = "HOST" ] && BASE="http://127.0.0.1:8080"
echo "=== P2 网关鉴权冒烟（模式=$MODE，访问通道=$ACCESS）==="

gw() {  # gw <path> [curl 参数...] -> 响应体
  local path="$1"; shift
  if [ "$ACCESS" = "HOST" ]; then curl -s --max-time 15 "${BASE}${path}" "$@"
  else docker exec "$CURLER" curl -s --max-time 15 "${BASE}${path}" "$@"; fi
}
gw_code() {
  local path="$1"; shift
  if [ "$ACCESS" = "HOST" ]; then curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}${path}" "$@"
  else docker exec "$CURLER" curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}${path}" "$@"; fi
}
# 网关自己的 401 统一响应体带 `未认证：`；下游服务的 401 不带。用于区分"谁拦的"
gw_blocked() {  # gw_blocked <path> [curl 参数...] -> 0=网关拦了
  gw "$1" "${@:2}" | grep -q '未认证：'
}

wait_gateway() {
  for _ in $(seq 1 30); do
    [ "$(gw_code /actuator/health)" = "200" ] && return 0
    sleep 2
  done
  return 1
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

# ── 0. 等网关就绪（否则首条用例必假失败）────────────────────────────────────
wait_gateway || { echo "[smoke] 网关未就绪"; exit 1; }

# ── A. 路由与白名单 ────────────────────────────────────────────────────────
echo "-- A. 路由与白名单 --"
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
  echo "-- B. 身份头协议（回显桩 :$STUB_PORT）--"
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1
  STUB_OK=0
  if docker create --name "$STUB_CONTAINER" --network "$NET" python:3.10-slim python /echo_stub.py "$STUB_PORT" >/dev/null 2>&1 \
     && docker cp "$(to_win "$ROOT/scripts/gateway_echo_stub.py")" "$STUB_CONTAINER:/echo_stub.py" >/dev/null 2>&1 \
     && docker start "$STUB_CONTAINER" >/dev/null 2>&1; then
    sleep 2
    docker exec "$STUB_CONTAINER" python -c "import urllib.request as u;print(u.urlopen('http://127.0.0.1:$STUB_PORT/probe').status)" >/dev/null 2>&1 && STUB_OK=1
  fi
  if [ "$STUB_OK" != "1" ]; then
    echo "  SKIP 回显桩未能启动，跳过 B 组"
  else
    # 冒烟专用覆盖：把 AI 路由临时指向回显桩（脚本结束/还原时会恢复）
    (cd "$ROOT" && AI_SERVICE_URL="http://${STUB_CONTAINER}:${STUB_PORT}" \
        docker compose up -d --force-recreate api-gateway >/dev/null 2>&1)
    wait_gateway

    probe_headers() {
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
      check "B1 有效令牌 -> 下游收到注入的 X-User-Id=$ADMIN_ID" $? "$OUT"
      printf '%s' "$OUT" | grep -q "X-Auth-Type = jwt"
      check "B2 有效令牌 -> X-Auth-Type=jwt" $? "$OUT"
      printf '%s' "$OUT" | grep -q "X-User-Name = admin"
      check "B3 有效令牌 -> X-User-Name=admin" $? "$OUT"
    fi

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
    wait_gateway
  fi
fi

# ── C / D. 凭据校验 ────────────────────────────────────────────────────────
PROBE="/api/auth/info?userId=$ADMIN_ID"
TOKEN_TAMPERED=$(printf '%s' "$TOKEN" | "$PY" -c "
import sys
h,p,s = sys.stdin.read().strip().rsplit('.',2)
# 必须改签名『首字符』而不是末字符：base64url 末位只承载 2~4 个有效比特，
# 改动会在解码时被丢弃，『篡改』后的签名与原签名等价——用例因此时好时坏。
print(f'{h}.{p}.' + ('B' if s[:1] == 'A' else 'A') + s[1:])")

if [ "$MODE" = "enforce" ]; then
  echo "-- C. 拒绝行为（enforce）--"
  expect_blocked() {  # 名称 令牌 —— 必须是"网关拦的"，不是下游拦的
    local body; body=$(gw "$PROBE" -H "Authorization: Bearer $2")
    printf '%s' "$body" | grep -q '未认证：'
    check "$1" $? "$body"
  }
  expect_blocked "C1 无凭据 -> 网关 401" ""
  expect_blocked "C2 签名被篡改 -> 网关 401" "$TOKEN_TAMPERED"
  expect_blocked "C3 异密钥签名 -> 网关 401" "$(forge 'another-secret-long-enough-0123456789012345' hongmeng-oa 600 access)"
  expect_blocked "C4 异 issuer(attacker) -> 网关 401" "$(forge "$JWT_SECRET" attacker 600 access)"
  expect_blocked "C5 已过期 -> 网关 401" "$(forge "$JWT_SECRET" hongmeng-oa -600 access)"
  expect_blocked "C6 refresh 令牌当 access 用 -> 网关 401" "$(forge "$JWT_SECRET" hongmeng-oa 600 refresh)"

  code=$(gw_code "$PROBE" -H "Authorization: Bearer $TOKEN"); [ "$code" = "200" ]
  check "C7 有效令牌 -> 200（含网关注入身份被下游接受）" $? "HTTP $code"

  # C8 黑名单：登出后同一 access 令牌立即失效
  # 两个必须做对的地方：
  # ① auth-service 的 logout 只在携带 refresh_token Cookie 时才真正吊销；
  # ② 吊销是按**设备**做的（refresh_token 里的 deviceId），所以用来登出的
  #    令牌必须与被测令牌来自**同一次登录**——否则吊销的是别的设备，测了个寂寞。
  # ⑤ 路径必须转成 Windows 风格：curl 是原生 Windows 程序，MSYS 的 /d/... 路径
  #    传给 -c 时会静默写不出 jar（文件根本不存在），logout 就带不上 refresh_token。
  JAR_PATH="$(to_win "$CJ")"
  # ③ jar 必须是"本次登录"的：残留 jar 会让 logout 带上一轮的 refresh_token，
  #    吊销的是上一轮设备，当前令牌不受影响（logout 仍返回"登出成功"，极具迷惑性）。
  rm -f "$CJ"
  # ④ 设备号必须唯一：auth-service 按 (userId,deviceId) 缓存 access/refresh 并按设备吊销，
  #    固定 deviceId 会撞上历史运行残留的黑名单与缓存，令用例时好时坏。
  DEV8="gw-bl8-$(date +%s)-$$"
  # ⑥ 诊断用：直接问 Redis「这个令牌进黑名单了吗」，把"登出没吊销"和"网关没拦住"区分开
  REDIS_C="${REDIS_CONTAINER:-oa-auth-redis}"
  REDIS_P="$(env_val AUTH_REDIS_PASSWORD "$ROOT/.env")"
  if [ "$ACCESS" = "HOST" ]; then
    LOGIN8=$(curl -s -c "$JAR_PATH" -X POST "${BASE}/api/auth/login" -H 'Content-Type: application/json' \
      -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PASS\",\"deviceId\":\"$DEV8\"}")
    TOKEN8=$(printf '%s' "$LOGIN8" | json_get token)
    LOGOUT_CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE}/api/auth/logout" \
      -b "$JAR_PATH" -H "Authorization: Bearer $TOKEN8")
  else
    JAR_PATH=/tmp/_gw_cj
    docker exec "$CURLER" rm -f "$JAR_PATH" >/dev/null 2>&1   # 同上：清掉上一轮残留
    LOGIN8=$(docker exec "$CURLER" sh -c "curl -s -c $JAR_PATH -X POST '${BASE}/api/auth/login' \
      -H 'Content-Type: application/json' \
      -d '{\"username\":\"admin\",\"password\":\"$ADMIN_PASS\",\"deviceId\":\"$DEV8\"}'")
    TOKEN8=$(printf '%s' "$LOGIN8" | json_get token)
    LOGOUT_CODE=$(docker exec "$CURLER" curl -s -o /dev/null -w '%{http_code}' \
      -X POST "${BASE}/api/auth/logout" -b "$JAR_PATH" -H "Authorization: Bearer $TOKEN8")
  fi
  sleep 1
  JAR_INFO="文件缺失"
  if [ -f "$CJ" ]; then
    JAR_INFO="cookie=$(grep -c refresh_token "$CJ" 2>/dev/null)"
  fi
  BL_HIT="?"
  if [ -n "$REDIS_P" ] && docker exec "$REDIS_C" true >/dev/null 2>&1; then
    BL_HIT=$(docker exec "$REDIS_C" redis-cli -a "$REDIS_P" --no-auth-warning \
             EXISTS "auth:blacklist:$TOKEN8" 2>/dev/null | tail -1 | tr -d '\r')
  fi
  expect_blocked "C8 登出后同设备原 access 令牌 -> 网关 401（logout=$LOGOUT_CODE jar=$JAR_INFO 黑名单命中=$BL_HIT）" "$TOKEN8"

  body=$(gw "/api/auth/info?userId=99999" -H "X-User-Id: 99999" -H "X-Auth-Type: jwt")
  printf '%s' "$body" | grep -q '未认证：'
  check "C9 仅伪造身份头（无令牌）-> 网关 401，伪造无效" $? "$body"
else
  echo "-- D. 影子行为（shadow：只记不拦）--"
  # 关键：影子模式"放行"不等于下游会 200——下游 auth-service 仍会因缺身份而 401。
  # 所以断言的是"不是网关拦的"。
  body=$(gw "$PROBE"); printf '%s' "$body" | grep -q '未认证：'; [ $? -ne 0 ]
  check "D1 无凭据 -> 网关未拦（下游自身响应）" $? "$(printf '%s' "$body" | head -c 120)"

  body=$(gw "$PROBE" -H "Authorization: Bearer $TOKEN_TAMPERED"); printf '%s' "$body" | grep -q '未认证：'; [ $? -ne 0 ]
  check "D2 篡改令牌 -> 网关未拦（下游自身响应）" $? "$(printf '%s' "$body" | head -c 120)"

  METRIC=$(gw /actuator/metrics/gateway_auth_would_deny_total | "$PY" -c \
    "import sys,json;print(json.load(sys.stdin)['measurements'][0]['value'])" 2>/dev/null)
  [ -n "$METRIC" ] && [ "${METRIC%%.*}" -gt 0 ]
  check "D3 gateway_auth_would_deny_total 已计数（count=$METRIC）" $? "$METRIC"

  METRIC2=$(gw /actuator/metrics/gateway_auth_would_deny_total | "$PY" -c \
    "import sys,json;d=json.load(sys.stdin);print(','.join(next((t['values'] for t in d['availableTags'] if t['tag']=='reason'), [])))" 2>/dev/null)
  [ -n "$METRIC2" ]
  check "D4 would-deny 带 reason 标签（$METRIC2）" $? "$METRIC2"
fi

echo
echo "=== 结果：通过 $PASS / 失败 $FAIL ==="
[ "$FAIL" = "0" ] || exit 1
