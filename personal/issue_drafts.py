"""issue 草稿：从微信聊天里提炼出的待办、需求，先拟成 GitHub issue 草稿存在本机运行数据 issue-drafts.json（权限 600），
用户在收件箱的「新需求」抽屉里看、改、选仓库，点「创建 issue」才由工作台用用户账号 gh issue create（用户 2026-11-20 要求）。
工作台不读微信、不调用模型：草稿由抽取脚本经命令行 save 交进来（见 main），这里只存、列、确认后创建。

一条草稿：
  {id, dedupe_key, source: {chat_key: 会话的键（只在本机，不给页面）, chat: 会话名, kind},
   excerpt: [{time, from, text}] 依据的几条原话（只在本机数据文件和本机页面里给用户对照，不进 issue、日志、提交）,
   message_at, project_id, repo（空是未归类，用户在页面里选）, candidates: [项目 id], title, body, labels,
   status: pending | failed | skipped, created_at, error: 上次创建失败的原因,
   skip_reason（skipped 才有）: 模型为什么没拟成草稿,
   new_repo: {name, description}（可选，和 repo 二选一）：对应不到现有仓库的需求，提议连仓库一起建；
   repo_created: "owner/name"（可选）：这条草稿已经建好的仓库，再点创建不会重复建}
创建了、选了不创建的从 drafts 里去掉（原话一起丢掉），记进 history（{dedupe_key, status: created | rejected, …}）：
同一个 dedupe_key 再交来就跳过，同一件事不重复拟稿。

没拟的话题（用户 2026-11-20 要求）：抽取脚本把模型认为和开发相关、但按规则没拟成草稿的话题也交来（skipped: true 加 skip_reason），
存成 status = skipped，不算待评审、不进按钮上的数字，列在抽屉底部「没拟的话题」：用户点「补成草稿」（promote）就变成待评审的草稿，
点「不要」和不创建一样记进 history。这样游标推过去以后，模型漏判的事也还看得见。

发到 GitHub 的只有 title、body、labels；标题和正文里带着原话（excerpt 里 12 个字以上的一条整句）的拒收，改写成需求再创建。
不打 agent 标签：创建之后要不要交给 Agent，由用户在收件箱里另点。
新建仓库（用户 2026-11-20 要求）：草稿带 new_repo，或用户在页面里选「新建仓库」，确认后先 gh repo create（一律私有，建在 OWNER 名下），
登记进数据目录的 projects.local.toml，再在新仓库里开这条 issue。和创建 issue 一样，不确认不建。
来源插件（现在只有微信，personal/plugins/wechat/needs.py）经钩子参与：needs.prepare（发事件之前刷新微信明文库）、
needs.project_of（草稿没写仓库时按会话登记找项目）、needs.created（未归类的草稿选了仓库并创建后记住会话 → 项目）。没装就不调。

做完后 @ 对方（用户 2026-11-20 要求）：微信里的名字 → GitHub 账号 记在 wechat-people.json（600，只在本机）。创建时页面交来
这条要 @ 的账号（没交就照记下的对应，按原话的发言人取），记进 history 那条的 mention。后台每 MENTION_EVERY 秒看一遍这些 issue
（mention_round）：关闭且原因是完成的，用用户账号在 issue 下发一条固定写法的评论 @ 这些账号（MENTION_TEXT）；关闭原因是不做的不发；
用户自己已经在评论里 @ 过的不重复发。发没发、没发成的原因记回 history。

扫描：页面点「读新需求」→ start_scan 记 issue-drafts-scan.json（running），在后台先让来源插件准备（prepare_sources，微信是刷新明文库：
中台派出的进程读不了微信的数据目录，工作台进程有这个权限，所以刷新在这里做），再按来源发事件：
每个来源一条本机事件：录音 recordings.issues.scan → 中台规则「录音拟 issue」（agent_watch.recording_changes，由 install 加）跑助理目录的录音脚本；
微信 wechat.issues.scan → 规则「微信拟 issue」（微信插件装上时加）跑 personal/plugins/wechat/extract.py。都带 --no-refresh；
脚本 save 草稿、scan-done --source <来源> 交回条数，按来源记进 issue-drafts-scan.json，scan_state 合成一份给页面。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import tomllib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from . import agent_watch, github, plugins, projects_config, reply_drafts, upstream

ROOT = Path(__file__).resolve().parents[1]
FILE = "issue-drafts.json"
SCAN = "issue-drafts-scan.json"
RECORDINGS_ENV = "RECORDING_ISSUES_CONFIG"
RECORDINGS_CONFIG = "~/.local/state/wechat-issues/recordings.json"  # 助理仓库的 recording_issues.py 读同一个文件
AUDIO_DIR = "/Volumes/Luochen/LCS/assistant/资料/raw/录音"  # recordings.json 没写 audio_dir 时的投放目录（和那个脚本的默认值一样）
AUDIO_TYPES = (".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mp4", ".mov", ".webm")
MAX_AUDIO = 500 * 1024 * 1024
AUDIO_SETTLED = 120  # 抽取脚本不读一分钟内改过的文件（可能还在拷）；上传是写完才改名的，把时间往前拨这么多秒
RECORDINGS = ("getnote", "lark", "audio")  # 录音来的草稿的 source.kind（助理仓库 scripts/recording_issues.py）
PEOPLE = "wechat-people.json"  # 微信里的名字 → GitHub 账号（做完后 @ 谁）
LOGIN = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}")  # GitHub 账号名
MAX_MENTION = 10
MENTION_EVERY = 10 * 60  # 隔这么久看一遍创建过、还没 @ 的 issue 做完没有
MENTION_TEXT = "{who} 这条已做完{change}，麻烦看一下。"
ISSUE_URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/([1-9][0-9]{0,8})")
SCAN_LIMIT = 15 * 60  # 一轮扫描跑这么久还没交回结果，算超时
HISTORY = 500  # 去重靠它，留得比回复草稿久
MAX_TITLE = 200
MAX_EXCERPT = 20
MAX_LINE = 500
MAX_LABELS = 10
QUOTE = 12  # 原话里这么长的一条整句出现在标题或正文里，算带了聊天原文
OPEN = ("pending", "failed")
SKIPPED = "skipped"
MAX_REASON = 100
OWNER = upstream.LOGIN  # 新仓库建在这个账号名下，一律私有
REPO_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}")  # GitHub 仓库名：字母数字 . _ -，1 到 100 字，不以 . 开头
MAX_DESCRIPTION = 200
NEW_GROUP = "其他"  # 新建的仓库登记到这个分组，之后在 projects.local.toml 里改
ID = re.compile(r"d[0-9a-f]{12}")

_creating_lock = threading.Lock()
_creating = set()


def path_of(data=None, name=FILE):
    return Path(data or ROOT / ".personal") / name


def stamp(at=None):
    return (at or datetime.now().astimezone()).isoformat(timespec="seconds")


def _private(path):
    """只本人可读写：里面有聊天原话。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load(path):
    data = agent_watch.read_json(path, {})
    data = data if isinstance(data, dict) else {}
    drafts = data.get("drafts") if isinstance(data.get("drafts"), dict) else {}
    history = data.get("history") if isinstance(data.get("history"), list) else []
    return {"drafts": {k: v for k, v in drafts.items() if isinstance(v, dict) and ID.fullmatch(k)},
            "history": [h for h in history if isinstance(h, dict)]}


