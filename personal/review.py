"""「助理」页的「回顾」标签：只读助理目录的 回顾/周、回顾/月，例外是三处写——保存三问的回答、运行助理的回顾脚本
收集事实、把「写总结和三问」交给 Agent。

助理目录和 personal/assistant.py 用同一个（默认 /Volumes/Luochen/LCS/assistant，环境变量 ASSISTANT_ROOT）。真源是助理仓库
scripts/review.py 写的 回顾/周/YYYY-Www.md、回顾/月/YYYY-MM.md 和同名的离线 HTML；这里不另存任何内容。md 里
<!-- review:summary -->、review:top3、review:questions、review:projects 四个块由回顾 Agent（技能 assistant-review）填，
没填时是占位符（top3 是候选列表）；页面只说「还没写」，不把占位符露出来。

保存回答只改那一期 md 里对应问题「回答：」之后那一段，整篇换名写入；该期有 HTML 就用 review.py --render 重出，和 md 保持一致。
「现在生成 / 更新数据」直接运行 review.py --week / --month：脚本重跑时靠标记保留不含占位符的块（含回答），
只写了一部分、还带占位符的块会被重置，这种情况这里先拒绝。
「让 Agent 写总结和三问」只记 ~/.agent-autoloop/issues/review-handoffs.json 并触发监看，监看作为 review.next 交给中台，
规则「写回顾」（技能 assistant-review）接。工作台不提交、不推送助理仓库。
"""
from __future__ import annotations

import calendar
import os
import re
import signal
import subprocess
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import agent_watch, assistant, learning
from .errors import APIError

FOLDER = "回顾"
KINDS = {"week": "周", "month": "月"}
LABELS = {"week": re.compile(r"([0-9]{4})-W([0-9]{2})"), "month": re.compile(r"([0-9]{4})-([0-9]{2})")}
PLACEHOLDER = "（待回顾 Agent 填写）"  # review.py 的 PLACEHOLDER
CANDIDATES = "候选（"  # review.py 给 top3 块的初始内容：候选列表，不是 Agent 挑的 3 件
BLOCKS = ("summary", "top3", "questions", "projects")
BLOCK_NAMES = {"summary": "一句话总结", "top3": "下周最重要的 3 件", "questions": "三问", "projects": "各项目过三问"}
ANSWER = "回答："
ANSWER_LIMIT = 5000
LISTED = 60  # 每个分区最多列这么多期
WEEK_TIME, MONTH_TIME = time(21, 0), time(9, 0)  # 中台规则「周回顾」周日 21:00、「月回顾」每月 1 日 09:00
RUN_TIMEOUT = 300
RENDER_TIMEOUT = 60
STATIC_LIMIT = 30 << 20
QUESTION = re.compile(r"([0-9]+)\.\s+(.*\S)")
ITEM = re.compile(r"\s*(?:[0-9]+\.|[-*])\s+(.*\S)")
SOURCE = re.compile(r"\s*·\s*[\w一-鿿./ -]+\.md(?::[0-9]+)?\s*$")  # 条目末尾的来源文件：页面不显示内部路径
TABLE_HEAD = ("类别", "本人（小时）", "占比")

RULE = agent_watch.RECAP_RULE
SKILL = agent_watch.RECAP_SKILL
RESULT_LINE = re.compile(r"RESULT: (?:REVIEW=(\S+)|BLOCKED=(.*\S.*))")
BUSY = learning.BUSY
_run_lock = threading.Lock()


def root() -> Path:
    return assistant.root()


def folder(base: Path, kind: str) -> Path:
    return base / FOLDER / KINDS[kind]


def span(kind: str, label: str) -> tuple[date, date]:
    """期号的起止日期：周是 ISO 周的周一到周日，月是 1 日到月底。期号不合法抛 ValueError。"""
    found = LABELS[kind].fullmatch(label) if kind in KINDS and isinstance(label, str) else None
    if not found:
        raise ValueError("期号要写成 2026-W47（周）或 2026-11（月）")
    year, number = int(found[1]), int(found[2])
    try:
        if kind == "week":
            start = date.fromisocalendar(year, number, 1)
            return start, start + timedelta(days=6)
        return date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])
    except ValueError:
        raise ValueError(f"没有 {label} 这一期") from None


