"""点了就打开工作台的桌面提醒：Agent 对 issue 的提问和处理结果、监看失败都走这里。

    python3 -m personal.notify_open notify --title 标题 --text 正文 --question 216
    python3 -m personal.notify_open open --question 216        # 点击后执行：没在跑就先启动工作台，再打开页面
    python3 -m personal.notify_open open --issue owner/repo#12

macOS 自带的 display notification 点了没有动作。装了 terminal-notifier 时用它的横幅，点击执行 open；
没装时弹一个带「打开工作台」按钮的对话框。打开的地址：提问 #/q/<编号>（页面找到它所属的 issue 就直接打开详情），
issue #/i/<owner>/<repo>/<编号>，其他为首页。工作台没在跑就在后台启动 python3 -m personal，等它能读再打开，
不注册常驻服务。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PAIHUO_PORT", "8790"))
WAIT_SECONDS = 90
SUBJECT = re.compile(r"([\w.-]+)/([\w.-]+)#([0-9]+)")


def record(line):
    """点没点、开了哪页记到 .personal/notify.log，方便查「点了没反应」。"""
    try:
        path = ROOT / ".personal" / "notify.log"
        path.parent.mkdir(exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def base(port=PORT):
    return f"http://127.0.0.1:{port}"


def subject(value):
    """--issue 只接受 owner/repo#编号。"""
    if not SUBJECT.fullmatch(value or ""):
        raise ValueError(f"issue 要写成 owner/repo#编号：{value!r}")
    return value


def fragment(question=None, issue=None):
    if question:
        return f"#/q/{int(question)}"
    if issue and (match := SUBJECT.fullmatch(issue)):
        return "#/i/{}/{}/{}".format(*match.groups())
    return ""


def alive(port=PORT, timeout=3):
    try:
        with build_opener(ProxyHandler({})).open(base(port) + "/api/board", timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def start(port=PORT):
    """后台启动工作台（脱离当前进程组，日志追加到 .personal/server.log），等到 /api/board 能读。"""
    if alive(port):
        return True
    log = ROOT / ".personal" / "server.log"
    log.parent.mkdir(exist_ok=True)
    with open(log, "a", encoding="utf-8") as stream:
        subprocess.Popen([sys.executable, "-m", "personal", "--port", str(port)], cwd=ROOT, stdout=stream,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if alive(port):
            return True
        time.sleep(1)
    return False


def open_page(question=None, issue=None, *, port=PORT, launch=start, opener=None):
    url = base(port) + "/" + fragment(question, issue)
    if not launch(port):
        dialog("工作台没启动起来", f"{WAIT_SECONDS} 秒内没连上 {base(port)}，看 {ROOT / '.personal/server.log'}。", button=None)
        return None
    (opener or (lambda u: subprocess.run(["open", u], check=False)))(url)
    record(f"打开 {url}")
    return url


def dialog(title, text, *, button="打开工作台"):
    """弹对话框；点了 button 返回 True。没有图形会话或被取消返回 False。"""
    buttons = ["稍后", button] if button else ["好"]
    script = ["on run argv",
              "set answer to display dialog (item 2 of argv) with title (item 1 of argv) buttons "
              + "{" + ", ".join(json.dumps(b, ensure_ascii=False) for b in buttons) + "}"
              + f" default button {len(buttons)} giving up after 1800",
              "return button returned of answer", "end run"]
    args = ["osascript"] + [part for line in script for part in ("-e", line)] + ["--", title, text[:900]]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=1900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        record(f"对话框没弹出：{exc}")
        return False
    record(f"对话框「{title}」：{result.stdout.strip() or result.stderr.strip()[:200]}")
    return bool(button) and result.stdout.strip() == button


def notify(title, text, question=None, issue=None, *, ask=dialog, show_page=open_page):
    """terminal-notifier 在就发可点的横幅；不在就弹对话框，点「打开工作台」再打开页面。"""
    if issue:
        subject(issue)
    notifier = shutil.which("terminal-notifier")
    if notifier:
        command = [sys.executable, "-m", "personal.notify_open", "open"]
        if question:
            command += ["--question", str(int(question))]
        elif issue:
            command += ["--issue", issue]
        # -execute 交给 shell 执行：每一段都用 shlex.quote，路径里有 $、反引号或引号也只是字面值
        execute = f"cd {shlex.quote(str(ROOT))} && {shlex.join(command)}"
        subprocess.run([notifier, "-title", title, "-message", text[:300], "-execute", execute,
                        "-group", f"paihuo-{question or issue or 'watch'}"], capture_output=True, timeout=30)
        return "banner"
    if ask(title, text):
        show_page(question, issue)
        return "opened"
    return "dismissed"


def checked_issue(value):
    try:
        return subject(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="点了就打开工作台的桌面提醒")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("notify", "open"):
        p = sub.add_parser(name)
        p.add_argument("--question", type=int)
        p.add_argument("--issue", type=checked_issue)
        if name == "notify":
            p.add_argument("--title", required=True)
            p.add_argument("--text", default="")
    args = parser.parse_args(argv)
    if args.command == "open":
        return 0 if open_page(args.question, args.issue) else 1
    notify(args.title, args.text, args.question, args.issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