def load(data=None):
    return _load(path_of(data))


@contextmanager
def book(data=None):
    """草稿文件的读-改-写，加文件锁；写完权限 600。"""
    path = path_of(data)
    with agent_watch.locked(path, _load) as found:
        yield found
    _private(path)


# ---- 能开 issue 的项目 ----

def eligible(projects):
    """登记表里能开 issue 的项目：有 GitHub 仓库，没归档、没暂停（自己的、有推送权限的、上游的都收，用户 2026-11-20 定）。
    返回 [{id, name, repo}]，按登记表顺序。"""
    out = []
    for p in projects or []:
        repo = (p.get("github") or "").strip()
        if not repo or p.get("archived") is True or p.get("paused") is True:
            continue
        out.append({"id": p["id"], "name": p.get("name") or p["id"], "repo": repo})
    return out


def _project_for(repo, allowed):
    low = (repo or "").strip().lower()
    found = next((p for p in allowed if p["repo"].lower() == low), None)
    if found is None:
        raise ValueError(f"{repo} 不是登记表里能开 issue 的仓库（没登记、已归档或已暂停）")
    return found


# ---- 校验 ----

def _text(value, limit):
    return " ".join(str(value or "").split())[:limit]


def squash(text):
    return re.sub(r"\s+", "", text or "")


def quoted(excerpt, *texts):
    """标题或正文里带着的原话（excerpt 里够长的一条，去掉空白后整句出现）；没有是空串。"""
    whole = squash("\n".join(texts))
    for line in excerpt or []:
        said = squash(line.get("text"))
        if len(said) >= QUOTE and said in whole:
            return line.get("text") or ""
    return ""


def check_title(title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError("标题是空的")
    title = " ".join(title.split())
    if len(title) > MAX_TITLE:
        raise ValueError(f"标题太长（{len(title)} 字），不超过 {MAX_TITLE} 字")
    if reply_drafts.mentions_ai(title):
        raise ValueError(f"标题里提到了 AI 工具或署名（{reply_drafts.AI_WORDS.search(title)[0]}），删掉再创建")
    return title


def check_text(title, body, excerpt):
    """发到 GitHub 的标题和正文：不空、不超长、不提 AI 工具、不带聊天原话。"""
    title, body = check_title(title), reply_drafts.check_body(body, "正文")
    said = quoted(excerpt, title, body)
    if said:
        raise ValueError(f"标题或正文里带着聊天原话（「{said[:20]}…」），改写成需求描述再创建；原话只留在本机")
    return title, body


def check_labels(labels):
    if labels is None:
        return []
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ValueError("labels 要写成字符串数组")
    out = list(dict.fromkeys(x.strip() for x in labels if x.strip()))
    if len(out) > MAX_LABELS:
        raise ValueError(f"labels 最多 {MAX_LABELS} 个")
    if any(x.lower() == "agent" for x in out):
        raise ValueError("草稿不带 agent 标签：创建之后要不要交给 Agent，在收件箱里另点")
    return out


def check_new_repo(value, excerpt=None):
    """提议新建的仓库 → {name, description}；没提议（None、空）是 None；写得不对抛 ValueError。"""
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict):
        raise ValueError("new_repo 要写成 {name, description}")
    name = value.get("name")
    if not isinstance(name, str) or not REPO_NAME.fullmatch(name.strip()):
        raise ValueError("新仓库的名字只能用字母、数字和 . _ -，1 到 100 个字，不以 . 开头")
    name = name.strip()
    if name.lower().endswith(".git"):
        raise ValueError("新仓库的名字不能以 .git 结尾")
    description = value.get("description") or ""
    if not isinstance(description, str):
        raise ValueError("新仓库的说明要写成字符串")
    description = " ".join(description.split())
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f"新仓库的说明太长（{len(description)} 字），不超过 {MAX_DESCRIPTION} 字")
    for label, text in (("名字", name), ("说明", description)):
        if reply_drafts.mentions_ai(text):
            raise ValueError(f"新仓库的{label}里提到了 AI 工具或署名（{reply_drafts.AI_WORDS.search(text)[0]}），删掉再创建")
    said = quoted(excerpt, description)
    if said:
        raise ValueError(f"新仓库的说明里带着聊天原话（「{said[:20]}…」），改写再创建；原话只留在本机")
    return {"name": name, "description": description}


