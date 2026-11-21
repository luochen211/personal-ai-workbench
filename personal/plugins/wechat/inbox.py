"""微信插件的收件箱部分（用户 2026-11-19 定，2026-11-21 改成插件）：收件箱里的「微信」范围，只读本机微信读取工具的漏看检查。

装了微信插件（personal/plugins，登记表 plugins.json；还没有登记表时有 wechat.json 就当已装）才启用，
没装就整块不出现：接口报 enabled: false，写接口拒绝，后台不刷新。配置是数据目录里的 wechat.json：
  {"reader": "vault", "vault_dir": "<微信读取工具目录>", "python": "<装了 pycryptodome 的 python>",
   "since": "7d", "interval_minutes": 15}
（老配置写的 skill_dir 照样认，等同 vault_dir。）读取工具目录里要有 scripts/decrypt_all_dbs.py 和 scripts/vault_cli.py。
后台每 interval_minutes 分钟（收件箱「一键读取」也会触发，不等它跑完）：
  1. <python> <vault_dir>/scripts/decrypt_all_dbs.py --mode incremental：只更新读取工具自己的私密明文库，不碰微信界面；
  2. <python> <vault_dir>/scripts/vault_cli.py --include-identifiers pending --since <since> [--previous 上次结果] --format json；
  3. 结果去掉账号标识（username 换成它的 md5 前 10 位 chat_key，和读取工具给私聊的 chat_key 同一算法）写进 wechat-pending.json（权限 600）。
同一时间只跑一轮（进程内锁加数据目录文件锁），两个子进程都有超时。读不到微信目录（没给完全磁盘访问）时照样给上次的数据，
再加一句去哪里打开权限。

消息原文只在数据目录（600）和本机页面里：不写日志（server.log）、不进离线快照 board.html。
页面上一行是「一类信号 × 一个会话」：私聊待回、群里 @我、引用我、提到我、@所有人、待收转账、转账已退回、我转出未被收、红包、未接来电。
只有私聊待回、群里 @我、待收转账算「待你回」（计入导航角标），疑似客套/表情的私聊不算；其余在「待你看」。
微信只读：「已处理」只记本机 wechat-done.json，这个会话有更新的消息才回来；「暂时不处理」用 snooze.py（键 wechat:<类>:<chat_key>）。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from ... import agent_watch, plugins, snooze
from . import reader

CONFIG = "wechat.json"
PENDING = "wechat-pending.json"
STATUS = "wechat-status.json"
DONE = "wechat-done.json"
LOCK = "wechat-refresh.lock"
DEFAULTS = {"since": "7d", "interval_minutes": 15, "decrypt_timeout": 900, "pending_timeout": 300}
KEY = re.compile(r"wechat:([a-z_]+):([0-9a-f]{6,40})")
DONE_KEEP_DAYS = 30
WAKE = 60  # 后台线程最多睡这么久醒来看一次到没到点

# (类, 页面上的信号, 图标, 是否算「待你回」)；顺序就是页面里同一时刻的排序
KINDS = [
    ("private", "私聊待回", "chat", True),
    ("at_me", "群里 @我", "at", True),
    ("quote_me", "引用我", "quote", False),
    ("name_mention", "提到我", "mention", False),
    ("at_all", "@所有人", "all", False),
    ("pending_to_me", "待收转账", "money", True),
    ("refunded_to_me", "转账已退回", "money", False),
    ("pending_from_me", "我转出未被收", "money", False),
    ("red_packets", "红包", "packet", False),
    ("missed_calls", "未接来电", "call", False),
]
LABELS = {k: label for k, label, _, _ in KINDS}
ICONS = {k: icon for k, _, icon, _ in KINDS}
REPLY = {k for k, _, _, reply in KINDS if reply}
PERMISSION = "后台进程没有读微信目录的权限，到 系统设置 → 隐私与安全性 → 完全磁盘访问 给 {binary} 打开"

_running = threading.Lock()


def path_of(data, name):
    return Path(data) / name


def now():
    return datetime.now().astimezone()


# ---- 配置 ----

def config(data, *, check_plugin=True):
    """wechat.json：没装微信插件或没有这个文件是 None（不启用）；写坏了也算启用，problem 里写原因。"""
    if check_plugin and not plugins.installed(data, "wechat"):
        return None
    path = path_of(data, CONFIG) if data else None
    if not path or not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {**DEFAULTS, "skill_dir": "", "python": "", "problem": f"wechat.json 读不了：{type(exc).__name__}"}
    if not isinstance(raw, dict):
        return {**DEFAULTS, "skill_dir": "", "python": "", "problem": "wechat.json 要写成 JSON 对象"}
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if v not in (None, "")}}
    cfg["skill_dir"] = cfg.get("vault_dir") or cfg.get("skill_dir")  # vault_dir 是现在的写法，skill_dir 是老写法
    cfg["skill_dir"] = str(Path(str(cfg.get("skill_dir") or "")).expanduser()) if cfg.get("skill_dir") else ""
    cfg["python"] = str(Path(str(cfg.get("python") or "")).expanduser()) if cfg.get("python") else ""
    if not re.fullmatch(r"\d+[hd]|last", str(cfg["since"])):
        cfg["since"] = DEFAULTS["since"]
    for key in ("interval_minutes", "decrypt_timeout", "pending_timeout"):
        try:
            cfg[key] = max(1, int(cfg[key]))
        except (TypeError, ValueError):
            cfg[key] = DEFAULTS[key]
    cfg["problem"] = problem(cfg)
    return cfg


def problem(cfg):
    """读取方式配置不对时的一句原因；都对是空串。"""
    return reader.open_reader(cfg).problem()


def real_binary(python):
    """完全磁盘访问要加的是真正的可执行文件：venv 里的 python 多半是符号链接，顺着解开。"""
    try:
        return str(Path(python).resolve())
    except OSError:
        return str(python)


# ---- 后台刷新 ----

def _write_private(path, data):
    """临时文件写完再换名，权限 600：里面有聊天原文。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def chat_key(value):
    """和读取工具 vault_cli.chat_key 同一算法：账号标识的 md5 前 10 位，换了名字也认得是同一个会话。"""
    return hashlib.md5(str(value).encode("utf-8")).hexdigest()[:10]


