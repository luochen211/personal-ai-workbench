"""「Agent → 运行记录」点开一轮的详情：只读本机 worker 的运行目录，不改中台数据。

运行目录是 ~/.agent-autoloop/worker/runs/<轮次>-<第几次>/，里面有 context.json（这一轮领到的事件和提醒）、
command-<回>.sh、stdout-<回>.log、stderr-<回>.log、exit-<回>（一回是一次进程；回执写错时接着原会话重写，就多一回）。
stdout 有三种：Claude Code 的 stream-json（每行一个 JSON，最后一条 type=result 就是交出的最后一条消息）、
Codex 的 --json（item.completed 里 agent_message 的正文）、命令和脚本的纯文本（整段输出就是最后一条消息）。
轮次的状态和时间取自中台 /v1/state（运行记录列表已经读过、在内存里），读不到时按文件推断。
页面上显示的正文、回执、日志、事件都先打码（mask），token、key 不出现在页面上。
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import agent_watch

TAIL_BYTES = 1 << 20  # stdout 只读最后 1 MB
STDERR_BYTES = 64 << 10
STDERR_LINES = 30
CONTEXT_BYTES = 4 << 20  # context.json 超过这么大就不读（事件写「太大没读」）
TEXT_LIMIT = 60000  # 页面上全文最多这么多字
SUMMARY_LIMIT = 600
ACTIVE = {"queued", "claimed", "unknown"}
RECEIPT = re.compile(r"```agent-autoloop[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.S)
TIER = re.compile(r"\s*【(问你|默认过)】\s*")
OUTCOME = re.compile(r"(RESULT|VERDICT):\s*(\S.*)")
# 打码：GitHub、OpenAI/Anthropic、Slack、AWS 的密钥样式，Bearer 头，key=、token: 这类赋值，32 位以上的十六进制串
SECRETS = [
    (re.compile(r"\b(gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}"), r"\1****"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{12,}"), "sk-****"),
    (re.compile(r"\b(xox[abposr]-)[A-Za-z0-9-]{8,}"), r"\1****"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA****"),
    (re.compile(r"\b(Bearer)(\s+)[A-Za-z0-9._~+/=-]{6,}", re.I), r"\1\2****"),
    (re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd)[\"']?\s*[=:]\s*[\"']?)"
                r"(?!\*{4})[^\s\"',;]{6,}"), r"\1****"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "****"),
]


def mask(text):
    if not isinstance(text, str):
        return text
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def masked(value):
    """把结构里所有字符串都打码。"""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, dict):
        return {k: masked(v) for k, v in value.items()}
    return value


def root():
    return agent_watch.home() / "worker" / "runs"


def inside(path, parent):
    """path 是 parent 里面的真实文件或目录（不是符号链接，换算后也没跑出去）。"""
    try:
        return not path.is_symlink() and path.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def numbered(folder, prefix, suffix=""):
    """folder 下 <prefix><数字><suffix> 的文件或目录 → {数字: 路径}；别的名字、符号链接、跑出 folder 的一律不要。"""
    pattern = re.compile(re.escape(prefix) + r"([1-9][0-9]{0,4})" + re.escape(suffix))
    found = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return found
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if match and inside(entry, folder):
            found[int(match[1])] = entry
    return found


def run_folder(run_id, base=None):
    """这一轮最后一次执行的目录和第几次；没有就是 (None, 0)。run_id 只认正整数，目录名只认 <轮次>-<第几次>。"""
    if not re.fullmatch(r"[1-9][0-9]{0,8}", str(run_id)):
        raise ValueError("轮次编号不对")
    base = base or root()
    tries = numbered(base, f"{int(run_id)}-")
    if not tries:
        return None, 0
    attempt = max(tries)
    return tries[attempt], attempt


def tail(path, size):
    """文件最后 size 字节（按 UTF-8 读，坏字节替换）；读不到是 None。"""
    try:
        with open(path, "rb") as stream:
            stream.seek(0, 2)
            total = stream.tell()
            stream.seek(max(0, total - size))
            data = stream.read()
    except OSError:
        return None, False
    return data.decode("utf-8", "replace"), total > size


def entries(text):
    """stream-json：每行一个 JSON（verbose 时一行也可能是一个数组），读不懂的行跳过。
    第一行不是带 type 的 JSON 就是纯文本（纯文本回执里单独一行的 JSON 不算）；只读了尾部时第一行可能是半行，看第二行。"""
    head = [line.strip() for line in text.splitlines()[:2] if line.strip()]
    def typed(line):
        try:
            value = json.loads(line)
        except ValueError:
            return False
        return any(isinstance(v, dict) and "type" in v for v in (value if isinstance(value, list) else [value]))
    if not any(typed(line) for line in head):
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        out.extend(v for v in (value if isinstance(value, list) else [value]) if isinstance(v, dict))
    return out


def assistant_text(entry):
    """一条消息里给人看的正文：Claude 的 assistant 消息拼起 text 块，Codex 的 agent_message 取 text。"""
    if entry.get("type") == "assistant":
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        parts = [c.get("text") for c in content or [] if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]
        return "\n\n".join(parts) if parts else None
    item = entry.get("item") if entry.get("type") == "item.completed" else None
    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
        return item["text"]
    return None


def read_output(text):
    """stdout → (格式, 最后一条消息, 是不是交出的结果)。
    stream-json 优先认最后一条成功的 type=result；没有（还在跑、出错了）就退回最后一条 assistant 正文。纯文本整段就是消息。"""
    parsed = entries(text)
    if not parsed:
        body = re.sub(r"\A@@session=\S*\n", "", text).strip()  # command 插件的会话标记行不算正文
        return "text", body or None, bool(body)
    kind = "codex-json" if any(str(e.get("type", "")).startswith(("item.", "turn.", "thread.")) for e in parsed) else "stream-json"
    for entry in reversed(parsed):
        if entry.get("type") == "result":
            if entry.get("subtype") == "success" and not entry.get("is_error") and isinstance(entry.get("result"), str):
                return kind, entry["result"], True
            break
    for entry in reversed(parsed):
        said = assistant_text(entry)
        if said and said.strip():
            return kind, said, False
    return kind, None, False


def final_output(folder):
    """这次执行最后一个交出消息的一回：回执重写时后一回才是最终的；都没交出就取最后一回里能读到的最后一条正文。"""
    turns = numbered(folder, "stdout-", ".log")
    fallback = ("", None, False, 0, False)
    for turn in sorted(turns, reverse=True):
        text, cut = tail(turns[turn], TAIL_BYTES)
        if text is None:
            continue
        kind, message, done = read_output(text)
        if done:
            return kind, message, True, turn, cut
        if fallback[1] is None and message:
            fallback = (kind, message, False, turn, cut)
        elif not fallback[0]:
            fallback = (kind, None, False, turn, cut)
    return fallback


def receipt(message):
    """最后一条消息拆成：第一行结论、去掉结论和回执后的正文、回执（解析好的 JSON，读不懂时带 error）、结果行。"""
    if not message:
        return {"conclusion": "", "body": "", "receipt": None, "outcome": None}
    text = message.strip()
    lines = [line for line in text.split("\n") if line.strip()]
    outcome = None
    if lines and (found := OUTCOME.fullmatch(lines[-1].strip())):
        outcome = {"kind": found[1], "value": found[2].strip()[:300], "line": lines[-1].strip()[:320]}
        text = text[: text.rstrip().rfind(lines[-1])].rstrip()
    blocks = list(RECEIPT.finditer(text))
    parsed = None
    if blocks:
        block = blocks[-1]
        try:
            data = json.loads(block[1])
            parsed = shape(data) if isinstance(data, dict) else {"error": "回执不是 JSON 对象"}
        except ValueError:
            parsed = {"error": "回执读不懂（不是合法 JSON）"}
        text = (text[: block.start()] + text[block.end():]).strip()
    first, _, rest = text.partition("\n")
    return {"conclusion": first.strip()[:SUMMARY_LIMIT], "body": rest.strip()[:TEXT_LIMIT], "receipt": parsed, "outcome": outcome}


def words(value, limit=4000):
    return value[:limit] if isinstance(value, str) else ""


def shape(data):
    """回执里页面要用的几块，只留认识的字段（都是字符串或数字）。"""
    def items(key):
        value = data.get(key)
        return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
    return {
        "notice": [{"title": words(n.get("title"), 300), "text": words(n.get("text"))} for n in items("notice")],
        "ask": [{"title": words(a.get("title"), 300), "text": words(a.get("text")),
                 "options": [words(o, 300) for o in a.get("options") or [] if isinstance(o, str)] if isinstance(a.get("options"), list) else [],
                 "recommend": a.get("recommend") if isinstance(a.get("recommend"), int) and not isinstance(a.get("recommend"), bool) else None}
                for a in items("ask")],
        "later": [{"at": words(l.get("at"), 60), "about": words(l.get("about")), "key": words(l.get("key"), 200), "cancel": l.get("cancel") is True}
                  for l in items("later")],
        "resolve": [{"card": r.get("card") if isinstance(r.get("card"), int) and not isinstance(r.get("card"), bool) else None,
                     "reason": words(r.get("reason"))} for r in items("resolve")],
        "events": [{"type": words(e.get("type"), 100), "subject": words(e.get("subject"), 200), "summary": words(e.get("summary"), SUMMARY_LIMIT)}
                   for e in items("events")],
    }


def context_events(folder):
    """context.json 里这一轮领到的事件：来源、类型、对象、摘要、链接。太大或读坏了给出原因。"""
    path = folder / "context.json"
    try:
        if not inside(path, folder):
            return [], "没有 context.json"
        if path.stat().st_size > CONTEXT_BYTES:
            return [], "context.json 太大，没读"
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        return [], "没有 context.json"
    except (OSError, ValueError):
        return [], "context.json 读不懂"
    events = data.get("events") if isinstance(data, dict) else None
    out = []
    for e in events if isinstance(events, list) else []:
        if not isinstance(e, dict):
            continue
        shown = {"source": words(e.get("sourceName") or e.get("source"), 100), "type": words(e.get("type"), 100),
                 "subject": words(e.get("subject"), 200), "summary": words(e.get("summary"), SUMMARY_LIMIT),
                 "link": words(e.get("link"), 500), "received_at": words(e.get("receivedAt"), 60), "count": 1}
        same = ("source", "type", "subject", "summary", "link")
        if out and all(out[-1][k] == shown[k] for k in same):  # 连着的同样事件（比如攒下的定时）并成一行，写次数
            out[-1]["count"] += 1
        else:
            out.append(shown)
    return out, ""


def exit_code(folder, turn):
    try:
        path = folder / f"exit-{turn}"
        if not inside(path, folder):
            return None
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw) if re.fullmatch(r"-?[0-9]{1,6}", raw) else None
    except (OSError, ValueError):
        return None


def stderr_tail(folder, turn):
    path = folder / f"stderr-{turn}.log"
    if not inside(path, folder):
        return ""
    text, _ = tail(path, STDERR_BYTES)
    return "\n".join((text or "").rstrip().split("\n")[-STDERR_LINES:]) if text and text.strip() else ""


def rule_from_command(folder):
    """中台读不到时，从 command-1.sh 的提示词里认规则名（「按规则「…」把这件活派给你」）。"""
    path = folder / "command-1.sh"
    if not inside(path, folder):
        return ""
    text, _ = tail(path, 64 << 10)
    found = re.search(r"规则「([^」\n]{1,80})」", text or "")
    return found[1] if found else ""


def file_time(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def status_of(state, error, exit_):
    """页面上的状态：进行中 / 排队中 / 完成 / 失败 / 没跑成。"""
    if state in ("done", "nothing"):
        return "完成"
    if state == "cancelled":
        return "没跑成"
    if state in ACTIVE:
        if error:
            return "失败"
        return "排队中" if state == "queued" else "进行中"
    if exit_ is None:
        return "进行中"
    return "完成" if exit_ == 0 else "失败"


def find_row(run_id):
    """中台里这一轮（运行记录列表已读进内存的那份）和开着的提醒；中台读不到时是 (None, None)。"""
    from . import issues
    try:
        rows, cards = issues.all_runs()
    except (OSError, ValueError, KeyError):
        return None, None
    return rows.get(int(run_id)), cards


def tiered(ask):
    """提问标题开头的档位：【问你】等你定（ask），【默认过】已按推荐做、可改（default）；没写按【问你】。显示时去掉前缀。"""
    found = TIER.match(ask["title"])
    return {**ask, "title": ask["title"][found.end():] or ask["title"] if found else ask["title"],
            "tier": "default" if found and found[1] == "默认过" else "ask"}


def waiting_asks(asks, row, cards):
    """回执里的提问还有没有在等你回答：中台的提问创建时间等于这一轮交回执的时间、标题一样，且还开着。
    读不到中台时 open 是 None（不知道）。"""
    if cards is None or not row:
        return [tiered({**a, "card": None, "open": None}) for a in asks]
    mine = [c for c in cards if c.get("kind") == "ask" and c.get("createdAt") == row.get("finished_at")]
    out = []
    for a in asks:
        card = next((c for c in mine if (c.get("title") or "") == a["title"]), None)
        if card:
            mine.remove(card)
        out.append(tiered({**a, "card": card["id"] if card else None, "open": bool(card) and card.get("state") == "open"}))
    return out


def detail(run_id, *, base=None, lookup=find_row):
    """一轮的详情。只读文件；没有这个运行目录也没有中台记录就报错。"""
    folder, attempt = run_folder(run_id, base)
    row, cards = lookup(run_id)
    if folder is None and row is None:
        raise LookupError(f"没有第 {int(run_id)} 轮的运行记录")
    kind, message, delivered, turn, cut = final_output(folder) if folder else ("", None, False, 0, False)
    if folder and not turn:
        turn = max(numbered(folder, "exit-") or numbered(folder, "stdout-", ".log") or [0])
    code = exit_code(folder, turn) if folder and turn else None
    events, events_error = context_events(folder) if folder else ([], "运行目录已清理（worker 留 14 天）")
    parts = receipt(message)
    if not parts["conclusion"] and row:
        parts["conclusion"] = row.get("summary") or ""
    state = row.get("state") if row else None
    error = (row.get("error") or "") if row else ""
    status = status_of(state, error, code)
    if folder and not row and not (folder / f"exit-{turn}").exists():
        status = "进行中"
    if row:
        started = row.get("claimed_at") or row.get("created_at")
    else:
        started = file_time(folder / "context.json") if folder else None
    finished = row.get("finished_at") if row else (file_time(folder / f"exit-{turn}") if folder and code is not None else None)
    failed = status in ("失败", "没跑成") or (code not in (None, 0))
    rec = parts["receipt"]
    if rec and "error" not in rec:
        rec = {**rec, "ask": waiting_asks(rec["ask"], row, cards)}
    subjects = list(dict.fromkeys([s for s in (row or {}).get("subjects", []) if s] + [e["subject"] for e in events if e["subject"]]))
    return masked({
        "id": int(run_id), "rule": (row or {}).get("rule") or (rule_from_command(folder) if folder else ""),
        "agent": (row or {}).get("agent") or "", "subjects": subjects, "status": status, "state": state or "",
        "active": status in ("进行中", "排队中") or (state in ACTIVE), "attempt": attempt,
        "started_at": started, "finished_at": finished, "error": error,
        "format": kind, "delivered": delivered, "truncated": cut,
        "conclusion": parts["conclusion"], "body": parts["body"], "receipt": rec, "outcome": parts["outcome"],
        "events": events, "events_error": events_error,
        "exit_code": code, "stderr": stderr_tail(folder, turn) if folder and turn and failed else "",
        "folder_missing": folder is None,
    })
