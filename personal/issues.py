"""以 GitHub issue 为中心的工作台数据：读 GitHub，处理进度取自 agent-autoloop。

issue 是任务本身。打了 agent 标签的 issue 由 agent-autoloop 的
「看带 agent 标签的 issue」规则派给 Agent；这里汇总它们在中台里走到了哪一步。
这里的写操作只有「交给 Agent」（确认发送回复草稿在 reply_drafts）：自己仓库的 issue 打 agent 标签并马上触发一次监看（见 agent_watch）；
上游仓库（别人的仓库）的 issue 不打标签，记进 upstream-handoffs.json 再触发监看，由监看作为 issues.opened 交给中台。
回答 Agent 的提问走本机中台的本人接口，不写 GitHub。
"""
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import activity, agent_watch, fresh, github, next_step, reply_mode, settled, snooze, upstream
from . import notifications as inbox
from .commits import cutoff

LABEL = "agent"
HANDLE_RULE = agent_watch.HANDLE_RULE
REVIEW_RULE = agent_watch.REVIEW_RULE  # 审查 agent PR 的规则，结论只认回执最后一行 VERDICT
RULES = {HANDLE_RULE, REVIEW_RULE}
REWORK_ROUND = re.compile(r"返工第 ([1-9]\d*) 轮")
WATCH_RULES = set(agent_watch.WATCH_RULES)
CACHE_SECONDS = 300  # issue、与我有关的 issue 和 PR 都缓存 5 分钟；过期先给旧值、后台刷新
DETAIL_SECONDS = 60  # issue 详情缓存 1 分钟；过期就等新的，限流或读失败时给旧的
LOST_AFTER = timedelta(minutes=15)  # 显式交给后这么久还没有新的一轮，派发可能丢了
REPO = re.compile(r"[\w.-]+/[\w.-]+")
MINE = "mine"  # projects.toml 的 issue_scope：上游仓库只看与我有关的
ACTIVE = {"queued", "claimed", "unknown"}
FINISHED = {"done", "nothing"}  # 中台认为跑完了的轮次；cancelled 是停了或连续没跑成
# 回执最后一行的结果行（思路借自 ai-daming/daming-skills 的 issue-pr-pipeline，MIT）：只认这一行，缺了就是结果不明
RESULT_LINE = re.compile(r"RESULT: (?:PR=([1-9]\d{0,8})|(NO_CHANGE)|BLOCKED=(.*\S.*))")
RESULTS_STORE = "run-results.json"  # 读到过的结果，worker 日志清理后照样认
TAIL_BYTES = 1 << 20
UNREAD = {"kind": "unknown", "reason": "读不到这一轮的回执全文"}  # 只记在内存，不写进文件
MISSING = {"kind": "unknown", "reason": "回执最后一行没有 RESULT 结果行"}
NO_VERDICT = {"kind": "unknown", "reason": "审查回执最后一行没有 VERDICT 结论行"}
QUESTION_TEXT = 20000
# 决定包（思路借自 ai-daming/daming-skills 的 issue-pr-pipeline，MIT）：提问标题开头写档位。
# 【问你】等本人定；【默认过】Agent 已按推荐做了，不答就算定了。没写档位的提问按【问你】算
TIER = re.compile(r"\s*【(问你|默认过)】\s*")
TIERS = {"问你": "ask", "默认过": "default"}
MAX_PAGES = 50  # 3000 轮，按每天 150 轮约 20 天
_runs_lock = threading.Lock()
_runs = {"settled": 0, "rows": {}}
_results_lock = threading.Lock()
_results = {}  # 轮次编号 → 读到的结果（只放从回执里读到的，读不到的下次再读）


