"""自动发现上游项目：近一个月本人在别人的仓库创建过 PR 或 issue，但登记表里还没有的，追加到数据目录的 projects.local.toml。

新项目进「开源协作」组，issue_scope = "mine"（只看与我有关的 issue 和 PR）。本地有检出的按 remote 匹配填 repo_path：
扫 projects.toml 里 [discovery] roots 列出的目录（每项可写 depth），只认 .git 是目录的主检出，不把 worktree 当项目。
[discovery] skip_owners 列出的账号下的仓库不自动登记；已登记的（含本机覆盖文件 projects.local.toml 里的）照常列出。
"""
from __future__ import annotations

import re
import subprocess
from datetime import date
from pathlib import Path

from . import upstream

DEFAULT_ROOTS = [{"path": str(Path.home()), "depth": 2}]  # [discovery] roots 没写时只在家目录下找
SKIP = {"node_modules", ".git", "_待删除", "worktrees", ".cache", "Library"}
REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")

ID_MAX = 16  # 项目标识最长位数
PROJECT_ID_PATTERN = rf"[a-z][a-z0-9]{{1,{ID_MAX - 1}}}"  # 项目标识：小写字母开头，2 到 ID_MAX 位小写字母和数字


def settings(config_path):
    """[discovery]：登记表的合上本机覆盖文件 projects.local.toml 的（覆盖文件写了的键优先）。"""
    from . import projects_config
    try:
        return projects_config.load(config_path)["discovery"]
    except (OSError, ValueError):
        return {}


def roots(config_path):
    return settings(config_path).get("roots") or DEFAULT_ROOTS


def skip_owners(config_path):
    """[discovery] skip_owners：这些账号下的仓库不自动登记（小写）。"""
    return {str(o).lower() for o in settings(config_path).get("skip_owners") or []}


def remotes(path):
    out = subprocess.run(["git", "-C", str(path), "config", "--get-regexp", r"^remote\..*\.url$"],
                         capture_output=True, text=True, timeout=20)
    found = {}
    for line in out.stdout.splitlines():
        name, _, url = line.partition(" ")
        match = REMOTE.search(url.strip())
        if match:
            found[name.split(".")[1]] = match[1]
    return found


def local_clones(entries):
    """{owner/repo 小写: (检出目录, {remote 名: owner/repo})}；同一仓库多处检出时取路径最短的。"""
    clones = {}

    def visit(folder, depth):
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and p.name not in SKIP and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            if (child / ".git").is_dir():
                try:
                    named = remotes(child)
                except (OSError, subprocess.TimeoutExpired):
                    named = {}
                for repo in set(named.values()):
                    key = repo.lower()
                    if key not in clones or len(str(child)) < len(str(clones[key][0])):
                        clones[key] = (child, named)
            elif depth > 1:
                visit(child, depth - 1)

    for entry in entries:
        folder = Path(entry["path"])
        if (folder / ".git").is_dir():
            named = remotes(folder)
            for repo in set(named.values()):
                clones.setdefault(repo.lower(), (folder, named))
        visit(folder, int(entry.get("depth", 1)))
    return clones


def aliases(project):
    return {str(v).lower() for v in [project.get("repository"), project.get("github"), *project.get("commit_repositories", [])] if v}


def slug(repo, taken):
    """项目标识：仓库名里的小写字母和数字，字母开头，最长 ID_MAX 位（PROJECT_ID_PATTERN）；重名在末尾加序号，仍不超长。"""
    base = re.sub(r"[^a-z0-9]", "", repo.split("/", 1)[1].lower()).lstrip("0123456789")[:ID_MAX]
    base = base if len(base) >= 2 else "upstream"
    name, n = base, 2
    while name in taken:
        name, n = f"{base[:ID_MAX - len(str(n))]}{n}", n + 1
    taken.add(name)
    return name


def plan(authored_rows, projects, clones, skip=frozenset()):
    """要追加的新项目：上游仓库、本人近月创建过 PR 或 issue、登记表任何别名都对不上；skip 里的账号（小写）下的仓库不加。"""
    known = set().union(*(aliases(p) for p in projects)) if projects else set()
    taken = {p["id"] for p in projects}
    repos = sorted({repo for r in authored_rows if upstream.upstream(repo := r["repository"]["nameWithOwner"])
                    and repo.split("/", 1)[0].lower() not in skip}, key=str.lower)
    entries = []
    for repo in repos:
        if repo.lower() in known:
            continue
        path, named = clones.get(repo.lower(), ("", {}))
        fork = next((r for r in named.values() if r.split("/", 1)[0].lower() == upstream.LOGIN), "")
        entries.append({"id": slug(repo, taken), "name": repo.split("/", 1)[1], "group": "开源协作", "repo_path": str(path),
                        "repository": fork or repo, "github": repo, "issue_scope": "mine",
                        "source_status": "上游 · 只看与我有关"})
        known.add(repo.lower())
    return entries


def toml_block(entry):
    lines = ["[[projects]]"]
    for key in ("id", "name", "group", "repo_path", "repository", "github", "issue_scope", "source_status"):
        value = str(entry.get(key, "")).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key} = "{value}"')
    return "\n".join(lines)


def append(config_path, entries, today=None):
    if not entries:
        return
    path = Path(config_path)
    text = path.read_text(encoding="utf-8").rstrip("\n") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"# 自动发现 {today or date.today().isoformat()}：近一个月本人在这些上游仓库创建过 PR 或 issue"
    Path(config_path).write_text(text + "\n\n" + header + "\n\n" + "\n\n".join(toml_block(e) for e in entries) + "\n",
                                 encoding="utf-8")


def run(config_path, projects, since, *, target=None, read=upstream.authored, scan=local_clones):
    """读近月本人创建的 PR 和 issue，追加没登记的上游仓库。返回 (本人创建的条目, 新加的项目)。
    roots 和 skip_owners 按 config_path 合上本机覆盖文件读；新项目写进 target（不给就写进 config_path）。"""
    rows = read(since)
    entries = plan(rows, projects, scan(roots(config_path)), skip_owners(config_path))
    append(target or config_path, entries)
    return rows, entries