def label_of(kind: str, day: date) -> str:
    if kind == "week":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02d}"
    return f"{day:%Y-%m}"


def current(today: date) -> dict:
    """现在点「生成」会出哪一期，和 review.py 的缺省一致：周是今天所在的周，月是昨天所在的月。"""
    return {"week": label_of("week", today), "month": label_of("month", today - timedelta(days=1))}


def next_runs(now: datetime) -> dict:
    """下一次自动生成的时刻：周日 21:00、每月 1 日 09:00。"""
    week = datetime.combine(now.date() + timedelta(days=(6 - now.weekday()) % 7), WEEK_TIME, tzinfo=now.tzinfo)
    if week <= now:
        week += timedelta(days=7)
    month = datetime.combine(now.date().replace(day=1), MONTH_TIME, tzinfo=now.tzinfo)
    if month <= now:
        month = datetime.combine((now.date().replace(day=1) + timedelta(days=32)).replace(day=1), MONTH_TIME, tzinfo=now.tzinfo)
    return {"week": week.isoformat(timespec="minutes"), "month": month.isoformat(timespec="minutes")}


def version(path: Path) -> str:
    return str(path.stat().st_mtime_ns)


# ---------- 读一期 md ----------

def block_match(markdown: str, name: str):
    return re.search(rf"<!-- review:{name} -->\n?(.*?)<!-- /review:{name} -->", markdown, re.S)


def block(markdown: str, name: str) -> str | None:
    """一个 Agent 块的正文；没有这个块是 None。"""
    found = block_match(markdown, name)
    return found[1].strip("\n") if found else None


def written(name: str, body: str | None) -> bool:
    """这个块 Agent 写了没有：没有块、空的、还带占位符、top3 还是脚本给的候选列表，都算没写。"""
    if body is None or not body.strip() or PLACEHOLDER in body:
        return False
    return not (name == "top3" and body.lstrip().startswith(CANDIDATES))


def items(body: str) -> list[str]:
    """块里的每一条（去掉序号和末尾的来源文件）；一条都没认出来就按非空行给。"""
    lines = [line for line in body.splitlines() if line.strip()]
    found = [m[1] for line in lines if (m := ITEM.fullmatch(line))]
    return [SOURCE.sub("", text) for text in (found or [line.strip() for line in lines])]


def questions(body: str | None) -> list[dict]:
    """三问和各自的回答。回答是「回答：」之后到下一题之前的各行；问题还是占位符的 text 为空。"""
    out: list[dict] = []
    answering = False
    for line in (body or "").splitlines():
        head = None if line.startswith((" ", "\t")) else QUESTION.fullmatch(line.rstrip())
        if head:
            text = head[2].strip()
            out.append({"no": len(out) + 1, "text": "" if PLACEHOLDER in text else text, "answer": ""})
            answering = False
        elif out and not answering and line.strip().startswith(ANSWER):
            out[-1]["answer"] = line.strip()[len(ANSWER):].strip()
            answering = True
        elif out and answering and line.strip():
            out[-1]["answer"] = (out[-1]["answer"] + "\n" + line.strip()).strip()
    return out


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def numbers(markdown: str) -> dict:
    """几个关键数字，只从 md 里认：本人合计小时、有记录的天数、各类别小时和占比、合并 PR 数、关掉 issue 数。认不到的不给。"""
    out: dict = {}
    found = re.search(r"本人合计约\s*([0-9.]+)\s*小时", markdown)
    if found and number(found[1]) is not None:
        out["hours"] = number(found[1])
    found = re.search(r"本人合计约[^；\n]*?([0-9]+)\s*天有记录", markdown)
    if found:
        out["days"] = int(found[1])
    lines = markdown.splitlines()
    for n, line in enumerate(lines):
        if not line.lstrip().startswith("|") or tuple(cells(line)[:3]) != TABLE_HEAD:
            continue
        rows = []
        for row in lines[n + 1:]:
            if not row.lstrip().startswith("|"):
                break
            parts = cells(row)
            if all(set(c) <= set("-: ") for c in parts) or len(parts) < 3 or number(parts[1]) is None:
                continue
            share = re.fullmatch(r"([0-9]+)%", parts[2])
            rows.append({"name": parts[0], "hours": number(parts[1]), "share": int(share[1]) if share else None})
        if rows:
            out["categories"] = rows
        break
    found = re.search(r"^- 合并的 PR：([0-9]+) 个", markdown, re.M)
    if found:
        out["merged_prs"] = int(found[1])
    found = re.search(r"^- 关掉的 issue[^：\n]*：([0-9]+) 个", markdown, re.M)
    if found:
        out["closed_issues"] = int(found[1])
    return out


