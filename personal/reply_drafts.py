"""回复草稿：收件箱里点「帮我回复」交给 Agent 后，Agent 不直接用用户账号发评论，而是把每条要发的回复写成草稿，
存在本机运行数据 .personal/reply-drafts.json（按 owner/repo#编号 一条）；用户在收件箱「草稿待确认」里看、改，
点「确认发送」才由工作台用用户账号发出（用户 2026-11-18 要求）。代码改动、推送、开 PR 仍由 Agent 自动做，不在这里。

一条草稿：
  {subject, kind: issue | pr, created_at, work: Agent 做了的代码改动或推送（给人看，不发出去）,
   based_on: 生成草稿时对方最后一条评论 {id, at, who}（没有就是 null）, based_error: 当时没读到讨论的原因,
   replies: [{id, to: 回复给谁, body, review_comment_id: PR 行内评审线程首条意见的 id（没有就是普通评论）, path}],
   error: 上次发送失败的原因}
history 记最近发出、丢弃、重新生成的（列表用它判断 Agent 那一轮的草稿已经有了结果）。

写草稿：Agent 运行技能里的 scripts/reply_draft.py save <草稿.json>（见 main），保存时读一次讨论记下 based_on。
确认发送（send）：发之前重读一次讨论，对方在 based_on 之后又有新评论就先不发，告诉页面「对方又回了 N 条」，
用户选仍发送（force）或重新生成；逐条发，失败的那条和后面的留在草稿里（已发出的去掉，重试不会重复发），全部发出才删草稿。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from pathlib import Path

from . import agent_watch, github
from .upstream import LOGIN

ROOT = Path(__file__).resolve().parents[1]
FILE = "reply-drafts.json"
KEY = re.compile(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]{0,8})")
MAX_REPLIES = 20
MAX_BODY = 60000  # GitHub 评论上限 65536 字符，留点余量
HISTORY = 100
# 回复里不能出现的：AI 工具的名字和署名行（发给人的回复只写事实）
AI_WORDS = re.compile(r"claude|anthropic|chatgpt|openai|copilot|codex|co-authored-by|generated with|AI ?生成|由 ?AI", re.I)
SHOWN = 5  # 过时提示里最多列这么多条对方的新评论

_sending_lock = threading.Lock()
_sending = set()


def path_of(data=None):
    """草稿文件：data 是运行数据目录（工作台的 .personal）；不给就用本仓库的 .personal。"""
    return Path(data or ROOT / ".personal") / FILE


def now():
    return datetime.now().astimezone()


def stamp(at=None):
    return (at or now()).isoformat(timespec="seconds")


def _load(path):
    data = agent_watch.read_json(path, {})
    data = data if isinstance(data, dict) else {}
    drafts = data.get("drafts") if isinstance(data.get("drafts"), dict) else {}
    history = data.get("history") if isinstance(data.get("history"), list) else []
    return {"drafts": {k: v for k, v in drafts.items() if isinstance(v, dict) and KEY.fullmatch(k)},
            "history": [h for h in history if isinstance(h, dict)]}


def load(data=None):
    return _load(path_of(data))


@contextmanager
def book(data=None):
    """草稿文件的读-改-写，加文件锁：Agent 写草稿和页面发送、丢弃同时发生也不会互相覆盖。"""
    with agent_watch.locked(path_of(data), _load) as found:
        yield found


def find(drafts, subject):
    """按 owner/repo#编号 找草稿（不分大小写）：返回 (文件里的写法, 草稿) 或 (None, None)。"""
    low = subject.lower()
    return next(((k, v) for k, v in drafts.items() if k.lower() == low), (None, None))


def parse_subject(subject):
    found = KEY.fullmatch((subject or "").strip()) if isinstance(subject, str) else None
    if not found:
        raise ValueError(f"写法不对：{str(subject)[:80]}（要写 owner/repo#编号）")
    return found[1], int(found[2])


def mentions_ai(text):
    return bool(AI_WORDS.search(text or ""))


def check_body(body, where="回复"):
    if not isinstance(body, str) or not body.strip():
        raise ValueError(f"{where}是空的")
    if len(body) > MAX_BODY:
        raise ValueError(f"{where}太长（{len(body)} 字），GitHub 一条评论放不下")
    if mentions_ai(body):
        raise ValueError(f"{where}里提到了 AI 工具或署名（{AI_WORDS.search(body)[0]}），删掉再发")
    return body.strip()


