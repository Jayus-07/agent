"""tasks/admission/store.py — Admission Redis Store（Lua 原子操作，Phase2 Step4）。

Key 布局（前缀 = TASK_ADMISSION_KEY_PREFIX = "agent:task:admission:"）：
  count:global                      ZSET  member=task_id  score=过期时间戳
  count:tenant:{tenant_id}          ZSET  同上
  count:user:{tenant_id}:{user_id}  ZSET  同上
  count:workflow:{workflow}         ZSET  同上
  token:{task_id}                   HASH  令牌明细（token_id/owner/四元组）
  defer:{task_id}                   STRING  满载 defer 重投计数

设计要点：
- ZSET score = 过期时刻：计数口径 = 物理清掉 score<=now 的成员后 ZCARD。
  Worker crash（无 finally）后成员随 score 过期"自动失效"，任何一次
  acquire 都会顺带物理清理——容量自愈不依赖外部 reaper。
- 原子性：check all limits + increment all + 写 token 在同一 Lua 脚本内
  完成，任一层超限则零写入（一次成功/一次失败，无跨层回滚）。
- per-task 单槽：token key 存在 = 该任务已占槽，acquire 语义为 takeover
  （换 owner + 续期，计数不变）——retry/resume/recovery 重入不会双占。
- release/renew 带 owner CAS：旧 execution（租约已丢）无法误删新 owner
  的槽位。
- scope keys 全部经 KEYS 传入（禁止 Lua 内动态拼 key）；四元组由
  controller 从 record / token hash 统一取得。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# ── Lua：acquire（幂等 takeover + 四层原子准入）──────────────
# KEYS: [token_key, global, tenant, user, workflow]
# ARGV: [now, ttl, lim_g, lim_t, lim_u, lim_w, task_id, owner_exec,
#        token_id, dispatch_stage, tenant_id, user_id, workflow,
#        workload_class, acquired_at]
# 返回 7 元组：[allowed, kind, scope, current, limit, prev_owner, token_id]
#   allowed '1'/'0'；kind = new | takeover | rejected；
#   rejected 时 scope/current/limit 说明是哪层满、水位多少。
_LUA_ACQUIRE = """
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local limits = {tonumber(ARGV[3]), tonumber(ARGV[4]),
                tonumber(ARGV[5]), tonumber(ARGV[6])}
local scope_names = {'global', 'tenant', 'user', 'workflow'}
local token_key = KEYS[1]

if redis.call('EXISTS', token_key) == 1 then
  local prev_owner = redis.call('HGET', token_key, 'owner_execution_id') or ''
  local prev_token = redis.call('HGET', token_key, 'token_id') or ARGV[9]
  redis.call('HSET', token_key,
    'owner_execution_id', ARGV[8],
    'token_id', prev_token,
    'dispatch_stage', ARGV[10])
  redis.call('ZADD', KEYS[2], now + ttl, ARGV[7])
  redis.call('ZADD', KEYS[3], now + ttl, ARGV[7])
  redis.call('ZADD', KEYS[4], now + ttl, ARGV[7])
  redis.call('ZADD', KEYS[5], now + ttl, ARGV[7])
  redis.call('EXPIRE', token_key, ttl)
  return {'1', 'takeover', '', '0', '-1', prev_owner, prev_token}
end

for i = 1, 4 do
  redis.call('ZREMRANGEBYSCORE', KEYS[i + 1], '-inf', now)
  local limit = limits[i]
  if limit >= 0 then
    local cur = redis.call('ZCARD', KEYS[i + 1])
    if cur >= limit then
      return {'0', 'rejected', scope_names[i], tostring(cur),
              tostring(limit), '', ''}
    end
  end
end

redis.call('ZADD', KEYS[2], now + ttl, ARGV[7])
redis.call('ZADD', KEYS[3], now + ttl, ARGV[7])
redis.call('ZADD', KEYS[4], now + ttl, ARGV[7])
redis.call('ZADD', KEYS[5], now + ttl, ARGV[7])
redis.call('HSET', token_key,
  'task_id', ARGV[7],
  'token_id', ARGV[9],
  'owner_execution_id', ARGV[8],
  'tenant_id', ARGV[11],
  'user_id', ARGV[12],
  'workflow', ARGV[13],
  'workload_class', ARGV[14],
  'acquired_at', ARGV[15],
  'dispatch_stage', ARGV[10])
