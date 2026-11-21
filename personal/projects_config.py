"""项目登记表：仓库里的 personal/projects.toml，加上数据目录里可选的本机覆盖文件 projects.local.toml。

本机覆盖文件放不想进仓库的私有项目、客户项目（数据目录整个不进仓库），写法和 projects.toml 一样：
- [[projects]]：id 和登记表里的相同就整条替换，不同的追加在后面；
- [discovery]：写了的键替换登记表里的同名键（例如 skip_owners、roots）；
- [owner]：本人的 GitHub 账号（login）、其他账号（accounts）和提交邮箱（emails），同样是写了的键优先。
覆盖文件登记的项目照常列出、交给：skip_owners 只管自动发现时不往 projects.toml 追加，不隐藏已登记的项目。
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "personal/projects.toml"
LOCAL_NAME = "projects.local.toml"


def data_dir():
    return Path(os.environ.get("WORKBENCH_DATA") or ROOT / ".personal").resolve()


def local_path():
    """本机覆盖文件的位置：数据目录（WORKBENCH_DATA，默认 .personal/）里的 projects.local.toml。"""
    return data_dir() / LOCAL_NAME


def read(path):
    path = Path(path)
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path.name} 写法不对：{exc}") from None


def overlay(local=None):
    """本机覆盖文件的内容；没有这个文件是空的。"""
    path = Path(local) if local is not None else local_path()
    if not path.is_file():
        return {}
    found = read(path)
    if not isinstance(found.get("projects", []), list) or not isinstance(found.get("discovery", {}), dict) \
            or not isinstance(found.get("owner", {}), dict):
        raise ValueError(f"{path.name} 里 projects 要写成 [[projects]]，discovery 要写成 [discovery]，owner 要写成 [owner]")
    return found


def merge(base, extra):
    """登记表的项目加上覆盖文件的项目：同一个 id 整条换成覆盖文件的（位置不变），新的按覆盖文件里的顺序追加。"""
    out = list(base)
    index = {p.get("id"): i for i, p in enumerate(out)}
    for project in extra:
        if project.get("id") in index:
            out[index[project.get("id")]] = project
        else:
            index[project.get("id")] = len(out)
            out.append(project)
    return out


def load(base=None, local=None):
    """{"owner": {...}, "discovery": {...}, "projects": [...]}：登记表合上本机覆盖文件。local 不给就用 local_path()。"""
    doc, extra = read(base or BASE), overlay(local)
    return {"owner": {**(doc.get("owner") or {}), **(extra.get("owner") or {})},
            "discovery": {**(doc.get("discovery") or {}), **(extra.get("discovery") or {})},
            "projects": merge(doc.get("projects") or [], extra.get("projects") or [])}
