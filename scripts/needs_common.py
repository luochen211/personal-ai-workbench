"""「读新需求」各来源的抽取脚本共用的部分：跑命令、问模型、把草稿交给工作台、交回结果。

用它的：微信插件的 personal/plugins/wechat/extract.py，和放在别处的录音抽取脚本（import 前把本目录加进 sys.path）。
草稿经本目录的 issue_draft.py 交进工作台的数据目录（环境变量 WORKBENCH_DATA 可换），等用户在「新需求」抽屉里确认后才创建。

问模型：默认是不带工具、不存会话记录的 claude -p（在空的临时目录里跑，结构化输出）。换模型：环境变量 NEEDS_MODEL_COMMAND
写一条命令（按 shell 规则拆开），它要接受和 claude -p 一样的参数（--output-format json --json-schema … --system-prompt …），
标准输入读 JSON，标准输出给 {"structured_output": {...}} 或 {"result": "<含 JSON 的文字>"}。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL_ENV = "NEEDS_MODEL_COMMAND"
CLAUDE_CANDIDATES = ("~/.npm-global/bin/claude", "~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude")  # 中台派出的进程 PATH 里不一定有

COMMAND_TIMEOUT = 120
MODEL_TIMEOUT = 300
MAX_TEXT = 600  # 一条消息最多这么多字
MAX_ITEMS = 8  # 一次最多拟这么多条
MAX_SKIPPED = 5  # 一次最多交回这么多个没拟的话题
MAX_REASON = 60
MAX_EXCERPT = 6
MAX_REPO_DESCRIPTION = 200
NEW_REPO_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,59}")  # 模型提议的仓库名：小写英文、数字、短横线
IDLE_MARK = "没事可做"  # 中台按第一行的这四个字认空跑

ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "project_id": {"type": "string"},
        "message_ids": {"type": "array", "items": {"type": "integer"}},
        "new_repo": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"],
        },
    },
    "required": ["title", "body", "project_id", "message_ids"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": ITEM},
        "skipped": {
            "type": "array",
            "items": {**ITEM, "properties": {**ITEM["properties"], "reason": {"type": "string"}},
                      "required": [*ITEM["required"], "reason"]},
        },
    },
    "required": ["items"],
}


class ScanError(Exception):
    """这一轮没跑成；文字会回写给工作台显示（不带聊天内容）。"""


def run(args: list[str], *, name: str = "", stdin: str | None = None, timeout: int = COMMAND_TIMEOUT, cwd: str | None = None,
        env: dict | None = None, ok=(0,)) -> str:
    name = name or Path(args[0]).name
    try:
        proc = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd, env=env)
    except FileNotFoundError:
        raise ScanError(f"找不到命令 {name}") from None
    except subprocess.TimeoutExpired:
        raise ScanError(f"{name} 超过 {timeout} 秒没返回") from None
    if proc.returncode not in ok:
        # 只带命令名和退出码：stderr 里可能有聊天内容，不往外带。
        raise ScanError(f"{name} 退出码 {proc.returncode}")
    return proc.stdout


# ---------------------------------------------------------------- 工作台


def draft_cli(*args: str, stdin: str | None = None, ok=(0,)) -> str:
    return run([sys.executable, str(HERE / "issue_draft.py"), *args], name=f"工作台 issue_draft {args[0]}", stdin=stdin, ok=ok)


def draft_json(*args: str) -> Any:
    try:
        return json.loads(draft_cli(*args))
    except ValueError:
        raise ScanError(f"工作台的 {args[0]} 输出不是 JSON") from None


def scan_done(source: str, found: int, saved: int, *, skipped: int = 0, error: str = "") -> None:
    """一个来源跑完，把条数（或没跑成的原因）交回工作台，「读新需求」按钮旁显示。"""
    draft_cli("scan-done", "--source", source, "--found", str(found), "--saved", str(saved), "--skipped", str(skipped),
              *(["--error", error] if error else []))


def saved_count(output: str) -> int:
    match = re.search(r"已存\s*(\d+)\s*条", output)
    return int(match.group(1)) if match else 0


def held_count(output: str) -> int:
    match = re.search(r"(\d+)\s*条没拟的话题", output)
    return int(match.group(1)) if match else 0


def proposed_repo(item: dict) -> dict | None:
    """模型提议新建的仓库：只有 project_id 留空、名字像样时才算数；其余一律当作没提议。工作台存草稿时还会再校验一遍。"""
    raw = item.get("new_repo")
    if str(item.get("project_id") or "").strip() or not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if not NEW_REPO_NAME.fullmatch(name):
        return None
    return {"name": name, "description": " ".join(str(raw.get("description") or "").split())[:MAX_REPO_DESCRIPTION]}


# ---------------------------------------------------------------- 模型


def model_command() -> list[str]:
    given = os.environ.get(MODEL_ENV)
    if given:
        return shlex.split(given)
    for path in CLAUDE_CANDIDATES:
        full = Path(path).expanduser()
        if full.is_file():
            return [str(full)]
    return ["claude"]


def ask_model(payload: dict, system: str) -> list[dict]:
    """把一段材料交给不带工具的模型，拿回 items。在空的临时目录里跑，不存会话记录。"""
    return ask(payload, system)[0]


def ask(payload: dict, system: str) -> tuple[list[dict], list[dict]]:
    """拿回 (items, skipped)，skipped 是模型认为相关但没拟的话题（要求里没让交的就是空的）。"""
    args = [*model_command(), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--system-prompt", system]
    with tempfile.TemporaryDirectory(prefix="needs-") as empty:
        out = run(args, stdin=json.dumps(payload, ensure_ascii=False), name="模型", timeout=MODEL_TIMEOUT, cwd=empty)
    try:
        reply = json.loads(out)
    except ValueError:
        raise ScanError("模型的输出不是 JSON") from None
    if isinstance(reply, dict) and reply.get("is_error"):
        raise ScanError("模型这一轮报错了")
    found = reply.get("structured_output") if isinstance(reply, dict) else None
    if not isinstance(found, dict):
        found = parse_loose(reply.get("result") if isinstance(reply, dict) else None)
    items = found.get("items") if isinstance(found, dict) else None
    if not isinstance(items, list):
        raise ScanError("模型没按格式交回结果")
    held = found.get("skipped") if isinstance(found.get("skipped"), list) else []
    return [x for x in items if isinstance(x, dict)][:MAX_ITEMS], [x for x in held if isinstance(x, dict)][:MAX_SKIPPED]


def parse_loose(text: Any) -> Any:
    if not isinstance(text, str):
        return None
    match = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(match.group(0)) if match else None
    except ValueError:
        return None
