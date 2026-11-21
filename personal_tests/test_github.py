import io
import threading
import time
from types import SimpleNamespace

import pytest

from personal import github

SECONDARY = "gh: You have exceeded a secondary rate limit. Please wait a few minutes before you try again. (HTTP 403)"


@pytest.fixture(autouse=True)
def clean():
    github.reset()
    yield
    github.reset()


def gh(calls, outputs):
    def runner(args, **kwargs):
        calls.append(args)
        code, out = outputs.pop(0) if outputs else (0, "[]")
        return SimpleNamespace(returncode=code, stdout=out if code == 0 else "", stderr=out if code else "")
    return runner


def test_secondary_limit_backs_off_and_stops_sending():
    calls = []
    runner = gh(calls, [(1, SECONDARY), (1, SECONDARY)])
    with pytest.raises(github.RateLimited) as first:
        github.run(["gh", "api", "x"], "rest", runner=runner)
    assert 55 < first.value.until - time.time() <= 60
    with pytest.raises(github.RateLimited):  # 暂停期间不再发请求
        github.run(["gh", "api", "x"], "rest", runner=runner)
    assert len(calls) == 1
    github._state["until"] = 0.0  # 暂停结束后再撞一次，等待翻倍
    with pytest.raises(github.RateLimited) as second:
        github.run(["gh", "api", "x"], "rest", runner=runner)
    assert 115 < second.value.until - time.time() <= 120


def test_retry_after_is_respected():
    until = github.strike("HTTP 429 Retry-After: 30", now=1000)
    assert until == 1030


def test_cache_keeps_old_value_while_limited_and_says_so(monkeypatch):
    loads = []
    def loader():
        loads.append(1)
        if len(loads) > 1:
            raise github.RateLimited(time.time() + 300, SECONDARY)
        return ["旧数据"]
    first = github.cached(("issues",), loader, 300)
    assert first["value"] == ["旧数据"] and not first["error"]
    limited = github.cached(("issues",), loader, 300, force=True)
    assert limited["value"] == ["旧数据"]
    note = github.describe(limited)
    assert note["limited"] and "显示的是" in note["message"] and "分钟后自动重试" in note["message"]
    github._state["until"] = time.time() + 300
    stale = github.cached(("issues",), loader, 0)  # 过期但被限流：不发请求，给旧值
    assert stale["value"] == ["旧数据"] and len(loads) == 2


def test_one_refresh_per_key_at_a_time():
    started, release, loads = threading.Event(), threading.Event(), []
    def loader():
        loads.append(1)
        started.set()
        release.wait(5)
        return len(loads)
    results = []
    threads = [threading.Thread(target=lambda: results.append(github.cached(("k",), loader, 300)["value"])) for _ in range(4)]
    threads[0].start(); started.wait(5)
    for t in threads[1:]:
        t.start()
    time.sleep(0.1); release.set()
    for t in threads:
        t.join(5)
    assert loads == [1] and results == [1, 1, 1, 1]


def test_stale_value_refreshes_in_background():
    values = iter(["一", "二"])
    github.cached(("bg",), lambda: next(values), 300)
    result = github.cached(("bg",), lambda: next(values), 0)
    assert result["value"] == "一"
    for _ in range(50):
        if github.cached(("bg",), lambda: "不该再读", 300)["value"] == "二":
            break
        time.sleep(0.02)
    assert github.cached(("bg",), lambda: "不该再读", 300)["value"] == "二"


def test_requests_are_counted_per_minute():
    out = io.StringIO()
    github.count("graphql", now=60 * 10, out=out)
    github.count("graphql", now=60 * 10 + 5, out=out)
    github.count("search", now=60 * 10 + 6, out=out)
    github.count("rest", now=60 * 11, out=out)
    assert "共 3（graphql 2、search 1）" in out.getvalue()


