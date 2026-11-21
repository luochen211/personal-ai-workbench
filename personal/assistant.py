"""个人助理目录的只读视图：每日简报和事项板块。

助理目录默认 /Volumes/Luochen/LCS/assistant，环境变量 ASSISTANT_ROOT 可覆盖（和助理仓库的
scripts/daily_brief.py 一致）。这里只读那个目录；唯一的写操作是 run_brief() 运行助理自己的
简报脚本，由脚本把今天的简报写到 资料/raw/daily/（覆盖前先把原来那份另存为 YYYY-MM-DD.prev.md）。
只认助理目录里面的文件：符号链接本身、或解析后落到目录外的路径一律不读。
"""
from __future__ import annotations
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from datetime import date, datetime, time, timedelta
from pathlib import Path
from .errors import APIError

DEFAULT_ROOT = "/Volumes/Luochen/LCS/assistant"
BRIEF_TIME = time(8, 30)  # 定时任务每天这个时刻生成简报
BRIEF_DATES = 60  # 日期列表最多列这么多天
RUN_TIMEOUT = 300  # 脚本每个来源最多等 60 秒，四个来源再留余量
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
GAP = re.compile(r"待补|未在来源里记录|来源里没有|没有记录")  # 「下一步」里这些说法表示还要补
BULLET = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s+")
FAILED = "该来源失败"
# 工作台不显示内部文件名：「来源记忆文件」整段，和行里只写文件名的括注，如「（a.md、b.md）」「（见 规范/x.md）」
SOURCE_SECTION = "来源记忆文件"
_FILE = r"[\w\u4e00-\u9fff./-]+\.md"
FILE_NOTE = re.compile(rf"\s*[（(]\s*(?:见|来自|出自|参见)?\s*{_FILE}(?:\s*[、,，;；]\s*{_FILE})*\s*[）)]")  # daily_brief.py 某个来源读不到时写的行首
# 旧简报把 GitHub 未读通知逐条列出；显示时收成一行：未读总数，加上真要本人处理的（请你评审、分配给你）。
# 和助理仓库 scripts/daily_brief.py 现在写的那一行一致。
GITHUB_SECTION = "GitHub 未读通知"
ACTION_REASONS = {"review_requested": "请你评审", "assign": "分配给你"}
MAX_ACTION_ITEMS = 3
GITHUB_TOTAL = re.compile(r"共\s*([0-9]+)\s*条")
GITHUB_ITEM = re.compile(r"-\s+\[([^\]]*)\]\s+(.*)（[^，（）]*，([A-Za-z_]+)）")
_run_lock = threading.Lock()


def root() -> Path:
    return Path(os.environ.get("ASSISTANT_ROOT") or DEFAULT_ROOT)


def inside(base: Path, path: Path) -> bool:
    """path 不是符号链接，并且解析后仍在助理目录里面。"""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def daily_dir(base: Path) -> Path:
    return base / "资料" / "raw" / "daily"


def stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def next_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now().astimezone()
    today = datetime.combine(now.date(), BRIEF_TIME, tzinfo=now.tzinfo)
    return today if now < today else today + timedelta(days=1)


def parse_date(value: str) -> str:
    """只接受 YYYY-MM-DD 形式的真实日期；其他一律拒绝，日期不会拼出目录外的路径。"""
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ValueError("日期格式应为 YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError("日期不存在") from None


def brief_dates(base: Path) -> list[str]:
    """只收严格的 YYYY-MM-DD.md（YYYY-MM-DD.prev.md 之类不算），新的在前。"""
    folder = daily_dir(base)
    if not folder.is_dir() or not inside(base, folder):
        return []
    found = []
    for path in folder.iterdir():
        if path.suffix == ".md" and DATE.fullmatch(path.stem) and path.is_file() and inside(base, path):
            try:
                found.append(parse_date(path.stem))
            except ValueError:
                continue
    return sorted(found, reverse=True)[:BRIEF_DATES]


def default_date(dates: list[str], today: str) -> str | None:
    """默认显示今天那份；没有就用不晚于今天的最新一份；只有未来的（--date 预览）就用离今天最近的。"""
    if today in dates:
        return today
    past = [d for d in dates if d <= today]
    if past:
        return past[0]
    return dates[-1] if dates else None


def failures(markdown: str) -> list[dict]:
    """简报里读失败的来源：{source: 所在小节标题, reason: 失败原因}。"""
    found, heading = [], ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
        elif line.strip().startswith(FAILED):
            reason = line.strip()[len(FAILED):].lstrip("：: ").strip()
            found.append({"source": heading or "（未知来源）", "reason": reason})
    return found


def github_line(total: int, items: list[tuple[str, str, str]]) -> str:
    """items 是 (仓库, 标题, reason)；只列 reason 要本人处理的，最多列 MAX_ACTION_ITEMS 条。"""
    todo = [item for item in items if item[2] in ACTION_REASONS]
    head = f"GitHub 未读 {total} 条"
    if not todo:
        return f"{head}，没有需要你处理的"
    shown = "；".join(f"[{repo}] {title}（{ACTION_REASONS[reason]}）" for repo, title, reason in todo[:MAX_ACTION_ITEMS])
    more = f"；另有 {len(todo) - MAX_ACTION_ITEMS} 条" if len(todo) > MAX_ACTION_ITEMS else ""
    return f"{head}，需要你处理 {len(todo)} 条：{shown}{more}"


def collapse_github(markdown: str) -> str:
    """旧简报的「GitHub 未读通知」逐条列表收成一行；新简报（已经是一行）和读失败的原样不动。"""
    lines = markdown.splitlines()
    start = next((n for n, line in enumerate(lines) if line.startswith("## " + GITHUB_SECTION)), None)
    if start is None:
        return markdown
    end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("#")), len(lines))
    body = [line.strip() for line in lines[start + 1:end] if line.strip()]
    total = GITHUB_TOTAL.fullmatch(body[0]) if body else None
    if not total:
        return markdown
    items = [(m[1], m[2], m[3]) for line in body[1:] if (m := GITHUB_ITEM.fullmatch(line))]
    tail = ["", *lines[end:]] if end < len(lines) else [""]
    out = [*lines[:start + 1], "", github_line(int(total[1]), items), *tail]
    return "\n".join(out).rstrip("\n") + ("\n" if markdown.endswith("\n") else "")