redis.call('EXPIRE', token_key, ttl)
return {'1', 'new', '', '0', '-1', '', ARGV[9]}
"""

# ── Lua：release（owner CAS；幂等——missing/mismatch 均零副作用）──
# KEYS: [token_key, global, tenant, user, workflow]
# ARGV: [owner_exec, task_id]
_LUA_RELEASE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing' end
local owner = redis.call('HGET', KEYS[1], 'owner_execution_id') or ''
if owner ~= ARGV[1] then return 'owner_mismatch' end
redis.call('ZREM', KEYS[2], ARGV[2])
redis.call('ZREM', KEYS[3], ARGV[2])
redis.call('ZREM', KEYS[4], ARGV[2])
redis.call('ZREM', KEYS[5], ARGV[2])
redis.call('DEL', KEYS[1])
return 'released'
"""

# ── Lua：renew（owner CAS；刷新四层 score 与 token TTL）──────
# KEYS/ARGV 同 release，另 ARGV[3]=ttl
_LUA_RENEW = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing' end
local owner = redis.call('HGET', KEYS[1], 'owner_execution_id') or ''
if owner ~= ARGV[1] then return 'owner_mismatch' end
local now = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
redis.call('ZADD', KEYS[2], now + ttl, ARGV[2])
redis.call('ZADD', KEYS[3], now + ttl, ARGV[2])
redis.call('ZADD', KEYS[4], now + ttl, ARGV[2])
redis.call('ZADD', KEYS[5], now + ttl, ARGV[2])
redis.call('EXPIRE', KEYS[1], ttl)
return 'renewed'
"""

_SCOPE_NAMES = ("global", "tenant", "user", "workflow")


class AdmissionStore:
    """Admission 计数/令牌的 Redis 存取（redis_client 可注入，便于测试）。"""

    def __init__(self, key_prefix: str, redis_client: Any | None = None):
        self._prefix = key_prefix
        self._client = redis_client

    # ── key 构造（唯一出口；调用方不得自拼）─────────────────────
    def _scope_keys(self, tenant_id: str, user_id: str, workflow: str) -> list[str]:
        return [
            self._prefix + "count:global",
            self._prefix + "count:tenant:" + tenant_id,
            self._prefix + "count:user:" + tenant_id + ":" + user_id,
            self._prefix + "count:workflow:" + workflow,
        ]

    def token_key(self, task_id: str) -> str:
        return self._prefix + "token:" + task_id

    def defer_key(self, task_id: str) -> str:
        return self._prefix + "defer:" + task_id

    def _redis(self):
        if self._client is not None:
            return self._client
        from backend.infra.redis.client import get_redis

        return get_redis()

    # ── 原子操作 ────────────────────────────────────────────────
    def acquire(self, *, task_id: str, owner_execution_id: str,
                tenant_id: str, user_id: str, workflow: str,
                workload_class: str,
                limits: tuple[int | None, int | None, int | None, int | None],
                ttl_seconds: int, dispatch_stage: str = "execute",
                token_id: str | None = None) -> dict:
        """原子准入：返回 {allowed, kind, scope, current, limit, token_id}。

        limits 元素 None = 该层不限制（Lua 内以 -1 表达）；0 = 全拒。
        """
        r = self._redis()
        if r is None:
            raise ConnectionError("admission store unavailable (redis None)")
        token_id = token_id or uuid.uuid4().hex
        now = int(time.time())
        limit_args = [(-1 if v is None else v) for v in limits]
        keys = [self.token_key(task_id)] + self._scope_keys(
            tenant_id, user_id, workflow)
        result = r.eval(
            _LUA_ACQUIRE, len(keys), *keys,
            now, ttl_seconds, *limit_args,
            task_id, owner_execution_id, token_id, dispatch_stage,
            tenant_id, user_id, workflow, workload_class,
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)),
        )
        allowed, kind, scope, current, limit, _prev, out_token = result
        return {
            "allowed": allowed == "1",
            "kind": kind,
            "scope": scope,
            "current": int(current) if current else None,
            "limit": None if limit == "-1" else int(limit),
            "token_id": out_token,
        }

    def release(self, *, task_id: str, owner_execution_id: str,
                tenant_id: str, user_id: str, workflow: str) -> str:
        """释放（owner CAS）。返回 released / missing / owner_mismatch。"""
        r = self._redis()
        if r is None:
            raise ConnectionError("admission store unavailable (redis None)")
        keys = [self.token_key(task_id)] + self._scope_keys(
            tenant_id, user_id, workflow)
        return r.eval(_LUA_RELEASE, len(keys), *keys,
                      owner_execution_id, task_id)

    def renew(self, *, task_id: str, owner_execution_id: str,
              tenant_id: str, user_id: str, workflow: str,
              ttl_seconds: int) -> str:
        """续期（owner CAS）。返回 renewed / missing / owner_mismatch。"""
        r = self._redis()
        if r is None:
            raise ConnectionError("admission store unavailable (redis None)")
        keys = [self.token_key(task_id)] + self._scope_keys(
            tenant_id, user_id, workflow)
        return r.eval(_LUA_RENEW, len(keys), *keys,
                      owner_execution_id, task_id,
                      int(time.time()), ttl_seconds)

    def get_token(self, task_id: str) -> dict | None:
        """读取令牌明细（观测/对账用；不存在返回 None）。"""
        r = self._redis()
        if r is None:
            return None
        data = r.hgetall(self.token_key(task_id))
        return data or None

    # ── defer 重投计数（满载退避；单写者 = 持该消息的 Worker）──────
    def note_deferred(self, task_id: str, *, ttl_seconds: int = 86400) -> int:
        r = self._redis()
        if r is None:
            raise ConnectionError("admission store unavailable (redis None)")
        pipe = r.pipeline()
        pipe.incr(self.defer_key(task_id))
        pipe.expire(self.defer_key(task_id), ttl_seconds)
        return int(pipe.execute()[0])

    def clear_deferred(self, task_id: str) -> None:
        r = self._redis()
        if r is None:
            return
        r.delete(self.defer_key(task_id))

    def get_deferred(self, task_id: str) -> int:
        """当前 defer 重投计数（无计数/不可用返回 0）。"""
        r = self._redis()
        if r is None:
            return 0
        try:
            return int(r.get(self.defer_key(task_id)) or 0)
        except (TypeError, ValueError):
            return 0

    # ── 对账（reconcile；由既有 zombie_reconcile beat 周期调用）────
    def scan_active_task_ids(self) -> list[str]:
        """global ZSET 的活跃成员（score 未过期）= 当前占槽任务集合。"""
        r = self._redis()
        if r is None:
            return []
        now = int(time.time())
        return list(r.zrangebyscore(
            self._prefix + "count:global", f"({now}", "+inf"))

    def sweep_expired(self) -> int:
        """物理清理 global 口径的过期成员，返回清理数（观测过期回收量）。

        tenant/user/workflow 口径的过期成员由该 scope 下一次 acquire
        顺带清理（ZREMRANGEBYSCORE 在校验前执行）。
        """
        r = self._redis()
        if r is None:
            return 0
        now = int(time.time())
        return int(r.zremrangebyscore(
            self._prefix + "count:global", "-inf", now))

    def scope_active(self, scope: str, *, tenant_id: str = "",
                     user_id: str = "", workflow: str = "") -> int:
        """单 scope 活跃计数（观测接口；先清过期再 ZCARD）。"""
        r = self._redis()
        if r is None:
            return 0
        now = int(time.time())
        keys_map = {
            "global": [self._prefix + "count:global"],
            "tenant": [self._prefix + "count:tenant:" + tenant_id],
            "user": [self._prefix + "count:user:" + tenant_id + ":" + user_id],
            "workflow": [self._prefix + "count:workflow:" + workflow],
        }
        key = keys_map[scope][0]
        r.zremrangebyscore(key, "-inf", now)
        return int(r.zcard(key))
