"""监看闸门：工作台每 INTERVAL 秒用一个 GraphQL 请求看一眼监看要读的 GitHub 状态，有变化才触发一次监看。

以前中台每 10 分钟跑一轮监看（personal/agent_watch.py），两周 1,679 轮里没有一轮派出过新东西（派出的都来自工作台
「交给 Agent」触发的「立即看 agent issue」），每轮还要发 6 个 GitHub 请求。现在：

- 读法：监看要的 GitHub 状态（带 agent 标签的 open issue 和它们的评论、本人 agent/issue-<编号> 分支开着的 PR
  和近一周合并的 PR）合成一个 GraphQL 请求（三个 search），结果存进中台目录的 github-snapshot.json（SNAPSHOT）。
  监看读 GitHub 时先用这份快照，FRESH 内的直接用，过期才自己读一次并写回，同一份状态不再读两遍。
- 闸门（工作台后台线程，keep_watching）：每 INTERVAL 秒读一次快照，和监看上次处理过的状态（gate.json 的 seen，
  由监看每轮结束时写）比；不一样、到了监看要求的复查时间（wake_at：CI 没登记的提交放满 SETTLE、WAIT 隔一小时重审、
  「处理 issue」还在跑、卡住提醒），或交给记录里有交出超过 GRACE 还没报给中台的事件（emit_check 没触发成），
  就发一次 issues.check（id 是 gate:<时刻>），规则「立即看 agent issue」马上跑一轮监看。没有变化不发，中台不记空轮。
- 兜底：中台的定时规则「看带 agent 标签的 issue」改成每小时一次，工作台没开或闸门出错时照样会看。
- 触发过、监看还没跑完的，RETRIGGER 内不重复触发；同一批没报的交给记录只触发一次。

gate.json 由工作台（闸门）和监看（中台跑的进程）一起写，读-改-写都加文件锁（agent_watch.locked）。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import timedelta

from . import agent_watch, github

SNAPSHOT = "github-snapshot.json"
STATE = "gate.json"
INTERVAL = 300  # 闸门每 5 分钟看一次（以前定时监看是 10 分钟）
START_DELAY = 20  # 工作台启动后先让页面的首批请求走完
FRESH = timedelta(minutes=4)  # 监看用快照的时效：比闸门间隔短，监看不会用到上一次闸门之前的状态
RETRIGGER = timedelta(minutes=30)  # 触发过、监看还没跑完（排队、失败）时，隔这么久才再触发
GRACE = timedelta(minutes=5)  # 交给记录交出这么久还没报给中台，才当成 emit_check 没触发成（加上闸门间隔，最晚 10 分钟补上）
KIND = "graphql:agent-watch"
COMMENTS = 100  # 带标签 issue 随快照读的最近评论条数；超过的监看照旧单独读全
SKIP_ENV = "WORKBENCH_WATCH_GATE"  # 设成 0 不开闸门（临时实例、测试）

ISSUE_NODE = (f"number title url body repository {{ nameWithOwner }} "
              f"comments(last: {COMMENTS}) {{ totalCount nodes {{ author {{ login }} createdAt body }} }}")
_used = {}  # 这一轮监看用到的快照部分（issues / prs → 数据），watch() 开始时清空、结束时写成 seen


def now():
    return agent_watch.now()


def path(name):
    return agent_watch.folder() / name


def stamp(at):
    return at.isoformat(timespec="seconds")


# ---------- 读 GitHub：一个请求，三个 search ----------

def issue_search(repos):
    return "is:issue is:open label:" + agent_watch.LABEL + " " + " ".join("repo:" + r for r in repos)


def pr_searches(at):
    since = (at - agent_watch.LEDGER_KEEP).date().isoformat()  # 合并的只用来核对派出记录，记录留 LEDGER_KEEP
    return "is:pr is:open author:@me head:agent", f"is:pr is:merged author:@me head:agent updated:>={since}"


def query(issues, prs):
    """issues / prs 为真才读那一部分。search 的结果按 GitHub 当前状态给，只有「搜得到」受索引延迟影响。"""
    head = []
    fields = ["viewer { login }"]
    if issues:
        head.append("$issues: String!")
        fields.append(f"issues: search(query: $issues, type: ISSUE, first: 100) {{ nodes {{ ... on Issue {{ {ISSUE_NODE} }} }} }}")
    if prs:
        head += ["$open: String!", "$merged: String!"]
        fields.append("open: search(query: $open, type: ISSUE, first: 100) { nodes { ... on PullRequest { "
                      f"repository {{ nameWithOwner }} {agent_watch.PR_FIELDS} }} }} }}")
        fields.append("merged: search(query: $merged, type: ISSUE, first: 100) { nodes { ... on PullRequest { "
                      f"repository {{ nameWithOwner }} {agent_watch.MERGED_FIELDS} }} }} }}")
    return f"query({', '.join(head)}) {{ {' '.join(fields)} }}" if head else ""


def issue_rows(nodes, repos):
    """search 节点 → 监看用的 issue（和原来 gh search issues 的 JSON 同形），多带 comments（agent_watch.issue_comments 的形状）。
    评论超过 COMMENTS 条的不带，监看照旧单独读全。"""
    wanted = {r.lower() for r in repos}
    rows = []
    for node in nodes or []:
        repo = ((node or {}).get("repository") or {}).get("nameWithOwner") or ""
        if repo.lower() not in wanted:
            continue
        row = {"repository": {"nameWithOwner": repo}, "number": node["number"], "title": node.get("title") or "",
               "url": node.get("url") or "", "body": node.get("body") or ""}
        thread = node.get("comments") or {}
        nodes_ = thread.get("nodes") or []
        if (thread.get("totalCount") or 0) <= len(nodes_):
            row["comments"] = [{"author": (c.get("author") or {}).get("login") or "?", "at": (c.get("createdAt") or "")[:10],
                                "time": c.get("createdAt") or "", "body": (c.get("body") or "").strip()} for c in nodes_ if c]
        rows.append(row)
    return rows


def pr_rows(data, repos):
    """search 节点 → agent_watch.agent_prs 的形状 {viewer, open, merged}：只留 repos 里 agent/issue-<编号> 分支的。"""
    wanted = {r.lower() for r in repos}
    found = {"viewer": (data.get("viewer") or {}).get("login") or "", "open": [], "merged": []}
    for state in ("open", "merged"):
        for node in (data.get(state) or {}).get("nodes") or []:
            repo = ((node or {}).get("repository") or {}).get("nameWithOwner") or ""
            if repo.lower() in wanted and agent_watch.BRANCH.fullmatch(node.get("headRefName") or ""):
                found[state].append({**{k: v for k, v in node.items() if k != "repository"}, "repo": repo})
    return found


def fetch(issue_repos=None, review_repos=None, *, at=None):
    """读 GitHub（一个请求）：{"issues": [...], "prs": {...}}，只有给了仓库的那部分。都没给就不发请求。"""
    at = at or now()
    text = query(bool(issue_repos), bool(review_repos))
    if not text:
        return {}
    variables = {}
    if issue_repos:
        variables["issues"] = issue_search(issue_repos)
    if review_repos:
        variables["open"], variables["merged"] = pr_searches(at)
    data = github.graphql(text, kind=KIND, **variables)
    out = {}
    if issue_repos:
        out["issues"] = issue_rows((data.get("issues") or {}).get("nodes"), issue_repos)
    if review_repos:
        out["prs"] = pr_rows(data, review_repos)
    return out


# ---------- 快照：闸门读的，监看接着用 ----------

def load_snapshot():
    data = agent_watch.read_json(path(SNAPSHOT), {})
    return data if isinstance(data, dict) else {}


def save(parts, repos, at=None):
    """把这次读到的部分写进快照：parts 是 {"issues"/"prs": 数据}，repos 是 {部分: 仓库列表}。"""
    at = at or now()
    with agent_watch.locked(path(SNAPSHOT), lambda p: load_snapshot()) as snap:
        for name, value in parts.items():
            snap[name] = {"at": stamp(at), "repos": sorted(repos[name]), "data": value}


def stored(name, repos, at=None):
    """快照里 name 部分：仓库一样、在 FRESH 内就给数据，否则 None。"""
    part = load_snapshot().get(name)
    if not isinstance(part, dict) or part.get("repos") != sorted(repos):
        return None
    try:
        age = (at or now()) - agent_watch.parse_time(part.get("at"))
    except (TypeError, ValueError):
        return None
    return part.get("data") if timedelta(0) <= age <= FRESH else None


def shared(name, repos):
    """监看读 GitHub 的入口：快照够新就用，不然读一次（只读这一部分）并写回快照。记下这一轮用到的数据。"""
    value = stored(name, repos)
    if value is None:
        found = fetch(repos if name == "issues" else None, repos if name == "prs" else None)
        value = found[name]
        save({name: value}, {name: repos})
    _used[name] = value
    return value


def issues(repos):
    return shared("issues", sorted(repos)) if repos else []


def prs(repos):
    return shared("prs", sorted(repos)) if repos else {"viewer": "", "open": [], "merged": []}


# ---------- 指纹：监看会因为什么派出东西 ----------

def digest(value):
    return hashlib.sha1(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def issues_print(rows):
    """带标签的 open issue 有哪些。正文、评论变了监看不会多派（事件 id 不变），不算。"""
    return digest(sorted(f"{r['repository']['nameWithOwner']}#{r['number']}".lower() for r in rows or []))


def prs_print(found):
    """agent PR 的 head、草稿、CI 结论、本人的审查评论，合并的谁合并、何时合并：review_pass 只看这些。"""
    found = found or {}
    viewer = found.get("viewer") or ""

    def reviews(pr):
        return [[r["sha"], r["verdict"], r["at"].isoformat()] for r in agent_watch.reviews_of(pr, viewer)]
    open_ = sorted([f"{pr['repo']}#{pr['number']}".lower(), pr.get("headRefName"), pr.get("headRefOid"), bool(pr.get("isDraft")),
                    (agent_watch.head_of(pr).get("statusCheckRollup") or {}).get("state"), reviews(pr)]
                   for pr in found.get("open") or [])
    merged = sorted([f"{pr['repo']}#{pr['number']}".lower(), pr.get("headRefOid"), pr.get("mergedAt"),
                     (pr.get("mergedBy") or {}).get("login"), reviews(pr)] for pr in found.get("merged") or [])
    return digest([viewer.lower(), open_, merged])


PRINTS = {"issues": issues_print, "prs": prs_print}


def prints(parts):
    return {name: PRINTS[name](value) for name, value in parts.items()}


# ---------- 交给记录里还没报的事件（不读 GitHub） ----------

LEDGERS = (("handoffs.json", ""), ("upstream-handoffs.json", "upstream:"), ("pr-handoffs.json", "pr:"),
           ("issue-feedback-handoffs.json", "issue-feedback:"), ("learning-handoffs.json", "learning:"),
           ("review-handoffs.json", "recap:"))


def unreported(at=None):
    """一天内交给、交出超过 GRACE、还没报给中台（不在 emitted.json，也不是 repeats.json 里不派的）的事件 id。
    交给时 emit_check 会马上触发监看，这里只接住它没触发成的。"""
    at = at or now()
    seen = set(agent_watch.read_json(path("emitted.json"), {}) or {}) | set(agent_watch.read_json(path(agent_watch.REPEATS), {}) or {})
    out = set()
    for name, prefix in LEDGERS:
        for key, value in agent_watch.load_handoffs(path(name)).items():
            handed = agent_watch.handed_time(value)
            if not handed:
                continue
            try:
                age = at - agent_watch.parse_time(handed)
                event = prefix + agent_watch.event_id(key, handed)
            except (TypeError, ValueError):
                continue
            if GRACE <= age <= agent_watch.HANDOFF_FRESH and event not in seen:
                out.add(event)
    return out


# ---------- gate.json：监看看过什么、闸门触发过什么 ----------

def load_state(p=None):
    data = agent_watch.read_json(p or path(STATE), {})
    return data if isinstance(data, dict) else {}


def begin():
    """监看一轮开始：清空这一轮用到的快照部分。"""
    _used.clear()


def mark_seen(wakes=(), at=None):
    """监看一轮跑完：记下这一轮处理过的状态（只记真的读了的部分）和下次该复查的时刻（wakes 里最早的，没有就空）。"""
    at = at or now()
    later = sorted(w for w in wakes if w and w > at)
    with agent_watch.locked(path(STATE), load_state) as state:
        state["seen"] = {**(state.get("seen") if isinstance(state.get("seen"), dict) else {}), **prints(_used)}
        state["seen_at"] = stamp(at)
        state["wake_at"] = stamp(later[0]) if later else ""


def since(state, key, at):
    """state[key] 记的时刻到 at 过了多久；没记或读不懂是 None。"""
    try:
        return at - agent_watch.parse_time(state[key])
    except (KeyError, TypeError, ValueError):
        return None


def reasons(state, current, pending, at):
    """该不该触发：返回原因（空表示不用）。同一个没看过的状态 RETRIGGER 内只触发一次：
    监看跑完了 seen 还对不上（比如中台规则没补齐、这一轮没读 PR），不会每次都触发。"""
    found = []
    seen = state.get("seen") if isinstance(state.get("seen"), dict) else {}
    changed = [name for name, value in current.items() if seen.get(name) != value]
    age = since(state, "triggered_at", at)
    if changed and (state.get("triggered") != current or age is None or age >= RETRIGGER):
        found.append("GitHub 有变化（" + "、".join({"issues": "带标签的 issue", "prs": "agent PR"}[n] for n in changed) + "）")
    wake = state.get("wake_at") or ""
    try:
        if wake and agent_watch.parse_time(wake) <= at:
            found.append("到了复查时间")
    except ValueError:
        pass
    if set(pending) - set(state.get("asked") or []):
        found.append("交给记录还没报给中台")
    return found


def asked_recently(state, at):
    """触发过、监看还没跑完一轮（seen_at 早于触发时间），且没过 RETRIGGER：不重复触发。"""
    asked, seen = since(state, "triggered_at", at), since(state, "seen_at", at)
    return asked is not None and asked < RETRIGGER and (seen is None or seen > asked)


def emit(reason, *, at=None):
    """发 local 来源的 issues.check（规则「立即看 agent issue」接）；id 带时刻，中台不会当成重复。"""
    at = at or now()
    args = ["agent-autoloop", "emit", agent_watch.CHECK, "--id", "gate:" + at.strftime("%Y%m%dT%H%M%S%z"),
            "--summary", ("工作台：" + reason)[:200]]
    return agent_watch.run(args, timeout=30).strip()


def scope(known=None):
    known = agent_watch.projects() if known is None else known
    return (sorted(known), sorted(r for r, p in known.items() if agent_watch.reviewable(r, p)))


def tick(*, known=None, at=None, read=None, trigger=None):
    """闸门看一次：读快照（一个请求）、写回，和监看看过的比，需要就触发。返回 {reasons, triggered, error}。"""
    at = at or now()
    issue_repos, review_repos = scope(known)
    parts = (read or fetch)(issue_repos, review_repos, at=at)
    save(parts, {"issues": issue_repos, "prs": review_repos}, at)
    current = prints(parts)
    pending = unreported(at)
    with agent_watch.locked(path(STATE), load_state) as state:
        why = reasons(state, current, pending, at)
        state["checked_at"] = stamp(at)
        fire = bool(why) and not asked_recently(state, at)
        error = ""
        if fire:
            try:
                (trigger or emit)("；".join(why), at=at)
                state.update(triggered=current, triggered_at=stamp(at), asked=sorted(pending), trigger_error="")
            except (RuntimeError, OSError, ValueError) as exc:  # 中台没开：下次再试
                fire, error = False, str(exc)[:300]
                state["trigger_error"] = error
    return {"reasons": why, "triggered": fire, "error": error}


def status():
    """给巡检状态看：闸门最近一次看和最近一次触发的时间。"""
    state = load_state()
    return {"interval": INTERVAL, "checked_at": state.get("checked_at") or None, "triggered_at": state.get("triggered_at") or None,
            "wake_at": state.get("wake_at") or None, "error": state.get("trigger_error") or ""}


def keep_watching(stop, *, interval=INTERVAL, delay=START_DELAY, once=None):
    """工作台后台线程。整台机器只开一个（gate.lock 非阻塞锁，拿不到就退出）；GitHub 限流中跳过这一次。"""
    lock_path = path("gate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("监看闸门：另一个工作台已经在看，这里不开", file=sys.stderr, flush=True)
            return
        stop.wait(delay)
        while not stop.is_set():
            try:
                if not github.blocked_until():
                    result = (once or tick)()
                    if result.get("triggered"):
                        print(f"监看闸门：{'；'.join(result['reasons'])}，已触发一轮监看", file=sys.stderr, flush=True)
                    elif result.get("error"):
                        print(f"监看闸门：没能触发监看（{result['error']}），下次再试", file=sys.stderr, flush=True)
            except github.RateLimited:
                pass
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:  # 网络断了、gh 出错：下次再试
                print(f"监看闸门：没读到 GitHub（{str(exc)[:200]}），下次再试", file=sys.stderr, flush=True)
            except Exception:  # 闸门出错不影响页面；中台每小时的兜底照样会看
                traceback.print_exc(file=sys.stderr)
            stop.wait(interval)


def start(stop=None):
    if os.environ.get(SKIP_ENV) == "0":
        return None, None
    stop = stop or threading.Event()
    thread = threading.Thread(target=keep_watching, args=(stop,), daemon=True, name="watch-gate")
    thread.start()
    return thread, stop