def brief(day: str | None = None, now: datetime | None = None) -> dict:
    """默认那份简报（或指定日期那份）和最近的日期列表。
    latest 是最新的文件日期，default 是不带日期时显示的那天；没有简报时两者和 brief 都是 null。"""
    base = root()
    now = now or datetime.now().astimezone()
    upcoming = next_run(now)
    today = now.date().isoformat()
    payload = {"root": str(base), "dir": str(daily_dir(base)), "today": today,
               "schedule": f"每天 {BRIEF_TIME:%H:%M} 自动生成", "next_run": upcoming.isoformat(timespec="minutes"),
               "dates": [], "latest": None, "default": None, "brief": None, "notice": "", "error": ""}
    if day is not None:
        day = parse_date(day)
    if not base.is_dir():
        payload["error"] = f"助理目录不存在：{base}（可用环境变量 ASSISTANT_ROOT 指定）"
        return payload
    dates = brief_dates(base)
    payload["dates"] = dates
    payload["latest"] = dates[0] if dates else None
    payload["default"] = default_date(dates, today)
    if day is not None and day not in dates:
        raise APIError(404, f"没有 {day} 的简报")
    shown = day or payload["default"]
    if shown is None:
        payload["notice"] = (f"还没有简报。下一次 {upcoming:%m-%d %H:%M} 自动生成；"
                             "也可以现在生成。")
        return payload
    path = daily_dir(base) / f"{shown}.md"
    markdown = collapse_github(path.read_text(encoding="utf-8", errors="replace"))
    payload["brief"] = {"date": shown, "markdown": markdown, "path": str(path), "modified": stamp(path),
                        "failures": failures(markdown)}
    return payload


def keep_previous(base: Path, today: str) -> str:
    """重新生成会覆盖今天那份：先另存为 YYYY-MM-DD.prev.md，返回另存的路径（没有原件就是空串）。"""
    current = daily_dir(base) / f"{today}.md"
    if not (current.is_file() and inside(base, current)):
        return ""
    backup = current.with_name(f"{today}.prev.md")
    if backup.is_symlink() or not inside(base, backup.parent):
        return ""
    shutil.copy2(current, backup)
    return str(backup)


