#!/usr/bin/env python3
"""工作台「对话」里的助理唯一能运行的命令：只读地查工作台、GitHub、本机仓库和中台。

对话进程（personal/chat.py）用 Claude Code 的 --restricted 模式启动，只放行 `python3 <本文件> …` 这一种 Bash 命令；
所以这里的每个子命令都只能读，参数先校验再交给 subprocess（列表参数，不经过 shell），不能借参数写东西。

  api <路径>                 GET 工作台的 /api/<路径>（只读接口，如 autoloop/runs、issues/done、brief、boards）
  issue <owner/repo> <编号>  gh issue view --comments
  pr <owner/repo> <编号>     gh pr view --comments
  issues <owner/repo>        gh issue list（开着的，最多 50 条）
  prs <owner/repo>           gh pr list（开着的，最多 50 条）
  log <目录> [条数]          git log --oneline（目录要在 /Volumes/Luochen 下）
  status <目录>              git status -sb
  hub                        agent-autoloop status
  projects                   能开 issue 的仓库（拟 issue 草稿前先看）
输出超过 60000 字截断。
"""
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

LIMIT = 60000
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API = re.compile(r"^[A-Za-z0-9/_.-]+(\?[A-Za-z0-9_.=&%-]*)?$")
ROOTS = (Path("/Volumes/Luochen"),)


def fail(message):
    print(message, file=sys.stderr)
    return 2


def show(text):
    print(text if len(text) <= LIMIT else text[:LIMIT] + f"\n…（截断，原文 {len(text)} 字）")
    return 0


def run(args, cwd=None):
    done = subprocess.run(args, capture_output=True, text=True, timeout=60, cwd=cwd)
    return show((done.stdout or "") + (("\n" + done.stderr) if done.returncode and done.stderr else ""))


def api(path):
    path = path.lstrip("/")
    if path.startswith("api/"):
        path = path[4:]
    if not API.match(path) or ".." in path:
        return fail("路径只能是 /api/ 下的只读路径，比如 autoloop/runs")
    port = os.environ.get("WORKBENCH_PORT", "8790")
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return show(response.read().decode("utf-8", "replace"))
    except OSError as exc:
        return fail(f"工作台没读到：{exc}")


def folder(value):
    path = Path(value).expanduser().resolve()
    if not any(path == root or root in path.parents for root in ROOTS) or not path.is_dir():
        raise ValueError(f"目录要在 {', '.join(map(str, ROOTS))} 下：{value}")
    return path


def main(argv):
    if not argv:
        return fail(__doc__)
    command, rest = argv[0], argv[1:]
    try:
        if command == "api" and len(rest) == 1:
            return api(rest[0])
        if command in ("issue", "pr") and len(rest) == 2 and REPO.match(rest[0]) and rest[1].isdigit():
            return run(["gh", command, "view", rest[1], "-R", rest[0], "--comments"])
        if command in ("issues", "prs") and len(rest) == 1 and REPO.match(rest[0]):
            return run(["gh", command[:-1], "list", "-R", rest[0], "--limit", "50"])
        if command == "log" and 1 <= len(rest) <= 2 and (len(rest) == 1 or rest[1].isdigit()):
            count = min(int(rest[1]) if len(rest) == 2 else 20, 200)
            return run(["git", "log", "--oneline", "--no-decorate", "-n", str(count)], cwd=folder(rest[0]))
        if command == "status" and len(rest) == 1:
            return run(["git", "status", "-sb"], cwd=folder(rest[0]))
        if command == "projects" and not rest:
            return run([sys.executable, str(Path(__file__).resolve().parent / "issue_draft.py"), "projects"])
        if command == "hub" and not rest:
            return run(["agent-autoloop", "status"])
    except ValueError as exc:
        return fail(str(exc))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return fail(f"没跑成：{exc}")
    return fail("不认识的用法。\n" + __doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