def parse(markdown: str) -> dict:
    """一期回顾 md 里页面要的部分。"""
    stamp = re.search(r"^生成时间：([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2})(.*)$", markdown, re.M)
    bodies = {name: block(markdown, name) for name in BLOCKS}
    done = {name: written(name, body) for name, body in bodies.items()}
    asked = questions(bodies["questions"])
    done["questions"] = any(q["text"] for q in asked)
    return {"generated": stamp[1] if stamp else "", "partial": bool(stamp and "还没过完" in stamp[2]),
            "summary": " ".join(bodies["summary"].split()) if done["summary"] else "",
            "top3": items(bodies["top3"]) if done["top3"] else [],
            "questions": asked,
            "projects": items(bodies["projects"]) if done["projects"] else [],
            "has": {name: body is not None for name, body in bodies.items()},
            "written": done, "numbers": numbers(markdown)}


def subject(kind: str, label: str) -> str:
    """交给记录的键，也是中台事件的 subject：中台只收字母、数字和 # . _ : / @ -，不能带中文。"""
    return f"review/{label}"


def period(base: Path, kind: str, label: str) -> dict | None:
    """一期：期号、起止日期和 md 里读到的内容；文件不在或期号不合法是 None。"""
    try:
        start, end = span(kind, label)
    except ValueError:
        return None
    path = folder(base, kind) / f"{label}.md"
    if not (path.is_file() and assistant.inside(base, path)):
        return None
    page = path.with_suffix(".html")
    return {"kind": kind, "label": label, "start": start.isoformat(), "end": end.isoformat(),
            "html": page.is_file() and assistant.inside(base, page), "version": version(path),
            "modified": assistant.stamp(path), **parse(path.read_text(encoding="utf-8", errors="replace"))}


def listing(base: Path, kind: str) -> list[dict]:
    where = folder(base, kind)
    if not (where.is_dir() and assistant.inside(base, where)):
        return []
    labels = sorted((p.stem for p in where.iterdir() if p.suffix == ".md" and LABELS[kind].fullmatch(p.stem)), reverse=True)
    return [found for label in labels[:LISTED] if (found := period(base, kind, label))]


# ---------- 「让 Agent 写」的进度 ----------

def ledger_path() -> Path:
    return agent_watch.recap_ledger_path()


def parse_result(text):
    """回执里最后一条结果行：RESULT: REVIEW=<期号> / BLOCKED=<原因>（技能的回执末尾还有通知块，所以不只看最后一行）。
    写成的结果沿用 learning.status 认的 article（no 放期号），两个标签共用一套进度状态。"""
    for line in reversed((text or "").split("\n")):
        found = RESULT_LINE.fullmatch(line.strip())
        if found:
            return {"kind": "article", "no": found[1]} if found[1] else {"kind": "blocked", "reason": found[2].strip()[:300]}
    return None


def runs():
    """中台里「写回顾」每一期最近一轮；读不到时返回 ({}, 原因)。"""
    from . import issues
    auto, error = issues.autoloop_state(rule=RULE, parse=parse_result)
    return (auto or {}).get("runs", {}), error


def status(entry, run, at) -> dict:
    """「让 Agent 写」的进度，状态和学习标签的「写下一篇」同一套（learning.status）；no 是期号。"""
    return learning.status(entry, run, at)


# ---------- 页面数据 ----------

