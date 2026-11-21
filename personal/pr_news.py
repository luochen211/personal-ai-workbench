"""项目详情页「我的 PR」的新消息和最近动态。

新消息只认已经同步好的 GitHub 通知：通知未读、主题是这个仓库的这个编号，就给那一行 PR 标「N 条新评论」或「有新消息」。
已读状态完全跟 GitHub：点开链接不算读，GitHub 那边读了、下一次通知同步后标记才消失；这里不改 GitHub 上的已读状态。

谁说了什么：对未读的 issue / PR 主题，一个 GraphQL 请求批量读最近的评论和评审，按（主题, 通知更新时间）缓存，
通知没有新动态就不再读；限流时不发请求，只标「有新消息」并写明。新评论条数是通知上次读过（last_read_at）之后
别人留的评论和评审；旧收件记录没有 last_read_at 的不写条数。

最近动态：最新评论或评审、CI 结论、合并或关闭、通知更新时间、PR 更新时间里最晚的那个，都是已经读到的数据，不另发请求。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from datetime import datetime

from . import github
from .notifications import subject_key, subject_link
from .upstream import LOGIN

COMMENTS = 20  # 每个主题读最近这么多条评论
REVIEWS = 10
RETRY = 300  # 读失败的主题 5 分钟内不重读
TOP = 3  # 项目页顶部最多列这么多条未读消息
CHUNK = 30  # 一个 GraphQL 请求最多读这么多个主题的评论和评审
NEAR = 60  # 有说明的动态（谁评论、CI、合并）和最晚时刻相差不到 60 秒，就用有说明的那条
SENTENCE = re.compile(r"[。！？!?\n]|\.(?:\s|$)")
WHAT = {"APPROVED": "批准", "CHANGES_REQUESTED": "要求修改", "COMMENTED": "评审", "DISMISSED": "评审"}
CHECKS = {"SUCCESS": "CI 通过", "FAILURE": "CI 失败", "ERROR": "CI 失败"}  # 运行中的没有结束时间，不当动态

_lock = threading.Lock()
_details = {}  # 'owner/repo#N'（小写）→ {'updated_at': 通知更新时间, 'value': 摘要或 None, 'at': 读取时刻, 'error': ''}


def first_sentence(text, limit=60):
    """正文第一句，压成一行，超过 limit 个字截断加「…」。"""
    text = " ".join((text or "").split())
    found = SENTENCE.search(text)
    if found:
        text = text[:found.end()].strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def summarize(node, last_read_at, *, known=True, login=LOGIN):
    """一个主题最近的评论和评审 → {count, more, who, what, at, text, url}。只算别人留的；
    count 是 last_read_at 之后的条数（known 为假时是 None），more 表示读到的都是新的、可能还有更早的。"""
    node = node or {}
    comments = ((node.get("comments") or {}).get("nodes") or [])
    reviews = ((node.get("reviews") or {}).get("nodes") or [])
    events = [{"who": (c.get("author") or {}).get("login") or "", "what": "评论", "at": c.get("createdAt") or "",
               "text": c.get("bodyText") or "", "url": c.get("url") or ""} for c in comments if c]
    events += [{"who": (r.get("author") or {}).get("login") or "", "what": WHAT.get(r.get("state"), "评审"),
                "at": r.get("submittedAt") or "", "text": r.get("bodyText") or "", "url": r.get("url") or ""}
               for r in reviews if r and r.get("state") != "PENDING"]
    others = sorted((e for e in events if e["at"] and e["who"].lower() != login.lower()), key=lambda e: e["at"])
    new = [e for e in others if not last_read_at or e["at"] > last_read_at] if known else []
    latest = others[-1] if others else None
    return {"count": len(new) if known else None,
            "more": bool(known and new and len(new) >= len(others) and len(comments) >= COMMENTS),
            "who": latest["who"] if latest else "", "what": latest["what"] if latest else "",
            "at": latest["at"] if latest else "", "text": first_sentence(latest["text"]) if latest else "",
            "url": latest["url"] if latest else ""}


def read_threads(keys):
    """读这些主题最近的评论和评审，每 CHUNK 个主题一个 GraphQL 请求、请求里按仓库分组；返回 {key: 节点}。
    限流抛 github.RateLimited，之后的请求不再发。"""
    comment = f"comments(last: {COMMENTS}) {{ nodes {{ author {{ login }} bodyText createdAt url }} }}"
    review = f"reviews(last: {REVIEWS}) {{ nodes {{ author {{ login }} bodyText submittedAt url state }} }}"
    keys, out = sorted(keys), {}
    for start in range(0, len(keys), CHUNK):
        repos = {}
        for key in keys[start:start + CHUNK]:
            repo, number = key.split("#")
            repos.setdefault(repo, []).append(int(number))
        fields = " ".join(
            f'r{i}: repository(owner: {json.dumps(repo.split("/")[0])}, name: {json.dumps(repo.split("/")[1])}) {{ '
            + " ".join(f"s{n}: issueOrPullRequest(number: {n}) {{ __typename ... on PullRequest {{ {comment} {review} }} "
                       f"... on Issue {{ {comment} }} }}" for n in numbers) + " }"
            for i, (repo, numbers) in enumerate(repos.items()))
        data = github.graphql(f"query {{ {fields} }}", kind="graphql:pr-news", partial=True)
        out.update({f"{repo}#{n}": (data.get(f"r{i}") or {}).get(f"s{n}") for i, (repo, numbers) in enumerate(repos.items())
                    for n in numbers})
    return out


def details(threads, *, read=None, now=None):
    """未读主题的「谁说了什么」：先用缓存，通知有新动态或没读过的一次批量读。返回 ({key: 摘要}, 说明)。
    限流时不发请求，说明里写明；读失败的 5 分钟内不重读。"""
    now = now or time.time()
    wanted = {}
    for raw in threads:
        key = subject_key(raw)
        if key and raw.get("unread"):
            wanted[key] = raw
    with _lock:
        stale = [k for k, raw in wanted.items() if not _usable(_details.get(k), raw.get("updated_at"), now)]
        note = ""
        if stale:
            try:
                github.check()
                found = (read or read_threads)(stale)
                for key in stale:
                    raw = wanted[key]
                    _details[key] = {"updated_at": raw.get("updated_at"), "at": now, "error": "",
                                     "value": summarize(found.get(key), raw.get("last_read_at"), known="last_read_at" in raw)}
            except github.RateLimited:
                note = f"GitHub 限流中，{len(stale)} 条新消息没读到是谁说的，先只标「有新消息」"
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                for key in stale:
                    _details[key] = {"updated_at": wanted[key].get("updated_at"), "at": now, "value": None,
                                     "error": str(exc)[:200]}
        out = {k: (_details.get(k) or {}).get("value") for k in wanted}
        failed = [k for k in wanted if (_details.get(k) or {}).get("error")]
    if failed and not note:
        note = f"{len(failed)} 条新消息没读到是谁说的：{_details[failed[0]]['error']}"
    return {k: v for k, v in out.items() if v}, note


def cached(raw):
    """消息页一行的「+N」：只看 details 已经读好的摘要，不发请求；通知已读或还没读到的返回 None。"""
    key = subject_key(raw)
    if not key or not raw.get("unread"):
        return None
    with _lock:
        entry = _details.get(key)
        return entry["value"] if entry and entry.get("updated_at") == raw.get("updated_at") and entry.get("value") else None


def _usable(entry, updated_at, now):
    if not entry or entry.get("updated_at") != updated_at:
        return False
    return entry.get("value") is not None or now - entry.get("at", 0) < RETRY


def news(raw, detail):
    """一行 PR 的新消息标记：通知未读才有。"""
    if not raw or not raw.get("unread"):
        return None
    detail = detail or {}
    return {"count": detail.get("count"), "more": bool(detail.get("more")), "who": detail.get("who", ""),
            "what": detail.get("what", ""), "at": detail.get("at") or raw.get("updated_at") or "",
            "text": detail.get("text", ""), "url": detail.get("url") or subject_link(raw)}


def _seconds(at):
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0.0


def last_activity(pr, raw=None, detail=None):
    """最近动态：最新评论或评审、CI 结论、合并或关闭、创建、通知更新时间、PR 更新时间里最晚的那个。
    返回 {at, who, what}；最晚的那条说不清是谁做了什么时，who 和 what 是空的（页面只写时间）。"""
    labeled = []
    if detail and detail.get("at"):
        labeled.append((detail["at"], detail.get("who", ""), detail.get("what", "")))
    if pr.get("state") == "merged" and pr.get("merged_at"):
        labeled.append((pr["merged_at"], "", "已合并"))
    elif pr.get("state") == "closed" and pr.get("closed_at"):
        labeled.append((pr["closed_at"], "", "已关闭"))
    if pr.get("checks_at") and CHECKS.get(pr.get("checks")):
        labeled.append((pr["checks_at"], "", CHECKS[pr["checks"]]))
    if pr.get("created_at"):
        labeled.append((pr["created_at"], "", "创建"))
    plain = [at for at in ((raw or {}).get("updated_at"), pr.get("updated_at")) if at]
    latest = max([_seconds(at) for at, _, _ in labeled] + [_seconds(at) for at in plain], default=0.0)
    if not latest:
        return {"at": "", "who": "", "what": ""}
    near = [item for item in labeled if _seconds(item[0]) >= latest - NEAR]
    if near:
        at, who, what = max(near, key=lambda item: _seconds(item[0]))
        return {"at": at, "who": who, "what": what}
    return {"at": max(plain, key=_seconds), "who": "", "what": ""}


def order(prs):
    """默认顺序：有未读消息的在前，再按最近动态从新到旧。"""
    return sorted(prs, key=lambda p: (p.get("news") is not None, _seconds((p.get("last") or {}).get("at"))), reverse=True)


def annotate(report, threads, found=None, note=""):
    """给项目 PR 结果加上新消息和最近动态（不改缓存里的原值）：
    - 每行 PR 带 news（通知未读时）和 last（最近动态），各组按 order 排；
    - prs.news：已合并、已关闭里带未读消息的，单独提到「开着」上面（原分组里照样保留，计数不变）；
    - unread：这个项目未读的 issue / PR 消息，最新的在前，最多 TOP 条；unread_total 是总数。"""
    if not report.get("available") or not report.get("prs"):
        return report
    found = found or {}
    repo = report["repo"].lower()
    by_number = {}
    for raw in threads:
        key = subject_key(raw)
        if key and key.split("#")[0] == repo:
            by_number[int(key.split("#")[1])] = raw

    def mark(pr):
        raw = by_number.get(pr["number"])
        detail = found.get(f"{repo}#{pr['number']}") if raw and raw.get("unread") else None
        return {**pr, "news": news(raw, detail), "last": last_activity(pr, raw, detail)}

    prs = {group: order([mark(p) for p in report["prs"].get(group, [])]) for group in ("open", "merged", "closed")}
    prs["news"] = order([p for p in prs["merged"] + prs["closed"] if p["news"]])
    unread = sorted((raw for raw in threads if raw.get("unread") and subject_key(raw)),
                    key=lambda raw: raw.get("updated_at") or "", reverse=True)
    top = []
    for raw in unread[:TOP]:
        key = subject_key(raw)
        item = news(raw, found.get(key))
        top.append({"number": int(key.split("#")[1]), "title": raw["subject"].get("title") or "GitHub 通知",
                    "kind": "pr" if raw["subject"].get("type") == "PullRequest" else "issue", **item})
    top.sort(key=lambda item: _seconds(item["at"]), reverse=True)  # 按页面上写的时间排
    return {**report, "prs": prs, "unread": top, "unread_total": len(unread), "news_note": note}


def attach(report, threads, *, read=None):
    """annotate 加上读「谁说了什么」：只读这个项目未读的主题。"""
    if not report.get("available") or not report.get("prs"):
        return report
    found, note = details(threads, read=read)
    return annotate(report, threads, found, note)


def _item(row, raw, detail):
    """一条未读消息的摘要：项目、PR 或 issue 编号、谁、说了什么的开头一句、时间、GitHub 位置。"""
    key = subject_key(raw)
    mark = news(raw, detail) or {}
    return {"id": row["id"], "project_id": row.get("project_id"), "project_name": row.get("project_name"),
            "repository": row["repository"], "number": int(key.split("#")[1]) if key else None,
            "kind": {"PullRequest": "pr", "Issue": "issue"}.get(raw["subject"].get("type"), "other"),
            "title": row["title"], "category": row["category"], "count": mark.get("count"), "more": mark.get("more", False),
            "who": mark.get("who", ""), "what": mark.get("what", ""), "text": mark.get("text", ""),
            "at": mark.get("at") or row.get("updated_at") or "", "url": mark.get("url") or row["url"]}


def digest(pairs, *, read=None):
    """GitHub 消息的摘要，给主页「按项目」和「按 issue」用：
    - projects：每个项目的未读条数、最近一条未读（谁、在哪个 PR 或 issue、说了什么）、最近一条消息的时间（含已读）；
    - latest：最近 TOP 条未读；
    - unread：未读总数（不算已归档项目的，和导航徽标一致）。
    pairs 是 [(页面行, 原始通知)]。谁说了什么读全部未读的 issue / PR 主题（已归档项目的不读；批量、缓存，限流不发请求），
    消息页每行的「+N」用同一份（见 cached）。"""
    pairs = sorted(pairs, key=lambda pair: pair[0].get("updated_at") or "", reverse=True)
    unread = [(row, raw) for row, raw in pairs if row["unread"] and row.get("classification") != "archived"]
    projects = {}
    for row, raw in pairs:
        pid = row.get("project_id")
        if pid and row.get("classification") == "workspace":
            entry = projects.setdefault(pid, {"unread": 0, "latest_at": row.get("updated_at") or "", "latest": None, "_pair": None})
            if row["unread"]:
                entry["unread"] += 1
                if entry["_pair"] is None or (not subject_key(entry["_pair"][1]) and subject_key(raw)):
                    entry["_pair"] = (row, raw)
    found, note = details([raw for _, raw in unread], read=read)
    for entry in projects.values():
        pair = entry.pop("_pair")
        if pair:
            entry["latest"] = _item(pair[0], pair[1], found.get(subject_key(pair[1]) or ""))
    latest = sorted((_item(row, raw, found.get(subject_key(raw) or "")) for row, raw in unread[:TOP]),
                    key=lambda item: _seconds(item["at"]), reverse=True)  # 按页面上写的时间排
    return {"projects": projects, "latest": latest, "unread": len(unread), "note": note}


def reset():
    """测试用。"""
    with _lock:
        _details.clear()
