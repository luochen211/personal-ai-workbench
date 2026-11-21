"""微信插件读微信库的那一层（读取方式）。插件只调用本机已有的读取工具，不带它的代码。

wechat.json 的 reader 选读取方式，现在只有一种：
  vault：本地明文库读取工具。目录（vault_dir）里有
    scripts/decrypt_all_dbs.py  --mode incremental：增量解密到它自己的私密明文库，不碰微信界面；读不到微信源库时退出码 2；
    scripts/vault_cli.py        --include-identifiers pending --since <since> --format json --output <文件> [--previous <上次结果>]：
                                漏看检查（私聊待回、群里 @我、引用我、提到我、@所有人、转账、红包、未接来电）；
                                [--decrypted-dir <明文库>] history <会话名> --start-time <时间> --limit N --format json：
                                {"messages": [{timestamp, time, sender, type, content}]}，「读新需求」用。
  python 是读取工具要用的解释器（装了 pycryptodome）；完全磁盘访问要给它解开符号链接后的真实文件。

要接别的读取工具：写一个类，实现 problem / decrypt / pending / history 这几个方法（返回值和 Vault 一样），登记进 READERS，
wechat.json 写 "reader": "<名字>"。插件其余部分只经这几个方法读微信。
"""
from __future__ import annotations

import json
import json
import os
import subprocess
from pathlib import Path

ABORTED = 2  # decrypt_all_dbs.py 读不到微信源库时的退出码
READABLE = {"文本", "链接/文件", "text", "link", "file"}  # vault_cli history 的 type（中文标签或英文）；图片、语音、系统消息不读


class ReaderError(Exception):
    """读取工具没读成；文字可以给人看（不带聊天内容）。"""


def last_line(*texts, limit=200):
    for text in texts:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        if lines:
            return lines[-1][:limit]
    return ""


def run(command, timeout, env=None):
    """跑一个子进程，返回 (退出码, 标准输出, 标准错误, 超时没有)。输出只留在内存里，不打印。"""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=env)
        return done.returncode, done.stdout, done.stderr, False
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return None, out, err, True
    except OSError as exc:
        return None, "", f"{type(exc).__name__}: {exc.strerror or exc}", False


class Vault:
    """本地明文库读取工具。"""
    name = "vault"
    DECRYPT = "scripts/decrypt_all_dbs.py"
    CLI = "scripts/vault_cli.py"

    def __init__(self, vault_dir, python):
        self.dir = Path(vault_dir) if vault_dir else None
        self.python = str(python or "")

    def problem(self):
        """读取工具目录或 python 不对时的一句原因；都对是空串。"""
        if not self.dir:
            return "wechat.json 没写 vault_dir（微信读取工具的目录）"
        if not self.dir.is_dir():
            return f"找不到微信读取工具的目录：{self.dir}"
        for script in (self.DECRYPT, self.CLI):
            if not (self.dir / script).is_file():
                return f"读取工具目录里没有 {script}：{self.dir}"
        if not self.python:
            return "wechat.json 没写 python（要装了 pycryptodome 的 Python）"
        python = Path(self.python)
        if not python.exists():
            return f"找不到 Python：{python}"
        if not os.access(python, os.X_OK):
            return f"这个 Python 不能运行：{python}"
        return ""

    def decrypt(self, timeout, home=None):
        """增量解密一轮。home 给了就在这个隔离的 HOME 下跑（另一个微信号：配置、密钥、明文库、增量状态互不覆盖）。
        返回 {ok, code, aborted（读不到微信目录）, timeout, line（最后一行输出，只给本机页面看）}。"""
        env = {**os.environ, "HOME": str(Path(home).expanduser())} if home else None
        code, out, err, timed_out = run([str(Path(self.python).expanduser()), str(self.dir / self.DECRYPT), "--mode", "incremental"],
                                        timeout, env)
        text = f"{out}\n{err}"
        aborted = code == ABORTED or "unreadable" in text or "lacks macOS permission" in text
        return {"ok": code == 0 and not aborted, "code": code, "aborted": aborted, "timeout": timed_out,
                "line": "解析超时" if timed_out else last_line(out, err)}

    def pending(self, since, output, previous, timeout):
        """漏看检查写进 output（JSON）。返回 {ok, code, timeout, error}；ok 时 output 里是结果。"""
        command = [self.python, str(self.dir / self.CLI), "--include-identifiers", "pending", "--since", str(since), "--format", "json",
                   "--output", str(output)]
        if previous and Path(previous).is_file():
            command += ["--previous", str(previous)]
        code, out, err, timed_out = run(command, timeout)
        ok = code == 0 and Path(output).is_file()
        error = "" if ok else "漏看检查超时" if timed_out else f"漏看检查没跑成：{last_line(err, out) or f'退出码 {code}'}"
        return {"ok": ok, "code": code, "timeout": timed_out, "error": error}

    def history(self, chat, since, *, decrypted_dir="", timeout=120, limit=5000):
        """一个会话 since 之后能读成文字的消息：[{ts, time, from, text}]，按读取工具给的顺序。读不成抛 ReaderError
        （原因只带命令名和退出码：输出里可能有聊天内容）。decrypted_dir 是另一个微信号的明文库。"""
        where = ["--decrypted-dir", str(Path(decrypted_dir).expanduser())] if decrypted_dir else []
        code, out, _err, timed_out = run([self.python, str(self.dir / self.CLI), *where, "history", chat,
                                          "--start-time", since.strftime("%Y-%m-%d %H:%M:%S"), "--limit", str(limit), "--format", "json"], timeout)
        if timed_out:
            raise ReaderError(f"微信本地库 history 超过 {timeout} 秒没返回")
        if code != 0:
            raise ReaderError(f"微信本地库 history 退出码 {code}")
        try:
            found = json.loads(out)
        except ValueError:
            raise ReaderError("微信本地库的输出不是 JSON") from None
        if not isinstance(found, dict) or not isinstance(found.get("messages"), list):
            raise ReaderError("微信本地库的输出格式不对")
        rows = []
        for row in found["messages"]:
            if not isinstance(row, dict) or row.get("type") not in READABLE:
                continue
            ts, text = row.get("timestamp"), str(row.get("content") or "").strip()
            if isinstance(ts, (int, float)) and not isinstance(ts, bool) and text:
                rows.append({"ts": int(ts), "time": str(row.get("time") or ""), "from": str(row.get("sender") or ""), "text": text})
        return rows


    def decrypted_dir(self, home=None, timeout=60):
        """读取工具的明文库目录（vault_cli.py status）；home 给了是另一个微信号的。问不到是空串。"""
        env = {**os.environ, "HOME": str(Path(home).expanduser())} if home else None
        code, out, _, _ = run([self.python, str(self.dir / self.CLI), "status", "--format", "json"], timeout, env)
        try:
            found = json.loads(out) if code == 0 else {}
        except ValueError:
            return ""
        return str(found.get("decrypted_dir") or "") if isinstance(found, dict) and found.get("exists") else ""


READERS = {Vault.name: Vault}


def open_reader(cfg):
    """按 wechat.json 选读取方式（没写是 vault）；不认识的读取方式给一个只会报原因的对象。"""
    kind = str(cfg.get("reader") or Vault.name)
    if kind not in READERS:
        return Unknown(kind)
    return READERS[kind](cfg.get("skill_dir") or "", cfg.get("python") or "")


class Unknown:
    def __init__(self, kind):
        self.name = kind

    def problem(self):
        return f"wechat.json 的 reader 不认识：{self.name}（现有：{'、'.join(READERS)}）"