def view(*, now: datetime | None = None, read_runs=None) -> dict:
    """回顾标签的数据：周、月两个分区，各期新的在前。"""
    now = now or datetime.now().astimezone()
    base = root()
    payload = {"root": str(base), "today": now.date().isoformat(), "current": current(now.date()),
               "schedule": {"week": f"每周日 {WEEK_TIME:%H:%M}", "month": f"每月 1 日 {MONTH_TIME:%H:%M}"},
               "next_run": next_runs(now), "weeks": [], "months": [], "error": "", "hub_error": ""}
    if not base.is_dir():
        payload["error"] = f"助理目录不存在：{base}（可用环境变量 ASSISTANT_ROOT 指定）"
        return payload
    payload["weeks"], payload["months"] = listing(base, "week"), listing(base, "month")
    book = agent_watch.load_handoffs(ledger_path())
    found, hub_error = (read_runs or runs)() if book else ({}, "")
    for row in payload["weeks"] + payload["months"]:
        key = subject(row["kind"], row["label"])
        row["handoff"] = status(book.get(key), found.get(key), now)
    if book and hub_error:
        payload["hub_error"] = hub_error
    return payload


def md_path(kind, label) -> tuple[Path, Path]:
    """(助理目录, 那一期的 md)；期号不合法抛 ValueError，文件不在抛 404。"""
    span(kind, label)
    base = root()
    path = folder(base, kind) / f"{label}.md"
    if not (path.is_file() and assistant.inside(base, path)):
        raise APIError(404, f"还没有 {label} 的回顾")
    return base, path


# ---------- 写一：保存三问的回答 ----------