def run(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError((result.stderr or "").strip()[:300] or "命令失败")
    return result.stdout


def search(repos, branches=None):
    """自己仓库的 open issue：GraphQL 批量读，不用搜索接口。branches 是配了 main_branch 的仓库 → 主线分支。"""
    return upstream.open_issues(repos, branches) if repos else []


def involved():
    """上游仓库里与我有关的 open issue 和 PR（一个跨仓库搜索），加上近一个月合并的我的 PR。"""
    since = cutoff(datetime.now().astimezone()).date().isoformat()
    return {"open": upstream.involved(), "merged": upstream.merged(since)}


def scoped(project):
    """只读与我有关的 issue（issue_scope = "mine"）：不整仓读取，交给 Agent 不打 agent 标签、记 upstream-handoffs.json。"""
    return project.get("issue_scope") == MINE


def push_access(project):
    """登记里写了 push_access = true：别人名下、你有推送权限的仓库（例如客户仓库），Agent 在原仓库开分支开 PR。"""
    return project.get("push_access") is True


def forks(project):
    """要 fork 才能开 PR 的上游仓库：只读与我有关的、又没写 push_access = true。
    交给时 detail 第一行写「范围：上游仓库」，技能照它 fork 到用户账号再向上游开 PR；写了 push_access 的不写这一行，照自己的仓库做。"""
    return scoped(project) and not push_access(project)


def hub(path, body=None):
    """调本机中台的本人接口（GET /v1/state、POST /v1/cards/<编号>/answer），见 agent_watch.hub。"""
    return agent_watch.hub(path, body)


def slim(r):
    return {"id": r["id"], "rule": r.get("rule") or "", "agent": r.get("agent") or "", "attempts": r.get("attempts", 0),
            "claimed_at": r.get("claimedAt"), "state": r.get("state"), "summary": r.get("summary") or "", "error": r.get("error") or "",
            "finished_at": r.get("finishedAt"), "created_at": r.get("createdAt"), "link": r.get("link") or "",
            "subjects": [e.get("subject", "") for e in r.get("events", []) if e.get("subject")],
            "rework": rework_round(r.get("events", [])), "pr_link": pr_link(r.get("events", [])),
            "sha": reviewed_sha(r.get("events", []))}


def rework_round(events):
    """返工事件（issues.rework）是第几轮，取摘要里的「返工第 N 轮」；不是返工就是 0。"""
    rounds = [int(m[1]) for e in events if e.get("type") == agent_watch.REWORK
              for m in [REWORK_ROUND.search(e.get("summary") or "")] if m]
    return max(rounds, default=0)


def pr_link(events):
    """审查和返工事件带的 PR 链接（审查事件 link 是 PR，返工事件 link 是审查评论，评论链接前半截就是 PR）。"""
    for e in events:
        found = re.match(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+", e.get("link") or "")
        if found and e.get("type") in (agent_watch.REVIEW, agent_watch.REWORK):
            return found[0]
    return ""


def reviewed_sha(events):
    """审查事件（pr.review）审的是哪个提交：link 是 …/pull/<号>/commits/<完整 SHA>。旧事件 link 只到 PR，返回空串。"""
    for e in events:
        found = re.match(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+/commits/([0-9a-f]{40})$", e.get("link") or "")
        if found and e.get("type") == agent_watch.REVIEW:
            return found[1]
    return ""


def all_runs(read=hub):
    """中台的近期轮次（全部规则）和等你处理的提问（中台协议里叫 cards）。/v1/state 一页只有最近 60 轮，监看规则每天就占 144 轮，
    所以往前翻页；已结束的轮次不会再变，翻到上次确认都已结束的位置就停。"""
    page = read("/v1/state")
    pages = [page]
    with _runs_lock:
        settled = _runs["settled"]
        while (page.get("hasMore") and page.get("nextBefore") and len(pages) < MAX_PAGES
               and min((r["id"] for r in page.get("runs", [])), default=0) > settled):
            page = read(f"/v1/state?before={int(page['nextBefore'])}")
            pages.append(page)
        seen = [r for p in pages for r in p.get("runs", [])]
        for r in seen:
            _runs["rows"][r["id"]] = slim(r)
        if seen:
            active = [r["id"] for r in seen if r.get("state") in ACTIVE]
            _runs["settled"] = min(active) - 1 if active else max(r["id"] for r in seen)
        return dict(_runs["rows"]), pages[0].get("cards", [])


def handle_runs(read=hub):
    """「处理 issue」和「审查 PR」两条规则的轮次（都以 issue 为单位）。"""
    rows, questions = all_runs(read)
    return {k: r for k, r in rows.items() if r["rule"] in RULES}, questions


def idle(r):
    """空跑：中台记成 nothing（没事可做）的轮次。「处理 issue」的 nothing 是 Agent 看过这条 issue 的结论，照常列出。
    监看规则改成「没事可做：」之前的旧轮次是 done，只能认监看自己写的那句结论。"""
    if r["state"] == "nothing":
        return r["rule"] != HANDLE_RULE
    return r["rule"] in WATCH_RULES and r["state"] == "done" and r["summary"].startswith(agent_watch.IDLE)


def repeated_reports(rows):
    """监看把派过的事件再报一次的轮次（中台按 id 去重，没有新派任何东西）：派出的对象都在更早的监看轮里报过。
    监看改成只在第一次报时写「派出」之前留下的旧轮次靠这个从运行记录里去掉。"""
    seen, repeats = set(), set()
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["rule"] not in WATCH_RULES or r["state"] != "done" or not r["summary"].startswith("派出 "):
            continue
        subjects = {x for part in r["summary"].split("；") if part.startswith("派出 ") and "：" in part
                    for x in part.split("：", 1)[1].split("、") if x}
        if subjects and subjects <= seen:
            repeats.add(r["id"])
        seen |= subjects
    return repeats


def run_failed(r):
    """没跑成的轮次：被停止，或没完成又带着错误（还在重试的也算）。和页面上的判断一致。"""
    return r["state"] == "cancelled" or (r["state"] != "done" and bool(r.get("error")))


CARD_WORD = "拆卡"  # 个人版的单位只有 GitHub issue，以卡片为单位的旧规则（如「派活：审查拆卡」）的轮次不列


def current_rules(path=None):
    """中台 rules.json 里现有规则的名字；读不到或读不懂返回 None（这时不按规则隐藏历史）。"""
    data = agent_watch.read_json(path or agent_watch.home() / "rules.json", None)
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return None
    return {r["name"] for r in rules if isinstance(r, dict) and isinstance(r.get("name"), str)}


def retired(r, names):
    """已停用规则的轮次：规则已从 rules.json 删掉（读得到 rules.json 时才判断），或是以卡片为单位的旧规则。"""
    return CARD_WORD in r["rule"] or (names is not None and r["rule"] not in names)


def run_log(limit=300, keep_idle=False):
    """Agent 运行记录：全部规则，新的在前，带耗时和失败原因。空跑和重报默认不列，只报条数；
    已从 rules.json 删掉的规则的轮次默认也不列，只报条数（retired）；keep_idle 时全列，巡检状态自己再挑。"""
    try:
        rows, _ = all_runs()
    except (OSError, ValueError, KeyError) as exc:
        return {"runs": [], "idle": 0, "retired": 0, "error": f"读不到 agent-autoloop：{exc}"}
    ordered = sorted(rows.values(), key=lambda r: r["id"], reverse=True)
    gone = 0
    if not keep_idle:
        names = current_rules()
        kept = [r for r in ordered if not retired(r, names)]
        gone, ordered = len(ordered) - len(kept), kept
    repeats = repeated_reports(ordered)
    shown = ordered if keep_idle else [r for r in ordered if not idle(r) and r["id"] not in repeats]
    out = []
    for r in shown[:limit]:
        start, end = r.get("claimed_at") or r.get("created_at"), r.get("finished_at")
        seconds = None
        if start and end:
            seconds = round((agent_watch.parse_time(end) - agent_watch.parse_time(start)).total_seconds())
        out.append({**r, "subjects": list(dict.fromkeys(s for s in r["subjects"] if s)), "seconds": seconds})
    return {"runs": out, "total": len(shown), "idle": len(ordered) - len(shown), "retired": gone, "error": ""}


def watch_status(limit=10):
    """巡检状态：下次定时（rules explain）、连续失败次数（watch-state.json）、最近一次运行（只给时间和好坏）、
    最近一次真正派出过的轮次、最近几次失败的轮次（同一原因连着失败的并成一行）。没事可做的轮次和把同一批事件再报一次的轮次不逐条列（用户 2026-11-13 定的）。
    同一条巡检规则之后已经跑成过的失败不算当前失败，不列在这里（运行记录里照样查得到）。"""
    log = run_log(limit=100000, keep_idle=True)
    runs = [r for r in log["runs"] if r["rule"] in WATCH_RULES]
    repeats = repeated_reports(runs)
    latest = runs[0] if runs else None
    dispatched = next((r for r in runs if r["state"] == "done" and not idle(r) and r["id"] not in repeats), None)
    succeeded = {}  # 每条巡检规则最近一次跑成的轮次号
    for r in runs:
        if r["state"] in FINISHED and not run_failed(r):
            succeeded[r["rule"]] = max(succeeded.get(r["rule"], 0), r["id"])
    failed, streak = [], False  # 原因相同、中间没有跑成过的失败轮次并成一行，带次数和最早一次的时间
    for r in runs:
        if not run_failed(r):
            streak = False
            continue
        if r["id"] < succeeded.get(r["rule"], 0):  # 之后已经跑成过，不算当前失败
            continue
        if streak and (failed[-1]["error"] or failed[-1]["summary"]) == (r["error"] or r["summary"]):
            failed[-1].update(count=failed[-1]["count"] + 1, first_at=r["created_at"])
        else:
            failed.append({**r, "count": 1, "first_at": r["created_at"]})
        streak = True
    failed = failed[:limit]
    last_run = latest and {"id": latest["id"], "created_at": latest["created_at"], "state": latest["state"],
                           "failed": run_failed(latest)}
    state = agent_watch.read_json(agent_watch.folder() / "watch-state.json", {})
    try:
        explain = run(["agent-autoloop", "rules", "explain"], timeout=30)
        found = re.search(r"看带 agent 标签的 issue：定时 (\S+ \S+ \S+ \S+ \S+)（下次 ([^）]+)）", explain)
        schedule, upcoming = (found[1], found[2]) if found else ("", "")
    except (ValueError, OSError, subprocess.TimeoutExpired):
        schedule = upcoming = ""
    from . import watch_gate
    return {"last_run": last_run, "dispatched": dispatched, "failed": failed, "gate": watch_gate.status(),
            "failures": int(state.get("failures", 0)), "notified": bool(state.get("notified")),
            "last_error": state.get("last_error", ""), "last_failed": state.get("last_failed"),
            "schedule": schedule, "next": upcoming, "notify_after": agent_watch.FAIL_NOTIFY_AFTER, "error": log["error"]}


def parse_result(text):
    """回执的结果行：最后一个非空行恰好是 RESULT: PR=<号> / RESULT: NO_CHANGE / RESULT: BLOCKED=<原因> 之一。
    别的位置、别的写法一律不认，返回 None。"""
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]  # 只按换行切：\v、\f、U+2028 不算换行
    found = RESULT_LINE.fullmatch(lines[-1]) if lines else None
    if not found:
        return None
    if found[1]:
        return {"kind": "pr", "pr": int(found[1])}
    if found[2]:
        return {"kind": "no_change"}
    return {"kind": "blocked", "reason": found[3].strip()[:300]}


def parse_review(text):
    """审查一轮的结论：最后一个非空行是 VERDICT: APPROVE / CHANGES_REQUIRED / WAIT，和 PR 评论里的一致；
    另认 STALE：派出后 PR 又有新提交，这一轮没审。"""
    verdict = agent_watch.parse_verdict(text, agent_watch.VERDICTS + (agent_watch.STALE,))
    return {"kind": "review", "verdict": verdict} if verdict else None


def last_result_text(log):
    """Claude 的 stream-json 输出里最后一条 type=result 的正文，就是这一回交出的最后一条消息。
    这一条出错了（is_error 或 subtype 不是 success）就当这一回没交出东西，返回 None：中台这时用上一回的内容结束这一轮。"""
    with open(log, "rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - TAIL_BYTES))
        lines = stream.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "result":
            ok = entry.get("subtype") == "success" and not entry.get("is_error") and isinstance(entry.get("result"), str)
            return entry["result"] if ok else None
    return None


def final_message(run_id):
    """这一轮 Agent 的最后一条消息。中台只存第一行结论，全文在本机 worker 的输出里：
    ~/.agent-autoloop/worker/runs/<轮次>-<第几次>/stdout-<第几回>.log，取最后一次里最后一个成功交出消息的一回。
    worker 不在本机，或日志已清理（worker 留 14 天），返回 None。"""
    def numbered(paths):
        return {int(p.stem.rsplit("-", 1)[1]): p for p in paths if p.stem.rsplit("-", 1)[1].isdigit()}
    try:
        tries = numbered((agent_watch.home() / "worker" / "runs").glob(f"{int(run_id)}-*"))
        turns = numbered(tries[max(tries)].glob("stdout-*.log")) if tries else {}
        for turn in sorted(turns, reverse=True):
            text = last_result_text(turns[turn])
            if text is not None:
                return text
    except OSError:
        pass
    return None


def outcome(r, read=final_message, parse=None):
    """parse 是结果行的读法，缺省是「处理 issue」的 parse_result。一轮的结果：还在跑的是 None；没跑完的（cancelled）是 failed；跑完的读回执最后一行，读不到、没写或读坏了是 unknown。
    记下的结果带着这一轮的结束时间，对不上（轮次编号被重用）就重读。"""
    if r["state"] not in FINISHED:
        return None if r["state"] in ACTIVE else {"kind": "failed", "reason": r["error"] or r["summary"] or "这一轮没跑完"}
    with _results_lock:
        known = _results.get(r["id"])
        if known and known["finished_at"] == r["finished_at"]:
            return known["result"]
    review = r.get("rule") == REVIEW_RULE
    try:
        said = read(r["id"])  # 本机 worker 先写完输出再交结果，第一次没读到以后也读不到，同样记在内存里不再翻目录
        if said is None:
            result = UNREAD
        elif review:
            result = parse_review(said) or NO_VERDICT
        else:
            result = (parse or parse_result)(said) or MISSING
    except Exception:  # 回执是别人写的资料，读坏了也只算结果不明，不能让列表一直报错
        result = {"kind": "unknown", "reason": "回执读不懂"}
    with _results_lock:
        _results[r["id"]] = {"finished_at": r["finished_at"], "result": result}
    return result


def load_results(data):
    """把 .personal/run-results.json 里记过的结果并进内存（内存里读不到的才用文件的），返回文件里的记录。"""
    try:
        stored = json.loads((Path(data) / RESULTS_STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    stored = {int(k): v for k, v in stored.items() if k.isdigit() and isinstance(v, dict)
              and isinstance(v.get("result"), dict) and isinstance(v.get("finished_at"), str)}
    with _results_lock:
        for key, value in stored.items():
            if key not in _results or _results[key]["result"] == UNREAD:
                _results[key] = value
    return stored


def save_results(data, stored):
    """内存里有、文件里没有或不一样的结果才写；写到同目录的唯一临时文件再换名，写失败不影响页面。"""
    with _results_lock:
        snapshot = {k: v for k, v in sorted(_results.items()) if v["result"] != UNREAD}
    if all(stored.get(k) == v for k, v in snapshot.items()):
        return
    try:
        agent_watch.write_json(Path(data) / RESULTS_STORE, {str(k): v for k, v in snapshot.items()})
    except OSError:
        pass


def tiered(title, kind):
    """提问的档位和去掉档位前缀的标题：ask 等本人定，default 已按推荐做、可改；处理结果（notice）没有档位。"""
    found = TIER.match(title)
    if kind != "ask":
        return title, ""
    return (title[found.end():] or title, TIERS[found[1]]) if found else (title, "ask")


def pending(asks):
    """还在等本人定的提问：【默认过】不算。"""
    return [q for q in asks if q["kind"] == "ask" and q.get("tier") != "default"]


def autoloop_state(data=None, *, rule=HANDLE_RULE, parse=None):
    """中台里每条 issue 的最近一轮、运行记录和等你回答的提问；只认「处理 issue」规则（以 issue 为单位）。
    每轮带上回执结果行（见 outcome）；给了 data（.personal/）就把读到的结果记下来。
    rule、parse 给「处理 PR 反馈」用：换一条规则、换一种结果行。"""
    try:
        if rule == HANDLE_RULE:
            rows, items = handle_runs()
        else:
            found, items = all_runs()
            rows = {k: r for k, r in found.items() if r["rule"] == rule}
    except (OSError, ValueError, KeyError) as exc:
        return {}, f"读不到 agent-autoloop：{exc}"
    known = load_results(data) if data else None
    results = {r["id"]: outcome(r, parse=parse) for r in rows.values()}
    if data:
        save_results(data, known)
    runs, history, reviews, reworks = {}, {}, {}, {}
    for r in sorted(rows.values(), key=lambda r: r["id"], reverse=True):
        for subject in dict.fromkeys(r["subjects"]):  # 排队中的一轮会并进同一 issue 的新事件，只记一次
            row = {**{k: v for k, v in r.items() if k != "subjects"}, "result": results[r["id"]]}
            if r.get("rule") == REVIEW_RULE:
                reviews.setdefault(subject, row)  # 最近一次审查
            else:
                runs.setdefault(subject, row)  # 留最近一轮处理
                reworks[subject] = max(reworks.get(subject, 0), r.get("rework") or 0)
            history.setdefault(subject, []).append(row)
    # 提问不带轮次编号：创建时间等于某轮交回执的时间，就是那一轮留下的；再按正文里的 owner/repo#编号兜底
    by_receipt = {}
    for r in rows.values():
        if r["finished_at"]:
            by_receipt.setdefault(r["finished_at"], []).extend(r["subjects"])
    waiting = {}
    for item in items:
        if item.get("state") != "open" or item.get("rule") not in (RULES if rule == HANDLE_RULE else {rule}):
            continue
        words = (item.get("title") or "") + (item.get("text") or "")
        subjects = dict.fromkeys(by_receipt.get(item.get("createdAt"), []) + [s for s in runs if s in words])
        title, tier = tiered(item.get("title") or "", item.get("kind"))
        shown = {"id": item["id"], "kind": item.get("kind"), "title": title, "tier": tier,
                 "text": (item.get("text") or "")[:QUESTION_TEXT], "options": item.get("options") or [],
                 "recommend": item.get("recommend"), "created_at": item.get("createdAt")}
        for subject in subjects:
            waiting.setdefault(subject, []).append(shown)
    return {"runs": runs, "history": history, "waiting": waiting, "reviews": reviews, "reworks": reworks}, ""


_rule_state = autoloop_state  # feedback_state 用；测试替换 autoloop_state 时不牵连它（替换 feedback_state 即可）


def pr_key(link):
    """PR 链接 → 'owner/repo#号'（小写，和 heads 的键一致）。"""
    found = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)", link or "")
    return f"{found[1].lower()}#{found[2]}" if found else ""


def review_fresh(auto, review, run_):
    """这次审查的结论对 PR 现在的 head 还算不算数。知道审的提交和现在的 head 就比提交（回答轮没有新提交时照样算数）；
    旧事件不知道审的提交、或没读到 head，退回比轮次：审查在最近一轮处理之后才算数。"""
    head = auto.get("heads", {}).get(pr_key(review.get("pr_link")))
    if review.get("sha") and head:
        return head == review["sha"]
    return not run_ or review["id"] > run_["id"]


def verdict_after(auto, subject, run_):
    """对 PR 现在的 head 有效的审查结论（APPROVE / CHANGES_REQUIRED / WAIT）；没审、还在审、审的是旧提交、
    审查发现提交已更新（STALE）都是 None。"""
    review = auto.get("reviews", {}).get(subject)
    if not review or review["state"] not in FINISHED or not review_fresh(auto, review, run_):
        return None
    verdict = (review.get("result") or {}).get("verdict")
    return None if verdict == agent_watch.STALE else verdict


def stage(row, auto, handed_at=None):
    """Agent 处理到哪一步：未交给 Agent / 已交给 Agent / 处理中 / 审查中 / 返工中 / 等你决定 / 等你看 / 已处理。
    开了 PR 之后另有一个审查：审查在跑是审查中；审查要改、还没满 MAX_REWORK 轮是返工中（返工那一轮在跑也是），
    满了等你决定；审查通过或等待都是等你看。
    显式交给之后、还没有更新的一轮时，算已交给 Agent，不沿用上一轮的结果。
    有【问你】没答的等你决定，只剩【默认过】的等你看（Agent 已按推荐做了）。
    最近一轮结束后只认回执的结果行：BLOCKED 等你决定；开了 PR 等你看；NO_CHANGE 才算已处理；
    没写结果行、读不到回执、没跑完的都算结果不明，等你看。"""
    subject = row["subject"]
    run_ = auto.get("runs", {}).get(subject)
    if run_ and run_["state"] in ACTIVE:
        return "返工中" if run_.get("rework") else "处理中"  # 排队或在跑的一轮会接上新交给的事件；上一轮留下的提问先不算
    review = auto.get("reviews", {}).get(subject)
    if review and review["state"] in ACTIVE and (not run_ or review["id"] > run_["id"]):
        return "审查中"
    if handed_at and (not run_ or agent_watch.parse_time(run_["created_at"]) < agent_watch.parse_time(handed_at)):
        return "已交给 Agent"
    asks = auto.get("waiting", {}).get(subject) or []
    result = (run_ or {}).get("result") or {}
    if pending(asks) or result.get("kind") == "blocked":
        return "等你决定"
    if result.get("kind") == "pr" and verdict_after(auto, subject, run_) == "CHANGES_REQUIRED":
        if rework_spent(auto, subject):
            return "等你决定"  # 这个提交已经返工过一轮、那一轮结束了却没推新提交：监看不会再派，等本人
        return "返工中" if auto.get("reworks", {}).get(subject, 0) < agent_watch.MAX_REWORK else "等你决定"
    if asks:
        return "等你看"
    if run_:
        return "已处理" if result.get("kind") == "no_change" else "等你看"
    # 上游仓库的标签是别人打的，不算交给
    return "已交给 Agent" if LABEL in row["labels"] and row.get("scope") != MINE else "未交给 Agent"


def review_state(auto, subject):
    """给页面的审查进度：最近一次审查的结论、审的提交、PR 现在的 head、已返工几轮、PR 链接、暂时不审的原因（hold）。
    current：结论对现在的 head 有效；moved：审完之后 PR 有了新提交（或审查发现提交已更新），要等重新审查。
    没审过也没有暂不审的原因是 None。"""
    review = auto.get("reviews", {}).get(subject)
    hold = auto.get("holds", {}).get(subject) or {}
    if not review and not hold:
        return None
    reworks = auto.get("reworks", {}).get(subject, 0)
    if not review:
        return {"id": None, "state": "", "active": False, "verdict": None, "current": False, "moved": False, "spent": False, "sha": "",
                "head": (hold.get("head") or "")[:12], "hold": hold.get("why") or "", "reworks": reworks,
                "max": agent_watch.MAX_REWORK, "url": hold.get("url") or "", "at": None}
    run_ = auto.get("runs", {}).get(subject)
    done = review["state"] in FINISHED
    verdict = (review.get("result") or {}).get("verdict") if done else None
    head = auto.get("heads", {}).get(pr_key(review.get("pr_link"))) or ""
    fresh = review_fresh(auto, review, run_)
    moved = done and (verdict == agent_watch.STALE or bool(review.get("sha") and head and head != review["sha"]))
    return {"id": review["id"], "state": review["state"], "active": review["state"] in ACTIVE, "verdict": verdict,
            "current": fresh and verdict != agent_watch.STALE, "moved": moved, "sha": (review.get("sha") or "")[:12],
            "spent": verdict == "CHANGES_REQUIRED" and fresh and rework_spent(auto, subject),
            "head": head[:12], "hold": hold.get("why") or "", "reworks": reworks, "max": agent_watch.MAX_REWORK,
            "url": review.get("pr_link") or "", "at": review.get("finished_at")}


def rework_spent(auto, subject):
    """PR 现在的 head 已经派过一轮返工（派出记录），且那一轮在中台已经结束：返工没推新提交（比如写了 RESULT: PR=），
    监看不会对同一提交再派返工，所以不能一直显示「返工第 N+1 轮」。"""
    round_ = auto.get("reworked", {}).get(subject)
    return bool(round_) and any(r.get("rework") == round_ and r.get("state") not in ACTIVE
                                for r in auto.get("history", {}).get(subject, []))


def review_ledger(heads):
    """监看的派出记录（~/.agent-autoloop/issues/dispatched.json）：每条 issue 已派出几轮返工、为什么暂时不审、
    PR 现在的 head 派过第几轮返工（reworked）。暂不审的原因只留记的提交还是 PR 现在 head 的（没读到 head 时照留）。"""
    ledger = agent_watch.load_ledger()
    reworks, reworked = {}, {}
    for entry in ledger["sent"].values():
        if entry.get("kind") == "rework" and entry.get("subject"):
            round_ = int(entry.get("round") or 0)
            reworks[entry["subject"]] = max(reworks.get(entry["subject"], 0), round_)
            if heads.get(str(entry.get("pr")).lower()) == entry.get("head"):
                reworked[entry["subject"]] = max(reworked.get(entry["subject"], 0), round_)
    holds = {}
    for key, hold in ledger["holds"].items():
        repo, _, number = key.rpartition("#")
        head = heads.get(key.lower())
        if isinstance(hold, dict) and hold.get("subject") and (not head or hold.get("head") == head):
            holds[hold["subject"]] = {**hold, "url": f"https://github.com/{repo}/pull/{number}"}
    return reworks, holds, reworked


def stage_label(row, auto):
    """阶段的显示文字：返工中写成「返工第 N 轮」，其余同阶段。"""
    if row["stage"] != "返工中":
        return row["stage"]
    run_ = auto.get("runs", {}).get(row["subject"]) or {}
    if run_.get("state") in ACTIVE and run_.get("rework"):
        return f"返工第 {run_['rework']} 轮"
    return f"返工第 {auto.get('reworks', {}).get(row['subject'], 0) + 1} 轮"


def shown_result(repo, result):
    """给页面的结果：开了 PR 的带上链接。"""
    if result and result.get("kind") == "pr":
        return {**result, "url": f"https://github.com/{repo}/pull/{result['pr']}"}
    return result


def stalled(row, at=None):
    """「已交给 Agent」却可能一直派不出去的原因：label_only 是只有标签、没有运行记录也没在工作台交给过
    （启用监看前就带标签的 issue 不会自动派）；lost 是显式交给后超过 LOST_AFTER 还没有新的一轮。"""
    if row["stage"] != "已交给 Agent":
        return ""
    if not row.get("handed_at"):
        return "" if row.get("run") else "label_only"
    at = at or datetime.now().astimezone()
    return "lost" if at - agent_watch.parse_time(row["handed_at"]) > LOST_AFTER else ""


def closed_since(data, repos):
    """已经知道关闭了的 issue → 关闭（或在消息里看到关闭）的时刻：各项目和全部项目的「已完成」缓存，加上 GitHub 消息的主题状态。
    只看已有结果，不发请求。"""
    activity.seed_stores(data, repos)
    known = activity.known_closed()
    for subject, at in inbox.closed_subjects(data).items():
        known[subject] = max(known.get(subject, ""), at)
    return known


def drop_closed(rows, known):
    """issue 缓存还没刷新时，已知关闭的先不列：关闭时刻不早于列表里的更新时间。重新打开的更新时间更晚，照常列出。"""
    return [r for r in rows if not known.get(r["subject"].lower(), "") >= r["updated_at"]]


def view(projects, *, refresh=False, max_age=None, focus=None, data=None):
    """max_age 和 focus 给项目详情页用：focus 项目所在的那份缓存超过 max_age 秒就后台重读，fetched_at 也取那一份的。
    已知关闭了的 issue（见 closed_since）不列。"""
    with_issues = [p for p in projects if p.get("github") and REPO.fullmatch(p["github"])]
    by_repo = {p["github"].lower(): p for p in with_issues}
    own = tuple(sorted(p["github"] for p in with_issues if not scoped(p)))
    mine = [p for p in with_issues if scoped(p)]
    target = next((p for p in with_issues if p["id"] == focus), None)
    short = min(CACHE_SECONDS, max_age) if max_age and target else CACHE_SECONDS
    branches = {p["github"]: p["main_branch"] for p in with_issues if not scoped(p) and p.get("main_branch")}
    first = github.cached(("issues", own, tuple(sorted(branches.items()))), lambda: search(list(own), branches),
                          short if target and not scoped(target) else CACHE_SECONDS, force=refresh)
    second = github.cached(("involved",), involved, short if target and scoped(target) else CACHE_SECONDS, force=refresh) if mine else \
        {"value": {"open": [], "merged": []}, "at": 0.0, "error": "", "limited_until": 0.0, "refreshing": False}
    theirs = second["value"] or {"open": [], "merged": []}
    mixed = list(first["value"] or [])  # 同一个请求里顺带读了 agent PR 的 head（type 是 PullRequestHead）
    heads = {f"{r['repository']['nameWithOwner'].lower()}#{r['number']}": r["headRefOid"]
             for r in mixed if r.get("type") == upstream.HEAD}
    mains = {r["repository"]["nameWithOwner"].lower(): {"branch": r.get("branch") or "", "committed_at": r.get("committed_at") or ""}
             for r in mixed if r.get("type") == upstream.REPO_INFO}
    raw = [r for r in mixed if r.get("type", "Issue") == "Issue"] + [
        r for r in theirs["open"] if r["type"] == "Issue" and scoped(by_repo.get(r["repository"]["nameWithOwner"].lower(), {}))]
    raw, notices, extra = signaled(raw, data, by_repo, first["at"], second["at"])
    notes = [github.describe(first), github.describe(second)]
    limited = next((n["message"] for n in notes if n["limited"]), "")
    error = "" if limited else "；".join(f"GitHub issue 读取失败：{n['message']}" for n in notes if n["message"])
    auto, auto_error = autoloop_state(data)
    ledger_reworks, holds, reworked = review_ledger(heads)
    reworks = {k: max(auto.get("reworks", {}).get(k, 0), ledger_reworks.get(k, 0))
               for k in set(auto.get("reworks", {})) | set(ledger_reworks)}
    auto = {**auto, "heads": heads, "holds": holds, "reworks": reworks, "reworked": reworked}
    handed = agent_watch.load_handoffs()
    upstream_handed = {k.lower(): v.get("handed_at") for k, v in agent_watch.load_handoffs(agent_watch.upstream_ledger_path()).items()
                       if isinstance(v, dict)}
    feedback, _ = feedback_state(data)  # 和上面同一个 /v1/state，读不到时上面已经报了
    involved_keys = {f"{r['repository']['nameWithOwner']}#{r['number']}".lower() for r in theirs["open"] if r.get("type") == "Issue"} | extra
    notes, numbers = {}, {}
    for repo, repo_rows in group_by_repo(raw).items():
        notes.update(next_step.statements(repo_rows))
        numbers[repo] = {r["number"] for r in repo_rows}
    snoozes, at_now = snooze.load(data), snooze.now()
    rows = []
    for item in raw:
        repo = item["repository"]["nameWithOwner"]
        project = by_repo.get(repo.lower())
        if not project:
            continue
        row = {"subject": f"{repo}#{item['number']}", "repo": repo, "number": item["number"],
               "project_id": project["id"], "project_name": project["name"], "title": item["title"],
               "url": item["url"], "labels": [label["name"] for label in item.get("labels", [])],
               "author": (item.get("author") or {}).get("login", ""), "comments": item.get("commentsCount", 0),
               "created_at": item["createdAt"], "updated_at": item["updatedAt"], "scope": MINE if scoped(project) else "",
               "push": scoped(project) and push_access(project)}
        row["handed_at"] = upstream_handed.get(row["subject"].lower()) if row["scope"] else agent_watch.handed_time(handed.get(row["subject"]))
        row["stage"] = stage(row, auto, row["handed_at"])
        row["stage_label"] = stage_label(row, auto)
        row["review"] = review_state(auto, row["subject"])
        row["run"] = auto.get("runs", {}).get(row["subject"])
        row["waiting"] = auto.get("waiting", {}).get(row["subject"], [])
        row["history"] = [{**r, "result": shown_result(repo, r.get("result"))}
                          for r in auto.get("history", {}).get(row["subject"], [])[:20]]
        if row["run"]:
            row["run"] = {**row["run"], "result": shown_result(repo, row["run"].get("result"))}
        row["stalled"] = stalled(row)
        row["flow"] = "issue" if row["stage"] != "未交给 Agent" else ""
        last = last_round(row, auto, feedback)  # 在 follow_feedback 换掉 handed_at、run 之前取
        seen = follow_feedback(row, feedback)
        row["next"] = next_step.classify(
            repo=repo, author=row["author"], thread=item.get("thread"), refs=item.get("refs") or [], flow=flow_of(row),
            upstream=forks(project), involved=row["subject"].lower() in involved_keys, seen=seen,
            title=item["title"], labels=row["labels"], assignees=item.get("assignees") or [], plan=bool(item.get("plan")),
            unchecked=item.get("unchecked") or 0, text=item.get("text") or "", updated_at=item["updatedAt"],
            main=mains.get(repo.lower()), paused=paused_note(project), notes=notes.get(row["subject"].lower(), []),
            open_numbers=numbers.get(repo.lower()) if not row["scope"] else None, notice=notices.get(row["subject"].lower()))
        row["next"] = next_step.settle(row["next"], holding(last, item.get("thread")), stage=row["stage"], stalled=row["stalled"])
        row["next"]["bucket"] = next_step.bucket(row["next"], stage=row["stage"], stalled=row["stalled"])
        row["switch"] = settled.needs_handle(last["run"], last["entry"]) if last and last["flow"] == "feedback" else None
        row["hint"] = reply_hint(row["next"], item.get("thread")) if row["next"]["bucket"] == "reply" and not row["next"].get("settled") else None
        row.setdefault("mode", "")
        row["their_at"] = their_at(item.get("thread"))  # 页面设暂时不处理时记下，之后对方更晚的动静让它回来
        snoozed_ = snooze.check(snoozes, row["subject"], row["their_at"], at_now)
        if snoozed_:  # 你设为暂时不处理：落搁置，对方有新动静或到点再回原来的类别（personal/snooze.py）
            row["snooze"] = {**snoozed_, "from": row["next"]["bucket"]}
            row["next"]["bucket"], row["hint"] = "hold", None
        rows.append(row)
    rows.sort(key=lambda r: r["updated_at"], reverse=True)
    if first["value"] is not None and (second["value"] is not None or not mine):
        notice_gone({r["subject"] for r in rows})
    rows = drop_closed(rows, closed_since(data, by_repo))
    fetched = (second["at"] if target and scoped(target) else first["at"]) if target else first["at"] or second["at"]
    return {"issues": rows, "label": LABEL, "error": error, "limited": limited, "autoloop_error": auto_error,
            "refreshing": first["refreshing"] or second["refreshing"],
            "upstream": upstream.tally(mine, theirs["open"], theirs["merged"]),
            "fetched_at": datetime.fromtimestamp(fetched).astimezone().isoformat(timespec="seconds") if fetched else None}


EPOCH = datetime.fromtimestamp(0).astimezone()


def signaled(raw, data, by_repo, own_at, theirs_at):
    """未读的 GitHub 通知（personal/fresh.py）比列表新的 issue：换成为通知单独重读到的那份（比列表新时），列表里没有的补上
    （登记了的仓库里只被 @ 的 issue 等），已关闭的去掉；还没重读到的后台重读，先把通知交给归类（notices）。
    返回 (raw, {主题（小写）: 通知}, 列表里原来没有、靠通知补上的主题)。"""
    signals = fresh.signals(data)
    if not signals:
        return raw, {}, set()
    index = {f"{r['repository']['nameWithOwner']}#{r['number']}".lower(): i for i, r in enumerate(raw)}
    out, drop, added, notices, wanted = list(raw), set(), set(), {}, {}
    for key, signal in signals.items():
        project = by_repo.get(signal["repo"])
        if signal["type"] != "Issue" or not project or project.get("archived") is True:
            continue
        listed_at = theirs_at if scoped(project) else own_at
        have = listed_at if key in index else 0
        found = fresh.entry(key)
        if found and found.get("node") and found["at"] > have:
            node, have = found["node"], found["at"]
            if node.get("state") != "OPEN":
                drop.add(key)
            elif key in index:
                out[index[key]] = node
            else:
                out.append(node)
                added.add(key)
        if fresh.stale(signal, have):
            notices[key] = signal
            wanted[key] = signal["updated_at"]
    if wanted:
        fresh.refresh(wanted)
    keep = [r for r in out if f"{r['repository']['nameWithOwner']}#{r['number']}".lower() not in drop]
    for key in notices.keys() - set(index) - added:  # 列表里没有、还没读到：先列一行，读到后换成真的
        signal = notices[key]
        project = by_repo[signal["repo"]]
        repo = project["github"] if project["github"].lower() == signal["repo"] else signal["repo"]
        keep.append({"type": "Issue", "repository": {"nameWithOwner": repo}, "number": signal["number"],
                     "title": signal["title"] or f"{repo}#{signal['number']}", "url": f"https://github.com/{repo}/issues/{signal['number']}",
                     "createdAt": signal["updated_at"], "updatedAt": signal["updated_at"], "author": {}, "labels": [], "commentsCount": 0})
        added.add(key)
    return keep, notices, added


def last_round(row, auto, feedback):
    """这条最近的一轮：「处理 issue」和「处理 issue 留言」里新的那一轮 {flow, run, cards, entry}（entry 是 issue 留言的交给记录）。
    没有轮次、最近一轮之后又交给过（还没派出）、最近一轮是返工的，都是 None。"""
    key = row["subject"].lower()
    entry = (feedback.get("handed") or {}).get(key) or {}
    pairs = [("issue", auto.get("runs", {}).get(row["subject"]), auto.get("waiting", {}).get(row["subject"]) or []),
             ("feedback", (feedback.get("runs") or {}).get(key), (feedback.get("waiting") or {}).get(key) or [])]
    pairs = [p for p in pairs if p[1]]
    if not pairs:
        return None
    flow, run_, cards = max(pairs, key=lambda p: (settled.parse(p[1].get("created_at")) or EPOCH).timestamp())
    if any(settled.after(h, run_.get("created_at")) for h in (row.get("handed_at"), entry.get("handed_at"))) or run_.get("rework"):
        return None
    return {"flow": flow, "run": run_, "cards": cards, "entry": entry}


def holding(last, thread):
    """最近一轮的结论是要你定或不用动、之后没有新动静（settled.holding）：返回结论，给 next_step.settle；否则 None。
    没读到留言（thread 是 None）判断不了有没有新动静，不拦。"""
    if not last or thread is None:
        return None
    from .conversation import assess
    info = assess(thread)
    found = settled.verdict(last["run"], last["cards"], flow=last["flow"])
    return settled.holding(found, their_at=info["their_at"], mine_at=info["mine_at"])


def reply_hint(step, thread):
    """待你回那一行的建议（reply_mode.hint）：「处理 issue」流程里等你决定、等你看的再交一轮只能处理（只回复不适用），
    其余看对方最后几条留言（conversation.assess，列表里已读到的，不另发请求）。"""
    if step["key"] == "flow" and step.get("action") != "feedback":
        return {"mode": reply_mode.HANDLE, "label": reply_mode.HINTS[reply_mode.HANDLE], "why": "「处理 issue」流程里的，再交一轮按处理走"}
    if thread is None:
        return reply_mode.hint([])
    from .conversation import assess
    return assess(thread)["hint"]


def their_at(thread):
    """对方（真人，不是你、不是机器人）最后一条留言的时间，列表里已读到的；只有正文的是开 issue 的时间。没读到留言是空串。"""
    if thread is None:
        return ""
    from .conversation import assess
    return assess(thread)["their_at"]


def group_by_repo(raw):
    out = {}
    for item in raw:
        out.setdefault(item["repository"]["nameWithOwner"].lower(), []).append(item)
    return out


def paused_note(project):
    """projects.toml 里 paused = true 的项目：返回 paused_note（没写是空串）；没暂停是 None。"""
    return str(project.get("paused_note") or "") if project.get("paused") is True else None


def feedback_state(data=None):
    """「处理 issue 留言」规则的轮次、提问和交给记录（待你回 issue 的「帮我回复」「处理并回复」走这条）：({runs, history, waiting, handed}, 错误)。"""
    from . import conversation
    auto, error = _rule_state(data, rule=conversation.RULE, parse=conversation.parse_result)
    lowered = {part: {k.lower(): v for k, v in (auto.get(part) or {}).items()} for part in ("runs", "history", "waiting")}
    lowered["handed"] = {k.lower(): v for k, v in conversation.ledger().items()}
    return lowered, error


FEEDBACK_FLOW = {"已交给 Agent", "处理中", "等你决定", "等你看"}  # issue 留言流程里还算在 Agent 手里的阶段；已处理（已回复）不算


def follow_feedback(row, feedback):
    """issue 留言流程还在派、在跑或等你看，且比「处理 issue」的流程新：这一行的阶段、最近一轮和运行记录换成它的（flow = feedback）。
    它的提问不在这里列（waiting 置空）。
    已处理完（已回复、开了 PR、不用动）的返回 {their_at, run, result}：那一轮看过的对方最后一条，给 next_step.classify 的 seen。"""
    from . import conversation
    key = row["subject"].lower()
    entry = (feedback.get("handed") or {}).get(key) or {}
    run_ = (feedback.get("runs") or {}).get(key)
    step = conversation.stage(run_, (feedback.get("waiting") or {}).get(key, []), entry.get("handed_at"))
    if step == "已处理":
        return {"their_at": entry.get("their_at") or "", "run": run_["id"], "result": run_.get("result"), "created_at": run_.get("created_at")}
    if step not in FEEDBACK_FLOW:
        return None
    at = max(entry.get("handed_at") or "", (run_ or {}).get("created_at") or "")
    if row["flow"] and flow_at(row) >= at:
        return None
    repo = row["repo"]
    row.update(flow="feedback", stage=step, stage_label=step, handed_at=entry.get("handed_at"), mode=entry.get("mode") or "", waiting=[], review=None,
               run={**run_, "result": conversation.shown_result(repo, run_.get("result"))} if run_ else None,
               history=[{**r, "result": conversation.shown_result(repo, r.get("result"))} for r in (feedback.get("history") or {}).get(key, [])[:20]])
    row["stalled"] = stalled(row)
    return None


def flow_at(row):
    return max(row.get("handed_at") or "", (row.get("run") or {}).get("created_at") or "")


def flow_of(row):
    """给 next_step.classify 的 Agent 流程；没有交给记录、轮次和 agent 标签的是 None。"""
    if not row["flow"]:
        return None
    return {"stage": row["stage"], "label": row.get("stage_label") or row["stage"], "kind": row["flow"], "at": row.get("handed_at")}


_open_lock = threading.Lock()
_open_subjects = None  # 上次列出的 open issue，'owner/repo#N'


def notice_gone(subjects):
    """issue 从 open 列表里消失（多半是关闭了）：让那个仓库的「已完成」缓存过期，下次读项目详情时重读。"""
    global _open_subjects
    with _open_lock:
        before, _open_subjects = _open_subjects, set(subjects)
    for subject in (before or set()) - set(subjects):
        activity.expire_closed(subject.rsplit("#", 1)[0])


def next_of(projects, repo, number, data=None):
    """列表（5 分钟缓存）里这条 issue 的那一行；不在列表里是 None。"""
    key = f"{repo}#{int(number)}".lower()
    return next((r for r in view(projects, data=data)["issues"] if r["subject"].lower() == key), None)


def handoff(projects, repo, number, *, mode=None, data=None, force=False):
    """交给 Agent，先按收件箱里 issue 的归类把关（next_step.allowed）：PR 评审中、待关闭、等对方回不收，说清原因；
    待你回（不论哪个仓库）和它的 issue 留言流程走 conversation.handoff，
    mode 是 reply（帮我回复，只写草稿）或 handle（处理并回复，缺省）；
    其余自己仓库的打 agent 标签并马上触发一次监看，之后的列表要重新读 GitHub 才看得到新标签；上游仓库的不打标签，见 upstream_handoff。
    「帮我回复」（mode=reply）只收走 issue 留言流程的，「处理 issue」流程的说清原因不收。
    Agent 上一轮已有结论、之后没有新动静的（待你定、Agent 看过）不带 force 不收（personal/settled.py）；force 是详情里的「仍交给 Agent」。
    「帮我回复」那一轮说要改代码的（row.switch），「处理并回复」照收，走 issue 留言流程（详情里的「改用处理并回复」）。"""
    mode = reply_mode.mode_of(mode)
    row = next_of(projects, repo, number, data)
    if row is None:
        raise ValueError(f"{repo}#{int(number)} 不在收件箱的 issue 里（可能已关闭，或列表还没读到），刷新后再交")
    if row.get("snooze"):
        raise ValueError(f"没交给：{snooze.REFUSED}")
    route, reason = next_step.allowed(row["next"], force)
    if row.get("switch") and mode == reply_mode.HANDLE:
        route, reason = "feedback", ""
    if reason:
        raise ValueError(f"没交给：{reason}")
    if route == "feedback":
        return feedback_handoff(projects, row["subject"], mode, data, **({"force": True} if force else {}))
    if mode == reply_mode.REPLY:
        raise ValueError(f"没交给：{REPLY_ONLY_REFUSED}")
    project = next((p for p in projects if (p.get("github") or "").lower() == repo.lower()), {})
    item = detail(projects, repo, number)
    forcing = {"force": True} if force else {}  # 只在「仍交给 Agent」时带，监看照它放行（agent_watch.repeats）
    if scoped(project):
        return upstream_handoff(project, repo, number, item, **forcing)
    try:
        result = agent_watch.handoff(repo, number, item["title"], item["url"], **forcing)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"没交给成：给 {repo}#{number} 打 {LABEL} 标签失败（{exc}）") from None
    github.expire("issues")
    github.expire("detail")
    return result


BATCH_MAX = 50
REPLY_ONLY_REFUSED = "这条走「处理 issue」流程（改代码、开 PR），「帮我回复」只写回复草稿，用「处理并回复」或「交给 Agent」"
BATCH_BUCKETS = {"todo", "reply"}  # 列表上只批量交待动手、待你回；待你看在详情里一条条交


def batch_handoff(projects, items, *, mode=None, data=None, trigger=None):
    """收件箱勾选的 issue 一起交给 Agent，逐条按收件箱的归类分流、逐条给结果，一条失败不影响别的。
    每条先看筛选（只收待动手、待你回），再过 next_step.allowed（和单条的交给接口同一把关）：
    issue 留言流程（待你回，不论哪个仓库）的一起走 conversation.handoff；「处理 issue」流程的自己仓库打 agent 标签，上游仓库记 upstream-handoffs.json，
    这两种不逐条触发中台，全部记完后只触发一次监看。返回 {results: [{subject, ok, flow, handed_at, error}], handed, routed, error}。
    mode 是 reply（帮我回复：只写回复草稿）或 handle（处理并回复，缺省），只给 issue 留言流程；mode=reply 时「处理 issue」流程的逐条拒绝。"""
    mode = reply_mode.mode_of(mode)
    if not isinstance(items, list) or not items:
        raise ValueError("请至少选一条")
    if len(items) > BATCH_MAX:
        raise ValueError(f"一次最多交 {BATCH_MAX} 条")
    wanted = {}
    for raw in items:
        found = isinstance(raw, str) and re.fullmatch(r"([\w.-]+/[\w.-]+)#([0-9]+)", raw.strip())
        if not found:
            raise ValueError(f"写法不对：{str(raw)[:80]}（要写 owner/repo#编号）")
        wanted.setdefault(raw.strip().lower(), raw.strip())
    rows = {r["subject"].lower(): r for r in view(projects, data=data)["issues"]}
    results = {}
    feedback, later = [], []
    quiet = lambda *args, **kwargs: ""  # 逐条记账时不触发中台，最后一起触发一次

    def fail(low, subject, reason):
        results[low] = {"subject": subject, "ok": False, "flow": "", "handed_at": None, "error": reason}

    for low, subject in wanted.items():
        row = rows.get(low)
        if row is None:
            fail(low, subject, "不在收件箱的 issue 里（可能已关闭，或列表还没读到），刷新后再交")
            continue
        subject = row["subject"]
        if row.get("snooze"):
            fail(low, subject, f"没交给：{snooze.REFUSED}")
            continue
        where = row["next"].get("bucket") or next_step.bucket(row["next"], stage=row["stage"], stalled=row["stalled"])
        if where not in BATCH_BUCKETS:
            fail(low, subject, f"这条在「{next_step.BUCKETS.get(where, where)}」，列表上只交待动手和待你回")
            continue
        route, reason = next_step.allowed(row["next"])
        if reason:
            fail(low, subject, f"没交给：{reason}")
            continue
        if route == "feedback":
            feedback.append((low, subject))
            continue
        if mode == reply_mode.REPLY:
            fail(low, subject, f"没交给：{REPLY_ONLY_REFUSED}")
            continue
        project = next((p for p in projects if (p.get("github") or "").lower() == row["repo"].lower()), {})
        try:
            if scoped(project):
                done = upstream_handoff(project, row["repo"], row["number"], detail(projects, row["repo"], row["number"]), trigger=quiet)
            else:
                done = agent_watch.handoff(row["repo"], row["number"], row["title"], row["url"], trigger=quiet)
        except github.RateLimited as exc:
            fail(low, subject, f"{exc}，这次没交给")
            continue
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            fail(low, subject, f"没交给成：{exc}")
            continue
        results[low] = {"subject": subject, "ok": True, "flow": "issue", "handed_at": done["handed_at"], "error": ""}
        later.append(subject)
    errors = []
    if feedback:
        from . import conversation
        try:
            done = conversation.handoff(projects, [s for _, s in feedback], mode=mode, data=data)
        except ValueError as exc:
            done = {"handed": [], "skipped": [{"subject": s, "reason": str(exc)} for _, s in feedback], "error": ""}
        handed = {h["subject"].lower(): h for h in done["handed"]}
        skipped = {s["subject"].lower(): s["reason"] for s in done["skipped"]}
        for low, subject in feedback:
            if low in handed:
                results[low] = {"subject": subject, "ok": True, "flow": "feedback", "handed_at": handed[low]["handed_at"], "error": ""}
            else:
                fail(low, subject, skipped.get(low) or "没交出")
        if done.get("error"):
            errors.append(done["error"])
    routed = ""
    if later:
        github.expire("issues")
        github.expire("detail")
        at = agent_watch.now()
        try:
            routed = (trigger or agent_watch.emit_check)("issues-batch", at, f"交给 Agent 的 {len(later)} 条 issue", "")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"已记下，但没能马上触发中台（{exc}），工作台的监看闸门 10 分钟内会再触发一次")
    out = [results[low] for low in wanted]
    return {"results": out, "handed": sum(r["ok"] for r in out), "routed": routed, "error": "；".join(errors)}


def feedback_handoff(projects, subject, mode=None, data=None, force=False):
    """待你回 issue 的「帮我回复」「处理并回复」（不论哪个仓库）：记进 issue-feedback-handoffs.json（带模式）并触发中台（conversation.handoff），不写 GitHub。"""
    from . import conversation
    done = conversation.handoff(projects, [subject], mode=mode, data=data, **({"force": True} if force else {}))
    first = done["handed"][0]
    return {"subject": first["subject"], "handed_at": first["handed_at"], "routed": done["routed"], "error": done["error"],
            "flow": "feedback"}


def upstream_handoff(project, repo, number, item, *, trigger=None, state=None, at=None, comments=None, force=False):
    """只读与我有关的仓库（issue_scope = "mine"，多是别人的仓库）的 issue 交给 Agent：不写 GitHub。先确认中台的「处理 issue」接得住 issues.opened，
    在 upstream-handoffs.json 的锁里重读账本、删掉超过 7 天的旧记录、跳过已经交给还在派或在处理的（双击也只记一次），
    记下交给时间、标题、链接和写好的 detail，再触发一次监看。上游仓库（forks）的 detail 写明上游仓库，技能照它 fork 后开 PR；
    写了 push_access = true 的不写，技能在原仓库开分支开 PR。"""
    missing = agent_watch.issue_rule_missing()
    if missing:
        raise ValueError(missing)
    key = f"{repo}#{int(number)}"
    if "/issues/" not in (item.get("url") or ""):
        raise ValueError(f"{key} 不是 issue（PR 在收件箱的 PR 行上交给），没交给")
    if (item.get("state") or "OPEN") != "OPEN":
        raise ValueError(f"{key} 已经关闭，没交给")
    try:  # 详情只带最近 20 条评论：交给时读全；读失败照交，detail 里写明怎么读
        thread = (comments or agent_watch.issue_comments)(repo, int(number))
    except github.RateLimited as exc:
        raise ValueError(f"{exc}，这次没交给") from None
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        thread = str(exc) or type(exc).__name__
    text = agent_watch.build_detail(repo, project, {"number": int(number), "url": item["url"], "body": item.get("body") or ""},
                                    thread, upstream=forks(project))
    auto, auto_error = state if state is not None else autoloop_state()
    if auto_error:  # 看不到中台就判断不了这条是不是还在处理，不放行
        raise ValueError(f"{auto_error}，没交给")
    at = at or agent_watch.now()
    stamp = at.isoformat(timespec="seconds")
    run_ = auto.get("runs", {}).get(key)
    with agent_watch.handoff_ledger(agent_watch.upstream_ledger_path()) as book:  # 在锁里重读再判断
        for old in [k for k, v in book.items() if agent_watch.stale_entry(v, at)]:
            del book[old]
        previous = next((v for k, v in book.items() if k.lower() == key.lower()), {})
        row = {"subject": key, "labels": [], "scope": MINE, "run": run_, "handed_at": previous.get("handed_at")}
        row["stage"] = stage(row, auto, row["handed_at"])
        if row["stage"] == "处理中" or (row["stage"] == "已交给 Agent" and not stalled(row, at)):
            raise ValueError(f"{key} 已经交给 Agent，还在派或在处理")
        for old in [k for k in book if k.lower() == key.lower()]:
            del book[old]
        their, mine = agent_watch.comment_times(thread)  # 监看据此判断这一轮之后有没有新动静（agent_watch.repeats）
        book[key] = {"handed_at": stamp, "title": item.get("title") or key, "url": item.get("url") or "", "detail": text,
                     "their_at": their, "mine_at": mine, **({"force": True} if force else {})}
    try:
        routed, error = (trigger or agent_watch.emit_check)(key, at, item.get("title") or "", item.get("url") or ""), ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        routed, error = "", f"已记下，但没能马上触发中台（{exc}），工作台的监看闸门 10 分钟内会再触发一次"
    return {"subject": key, "handed_at": stamp, "routed": routed, "error": error}


def answer(projects, question, body, *, send=None):
    """回答 Agent 对某条 issue 的提问，或把处理结果标成看过：调中台协议的 POST /v1/cards/<编号>/answer
    （agent-autoloop answer 背后就是它）。不走命令行，正文以 - 或 -- 开头也原样送到，不会被当成选项。
    choice 是从 1 起的选项序号，text 是别的想法。只接受当前项目里 issue 的提问。"""
    auto, error = autoloop_state()
    if error:
        raise ValueError(error)
    repos = {(p.get("github") or "").lower() for p in projects}
    known = {w["id"] for subject, items in auto.get("waiting", {}).items() for w in items
             if subject.rsplit("#", 1)[0].lower() in repos}
    if int(question) not in known:
        raise ValueError("这条提问不在当前项目的 issue 里，或已经回答过")
    return deliver(question, body, send=send)


def deliver(question, body, *, send=None):
    """把回答送到中台：POST /v1/cards/<编号>/answer。调用方先核对这条提问是不是它管的。"""
    try:
        done = (send or hub)(f"/v1/cards/{int(question)}/answer", reply(body))
    except OSError as exc:
        raise ValueError(f"读不到 agent-autoloop：{exc}") from None
    if not done.get("changed"):
        return {"message": "这条提问已经处理过了，这次没有改动。"}
    return {"message": "结案了。" if done.get("state") == "closed" else "收到，Agent 接着干。"}


def reply(body):
    """页面送来的一条回答 → 中台协议的回答：choice 从 1 起，协议里从 0 起；text 是别的想法。"""
    if not isinstance(body, dict):
        raise ValueError("回答格式不对")
    payload = {}
    choice, words = body.get("choice"), body.get("text", "")
    if choice is not None:
        if not isinstance(choice, int) or isinstance(choice, bool) or not 0 < choice <= 20:
            raise ValueError("选项序号无效")
        payload["choice"] = choice - 1  # 协议里从 0 起
    if not isinstance(words, str) or len(words) > 4000:
        raise ValueError("回答最多 4000 字")
    if words.strip():
        payload["text"] = words.strip()
    if not payload:
        raise ValueError("请选一个选项或写下回答")
    return payload


def answer_package(projects, subject, body, *, send=None):
    """一次答完一条 issue 的决定包：body 是 {"answers": [{"id": 提问编号, "choice": 从 1 起, "text": …}, …]}。
    先全部校验，再按顺序逐条交给中台。中台没有一次交多条回答的接口：每条回答各排一轮，只有前一轮还在排队时
    才并进去；空闲的 worker 往往在第一条到达时就领走了那一轮，后面的回答落进下一轮。所以一个包可能分成
    同一个 PR 上的几轮处理，技能按每轮拿到的那部分回答做。只接受这条 issue 还没答的提问，处理结果（notice）不在包里。"""
    answers = body.get("answers")
    if not isinstance(answers, list) or not 0 < len(answers) <= 20:
        raise ValueError("请至少答一条")
    auto, error = autoloop_state()
    if error:
        raise ValueError(error)
    if subject.rsplit("#", 1)[0].lower() not in {(p.get("github") or "").lower() for p in projects}:
        raise ValueError("这条 issue 不在当前项目里")
    known = {w["id"] for w in auto.get("waiting", {}).get(subject, []) if w["kind"] == "ask"}
    planned = []
    for item in answers:
        question = item.get("id") if isinstance(item, dict) else None
        if not isinstance(question, int) or isinstance(question, bool) or question not in known:
            raise ValueError("有一条提问不在这条 issue 里，或已经回答过；刷新后再答")
        if question in {q for q, _ in planned}:
            raise ValueError("同一条提问答了两次")
        planned.append((question, reply(item)))
    sent = 0
    for question, payload in planned:
        try:
            done = (send or hub)(f"/v1/cards/{question}/answer", payload)
        except (OSError, ValueError) as exc:
            raise ValueError(f"已送出 {sent} 条，第 {sent + 1} 条没送出：{exc}；已送出的不用再答，剩下的可以再送一次") from None
        sent += bool(done.get("changed"))
    if not sent:
        return {"message": "这些提问已经处理过了，这次没有改动。"}
    return {"message": f"收到 {sent} 条回答，Agent 接着干。"}


def linked(projects, repo, number):
    """引用这条 issue 的 open PR，只读；缓存 5 分钟，过期先给旧值、后台刷新，限流时不发请求。"""
    if repo not in {p.get("github") for p in projects}:
        raise ValueError("这个仓库不在当前项目范围内")
    result = github.cached(("linked", repo.lower(), int(number)), lambda: upstream.linked_prs(repo, number), CACHE_SECONDS)
    note = github.describe(result)
    return {"prs": result["value"] or [], "error": "" if note["limited"] else note["message"],
            "limited": note["message"] if note["limited"] else ""}


def read_detail(repo, number):
    return json.loads(github.run(["gh", "issue", "view", str(int(number)), "--repo", repo, "--json",
                                  "number,title,body,url,labels,author,createdAt,updatedAt,comments,state"], "issue"))


def detail(projects, repo, number, *, read=None):
    """issue 详情，缓存 DETAIL_SECONDS 秒；限流或读失败时给缓存里的旧数据，并在 notice 里说明。"""
    if repo not in {p.get("github") for p in projects}:
        raise ValueError("这个仓库不在当前项目范围内")
    result = github.cached(("detail", repo, int(number)), lambda: (read or read_detail)(repo, number), DETAIL_SECONDS, background=False)
    note = github.describe(result)
    issue = result["value"]
    if issue is None:
        raise ValueError(note["message"][:300] or "GitHub 读取失败")
    comments = [{"author": (c.get("author") or {}).get("login", ""), "body": c.get("body") or "",
                 "created_at": c.get("createdAt")} for c in issue.get("comments", [])][-20:]
    return {"repo": repo, "number": issue["number"], "title": issue["title"], "body": issue.get("body") or "",
            "url": issue["url"], "labels": [label["name"] for label in issue.get("labels", [])],
            "author": (issue.get("author") or {}).get("login", ""), "created_at": issue["createdAt"],
            "updated_at": issue["updatedAt"], "comments": comments, "state": issue.get("state") or "OPEN",
            "notice": note["message"] if note["limited"] or result["error"] else ""}