def dedupe_of(payload, source, title):
    given = payload.get("dedupe_key")
    if given is not None:
        if not isinstance(given, str) or not given.strip() or len(given) > 200:
            raise ValueError("dedupe_key 要写成 200 字以内的字符串")
        return given.strip()
    seed = "|".join([source["chat_key"] or source["chat"], str(payload.get("message_at") or ""), squash(title).lower()])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def validate(payload, projects):
    """抽取脚本交来的一条 → 存进文件的样子；写得不对抛 ValueError。"""
    if not isinstance(payload, dict):
        raise ValueError("草稿要写成 JSON 对象")
    allowed = eligible(projects)
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source = {"chat_key": _text(src.get("chat_key"), 200), "chat": _text(src.get("chat"), 100), "kind": _text(src.get("kind"), 20) or "wechat"}
    raw = payload.get("excerpt") or []
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        raise ValueError("excerpt 要写成 [{time, from, text}]")
    excerpt = [{"time": _text(x.get("time"), 40), "from": _text(x.get("from"), 60), "text": str(x.get("text") or "").strip()[:MAX_LINE]}
               for x in raw[:MAX_EXCERPT] if str(x.get("text") or "").strip()]
    title, body = check_text(payload.get("title"), payload.get("body"), excerpt)
    repo, project_id = _text(payload.get("repo"), 200), ""
    new_repo = check_new_repo(payload.get("new_repo"), excerpt)
    if new_repo and repo:
        raise ValueError("repo 和 new_repo 二选一：开在现有仓库就不写 new_repo，要新建仓库就把 repo 留空")
    if repo:
        found = _project_for(repo, allowed)
        repo, project_id = found["repo"], found["id"]
    ids = {p["id"] for p in allowed}
    wanted = payload.get("candidates") or []
    if not isinstance(wanted, list):
        raise ValueError("candidates 要写成项目 id 的数组")
    candidates = list(dict.fromkeys(x for x in wanted if isinstance(x, str) and x in ids))[:10]
    key = dedupe_of(payload, source, title)
    skipped = payload.get("skipped") is True
    return {"id": "d" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], "dedupe_key": key, "source": source, "excerpt": excerpt,
            "message_at": _text(payload.get("message_at"), 40), "project_id": project_id, "repo": repo, "candidates": candidates,
            "title": title, "body": body, "labels": check_labels(payload.get("labels")), "status": SKIPPED if skipped else "pending", "error": "",
            **({"new_repo": new_repo} if new_repo else {}),
            **({"skip_reason": _text(payload.get("skip_reason"), MAX_REASON) or "没有说明"} if skipped else {})}


# ---- 做完后 @ 谁：微信里的名字 → GitHub 账号 ----

def people(data=None):
    """记下的对应 {微信里的名字: GitHub 账号}。"""
    found = agent_watch.read_json(path_of(data, PEOPLE), {})
    return {str(k): v for k, v in found.items() if isinstance(v, str) and LOGIN.fullmatch(v)} if isinstance(found, dict) else {}


def senders(draft):
    """这条草稿原话的发言人（照出现的顺序，不重复）。"""
    return list(dict.fromkeys(x.get("from") for x in draft.get("excerpt") or [] if x.get("from")))


def check_login(value):
    login = str(value or "").strip().lstrip("@")
    if login and not LOGIN.fullmatch(login):
        raise ValueError(f"「{login[:40]}」不是 GitHub 账号名：只能用字母、数字和中间的 -")
    return login


def check_mention(value, draft, data=None):
    """这条做完后 @ 谁。value 是页面交来的 {微信里的名字: GitHub 账号}（名字得是这条草稿原话的发言人，账号留空是不 @、也不再记），
    没交（None）就照记下的对应。返回 (要 @ 的账号, 要记下或去掉的对应)；不 @ 自己。"""
    names, known = senders(draft), people(data)
    if value is None:
        pairs = {n: known[n] for n in names if n in known}
    elif not isinstance(value, dict):
        raise ValueError("mention 要写成 {微信里的名字: GitHub 账号}")
    else:
        strange = [str(n)[:40] for n in value if n not in names]
        if strange:
            raise ValueError(f"「{strange[0]}」不是这条草稿里的发言人")
        pairs = {n: check_login(v) for n, v in value.items()}
    logins = list(dict.fromkeys(v for v in pairs.values() if v and v.lower() != OWNER.lower()))
    if len(logins) > MAX_MENTION:
        raise ValueError(f"一条 issue 最多 @ {MAX_MENTION} 个账号")
    return logins, ({} if value is None else pairs)


def remember_people(pairs, *, data=None):
    """把页面填的对应记下（留空的去掉），下次同一个人直接带出账号。"""
    if not pairs:
        return
    path = path_of(data, PEOPLE)
    with agent_watch.locked(path, lambda p: people(data)) as found:
        for name, login in pairs.items():
            if login:
                found[name] = login
            else:
                found.pop(name, None)
    _private(path)


# ---- 写草稿（抽取脚本用） ----

def save(payloads, projects, *, data=None, at=None):
    """存一批草稿。已经创建过、选过不创建的（history 里有这个 dedupe_key），和已有的待确认草稿，都跳过（不盖掉用户改过的字）。
    没写仓库、这个会话用户选过项目的，直接对应上（提议新建仓库的不对应）。返回 {saved: [草稿], skipped: [{dedupe_key, why}], errors: [{index, error}]}。"""
    items = payloads if isinstance(payloads, list) else [payloads]
    allowed = {p["id"]: p for p in eligible(projects)}
    saved, skipped, errors, ready = [], [], [], []
    for n, payload in enumerate(items):
        try:
            draft = validate(payload, projects)
        except ValueError as exc:
            errors.append({"index": n, "error": str(exc)})
            continue
        picked = allowed.get(next((p for p in plugins.hook(path_of(data).parent, "needs.project_of", draft["source"], data=data) if p), ""))
        if not draft["repo"] and not draft.get("new_repo") and picked:
            draft.update(repo=picked["repo"], project_id=picked["id"])
        draft["created_at"] = stamp(at)
        ready.append(draft)
    if ready:
        with book(data) as found:
            done = {h.get("dedupe_key"): h.get("status") for h in found["history"]}
            waiting = {d.get("dedupe_key"): d.get("status") for d in found["drafts"].values()}
            for draft in ready:
                key = draft["dedupe_key"]
                if key in done:
                    skipped.append({"dedupe_key": key, "why": "已创建过" if done[key] == "created" else "你选过不创建"})
                elif key in waiting:
                    skipped.append({"dedupe_key": key, "why": "已在没拟的话题里" if waiting[key] == SKIPPED else "已有待确认的草稿"})
                else:
                    found["drafts"][draft["id"]] = draft
                    waiting[key] = draft["status"]
                    saved.append(draft)
    return {"saved": saved, "skipped": skipped, "errors": errors}


