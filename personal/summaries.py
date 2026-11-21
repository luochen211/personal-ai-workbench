"""项目总览的一句简介：projects.toml 的 summary → GitHub 仓库描述（缓存一天）→ 本地 README 第一段，都没有就空着。"""
from __future__ import annotations

import json
import re
import subprocess

from . import github
import threading
import time
from pathlib import Path

LIMIT = 60
CACHE_SECONDS = 86400
REPO = re.compile(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
RETRY_SECONDS = 600
_lock = threading.Lock()
_failed = {"at": 0.0}


def clip(text, limit=LIMIT):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def plain(line):
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)            # 图片、徽章
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)         # 链接留文字
    line = re.sub(r"<[^>]+>", "", line)                           # HTML 标签
    line = re.sub(r"[*_`~]+", "", line)
    return line.strip(" >-|\t")


def skip(line):
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "[![", "![", "<!--", "---", "===", "|", "```")):
        return True
    if re.match(r"^<(img|p|div|a|h\d|br|picture|source|center)\b", stripped, re.I) and not plain(stripped):
        return True
    # 目录导航行：几个短词用 · 或 | 隔开，例如「快速开始 · 现在能做什么 · 当前进展」
    parts = re.split(r"\s+[·|]\s+", plain(stripped))
    if len(parts) >= 3 and all(len(part) <= 12 for part in parts):
        return True
    # 语言切换行：几个语言名用 | 或 · 隔开
    return bool(re.search(r"(English|中文|简体|繁體|日本語|한국어)", stripped)) and len(re.findall(r"[|·/]", stripped)) >= 1 \
        and len(plain(stripped)) <= 40


def readme_summary(repo_path):
    if not repo_path:
        return ""
    folder = Path(repo_path)
    if not folder.is_dir():
        return ""
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.name.lower() in {"readme.md", "readme", "readme.txt", "readme.zh.md", "readme_zh.md"}),
                   key=lambda p: (p.name.lower() != "readme.md", p.name))
    for file in files:
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        fenced, front = False, lines[:1] == ["---"]
        paragraph = []
        for index, line in enumerate(lines):
            if front:
                front = not (index > 0 and line.strip() == "---")
                continue
            if line.strip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            if skip(line) or not plain(line):
                if paragraph:
                    break
                continue
            paragraph.append(plain(line))
        if paragraph:
            return clip(" ".join(paragraph).rstrip("：:"))
    return ""


def fetch_descriptions(repos):
    """一次 GraphQL 查完所有仓库的描述；读不到的仓库就没有这一项。"""
    pairs = [REPO.fullmatch(r) for r in repos]
    pairs = [m for m in pairs if m]
    result = {}
    for start in range(0, len(pairs), 50):
        chunk = pairs[start:start + 50]
        fields = " ".join(f'r{i}: repository(owner: {json.dumps(m[1])}, name: {json.dumps(m[2])}) {{ nameWithOwner description }}'
                          for i, m in enumerate(chunk))
        data = github.graphql(f"query {{ {fields} }}", kind="graphql:descriptions")
        for i, m in enumerate(chunk):
            repo = data.get(f"r{i}")
            if repo is not None:
                result[m[0].lower()] = repo.get("description") or ""
    return result


def descriptions(data, repos, *, fetch=None, now=None):
    """仓库描述缓存在 .personal/descriptions.json，一天内不重查。"""
    now = now or time.time()
    path = Path(data) / "descriptions.json"
    with _lock:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        wanted = sorted({r.lower() for r in repos if r})
        stale = now - cache.get("at", 0) > CACHE_SECONDS or not set(wanted) <= set(cache.get("repos", []))
        if stale and now - _failed["at"] > RETRY_SECONDS:
            try:
                found = (fetch or fetch_descriptions)(wanted)
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                found = None
                _failed["at"] = now  # 读不到就 10 分钟后再试，不随每次页面刷新重发
            if found is not None:
                cache = {"at": now, "repos": wanted, "descriptions": found}
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return cache.get("descriptions", {})


def summarize(project, described):
    """返回 (简介, 来源)；三处都没有时简介为空。"""
    if str(project.get("summary", "")).strip():
        return clip(project["summary"]), "projects.toml"
    for repo in (project.get("repository"), project.get("github")):
        text = described.get(str(repo or "").lower(), "")
        if text.strip():
            return clip(text), "GitHub"
    text = readme_summary(project.get("repo_path"))
    return (text, "README") if text else ("", "")