def with_answer(markdown: str, no: int, text: str) -> str:
    """把第 no 题的回答写进 md：只换这一题「回答：」之后到下一题之前的各行，其余一字不动。
    回答每行缩进到「回答：」那一列（回顾脚本读回答、出 HTML 都靠缩进认它属于这一题），空行不留。"""
    found = block_match(markdown, "questions")
    if not found:
        raise ValueError("这一期没有三问")
    lines = found[1].splitlines(keepends=True)
    heads = [n for n, line in enumerate(lines) if not line.startswith((" ", "\t")) and QUESTION.fullmatch(line.rstrip())]
    if not 1 <= no <= len(heads):
        raise ValueError(f"这一期没有第 {no} 个问题")
    if PLACEHOLDER in lines[heads[no - 1]]:
        raise ValueError(f"第 {no} 个问题还没写，先等回顾 Agent 写好问题")
    first, stop = heads[no - 1] + 1, heads[no] if no < len(heads) else len(lines)
    at = next((n for n in range(first, stop) if lines[n].strip().startswith(ANSWER)), None)
    indent = lines[at][:len(lines[at]) - len(lines[at].lstrip())] if at is not None else "   "
    said = [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    fresh = [f"{indent}{ANSWER}{said[0] if said else ''}\n", *(f"{indent}{line}\n" for line in said[1:])]
    if at is None:
        at = end = first
    else:
        end = stop
        while end > at + 1 and not lines[end - 1].strip():  # 题与题之间的空行留着
            end -= 1
    body = "".join(lines[:at] + fresh + lines[end:])
    return markdown[:found.start(1)] + body + markdown[found.end(1):]


def script_path(base: Path) -> Path:
    script = base / "scripts" / "review.py"
    if not (script.is_file() and assistant.inside(base, script)):
        raise APIError(404, f"找不到回顾脚本：{script}")
    return script


def execute(base: Path, args: list[str], timeout: int) -> tuple[int, str, str]:
    """运行助理的回顾脚本：自己的进程组、不接标准输入，超时杀掉整个进程组（和简报脚本同一种跑法）。"""
    process = subprocess.Popen([sys.executable, str(script_path(base)), *args], cwd=str(base), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                               env={**os.environ, "ASSISTANT_ROOT": str(base)})
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise APIError(504, f"回顾脚本超过 {timeout} 秒没有结束，已停止") from None
    return process.returncode, (stdout or "").strip(), (stderr or "").strip()


def rerender(base: Path, path: Path) -> str:
    """md 改了以后重出同名 HTML；没成返回一句原因（回答已经存好，不算失败）。"""
    try:
        code, out, err = execute(base, ["--render", str(path)], RENDER_TIMEOUT)
    except (APIError, OSError) as exc:
        return f"回答已保存，但网页没能重新生成：{exc}"
    if code != 0:
        return f"回答已保存，但网页没能重新生成：{((err or out or '无输出').splitlines() or [''])[-1][:200]}"
    return ""


def save_answer(body: dict) -> dict:
    """保存一题的回答：只改那一期 md 里这一题「回答：」之后那一段。version 对不上（打开后文件又被改过）就不写。
    写了且这一期有 HTML，就用 review.py --render 重出，让 HTML 和 md 一致。"""
    kind, label, no, text = body.get("kind"), body.get("label"), body.get("no"), body.get("text")
    if not isinstance(text, str):
        raise ValueError("回答要写成文字")
    if len(text) > ANSWER_LIMIT:
        raise ValueError(f"回答太长（最多 {ANSWER_LIMIT} 字）")
    if "<!--" in text or "-->" in text or PLACEHOLDER in text:
        raise ValueError(f"回答里不能有 <!--、--> 和「{PLACEHOLDER}」：回顾脚本靠它们认块")
    if isinstance(no, bool) or not isinstance(no, int):
        raise ValueError("题号要写成数字")
    base, path = md_path(kind, label)
    expected = body.get("version")
    if expected is not None and expected != version(path):
        raise APIError(409, f"{label} 在你打开后又被改过，刷新后再保存（你写的还在输入框里）")
    before = path.read_text(encoding="utf-8")
    after = with_answer(before, no, text)
    note = ""
    if after != before:
        learning.write_text(path, after)
        if path.with_suffix(".html").is_file():
            note = rerender(base, path)
    return {"period": period(base, kind, label), "note": note}


# ---------- 写二：运行回顾脚本收集事实 ----------

def at_risk(markdown: str) -> list[str]:
    """重跑脚本会丢内容的块：脚本只保留不含占位符的块，所以「带占位符、又已经写了别的」的块会被整块重置。"""
    out = []
    for name in BLOCKS:
        body = block(markdown, name)
        if body is None or PLACEHOLDER not in body:
            continue
        rest = re.sub(r"^\s*[0-9]+\.\s*", "", body.replace(PLACEHOLDER, ""), flags=re.M).replace(ANSWER, "")
        if rest.strip():
            out.append(BLOCK_NAMES[name])
    return out


def run(body: dict, *, timeout: int = RUN_TIMEOUT, now: datetime | None = None) -> dict:
    """「现在生成 / 更新数据」：运行 review.py --week 或 --month，只做收集事实这一步；同一时间只跑一个。
    不带期号是脚本的缺省（今天所在的周、昨天所在的月）；带期号就生成那一期（不能是还没开始的）。"""
    kind, label = body.get("kind"), body.get("label")
    if kind not in KINDS:
        raise ValueError("要说明是周回顾（week）还是月回顾（month）")
    now = now or datetime.now().astimezone()
    label = label or current(now.date())[kind]
    start, _ = span(kind, label)
    if start > now.date():
        raise ValueError(f"{label} 还没开始")
    base = root()
    script_path(base)
    existing = folder(base, kind) / f"{label}.md"
    existed = existing.is_file() and assistant.inside(base, existing)
    if existed:
        risky = at_risk(existing.read_text(encoding="utf-8", errors="replace"))
        if risky:
            raise APIError(409, f"{label} 的{'、'.join(risky)}只写了一部分，重新生成会把这一块重置。先补全（或让 Agent 写完）再更新数据")
    if not _run_lock.acquire(blocking=False):
        raise APIError(409, "回顾正在生成，请稍候")
    try:
        code, out, err = execute(base, ["--week" if kind == "week" else "--month", "--date", start.isoformat()], timeout)
    finally:
        _run_lock.release()
    if code != 0:
        raise APIError(502, f"回顾脚本失败（退出码 {code}）：{((err or out or '无输出').splitlines() or [''])[-1][:300]}")
    return {"ok": True, "kind": kind, "label": label, "existed": existed, **view(now=now)}


# ---------- 写三：让 Agent 写总结和三问 ----------

def build_detail(base: Path, kind: str, label: str, path: Path, missing: list[str]) -> str:
    name = "周回顾" if kind == "week" else "月回顾"
    start, _ = span(kind, label)
    flag = "--week" if kind == "week" else "--month"
    lines = [
        f"期号：{name} {label}（review.py {flag} --date {start.isoformat()}）。用户在工作台「回顾」标签点了「让 Agent 写总结和三问」。",
        f"只做这一期，LABEL 用 {label}，不按今天另算。",
        f"主检出里已有这一期：{path}（工作台生成的，可能还没进 git）。开好工作区后先把它复制到工作区的同一路径，再运行脚本并带上 "
        f"--date {start.isoformat()}：脚本靠标记保留已经写好的块和各题的「回答：」，用户写的回答一个字不能丢。",
        f"还没写的块：{'、'.join(missing) or '无'}。按技能 {SKILL} 第 2 节补这些块；已经写了的块和回答不改。",
        "然后照技能第 3、4 节：重出 HTML、提交、开 PR、合并、同步主检出、通知。",
        f"回执里单独一行写 RESULT: REVIEW={label}；写不成写 RESULT: BLOCKED=<原因>。",
    ]
    return "\n".join(lines)[:agent_watch.DETAIL_LIMIT]


def handoff(body: dict, *, at=None, trigger=None, read_runs=None) -> dict:
    """「让 Agent 写总结和三问」：只在页面上点了才交。确认中台有规则「写回顾」，在账本锁里重读、已交给还在派或在写的不再交，
    记下交给时间、期号和写好的 detail，再触发一次监看。不写助理目录（由 Agent 写）。"""
    kind, label = body.get("kind"), body.get("label")
    if kind not in KINDS:
        raise ValueError("要说明是周回顾（week）还是月回顾（month）")
    base, path = md_path(kind, label)
    missing = agent_watch.recap_rule_missing()
    if missing:
        raise ValueError(missing)
    found = parse(path.read_text(encoding="utf-8", errors="replace"))
    todo = [f"{BLOCK_NAMES[n]}（{n}）" for n in BLOCKS if found["has"][n] and not found["written"][n]]
    if not todo:
        raise ValueError(f"{label} 的总结和三问都已经写了")
    at = at or agent_watch.now()
    stamp = at.isoformat(timespec="seconds")
    key = subject(kind, label)
    title = f"写{'周' if kind == 'week' else '月'}回顾 {label} 的总结和三问"
    seen, _ = (read_runs or runs)()
    with agent_watch.handoff_ledger(ledger_path()) as book:  # 在锁里重读账本再判断，双击也只记一次
        for old in [k for k, v in book.items() if agent_watch.stale_entry(v, at)]:
            del book[old]
        state = status(book.get(key), seen.get(key), at)
        if state["state"] in BUSY:
            raise APIError(409, f"已经交给 Agent，正在写 {label}")
        book[key] = {"handed_at": stamp, "kind": kind, "no": label, "title": title, "url": "",
                     "detail": build_detail(base, kind, label, path, todo)}
    try:
        routed, error = (trigger or agent_watch.emit_check)(key, at, title, ""), ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        routed, error = "", f"已记下，但没能马上触发中台（{exc}），工作台的监看闸门 10 分钟内会再触发一次"
    return {"kind": kind, "label": label, "handed_at": stamp, "routed": routed, "error": error,
            "handoff": {"state": "pending", "no": label, "handed_at": stamp}}


# ---------- 只读静态路由 ----------

def static_file(kind: str, name: str) -> tuple[bytes, str]:
    """一期的离线 HTML，只读。只认 回顾/周、回顾/月 下期号合法的 .html，不走符号链接，别的类型一律 404。"""
    if kind not in KINDS or not isinstance(name, str) or not name.endswith(".html") or not LABELS[kind].fullmatch(name[:-5]):
        raise APIError(404, "没有这个文件")
    base = root()
    path = folder(base, kind) / name
    if not (path.is_file() and assistant.inside(base, path)) or any(p.is_symlink() for p in (path.parent, path.parent.parent)):
        raise APIError(404, "没有这个文件")
    if path.stat().st_size > STATIC_LIMIT:
        raise APIError(413, "文件太大")
    return path.read_bytes(), "text/html"
