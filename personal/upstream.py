"""用 GraphQL 批量读 GitHub：自己仓库的 open issue、上游仓库里与我有关的 issue 和 PR、近期本人创建的 PR 和 issue。

都经 personal.github 出口（并发上限、限流退避、计数）。搜索只发跨仓库的合并查询，不按仓库逐个查。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import github


def _owner():
    """登记表 [owner]（projects.toml 合上数据目录的 projects.local.toml）；读不到是空的。"""
    from . import projects_config
    try:
        return projects_config.load()["owner"]
    except (OSError, ValueError):
        return {}


def _gh_login():
    try:
        out = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


_OWNER = _owner()
# 本人的 GitHub 账号：环境变量 WORKBENCH_GITHUB_LOGIN → 登记表 [owner] login → gh 当前登录的账号
LOGIN = (os.environ.get("WORKBENCH_GITHUB_LOGIN") or str(_OWNER.get("login") or "") or _gh_login()).strip()
OWN_ACCOUNTS = {LOGIN, *map(str, _OWNER.get("accounts") or [])} - {""}  # 本人的账号：这些账号下的仓库不算上游
OWN_EMAILS = {str(e).lower() for e in _OWNER.get("emails") or []}  # 本人提交用的其他邮箱（如 GitHub 的 noreply 地址）
REPO = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
CHUNK = 10
ISSUE_WORKERS = 3
ISSUE_CHUNK = 4  # 列表带上留言和引用后，10 个仓库一个请求会超 GitHub 的单次查询资源上限（实测 5 个可以）
MAX_SEARCH_PAGES = 10
# 列表顺带读最近 THREAD 条留言和交叉引用（一个混合连接，GraphQL 计分按连接数算，不另加连接）：
# 谁最后说话、在等谁、有没有引用它的 open PR，Issue 页按这些归类（personal/next_step.py），不另发请求
THREAD = 15
CLIP = 1500  # 留言和正文只留这么多字，归类和一句话摘要够用
BODY_CLIP = 8000  # 正文另留一份长的（text），改判时找「依赖：」「待确认」这类写在后面的字
# body 只用来认自动生成的规划标记和没勾的清单项（bodyText 里没有 HTML 注释和勾选框），读完就丢，不放进列表
ISSUE = ("number title url createdAt updatedAt bodyText body author { __typename login } labels(first: 20) { nodes { name } } "
         "assignees(first: 10) { nodes { login } } milestone { title } comments { totalCount } "
         f"timelineItems(itemTypes: [ISSUE_COMMENT, CROSS_REFERENCED_EVENT], last: {THREAD}) {{ nodes {{ __typename "
         "... on IssueComment { author { __typename login } authorAssociation bodyText createdAt createdViaEmail url } "
         "... on CrossReferencedEvent { willCloseTarget source { __typename ... on PullRequest { number title url state baseRefName mergedAt } } } } }")
PLAN_MARK = re.compile(r"<!--\s*(?:project-plan|issue-feasibility-audit)\b", re.I)  # 自动生成的规划卡、可行性审计卡
UNCHECKED = re.compile(r"^\s*[-*+]\s+\[ \]", re.M)
REPO_INFO = "Repository"  # open_issues 顺带给的仓库主线：{type, repository, branch, committed_at}
PULL = "number title url createdAt updatedAt author { login } isDraft reviewDecision state mergedAt"
HEAD = "PullRequestHead"  # open_issues 顺带给的 agent PR head：{type, repository, number, headRefOid}
AGENT_BRANCH = re.compile(r"agent/issue-[1-9]\d*")


def item(node, repo, kind="Issue"):
    """统一成工作台列表用的形状（和原来 gh search 的 JSON 一致）。"""
    row = {"type": kind, "repository": {"nameWithOwner": repo}, "number": node["number"], "title": node["title"],
           "url": node["url"], "createdAt": node["createdAt"], "updatedAt": node["updatedAt"],
           "author": node.get("author") or {}, "labels": (node.get("labels") or {}).get("nodes", []),
           "commentsCount": (node.get("comments") or {}).get("totalCount", 0)}
    if kind == "Issue" and "timelineItems" in node:
        row.update(thread(node))
    if kind == "Issue":
        body = node.get("body") or ""
        row.update(text=(node.get("bodyText") or "")[:BODY_CLIP],assignees=[a["login"] for a in ((node.get("assignees") or {}).get("nodes") or []) if a and a.get("login")],
                   milestone=(node.get("milestone") or {}).get("title") or "",
                   plan=bool(PLAN_MARK.search(body)), unchecked=len(UNCHECKED.findall(body)))
    if kind == "PullRequest":
        row.update(isDraft=node.get("isDraft", False), reviewDecision=node.get("reviewDecision"),
                   state=node.get("state"), mergedAt=node.get("mergedAt"))
    return row


def clip(text):
    text = text or ""
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def thread(node):
    """列表节点里的留言和引用 → {"thread": 和 conversation.read_threads 读到的 issue 节点同形（conversation.assess 直接用）,
    "refs": 引用这条 issue 的 PR [{number, title, url, state, closes, base, merged_at}]}。同一个 PR 引用多次只留一条，closes 取或。"""
    items = ((node.get("timelineItems") or {}).get("nodes")) or []
    comments = [{"author": c.get("author"), "authorAssociation": c.get("authorAssociation") or "", "bodyText": clip(c.get("bodyText")),
                 "createdAt": c.get("createdAt"), "createdViaEmail": bool(c.get("createdViaEmail")), "url": c.get("url") or ""}
                for c in items if (c or {}).get("__typename") == "IssueComment"]
    refs = {}
    for e in items:
        source = (e or {}).get("source") or {}
        if (e or {}).get("__typename") != "CrossReferencedEvent" or source.get("__typename") != "PullRequest" or not source.get("url"):
            continue
        ref = refs.setdefault(source["url"], {"number": source.get("number"), "title": source.get("title") or "",
                                               "url": source["url"], "state": source.get("state") or "", "closes": False,
                                               "base": source.get("baseRefName") or "", "merged_at": source.get("mergedAt") or ""})
        ref["closes"] = ref["closes"] or bool(e.get("willCloseTarget"))
    return {"thread": {"author": node.get("author") or {}, "createdAt": node.get("createdAt"), "bodyText": clip(node.get("bodyText")),
                       "url": node.get("url") or "", "comments": {"nodes": comments}},
            "refs": list(refs.values())}


COMMIT = "target { ... on Commit { committedDate } }"


def open_issues(repos, branches=None):
    """ISSUE_CHUNK 个仓库一组，一个 GraphQL 请求读完它们的 open issue（每仓最近更新的 100 条，带最近的留言和引用）。
    同一个请求顺带读 agent/issue-<编号> 分支开的 open PR 的 head 提交，放在列表里（type 是 HEAD），
    工作台用它判断审查结论还算不算数，不另发请求；也顺带读主线（branches 里配了 main_branch 的用它，没配用默认分支）
    最近一次提交的时间（type 是 REPO_INFO），收件箱据此判断项目是不是久未动。"""
    pairs = [m for m in (REPO.fullmatch(r) for r in repos) if m]
    wanted = {k.lower(): v for k, v in (branches or {}).items() if v}

    def main_ref(m):
        branch = wanted.get(f"{m[1]}/{m[2]}".lower())
        return f"main: ref(qualifiedName: {json.dumps('refs/heads/' + branch)}) {{ name {COMMIT} }} " if branch else ""

    def read(chunk):
        fields = " ".join(
            f"r{i}: repository(owner: {json.dumps(m[1])}, name: {json.dumps(m[2])}) {{ nameWithOwner "
            f"defaultBranchRef {{ name {COMMIT} }} {main_ref(m)}"
            f"issues(states: OPEN, first: 100, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{ nodes {{ {ISSUE} }} }} "
            f"pullRequests(states: OPEN, first: 50, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{ nodes {{ number headRefName headRefOid }} }} }}"
            for i, m in enumerate(chunk))
        data = github.graphql(f"query {{ {fields} }}", kind="graphql:issues")
        rows = []
        for i in range(len(chunk)):
            repo = data.get(f"r{i}")
            if repo:
                main = repo.get("main") or repo.get("defaultBranchRef") or {}
                rows.append({"type": REPO_INFO, "repository": {"nameWithOwner": repo["nameWithOwner"]}, "branch": main.get("name") or "",
                             "committed_at": (main.get("target") or {}).get("committedDate") or ""})
                rows += [item(node, repo["nameWithOwner"]) for node in repo["issues"]["nodes"]]
                rows += [{"type": HEAD, "repository": {"nameWithOwner": repo["nameWithOwner"]}, "number": node["number"],
                          "headRefOid": node["headRefOid"]}
                         for node in (repo.get("pullRequests") or {}).get("nodes") or []
                         if node and AGENT_BRANCH.fullmatch(node.get("headRefName") or "") and node.get("headRefOid")]
        return rows

    chunks = [pairs[start:start + ISSUE_CHUNK] for start in range(0, len(pairs), ISSUE_CHUNK)]
    with ThreadPoolExecutor(max_workers=ISSUE_WORKERS) as pool:  # 一组要几秒，几组同时读；并发上限仍由 github.py 管
        return [row for rows in pool.map(read, chunks) for row in rows]


def search(query, kind):
    """GraphQL 搜索（issue 和 PR 一起），翻页最多 MAX_SEARCH_PAGES 页。"""
    text = ("query($q: String!, $after: String) { search(query: $q, type: ISSUE, first: 100, after: $after) { "
            "pageInfo { hasNextPage endCursor } nodes { __typename "
            f"... on Issue {{ repository {{ nameWithOwner }} {ISSUE} }} "
            f"... on PullRequest {{ repository {{ nameWithOwner }} {PULL} }} }} }} }}")
    rows, after = [], None
    for _ in range(MAX_SEARCH_PAGES):
        data = github.graphql(text, kind=kind, q=query, **({"after": after} if after else {}))["search"]
        rows += [item(n, n["repository"]["nameWithOwner"], n["__typename"]) for n in data["nodes"] if n.get("repository")]
        if not data["pageInfo"]["hasNextPage"]:
            break
        after = data["pageInfo"]["endCursor"]
    return rows


def involved(login=LOGIN):
    """与我有关（本人创建、被指派、被 @ 或评论过）的 open issue 和 PR，一个跨仓库查询。"""
    return search(f"involves:{login} is:open", "graphql:involves")


def merged(since, login=LOGIN):
    return search(f"author:{login} is:pr is:merged merged:>={since}", "graphql:merged")


def authored(since, login=LOGIN):
    """近期本人创建的 PR 和 issue，用来判断上游仓库是否在近月范围、发现新的上游仓库。"""
    others = " ".join(f"-user:{name}" for name in sorted(OWN_ACCOUNTS))  # 自己的仓库靠本人提交判断，这里只看上游
    return search(f"author:{login} created:>={since} {others}", "graphql:authored")


def upstream(repo):
    owner = repo.split("/", 1)[0].lower()
    return owner not in OWN_ACCOUNTS


def pr_stage(row):
    if row.get("mergedAt"):
        return "已合并"
    if row.get("reviewDecision") == "CHANGES_REQUESTED":
        return "要改"
    return "草稿" if row.get("isDraft") else "待审"


def tally(projects, involved_rows, merged_rows, login=LOGIN):
    """按项目数：我的 open PR（待审 / 要改 / 草稿）、近月已合并 PR、我参与的 open issue。"""
    by_repo = {p["github"].lower(): p["id"] for p in projects if p.get("github")}
    out = {}
    for row in involved_rows + merged_rows:
        pid = by_repo.get(row["repository"]["nameWithOwner"].lower())
        if not pid:
            continue
        counts = out.setdefault(pid, {"open_prs": 0, "待审": 0, "要改": 0, "草稿": 0, "已合并": 0, "issues": 0})
        if row["type"] == "PullRequest":
            if (row.get("author") or {}).get("login", "").lower() != login.lower():
                continue
            stage = pr_stage(row)
            counts[stage] += 1
            if stage != "已合并":
                counts["open_prs"] += 1
        else:
            counts["issues"] += 1
    return out


LINKED_PR = ("number title url state isDraft reviewDecision author { login } repository { nameWithOwner } "
             "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }")


def linked_prs(repo, number):
    """引用这条 issue 的 open PR（issue 时间线上的交叉引用，最近 100 条），一个 GraphQL 请求。"""
    found = REPO.fullmatch(repo)
    if not found:
        raise ValueError("仓库名无效")
    text = ("query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { "
            "issue(number: $number) { timelineItems(itemTypes: [CROSS_REFERENCED_EVENT], last: 100) { nodes { "
            f"... on CrossReferencedEvent {{ source {{ __typename ... on PullRequest {{ {LINKED_PR} }} }} }} }} }} }} }} }}")
    data = github.graphql(text, kind="graphql:linked", owner=found[1], name=found[2], number=int(number))
    nodes = ((((data.get("repository") or {}).get("issue") or {}).get("timelineItems") or {}).get("nodes") or [])
    rows = {}
    for node in nodes:
        pr = (node or {}).get("source") or {}
        if pr.get("__typename") != "PullRequest" or pr.get("state") != "OPEN":
            continue
        commits = (pr.get("commits") or {}).get("nodes") or []
        rollup = ((commits[0] or {}).get("commit") or {}).get("statusCheckRollup") if commits else None
        rows[pr["url"]] = {"repo": (pr.get("repository") or {}).get("nameWithOwner", ""), "number": pr["number"],
                           "title": pr["title"], "url": pr["url"], "author": (pr.get("author") or {}).get("login", ""),
                           "draft": bool(pr.get("isDraft")), "review": pr.get("reviewDecision") or "",
                           "checks": (rollup or {}).get("state") or ""}
    return sorted(rows.values(), key=lambda r: (r["repo"], r["number"]))