# ---- 页面用：列出、创建、不创建 ----

def shown(draft):
    """给页面的一条：会话的键不出数据文件。"""
    return {**draft, "source": {k: v for k, v in draft.get("source", {}).items() if k != "chat_key"}}


def listing(projects, data=None):
    """收件箱要的：待确认的草稿（新的在前）、没拟的话题（skipped，新的在前）和能选的项目。"""
    every = sorted(load(data)["drafts"].values(), key=lambda d: d.get("created_at") or "", reverse=True)
    drafts = [d for d in every if d.get("status") in OPEN]
    known = people(data)
    return {"drafts": [shown(d) for d in drafts], "skipped": [shown(d) for d in every if d.get("status") == SKIPPED],
            "projects": eligible(projects), "scan": scan_state(data), "owner": OWNER,
            "people": {n: known[n] for d in drafts for n in senders(d) if n in known}, "mentions": waiting_mentions(data)}


# ---- 扫描：页面点「读新需求」→ 每个来源发一条本机事件 → 各自的抽取脚本跑完调 scan-done --source ----

# 核心来源：录音（得到、飞书妙记、本机录音文件），抽取脚本在助理目录。插件的来源见 plugins.needs_sources（微信）
CORE_SOURCES = [{"id": "recordings", "label": "录音", "event": agent_watch.RECORDING_EVENT, "rule": agent_watch.RECORDING_RULE,
                 "subject": "recording-issues"}]


def sources(data=None):
    """这一轮能读的来源：核心的录音 + 已装插件的（每条 {id, label, event, rule, subject}）。"""
    found = [dict(s) for s in CORE_SOURCES]
    for src in plugins.needs_sources(path_of(data).parent):
        plugin = next((p for p, info in plugins.KNOWN.items() if src in info.get("needs_sources", [])), "")
        found.append({"label": plugins.KNOWN.get(plugin, {}).get("name", src["id"]), **src})
    return found


def _load_scan(path):
    data = agent_watch.read_json(path, {})
    return data if isinstance(data, dict) else {}


def _count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def scan_state(data=None, *, now=None):
    """{status: idle | running | done | failed, requested_at, finished_at, found, saved, skipped, error, refresh_notes}，
    按来源记的结果合成一份（页面照旧读这一份）：条数相加；有一个来源还在跑就是 running；全部来源都没跑成才算 failed，
    只有一部分没跑成写进 refresh_notes。skipped：这一轮新交来的没拟的话题条数。一个来源 running 超过 15 分钟算它超时失败。
    refresh_notes：哪个来源没读（中台没有它的规则）、哪个微信号的明文库没刷新成、哪个来源没跑成。"""
    found = _load_scan(path_of(data, SCAN))
    notes = [x for x in found.get("refresh_notes", []) if isinstance(x, str)] if isinstance(found.get("refresh_notes"), list) else []
    out = {"status": found.get("status") if found.get("status") in ("running", "done", "failed") else "idle",
           "requested_at": found.get("requested_at") or "", "finished_at": found.get("finished_at") or "",
           "found": 0, "saved": 0, "skipped": 0, "error": found.get("error") or "", "refresh_notes": notes}
    parts = {k: v for k, v in (found.get("sources") or {}).items() if isinstance(v, dict)} if isinstance(found.get("sources"), dict) else {}
    if not parts:
        return out
    try:
        age = ((now or datetime.now().astimezone()) - agent_watch.parse_time(out["requested_at"])).total_seconds()
    except (ValueError, TypeError):
        age = SCAN_LIMIT + 1
    states = []
    for part in parts.values():
        status, error = part.get("status"), part.get("error") or ""
        if status == "running" and age > SCAN_LIMIT:
            status, error = "failed", "15 分钟没交回结果，算超时；看中台这一轮的运行记录"
        states.append((part.get("label") or "", status, error))
        for key in ("found", "saved", "skipped"):
            out[key] += _count(part.get(key))
    finished = [p.get("finished_at") for p in parts.values() if p.get("finished_at")]
    out["finished_at"] = max(finished) if finished else ""
    failed = [(label, error) for label, status, error in states if status == "failed"]
    if any(status == "running" for _, status, _ in states):
        out["status"] = "running"
    elif len(failed) == len(states):
        out.update(status="failed", error=failed[0][1])
    else:
        out.update(status="done", error="")
    if out["status"] != "failed":
        out["refresh_notes"] = notes + [f"{label}没跑成：{error}" for label, error in failed]
    return out


def emit_scan(source, event_id):
    """给一个来源发本机事件（和 agent_watch.emit_check 同一写法），由它的规则接；发不出去抛 RuntimeError。
    subject 只能用字母、数字和 # . _ : / @ -，所以不写中文。"""
    missing = agent_watch.source_rule_missing(source["rule"], source["event"])
    if missing:
        raise RuntimeError(missing)
    try:
        return agent_watch.run(["agent-autoloop", "emit", source["event"], "--id", event_id, "--subject", source["subject"]], timeout=30).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"没连上中台：{exc}") from None


def prepare_sources(data=None):
    """发事件之前让来源插件准备好（微信：刷新要读的明文库），返回没准备成的说明；没装插件就是空的。"""
    return [note for notes in plugins.hook(path_of(data).parent, "needs.prepare", data) for note in notes]


def _mark(path, requested, ids, reason):
    """把这一轮里还在跑的这些来源记成没跑成。"""
    with agent_watch.locked(path, _load_scan) as found:
        if found.get("requested_at") != requested:
            return
        for key in ids:
            part = (found.get("sources") or {}).get(key)
            if isinstance(part, dict) and part.get("status") == "running":
                part.update(status="failed", finished_at=stamp(), error=reason)
    _private(path)


