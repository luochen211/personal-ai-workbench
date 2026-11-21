"""「对话」页：在工作台里和助理聊天（用户 2026-11-21 要求）。

助理只读（用户 2026-11-21 定）：每条消息在后台起一次 Claude Code（中台的 claude-guarded，带沙箱），用 --restricted
（不读用户设置、没有改文件的工具、拒绝跳过权限）、--permission-mode dontAsk（没放行的一律拒绝）、--strict-mcp-config
（只带下面的生财 MCP，读不到微信），工具只给 Read / Grep / Glob / WebSearch / ToolSearch 和一条 Bash：
`python3 scripts/chat_read.py …`（只读查询）。
生财有术 MCP（用户 2026-11-21 要求接入，用来按本人在做的事推荐生财内容）：沿用本机 Claude Code 已有的授权，
只按名单放行只读工具（SCYS_TOOLS）；打赏、点赞、收藏、关注、发布技能、提反馈、发起 AI 问答这些会花钱、改账号或耗次数的不放行。
MCP 工具要先用 ToolSearch 加载说明才能调用，所以 ToolSearch 在可用工具里（它只加载说明，不执行东西）。
要改东西时助理不改，在回复末尾附一段 ```issue-draft 的 JSON；页面把它显示成卡片，本人点「存成草稿」才进「新需求」抽屉，
在那里确认后才创建 issue，之后照常交给 Agent，走 PR 和审查。

对话存在数据目录 chats.json（600），只在本机；同一个对话接着聊用 --resume 上一轮的 Claude 会话。
跑的时候进度（已收到的字、用过的查询）放在内存里，页面每半秒读一次 GET /api/chats/<id>；刷新页面不丢，跑完才写进文件。
"""
import json
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import agent_watch

ROOT = Path(__file__).resolve().parents[1]
FILE = "chats.json"
PROGRAM_ENV = "WORKBENCH_CHAT_PROGRAM"
PROGRAM = "~/.agent-autoloop/bin/claude-guarded"  # 和中台的 Agent 同一个沙箱：读不到微信库和密钥
MODEL = "claude-opus-5-5"
READER = ROOT / "scripts" / "chat_read.py"
DIRS = ["/Volumes/Luochen/工作", "/Volumes/Luochen/LCS"]  # 文件工具能读的目录（工作目录是助理仓库，在 LCS 下）
TIMEOUT = 15 * 60
MAX_TEXT = 8000
MAX_TOOLS = 40
ID = re.compile(r"^c[0-9a-f]{12}$")
DRAFT = re.compile(r"```issue-draft\s*\n(.*?)\n```", re.S)
SCYS = {"mcpServers": {"scys-mcp": {"type": "http", "url": "https://mcp.scys.com/shengcai-web/mcp"}}}
SCYS_TOOLS = (  # 只读：搜索、读详情和评论、看本人的足迹/划线/画像/活动进度
    "activityList", "activityManualDetail", "activityManualSearch", "activityManualToc", "activityPilotSearch", "activitySearch",
    "ai__listAiFeedItems", "co-creation__getFeedback", "co-creation__listFeedbacks", "contentSearch", "getDailyReport",
    "getDeepseaContentDetail", "getDeepseaCourseChapterContent", "getDeepseaCourseDetail", "getDocumentDetail",
    "getMyActivityProgress", "getMyBenefits", "getMyInsight", "getPartyDetail", "getProfileInfo", "getProjectToolDetail",
    "getTrustedContentSection", "getUserComments", "getUserRelationList", "koc__getMaterials", "listMenu", "listMyActivities",
    "listMyDeepseaCommunities", "listMyHighlightComments", "listMyHighlights", "listMyTopicFootprints", "listProjectCaseFilters",
    "liuXiaoPaiHistoryList", "pageActivityCourseComments", "pageDeepseaContentComments", "pageDeepseaCourseComments",
    "pageTopicComment", "pageVisibleActivitySubmissionComments", "projectLibDetail", "projectLibSearch", "qaHistoryList",
    "searchDeepseaCourseChapters", "searchDeepseaCourses", "searchDeepseaPublicContent", "searchMyActivityOutputs",
    "searchParties", "searchProjectCases", "searchProjectTools", "searchTopic", "searchTrustedContent",
    "searchVisibleActivityQa", "searchVisibleActivitySubmissions", "skillhubGetReadme", "skillhubListComments",
    "skillhubListOfficialFeedback", "skillhubMySkills", "skillhubSearch", "tokenrank__queryMyTokenrank",
    "tokenrank__queryPublicTokenrank", "topicDetail", "userSearch")