def run_brief(timeout: int = RUN_TIMEOUT, now: datetime | None = None) -> dict:
    """立即运行助理的简报脚本生成今天的简报；同一时间只跑一个。
    脚本在自己的进程组里跑、不接标准输入；超时就杀掉整个进程组。"""
    base = root()
    script = base / "scripts" / "daily_brief.py"
    if not (script.is_file() and inside(base, script)):
        raise APIError(404, f"找不到简报脚本：{script}")
    if not _run_lock.acquire(blocking=False):
        raise APIError(409, "简报正在生成，请稍候")
    try:
        today = (now or datetime.now().astimezone()).date().isoformat()
        previous = keep_previous(base, today)
        process = subprocess.Popen([sys.executable, str(script)], cwd=str(base), stdin=subprocess.DEVNULL,
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
            raise APIError(504, f"简报脚本超过 {timeout} 秒没有结束，已停止") from None
    finally:
        _run_lock.release()
    output = (stdout or "").strip()
    if process.returncode != 0:
        lines = ((stderr or "").strip() or output or "无输出").splitlines()
        raise APIError(502, f"简报脚本失败（退出码 {process.returncode}）：{lines[-1][:300]}")
    report = brief(now=now)
    return {"ok": True, "output": output[-500:], "previous": previous, **report}


def sections(text: str) -> dict[str, str]:
    """按二级标题切 README：{标题: 正文}；代码块里的 # 不算标题。"""
    found: dict[str, list[str]] = {}
    current = None
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        heading = None if fenced else re.match(r"(#{1,2})\s+(.+?)\s*#*\s*$", line)
        if heading:
            current = heading[2] if len(heading[1]) == 2 else None
            if current is not None:
                found.setdefault(current, [])
            continue
        if current is not None:
            found[current].append(line)
    return {k: "\n".join(v).strip() for k, v in found.items()}


def clean(text: str) -> str:
    """去掉「来源记忆文件」段和只写文件名的括注；代码块原样保留。"""
    out: list[str] = []
    fenced = skipping = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        heading = None if fenced else re.match(r"(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            was, skipping = skipping, heading[2].startswith(SOURCE_SECTION)
            if was and not skipping and out and out[-1].strip():
                out.append("")
        if skipping:
            continue
        out.append(line if fenced else FILE_NOTE.sub("", line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + ("\n" if text.endswith("\n") else "")


def section(parts: dict[str, str], name: str) -> str | None:
    for title, body in parts.items():
        if title == name or title.startswith((name + "（", name + "(", name + " ")):
            return body
    return None


def summaries(base: Path, index: Path) -> list[tuple[str, str]]:
    """事项/README.md 表格里的板块顺序和一句话。"""
    if not (index.is_file() and inside(base, index)):
        return []
    rows = []
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if len(cells) < 2:
            continue
        link = re.fullmatch(r"\[([^\]]+?)/?\]\([^)]*\)", cells[0])
        name = (link[1] if link else cells[0]).strip().rstrip("/")
        if name and name != "板块" and not set(name) <= set("-: "):
            rows.append((name, cells[1]))
    return rows


NEWS_FILE = "动态.md"  # 板块按日期记发生了什么的文件，新的在上（见助理目录 事项/README.md）
NEWS_LIMIT = 5
NEWS_MONTH = re.compile(r"([0-9]{4})-([0-9]{1,2})")  # 动态.md 按月分段：## 2026-11
NEWS_ITEM = re.compile(r"[-*+]\s+((?:[0-9]{4}-)?([0-9]{1,2})-([0-9]{1,2})[^：:\s]*)\s*[：:]\s*(.+)")  # - 11-18：…、- 11-13～14：…


def news(folder: Path, base: Path, limit: int = NEWS_LIMIT) -> list[dict]:
    """板块 动态.md 里最近 limit 条按日期的条目：只认「## YYYY-MM」月份段下以「MM-DD：」开头的条目，
    按日期从新到旧（同一天保持文件里的顺序）；「接下来已约的」这类不是月份的段不算。没有这个文件返回空列表。"""
    path = folder / NEWS_FILE
    if not (path.is_file() and inside(base, path)):
        return []
    found, month, fenced = [], None, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        heading = None if fenced else re.match(r"(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            month = NEWS_MONTH.fullmatch(heading[2].strip()) if len(heading[1]) == 2 else month
            continue
        item = None if fenced or not month else NEWS_ITEM.fullmatch(line.strip())
        if item:
            key = (int(month[1]), int(item[2]), int(item[3]))
            found.append((key, {"date": item[1], "text": FILE_NOTE.sub("", item[4]).strip()}))
    found.sort(key=lambda pair: pair[0], reverse=True)  # sort 是稳定的：同一天按文件里的先后
    return [entry for _, entry in found[:limit]]


def board(folder: Path, base: Path, summary: str) -> dict:
    readme = folder / "README.md"
    text = clean(readme.read_text(encoding="utf-8", errors="replace"))
    parts = sections(text)
    status, upcoming = section(parts, "状态"), section(parts, "下一步")
    gaps = [BULLET.sub("", line).strip() for line in (upcoming or "").splitlines() if GAP.search(line)]
    return {"name": folder.name, "summary": clean(summary), "status": status, "next": upcoming,
            "next_missing": not upcoming, "next_gaps": gaps, "readme": text, "news": news(folder, base),
            "path": readme.relative_to(base).as_posix(), "abs_path": str(readme), "modified": stamp(readme)}


def boards() -> dict:
    """事项下每个实际存在的板块目录（含 README.md）；顺序按 事项/README.md 的表格，表外的排在后面。"""
    base = root()
    folder = base / "事项"
    payload = {"root": str(base), "index": str(folder / "README.md"), "boards": [], "error": ""}
    if not (folder.is_dir() and inside(base, folder)):
        payload["error"] = f"找不到事项目录：{folder}（可用环境变量 ASSISTANT_ROOT 指定助理目录）"
        return payload
    listed = summaries(base, folder / "README.md")
    order = {name: n for n, (name, _) in enumerate(listed)}
    said = dict(listed)
    present = [p for p in folder.iterdir()
               if not p.name.startswith(".") and p.is_dir() and inside(base, p)
               and (p / "README.md").is_file() and inside(base, p / "README.md")]
    present.sort(key=lambda p: (order.get(p.name, len(order)), p.name))
    payload["boards"] = [board(p, base, said.get(p.name, "")) for p in present]
    return payload
