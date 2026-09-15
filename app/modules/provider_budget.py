"""Bounds how many external-provider calls (OpenAI, outbound HTTP fetches)
are in flight at once ACROSS ALL WORKERS -- Celery's `--concurrency` only
limits parallelism within a single worker process, this limits it fleet-wide
via a Redis-backed semaphore.

Scope of what this actually limits (read before assuming more): this is a
CONCURRENCY limit only -- "at most N calls in flight at any instant, fleet-
wide." It does NOT implement:
  - a request-RATE limit (e.g. "at most N calls per minute" / token-bucket
    throttling) -- a burst of N calls that each finish instantly can still
    repeat back-to-back arbitrarily fast;
  - a total-USAGE cap (e.g. "at most N calls per day", or a token/cost
    budget) -- there is no accounting of how many calls have happened over
    time, only how many are open right now.
If rate- or usage-limiting is needed later, it is a separate mechanism (e.g.
a token-bucket counter with its own TTL window), not something this module
provides as a side effect of concurrency limiting.

Design: each holder registers its own unique token in a Redis SORTED SET,
scored by acquisition time, via a single Lua script that atomically (1)
prunes any member older than `slot_ttl` (an individually-expired permit --
e.g. a worker that crashed without releasing), (2) checks the pruned
cardinality against the limit, and (3) adds the new token only if there was
room. Steps (1)-(3) happen in one Redis command (Lua scripts run atomically,
uninterrupted by other clients), so there is no race window between
"check capacity" and "claim a slot" the way a plain check-then-increment
counter would have.

release() removes ONLY the caller's own token (`ZREM key token`) -- it is
structurally impossible for one holder's release to remove a DIFFERENT
holder's permit, expired or not, because each holder's token is unique and
release only ever names its own.

Falls back to a permissive no-op if Redis is unreachable or not configured,
so this module never becomes a new single point of failure for the
synchronous (non-background-job) API paths that don't need it.
"""
import os
import time
import uuid
import logging

logger = logging.getLogger(__name__)

PROVIDER_CONCURRENCY_LIMIT = int(os.getenv("PROVIDER_CONCURRENCY_LIMIT", "4"))
SLOT_TTL_SECONDS = int(os.getenv("PROVIDER_SLOT_TTL_SECONDS", "120"))  # individual permit expiry if a holder crashes without releasing
ACQUIRE_TIMEOUT_SECONDS = int(os.getenv("PROVIDER_ACQUIRE_TIMEOUT_SECONDS", "30"))

# KEYS[1] = the sorted-set key; ARGV = now, slot_ttl, limit, token (all as strings)
_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl)
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, now, token)
    redis.call('EXPIRE', key, ttl * 2)
    return 1
else
    return 0
end
"""


class ProviderBudgetTimeoutError(Exception):
    """Raised when no provider-call slot became available within the timeout."""


class NullBudget:
    """No-op budget used when Redis isn't configured/reachable -- calls are
    never throttled, which is the correct default for the synchronous API
    paths that don't go through Celery at all."""

    def acquire(self, timeout=None):
        return True

    def release(self):
        pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class RedisTokenBudget:
    """A counting semaphore where each holder owns a distinct, individually-
    expiring token -- see module docstring for why this (not a shared
    counter) is what makes release() safe against ever touching another
    holder's permit."""

    def __init__(self, redis_client, key: str, limit: int, slot_ttl: int):
        self._redis = redis_client
        self._key = key
        self._limit = limit
        self._slot_ttl = slot_ttl
        self._token = uuid.uuid4().hex
        self._held = False

    def acquire(self, timeout=ACQUIRE_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            now = time.time()
            got_slot = self._redis.eval(_ACQUIRE_SCRIPT, 1, self._key, now, self._slot_ttl, self._limit, self._token)
            if got_slot:
                self._held = True
                return True
            if time.monotonic() >= deadline:
                raise ProviderBudgetTimeoutError(
                    f"no provider-call slot available within {timeout}s (limit={self._limit})"
                )
            time.sleep(0.2)

    def release(self):
        if not self._held:
            return
        self._redis.zrem(self._key, self._token)  # removes ONLY this token -- never another holder's
        self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def get_budget(name: str = "default"):
    """Returns a RedisTokenBudget if a broker Redis is reachable, else a
    NullBudget. `name` namespaces the semaphore key (e.g. "openai", "fetch")
    so different provider types can have independent budgets if desired."""
    broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(broker_url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        return RedisTokenBudget(client, f"provider_budget:{name}", PROVIDER_CONCURRENCY_LIMIT, SLOT_TTL_SECONDS)
    except Exception as e:
        logger.warning("provider_budget: Redis unavailable (%s); running without a concurrency budget", e)
        return NullBudget()
