"""「自媒体」页：麦田（自媒体多平台发布中控，github.com/CzzzzzzJ/mdldm-maitian）的只读视图，和助理目录里「在做的内容」那张表。

麦田：工作台后端带口令（cookie mt_key，和麦田网页一样）读麦田的 GET /api/overview，整理成页面要的三块：最近几期各平台状态、
各平台粉丝和近 7 天涨粉、要本人处理的健康问题。只发 GET，不调用 /api/action 等写接口。口令只在后端用：不回给页面、不写日志；
「打开麦田大屏」走工作台的 /media/maitian，由后端 302 到麦田带口令的入口（麦田写 cookie 后再跳回它的首页）。

连接配置按顺序找：工作台本机运行数据 .personal/maitian.json 的 url、key；麦田目录（默认 /Volumes/Luochen/工作/开源/maitian，
环境变量 MAITIAN_ROOT）的 config.json 的 web.port、web.key；都没有就退回演示版 http://127.0.0.1:8788、口令 demo（node src/demo.mjs）。
「在做的内容」只读助理目录 事项/自媒体与内容/README.md 里「## 在做的内容」下的第一张表，原样给每格文字，不另抄；
这个板块原名「写书与内容」，新目录不在时退回旧目录 事项/写书与内容/README.md。
"""
from __future__ import annotations

import json
import os
import re
import socket
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from . import assistant

DEFAULT_ROOT = "/Volumes/Luochen/工作/开源/maitian"
LOCAL_FILE = "maitian.json"
DEMO_URL, DEMO_KEY = "http://127.0.0.1:8788", "demo"
TIMEOUT = 4
EPISODES = 6  # 最近几期
FOLLOWER_DAYS = 7
NAMES = {"bilibili": "B站", "douyin": "抖音", "xhs": "小红书", "channels": "视频号", "youtube": "YouTube", "x": "X",
         "instagram": "Instagram", "zsz": "知识站"}
ORDER = list(NAMES)
STATE_CN = {"planned": "计划中", "pending": "等待", "running": "进行中", "filled": "已填好", "awaiting_user": "等你点发布",
            "scheduled": "已定时", "published": "已发布", "failed": "异常", "not_logged_in": "未登录", "stopped": "已停止", "skipped": "跳过"}
WAITING = ("awaiting_user", "filled")  # 麦田已填好、等本人在后台点发布
BAD = ("failed", "not_logged_in", "stopped")
# 一期里各状态的先后：等你点发布、出了问题的在前
STATE_ORDER = ["awaiting_user", "filled", "failed", "not_logged_in", "stopped", "running", "pending", "scheduled", "published", "planned", "skipped"]
CONTENT_PATH = ("事项", "自媒体与内容", "README.md")
LEGACY_CONTENT_PATH = ("事项", "写书与内容", "README.md")  # 板块改名前的目录，过渡期兼容
CONTENT_SECTION = "在做的内容"
COLUMNS = ("内容", "做到哪", "发布")


def root() -> Path:
    return Path(os.environ.get("MAITIAN_ROOT") or DEFAULT_ROOT)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def settings(data) -> dict:
    """麦田地址和口令，以及来源：local（工作台本机配置）、maitian（麦田 config.json）、demo（演示版）。"""
    local = _read_json(Path(data) / LOCAL_FILE)
    if isinstance(local, dict) and isinstance(local.get("url"), str) and local["url"].strip():
        return {"url": local["url"].strip().rstrip("/"), "key": str(local.get("key") or ""), "source": "local",
                "demo": bool(local.get("demo"))}
    config = _read_json(root() / "config.json")
    web = config.get("web") if isinstance(config, dict) else None
    if isinstance(web, dict):
        port = web.get("port") or 8787
        return {"url": f"http://127.0.0.1:{int(port)}", "key": str(web.get("key") or ""), "source": "maitian", "demo": False}
    return {"url": DEMO_URL, "key": DEMO_KEY, "source": "demo", "demo": True}


def start_command(conf: dict) -> str:
    """麦田没在运行时给的一行启动命令：演示版起演示，真实版起常驻进程（网页随它起来）。"""
    return f"cd {root()} && node src/{'demo.mjs' if conf['demo'] else 'hub.mjs daemon'}"


def _opener():
    return build_opener(ProxyHandler({}))  # 本机地址不走系统代理