def _scan_job(data, emit, at, refresh, ready):
    """后台：让来源插件准备（刷新微信明文库）→ 记下没准备成的 → 每个来源发一条事件。出什么错都落成 failed 和原因，不悄悄丢。"""
    path, requested = path_of(data, SCAN), stamp(at)
    ids = [s["id"] for s in ready]
    try:
        notes = list((refresh or prepare_sources)(data))
        with agent_watch.locked(path, _load_scan) as found:
            if found.get("requested_at") != requested:
                return  # 这一轮已经被别的结果盖掉（超时后又点了一次）
            found["refresh_notes"] = list(found.get("refresh_notes") or []) + [_text(n, 200) for n in notes if n]
        _private(path)
    except Exception as exc:  # 线程里的错不能丢；只写类型，不写可能带路径和原文的内容
        _mark(path, requested, ids, f"没触发成：刷新微信库时出错（{type(exc).__name__}）")
        return
    for source in ready:
        try:
            (emit or emit_scan)(source, f"scan:{source['id']}:{at.strftime('%Y%m%dT%H%M%S')}")
        except RuntimeError as exc:
            _mark(path, requested, [source["id"]], f"没触发成：{exc}")
        except Exception as exc:
            _mark(path, requested, [source["id"]], f"没触发成：发事件时出错（{type(exc).__name__}）")


def _spawn(job):
    threading.Thread(target=job, daemon=True, name="issue-drafts-scan").start()


def start_scan(*, data=None, emit=None, at=None, refresh=None, spawn=None):
    """页面点了「读新需求」。已有一轮在跑（没超时）就不重复触发，返回当前状态。
    中台接不住的来源（没有它的规则）这一轮不读，写进说明；一个都接不住就记失败。
    立刻返回 running：刷新微信明文库和发事件在后台做（spawn 默认起一个线程，测试里可以换成直接调用）。"""
    at = at or datetime.now().astimezone()
    path = path_of(data, SCAN)
    every = sources(data)
    missing = {s["id"]: agent_watch.source_rule_missing(s["rule"], s["event"]) for s in every} if emit is None else {}
    ready = [s for s in every if not missing.get(s["id"])]
    with agent_watch.locked(path, _load_scan) as found:
        if scan_state(data, now=at)["status"] == "running":
            return scan_state(data, now=at)
        found.clear()
        found.update(requested_at=stamp(at), refresh_notes=[f"{s['label']}没读：{missing[s['id']]}" for s in every if missing.get(s["id"])])
        if ready:
            found.update(status="running", sources={s["id"]: {"label": s["label"], "status": "running"} for s in ready})
        else:
            first = next((missing[s["id"]] for s in every if missing.get(s["id"])), "没有可读的来源")
            found.update(status="failed", finished_at=stamp(at), error=f"没触发成：{first}", refresh_notes=[])
    _private(path)
    if ready:
        (spawn or _spawn)(lambda: _scan_job(data, emit, at, refresh, ready))
    return scan_state(data, now=at)


def scan_done(found_count, saved_count, error="", *, source, skipped=0, data=None, at=None):
    """一个来源的抽取脚本跑完交回结果；skipped 是这一轮新交来的没拟的话题条数。"""
    path = path_of(data, SCAN)
    label = next((s["label"] for s in sources(data) if s["id"] == source), source)
    with agent_watch.locked(path, _load_scan) as found:
        if not isinstance(found.get("sources"), dict):
            found["sources"] = {}
        found.setdefault("requested_at", stamp(at))
        found["sources"][source] = {"label": label, "status": "failed" if error else "done", "finished_at": stamp(at),
                                    "found": int(found_count), "saved": int(saved_count), "skipped": int(skipped), "error": _text(error, 300)}
    _private(path)
    return scan_state(data, now=at)


# ---- 上传录音：存进投放目录，接着读一轮 ----

def audio_dir():
    """录音的投放目录：recordings.json 的 audio_dir（写成空串是关掉了这一种来源，返回 None），没写就是 AUDIO_DIR。"""
    found = agent_watch.read_json(Path(os.environ.get(RECORDINGS_ENV) or RECORDINGS_CONFIG).expanduser(), {})
    value = found.get("audio_dir") if isinstance(found, dict) else None
    if value == "":
        return None
    return Path(value if isinstance(value, str) else AUDIO_DIR).expanduser()


def audio_name(name, at=None):
    """上传的文件名 → 存下来的名字：只留文件名本身，前面加时间；不是音频的抛 ValueError。"""
    base = re.sub(r"[\x00-\x1f/\\:]", "", str(name or "").replace("\\", "/").split("/")[-1]).strip().lstrip(".")
    stem, suffix = os.path.splitext(base)
    if suffix.lower() not in AUDIO_TYPES:
        raise ValueError("只收音频文件：" + "、".join(x[1:] for x in AUDIO_TYPES))
    return f"{(at or datetime.now().astimezone()).strftime('%Y%m%d-%H%M%S')}-{stem[:80] or '录音'}{suffix.lower()}"


def save_audio(name, stream, length, *, data=None, at=None, scan=None):
    """页面上传的一段录音：写进投放目录（先写临时文件再改名），接着读一轮（start_scan），抽取脚本在本机转写。
    返回 {saved: 存下的文件名, scan: 扫描状态, queued: 已有一轮在跑、这段要下一轮才读}。录音不出本机。"""
    folder = audio_dir()
    if folder is None:
        raise ValueError("本机录音这一种来源关掉了（recordings.json 的 audio_dir 是空的）")
    if not 0 < length <= MAX_AUDIO:
        raise ValueError(f"录音文件要在 {MAX_AUDIO // 1024 // 1024} MB 以内")
    final = folder / audio_name(name, at)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{final.name}.{os.getpid()}.part"
    left = length
    try:
        with open(tmp, "wb") as out:
            while left:
                piece = stream.read(min(left, 1 << 20))
                if not piece:
                    raise ValueError("录音没传完，再传一次")
                out.write(piece)
                left -= len(piece)
        old = (at or datetime.now().astimezone()).timestamp() - AUDIO_SETTLED
        os.utime(tmp, (old, old))
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    busy = scan_state(data)["status"] == "running"
    return {"saved": final.name, "scan": (scan or start_scan)(data=data), "queued": busy}


