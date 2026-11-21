"""「学习」标签的「小说导读」分区：只读小说导读目录，阅读状态记在工作台本机运行数据里。

导读根目录默认 /Volumes/Luochen/impact/教材，环境变量 NOVELS_ROOT 可覆盖。根目录下每个带 index.html 的子目录算一本书
（自动发现，不写死书名；隐藏目录、符号链接、没有 index.html 的目录跳过），另有跨书的「人物选择对照」页。
导读目录一律不写：阅读状态（在读 / 读完 / 未开始）存在运行数据目录的 novel-status.json。
只读路由 /novels/<路径> 打开根目录里的 HTML、样式、脚本和图片（导读页之间的 ../ 链接要能走通，所以按整个根目录给），
别的类型（epub、md、json……）一律不给。
"""
from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path

from . import agent_watch
from .errors import APIError
from .learning import STATIC_LIMIT, inside

DEFAULT_ROOT = "/Volumes/Luochen/impact/教材"
HOME = "index.html"
COMPARE = "选择对照/人物选择对照.html"
FILE = "novel-status.json"
STATES = ("unread", "reading", "done")  # 未开始（默认）、在读、读完
STATIC = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}
_lock = threading.Lock()


def root() -> Path:
    return Path(os.environ.get("NOVELS_ROOT") or DEFAULT_ROOT)


def status_path(data) -> Path:
    return Path(data) / FILE


def load_status(data) -> dict:
    raw = agent_watch.read_json(status_path(data), {})
    books = raw.get("books") if isinstance(raw, dict) else None
    return {k: v for k, v in (books or {}).items() if isinstance(v, dict) and v.get("status") in STATES}


def books_of(base: Path) -> list[str]:
    """根目录下带 index.html 的书目录，按名字排。"""
    if not base.is_dir():
        return []
    out = []
    for path in base.iterdir():
        home = path / HOME
        if path.name.startswith(".") or path.is_symlink() or not path.is_dir():
            continue
        if home.is_file() and not home.is_symlink() and inside(base, home):
            out.append(path.name)
    return sorted(out)


def view(data) -> dict:
    """小说导读分区的数据：每本书的名字和阅读状态，在读的排前面；「人物选择对照」页的路径（没有就是空串）。"""
    base = root()
    if not base.is_dir():
        return {"root": str(base), "books": [], "compare": "",
                "error": f"找不到小说导读目录：{base}（可用环境变量 NOVELS_ROOT 指定）"}
    saved = load_status(data)
    books = [{"name": name, "status": saved.get(name, {}).get("status", "unread")} for name in books_of(base)]
    books.sort(key=lambda b: b["status"] != "reading")  # 稳定排序：在读的在前，其余照名字
    compare = base / COMPARE
    return {"root": str(base), "books": books, "compare": COMPARE if compare.is_file() and inside(base, compare) else "",
            "error": ""}


def set_status(data, body: dict) -> dict:
    """改一本书的阅读状态：只认导读根目录里现有的书，只写运行数据，不碰导读目录。"""
    name, state = body.get("book"), body.get("status")
    if state not in STATES:
        raise ValueError("阅读状态只能是 unread、reading、done")
    if not isinstance(name, str) or name not in books_of(root()):
        raise APIError(404, "没有这本书的导读")
    with _lock:
        saved = load_status(data)
        if state == "unread":
            saved.pop(name, None)
        else:
            saved[name] = {"status": state, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        agent_watch.write_json(status_path(data), {"books": saved})
    return {"book": name, "status": state}


def static_file(rest: str) -> tuple[bytes, str]:
    """导读根目录里的一个文件，只读：只给 HTML、样式、脚本和图片；路径不出根目录、不走符号链接、不读隐藏文件。"""
    base = root()
    parts = rest.split("/")
    if not rest or any(not p or p in (".", "..") or p.startswith(".") or "\\" in p for p in parts):
        raise APIError(404, "没有这个文件")
    path = base.joinpath(*parts)
    kind = STATIC.get(path.suffix.lower())
    if kind is None or not (path.is_file() and inside(base, path)):
        raise APIError(404, "没有这个文件")
    if any(p.is_symlink() for p in [path, *path.parents] if p != base and p.is_relative_to(base)):
        raise APIError(404, "没有这个文件")
    if path.stat().st_size > STATIC_LIMIT:
        raise APIError(413, "文件太大")
    return path.read_bytes(), kind