def test_concurrent_failures_in_one_limit_window_count_once():
    barrier = threading.Barrier(3)
    def runner(args, **kwargs):
        barrier.wait(5)  # 三个请求都已发出，再一起撞上限流
        return SimpleNamespace(returncode=1, stdout="", stderr=SECONDARY)
    errors = []
    def call():
        try:
            github.run(["gh", "api", "x"], "rest", runner=runner)
        except github.RateLimited as exc:
            errors.append(exc.until)
    threads = [threading.Thread(target=call) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(errors) == 3 and github._state["strikes"] == 1
    assert 55 < github._state["until"] - time.time() <= 60  # 没有翻倍成 240 秒


def test_request_waiting_for_a_slot_rechecks_the_limit(monkeypatch):
    monkeypatch.setattr(github, "_slots", threading.BoundedSemaphore(1))
    inside, release, calls = threading.Event(), threading.Event(), []
    def runner(args, **kwargs):
        calls.append(args)
        inside.set()
        release.wait(5)
        return SimpleNamespace(returncode=1, stdout="", stderr=SECONDARY)
    outcomes = []
    def call():
        try:
            github.run(["gh", "api", "x"], "rest", runner=runner)
        except github.RateLimited:
            outcomes.append("limited")
    first = threading.Thread(target=call); first.start(); inside.wait(5)
    second = threading.Thread(target=call); second.start()  # 在槽位外等
    time.sleep(0.1); release.set()
    first.join(5); second.join(5)
    assert outcomes == ["limited", "limited"] and len(calls) == 1  # 第二个拿到槽位后没再发请求


def test_success_returning_during_a_pause_does_not_lift_it():
    github.strike(SECONDARY)
    github.succeeded()
    assert github.blocked_until() and github._state["strikes"] == 1
    github._state["until"] = 0.0
    github.succeeded()
    assert github._state["strikes"] == 0


def test_graphql_partial_accepts_not_found_but_not_other_errors(monkeypatch):
    import json
    body = {"data": {"r0": None, "r1": {"s1": {"state": "OPEN"}}},
            "errors": [{"type": "NOT_FOUND", "path": ["r0"], "message": "Could not resolve to a Repository"}]}
    monkeypatch.setattr(github.subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=1, stdout=json.dumps(body), stderr="gh: Could not resolve"))
    assert github.graphql("query { x }", partial=True) == body["data"]
    with pytest.raises(RuntimeError):
        github.graphql("query { x }")
    limited = {"data": {"r0": None}, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
    monkeypatch.setattr(github.subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=1, stdout=json.dumps(limited), stderr="gh: API rate limit exceeded"))
    with pytest.raises(github.RateLimited):
        github.graphql("query { x }", partial=True)


def test_expire_keeps_the_read_time_and_refreshes_in_background():
    reads = []
    github.cached(("prs-open", "o/r"), lambda: reads.append(1) or ["old"], 300)
    github.cached(("prs-open", "other/x"), lambda: ["other"], 300)
    before = github._slot(("prs-open", "o/r"))["at"]
    github.expire("prs-open", "o/r")
    assert not github._slot(("prs-open", "other/x"))["expired"]  # 只让这个仓库过期
    result = github.cached(("prs-open", "o/r"), lambda: reads.append(2) or ["new"], 300)
    assert result["value"] == ["old"] and result["at"] == before and result["refreshing"]  # 先给旧值，读取时间不清零
    deadline = time.time() + 2
    while github._slot(("prs-open", "o/r"))["lock"].locked() and time.time() < deadline:
        time.sleep(0.01)
    assert github.cached(("prs-open", "o/r"), lambda: ["again"], 300)["value"] == ["new"] and reads == [1, 2]


EOF_ERR = 'Post "https://api.github.com/graphql": EOF'


def test_transient_network_drop_is_retried_then_succeeds():
    calls, waits = [], []
    runner = gh(calls, [(1, EOF_ERR), (1, "read: connection reset by peer"), (0, '{"ok": 1}')])
    out = github.run(["gh", "api", "graphql"], "graphql", runner=runner, sleep=waits.append)
    assert out == '{"ok": 1}' and len(calls) == 3 and waits == list(github.RETRY_DELAYS)


def test_transient_drop_gives_up_after_retries():
    calls, waits = [], []
    runner = gh(calls, [(1, EOF_ERR)] * 5)
    with pytest.raises(RuntimeError, match="EOF"):
        github.run(["gh", "api", "graphql"], "graphql", runner=runner, sleep=waits.append)
    assert len(calls) == len(github.RETRY_DELAYS) + 1


def test_writes_and_real_errors_are_not_retried():
    calls, waits = [], []
    runner = gh(calls, [(1, EOF_ERR)])
    with pytest.raises(RuntimeError):
        github.run(["gh", "issue", "edit", "1", "--add-label", "agent"], "rest:label", runner=runner, sleep=waits.append)
    runner = gh(calls, [(1, "HTTP 404: Not Found")])
    with pytest.raises(RuntimeError):
        github.run(["gh", "api", "x"], "rest", runner=runner, sleep=waits.append)
    assert len(calls) == 2 and waits == []
    assert not github.transient("HTTP 403: API rate limit exceeded EOF")