def why(message):
    """gh 的报错 → 给人看的中文原因。"""
    text = message or ""
    if "could not add label" in text.lower() or ("label" in text.lower() and "not found" in text.lower()):
        return "仓库里没有草稿带的标签，去掉标签或先在仓库里建好"
    if "Issues are disabled" in text or "has disabled issues" in text or "HTTP 410" in text:
        return "这个仓库没开 issue"
    return reply_drafts.why(text).replace("在这里发言", "在这里开 issue").replace("稍后再发", "稍后再试")


def gh_create(args, body):
    """正文从标准输入给 gh；gh issue create 是写操作，瞬断不自动重试（github.writes）。"""
    return reply_drafts.gh_send(args, body)


def command(repo, title, labels):
    args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body-file", "-"]
    for label in labels:
        args += ["--label", label]
    return args


@contextmanager
def creating(draft_id):
    with _creating_lock:
        if draft_id in _creating:
            raise ValueError("这条草稿正在创建，等这一次结束")
        _creating.add(draft_id)
    try:
        yield
    finally:
        with _creating_lock:
            _creating.discard(draft_id)


def _remember(found, draft, status, at, **extra):
    found["history"].insert(0, {"id": draft["id"], "dedupe_key": draft.get("dedupe_key"), "status": status, "at": stamp(at),
                                "title": draft.get("title") or "", "repo": draft.get("repo") or "", **extra})
    del found["history"][HISTORY:]


def _gone(found, draft_id):
    past = next((h for h in found["history"] if h.get("id") == draft_id), None)
    if past and past.get("status") == "created":
        return ValueError(f"这条草稿已经创建过了：{past.get('issue_url') or past.get('repo')}，刷新看看")
    return ValueError("没有这条待确认的 issue 草稿（可能已经创建或选了不创建），刷新看看")


def _id(draft_id):
    if not isinstance(draft_id, str) or not ID.fullmatch(draft_id):
        raise ValueError("草稿编号不对")
    return draft_id


# ---- 新建仓库：查有没有、建、登记 ----

def repo_exists(full, *, run=None):
    """GitHub 上有没有这个仓库。查不成（断网、限流）抛 RuntimeError。"""
    try:
        (run or github.run)(["gh", "repo", "view", full, "--json", "nameWithOwner"], "read:repo-view", timeout=60)
        return True
    except github.RateLimited:
        raise
    except RuntimeError as exc:
        text = str(exc)
        if "Could not resolve to a Repository" in text or "HTTP 404" in text or "not found" in text.lower():
            return False
        raise


def repo_command(full, description):
    """一律私有：这里不提供公开的选项。"""
    args = ["gh", "repo", "create", full, "--private"]
    return args + (["--description", description] if description else [])


def gh_repo_create(args):
    """gh repo create 是写操作，瞬断不自动重试（github.writes）。"""
    return github.run(args, "write:repo-create", timeout=60)


def _toml(value):
    return json.dumps(str(value), ensure_ascii=False)  # JSON 的字符串写法也是合法的 TOML 基本字符串


