"""「助理」页的「学习」标签：只读学习课题目录，例外只有两处写——保存某一篇的学习反馈，和把「写下一篇」交给 Agent。

学习根目录默认两个：/Volumes/Luochen/学习资料/学习课题，和 NAS 上黄执中的 /Volumes/公共/百度网盘下载/黄执中/学习课题；
环境变量 LEARNING_ROOT 可覆盖（多个用 : 隔开）。课题按根目录顺序列，同名的只认前一个。每个根目录的真源是它的 INDEX.md
（一张表「课题 | 当前进度 | 最近更新 | 下一步」）和每个课题目录里的 NN.md / NN.html、00-学习计划.md、index.html；
这里不另存进度。

反馈的位置照技能 dbs-learning 和现有课题的约定：写在那一篇 NN.md 末尾「## 学习反馈」区「请写在这行下面：」之后
（_tools/build_learning.py 生成 HTML 时也从这里取）。保存只改这一段，整篇换名写入，不动 HTML。

「写下一篇」只在页面上点了才交：记进 ~/.agent-autoloop/issues/learning-handoffs.json（和别的交给记录同一套加锁账本），
再发一次 issues.check 触发监看；监看每轮把一天内交给的作为 learning.next 事件交给中台（中台按 id 去重，只写一次），
规则「写学习文章」用技能 dbs-learning 写下一篇、生成 HTML、更新 INDEX.md。不定时、不自动写。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from . import agent_watch
from .errors import APIError

DEFAULT_ROOTS = ("/Volumes/Luochen/学习资料/学习课题", "/Volumes/公共/百度网盘下载/黄执中/学习课题")
# 根目录旁边可被课题页引用的目录（黄执中的学习页用 ../../导读/img/ 配图、链回课程导读）
SHARED = ("导读",)
INDEX = "INDEX.md"
PLAN = "00-学习计划.md"
HOME = "index.html"
PLAN_ANCHOR = "学习目标"  # build_learning.py 把学习计划放在课题首页，第一个小节是「学习目标」
ARTICLE = re.compile(r"([0-9]{2})\.md")
NUMBER = re.compile(r"[0-9]{2}")
SECTION = "\n## 学习反馈"
MARK = "请写在这行下面："
# 反馈区的默认提示（技能 dbs-learning「反馈提取规则」）：不算用户写的反馈
PROMPTS = {"你可以写：", "请写在这行下面：", "1. 哪里看懂了？", "2. 哪里没看懂？", "3. 哪个地方想展开？",
           "4. 这个主题和你的真实问题有什么关系？"}
FEEDBACK_TEMPLATE = ("\n\n---\n\n## 学习反馈\n\n你可以写：\n\n1. 哪里看懂了？\n2. 哪里没看懂？\n3. 哪个地方想展开？\n"
                     "4. 这个主题和你的真实问题有什么关系？\n\n请写在这行下面：\n")
FEEDBACK_LIMIT = 20000
STATIC = {".html": "text/html", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".webp": "image/webp", ".svg": "image/svg+xml", ".css": "text/css"}
STATIC_LIMIT = 30 << 20
# 静态页放进沙箱：脚本照跑，但没有工作台的来源，不能借本机身份调接口
SANDBOX = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-downloads"

RULE = agent_watch.LEARNING_RULE
SKILL = agent_watch.LEARNING_SKILL
RESULT_LINE = re.compile(r"RESULT: (?:ARTICLE=([0-9]{2})|BLOCKED=(.*\S.*))")
LOST_AFTER = timedelta(minutes=15)  # 交给后这么久还没有新的一轮：派发可能丢了，可以再交


def roots() -> list[Path]:
    value = os.environ.get("LEARNING_ROOT")
    return [Path(p) for p in (value.split(os.pathsep) if value else DEFAULT_ROOTS) if p]


def root() -> Path:
    """第一个学习根目录：中台规则「写学习文章」的工作目录。"""
    return roots()[0]


def inside(base: Path, path: Path) -> bool:
    """path 不是符号链接，并且解析后仍在学习根目录里面。"""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def version(path: Path) -> str:
    """文件的修改时间（纳秒）：保存反馈时用来发现「这篇在你打开后又被改过」。"""
    return str(path.stat().st_mtime_ns)


def parse_index(text: str) -> list[dict]:
    """INDEX.md 里「课题 | 当前进度 | 最近更新 | 下一步」那张表，原文照搬每一格。"""
    rows, header = [], None
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            header = None if rows else header
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if cells and cells[0] == "课题":
                header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        row = dict(zip(header, cells + [""] * (len(header) - len(cells))))
        name = re.sub(r"^\[([^\]]+)\]\([^)]*\)$", r"\1", row.get("课题", "")).strip().rstrip("/")
        if name:
            rows.append({"name": name, "progress": row.get("当前进度", ""), "updated": row.get("最近更新", ""),
                         "next": row.get("下一步", "")})
    return rows


def feedback_of(markdown: str) -> str:
    """一篇文章里用户写的反馈：「学习反馈」区「请写在这行下面：」之后的内容；没有这一行就取整个区，去掉默认提示。"""
    head, found, section = markdown.partition(SECTION)
    if not found:
        return ""
    if MARK in section:
        return section.split(MARK, 1)[1].strip()
    body = section.split("\n", 1)[1] if "\n" in section else ""
    return "\n".join(line for line in body.splitlines() if line.strip() not in PROMPTS).strip()


def with_feedback(markdown: str, text: str) -> str:
    """把反馈写进文章：换掉「请写在这行下面：」之后的内容；没有反馈区就照技能的模板补一个。其余一字不动。"""
    text = text.replace("\r\n", "\n").strip()
    tail = f"\n\n{text}\n" if text else "\n"
    head, found, section = markdown.partition(SECTION)
    if found and MARK in section:
        before = section.split(MARK, 1)[0]
        return f"{head}{SECTION}{before}{MARK}{tail}"
    if found:
        return f"{markdown.rstrip()}\n\n{MARK}{tail}"
    return markdown.rstrip() + FEEDBACK_TEMPLATE + tail


def title_of(path: Path, number: str) -> str:
    """文章标题取 md 的一级标题，去掉开头的「NN｜」。"""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if line.startswith("# "):
                    title = line[2:].strip()
                    return re.sub(rf"^{number}\s*[｜|]\s*", "", title) or title
                if line.strip():
                    break
    except OSError:
        pass
    return ""


def articles(base: Path, folder: Path) -> list[dict]:
    out = []
    for path in sorted(folder.iterdir()):
        found = ARTICLE.fullmatch(path.name)
        if not found or found[1] == "00" or not path.is_file() or not inside(base, path):
            continue
        number = found[1]
        text = path.read_text(encoding="utf-8", errors="replace")
        feedback = feedback_of(text)
        page = folder / f"{number}.html"
        out.append({"no": number, "title": title_of(path, number), "html": page.is_file() and inside(base, page),
                    "feedback": feedback, "filled": bool(feedback), "version": version(path)})
    return out


def topics_of(base: Path) -> tuple[list[dict], str]:
    index = base / INDEX
    if not (base.is_dir() and index.is_file() and inside(base, index)):
        return [], f"找不到学习索引：{index}（可用环境变量 LEARNING_ROOT 指定学习根目录）"
    return parse_index(index.read_text(encoding="utf-8", errors="replace")), ""


def folder_of(base: Path, name: str) -> Path | None:
    """INDEX.md 里登记了、目录也真的在根目录下的课题；别的一律不认。"""
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    if name not in {t["name"] for t in topics_of(base)[0]}:
        return None
    folder = base / name
    return folder if folder.is_dir() and inside(base, folder) and folder.parent.resolve() == base.resolve() else None


def base_of(name: str) -> Path | None:
    """课题登记在哪个根目录：按顺序第一个 INDEX.md 里有它的。"""
    for base in roots():
        if isinstance(name, str) and name in {t["name"] for t in topics_of(base)[0]}:
            return base
    return None


def locate(name: str) -> tuple[Path, Path] | tuple[None, None]:
    """课题的 (根目录, 课题目录)；没登记或目录不在返回 (None, None)。"""
    base = base_of(name)
    folder = folder_of(base, name) if base else None
    return (base, folder) if folder else (None, None)


def subject(name: str) -> str:
    """交给记录的键，也是中台事件的 subject。中台只收字母、数字和 # . _ : / @ -，课题名是中文，所以用课题名的摘要；
    课题名本身写在交给记录的 topic 和 detail 里。"""
    return "learning/" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]


def ledger_path() -> Path:
    return agent_watch.learning_ledger_path()


def parse_result(text):
    """回执最后一个非空行：RESULT: ARTICLE=<两位篇号> / BLOCKED=<原因>。别的一律不认。"""
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    found = RESULT_LINE.fullmatch(lines[-1]) if lines else None
    if not found:
        return None
    if found[1]:
        return {"kind": "article", "no": found[1]}
    return {"kind": "blocked", "reason": found[2].strip()[:300]}


def runs():
    """中台里「写学习文章」每个课题最近一轮；读不到时返回 ({}, 原因)。"""
    from . import issues
    auto, error = issues.autoloop_state(rule=RULE, parse=parse_result)
    return (auto or {}).get("runs", {}), error


def status(entry: dict | None, run: dict | None, at) -> dict:
    """「写下一篇」的进度：idle 没交过；pending 已交给、还没派出；lost 交给超过 15 分钟没有新的一轮；
    running 在写；done 写完；blocked 写不成；failed 没跑完；unknown 跑完但回执没写结果行。"""
    entry = entry if isinstance(entry, dict) else {}
    handed = entry.get("handed_at")
    no = entry.get("no") or ""
    from . import issues
    if run and run.get("state") in issues.ACTIVE:
        return {"state": "running", "no": no, "handed_at": handed, "run": run["id"]}
    try:
        newer = not run or agent_watch.parse_time(run["created_at"]) < agent_watch.parse_time(handed)
    except (TypeError, ValueError, KeyError):
        newer = False
    if handed and newer:
        try:
            lost = at - agent_watch.parse_time(handed) > LOST_AFTER
        except (TypeError, ValueError):
            lost = True
        return {"state": "lost" if lost else "pending", "no": no, "handed_at": handed}
    if not run:
        return {"state": "idle", "no": "", "handed_at": None}
    result = run.get("result") or {}
    kind = result.get("kind")
    state = {"article": "done", "blocked": "blocked", "failed": "failed"}.get(kind, "unknown")
    return {"state": state, "no": result.get("no") or no, "handed_at": handed, "run": run["id"],
            "reason": result.get("reason") or run.get("error") or "", "finished_at": run.get("finished_at")}


BUSY = {"pending", "running"}


def view(*, at=None, read_runs=None) -> dict:
    """学习标签的数据：INDEX.md 每个课题、它已写的各篇和反馈、「写下一篇」的进度。default 是最近更新的那个课题。"""
    at = at or agent_watch.now()
    listed, errors, seen = [], [], set()
    for base in roots():
        rows, error = topics_of(base)
        if error:
            errors.append(error)
        for row in rows:
            if row["name"] not in seen:
                seen.add(row["name"])
                listed.append((base, row))
    payload = {"root": str(root()), "roots": [str(b) for b in roots()], "topics": [], "default": "",
               "error": "；".join(errors), "hub_error": ""}
    if not listed:
        return payload
    book = agent_watch.load_handoffs(ledger_path())
    found, hub_error = (read_runs or runs)() if book else ({}, "")
    for base, row in listed:
        folder = folder_of(base, row["name"])
        topic = {**row, "exists": folder is not None, "path": f"{base.name}/{row['name']}",
                 "articles": [], "next_no": "", "home": False, "plan": False,
                 "unfilled": [], "handoff": status(book.get(subject(row["name"])), found.get(subject(row["name"])), at)}
        if folder is not None:
            items = articles(base, folder)
            last = max((int(a["no"]) for a in items), default=0)
            topic.update(articles=items, next_no=f"{last + 1:02d}" if last < 99 else "",
                         home=(folder / HOME).is_file() and inside(base, folder / HOME),
                         plan=(folder / PLAN).is_file() and inside(base, folder / PLAN),
                         unfilled=[a["no"] for a in items if not a["filled"]])
        payload["topics"].append(topic)
    if book and hub_error:
        payload["hub_error"] = hub_error
    dated = [t for t in payload["topics"] if t["exists"]]
    if dated:
        payload["default"] = max(dated, key=lambda t: t["updated"])["name"]
    return payload


def article_path(name: str, number: str) -> Path:
    _, folder = locate(name)
    if folder is None:
        raise APIError(404, "INDEX.md 里没有这个课题，或课题目录不在")
    if not isinstance(number, str) or not NUMBER.fullmatch(number) or number == "00":
        raise ValueError("篇号要写成两位数字，如 07")
    path = folder / f"{number}.md"
    if not (path.is_file() and inside(folder, path)):
        raise APIError(404, f"{name} 没有第 {number} 篇")
    return path


def write_text(path: Path, text: str):
    """写到同目录的临时文件再换名，沿用原文件的权限。"""
    mode = path.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="." + path.name, suffix=".tmp",
                                     delete=False) as stream:
        stream.write(text)
    os.chmod(stream.name, mode)
    Path(stream.name).replace(path)


def save_feedback(body: dict) -> dict:
    """保存一篇的反馈：只改 NN.md「请写在这行下面：」之后那一段。version 对不上（打开后文章又被改过）就不写。"""
    name, number, text = body.get("topic"), body.get("no"), body.get("text")
    if not isinstance(text, str):
        raise ValueError("反馈要写成文字")
    if len(text) > FEEDBACK_LIMIT:
        raise ValueError(f"反馈太长（最多 {FEEDBACK_LIMIT} 字）")
    path = article_path(name, number)
    expected = body.get("version")
    if expected is not None and expected != version(path):
        raise APIError(409, f"第 {number} 篇在你打开后又被改过，刷新后再保存（你写的还在输入框里）")
    current = path.read_text(encoding="utf-8")
    updated = current if feedback_of(current) == text.replace("\r\n", "\n").strip() else with_feedback(current, text)
    if updated != current:
        write_text(path, updated)
    feedback = feedback_of(updated)
    return {"topic": name, "no": number, "feedback": feedback, "filled": bool(feedback), "version": version(path)}


def build_detail(base: Path, row: dict, folder: Path, items: list[dict], number: str) -> str:
    latest = items[-1]["no"] if items else ""
    filled = [a["no"] for a in items if a["filled"]]
    tool = base / "_tools" / "build_learning.py"
    if not tool.is_file():  # 黄执中的生成脚本在根目录上一层的 _tools 里
        tool = base.parent / "_tools" / "build_learning.py"
    lines = [
        f"课题：{row['name']}",
        f"课题目录：{folder}",
        f"学习根目录：{base}（索引 {base / INDEX}）",
        f"要写：第 {number} 篇（{folder / (number + '.md')}），只写这一篇。用户在工作台点了「写下一篇」。",
        f"INDEX.md 这一行的「下一步」（用户写的，作提示）：{row['next'] or '（空）'}",
        f"已写 {len(items)} 篇，最新一篇 {latest or '无'}；已填反馈的：{'、'.join(filled) or '无'}。",
        "",
        f"做法：按技能 {SKILL} 的 Phase 2 到 Phase 5 写。先读 00-学习计划.md，课题目录和 _tools 里有这个课题的写作说明就照它；"
        f"读最新一篇末尾「学习反馈」区「{MARK}」之后用户写的内容，前几篇的反馈也看。"
        "最新一篇没有反馈也照写，开头注明「本篇基于当前可见上下文生成」，不要停下来问。",
        "写之前先检索、联网核实材料（论文、公司案例），核实过的写进正文，文末列可点击的来源，没核实的标「待核实」。"
        "别人的观点直接写进文章，不给书单、不布置阅读。主题不跑偏。",
        "文章末尾保留「学习反馈」区，模板照技能，「请写在这行下面：」之后留空。",
        f"写完：运行 python3 -I {tool} {folder} 生成 HTML；更新 00-学习计划.md 的当前进度、反馈摘要和下一步；"
        f"更新 {base / INDEX} 里这个课题那一行（当前进度、最近更新、下一步）。",
        f"不写第 {int(number) + 1:02d} 篇，不改别的课题和已写各篇的反馈，不提交 git，不给别人发消息。",
        f"回执最后一行写 RESULT: ARTICLE={number}；写不成写 RESULT: BLOCKED=<原因>。",
    ]
    return "\n".join(lines)[:agent_watch.DETAIL_LIMIT]


def handoff(body: dict, *, at=None, trigger=None, read_runs=None) -> dict:
    """「写下一篇」：只在页面上点了才交。确认中台有规则「写学习文章」，在账本锁里重读、已交给还在派或在写的不再交，
    记下交给时间、篇号和写好的 detail，再触发一次监看。不写学习目录（由 Agent 写）。"""
    name = body.get("topic")
    base, folder = locate(name)
    if folder is None:
        raise APIError(404, "INDEX.md 里没有这个课题，或课题目录不在")
    missing = agent_watch.learning_rule_missing()
    if missing:
        raise ValueError(missing)
    row = next(t for t in topics_of(base)[0] if t["name"] == name)
    items = articles(base, folder)
    last = max((int(a["no"]) for a in items), default=0)
    if last >= 99:
        raise ValueError("篇号已经到 99")
    number = f"{last + 1:02d}"
    at = at or agent_watch.now()
    stamp = at.isoformat(timespec="seconds")
    key = subject(name)
    found, _ = (read_runs or runs)()
    with agent_watch.handoff_ledger(ledger_path()) as book:  # 在锁里重读账本再判断，双击也只记一次
        for old in [k for k, v in book.items() if agent_watch.stale_entry(v, at)]:
            del book[old]
        now_state = status(book.get(key), found.get(key), at)
        if now_state["state"] in BUSY:
            raise APIError(409, f"已经交给 Agent，正在写第 {now_state['no'] or number} 篇")
        book[key] = {"handed_at": stamp, "topic": name, "no": number, "title": f"写《{name}》第 {number} 篇",
                     "url": "", "detail": build_detail(base, row, folder, items, number)}
    try:
        routed, error = (trigger or agent_watch.emit_check)(key, at, f"写《{name}》第 {number} 篇", ""), ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        routed, error = "", f"已记下，但没能马上触发中台（{exc}），工作台的监看闸门 10 分钟内会再触发一次"
    return {"topic": name, "no": number, "handed_at": stamp, "routed": routed, "error": error,
            "handoff": {"state": "pending", "no": number, "handed_at": stamp}}


def static_file(name: str, rest: str) -> tuple[bytes, str]:
    """课题的一个文件（HTML、图片、样式），只读。只认 INDEX.md 登记的课题，不走符号链接、不读隐藏文件。
    rest 先按课题目录解析；找不到再按根目录的上一层解析（工作台用 <根目录名>/<课题>/NN.html 打开，页面里 ../../导读/ 才走得通），
    这时只认课题目录和 SHARED 里的目录。"""
    base, folder = locate(name)
    if folder is None:
        raise APIError(404, "没有这个课题")
    parts = rest.split("/")
    if not rest or any(not p or p in (".", "..") or p.startswith(".") or "\\" in p for p in parts):
        raise APIError(404, "没有这个文件")
    path, scope = folder.joinpath(*parts), folder
    if not path.is_file():
        path = base.parent.joinpath(*parts)
        scope = folder if path.resolve().is_relative_to(folder.resolve()) else base.parent / parts[0]
        if scope != folder and parts[0] not in SHARED:
            raise APIError(404, "没有这个文件")
    kind = STATIC.get(path.suffix.lower())
    if kind is None or not (path.is_file() and path.resolve().is_relative_to(scope.resolve())):
        raise APIError(404, "没有这个文件")
    if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(base.parent)):
        raise APIError(404, "没有这个文件")
    if path.stat().st_size > STATIC_LIMIT:
        raise APIError(413, "文件太大")
    return path.read_bytes(), kind