SCYS_PREFIX = "mcp__scys-mcp__"
PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "~/.npm-global/bin", "~/.local/bin"]

PROMPT = """你在落尘的个人工作台「对话」页里回答本人，用中文，先给结论，简洁。

你是只读的：可以用 Read、Grep、Glob 读文件，用 WebSearch 查网上，用 Bash 只能运行 `python3 {reader} <子命令>` 查工作台、GitHub、本机仓库和中台（不带参数运行看用法；`projects` 列出能开 issue 的仓库）。别的命令、改文件、写 GitHub 都会被拒绝，也不要尝试绕过。全局规则里关于提交、推送、开 PR、写文件、出 HTML 页面的要求在这里都不适用。

生财有术（scys.com）的帖子、案例、航海、活动、课程、深海圈和公众号/推特精选，用 `mcp__scys-mcp__` 开头的工具查（先用 ToolSearch 加载）；不要用 WebSearch 去搜或抓 scys.com，自动访问会让本人账号被封。限流返回 MCP_RATE_LIMITED 时照 retryAfterSeconds 等，查不到就直说。

本人要你推荐生财上适合读的内容时：
1. 先看本人在做什么：助理仓库 事项/ 下各板块的现状和下一步，加上 `api autoloop/runs` 里最近的活；
2. 再看本人在生财上的痕迹：getMyInsight、listMyTopicFootprints（看过的）、listMyHighlights（划过的）、listMyActivities / getMyActivityProgress（在跟的航海和活动）；
3. 归纳 3～5 个当下的关注点，每个关注点去搜（searchTopic、searchProjectCases、activityManualSearch、searchTrustedContent、searchDeepseaPublicContent 等），读一下详情再选；
4. 去掉本人看过的，每个关注点给 1～3 条：标题、作者、链接（工具返回里有才给）、一句「为什么现在适合读」，并写明对应哪个事项。宁少勿滥，挑不出好的就说没有。

回答前先读真源：当前目录是助理仓库，按根目录 README 的路由表找板块；问助理做过什么、在做什么，用 `api autoloop/runs`、`api autoloop/watch`、`api issues/done`。事实和推测分开写，查不到的写「待确认」，不编。

本人要改代码、改文档、加功能或做任何写操作时，不要自己做，也不要说你做了：先用 `projects` 确认仓库，在回复最后附一段 issue 草稿，格式严格如下（JSON 一行或多行都行）：
```issue-draft
{{"repo": "owner/name", "title": "一句话说清要做什么", "body": "背景：…\\n\\n要做什么：…\\n\\n怎么算做完：…"}}
```
页面会把它显示成卡片，本人确认后才进「新需求」并创建 issue，再交给 Agent 走 PR 和审查。一次最多附 3 段。"""

_lock = threading.Lock()
_live = {}  # 对话 id → {"proc", "text", "tools", "started_at", "stop"}


def path_of(data=None):
    return Path(data or ROOT / ".personal") / FILE


def stamp(at=None):
    return (at or datetime.now().astimezone()).isoformat(timespec="seconds")


def program():
    return Path(os.environ.get(PROGRAM_ENV) or PROGRAM).expanduser()


def _load(data=None):
    found = agent_watch.read_json(path_of(data), {})
    chats = found.get("chats") if isinstance(found, dict) and isinstance(found.get("chats"), dict) else {}
    return {k: v for k, v in chats.items() if ID.fullmatch(k) and isinstance(v, dict)}


def _save(chats, data=None):
    path = path_of(data)
    agent_watch.write_json(path, {"chats": chats})
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _update(cid, change, data=None):
    with _lock:
        chats = _load(data)
        if cid in chats:
            change(chats[cid])
            _save(chats, data)


def drafts_in(text):
    """回复里的 ```issue-draft 段 → [{repo, title, body}]；写坏的跳过。"""
    out = []
    for raw in DRAFT.findall(text or "")[:3]:
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip() for k in ("repo", "title", "body")):
            out.append({k: item[k].strip() for k in ("repo", "title", "body")})
    return out


