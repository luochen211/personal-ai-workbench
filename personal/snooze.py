"""暂时不处理（用户 2026-11-18 要求）：收件箱里要你处理的条目（待你回、草稿待确认、待动手、待你看）点「暂时不处理」，
就从这些筛选拿走、落「搁置」，不计入导航角标，也不能交给 Agent（交给接口拒绝，提示先恢复）。

只记在工作台本机运行数据 .personal/snoozed.json，不写 GitHub（不加标签、不评论、不关 issue）。按 owner/repo#编号 一条：
  {subject, at: 设定时间, until: 到点回来的时间（空串是「有新动静再说」）, their_at: 设定时对方最后一条动静的时间（没有是空串）}

何时回来（check）：
- 对方有新动静：别人（不是你、不是机器人）的留言、评审、行内意见或 PR 新提交，时间晚于设定时记下的 their_at
  （没记下就和设定时间比）。用列表已经读到的时间线判断，不另发 GitHub 请求。
- 选了「1 天后」「1 周后」的，到点也回来（对方先有新动静就先回来）。
- 在搁置里点「恢复」（restore）立刻回来。
回来之后记录留着也不再生效，下次设定时覆盖；到点的旧记录在下次写入时顺手删掉。
微信的行（personal/plugins/wechat/inbox.py，用户 2026-11-19 要求）也用这里：键是 wechat:<类>:<会话>，their_at 是那一行最新一条消息的时间，
这个会话有更新的消息就回来。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from . import agent_watch
from .reply_drafts import KEY

FILE = "snoozed.json"
WAITS = {"news": None, "1d": timedelta(days=1), "1w": timedelta(days=7)}  # 页面上的三个选项：有新动静再说、1 天后、1 周后
MAX_ITEMS = 50
WECHAT = re.compile(r"wechat:[a-z_]+:[0-9a-f]{6,40}")  # 微信的行（personal/wechat.KEY）
REFUSED = "这条设为暂时不处理了，先在收件箱「搁置」里点「恢复」再交给 Agent"


def valid(subject):
    """owner/repo#编号，或微信的 wechat:<类>:<会话>。"""
    return bool(KEY.fullmatch(subject) or WECHAT.fullmatch(subject))


def path_of(data):
    return Path(data) / FILE


def now():
    return datetime.now().astimezone()


def parse(value):
    try:
        return agent_watch.parse_time(value) if value else None
    except (TypeError, ValueError):
        return None


def _load(path):
    raw = agent_watch.read_json(path, {})
    items = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), dict) else {}
    return {"items": {k.lower(): v for k, v in items.items()
                      if isinstance(v, dict) and valid(k) and parse(v.get("at"))}}


def load(data):
    """{owner/repo#N（小写）: 记录}；没给运行数据目录（测试、离线）就是空的。"""
    return _load(path_of(data))["items"] if data else {}


def check(records, subject, their_at="", at_now=None):
    """这一条现在是不是暂时不处理：是就返回 {at, until, line}，不是（没设、到点、对方有新动静）返回 None。
    their_at 是列表已读到的对方最后一条动静的时间。"""
    record = (records or {}).get((subject or "").lower())
    if not record:
        return None
    at_now = at_now or now()
    until = parse(record.get("until"))
    if until and at_now >= until:
        return None
    base = parse(record.get("their_at")) or parse(record.get("at"))
    latest = parse(their_at)
    if latest and base and latest > base:
        return None
    return {"at": record["at"], "until": record.get("until") or "", "line": line(record)}


def line(record):
    """行上那一句：「你 11-18 设为暂时不处理」，选了时长的后面写几号几点回来。"""
    at = parse(record.get("at")).astimezone()
    until = parse(record.get("until"))
    back = f"，{until.astimezone():%m-%d %H:%M} 回来" if until else ""
    return f"你 {at:%m-%d} 设为暂时不处理{back}"


def refusal(data, subject, their_at="", at_now=None):
    """交给接口的把关：暂时不处理中的返回拒绝原因，否则空串。"""
    return REFUSED if check(load(data), subject, their_at, at_now) else ""


def put(data, items, wait="news", at=None):
    """设为暂时不处理。items 是 [{subject, their_at}]（their_at 是页面那一行读到的对方最后一条动静的时间，可空）。
    wait 是 news（有新动静再说）、1d、1w。返回 {snoozed: [subject], until}。"""
    if wait not in WAITS:
        raise ValueError("回来的时间只能选：有新动静再说、1 天后、1 周后")
    if not isinstance(items, list) or not items:
        raise ValueError("请至少选一条")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"一次最多 {MAX_ITEMS} 条")
    wanted = {}
    for raw in items:
        subject = str((raw or {}).get("subject") if isinstance(raw, dict) else raw or "").strip()
        if not valid(subject):
            raise ValueError(f"写法不对：{subject[:80]}（要写 owner/repo#编号）")
        their = str((raw or {}).get("their_at") or "") if isinstance(raw, dict) else ""
        wanted[subject.lower()] = {"subject": subject, "their_at": their if parse(their) else ""}
    at = at or now()
    stamp = at.isoformat(timespec="seconds")
    until = (at + WAITS[wait]).isoformat(timespec="seconds") if WAITS[wait] else ""
    with agent_watch.locked(path_of(data), _load) as book:
        for old in [k for k, v in book["items"].items() if parse(v.get("until")) and parse(v["until"]) <= at]:
            del book["items"][old]
        for low, item in wanted.items():
            book["items"][low] = {**item, "at": stamp, "until": until}
    return {"snoozed": [w["subject"] for w in wanted.values()], "until": until}


def restore(data, subjects):
    """恢复：删掉这些条的记录，立刻回到原来的类别。返回 {restored: [subject]}。"""
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("请至少选一条")
    lows = {str(s).strip().lower() for s in subjects if isinstance(s, str)}
    with agent_watch.locked(path_of(data), _load) as book:
        gone = [book["items"].pop(k)["subject"] for k in list(book["items"]) if k in lows]
    return {"restored": gone}


def apply(row, records, at_now=None):
    """PR 的行（pr_feedback.view）：暂时不处理中的 state 改成 hold，带上 snooze（from 是原来的 state）。
    已合并或关闭（done）的不改。"""
    if row.get("state") == "done":
        return row
    found = check(records, row["subject"], row.get("their_last") or row.get("their_at") or "", at_now)
    if found:
        row["snooze"] = {**found, "from": row["state"]}
        row["state"] = "hold"
    return row