def sanitize(data):
    """去掉账号标识：每条按 username 算 chat_key 再删掉 username；没有 username（旧结果）就按会话名算。"""
    def fix(item):
        if not isinstance(item, dict):
            return item
        name = item.pop("username", None)
        if not item.get("chat_key"):
            item["chat_key"] = chat_key(name if isinstance(name, str) and name and not name.startswith("[") else item.get("chat") or "")
        return item
    for item in data.get("private_pending") or []:
        fix(item)
    for group in ("groups", "money"):
        for items in (data.get(group) or {}).values():
            for item in items or []:
                fix(item)
    for item in data.get("missed_calls") or []:
        fix(item)
    return data


def refresh(data, cfg=None):
    """跑一轮：先增量解析，再出漏看检查。已经在跑就返回 False，不排队、不叠着跑。"""
    cfg = cfg or config(data)
    if not cfg or cfg["problem"]:
        return False
    if not _running.acquire(blocking=False):
        return False
    try:
        with open(path_of(data, LOCK), "a", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False  # 别的工作台进程在跑同一个数据目录
            try:
                _refresh(data, cfg)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return True
    finally:
        _running.release()


def _refresh(data, cfg):
    started = now().isoformat(timespec="seconds")
    source = reader.open_reader(cfg)
    status = {"started_at": started, "python": real_binary(cfg["python"])}
    _write_private(path_of(data, STATUS), {**load_status(data), "started_at": started, "running": True})

    status["decrypt"] = source.decrypt(cfg["decrypt_timeout"])
    aborted = status["decrypt"]["aborted"]

    target = path_of(data, PENDING)
    tmp = path_of(data, f".{PENDING}.new")
    pending = source.pending(cfg["since"], tmp, target, cfg["pending_timeout"])
    if pending["ok"]:
        try:
            result = json.loads(tmp.read_text(encoding="utf-8"))
            if not isinstance(result, dict):
                raise ValueError("不是 JSON 对象")
            _write_private(target, sanitize(result))
        except (OSError, ValueError) as exc:
            pending.update(ok=False, error=f"漏看检查的结果读不了：{type(exc).__name__}")
    try:
        tmp.unlink()
    except OSError:
        pass
    status["pending"] = pending
    status["finished_at"] = now().isoformat(timespec="seconds")
    status["running"] = False
    _write_private(path_of(data, STATUS), status)
    if not pending["ok"] or not status["decrypt"]["ok"]:
        # 只写一句，不带输出原文
        print(f"微信数据刷新：{'解析中止' if aborted else '解析失败' if not status['decrypt']['ok'] else '解析完成'}；"
              f"{'漏看检查完成' if pending['ok'] else '漏看检查失败'}", file=sys.stderr, flush=True)


def load_status(data):
    raw = agent_watch.read_json(path_of(data, STATUS), {})
    return raw if isinstance(raw, dict) else {}


DENIED_BACKOFF_MINUTES = 360


def due(data, cfg, at=None):
    """上一轮开始超过 interval_minutes 分钟（或从没跑过）就该跑了。
    上一轮解析因权限中止时改为每 6 小时自动重试一次：每次中止读取工具都会在 vault 里多写一个 manifest；
    开了权限后点「一键读取」（kick）立即重试，不受这个间隔限制。"""
    status = load_status(data)
    started = status.get("started_at")
    try:
        last = agent_watch.parse_time(started) if started else None
    except (TypeError, ValueError):
        last = None
    minutes = cfg["interval_minutes"]
    if (status.get("decrypt") or {}).get("aborted"):
        minutes = max(minutes, DENIED_BACKOFF_MINUTES)
    return not last or ((at or now()) - last).total_seconds() >= minutes * 60


def kick(data):
    """收件箱「一键读取」：在后台跑一轮，不等它跑完。没启用、配置不对、已经在跑都不再起。"""
    cfg = config(data)
    if not cfg:
        return {"enabled": False, "started": False}
    if cfg["problem"]:
        return {"enabled": True, "started": False, "problem": cfg["problem"]}
    if _running.locked():
        return {"enabled": True, "started": False, "running": True}
    threading.Thread(target=refresh, args=(data, cfg), daemon=True, name="wechat-refresh").start()
    return {"enabled": True, "started": True}


def keep_fresh(data, stop, wake=WAKE):
    """后台线程：每次醒来读一次配置（随时加上 wechat.json 也生效），到点就跑一轮。"""
    while not stop.is_set():
        try:
            cfg = config(data)
            if cfg and not cfg["problem"] and due(data, cfg):
                refresh(data, cfg)
        except Exception as exc:  # 线程不能死；只写类型，不写可能带原文的内容
            print(f"微信数据刷新出错：{type(exc).__name__}", file=sys.stderr, flush=True)
        stop.wait(wake)


def start_keeper(data, stop=None):
    stop = stop or threading.Event()
    thread = threading.Thread(target=keep_fresh, args=(data, stop), daemon=True, name="wechat-keeper")
    thread.start()
    return thread, stop


# ---- 页面上的行 ----

def _at(text):
    """读取工具给的「YYYY-MM-DD HH:MM」（本机时间）换成带时区的 ISO；读不了是空串。"""
    try:
        return datetime.strptime(str(text), "%Y-%m-%d %H:%M").astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def _ts(item):
    if isinstance(item.get("last_ts"), int):
        return item["last_ts"]
    if isinstance(item.get("ts"), int):
        return item["ts"]
    at = _at(item.get("time"))
    return int(datetime.fromisoformat(at).timestamp()) if at else 0


def _msg(item):
    return {"time": _at(item.get("time")), "from": str(item.get("from") or ""), "text": str(item.get("text") or "")}


def _money_text(item, kind):
    amount = str(item.get("amount") or "").strip()
    memo = f"（备注：{item['memo']}）" if item.get("memo") else ""
    if kind == "pending_from_me":
        return f"转给 {item.get('to') or '对方'} {amount}{memo}，对方还没收"
    who = item.get("from") or "对方"
    if kind == "refunded_to_me":
        back = f"{item['refunded_at']} " if item.get("refunded_at") else ""
        return f"{who} 转 {amount}{memo}，{back}已退回"
    return f"{who} 转 {amount}{memo}，还没收"


def _entries(raw):
    """读取工具的结果拆成 (类, 条目)。"""
    for item in raw.get("private_pending") or []:
        yield "private", item
    for kind in ("at_me", "quote_me", "name_mention", "at_all"):
        for item in (raw.get("groups") or {}).get(kind) or []:
            yield kind, item
    for kind in ("pending_to_me", "refunded_to_me", "pending_from_me", "red_packets"):
        for item in (raw.get("money") or {}).get(kind) or []:
            yield kind, item
    for item in raw.get("missed_calls") or []:
        yield "missed_calls", item


def _notes(kind, item):
    notes = []
    if kind == "private":
        if item.get("kind"):
            notes.append(str(item["kind"]))
        notes.append(f"待回 {item.get('waiting_count') or len(item.get('messages') or [])} 条")
        if item.get("low_signal"):
            notes.append("疑似客套/表情")
        if item.get("seen_before"):
            notes.append("上期已列出")
    if item.get("muted_group"):
        notes.append("免打扰群")
    if item.get("you_spoke_after"):
        notes.append("之后你在群里说过话")
    if item.get("maybe_expired"):
        notes.append("可能已过期")
    elif item.get("expires"):
        notes.append(f"{item['expires']} 截止")
    if item.get("opened_hint"):
        notes.append("疑似已领取")
    if item.get("you_contacted_after"):
        notes.append("之后你联系过对方")
    return notes


def rows(raw):
    """一行是「一类信号 × 一个会话」：同一个群的几条 @我 合成一行，按最新一条排。"""
    found = {}
    for kind, item in _entries(raw):
        if not isinstance(item, dict) or kind not in LABELS:
            continue
        key = f"wechat:{kind}:{item.get('chat_key') or chat_key(item.get('chat') or '')}"
        row = found.setdefault(key, {"key": key, "kind": kind, "label": LABELS[kind], "icon": ICONS[kind],
                                     "chat": str(item.get("chat") or "未知会话"), "ts": 0, "hits": [], "notes": [],
                                     "low_signal": False})
        ts = _ts(item)
        if kind == "private":
            hit = {"messages": [_msg(m) for m in item.get("messages") or [] if isinstance(m, dict)], "context": []}
            more = (item.get("waiting_count") or 0) - len(hit["messages"])
            if more > 0:
                hit["more"] = f"更早还有 {more} 条（自 {item.get('waiting_since') or '更早'} 起）"
            row["low_signal"] = bool(item.get("low_signal"))
        elif kind in ("pending_to_me", "refunded_to_me", "pending_from_me"):
            hit = {"messages": [{"time": _at(item.get("time")), "from": "", "text": _money_text(item, kind)}], "context": []}
        else:
            hit = {"messages": [_msg(item)], "context": [_msg(m) for m in item.get("context") or [] if isinstance(m, dict)]}
        hit["ts"] = ts
        row["hits"].append(hit)
        row["notes"] = list(dict.fromkeys(row["notes"] + _notes(kind, item)))
        row["ts"] = max(row["ts"], ts)
    out = []
    order = {k: n for n, (k, *_rest) in enumerate(KINDS)}
    for row in found.values():
        row["hits"].sort(key=lambda h: h["ts"])
        last = row["hits"][-1]["messages"]
        row["at"] = datetime.fromtimestamp(row["ts"]).astimezone().isoformat(timespec="seconds") if row["ts"] else ""
        row["text"] = [f"{m['from']}：{m['text']}" if m["from"] and m["from"] != row["chat"] else m["text"] for m in last[-2:]]
        row["count"] = len(row["hits"])
        out.append(row)
    out.sort(key=lambda r: (-r["ts"], order[r["kind"]]))
    return out


def bucket(row):
    """不算暂时不处理时落哪一类：私聊待回（不是客套）、群里 @我、待收转账是待你回，其余待你看。"""
    return "reply" if row["kind"] in REPLY and not row["low_signal"] else "look"


# ---- 已处理（只记本机） ----

def _load_done(path):
    raw = agent_watch.read_json(path, {})
    items = raw.get("items") if isinstance(raw, dict) and isinstance(raw.get("items"), dict) else {}
    return {"items": {k: v for k, v in items.items() if KEY.fullmatch(k) and isinstance(v, dict) and isinstance(v.get("ts"), int)}}


def load_done(data):
    return _load_done(path_of(data, DONE))["items"]


def mark_done(data, key, ts):
    """「已处理」：记下这一行最新一条消息的时间，这个会话有更新的消息才回来。"""
    key = str(key or "")
    if not KEY.fullmatch(key):
        raise ValueError("写法不对：要写 wechat:<类>:<会话>")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
        raise ValueError("缺少这一行最新一条消息的时间")
    stamp = now()
    cutoff = stamp.timestamp() - DONE_KEEP_DAYS * 86400
    with agent_watch.locked(path_of(data, DONE), _load_done) as book:
        for old in [k for k, v in book["items"].items() if v["ts"] < cutoff]:
            del book["items"][old]
        book["items"][key] = {"ts": ts, "at": stamp.isoformat(timespec="seconds")}
    os.chmod(path_of(data, DONE), 0o600)
    return {"done": key}


def done(data, body):
    """POST /api/wechat/done 的请求体：{key, ts}。"""
    return mark_done(data, body.get("key"), body.get("ts"))


def kick_route(data, body):
    """POST /api/wechat/refresh：一键读取，后台跑一轮，不等它跑完。"""
    return kick(data)


# ---- 接口 ----

def _hint(status, raw, cfg):
    """一句提示：读不到微信目录给去哪开权限；读取工具目录、python 不对给原因；漏看检查失败给原因。"""
    if cfg["problem"]:
        return cfg["problem"]
    decrypt = status.get("decrypt") or {}
    gaps = [str(g) for g in ((raw.get("manifest") or {}).get("gaps") or [])]
    denied = decrypt.get("aborted") or any(("中止" in g or "unreadable" in g or "超过" in g) for g in gaps)
    lines = []
    if denied:
        binary = status.get("python") or real_binary(cfg["python"])
        lines.append(PERMISSION.format(binary=binary))
        server = real_binary(sys.executable)
        if server != binary:
            lines[-1] += f"（工作台由 launchd 在后台启动时，运行工作台的 {server} 也要打开）"
    elif decrypt.get("timeout"):
        lines.append("微信数据解析超时，下一轮再试")
    elif decrypt and not decrypt.get("ok") and decrypt.get("line"):
        lines.append(f"微信数据解析没成功：{decrypt['line']}")
    elif gaps:
        lines.append(f"微信数据不完整：{gaps[0][:160]}")
    pending = status.get("pending") or {}
    if pending and not pending.get("ok") and pending.get("error"):
        lines.append(pending["error"])
    return "；".join(lines)


def view(data, at_now=None):
    """GET /api/wechat：没配置只回 {enabled: false}；配置了给行（已处理的藏起来，暂时不处理的落搁置）和新鲜度。"""
    cfg = config(data)
    if not cfg:  # plugin: false 是没装微信插件（简报据此连微信这一项都不写），没有这个字段是装了但没有 wechat.json
        return {"enabled": False, **({} if plugins.installed(data, "wechat") else {"plugin": False})}
    raw = agent_watch.read_json(path_of(data, PENDING), {})
    raw = raw if isinstance(raw, dict) else {}
    status = load_status(data)
    done = load_done(data)
    held = snooze.load(data)
    shown, hidden = [], 0
    for row in rows(raw):
        mark = done.get(row["key"])
        if mark and row["ts"] <= mark["ts"]:
            hidden += 1
            continue
        row["bucket"] = bucket(row)
        row["snooze"] = None
        found = snooze.check(held, row["key"], row["at"], at_now)
        if found:
            row["snooze"] = {**found, "from": row["bucket"]}
            row["bucket"] = "hold"
        shown.append(row)
    manifest = raw.get("manifest") or {}
    return {
        "enabled": True,
        "rows": shown,
        "hidden": hidden,
        "updated_at": _at(manifest.get("created_at")) or _at(raw.get("generated_at")),
        "checked_at": _at(raw.get("generated_at")),
        "refreshed_at": status.get("finished_at") or "",
        "running": _running.locked(),
        "hint": _hint(status, raw, cfg),
        "has_data": bool(raw),
    }


# ---- 插件弹窗里的检查（personal/plugins.overview 调，没装也查配置） ----

PACKAGES = (("Crypto", "pycryptodome"), ("zstandard", "zstandard"), ("pyexpat", "pyexpat"))
_packages = {}  # python → (查的时间, 缺的包)


def missing_packages(python, at=None):
    """读取工具的 python 缺哪些包（解密要 pycryptodome，公众号文章要 zstandard，解析要 pyexpat）。十分钟内查过就用上次的。"""
    at = at or time.time()
    found = _packages.get(python)
    if found and at - found[0] < 600:
        return found[1]
    code = (f"import importlib.util as u; print(','.join(p for m, p in {[(m, p) for m, p in PACKAGES]!r} "
            f"if u.find_spec(m) is None))")
    result, out, _, _ = reader.run([python, "-c", code], 20)
    missing = [p for p in (out or "").strip().split(",") if p] if result == 0 else [p for _, p in PACKAGES]
    _packages[python] = (at, missing)
    return missing


def checks(data):
    """插件弹窗里「装好了没有」的几项：[{key, label, ok（True、False，None 是还说不准）, detail, hint}]。不读聊天内容。"""
    from .. import installed
    from ... import agent_watch as watch
    from . import needs
    cfg = config(data, check_plugin=False)
    rows = []
    trouble = (cfg or {}).get("problem") if cfg else "数据目录里还没有 wechat.json"
    rows.append({"key": "reader", "label": "读取工具", "ok": not trouble,
                 "detail": trouble or f"{cfg['skill_dir']}", "hint": "" if not trouble else "装上时给 --vault-dir 和 --python，见 personal/plugins/wechat/README.md 第 1、3 步"})
    if cfg and cfg.get("python") and Path(cfg["python"]).exists():
        lack = missing_packages(cfg["python"])
        rows.append({"key": "python", "label": "Python 的包", "ok": not lack, "detail": "齐了" if not lack else "缺 " + "、".join(lack),
                     "hint": "" if not lack else f"{cfg['python']} -m pip install " + " ".join(p for p in lack if p != "pyexpat")
                     + ("（pyexpat 是标准库：换一个带它的 Python）" if "pyexpat" in lack else "")})
    else:
        rows.append({"key": "python", "label": "Python 的包", "ok": None, "detail": "先配好读取工具的 python", "hint": ""})
    status = load_status(data)
    decrypt = status.get("decrypt") or {}
    binary = real_binary(cfg["python"]) if cfg and cfg.get("python") else "读取工具用的 python"
    disk = None if not decrypt else bool(decrypt.get("ok")) or not decrypt.get("aborted")
    rows.append({"key": "disk", "label": "完全磁盘访问", "ok": disk,
                 "detail": "还没刷新过，点收件箱「微信」里的「一键读取」试一次" if disk is None else "能读微信目录" if disk else "后台读不到微信目录",
                 "hint": "" if disk else f"系统设置 → 隐私与安全性 → 完全磁盘访问 给 {binary} 打开"})
    on = installed(data, "wechat")
    missing_rule = watch.source_rule_missing("微信拟 issue", "wechat.issues.scan") if on else ""
    rows.append({"key": "rule", "label": "中台规则「微信拟 issue」", "ok": (not missing_rule) if on else None,
                 "detail": "装上后才加" if not on else missing_rule or "已在中台", "hint": "重新装一次插件" if on and missing_rule else ""})
    chats = needs.mappings(data)
    rows.append({"key": "chats", "label": "登记的群", "ok": bool(chats),
                 "detail": f"{len(chats)} 个：" + "、".join(m["chat"] for m in chats[:5]) + ("……" if len(chats) > 5 else "") if chats else "还没登记，「读新需求」不会读微信",
                 "hint": "" if chats else "python3 -m personal.plugins wechat map --chat <群名> --project <项目 id>"})
    fresh = (status.get("pending") or {}).get("ok")
    rows.append({"key": "data", "label": "微信数据", "ok": None if not status else bool(fresh),
                 "detail": f"最近一次刷新 {str(status.get('finished_at') or status.get('started_at') or '')[:16].replace('T', ' ')}" if status else "还没有",
                 "hint": "" if fresh or not status else "看收件箱「微信」范围里的提示"})
    return rows