def validate(payload):
    """Agent 交来的草稿 → 存进文件的样子；写得不对抛 ValueError（说清哪里不对）。"""
    if not isinstance(payload, dict):
        raise ValueError("草稿要写成 JSON 对象")
    repo, number = parse_subject(payload.get("subject"))
    kind = payload.get("kind")
    if kind not in ("issue", "pr"):
        raise ValueError("kind 要写 issue 或 pr")
    replies = payload.get("replies")
    if not isinstance(replies, list) or not replies:
        raise ValueError("replies 至少要有一条回复；不用回复就不要写草稿")
    if len(replies) > MAX_REPLIES:
        raise ValueError(f"一条草稿最多 {MAX_REPLIES} 条回复")
    out = []
    for n, item in enumerate(replies, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {n} 条回复要写成对象")
        body = check_body(item.get("body"), f"第 {n} 条回复")
        target = item.get("review_comment_id")
        if target is not None:
            if kind != "pr":
                raise ValueError(f"第 {n} 条：只有 PR 的行内评审回复才写 review_comment_id")
            if isinstance(target, bool) or not str(target).isdigit():
                raise ValueError(f"第 {n} 条：review_comment_id 要写评审意见的数字 id")
            target = int(target)
        out.append({"id": f"r{n}", "to": str(item.get("to") or "").strip()[:100], "body": body,
                    "review_comment_id": target, "path": str(item.get("path") or "")[:300] if target else ""})
    work = payload.get("work") or ""
    if not isinstance(work, str):
        raise ValueError("work 要写成字符串")
    return {"subject": f"{repo}#{number}", "kind": kind, "work": work.strip()[:4000], "replies": out}


# ---- 读讨论：对方（不是你、不是机器人）的评论 ----

def _lines(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def gh_read(args):
    return github.run(args, "rest:reply-drafts", timeout=60)


def thread(subject, kind, *, read=None):
    """这条 issue 或 PR 下对方的评论（主楼评论；PR 再加行内评审意见和带文字的评审）：[{id, who, at, text, url}]，旧的在前。
    你的、机器人的不算。读不到抛 RuntimeError / github.RateLimited。"""
    from .pr_feedback import is_bot
    repo, number = parse_subject(subject)
    read = read or gh_read
    pick = '{id, who: .user.login, type: .user.type, at: .created_at, text: .body, url: .html_url}'
    found = _lines(read(["gh", "api", "--paginate", f"repos/{repo}/issues/{number}/comments", "--jq", f".[] | {pick}"]))
    if kind == "pr":
        found += _lines(read(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/comments", "--jq", f".[] | {pick}"]))
        reviews = _lines(read(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/reviews", "--jq",
                               '.[] | {id, who: .user.login, type: .user.type, at: .submitted_at, text: .body, url: .html_url, state}']))
        found += [r for r in reviews if r.get("state") not in ("PENDING", "DISMISSED") and r.get("at")
                  and ((r.get("text") or "").strip() or r.get("state") == "CHANGES_REQUESTED")]
    out = []
    for c in found:
        who = c.get("who") or ""
        if not c.get("at") or not who or who.lower() == LOGIN.lower():
            continue
        if is_bot({"__typename": "Bot" if c.get("type") == "Bot" else "User", "login": who}):
            continue
        out.append({"id": str(c.get("id") or ""), "who": who, "at": c["at"], "text": " ".join((c.get("text") or "").split())[:200],
                    "url": c.get("url") or ""})
    return sorted(out, key=lambda c: agent_watch.parse_time(c["at"]))


def latest(items):
    return {"id": items[-1]["id"], "at": items[-1]["at"], "who": items[-1]["who"]} if items else None


def newer(draft, items):
    """生成草稿之后对方的新评论：晚于 based_on（没有就晚于草稿生成时间）。"""
    base = (draft.get("based_on") or {}).get("at") or draft.get("created_at")
    seen = (draft.get("based_on") or {}).get("id")
    if not base:
        return []
    at = agent_watch.parse_time(base)
    return [c for c in items if agent_watch.parse_time(c["at"]) > at and c["id"] != seen]


# ---- 写草稿（Agent 用） ----

def save(payload, *, data=None, read=None, at=None):
    """存一条草稿（同一条 owner/repo#编号 已有草稿就换成新的）；保存时读一次讨论记下对方最后一条评论。"""
    draft = validate(payload)
    try:
        based, error = latest(thread(draft["subject"], draft["kind"], read=read)), ""
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        based, error = None, f"生成草稿时没读到讨论：{str(exc)[:200]}"
    draft.update(created_at=stamp(at), based_on=based, based_error=error, error="")
    with book(data) as found:
        old, _ = find(found["drafts"], draft["subject"])
        if old:
            del found["drafts"][old]
        found["drafts"][draft["subject"]] = draft
    return draft


# ---- 页面用：列出、发送、丢弃、重新生成 ----

def listing(data=None):
    """收件箱要的草稿：新的在前。"""
    drafts = sorted(load(data)["drafts"].values(), key=lambda d: d.get("created_at") or "", reverse=True)
    return {"drafts": drafts}


def marks(data=None):
    """{owner/repo#编号（小写）: 最近一次草稿生成、发出、丢弃或重新生成的时刻}：列表用它判断 Agent 那一轮写的草稿有了着落，
    回执说回复了、GitHub 上却还没有你的评论，不算「去看一眼」。"""
    found = load(data)
    out = {}
    for d in found["drafts"].values():
        out[d["subject"].lower()] = max(out.get(d["subject"].lower(), ""), d.get("created_at") or "")
    for h in found["history"]:
        key = str(h.get("subject") or "").lower()
        if key:
            out[key] = max(out.get(key, ""), h.get("created_at") or "", h.get("at") or "")
    return out


def pending(data=None):
    """现在有草稿的 owner/repo#编号（小写）。"""
    return {d["subject"].lower() for d in load(data)["drafts"].values()}


def _remember(found, draft, how, at):
    found["history"].insert(0, {"subject": draft["subject"], "kind": draft.get("kind"), "how": how, "at": stamp(at),
                                "created_at": draft.get("created_at") or ""})
    del found["history"][HISTORY:]


def why(message):
    """gh 的报错 → 给人看的中文原因。"""
    text = message or ""
    if "gh auth login" in text or "not logged" in text.lower() or "HTTP 401" in text:
        return "gh 没登录或登录过期，先在终端运行 gh auth login"
    if "HTTP 404" in text or "Not Found" in text or "Could not resolve" in text:
        return "GitHub 上找不到这条（可能已删除，或这个账号没有权限）"
    if "locked" in text.lower():
        return "这条讨论已锁定，不能再发言"
    if "HTTP 403" in text:
        return "这个账号没有权限在这里发言"
    if "HTTP 422" in text:
        return "GitHub 拒收：要回复的评审意见不存在或已不能回复"
    if github.transient(text) or "timeout" in text.lower() or "could not connect" in text.lower():
        return "连不上 GitHub，稍后再发"
    return f"GitHub 返回：{' '.join(text.split())[:200] or '没有说明'}"


def gh_send(args, body):
    """发一条：正文从标准输入给 gh，不经命令行参数。"""
    return github.run(args, "write:reply-drafts", timeout=60, runner=partial(subprocess.run, input=body))


def command(draft, reply):
    """发这条回复的 gh 命令和标准输入：行内评审回复回到线程首条意见上，其余是 issue / PR 下的普通评论。"""
    repo, number = parse_subject(draft["subject"])
    if reply.get("review_comment_id"):
        return (["gh", "api", "--method", "POST", f"repos/{repo}/pulls/{number}/comments/{reply['review_comment_id']}/replies",
                 "--input", "-"], json.dumps({"body": reply["body"]}, ensure_ascii=False))
    verb = "pr" if draft["kind"] == "pr" else "issue"
    return ["gh", verb, "comment", str(number), "--repo", repo, "--body-file", "-"], reply["body"]


@contextmanager
def sending(subject):
    low = subject.lower()
    with _sending_lock:
        if low in _sending:
            raise ValueError(f"{subject} 正在发送，等这一次结束")
        _sending.add(low)
    try:
        yield
    finally:
        with _sending_lock:
            _sending.discard(low)


def edited(draft, bodies):
    """页面改过的正文换进草稿（按回复 id）；改空了、提到 AI 工具的抛 ValueError。"""
    bodies = bodies or {}
    if not isinstance(bodies, dict):
        raise ValueError("bodies 要写成 {回复 id: 正文}")
    replies = []
    for n, reply in enumerate(draft["replies"], 1):
        body = bodies.get(reply["id"], reply["body"])
        replies.append({**reply, "body": check_body(body, f"第 {n} 条回复")})
    return {**draft, "replies": replies}


def _update(data, subject, change):
    with book(data) as found:
        key, current = find(found["drafts"], subject)
        if current is not None:
            change(found, key, current)


def send(subject, bodies=None, *, force=False, data=None, read=None, post=None, at=None):
    """确认发送这条草稿。返回：
    {ok: True, sent: N}：全部发出，草稿删了；
    {ok: False, stale: N, newer: [...]}：对方在草稿生成后又回了 N 条，这次没发（改过的正文存回草稿）；
    {ok: False, sent: K, error: 原因}：第 K+1 条发送失败，已发出的从草稿里去掉，其余和原因留在草稿里。
    草稿不在、改空了、提到 AI 工具的抛 ValueError。"""
    parse_subject(subject)
    with sending(subject):
        key, draft = find(load(data)["drafts"], subject)
        if draft is None:
            raise ValueError(f"{subject} 没有待确认的回复草稿（可能已经发出或丢弃了），刷新看看")
        draft = edited(draft, bodies)

        def keep(found, key, current):  # 把页面改过的正文存回去，下次打开还在
            found["drafts"][key] = {**current, "replies": draft["replies"]}

        if not force:
            try:
                items = thread(draft["subject"], draft["kind"], read=read)
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                _update(data, subject, keep)
                return {"ok": False, "sent": 0, "error": f"发送前没读到这条讨论（{why(str(exc))}），这次没发；可以稍后再发，或选仍发送"}
            fresh = newer(draft, items)
            if fresh:
                _update(data, subject, keep)
                return {"ok": False, "sent": 0, "stale": len(fresh), "newer": fresh[-SHOWN:][::-1],
                        "error": f"对方又回了 {len(fresh)} 条，草稿可能过时"}
        post = post or gh_send
        done = 0
        for reply in draft["replies"]:
            args, body = command(draft, reply)
            try:
                post(args, body)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
                reason = why(str(exc))
                left = draft["replies"][done:]

                def failed(found, key, current):
                    found["drafts"][key] = {**current, "replies": left, "error": reason}
                _update(data, subject, failed)
                return {"ok": False, "sent": done, "error": f"第 {done + 1} 条发送失败：{reason}"
                        + (f"（前 {done} 条已发出，已从草稿里去掉）" if done else "") + "，草稿还在"}
            done += 1

        def finished(found, key, current):
            del found["drafts"][key]
            _remember(found, current, "sent", at)
        _update(data, subject, finished)
    github.expire("involved")  # 页面下次读到你刚发的评论，这一行回到等对方
    return {"ok": True, "sent": done}


def discard(subject, *, data=None, at=None):
    parse_subject(subject)
    with book(data) as found:
        key, draft = find(found["drafts"], subject)
        if draft is None:
            raise ValueError(f"{subject} 没有待确认的回复草稿")
        del found["drafts"][key]
        _remember(found, draft, "discarded", at)
    return {"ok": True}


def regenerate(projects, subject, *, data=None, handoff=None, at=None, mode=None):
    """重新生成：照原来的流程再交给 Agent（PR 走 PR 反馈，issue 走 issue 留言），交出去了才删旧草稿。
    mode 给了就按它交（草稿里的「改用处理并回复」给 handle：只回复那一轮说要改代码）；没给照上一次交给的模式。"""
    from . import reply_mode
    mode = reply_mode.mode_of(mode) if mode else None
    parse_subject(subject)
    key, draft = find(load(data)["drafts"], subject)
    if draft is None:
        raise ValueError(f"{subject} 没有待确认的回复草稿")
    if handoff is None:
        from . import conversation, pr_feedback
        # 照上一次交给的模式（帮我回复只写草稿 / 处理并回复）再交；没有记录的按处理并回复
        mode = mode or last_mode(pr_feedback.ledger() if draft["kind"] == "pr" else conversation.ledger(), draft["subject"])
        handoff = (lambda s: pr_feedback.handoff(projects, [s], mode=mode, data=data)) if draft["kind"] == "pr" \
            else (lambda s: conversation.handoff(projects, [s], mode=mode, data=data))
    result = handoff(draft["subject"])  # 交不出去抛 ValueError，草稿留着
    with book(data) as found:
        key, current = find(found["drafts"], subject)
        if current is not None:
            del found["drafts"][key]
            _remember(found, current, "regenerated", at)
    return {"ok": True, "handed": result.get("handed", []), "routed": result.get("routed", ""), "error": result.get("error", "")}


def last_mode(ledger, subject):
    """交给记录里这一条上次的模式（reply_mode：reply / handle）；没有记录或旧记录没写是 None（按处理并回复）。"""
    entry = next((v for k, v in (ledger or {}).items() if k.lower() == subject.lower()), None) or {}
    return entry.get("mode") or None


# ---- 命令行（技能里的 scripts/reply_draft.py 转到这里） ----

def main(argv=None):
    parser = argparse.ArgumentParser(description="把回复写成草稿，等用户在工作台确认后再发（不发 GitHub 评论）")
    sub = parser.add_subparsers(dest="command", required=True)
    put = sub.add_parser("save", help="存一条草稿：参数是草稿 JSON 文件，- 是标准输入")
    put.add_argument("file")
    show = sub.add_parser("show", help="看一条草稿")
    show.add_argument("subject")
    args = parser.parse_args(argv)
    if args.command == "show":
        _, draft = find(load()["drafts"], args.subject)
        print(json.dumps(draft, ensure_ascii=False, indent=1) if draft else f"{args.subject} 没有草稿")
        return 0 if draft else 1
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        draft = save(json.loads(raw))
    except (ValueError, OSError) as exc:
        print(f"没存上：{exc}", file=sys.stderr)
        return 1
    print(f"已存草稿 {draft['subject']}：{len(draft['replies'])} 条回复，等用户在工作台确认后发出"
          + (f"（{draft['based_error']}）" if draft["based_error"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
