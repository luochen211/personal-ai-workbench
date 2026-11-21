"""项目详情页的「我的 PR」和「近 30 天已完成的 issue」：本人在项目 GitHub 仓库里的 PR 分成开着、近 30 天已合并、
近 30 天已关闭未合并三组；近 30 天关闭、本人创建或被本人 PR 关闭的 issue 带上关闭原因。

都经 personal.github 出口（并发上限、限流退避、单飞缓存）。每个仓库两份缓存：
- 开着的 PR：一个 GraphQL 搜索，5 分钟（项目详情页打开时 2 分钟）过期，过期先给旧值、后台刷新；
- 已合并、已关闭的 PR 和已完成的 issue：一个 GraphQL 请求里两个搜索，长期缓存（6 小时，存到 .personal/），
  看到合并或关闭的迹象就提前失效：开着的 PR 从列表里消失、GitHub 消息里主题变成已合并或已关闭、issue 列表里少了一条。
限流时不发请求，给旧值并写明。
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import github
from .upstream import LOGIN

OPEN_TTL = 300
FRESH_AGE = 120  # 项目详情页打开时，开着的 PR 超过 2 分钟就后台重读
CLOSED_TTL = 6 * 3600  # 已合并、已关闭的不会再变，只有出现新的合并或关闭才要重读；兜底 6 小时
WINDOW_DAYS = 30
STORE = "project-activity.json"
REPO = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
REASONS = {"COMPLETED": "已完成", "NOT_PLANNED": "不做", "DUPLICATE": "重复", "REOPENED": "已完成"}

PR = ("number title url state isDraft reviewDecision createdAt updatedAt mergedAt closedAt author { login } "
      "commits(last: 1) { nodes { commit { statusCheckRollup { state } checkSuites(last: 10) { nodes { updatedAt conclusion } } } } }")
CLOSING = ("closingIssuesReferences(first: 10) { nodes { number title url state stateReason createdAt closedAt "
           "author { login } } }")
ISSUE = ("number title url state stateReason createdAt closedAt author { login } "
         "timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { ... on ClosedEvent { createdAt actor { login } "
         "closer { __typename ... on PullRequest { number url title author { login } } "
         "... on Commit { abbreviatedOid url } } } } }")

_store_lock = threading.Lock()
_seeded = set()
_seen_open = {}  # 仓库（小写）→ 上次看到的开着的 PR 编号


def since_date(now=None):
    return ((now or datetime.now().astimezone()) - timedelta(days=WINDOW_DAYS)).date().isoformat()


def _search(alias, query, fields):
    return (f'{alias}: search(query: {json.dumps(query)}, type: ISSUE, first: 100) {{ '
            f'nodes {{ __typename ... on {fields} }} }}')


def _pr(node):
    commits = (node.get("commits") or {}).get("nodes") or []
    commit = ((commits[0] or {}).get("commit") or {}) if commits else {}
    rollup = commit.get("statusCheckRollup")
    suites = [s.get("updatedAt") for s in ((commit.get("checkSuites") or {}).get("nodes") or [])
              if s and s.get("conclusion") and s.get("updatedAt")]
    state = "merged" if node.get("state") == "MERGED" or node.get("mergedAt") else \
        "closed" if node.get("state") == "CLOSED" else "open"
    return {"number": node["number"], "title": node["title"], "url": node["url"], "state": state,
            "draft": bool(node.get("isDraft")), "review": node.get("reviewDecision") or "",
            "checks": (rollup or {}).get("state") or "", "checks_at": max(suites) if suites else None, "created_at": node.get("createdAt"),
            "updated_at": node.get("updatedAt"), "merged_at": node.get("mergedAt"), "closed_at": node.get("closedAt"),
            "closes": [{"number": i["number"], "title": i["title"], "url": i["url"], "state": i.get("state"),
                        "state_reason": i.get("stateReason") or "", "created_at": i.get("createdAt"),
                        "closed_at": i.get("closedAt"), "author": (i.get("author") or {}).get("login", "")}
                       for i in ((node.get("closingIssuesReferences") or {}).get("nodes") or []) if i]}


def _issue(node):
    events = (node.get("timelineItems") or {}).get("nodes") or []
    event = events[-1] if events else {}
    closer = (event or {}).get("closer") or {}
    return {"number": node["number"], "title": node["title"], "url": node["url"],
            "state_reason": node.get("stateReason") or "", "created_at": node.get("createdAt"),
            "closed_at": node.get("closedAt"), "author": (node.get("author") or {}).get("login", ""),
            "actor": ((event or {}).get("actor") or {}).get("login", ""),
            "closer": {"kind": "pr", "number": closer["number"], "url": closer["url"], "title": closer.get("title", ""),
                       "author": (closer.get("author") or {}).get("login", "")} if closer.get("__typename") == "PullRequest"
            else {"kind": "commit", "oid": closer.get("abbreviatedOid", ""), "url": closer.get("url", "")}
            if closer.get("__typename") == "Commit" else None}


def _owner_name(repo):
    found = REPO.fullmatch(repo or "")
    if not found:
        raise ValueError("仓库名无效")
    return found[0]


def read_open(repo, login=LOGIN):
    """本人在这个仓库开着的 PR：一个 GraphQL 搜索。"""
    repo = _owner_name(repo)
    data = github.graphql("query { " + _search("open", f"repo:{repo} is:pr is:open author:{login}", f"PullRequest {{ {PR} }}")
                          + " }", kind="graphql:project-prs")
    return [_pr(n) for n in (data.get("open") or {}).get("nodes", []) if n and n.get("__typename") == "PullRequest"]


def read_closed(repo, since, login=LOGIN):
    """近 30 天本人已合并或已关闭的 PR（带它们关闭的 issue），和近 30 天关闭的本人 issue（带关闭事件）：
    一个 GraphQL 请求里两个搜索。"""
    repo = _owner_name(repo)
    data = github.graphql("query { "
                          + _search("prs", f"repo:{repo} is:pr is:closed author:{login} closed:>={since}", f"PullRequest {{ {PR} {CLOSING} }}")
                          + " "
                          + _search("issues", f"repo:{repo} is:issue is:closed author:{login} closed:>={since}", f"Issue {{ {ISSUE} }}")
                          + " }", kind="graphql:project-closed")
    return {"since": since,
            "prs": [_pr(n) for n in (data.get("prs") or {}).get("nodes", []) if n and n.get("__typename") == "PullRequest"],
            "issues": [_issue(n) for n in (data.get("issues") or {}).get("nodes", []) if n and n.get("__typename") == "Issue"]}


def done_issues(closed, since):
    """近 30 天关闭、本人创建或被本人 PR 关闭的 issue，新关闭的在前。关闭原因：被哪个 PR 关闭；GitHub 记成提交关闭的，
    找本人 PR 里写了关闭它的那个；都没有就写谁手动关的。"""
    closing = {}
    for pr in closed.get("prs", []):
        if pr["state"] == "merged":
            for issue in pr["closes"]:
                closing.setdefault(issue["number"], pr)
    out = {}
    for issue in closed.get("issues", []):
        if (issue.get("closed_at") or "") < since:
            continue
        pr = closing.get(issue["number"])
        closer = issue.get("closer")
        if closer and closer["kind"] == "pr":
            by = {"kind": "pr", "number": closer["number"], "url": closer["url"], "title": closer["title"]}
        elif pr:
            by = {"kind": "pr", "number": pr["number"], "url": pr["url"], "title": pr["title"],
                  **({"commit": closer["oid"]} if closer else {})}
        elif closer:
            by = {"kind": "commit", "oid": closer["oid"], "url": closer["url"]}
        else:
            by = {"kind": "manual", "actor": issue.get("actor") or ""}
        out[issue["number"]] = {**{k: issue[k] for k in ("number", "title", "url", "author", "created_at", "closed_at")},
                                "reason": REASONS.get(issue["state_reason"], "已关闭"), "by": by}
    for number, pr in closing.items():  # 别人创建、被本人 PR 关闭的
        issue = next(i for i in pr["closes"] if i["number"] == number)
        if number in out or issue.get("state") != "CLOSED" or (issue.get("closed_at") or "") < since:
            continue
        out[number] = {**{k: issue[k] for k in ("number", "title", "url", "author", "created_at", "closed_at")},
                       "reason": REASONS.get(issue["state_reason"], "已关闭"),
                       "by": {"kind": "pr", "number": pr["number"], "url": pr["url"], "title": pr["title"]}}
    return sorted(out.values(), key=lambda i: i["closed_at"] or "", reverse=True)


def pending(pr):
    """开着、不是草稿、还没批准也没被要求修改：等人审。"""
    return pr["state"] == "open" and not pr["draft"] and pr["review"] not in {"APPROVED", "CHANGES_REQUESTED"}


def _load(data):
    try:
        return json.loads((Path(data) / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data, repo, value):
    if data is None:
        return
    with _store_lock:
        saved = _load(data)
        saved[repo] = {"at": time.time(), "value": value}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, prefix="project-activity-",
                                         suffix=".tmp", delete=False) as stream:
            json.dump(saved, stream, ensure_ascii=False)
            temporary = stream.name
        Path(temporary).replace(Path(data) / STORE)


def _seed(data, repo):
    """重启后先用保存的已合并、已关闭结果，不重读。"""
    if data is None or (str(data), repo) in _seeded:
        return
    _seeded.add((str(data), repo))
    entry = _load(data).get(repo)
    if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
        github.seed(("prs-closed", repo), entry["value"], float(entry.get("at") or 0))


def invalidate(repo):
    """这个仓库有 PR 合并或 issue 关闭：PR 和 issue 的缓存都过期，下次请求重读。"""
    key = repo.lower()
    github.expire("prs-open", key)
    github.expire("prs-closed", key)
    github.expire("issues")
    github.expire("involved")
    github.expire(*ALL_KEY)


def expire_closed(repo):
    github.expire("prs-closed", repo.lower())
    github.expire(*ALL_KEY)


def _stamp(at):
    return datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds") if at else None


def view(project, data=None, *, max_age=None, force=False, now=None):
    repo = project.get("github") or ""
    if not REPO.fullmatch(repo):
        return {"repo": "", "available": False}
    key = repo.lower()
    since = since_date(now)
    ttl = min(OPEN_TTL, max_age) if max_age else OPEN_TTL
    opened = github.cached(("prs-open", key), lambda: read_open(repo), ttl, force=force)
    _seed(data, key)

    def load_closed():
        value = read_closed(repo, since)
        try:
            _save(data, key, value)
        except OSError:
            pass
        return value

    current = {pr["number"] for pr in opened["value"] or []}
    before = _seen_open.get(key)
    if opened["value"] is not None:
        _seen_open[key] = current
    known = github.peek(("prs-closed", key)) or {}
    closed_numbers = {pr["number"] for pr in known.get("prs", [])}
    gone = (before or set()) - current
    if gone - closed_numbers:  # 开着的 PR 不见了、还没出现在已合并或已关闭里：重读，issue 也可能跟着关了
        expire_closed(repo)
        github.expire("issues")
        github.expire("involved")
    closed = github.cached(("prs-closed", key), load_closed, CLOSED_TTL, force=force)
    value = closed["value"] or {"prs": [], "issues": []}
    open_prs = sorted(opened["value"] or [], key=lambda p: p["number"], reverse=True)
    merged = sorted((p for p in value["prs"] if p["state"] == "merged" and (p["merged_at"] or "") >= since),
                    key=lambda p: p["merged_at"] or "", reverse=True)
    dropped = sorted((p for p in value["prs"] if p["state"] == "closed" and (p["closed_at"] or "") >= since),
                     key=lambda p: p["closed_at"] or "", reverse=True)
    open_numbers = {p["number"] for p in open_prs}
    merged = [p for p in merged if p["number"] not in open_numbers]  # 重新打开的以开着为准
    dropped = [p for p in dropped if p["number"] not in open_numbers]
    notes = [github.describe(opened), github.describe(closed)]
    limited = next((n["message"] for n in notes if n["limited"]), "")
    if limited and not (opened["at"] or closed["at"]):
        until = github.blocked_until()
        limited = f"GitHub 限流中，PR 还没读到，约 {max(1, round((until - time.time()) / 60)) if until else 1} 分钟后自动重试"
    error = "" if limited else "；".join(dict.fromkeys(n["message"] for n in notes if n["message"]))
    return {"repo": repo, "available": True, "since": since,
            "prs": {"open": open_prs, "merged": merged, "closed": dropped},
            "done_issues": done_issues(value, since),
            "counts": {"pending": sum(pending(p) for p in open_prs), "open": len(open_prs), "merged": len(merged)},
            "loaded": opened["value"] is not None and closed["value"] is not None,
            "fetched_at": _stamp(opened["at"]), "closed_fetched_at": _stamp(closed["at"]),
            "refreshing": opened["refreshing"] or closed["refreshing"], "limited": limited,
            "error": f"PR 读取失败：{error}" if error else ""}


def reset():
    """测试用。"""
    _seeded.clear()
    _seen_open.clear()


# ---- Issue 列表页的「已完成」：全部项目近 30 天已完成的 issue ----
# 不按项目逐个查（40 多个项目各一次太重），用两个跨仓库搜索：本人创建、近 30 天关闭的 issue（带关闭事件），
# 和本人近 30 天合并的 PR（带它们关闭的 issue），再按仓库归到项目，每个仓库照项目详情页的 done_issues 算。
# 第一次（和每 6 小时）整读 30 天，之后每 10 分钟只读上次读取以来新关闭、新合并的，并进保存的结果；
# 已读到的各项目详情页缓存（含 .personal/project-activity.json）一起并进来，同一条 issue 只留一条。
ALL_TTL = 600
ALL_FULL = CLOSED_TTL
ALL_MARGIN = 600  # 增量读往前多读 10 分钟，接住 GitHub 搜索索引的延迟
ALL_STORE = "done-issues.json"
ALL_KEY = ("done-all",)
MAX_PAGES = 10
ALL_PR = "number title url state mergedAt closedAt repository { nameWithOwner } " + CLOSING
ALL_ISSUE = "repository { nameWithOwner } " + ISSUE


def _paged(query, fields, kind):
    text = ("query($q: String!, $after: String) { search(query: $q, type: ISSUE, first: 100, after: $after) { "
            f"pageInfo {{ hasNextPage endCursor }} nodes {{ __typename ... on {fields} }} }} }}")
    nodes, after = [], None
    for _ in range(MAX_PAGES):
        data = github.graphql(text, kind=kind, q=query, **({"after": after} if after else {}))["search"]
        nodes += [n for n in data["nodes"] if n]
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return nodes


def read_all(start, login=LOGIN):
    """start 起本人创建后关闭的 issue 和本人合并的 PR，按仓库（小写）分组；start 是日期或 UTC 时刻。"""
    repos = {}

    def slot(node):
        name = (node.get("repository") or {}).get("nameWithOwner") or ""
        return repos.setdefault(name.lower(), {"name": name, "prs": [], "issues": []}) if name else None

    for node in _paged(f"is:issue is:closed author:{login} closed:>={start}", f"Issue {{ {ALL_ISSUE} }}", "graphql:done-issues"):
        if node.get("__typename") == "Issue" and (bucket := slot(node)):
            bucket["issues"].append(_issue(node))
    for node in _paged(f"is:pr is:merged author:{login} merged:>={start}", f"PullRequest {{ {ALL_PR} }}", "graphql:done-prs"):
        if node.get("__typename") == "PullRequest" and (bucket := slot(node)):
            bucket["prs"].append(_pr(node))
    return repos


def _union(*values):
    """几份 {prs, issues} 合成一份，同一编号只留一条（后面的覆盖前面的）。"""
    prs, found = {}, {}
    for value in values:
        for pr in (value or {}).get("prs", []):
            prs[pr["number"]] = pr
        for issue in (value or {}).get("issues", []):
            found[issue["number"]] = issue
    return {"prs": list(prs.values()), "issues": list(found.values())}


def merge_all(old, new, since):
    """把增量读到的并进旧结果，去掉关闭或合并早于 since 的。"""
    repos = {}
    for key in set(old) | set(new):
        value = _union(old.get(key), new.get(key))
        value["prs"] = [p for p in value["prs"] if (p.get("merged_at") or p.get("closed_at") or "") >= since]
        value["issues"] = [i for i in value["issues"] if (i.get("closed_at") or "") >= since]
        if value["prs"] or value["issues"]:
            repos[key] = {"name": (new.get(key) or old.get(key) or {}).get("name", key), **value}
    return repos


def _utc(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_all(since, *, now=None, read=None):
    """给 github.cached 用：有 6 小时内整读过的结果就只读上次以来的增量，否则整读 since 起的 30 天。"""
    now = now or time.time()
    old = github.peek(ALL_KEY)
    if old and old.get("since", "") <= since and now - old.get("full_at", 0) < ALL_FULL and old.get("at"):
        fresh = (read or read_all)(_utc(old["at"] - ALL_MARGIN))
        return {"since": since, "full_at": old["full_at"], "at": now, "repos": merge_all(old.get("repos", {}), fresh, since)}
    return {"since": since, "full_at": now, "at": now, "repos": merge_all({}, (read or read_all)(since), since)}


def _seed_all(data):
    if data is None or (str(data), ALL_KEY) in _seeded:
        return
    _seeded.add((str(data), ALL_KEY))
    try:
        entry = json.loads((Path(data) / ALL_STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(entry, dict) and isinstance(entry.get("repos"), dict):
        github.seed(ALL_KEY, entry, float(entry.get("at") or 0))


def _save_all(data, value):
    if data is None:
        return
    with _store_lock, tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, prefix="done-issues-",
                                                  suffix=".tmp", delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False)
        temporary = stream.name
    Path(temporary).replace(Path(data) / ALL_STORE)


def _per_repo(data, keys):
    """各项目详情页已经读到的已完成结果：只看缓存和保存的文件，不发请求。"""
    out = {}
    for key in keys:
        _seed(data, key)
        value = github.peek(("prs-closed", key))
        if value:
            out[key] = value
    return out


def done_all(projects, data=None, *, force=False, now=None):
    """Issue 列表页的「已完成」：全部项目近 30 天已完成的 issue，带项目名和关闭原因，新关闭的在前。
    限流时不发请求，给旧值并写明；没有旧值时先不列。"""
    by_repo = {p["github"].lower(): p for p in projects if REPO.fullmatch(p.get("github") or "")}
    since = since_date(now)
    _seed_all(data)

    def load():
        value = load_all(since)
        try:
            _save_all(data, value)
        except OSError:
            pass
        return value

    result = github.cached(ALL_KEY, load, ALL_TTL, force=force)
    found = (result["value"] or {}).get("repos", {})
    cached = _per_repo(data, by_repo)
    rows = []
    for key, project in by_repo.items():
        for item in done_issues(_union(cached.get(key), found.get(key)), since):
            repo = (found.get(key) or {}).get("name") or project["github"]
            rows.append({**item, "repo": repo, "subject": f"{repo}#{item['number']}",
                         "project_id": project["id"], "project_name": project["name"]})
    rows.sort(key=lambda r: r["closed_at"] or "", reverse=True)
    note = github.describe(result)
    limited = note["message"] if note["limited"] else ""
    if limited and not result["at"]:
        until = github.blocked_until()
        limited = f"GitHub 限流中，已完成的 issue 还没读到，约 {max(1, round((until - time.time()) / 60)) if until else 1} 分钟后自动重试"
    error = "" if limited else note["message"]
    return {"issues": rows, "since": since, "loaded": result["value"] is not None or bool(rows),
            "fetched_at": _stamp(result["at"]), "refreshing": result["refreshing"], "limited": limited,
            "error": f"已完成的 issue 读取失败：{error}" if error else ""}


def seed_stores(data, repos):
    """重启后把保存的已完成结果放进缓存（不发请求），known_closed 才认得出来。"""
    _seed_all(data)
    for repo in repos:
        _seed(data, repo.lower())


def known_closed():
    """已经知道关闭了的 issue（'owner/repo#N' 小写 → 关闭时间）：只看全部项目和各项目的已完成缓存，不发请求。"""
    out = {}
    for key, value in github.entries("prs-closed") + github.entries(*ALL_KEY):
        repos = value.get("repos", {}) if key == ALL_KEY else {key[1]: value}
        for repo, closed in repos.items():
            for issue in closed.get("issues", []):
                out[f"{repo}#{issue['number']}"] = max(out.get(f"{repo}#{issue['number']}", ""), issue.get("closed_at") or "")
            for pr in closed.get("prs", []):
                if pr.get("state") == "merged":
                    for ref in pr.get("closes", []):
                        if ref.get("state") == "CLOSED" and ref.get("closed_at"):
                            subject = f"{repo}#{ref['number']}"
                            out[subject] = max(out.get(subject, ""), ref["closed_at"])
    return out
