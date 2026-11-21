"""工作台对 GitHub 的统一出口：并发上限、限流退避、单飞缓存和每分钟请求计数。

所有 gh 调用都走 call()：同时最多 MAX_CONCURRENT 个；遇到 403/429 限流（含二级限流）就整体暂停，
有 Retry-After 用它，没有就从 60 秒起按次数翻倍（最长 15 分钟），暂停期间不再发请求，直接抛 RateLimited。
cached() 给每个 key 一份带 TTL 的结果：同一 key 同时只有一个请求在刷新，其他调用等它；过期时可以先返回旧值、
后台刷新；刷新失败保留旧值并记下原因。每分钟发了多少请求、打到哪类接口，写到服务的标准错误输出。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

MAX_CONCURRENT = 3
BACKOFF_START = 60
BACKOFF_MAX = 900
LIMIT_WORDS = re.compile(r"rate limit|HTTP 429|abuse detection|secondary rate", re.I)
# 网络瞬断（代理节点切换、线路抖动）：连接在 GitHub 回复前被掐断。只读请求隔几秒重试，不当成读取失败。
TRANSIENT_WORDS = re.compile(r"(?::|\b)EOF\b|connection reset|broken pipe|i/o timeout|TLS handshake timeout|"
                             r"unexpected EOF|connection refused|HTTP 50[234]|server misbehaving", re.I)
RETRY_DELAYS = (2, 5)
WRITE_VERBS = {"edit", "create", "delete", "close", "reopen", "comment", "merge"}

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_state_lock = threading.Lock()
_state = {"until": 0.0, "strikes": 0, "reason": ""}
_counts = {"minute": None, "kinds": {}}


class RateLimited(RuntimeError):
    def __init__(self, until, reason=""):
        super().__init__(f"GitHub 限流中，约 {max(1, round((until - time.time()) / 60))} 分钟后重试")
        self.until = until
        self.reason = reason


def blocked_until():
    with _state_lock:
        return _state["until"] if _state["until"] > time.time() else 0.0


def count(kind, *, now=None, out=None):
    """记一次请求；跨分钟时把上一分钟的统计写到标准错误输出。"""
    minute = time.strftime("%H:%M", time.localtime(now or time.time()))
    with _state_lock:
        if _counts["minute"] != minute:
            if _counts["minute"] and _counts["kinds"]:
                detail = "、".join(f"{k} {n}" for k, n in sorted(_counts["kinds"].items()))
                print(f"GitHub 请求 {_counts['minute']}：共 {sum(_counts['kinds'].values())}（{detail}）",
                      file=out or sys.stderr, flush=True)
            _counts.update(minute=minute, kinds={})
        _counts["kinds"][kind] = _counts["kinds"].get(kind, 0) + 1


def limited(text):
    return bool(LIMIT_WORDS.search(text or "")) or ("HTTP 403" in (text or "") and "limit" in (text or "").lower())


def strike(text, *, now=None):
    """记一次限流，算出暂停到什么时候。已经在暂停中（并发的请求一起撞上同一次限流）只算一次，
    不再翻倍；带 Retry-After 时取更晚的那个。"""
    now = now or time.time()
    found = re.search(r"retry[- ]after:?\s*(\d+)", text or "", re.I)
    with _state_lock:
        if _state["until"] > now:
            if found:
                _state["until"] = max(_state["until"], now + int(found[1]))
            return _state["until"]
        _state["strikes"] += 1
        wait = int(found[1]) if found else min(BACKOFF_MAX, BACKOFF_START * 2 ** (_state["strikes"] - 1))
        _state.update(until=now + wait, reason=(text or "")[:200])
        return _state["until"]


def recover():
    with _state_lock:
        _state.update(strikes=0, until=0.0, reason="")


def succeeded():
    """一次请求成功：暂停已经结束才清零。暂停前发出、暂停中才返回的请求不解除暂停。"""
    with _state_lock:
        if _state["until"] <= time.time():
            _state.update(strikes=0, until=0.0, reason="")


def check():
    """限流中就抛 RateLimited，不发请求。"""
    until = blocked_until()
    if until:
        raise RateLimited(until, _state["reason"])


def transient(text):
    return bool(TRANSIENT_WORDS.search(text or "")) and not limited(text)


def writes(args):
    """gh issue edit、gh label create、gh api --method POST 这类写操作：瞬断时可能已经生效，不自动重试。"""
    if len(args) > 2 and args[0] == "gh" and args[1] == "api":
        for flag in ("--method", "-X"):
            if flag in args[:-1] and args[args.index(flag) + 1].upper() != "GET":
                return True
        return False
    return len(args) > 2 and args[0] == "gh" and args[2] in WRITE_VERBS


def run(args, kind, *, timeout=120, runner=None, sleep=time.sleep):
    """执行一条 gh 命令；限流时抛 RateLimited，其他失败抛 RuntimeError。
    只读请求遇到网络瞬断（见 TRANSIENT_WORDS）按 RETRY_DELAYS 隔几秒重试，重试等待时不占槽位。"""
    delays = () if writes(args) else RETRY_DELAYS
    for attempt in range(len(delays) + 1):
        check()
        with _slots:
            check()  # 等槽位期间别的请求可能撞上了限流，拿到槽位后再看一次
            count(kind)
            result = (runner or subprocess.run)(args, capture_output=True, text=True, timeout=timeout)
        if not result.returncode:
            succeeded()
            return result.stdout
        message = (result.stderr or result.stdout or "").strip()
        if limited(message):
            raise RateLimited(strike(message), message)
        if attempt < len(delays) and transient(message):
            sleep(delays[attempt])
            continue
        raise RuntimeError(message[:500] or "gh 命令失败")


def _not_found_ok(args, **kwargs):
    """gh 遇到 GraphQL 报错就以非零退出，但标准输出里仍有其余字段的数据。错误全是 NOT_FOUND（仓库删了、编号不存在）
    时当作成功，读不到的字段是 null；其他错误（含限流）照常失败。"""
    result = subprocess.run(args, **kwargs)
    if result.returncode:
        try:
            data = json.loads(result.stdout or "")
        except ValueError:
            return result
        errors = data.get("errors") if isinstance(data, dict) else None
        if data.get("data") and errors and all(e.get("type") == "NOT_FOUND" for e in errors):
            result.returncode = 0
    return result


def graphql(query, kind="graphql", *, partial=False, **variables):
    """partial 为真时，部分字段 NOT_FOUND 不算失败，其余字段照常返回。"""
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    out = run(args, kind, runner=_not_found_ok if partial else None)
    data = json.loads(out or "{}")
    if data.get("errors") and not data.get("data"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False)[:500])
    return data.get("data") or {}


_cache_lock = threading.Lock()
_cache = {}


def _slot(key):
    with _cache_lock:
        return _cache.setdefault(key, {"value": None, "at": 0.0, "error": "", "limited_until": 0.0, "expired": False,
                                       "lock": threading.Lock()})


def _refresh(slot, loader):
    try:
        value = loader()
        slot.update(value=value, at=time.time(), error="", limited_until=0.0, expired=False)
    except RateLimited as exc:
        slot.update(error=str(exc), limited_until=exc.until)
        with _state_lock:
            if exc.until > _state["until"]:
                _state.update(until=exc.until, reason=exc.reason[:200])
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        slot.update(error=str(exc)[:500])


def cached(key, loader, ttl, *, force=False, background=True):
    """返回 {value, at, error, limited_until, refreshing}。没有旧值时等这次读取；有旧值且过期时，
    background 为真就后台刷新、先给旧值。同一 key 只有一个刷新在跑。"""
    slot = _slot(key)
    stale = force or slot["expired"] or time.time() - slot["at"] > ttl
    blocked = blocked_until()
    if stale and blocked:  # 限流中：不发请求，给旧值并说明
        slot.update(error="GitHub 限流中", limited_until=blocked)
        return {"value": slot["value"], "at": slot["at"], "error": slot["error"], "limited_until": blocked,
                "refreshing": False}
    if stale and slot["value"] is not None and background and not force:
        if slot["lock"].acquire(blocking=False):
            snapshot = {"value": slot["value"], "at": slot["at"], "error": slot["error"],
                        "limited_until": slot["limited_until"], "refreshing": True}
            def work():
                try:
                    _refresh(slot, loader)
                finally:
                    slot["lock"].release()
            threading.Thread(target=work, daemon=True).start()
            return snapshot
    elif stale:
        with slot["lock"]:  # 别人在刷新就等它，不另发请求
            if force or slot["expired"] or time.time() - slot["at"] > ttl:
                _refresh(slot, loader)
    return {"value": slot["value"], "at": slot["at"], "error": slot["error"], "limited_until": slot["limited_until"],
            "refreshing": slot["lock"].locked()}


def expire(*prefix):
    """让 key 以 prefix 开头的缓存过期，下次读取时刷新（例如交给 Agent 打了标签之后）。
    expire("issues") 让所有 issue 列表过期；expire("prs-open", "o/r") 只让这个仓库的过期。
    过期的照样先给旧值、后台刷新，读取时间不清零。"""
    with _cache_lock:
        for key, slot in _cache.items():
            if key and tuple(key[:len(prefix)]) == prefix:
                slot["expired"] = True  # 保留读取时间，页面照样写「读取于」


def peek(key):
    """缓存里现在的值，不发请求、不刷新；没有就是 None。"""
    with _cache_lock:
        slot = _cache.get(key)
    return slot["value"] if slot else None


def entries(*prefix):
    """key 以 prefix 开头、已经有值的缓存：[(key, value)]，不发请求、不刷新。"""
    with _cache_lock:
        return [(key, slot["value"]) for key, slot in _cache.items()
                if key and tuple(key[:len(prefix)]) == prefix and slot["value"] is not None]


def seed(key, value, at):
    """把保存在磁盘上的旧结果放进缓存（重启后不用重读）；已经有值就不动。"""
    slot = _slot(key)
    with _cache_lock:
        if slot["value"] is None and value is not None:
            slot.update(value=value, at=at)


def describe(result):
    """给页面的一行说明：限流时写清显示的是几点的数据、约几分钟后重试。"""
    until = result.get("limited_until") or 0
    if until > time.time():
        at = datetime.fromtimestamp(result["at"]).strftime("%H:%M") if result.get("at") else "—"
        minutes = max(1, round((until - time.time()) / 60))
        return {"limited": True, "message": f"GitHub 限流中，显示的是 {at} 的数据，约 {minutes} 分钟后自动重试"}
    return {"limited": False, "message": result.get("error") or ""}


def reset():
    """测试用：清空缓存和限流状态。"""
    with _cache_lock:
        _cache.clear()
    recover()
    _counts.update(minute=None, kinds={})
