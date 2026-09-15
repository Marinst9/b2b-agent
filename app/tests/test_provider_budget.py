import threading
import time

import pytest

from modules import provider_budget
from modules.provider_budget import RedisTokenBudget, NullBudget, ProviderBudgetTimeoutError, get_budget, _ACQUIRE_SCRIPT


class FakeRedis:
    """In-memory stand-in for the handful of Redis operations
    RedisTokenBudget uses (a sorted set + one Lua script), so its logic can
    be verified deterministically -- including under genuine concurrent
    access from real OS threads -- without a real Redis server. A single
    lock serializes access to the internal dict, mirroring the fact that
    real Redis executes commands (including Lua scripts) atomically/
    single-threadedly; this is what makes the fake a fair stand-in for
    checking the SEMAPHORE's own correctness rather than accidentally
    testing bugs in the fake itself.
    """

    def __init__(self):
        self._sets = {}  # key -> {member: score}
        self._lock = threading.Lock()

    def eval(self, script, numkeys, key, now, ttl, limit, token):
        assert script is _ACQUIRE_SCRIPT  # this fake only understands the one script the module uses
        now, ttl, limit = float(now), float(ttl), int(limit)
        with self._lock:
            members = self._sets.setdefault(key, {})
            cutoff = now - ttl
            for member, score in list(members.items()):
                if score < cutoff:
                    del members[member]
            if len(members) < limit:
                members[token] = now
                return 1
            return 0

    def zrem(self, key, token):
        with self._lock:
            self._sets.get(key, {}).pop(token, None)

    def zcard(self, key):
        with self._lock:
            return len(self._sets.get(key, {}))


def test_null_budget_never_blocks():
    with NullBudget():
        pass  # no exception, no state to check


def test_allows_up_to_the_limit():
    fake = FakeRedis()
    budgets = [RedisTokenBudget(fake, "k", limit=2, slot_ttl=60) for _ in range(2)]
    assert budgets[0].acquire(timeout=1) is True
    assert budgets[1].acquire(timeout=1) is True


def test_rejects_beyond_the_limit():
    fake = FakeRedis()
    holder = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    holder.acquire(timeout=1)

    blocked = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    with pytest.raises(ProviderBudgetTimeoutError):
        blocked.acquire(timeout=0.5)


def test_releasing_frees_a_slot_for_the_next_acquirer():
    fake = FakeRedis()
    first = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    first.acquire(timeout=1)

    second = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    with pytest.raises(ProviderBudgetTimeoutError):
        second.acquire(timeout=0.3)

    first.release()
    assert second.acquire(timeout=1) is True


def test_release_without_acquire_is_a_no_op():
    fake = FakeRedis()
    budget = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    budget.release()  # never held -- must not raise or affect anything
    assert fake.zcard("k") == 0


def test_context_manager_releases_on_exit_even_after_exception():
    fake = FakeRedis()
    budget = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    with pytest.raises(ValueError):
        with budget:
            raise ValueError("boom")

    second = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    assert second.acquire(timeout=1) is True  # slot was released despite the exception


def test_get_budget_falls_back_to_null_budget_when_redis_unreachable(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://localhost:1/0")  # nothing listening on port 1
    budget = get_budget("test")
    assert isinstance(budget, NullBudget)


# --- individually-expiring permits (not a single TTL on the whole key) ------

def test_expired_permit_is_pruned_and_frees_capacity_for_a_new_holder():
    fake = FakeRedis()
    stuck = RedisTokenBudget(fake, "k", limit=1, slot_ttl=1)
    stuck.acquire(timeout=1)  # never released -- simulates a crashed holder

    # Simulate time passing by backdating the stuck holder's score directly.
    fake._sets["k"][stuck._token] = time.time() - 5

    newer = RedisTokenBudget(fake, "k", limit=1, slot_ttl=1)
    assert newer.acquire(timeout=1) is True  # the expired permit was pruned, freeing the slot


def test_a_still_valid_permit_is_never_pruned_early():
    fake = FakeRedis()
    holder = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    holder.acquire(timeout=1)

    other = RedisTokenBudget(fake, "k", limit=1, slot_ttl=60)
    with pytest.raises(ProviderBudgetTimeoutError):
        other.acquire(timeout=0.3)  # holder's permit is still well within its TTL


# --- release can only ever remove the caller's OWN token --------------------

def test_expired_holders_release_cannot_touch_a_newer_holders_permit():
    """The exact scenario requested: holder A's permit expires and is pruned
    (capacity freed for holder B), and A -- unaware its permit is gone --
    later calls release(). A's release must be a complete no-op: it names
    only A's own token, which no longer exists, so it can never remove B's
    live permit."""
    fake = FakeRedis()
    a = RedisTokenBudget(fake, "k", limit=1, slot_ttl=1)
    a.acquire(timeout=1)

    fake._sets["k"][a._token] = time.time() - 5  # A's permit has conceptually expired

    b = RedisTokenBudget(fake, "k", limit=1, slot_ttl=1)
    assert b.acquire(timeout=1) is True  # B takes the freed slot

    a.release()  # A, unaware it already lost its slot, releases "its" permit

    assert fake.zcard("k") == 1  # B's permit is still there
    assert b._token in fake._sets["k"]


def test_two_holders_with_different_tokens_release_independently():
    fake = FakeRedis()
    a = RedisTokenBudget(fake, "k", limit=2, slot_ttl=60)
    b = RedisTokenBudget(fake, "k", limit=2, slot_ttl=60)
    a.acquire(timeout=1)
    b.acquire(timeout=1)

    a.release()

    assert fake.zcard("k") == 1
    assert b._token in fake._sets["k"]
    assert a._token not in fake._sets["k"]


# --- genuine concurrency (real OS threads against the fake) -----------------

def test_concurrent_threads_never_exceed_the_limit():
    """Real threads hammering acquire() at once -- the Lua-script-equivalent
    atomicity in the fake (a single lock around check+insert) must ensure no
    more than `limit` permits are EVER held simultaneously, matching what
    Redis's own single-threaded command execution guarantees for real."""
    fake = FakeRedis()
    limit = 3
    num_threads = 10
    max_observed_concurrent = {"value": 0}
    observed_lock = threading.Lock()
    currently_held = {"count": 0}
    results = []

    def worker():
        budget = RedisTokenBudget(fake, "shared", limit=limit, slot_ttl=30)
        try:
            budget.acquire(timeout=5)
        except ProviderBudgetTimeoutError:
            results.append("timeout")
            return
        with observed_lock:
            currently_held["count"] += 1
            max_observed_concurrent["value"] = max(max_observed_concurrent["value"], currently_held["count"])
        time.sleep(0.05)  # hold the permit briefly so overlaps are likely
        with observed_lock:
            currently_held["count"] -= 1
        budget.release()
        results.append("ok")

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert max_observed_concurrent["value"] <= limit
    assert results.count("ok") == num_threads  # everyone eventually got a turn
    assert fake.zcard("shared") == 0  # all released cleanly


# --- documenting the actual scope of what this limits ------------------------

def test_module_docstring_documents_scope_limits():
    """This is a documentation-presence check, not a behavioral one: the
    milestone explicitly asks whether the implementation limits concurrency,
    rate, or total usage, and the answer (concurrency only) must be written
    down, not left implicit."""
    doc = provider_budget.__doc__
    assert "CONCURRENCY limit only" in doc
    assert "does NOT implement" in doc
    assert "rate" in doc.lower()
    assert "usage" in doc.lower()
