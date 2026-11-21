"""GitHub 通知当信号：未读的提到你、请你评审、你参与或你发起的主题有新动静，比收件箱缓存里的 issue / PR 数据新时，
单独重读这几条主题（一个 GraphQL 请求，经 personal/github.py），不等下一次整表刷新（用户 2026-11-20 定）。

- signals：已同步的通知里未读、原因在 SIGNALS、主题是 issue 或 PR 的，{主题（小写）: 信号}；同一主题取最新的那条通知。
  信号带上 pr_news 已读好的「谁说了什么」（没读到是空的），收件箱据此在重读完成前先把这一行放进「待你回」。
- stale：通知的更新时间晚于这份数据的读取时刻，就是数据旧了。比的是读取时刻、不是 issue 的 updatedAt：
  通知的更新时间常比触发它的留言晚几秒，比 updatedAt 会把你自己刚回过的那条一直当成新的。
- refresh：后台重读一批主题，只读还没为这次通知读过的；读失败的 RETRY 秒内不重读，限流时不发请求。
  issue 存成列表里的形状（upstream.item，带留言和引用，加 state），给 issues.view 换掉旧行或补上列表里没有的行；
  PR 顺手按通知的更新时间读好时间线（pr_feedback.timelines），收件箱 PR 行下次读时直接用。
- 通知同步（notifications.sync）发现新的或更新了的信号就调 refresh，issues.view 读到旧数据时也调一次（重启后补上）。
只读，不标记已读，不写 GitHub。
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime

from . import github

SIGNALS = {"mention", "team_mention", "review_requested", "comment", "author"}
SAYS = {"mention": "@了你", "team_mention": "@了你的团队", "review_requested": "请你评审", "comment": "有新留言", "author": "有新动静"}
RANK = ["mention", "team_mention", "review_requested", "comment", "author"]  # 同一时刻几条通知时，说法取靠前的
RETRY = 300
CHUNK = 20  # 一个 GraphQL 请求最多读这么多条主题

_lock = threading.Lock()
_store = {}  # 主题（小写）→ {"version": 通知更新时间, "at": 读到节点的时刻（没读到是 0）, "node": 列表形状的 issue / PR 节点 / None,
#               "error": 这次没读成的原因, "tried": 没读成的时刻}
_running = set()


def seconds(at):
    try:
        return datetime.fromisoformat(str(at or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def signals(data):
    """{主题（小写）: {key, repo, number, type, reason, says, updated_at, title, id, who, what, at, text}}。没有数据目录或没同步过是空的。"""
    if data is None:
        return {}
    from . import notifications, pr_news
    try:
        rows = notifications.stored(notifications.metadata(data))
    except (OSError, ValueError):
        return {}
    out = {}
    for _, raw in rows:
        if not raw.get("unread") or raw.get("reason") not in SIGNALS:
            continue
        key = notifications.subject_key(raw)
        if not key:
            continue
        old = out.get(key)
        at = raw.get("updated_at") or ""
        if old and (old["updated_at"], -RANK.index(old["reason"])) >= (at, -RANK.index(raw["reason"])):
            continue
        detail = pr_news.cached(raw) or {}
        repo, _, number = key.rpartition("#")
        out[key] = {"key": key, "repo": repo, "number": int(number), "type": raw["subject"].get("type") or "", "reason": raw["reason"],
                    "says": SAYS[raw["reason"]], "updated_at": at, "title": raw["subject"].get("title") or "", "id": raw.get("id") or "", "who": detail.get("who") or "",
                    "what": detail.get("what") or "", "at": detail.get("at") or "", "text": detail.get("text") or ""}
    return out


def stale(signal, fetched_at):
    """这条通知晚于数据的读取时刻（epoch 秒，0 是没读过）。"""
    return seconds(signal.get("updated_at")) > (fetched_at or 0)


def entry(key):
    """为通知重读过的这条主题：{version, at, node, error}，没读过是 None。"""
    with _lock:
        found = _store.get(key.lower())
        return dict(found) if found else None


def _usable(found, version, now):
    if not found or found.get("version") != version:
        return False
    return not found.get("error") or now - found.get("tried", 0) < RETRY


def read_subjects(keys):
    """读这些主题（'owner/repo#N'，小写）：issue 是列表形状（upstream.item，加 state），PR 是 {type, number, state}。
    几个仓库一组、最多 CHUNK 条一个请求；读不到的是 None。限流抛 github.RateLimited。"""
    from . import upstream
    pairs = sorted((key.rpartition("#")[0], int(key.rpartition("#")[2])) for key in keys)
    out = {}
    for start in range(0, len(pairs), CHUNK):
        repos = {}
        for repo, number in pairs[start:start + CHUNK]:
            repos.setdefault(repo, []).append(number)
        fields = " ".join(
            f'r{i}: repository(owner: {json.dumps(repo.split("/")[0])}, name: {json.dumps(repo.split("/")[1])}) {{ nameWithOwner '
            + " ".join(f"s{n}: issueOrPullRequest(number: {n}) {{ __typename ... on Issue {{ state {upstream.ISSUE} }} "
                       f"... on PullRequest {{ number state updatedAt }} }}" for n in numbers) + " }"
            for i, (repo, numbers) in enumerate(repos.items()))
        data = github.graphql(f"query {{ {fields} }}", kind="graphql:fresh-subjects", partial=True)
        for i, (repo, numbers) in enumerate(repos.items()):
            found = data.get(f"r{i}") or {}
            name = found.get("nameWithOwner") or repo
            for n in numbers:
                node = found.get(f"s{n}")
                if not node:
                    out[f"{repo}#{n}"] = None
                elif node.get("__typename") == "Issue":
                    out[f"{repo}#{n}"] = {**upstream.item(node, name), "state": node.get("state") or ""}
                else:
                    out[f"{repo}#{n}"] = {"type": "PullRequest", "number": node.get("number"), "state": node.get("state") or "",
                                          "updatedAt": node.get("updatedAt") or ""}
    return out


def refresh(wanted, *, read=None, timelines=None, background=True, now=None):
    """后台重读 wanted（{主题（小写）: 通知更新时间}）里还没为这次通知读过的；返回这次要读的主题。
    PR 顺手读好时间线（pr_feedback.timelines，按通知更新时间记），给收件箱的 PR 行用。"""
    now = now or time.time()
    with _lock:
        todo = {k.lower(): v for k, v in wanted.items() if k.lower() not in _running and not _usable(_store.get(k.lower()), v, now)}
        _running.update(todo)
    if not todo:
        return []

    def work():
        try:
            github.check()
            found = (read or read_subjects)(list(todo))
            at = time.time()
            with _lock:
                for key, version in todo.items():
                    _store[key] = {"version": version, "at": at, "node": found.get(key), "error": ""}
            prs = {k: todo[k] for k, v in found.items() if v and v.get("type") == "PullRequest"}
            if prs:
                from . import pr_feedback
                (timelines or pr_feedback.timelines)(prs)
        except (github.RateLimited, RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            at = time.time()
            with _lock:
                for key, version in todo.items():
                    old = _store.get(key) or {}
                    _store[key] = {"version": version, "at": old.get("at", 0), "node": old.get("node"),
                                   "error": str(exc)[:200] or "读取失败", "tried": at}
        finally:
            with _lock:
                _running.difference_update(todo)

    if background:
        threading.Thread(target=work, daemon=True).start()
    else:
        work()
    return sorted(todo)


def running(key):
    with _lock:
        return key.lower() in _running


def reset():
    """测试用。"""
    with _lock:
        _store.clear()
        _running.clear()
