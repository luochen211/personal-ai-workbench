"""Agent 上一轮已经有结论、之后没有新动静的条目，不再交给 Agent（用户 2026-11-19 定）。

起因：运行记录里同一条 issue 一天被派了 3～6 次，每次 Agent 都写「没有要改的、剩下要你定」。
Agent 交回「要你定」或「不用动」以后，这条仍落在待你回或待动手、带着「交给 Agent」，一键全交就又派了一轮。

只看这条最近一轮已结束（中台 done / nothing）的结论：
- 待你定（decide）：回执结果行是 BLOCKED，或还有没答的【问你】提问。
- 不用动（nothing）：「处理 issue」的 NO_CHANGE，「处理 issue 留言」「处理 PR 反馈」的 NO_ACTION，或中台记成 nothing（没事可做）。
开了 PR、推送、回复、关闭、结果不明、没跑完的都不在这里管，照原来的流程。

新动静（fresh）：别人（不是你、不是机器人）在这一轮开始之后有留言、评审或提交，或你在这一轮结束之后又留了言（比如回答了它的问题）。
有新动静就照原来的归类；没有就不出「交给 Agent」：待你定落「待你回」，行上写 Agent 要你定的那一句；不用动落「待你看」，
行上写 Agent 的结论。交给接口（单条、批量、PR）拒绝并说明原因，详情里留「仍交给 Agent」（force）兜底；
监看（agent_watch.repeats）对这样又交来、又不是 force 的同样不派。

「帮我回复」那一轮说要改代码（needs_handle）：技能约定回执第一行写「这条要改代码，要动手请在工作台用『处理并回复』再交一次」，
草稿和详情里据此出一键「改用处理并回复」。
"""
from __future__ import annotations

import re
from datetime import datetime

DECIDE, NOTHING = "decide", "nothing"
FINISHED = {"done", "nothing"}  # 中台认为跑完了的轮次
MOVED = {"pr", "pushed", "closed", "replied"}  # 这几种结果动了手，由各流程原来的规则管
QUIET = {"no_change", "no_action"}
NEEDS_CODE = re.compile(r"改代码[^\n]{0,40}处理并回复")
LINE = 120  # 行上那一句最多的字数
TIER = re.compile(r"\s*【(?:问你|默认过)】\s*")  # 提问标题开头的档位，行上不写


def parse(at):
    try:
        found = datetime.fromisoformat(str(at or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.astimezone()


def after(first, second):
    """first 晚于 second（Z 和 +08:00 两种写法都认）；有一个空着或读不懂就不算。"""
    a, b = parse(first), parse(second)
    return bool(a and b and a > b)


def clip(text, limit=LINE):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def asks(cards):
    """还在等你定的提问：【默认过】（Agent 已按推荐做了）和通知不算。"""
    return [c for c in cards or [] if c.get("kind") == "ask" and c.get("tier") != "default"]


def verdict(run_, cards=(), *, flow=""):
    """最近一轮的结论：{kind: decide / nothing, run, rule, flow, line, since, finished_at}；不是这两种（或还没跑完）是 None。
    cards 是这条还开着的提问和通知（issues.autoloop_state 的 waiting）；since 是这一轮开始的时刻（领走时刻，没有用创建时刻）。"""
    if not run_ or run_.get("state") not in FINISHED:
        return None
    result = run_.get("result") or {}
    summary = run_.get("summary") or ""
    waiting = asks(cards)
    if waiting:
        kind, line = DECIDE, TIER.sub("", waiting[0].get("title") or "", count=1) or summary
    elif result.get("kind") in MOVED:
        return None
    elif result.get("kind") == "blocked":
        kind, line = DECIDE, result.get("reason") or summary
    elif result.get("kind") in QUIET or run_.get("state") == "nothing":
        notice = next((c.get("title") for c in cards or [] if c.get("kind") != "ask" and c.get("title")), "")
        kind, line = NOTHING, result.get("reason") or summary or notice
    else:
        return None
    return {"kind": kind, "run": run_.get("id"), "rule": run_.get("rule") or "", "flow": flow, "line": clip(line) or "没写原因",
            "since": run_.get("claimed_at") or run_.get("created_at") or "", "finished_at": run_.get("finished_at") or ""}


def fresh(found, *, their_at="", mine_at=""):
    """这一轮之后有没有新动静：别人的动静晚于这一轮开始，或你的留言晚于这一轮结束。"""
    return after(their_at, found["since"]) or after(mine_at, found["finished_at"])


def holding(found, *, their_at="", mine_at=""):
    """有结论、之后又没有新动静：返回结论（这条不该再派）；否则 None。"""
    return found if found and not fresh(found, their_at=their_at, mine_at=mine_at) else None


def when(at):
    found = parse(at)
    return found.astimezone().strftime("%m-%d %H:%M") if found else ""


def refusal(found):
    """交给接口拒绝时的说明。"""
    at = when(found.get("finished_at"))
    head = f"Agent 第 {found['run']} 轮" + (f"（{at} 结束）" if at else "")
    if found["kind"] == DECIDE:
        what = f"{head}的结论是要你定：{found['line']}"
        how = "先回答它的提问或在 GitHub 上回复，再交给 Agent"
    else:
        what = f"{head}已经看过，结论是不用动：{found['line']}"
        how = "有人回复、评审或提交后会自动放开"
    return f"{what}。之后没有新的留言、评审或提交，再派一轮也是同样的结论；{how}，确实要再派请在详情里点「仍交给 Agent」"


def needs_handle(run_, entry):
    """「帮我回复」（只回复）的那一轮说这条要改代码：{run, at, line}；不是是 None。
    entry 是这条的交给记录（带 mode）；交给记录比这一轮新（又交了一次、还没跑）的不算。"""
    entry = entry or {}
    if not run_ or run_.get("state") not in FINISHED or entry.get("mode") != "reply":
        return None
    if after(entry.get("handed_at"), run_.get("created_at")):
        return None
    summary = run_.get("summary") or ""
    if not NEEDS_CODE.search(summary):
        return None
    return {"run": run_.get("id"), "at": run_.get("finished_at") or "", "line": clip(summary, 200)}
