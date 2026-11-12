"""登录/注册限流的原子性（#61 review 第 1 条）。

旧实现是「先 ``check()``，再验密码，失败才 ``record_failure()``」，两者之间没有
占位：并发请求在首个失败落账前全部通过了 ``check()``，然后一起去跑 scrypt。
这些用例直接盯住两件事：

* 名额在**进入**时就原子占下——在途请求数不可能超过 ``max_inflight``；
* 成功只释放自己的名额，**不清零**失败记录。
"""

from __future__ import annotations

import threading
import time

from app.errors import ApiProblem
from app.identity import LoginLimiter

KEY = "203.0.113.7"


def locked(exc: ApiProblem) -> bool:
    return exc.status_code == 429 and exc.code == "ACCOUNT_LOCKED"


def failing(limiter: LoginLimiter, key: str = KEY) -> ApiProblem | None:
    """走一次「认证失败」：正常返回 None，被限流时返回 429。"""

    try:
        with limiter.attempt(key):
            raise ApiProblem(401, "INVALID_CREDENTIALS", "用户名或密码错误。")
    except ApiProblem as exc:
        return exc
    raise AssertionError("unreachable")


def succeeding(limiter: LoginLimiter, key: str = KEY) -> ApiProblem | None:
    try:
        with limiter.attempt(key):
            return None
    except ApiProblem as exc:
        return exc


def test_five_failures_close_the_window():
    limiter = LoginLimiter()

    for _ in range(5):
        problem = failing(limiter)
        assert problem is not None and problem.code == "INVALID_CREDENTIALS"

    blocked = succeeding(limiter)
    assert blocked is not None and locked(blocked)
    assert blocked.headers["Retry-After"]
    # 被拒的这次没有执行，也没记成新的失败：窗口滑过后仍只有 5 条。
    assert len(limiter._attempts[KEY]) == 5


def test_a_success_does_not_clear_the_failure_count():
    """握有一个有效账号的人不能靠「登录成功」把同一 IP 的失败计数刷掉。"""

    limiter = LoginLimiter()
    for _ in range(4):
        failing(limiter)

    assert succeeding(limiter) is None  # 成功只释放名额

    assert failing(limiter).code == "INVALID_CREDENTIALS"  # 第 5 次失败落账
    blocked = succeeding(limiter)
    assert blocked is not None and locked(blocked)


def test_the_lockout_counts_through_windows():
    now = [0.0]
    limiter = LoginLimiter(lock_after=20, clock=lambda: now[0])

    # 每个窗口 5 次失败，窗口之间滑过去，累计 20 次即锁 900 秒。
    for round_index in range(4):
        now[0] = round_index * 61.0
        for _ in range(5):
            failing(limiter)

    blocked = succeeding(limiter)
    assert blocked is not None and locked(blocked)
    assert blocked.details["retryAfterSeconds"] == 900

    now[0] += 900.0
    assert succeeding(limiter) is None


def test_inflight_cap_rejects_rather_than_queueing():
    """5 个请求卡在慢哈希里时，第 6 个立刻 429，而不是排队等着把 CPU 压满。"""

    limiter = LoginLimiter(max_attempts=5, max_inflight=5)
    entered = threading.Semaphore(0)
    release = threading.Event()
    errors: list[BaseException] = []

    def hold() -> None:
        try:
            with limiter.attempt(KEY):
                entered.release()
                release.wait(5.0)
        except BaseException as exc:  # pragma: no cover - 失败时由断言暴露
            errors.append(exc)

    threads = [threading.Thread(target=hold) for _ in range(5)]
    for thread in threads:
        thread.start()
    for _ in range(5):
        assert entered.acquire(timeout=5.0), "在途请求没有全部进入限流块"

    blocked = succeeding(limiter)
    assert blocked is not None and locked(blocked)
    assert blocked.details["retryAfterSeconds"] == 1

    release.set()
    for thread in threads:
        thread.join(5.0)
    assert not errors
    # 名额都释放了：在途满不是失败，下一个请求照常放行。
    assert succeeding(limiter) is None


def test_a_concurrent_burst_cannot_execute_more_than_the_window():
    """20 个并发失败最多只有 5 个真正执行，其余在门口被 429 拦下。"""

    limiter = LoginLimiter(max_attempts=5, max_inflight=5)
    start = threading.Barrier(20)
    executed = []
    rejected = []
    lock = threading.Lock()

    def attack() -> None:
        start.wait(5.0)

        def slow_failure():
            try:
                with limiter.attempt(KEY):
                    # 模拟 scrypt 的耗时：保住在途名额，让其余 15 个在门口被拒。
                    time.sleep(0.2)
                    raise ApiProblem(401, "INVALID_CREDENTIALS", "用户名或密码错误。")
            except ApiProblem as exc:
                return exc
            raise AssertionError("unreachable")

        problem = slow_failure()
        with lock:
            (rejected if problem is not None and locked(problem) else executed).append(problem)

    threads = [threading.Thread(target=attack) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10.0)

    assert len(executed) + len(rejected) == 20
    assert len(executed) == 5, f"窗口内执行了 {len(executed)} 次认证"
    assert len(rejected) == 15


def test_keys_are_independent():
    limiter = LoginLimiter()

    for _ in range(5):
        failing(limiter, "198.51.100.1")

    assert succeeding(limiter, "198.51.100.1") is not None
    assert succeeding(limiter, "198.51.100.2") is None


def test_only_401_and_403_are_counted_as_failures():
    """数据库故障之类的 5xx 只释放名额；否则一次故障就把整片 IP 锁死。"""

    limiter = LoginLimiter()
    for _ in range(20):
        try:
            with limiter.attempt(KEY):
                raise ApiProblem(503, "DEPENDENCY_UNAVAILABLE", "数据库暂不可用。")
        except ApiProblem:
            pass

    assert KEY not in limiter._failures
    assert succeeding(limiter) is None


def test_reset_clears_everything_mid_flight():
    limiter = LoginLimiter()
    for _ in range(3):
        failing(limiter)

    try:
        with limiter.attempt(KEY):
            limiter.reset()  # 名额被占着时重置
            assert limiter._inflight == {}
            raise RuntimeError("放弃这次请求")
    except RuntimeError:
        pass

    # 重置之后退出限流块不能把名额还成负数，也不该复活旧的失败计数。
    assert limiter._inflight == {}
    assert limiter._failures == {}
    assert succeeding(limiter) is None