def register_project(full, name, projects, *, data=None):
    """把新仓库登记进数据目录的 projects.local.toml（追加一条 [[projects]]），返回 {id, name, repo}。
    已经登记过这个仓库（上次登记了、issue 没建成）就不再写。写不了抛 OSError / ValueError。"""
    from . import discovery
    path = path_of(data, projects_config.LOCAL_NAME)
    known = list(projects or []) + list(projects_config.overlay(path).get("projects") or [])
    for p in known:
        if (p.get("github") or "").lower() == full.lower():
            return {"id": p["id"], "name": p.get("name") or p["id"], "repo": p["github"]}
    project_id = discovery.slug(full, {p.get("id") for p in known})
    block = "\n".join(["[[projects]]", f"id = {_toml(project_id)}", f"name = {_toml(name)}", f"group = {_toml(NEW_GROUP)}",
                       'repo_path = ""', f"repository = {_toml(full)}", f"github = {_toml(full)}", 'source_status = "GitHub Issues"'])
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    text = old + ("" if not old or old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n") + block + "\n"
    tomllib.loads(text)  # 写之前先确认整份还读得了（读不了抛 ValueError 的子类）
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return {"id": project_id, "name": name, "repo": full}


def create(draft_id, projects, *, title=None, body=None, repo=None, new_repo=None, mention=None, data=None, post=None, at=None,
           view=None, make=None, register=None):
    """确认创建。页面改过的标题、正文、选的仓库先存回草稿，再 gh issue create。返回：
    {ok: True, url, repo, repo_created}：创建了，草稿移进记录；{ok: False, error}：没创建成，草稿和原因留着。
    草稿不在、标题正文不合格、仓库不能开 issue、新仓库的名字不合格的抛 ValueError。

    开在哪：repo（现有的登记仓库）和 new_repo（{name, description}，新建私有仓库）二选一，都不给就用草稿上的。
    新建仓库的顺序：查 OWNER/name 在不在（在且登记过 → 当现有仓库用；在但没登记 → 不建、不开 issue，让用户换名字或先登记）
    → gh repo create --private → 登记进 projects.local.toml → gh issue create。仓库建成后草稿上记 repo_created，
    后面哪一步没成，再点一次都不会重复建仓库。
    mention：这条做完后 @ 谁（check_mention）；创建成了记进 history，对应记进 wechat-people.json。"""
    _id(draft_id)
    if repo and new_repo:
        raise ValueError("开在现有仓库还是新建仓库，二选一")
    with creating(draft_id):
        found = load(data)
        draft = found["drafts"].get(draft_id)
        if draft is None or draft.get("status") not in OPEN:
            raise _gone(found, draft_id)
        new_title, new_body = check_text(draft["title"] if title is None else title, draft["body"] if body is None else body, draft.get("excerpt"))
        logins, pairs = check_mention(mention, draft, data)
        fresh = None if repo else check_new_repo(new_repo, draft.get("excerpt")) or (None if draft.get("repo") else draft.get("new_repo"))
        wanted = "" if fresh else repo or draft.get("repo") or ""
        if not fresh and not wanted:
            raise ValueError("还没选仓库：先选这条要开在哪个仓库，或选新建仓库")
        allowed = eligible(projects)
        unsorted = not draft.get("repo")  # 交来时没归类，是用户在页面里选的仓库
        draft = {k: v for k, v in draft.items() if k != "new_repo"} | {"title": new_title, "body": new_body}
        target = None
        if fresh:
            draft.update(new_repo=fresh, repo="", project_id="")
        else:
            target = _project_for(wanted, allowed)
            draft.update(repo=target["repo"], project_id=target["id"])

        def keep(error=""):
            with book(data) as now:
                if draft_id in now["drafts"]:
                    now["drafts"][draft_id] = {**draft, "status": "failed" if error else draft["status"], "error": error}

        def stop(reason):
            keep(reason)
            return {"ok": False, "error": reason}
        keep()  # 页面改过的字先存回去，创建失败、下次打开还在
        if fresh:
            full = f"{OWNER}/{fresh['name']}"
            if (draft.get("repo_created") or "").lower() != full.lower():
                try:
                    exists = (view or repo_exists)(full)
                except subprocess.TimeoutExpired:
                    return stop("GitHub 没在限定时间内回应，没查成这个仓库名有没有被占用，稍后再试；没有建仓库")
                except (RuntimeError, OSError) as exc:
                    return stop(f"没查成 {full} 在不在：{why(str(exc))}；没有建仓库")
                if exists:
                    target = next((p for p in allowed if p["repo"].lower() == full.lower()), None)
                    if target is None:
                        return stop(f"{full} 已经存在，但没登记在工作台里：换一个名字，或先把它登记进项目表再选它；没有建仓库，也没有开 issue")
                else:
                    try:
                        (make or gh_repo_create)(repo_command(full, fresh["description"]))
                    except subprocess.TimeoutExpired:
                        return stop(f"GitHub 没在限定时间内回应，不确定 {full} 有没有建成：先去 GitHub 看一眼，没有再点创建")
                    except (RuntimeError, OSError) as exc:
                        return stop(f"没建成仓库 {full}：{why(str(exc))}，草稿还在")
                    draft["repo_created"] = full
                    keep()
            if target is None:
                try:
                    target = (register or register_project)(full, fresh["name"], projects, data=data)
                except (OSError, ValueError) as exc:
                    return stop(f"私有仓库 {full} 已经建好，但没登记进工作台（{type(exc).__name__}：{str(exc)[:120]}）；"
                                "再点一次会接着登记并开 issue，不会重复建仓库")
            draft.pop("new_repo", None)
            draft.update(repo=target["repo"], project_id=target["id"])
            keep()  # 仓库已经有了：从这里起再点只开 issue
        try:
            out = (post or gh_create)(command(draft["repo"], draft["title"], draft.get("labels") or []), draft["body"])
        except subprocess.TimeoutExpired:
            return stop("GitHub 没在限定时间内回应，不确定有没有创建成功：先去仓库看一眼，没有再点创建")
        except (RuntimeError, OSError) as exc:
            reason = why(str(exc))
            keep(reason)
            made = f"私有仓库 {draft['repo']} 已经建好；" if draft.get("repo_created") else ""
            return {"ok": False, "error": f"{made}没创建成 issue：{reason}，草稿还在"}
        url = next((line.strip() for line in reversed((out or "").splitlines()) if line.strip().startswith("https://")), "")
        with book(data) as now:
            now["drafts"].pop(draft_id, None)
            _remember(now, draft, "created", at, issue_url=url, **({"repo_created": True} if draft.get("repo_created") else {}),
                      **({"mention": logins} if logins else {}))
    remember_people(pairs, data=data)
    if unsorted:  # 未归类的草稿用户选了仓库：来源插件（微信）记住这个会话 → 项目
        plugins.hook(path_of(data).parent, "needs.created", draft, data=data, at=at)
    github.expire("issues")  # 页面下次读到刚开的这条
    return {"ok": True, "url": url, "repo": draft["repo"], "repo_created": bool(draft.get("repo_created")),
            **({"mention": logins} if logins else {})}


def promote(draft_id, *, data=None, at=None):
    """没拟的话题补成草稿：status 改成 pending，排到待评审的最前面。"""
    _id(draft_id)
    with book(data) as found:
        draft = found["drafts"].get(draft_id)
        if draft is None or draft.get("status") != SKIPPED:
            raise ValueError("没有这条没拟的话题（可能已经补成草稿或选了不要），刷新看看")
        draft.pop("skip_reason", None)
        draft.update(status="pending", error="", created_at=stamp(at))
    return {"ok": True}


def reject(draft_id, *, data=None, at=None):
    """不创建（没拟的话题点「不要」也走这里）：草稿（连同原话）删掉，记下 dedupe_key，同一件事不再拟稿。"""
    _id(draft_id)
    with book(data) as found:
        draft = found["drafts"].pop(draft_id, None)
        if draft is None:
            raise _gone(found, draft_id)
        _remember(found, draft, "rejected", at)
    return {"ok": True}


# ---- 做完后 @ 对方 ----

def _waiting(found):
    return [h for h in found["history"] if h.get("status") == "created" and h.get("mention") and not h.get("mention_done")
            and ISSUE_URL.fullmatch(h.get("issue_url") or "")]


def waiting_mentions(data=None):
    """给页面的：创建过、做完后要 @ 人、还没 @ 的 issue。"""
    return [{"title": h.get("title") or "", "url": h["issue_url"], "logins": h["mention"], "error": h.get("mention_error") or ""}
            for h in _waiting(load(data))]


def issue_state(repo, number):
    """一条 issue 现在的状态、关闭原因、关掉它的 PR 和评论。"""
    out = github.run(["gh", "issue", "view", str(number), "--repo", repo, "--json",
                      "state,stateReason,closedByPullRequestsReferences,comments"], "read:issue-done", timeout=60)
    return json.loads(out)


def mention_text(logins, repo, state):
    """评论的写法是固定的：@ 谁、改动在哪个 PR（有的话）。"""
    refs = []
    for pr in state.get("closedByPullRequestsReferences") or []:
        url = str(pr.get("url") or "")
        if pr.get("number"):
            refs.append(f"#{pr['number']}" if f"github.com/{repo.lower()}/pull/" in url.lower() else url)
    refs = [r for r in refs if r]
    return MENTION_TEXT.format(who=" ".join("@" + x for x in logins), change=f"，改动见 {'、'.join(refs)}" if refs else "")


def mentioned(logins, state):
    """用户自己是不是已经在这条 issue 的评论里 @ 过这些账号（手动 @ 过，或上次发出去了但没等到回应）。"""
    for c in state.get("comments") or []:
        if ((c.get("author") or {}).get("login") or "").lower() != OWNER.lower():
            continue
        named = {m.lower() for m in re.findall(r"@([A-Za-z0-9-]+)", c.get("body") or "")}
        if all(x.lower() in named for x in logins):
            return True
    return False


def mention_round(*, data=None, view=None, post=None, at=None):
    """看一遍创建过、还没 @ 的 issue：关闭且原因是完成的发评论 @；关闭原因是不做的记 skipped，不发；已经 @ 过的记 already。
    还开着的下一轮再看。返回这一轮的结果 [{url, done | error}]。"""
    results = []
    for entry in _waiting(load(data)):
        repo, number = ISSUE_URL.fullmatch(entry["issue_url"]).groups()
        result = {"url": entry["issue_url"]}
        try:
            state = (view or issue_state)(repo, number)
            if state.get("state") != "CLOSED":
                if not entry.get("mention_error"):
                    continue
                result["error"] = ""  # 上一轮没读成、这一轮读到了：去掉旧的原因
            elif (state.get("stateReason") or "COMPLETED") != "COMPLETED":
                result["done"] = "skipped"
            elif mentioned(entry["mention"], state):
                result["done"] = "already"
            else:
                (post or gh_create)(["gh", "issue", "comment", number, "--repo", repo, "--body-file", "-"],
                                    mention_text(entry["mention"], repo, state))
                result["done"] = "sent"
        except github.RateLimited:
            break  # 限流了：这一轮到此为止，下一轮再看
        except subprocess.TimeoutExpired:
            result["error"] = "GitHub 没在限定时间内回应，下一轮再看"  # 发没发出去下一轮从评论里看得出来
        except (RuntimeError, OSError, ValueError) as exc:
            result["error"] = why(str(exc)).replace("在这里开 issue", "在这里评论")
        results.append(result)
    if results:
        by_url = {r["url"]: r for r in results}
        with book(data) as found:
            for h in found["history"]:
                r = by_url.get(h.get("issue_url")) if h.get("status") == "created" and h.get("mention") else None
                if not r:
                    continue
                if r.get("done"):
                    h.update(mention_done=r["done"], mention_at=stamp(at))
                    h.pop("mention_error", None)
                elif r["error"]:
                    h["mention_error"] = r["error"]
                else:
                    h.pop("mention_error", None)
        if any(r.get("done") == "sent" for r in results):
            github.expire("issues")
    return results


def keep_mentions(data, stop, wake=MENTION_EVERY):
    """后台线程：隔一阵看一遍。没有等着 @ 的 issue 时只读一次本机文件，不请求 GitHub。"""
    while not stop.is_set():
        try:
            mention_round(data=data)
        except Exception as exc:  # 线程不能死
            print(f"做完后 @ 对方出错：{type(exc).__name__}", file=sys.stderr, flush=True)
        stop.wait(wake)


def start_mention_keeper(data, stop=None):
    stop = stop or threading.Event()
    thread = threading.Thread(target=keep_mentions, args=(data, stop), daemon=True, name="mention-keeper")
    thread.start()
    return thread, stop


# ---- 命令行（scripts/issue_draft.py 转到这里） ----

def main(argv=None, *, projects=None, data=None):
    parser = argparse.ArgumentParser(description="把提炼出的需求存成 issue 草稿，等用户在工作台确认后再创建（不写 GitHub）")
    sub = parser.add_subparsers(dest="command", required=True)
    put = sub.add_parser("save", help="存草稿：参数是 JSON 文件（一条对象或数组），- 是标准输入")
    put.add_argument("file")
    sub.add_parser("list", help="列出待确认的草稿（不带原话）")
    sub.add_parser("projects", help="能开 issue 的项目")
    end = sub.add_parser("scan-done", help="一个来源的抽取脚本跑完交回结果")
    end.add_argument("--source", required=True, help="来源：recordings、wechat……")
    end.add_argument("--found", type=int, required=True)
    end.add_argument("--saved", type=int, required=True)
    end.add_argument("--skipped", type=int, default=0, help="这一轮新交来的没拟的话题条数")
    end.add_argument("--error", default="")
    sub.add_parser("plugins", help="哪些插件装了（JSON）：抽取脚本据此决定读不读微信")
    args = parser.parse_args(argv)
    if projects is None or data is None:
        from . import __main__ as app
        projects = app.project_config() if projects is None else projects
        data = app.DATA if data is None else data
    if args.command == "list":
        shown_list = listing(projects, data)
        rows = [{k: d.get(k) for k in ("id", "status", "repo", "new_repo", "title", "created_at", "error", "skip_reason") if k not in ("new_repo", "skip_reason") or d.get(k)}
                for d in shown_list["drafts"] + shown_list["skipped"]]
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if args.command == "plugins":
        print(json.dumps(plugins.status(path_of(data).parent), ensure_ascii=False))
        return 0
    if args.command == "scan-done":
        print(json.dumps(scan_done(args.found, args.saved, args.error, source=args.source, skipped=args.skipped, data=data), ensure_ascii=False))
        return 0
    if args.command == "projects":
        print(json.dumps(eligible(projects), ensure_ascii=False, indent=1))
        return 0
    try:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
        result = save(json.loads(raw), projects, data=data)
    except (ValueError, OSError) as exc:
        print(f"没存上：{exc}", file=sys.stderr)
        return 1
    held = sum(1 for d in result["saved"] if d["status"] == SKIPPED)
    print(f"已存 {len(result['saved']) - held} 条 issue 草稿、{held} 条没拟的话题，跳过 {len(result['skipped'])} 条，{len(result['errors'])} 条写得不对；等用户在工作台确认后创建")
    for item in result["skipped"]:
        print(f"跳过 {item['dedupe_key']}：{item['why']}")
    for item in result["errors"]:
        print(f"第 {item['index'] + 1} 条没存：{item['error']}", file=sys.stderr)
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