def fetch(conf: dict, path: str, timeout: float = TIMEOUT):
    """带口令 GET 麦田的接口；返回 (HTTP 状态, JSON)。连不上抛 ConnectionError。"""
    headers = {"Accept": "application/json"}
    if conf["key"]:
        headers["Cookie"] = f"mt_key={conf['key']}"
    try:
        with _opener().open(Request(conf["url"] + path, headers=headers), timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, None
    except (URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
        raise ConnectionError(str(exc)) from None


def _platform(task: dict) -> str:
    return task.get("name") or NAMES.get(task.get("platform", ""), task.get("platform", ""))


def episode(raw: dict) -> dict:
    """一期：期号、标题、计划/发布时间、按状态归组的平台（等你点发布、出问题的在前），等你点发布的平台另列。"""
    groups: dict[str, list[str]] = {}
    for task in raw.get("tasks") or []:
        state = task.get("state") or ""
        groups.setdefault("awaiting_user" if state in WAITING else state, []).append(_platform(task))  # 已填好也算等你点发布
    states = sorted(groups, key=lambda s: STATE_ORDER.index(s) if s in STATE_ORDER else len(STATE_ORDER))
    return {"id": str(raw.get("id", "")), "episode": raw.get("episode"), "title": raw.get("short") or raw.get("name") or "",
            "schedule": raw.get("schedule") or "",
            "groups": [{"state": s, "label": "等你点发布" if s in WAITING else STATE_CN.get(s, s or "未知"),
                        "tone": "wait" if s in WAITING else "bad" if s in BAD else "ok" if s == "published" else "",
                        "platforms": groups[s]} for s in states],
            "waiting": groups.get("awaiting_user", [])}


def followers(rows: list) -> list:
    """每个平台最新的粉丝数，和近 7 天涨了多少（最新一天减 7 天前那天；记录不满 7 天从最早一天算，和麦田小组件一样）。"""
    by: dict[str, list] = {}
    for row in sorted((r for r in rows or [] if r.get("total") is not None), key=lambda r: r.get("day", "")):
        by.setdefault(row.get("platform", ""), []).append(row)
    out = []
    for platform in sorted(by, key=lambda p: ORDER.index(p) if p in ORDER else len(ORDER)):
        days = by[platform]
        last = days[-1]
        try:
            cutoff = (date.fromisoformat(last["day"]) - timedelta(days=FOLLOWER_DAYS)).isoformat()
        except (KeyError, ValueError):
            cutoff = ""
        before = next((r for r in reversed(days) if r.get("day", "") <= cutoff), None) or (days[0] if days[0] is not last else None)
        out.append({"platform": platform, "name": NAMES.get(platform, platform), "total": last["total"],
                    "week": last["total"] - before["total"] if before else None, "day": last.get("day", "")})
    return out


def problems(health: dict) -> list:
    """健康状态里要本人处理的：专用 Chrome 没开、某平台登录失效、成片目录（NAS）读不到。"""
    out = []
    if health.get("chrome") is False:
        out.append("专用 Chrome 没开")
    for platform, state in (health.get("login") or {}).items():
        if state == "out":
            out.append(f"{NAMES.get(platform, platform)}登录失效，在专用 Chrome 里重新登录")
    if health.get("nas") is False:
        out.append("成片目录读不到（NAS 没挂载）")
    return out


def summarize(raw: dict) -> dict:
    episodes = sorted(raw.get("episodes") or [], key=lambda e: e.get("episode") or 0, reverse=True)[:EPISODES]
    shown = [episode(e) for e in episodes]
    return {"name": raw.get("name") or "麦田", "episodes": shown, "waiting": sum(len(e["waiting"]) for e in shown),
            "followers": followers(raw.get("followers") or []), "problems": problems(raw.get("health") or {}),
            "now": raw.get("now") or ""}


def view(data) -> dict:
    """「麦田」标签的数据。state：ok 读到了；down 麦田没在运行；denied 口令不对；error 其他。不带口令和地址里的口令。"""
    conf = settings(data)
    payload = {"state": "ok", "demo": conf["demo"], "source": conf["source"], "start": start_command(conf), "maitian": None, "error": ""}
    try:
        status, body = fetch(conf, "/api/overview")
    except ConnectionError:
        payload["state"] = "down"
        return payload
    if status in (401, 403):
        payload["state"] = "denied"
        payload["error"] = "麦田口令不对：改麦田 config.json 的 web.key，或工作台 .personal/maitian.json 的 key"
    elif status != 200 or not isinstance(body, dict):
        payload["state"] = "error"
        payload["error"] = f"麦田返回 {status}"
    else:
        payload["maitian"] = summarize(body)
    return payload


def open_url(data) -> str:
    """「打开麦田大屏」302 的去处：带口令的入口，麦田写 cookie 后跳回首页。只拼配置里的地址。"""
    conf = settings(data)
    parts = urlsplit(conf["url"])
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("麦田地址要以 http:// 或 https:// 开头")
    from urllib.parse import quote
    return conf["url"] + "/" + (f"?key={quote(conf['key'], safe='')}" if conf["key"] else "")


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def table(markdown: str, section: str = CONTENT_SECTION) -> list[dict]:
    """「## 在做的内容」下第一张表的每一行：内容、做到哪、发布（按表头找列，列顺序变了也认）。"""
    rows, fenced, inside, header = [], False, False, None
    for line in markdown.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        heading = None if fenced else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            if inside or header:
                break
            inside = heading[1] == "##" and heading[2].strip() == section
            continue
        if not inside:
            continue
        if not line.lstrip().startswith("|"):
            if header:
                break
            continue
        cells = _cells(line)
        if header is None:
            header = {name: cells.index(name) for name in COLUMNS if name in cells}
            if "内容" not in header:
                return []
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        pick = lambda name: cells[header[name]] if name in header and header[name] < len(cells) else ""
        if pick("内容"):
            rows.append({"content": pick("内容"), "progress": pick("做到哪"), "publish": pick("发布")})
    return rows


def content() -> dict:
    """「在做的内容」标签的数据：表格各行和 README 的修改时间；读不到时写原因。"""
    base = assistant.root()
    path = base.joinpath(*CONTENT_PATH)
    if not path.is_file():
        path = base.joinpath(*LEGACY_CONTENT_PATH)
    payload = {"rows": [], "modified": "", "error": ""}
    if not (path.is_file() and assistant.inside(base, path)):
        payload["error"] = "找不到助理目录里的「自媒体与内容」板块（可用环境变量 ASSISTANT_ROOT 指定助理目录）"
        return payload
    payload["rows"] = table(path.read_text(encoding="utf-8", errors="replace"))
    payload["modified"] = assistant.stamp(path)
    if not payload["rows"]:
        payload["error"] = "「自媒体与内容」板块里没有「在做的内容」表"
    return payload
