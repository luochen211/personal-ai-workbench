"""微信插件的「新需求」部分：会话 → 项目的登记（也是白名单），「读新需求」前刷新微信明文库，草稿归项目。

核心的草稿抽屉（personal/issue_drafts.py）不认识微信，只在装了插件时经钩子调这里（见 plugin.json 的 hooks）：
  needs.prepare  发事件之前刷新要读的明文库，返回没刷新成的说明；
  needs.project_of  草稿没写仓库时，按会话登记找项目；
  needs.created  未归类的草稿用户选了仓库并创建后，记住这个会话 → 项目。

会话 → 项目 记在数据目录的 wechat-projects.json（600）。键是会话名（命令行 map 登记的），
或 10 位的 chat_key（账号标识的 md5 前 10 位，和 inbox.chat_key 同一算法；用户在页面给未归类的草稿选了仓库并创建后按它记）。
工作台不读微信库，由会话名算不出 chat_key，所以两种键都认（project_of）。

默认微信号以外的号登记在 accounts.json（WECHAT_ISSUES_ACCOUNTS 可换）：{"accounts": {名: {chats: [会话名], decrypted_dir, home, python}}}，
各在自己的隔离 HOME 下跑读取工具的增量解析，两个号的配置、密钥、明文库、增量状态互不覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

from ... import agent_watch
from ...issue_drafts import RECORDINGS, _private, _text, eligible, path_of, stamp
from . import inbox, reader

CHATS = "wechat-projects.json"
ACCOUNTS_ENV = "WECHAT_ISSUES_ACCOUNTS"
ACCOUNTS = "~/.local/state/wechat-issues/accounts.json"  # 默认微信号以外的号：抽取脚本读同一个文件
ACCOUNT_REFRESH_TIMEOUT = 600
NOT_CHATS = RECORDINGS + ("chat",)  # 录音一段一个键、工作台对话（personal/chat.py）也不是微信会话：不记进白名单


# ---- 会话 → 项目（用户选过一次就记住） ----

def _load_chats(path):
    data = agent_watch.read_json(path, {})
    chats = data.get("chats") if isinstance(data, dict) and isinstance(data.get("chats"), dict) else {}
    return {"chats": {k: v for k, v in chats.items() if isinstance(k, str) and k and isinstance(v, dict) and v.get("project_id")}}


def mappings(data=None):
    """登记了的会话（也是白名单：只有这里有的会话才会被读）：[{key, chat, project_id, at}]。"""
    return [{"key": k, "chat": v.get("chat") or k, "project_id": v["project_id"], "at": v.get("at") or ""}
            for k, v in _load_chats(path_of(data, CHATS))["chats"].items()]


def unmap_chat(chat, *, data=None):
    """去掉一个会话的登记（按键，或登记时记下的会话名）；去掉了返回 True。"""
    path = path_of(data, CHATS)
    with agent_watch.locked(path, _load_chats) as found:
        keys = [k for k, v in found["chats"].items() if k == chat or v.get("chat") == chat]
        for key in keys:
            del found["chats"][key]
    _private(path)
    return bool(keys)


def map_chat(chat, project_id, projects, *, data=None, at=None):
    """登记一个会话 → 项目（键是会话名）；项目要是能开 issue 的。"""
    chat = _text(chat, 100)
    if not chat:
        raise ValueError("会话名是空的")
    if project_id not in {p["id"] for p in eligible(projects)}:
        raise ValueError(f"{project_id} 不是登记表里能开 issue 的项目（没登记、没有 GitHub 仓库、已归档或已暂停）")
    remember_chat(chat, project_id, chat=chat, data=data, at=at)
    return {"key": chat, "chat": chat, "project_id": project_id}


def project_of(chat_key="", chat="", *, data=None):
    """这个会话登记的项目 id：先按 chat_key，再按会话名（键是会话名，或登记时记下的会话名）；没登记是空串。"""
    chats = _load_chats(path_of(data, CHATS))["chats"]
    if chat_key and chat_key in chats:
        return chats[chat_key]["project_id"]
    if chat:
        hit = chats.get(chat) or next((v for v in chats.values() if v.get("chat") == chat), None)
        if hit:
            return hit["project_id"]
    return ""


def remember_chat(chat_key, project_id, *, chat="", data=None, at=None):
    if not chat_key or not project_id:
        return
    path = path_of(data, CHATS)
    with agent_watch.locked(path, _load_chats) as found:
        found["chats"][chat_key] = {"project_id": project_id, "chat": chat or (found["chats"].get(chat_key) or {}).get("chat") or "", "at": stamp(at)}
    _private(path)


def created(draft, *, data=None, at=None):
    """钩子 needs.created：未归类的微信草稿用户选了仓库并创建后，这个会话还没登记过项目才记；
    已登记的会话（白名单）不因为一条开在别处的草稿改对应。"""
    source = draft.get("source", {})
    if source.get("kind") in NOT_CHATS or project_of(source.get("chat_key") or "", source.get("chat") or "", data=data):
        return
    remember_chat(source.get("chat_key"), draft["project_id"], chat=source.get("chat") or "", data=data, at=at)


def project_for(source, *, data=None):
    """钩子 needs.project_of：草稿来源登记的项目 id。"""
    return project_of(source.get("chat_key") or "", source.get("chat") or "", data=data)


# ---- 彻底删除（uninstall --purge）：清掉微信在共用文件里的那部分 ----

def purge(data):
    """数据目录里自己的文件由核心按清单删；这里清共用文件里微信的那部分：
    「暂时不处理」里 wechat: 开头的键，「新需求」里来源是微信、还没创建的草稿（已创建、不创建的历史留着，免得同一件事再拟）。
    录音、工作台对话的草稿和「做完后 @」的对应（wechat-people.json，录音也用）不动；抽取脚本的游标在数据目录外，也不动。"""
    from ... import issue_drafts, snooze
    base = Path(data)
    with agent_watch.locked(snooze.path_of(base), snooze._load) as found:
        keys = [k for k in found["items"] if k.startswith("wechat:")]
        for key in keys:
            del found["items"][key]
    if snooze.path_of(base).exists():
        os.chmod(snooze.path_of(base), 0o600)
    with issue_drafts.book(base) as found:
        ids = [i for i, d in found["drafts"].items() if (d.get("source") or {}).get("kind") not in NOT_CHATS]
        for draft_id in ids:
            del found["drafts"][draft_id]
    return [f"已清掉「暂时不处理」里的微信条目 {len(keys)} 条、还没创建的微信草稿 {len(ids)} 条",
            "没动：微信读取工具和它的明文库、做完后 @ 的对应、抽取脚本的游标"]


# ---- 「读新需求」前刷新微信明文库 ----

def accounts_path():
    return Path(os.environ.get(ACCOUNTS_ENV) or ACCOUNTS).expanduser()


def load_accounts(path=None):
    """默认微信号以外的号：{名: {chats: [会话名], decrypted_dir, home: 隔离的 HOME, python: 可选}}。文件不在、写坏了都是空的。"""
    raw = agent_watch.read_json(path or accounts_path(), {})
    found = raw.get("accounts") if isinstance(raw, dict) else None
    return {k: v for k, v in (found or {}).items() if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("chats"), list)}


def decrypt_account(home, python, vault):
    """在一个号自己的隔离 HOME 下跑读取工具的增量解析。成了返回 True。输出里可能有路径和库名，只看结果，不打印。"""
    return reader.Vault(vault, python).decrypt(ACCOUNT_REFRESH_TIMEOUT, home=home)["ok"]


def refresh_all(data=None, *, decrypt=None):
    """钩子 needs.prepare：发事件之前把要读的明文库刷新一遍：默认号走 inbox.refresh（等它跑完），
    accounts.json 里登记了会话的号各跑一次增量解析。返回没刷新成的说明（数组，空是都成了）；刷新不成不拦这一轮。"""
    base = path_of(data).parent
    chats = {m["chat"] for m in mappings(data)}
    accounts = load_accounts()
    elsewhere = {c for acc in accounts.values() for c in acc["chats"] if isinstance(c, str)}
    notes = []
    cfg = inbox.config(base)
    if chats - elsewhere:  # 有登记的会话在默认微信号里
        if not cfg or cfg["problem"]:
            notes.append("默认微信号的库没刷新：" + (cfg["problem"] if cfg else "工作台没启用微信（没有 wechat.json）"))
        else:
            if not inbox.refresh(base, cfg):  # 已经有一轮在跑：等它跑完
                with inbox._running:
                    pass
            if not (inbox.load_status(base).get("decrypt") or {}).get("ok"):
                notes.append("默认微信号的库没刷新成，读的是上一次刷新时的记录")
    for name, acc in accounts.items():
        if not chats & {c for c in acc["chats"] if isinstance(c, str)}:
            continue
        python, vault = acc.get("python") or (cfg or {}).get("python"), (cfg or {}).get("skill_dir")
        if not acc.get("home") or not python or not vault:
            notes.append(f"{name} 的库没刷新：" + ("accounts.json 里没写 home" if not acc.get("home") else "工作台的 wechat.json 没写 python 或 vault_dir"))
        elif not (decrypt or decrypt_account)(acc["home"], python, vault):
            notes.append(f"{name} 的库没刷新成，读的是上一次刷新时的记录")
    return notes


# ---- 命令行：python3 -m personal.plugins wechat map|unmap|mappings ----

def articles(argv, *, data):
    """python3 -m personal.plugins wechat articles ...：找好各微信号的明文库，用读取工具的 python 跑 articles.py（要 zstandard、expat）。"""
    import subprocess
    import sys
    base = path_of(data).parent
    cfg = inbox.config(base)
    if not cfg or cfg["problem"]:
        print("微信读不了：" + (cfg["problem"] if cfg else "没有 wechat.json"), file=sys.stderr)
        return 1
    source = reader.open_reader(cfg)
    dirs = {"wechat1": source.decrypted_dir()} if hasattr(source, "decrypted_dir") else {}
    for name, acc in load_accounts().items():
        dirs[name] = acc.get("decrypted_dir") or (source.decrypted_dir(acc["home"]) if acc.get("home") else "")
    given = [f"--dir={name}={folder}" for name, folder in dirs.items() if folder]
    script = Path(__file__).with_name("articles.py")
    return subprocess.run([cfg["python"], str(script), *given, *argv], check=False).returncode


def cli(argv, *, data, projects):
    import argparse
    import json
    import sys
    if argv and argv[0] == "articles":
        return articles(argv[1:], data=data)
    parser = argparse.ArgumentParser(prog="python3 -m personal.plugins wechat", description="微信插件：登记要读的会话（白名单）；articles 查公众号文章（articles -h 看用法）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("mappings", help="登记了的会话：会话 → 项目")
    add = sub.add_parser("map", help="登记一个会话 → 项目（白名单加一条）")
    add.add_argument("--chat", required=True, help="会话名（群名或联系人名，和微信里显示的一样）")
    add.add_argument("--project", required=True, help="项目 id（python3 scripts/issue_draft.py projects 列出的 id）")
    drop = sub.add_parser("unmap", help="去掉一个会话的登记")
    drop.add_argument("--chat", required=True)
    args = parser.parse_args(argv)
    if args.command == "mappings":
        print(json.dumps(mappings(data), ensure_ascii=False, indent=1))
        return 0
    if args.command == "map":
        try:
            row = map_chat(args.chat, args.project, projects, data=data)
        except ValueError as exc:
            print(f"没登记上：{exc}", file=sys.stderr)
            return 1
        print(f"已登记：{row['chat']} → {row['project_id']}")
        return 0
    print("已去掉" if unmap_chat(args.chat, data=data) else "没有这个会话的登记")
    return 0