def listing(data=None):
    """左边的对话列表，新的在前。"""
    chats = _load(data)
    rows = [{"id": c["id"], "title": c.get("title") or "（没有标题）", "updated_at": c.get("updated_at"), "running": c["id"] in _live}
            for c in chats.values()]
    rows.sort(key=lambda r: r["updated_at"] or "", reverse=True)
    return {"chats": rows, "model": MODEL, "ready": program().exists(), "program": str(program())}


def detail(cid, data=None):
    chat = _load(data).get(cid)
    if not chat:
        raise KeyError("没有这个对话")
    live = _live.get(cid)
    messages = [{**m, "drafts": drafts_in(m.get("text"))} if m.get("role") == "assistant" else m for m in chat.get("messages", [])]
    out = {"id": cid, "title": chat.get("title", ""), "messages": messages, "running": bool(live)}
    if live:
        out["live"] = {"text": live["text"], "tools": live["tools"][-MAX_TOOLS:], "started_at": live["started_at"]}
    return out


def delete(cid, data=None):
    if cid in _live:
        raise ValueError("这个对话还在回答，先停止")
    with _lock:
        chats = _load(data)
        if chats.pop(cid, None) is None:
            raise KeyError("没有这个对话")
        _save(chats, data)
    return {"ok": True}


def args_for(session):
    reader = str(READER)
    return [str(program()), "-p", "--restricted", "--tools", "Read,Grep,Glob,WebSearch,ToolSearch,Bash",
            "--allowedTools", ",".join(["Read,Grep,Glob,WebSearch,ToolSearch", f"Bash(python3 {reader} *)", *(SCYS_PREFIX + t for t in SCYS_TOOLS)]),
            "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", json.dumps(SCYS),
            "--model", MODEL, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--append-system-prompt", PROMPT.format(reader=reader), *[a for d in DIRS for a in ("--add-dir", d)],
            *(["--resume", session] if session else [])]


def tool_line(block):
    """一次工具调用写成一句给页面看。"""
    name, given = block.get("name", ""), block.get("input") or {}
    if name == "Bash":
        command = str(given.get("command", ""))
        return {"name": "查询", "detail": command.split(str(READER), 1)[-1].strip() if str(READER) in command else command[:120]}
    if name == "Read":
        return {"name": "读文件", "detail": str(given.get("file_path", ""))}
    if name in ("Grep", "Glob"):
        return {"name": "搜索", "detail": str(given.get("pattern", ""))}
    if name == "WebSearch":
        return {"name": "上网查", "detail": str(given.get("query", ""))}
    if name.startswith(SCYS_PREFIX):
        words = next((str(given[k]) for k in ("keyword", "query", "accountKey") if given.get(k)), "")
        return {"name": "查生财", "detail": f"{name[len(SCYS_PREFIX):]} {words}".strip()[:120]}
    if name == "ToolSearch":
        return {"name": "加载工具", "detail": str(given.get("query", ""))[:120]}
    return {"name": name, "detail": ""}


def consume(lines, live, on_session):
    """读 stream-json：文字片段累进 live["text"]，工具调用记进 live["tools"]；返回 result 那一行（没有就是 None）。"""
    result, texts = None, 0
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init" and event.get("session_id"):
            on_session(event["session_id"])
        elif kind == "stream_event":
            inner = event.get("event") or {}
            if inner.get("type") == "content_block_start" and (inner.get("content_block") or {}).get("type") == "text":
                if texts and not live["text"].endswith("\n\n"):
                    live["text"] += "\n\n"
                texts += 1
            elif inner.get("type") == "content_block_delta" and (inner.get("delta") or {}).get("type") == "text_delta":
                live["text"] += inner["delta"].get("text", "")
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    live["tools"].append(tool_line(block))
        elif kind == "result":
            result = event
    return result


def _run(cid, text, session, data, spawn):
    live = _live[cid]
    error, cost = "", None
    try:
        env = {**os.environ, "PATH": ":".join([str(Path(p).expanduser()) for p in PATH] + [os.environ.get("PATH", "")]),
               "WORKBENCH_PORT": os.environ.get("WORKBENCH_PORT", "8790")}
        proc = spawn(args_for(session), cwd=_cwd(), env=env)
        live["proc"] = proc
        errors = []
        drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() if proc.stderr else ""), daemon=True)
        drain.start()  # stderr 另开线程读，免得它写满管道卡住 stdout
        proc.stdin.write(text)
        proc.stdin.close()
        timer = threading.Timer(TIMEOUT, lambda: (live.update(stop="超过 15 分钟，停了"), proc.kill()))
        timer.start()
        try:
            result = consume(proc.stdout, live, lambda s: _update(cid, lambda c: c.update(session=s), data))
            proc.wait()
        finally:
            timer.cancel()
        drain.join(5)
        tail = "".join(errors).strip()[-600:]
        if live.get("stop"):
            error = live["stop"]
        elif result is None:
            error = f"Claude 没跑完（退出码 {proc.returncode}）" + (f"：{tail}" if tail else "")
        elif result.get("is_error"):
            error = str(result.get("result") or result.get("subtype") or "出错了")[:600]
        if result:
            cost = result.get("total_cost_usd")
            if not live["text"].strip() and result.get("result"):
                live["text"] = str(result["result"])
    except OSError as exc:
        error = f"启动不了 Claude：{exc}"
    reply = {"role": "assistant", "text": live["text"], "tools": live["tools"][-MAX_TOOLS:], "at": stamp(),
             "seconds": round(time.time() - live["t0"]), **({"error": error} if error else {}), **({"cost": cost} if cost is not None else {})}
    _update(cid, lambda c: (c["messages"].append(reply), c.update(updated_at=reply["at"])), data)
    _live.pop(cid, None)


def _cwd():
    from . import assistant
    return str(assistant.root())


def _popen(args, cwd, env):
    return subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)


def send(text, cid=None, *, data=None, spawn=None, start=None):
    """发一条消息：新对话不带 cid。立刻返回对话 id，后台跑 Claude。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("消息是空的")
    if len(text) > MAX_TEXT:
        raise ValueError(f"消息太长（{len(text)} 字），最多 {MAX_TEXT} 字")
    if not program().exists():
        raise ValueError(f"找不到 {program()}：对话要用中台的沙箱启动 Claude，没有它不启动")
    with _lock:
        chats = _load(data)
        if cid is None:
            cid = "c" + secrets.token_hex(6)
            chats[cid] = {"id": cid, "title": text.splitlines()[0][:40], "created_at": stamp(), "session": "", "messages": []}
        elif cid not in chats:
            raise KeyError("没有这个对话")
        if cid in _live:
            raise ValueError("上一条还在回答，等它答完或先停止")
        chat = chats[cid]
        chat["messages"].append({"role": "user", "text": text, "at": stamp()})
        chat["updated_at"] = stamp()
        _save(chats, data)
        _live[cid] = {"proc": None, "text": "", "tools": [], "started_at": stamp(), "t0": time.time(), "stop": ""}
    job = threading.Thread(target=_run, args=(cid, text, chat.get("session") or "", data, spawn or _popen), daemon=True)
    (start or (lambda t: t.start()))(job)
    return {"id": cid}


def stop(cid):
    live = _live.get(cid)
    if not live:
        return {"ok": True, "running": False}
    live["stop"] = "你停止了这次回答"
    if live.get("proc"):
        live["proc"].kill()
    return {"ok": True, "running": True}


def save_draft(cid, draft, projects, *, data=None):
    """把回复里的一张 issue 草稿存进「新需求」：只存本机草稿，确认创建仍在抽屉里点。"""
    from . import issue_drafts
    chat = _load(data).get(cid)
    if not chat:
        raise KeyError("没有这个对话")
    if not isinstance(draft, dict):
        raise ValueError("草稿要写成 JSON 对象")
    payload = {"repo": draft.get("repo") or "", "title": draft.get("title") or "", "body": draft.get("body") or "",
               "source": {"kind": "chat", "chat": f"工作台对话 · {chat.get('title', '')}"[:100], "chat_key": f"chat:{cid}"}}
    result = issue_drafts.save([payload], projects, data=data)
    if result["errors"]:
        raise ValueError(result["errors"][0]["error"])
    if result["skipped"]:
        return {"ok": True, "saved": False, "why": result["skipped"][0]["why"]}
    return {"ok": True, "saved": True, "id": result["saved"][0]["id"]}
