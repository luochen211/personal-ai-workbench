"""把带 agent 标签的 issue 交给 agent-autoloop：定时监看、显式「交给 Agent」和失败提醒。

agent-autoloop 的两条规则调用 ~/.agent-autoloop/issues/watch.py：「立即看 agent issue」在工作台点「交给 Agent」时、
和工作台的监看闸门（personal/watch_gate.py，每 5 分钟看一眼 GitHub）发现监看还没看过的变化时马上一次；
「看带 agent 标签的 issue」每小时一次，是工作台没开时的兜底。watch.py 只是薄入口（personal/watch_entry.py），
按环境变量 PAIHUO_ROOT 或同目录的 paihuo-root 文件找到本仓库，再调用这里的 main()。

    python3 -m personal.agent_watch          # 监看一次，回执打印到标准输出
    python3 -m personal.agent_watch install  # 仓库搬家后运行：更新 paihuo-root、watch.py、桌面通道、技能和中台规则
    python3 -m personal.agent_watch rereview owner/repo#34  # 审查卡住时手动重派一次（新后缀的 id）

监看只读 GitHub，请求都经 personal/github.py（并发上限、限流退避、计数）；带标签的 issue 和 agent PR 先用闸门写的
快照 github-snapshot.json，过期才自己读（一个 GraphQL 请求），每轮结束把看过的状态和下次复查时刻记进 gate.json。事件 id 默认是 owner/repo#编号，
中台按 id 去重。第一次运行时已有的 issue 记进 baseline.json（带记录时间），之后只有「agent 标签在记录时间之后
才打上」或「在工作台显式交给」的才放行。确认「标签早于记录时间」的结论记在 held.json，不再每轮翻 events；
issue 掉出搜索结果（标签被摘掉）时删掉结论，重新打标签后再核对。
显式交给记在 handoffs.json（读-改-写加文件锁），事件 id 加上交给时间，同一条 issue 再交一次会重新派。
「PR 待处理」页交给的 PR 记在 pr-handoffs.json（同样加锁），不打标签（别人的仓库没有权限）：每一轮把一天内交给的
PR 作为 prs.feedback 事件交给中台（detail 在交给时就写好，监看不为它读 GitHub），规则「处理 PR 反馈」接它。
收件箱「待你回」里交给的 issue 留言记在 issue-feedback-handoffs.json（同样加锁、不打标签），每一轮作为 issues.feedback 事件交给中台，
规则「处理 issue 留言」接它。这两条规则按对象并行（FEEDBACK_PARALLEL），用单独的 Agent（FEEDBACK_AGENT，模型 FEEDBACK_MODEL）。
「学习」标签点「写下一篇」记在 learning-handoffs.json（同样加锁），作为 learning.next 交给规则「写学习文章」（技能 dbs-learning）。
「回顾」标签点「让 Agent 写总结和三问」记在 review-handoffs.json（同样加锁），作为 review.next 交给规则「写回顾」（技能 assistant-review）。
上游仓库（别人的仓库）里与我有关的 issue 也不打标签：交给记在 upstream-handoffs.json（同样加锁），每一轮把一天内交给的
作为 issues.opened 事件交给中台（detail 标明「上游仓库」），规则「处理 issue」照常接，技能 handle-issue 按上游仓库的做法 fork 后开 PR。
连续失败 FAIL_NOTIFY_AFTER 次发一次桌面通知，恢复成功后清零。

不派重复的（用户 2026-11-19 定，见 personal/settled.py）：要第一次报给中台的 issue、issue 留言、PR 反馈事件，先看这条最近一轮
（「处理 issue」「处理 issue 留言」或「处理 PR 反馈」）已结束、结论是要你定或不用动，而交给时（或这一轮读到的评论里）
这一轮之后既没有别人的新留言、评审、提交，也没有你的新留言，又不是工作台详情里「仍交给 Agent」（force）交的，就不派，
记进 repeats.json（两天后删），回执第一行写明。读不到中台时照派。

审查与返工（思路借自 ai-daming/daming-skills issue-pr-pipeline 的 Review Agent，MIT）：同一轮监看还读 agent/issue-<编号>
分支开的 PR。只审本人仓库（owner 是 OWNER，别的仓库要在 projects.toml 写 review_prs = true）里、本人账号从同一仓库开的 PR；
fork 来的、别人开的都不审，因为审查 Agent 会在本机装依赖、跑测试。审查结果是本人账号在 PR 上留的评论（带 head 提交标记，
最后一行 VERDICT），别人贴的不算。当前 head 没审过、CI 跑完了（或没有 CI）、这条 issue 的「处理 issue」不在跑，派一次
pr.review，事件 id 绑定 head 提交；最近一次审查是 CHANGES_REQUIRED，派 issues.rework 回到同一分支和 PR，最多 MAX_REWORK 轮，
再要改就停下等本人；WAIT 隔 WAIT_RETRY 再审，同一提交最多再审 MAX_WAITS 次。
派过的事件记进 dispatched.json，不再重放（中台按 id 永久去重）；派出后 STUCK_AFTER 还没结论、或返工后 head 没变，
提醒一次，不自动重派。CI 没过、CI 久不出结果、等待满了、审查期间 head 变了、PR 不是本人合并的，也各提醒一次。
审查独立只是约定：审查 Agent 和处理 issue 一样有完整权限，靠技能约束它不改代码、不推送、不合并。Agent 都不合并。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from . import github, reply_mode, settled, upstream

ROOT = Path(__file__).resolve().parents[1]
ENTRY = Path(__file__).with_name("watch_entry.py")
CHANNEL = Path(__file__).with_name("autoloop_plugins") / "desktop" / "channel.ts"
SKILL = "handle-issue"  # 「处理 issue」规则加载的技能，正本在 personal/skills/handle-issue
REVIEW_SKILL = "review-pr"  # 「审查 PR」规则加载的技能，正本在 personal/skills/review-pr
PR_SKILL = "handle-pr-feedback"  # 「处理 PR 反馈」规则加载的技能，正本在 personal/skills/handle-pr-feedback
ISSUE_FEEDBACK_SKILL = "handle-issue-feedback"  # 「处理 issue 留言」规则加载的技能，正本在 personal/skills/handle-issue-feedback
SKILLS = (SKILL, REVIEW_SKILL, PR_SKILL, ISSUE_FEEDBACK_SKILL)
PR_RULE = "处理 PR 反馈"
PR_EVENT = "prs.feedback"
ISSUE_FEEDBACK_RULE = "处理 issue 留言"
ISSUE_FEEDBACK_EVENT = "issues.feedback"
FEEDBACK_RULES = (PR_RULE, ISSUE_FEEDBACK_RULE)
# 工作台「学习」标签点「写下一篇」：记在 learning-handoffs.json，作为 learning.next 交给规则「写学习文章」（技能 dbs-learning，
# 技能在 ~/.claude/skills，不在本仓）。只在点了时交，监看不会自己生出这种事件
LEARNING_RULE = "写学习文章"
LEARNING_EVENT = "learning.next"
LEARNING_SKILL = "dbs-learning"
# 工作台「回顾」标签点「让 Agent 写总结和三问」：记在 review-handoffs.json，作为 review.next 交给规则「写回顾」
# （技能 assistant-review，在助理仓库，不在本仓；和定时的「周回顾」「月回顾」是同一个技能）。只在点了时交
# 收件箱点「读新需求」：每个来源一条本机事件、一条规则（不进沙箱的 command Agent，不调 Claude）。录音是核心来源，规则在这里；
# 插件的来源（微信）在插件清单里（personal/plugins/<id>/plugin.json 的 needs_sources 和 autoloop），装插件时加、去掉时删
RECORDING_RULE = "录音拟 issue"
RECORDING_EVENT = "recordings.issues.scan"
RECORDING_AGENT = "recording-issues"
RECORDING_SCRIPT = "scripts/recording_issues.py"  # 在助理目录（ASSISTANT_ROOT）：得到、飞书妙记、本机录音文件，很私人，不进本仓
RECAP_RULE = "写回顾"
RECAP_EVENT = "review.next"
RECAP_SKILL = "assistant-review"
RECAP_LIKE = ("周回顾", "月回顾")  # 「写回顾」的 Agent、目录照这两条定时规则里有的那条抄
# 中台规则的 parallel：写了就按对象（subject）分事项，同一对象一次一轮，这条规则（同一组）最多同时这么多轮
# （agent-autoloop kernel/rules.ts、kernel/decide.ts 的 enqueue 和 claimNext）。真正同时跑几个还受接单程序容量限制：
# ~/.agent-autoloop/config.json 的 worker.capacity，没写是 WORKER_CAPACITY（worker/worker.ts），全部规则合起来算。
FEEDBACK_PARALLEL = 10
WORKER_CAPACITY = 2
FEEDBACK_AGENT = "claude-feedback"  # 这两条规则用的 Agent：照「处理 issue」的 Agent 抄一份，只多一个 model
FEEDBACK_MODEL = "claude-opus-5-5"  # 中台按 Agent 选模型（rules.json 里 agents.<id>.model）
LABEL = "agent"
LABEL_COLOR = "0E8A16"
OPENED = "issues.opened"
CHECK = "issues.check"
REVIEW = "pr.review"
REWORK = "issues.rework"
HANDLE_RULE = "处理 issue"
REVIEW_RULE = "审查 PR"
REVIEW_AGENT = "claude-review"
WATCH_RULES = ("看带 agent 标签的 issue", "立即看 agent issue")
BRANCH = re.compile(r"agent/issue-([1-9]\d*)")
REVIEW_MARK = re.compile(r"<!-- paihuo-review head=([0-9a-f]{7,40}) -->")
VERDICTS = ("APPROVE", "CHANGES_REQUIRED", "WAIT")  # PR 评论里认的结论
STALE = "STALE"  # 只在审查回执里：派出后 PR 又有新提交，没审、没留评论
VERDICT_LINE = re.compile(r"VERDICT\s*[:：]\s*([A-Z_]+)")
FENCE = re.compile(r"(`{3,}|~{3,})[\w-]*")
OWNER = upstream.LOGIN  # 默认只审这个账号名下仓库里的 PR；别的仓库在 projects.toml 写 review_prs = true
MAX_REWORK = 3  # 返工最多几轮（按派出记录数）；第 MAX_REWORK + 1 次要改就停下等本人
MAX_WAITS = 3  # 同一提交审出 WAIT 后最多再审几次（第一次之外），即同一提交最多审 1 + MAX_WAITS 次
WAIT_RETRY = timedelta(hours=1)
SETTLE = timedelta(minutes=3)  # 没有 CI 的提交至少放这么久再审，免得 CI 还没登记就开审
STUCK_AFTER = timedelta(hours=2)  # 审查派出后这么久还没有结论评论、返工派出后这么久 head 没变：提醒一次，不重派
CI_PATIENCE = timedelta(hours=1)  # CI 一直没跑完这么久：提醒一次
RECHECK = timedelta(minutes=10)  # 「处理 issue」还在跑、读不到中台而没派审查：隔这么久再看（工作台闸门按它触发）
LEDGER = "dispatched.json"
LEDGER_KEEP = timedelta(days=30)
CI_DONE = {"SUCCESS", None}  # 没有 CI 时 statusCheckRollup 是 null
CI_FAILED = {"FAILURE", "ERROR"}
ACTIVE_RUNS = {"queued", "claimed", "unknown"}  # 中台里还没结束的轮次
REVIEW_TEXT = 2500  # 返工事件里审查意见最多的字数
FAIL_NOTIFY_AFTER = 3
# 没派出 issue 的一轮：第一行以中台协议的「没事可做：」开头，中台把这一轮记成 nothing，工作台不列进运行记录
NOTHING = "没事可做："
IDLE = f"没有新的带 {LABEL} 标签的 issue"
DETAIL_LIMIT = 4000  # 中台 protocol/validate.ts 对 detail 的上限
FEEDBACK_DETAIL_LIMIT = DETAIL_LIMIT - 100  # PR 反馈、issue 留言交给时写的 detail：派活时第一行还要写模式（reply_mode.LINES），先留出位置
BODY_SHARE = 1500  # 有评论时正文最多占的字数，其余留给评论
COMMENT_LIMIT = 600  # 每条评论最多的字数
HANDOFF_FRESH = timedelta(days=1)
LEDGER_KEEP = timedelta(days=7)  # 上游 issue 的交给记录留这么久，之后交给或监看时删掉
GUARD = "_guard"  # 事件上只给监看自己用的字段（交给时间、force、交给时别人和你最后的动静），报给中台前去掉
REPEATS = "repeats.json"  # 上一轮已有结论、之后没有新动静而没派的事件 id → 记下的时刻
REPEAT_PAGES = 20  # 找最近一轮最多往前翻这么多页 /v1/state（一页 60 轮，约一周）
UPSTREAM_SCOPE = "范围：上游仓库（别人的仓库，你没有推送权限）。按技能 handle-issue「上游仓库」一节做：先查有没有人在做，fork 到你的账号再向上游开 PR。"


def home():
    return Path(os.environ.get("AGENT_AUTOLOOP_HOME") or Path.home() / ".agent-autoloop")


def folder():
    return home() / "issues"


def run(args, timeout=120):
    """本机命令（agent-autoloop）；GitHub 请求走 github.run。"""
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "").strip()[:500] or f"{args[0]} 失败")
    return result.stdout


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """写到同目录的临时文件再换名；原文件在就沿用它的权限（临时文件默认 0600）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name, suffix=".tmp",
                                     delete=False) as stream:
        json.dump(data, stream, ensure_ascii=False, indent=1)
    if mode is not None:
        os.chmod(stream.name, mode)
    Path(stream.name).replace(path)


def hub(path, body=None):
    """调本机中台的本人接口（GET /v1/state、POST /v1/cards/<编号>/answer），地址和本人密钥取自 agent-autoloop 的 config.json。"""
    config = json.loads((home() / "config.json").read_text(encoding="utf-8"))
    headers = {"Authorization": "Bearer " + config["owner"]}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    request = Request(config["url"].rstrip("/") + path, data=data, headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get("error") or str(exc)
        except (ValueError, AttributeError):
            message = str(exc)
        raise ValueError(f"中台没接受：{message}") from None


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result if result.tzinfo else result.astimezone()


def now():
    return datetime.now().astimezone()


def projects(path=None):
    """按 agent 标签巡检的项目（登记表合上本机覆盖文件 projects.local.toml，见 personal/projects_config.py）。"""
    from . import projects_config
    rows = projects_config.load(path)["projects"]
    # 上游仓库（issue_scope = "mine"）不在别人的仓库里按标签派活；归档的项目（archived = true）不再巡检
    return {p["github"]: p for p in rows if p.get("github") and p.get("issue_scope") != "mine" and p.get("archived") is not True}


def search(repos):
    """这些仓库里带 agent 标签的 open issue（带最近的评论）。先用工作台闸门刚读的快照，过期才读 GitHub（personal/watch_gate.py）。"""
    from . import watch_gate
    return watch_gate.issues(repos)


def view_one(repo, number):
    """直接读一条 issue；GitHub 搜索索引要过一阵才收录新标签，刚交给的 issue 用它补上。"""
    item = json.loads(github.run(["gh", "issue", "view", str(int(number)), "--repo", repo,
                                  "--json", "number,title,url,body,labels,state"], "issue"))
    if item.get("state") != "OPEN" or LABEL not in {label["name"] for label in item.get("labels", [])}:
        return None
    return {"repository": {"nameWithOwner": repo}, "number": item["number"], "title": item["title"],
            "url": item["url"], "body": item.get("body") or ""}


def recent_handoffs(issues, known, handoffs, fetch, *, at=None):
    """一天内显式交给、但搜索结果里还没有的 issue，逐条直接读。"""
    found = {f"{i['repository']['nameWithOwner']}#{i['number']}" for i in issues}
    at = at or now()
    extra = []
    for key, value in handoffs.items():
        repo, _, number = key.rpartition("#")
        handed = handed_time(value)
        if key in found or repo not in known or not handed or at - parse_time(handed) > HANDOFF_FRESH:
            continue
        try:
            item = fetch(repo, number)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            continue
        if item:
            extra.append(item)
    return extra


def labeled_at(repo, number):
    """agent 标签最近一次打上的时间；读不到就当没有。"""
    pages = json.loads(github.run(["gh", "api", "--method", "GET", "--paginate", "--slurp",
                                   f"repos/{repo}/issues/{int(number)}/events?per_page=100"], "rest:events") or "[]")
    times = [parse_time(e["created_at"]) for page in pages for e in page
             if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == LABEL]
    return max(times) if times else None


def load_baseline(path=None):
    """返回 (记录时间, issue 集合)；旧格式是纯列表，用文件修改时间当记录时间。"""
    path = Path(path or folder() / "baseline.json")
    data = read_json(path, None)
    if data is None:
        return None, set()
    if isinstance(data, list):
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc), set(data)
    return parse_time(data["recorded_at"]), set(data.get("issues", []))


def load_handoffs(path=None):
    data = read_json(path or folder() / "handoffs.json", {})
    return data if isinstance(data, dict) else {}


def handed_time(value):
    """handoffs.json 里一条的交给时间：平常是时间字符串；详情里「仍交给 Agent」交的写成 {handed_at, force: true}。"""
    if isinstance(value, dict):
        value = value.get("handed_at")
    return value if isinstance(value, str) and value else None


def forced(value):
    return isinstance(value, dict) and value.get("force") is True


@contextmanager
def locked(path, load):
    """加排他文件锁读 path（load 读出来），with 块正常结束才写回；并发的读-改-写不会互相覆盖。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = load(path)
            yield data
            write_json(path, data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def handoff_ledger(path=None):
    """handoffs.json 的读-改-写，并发交给不会互相覆盖。"""
    with locked(path or folder() / "handoffs.json", load_handoffs) as ledger:
        yield ledger


def pr_ledger_path():
    return folder() / "pr-handoffs.json"


def issue_feedback_ledger_path():
    return folder() / "issue-feedback-handoffs.json"


def upstream_ledger_path():
    return folder() / "upstream-handoffs.json"


def learning_ledger_path():
    return folder() / "learning-handoffs.json"


def recap_ledger_path():
    return folder() / "review-handoffs.json"


def ledger_events(ledger, prefix, kind, at=None):
    """账本里一天内交给的记录 → 事件。detail 在交给时写好，这里不读 GitHub；
    事件 id 带交给时间，中台按 id 去重，同一条再交一次会重新派。
    记录带模式（reply_mode：帮我回复只写草稿 / 处理并回复）的，派活提示（detail）第一行写明模式，技能照它走；没有模式的旧记录不写，按处理并回复。"""
    at = at or now()
    events = []
    for key, entry in sorted(ledger.items()):
        if not isinstance(entry, dict) or not entry.get("handed_at"):
            continue
        try:
            if at - parse_time(entry["handed_at"]) > HANDOFF_FRESH:
                continue
        except ValueError:
            continue
        link = entry.get("url") or ""
        head = reply_mode.line(entry.get("mode"))
        detail = f"{head}\n{entry.get('detail') or ''}" if head else entry.get("detail") or ""
        events.append({"id": prefix + event_id(key, entry["handed_at"]), "type": kind, "subject": key,
                       "summary": (entry.get("title") or key)[:200], "detail": detail[:DETAIL_LIMIT],
                       "link": link if link.startswith("https://github.com/") else ""})
    return events


def ledger_guards():
    """账本事件（上游 issue、PR 反馈、issue 留言）的事件 id → 交给时记下的 {handed_at, force, their_at, mine_at}，给 repeats 用。"""
    out = {}
    for path, prefix in ((upstream_ledger_path(), "upstream:"), (pr_ledger_path(), "pr:"), (issue_feedback_ledger_path(), "issue-feedback:")):
        for key, entry in load_handoffs(path).items():
            if isinstance(entry, dict) and handed_time(entry):
                try:
                    out[prefix + event_id(key, entry["handed_at"])] = {
                        "handed_at": entry["handed_at"], "force": entry.get("force") is True,
                        "their_at": entry.get("their_last") or entry.get("their_at") or "", "mine_at": entry.get("mine_at") or ""}
                except ValueError:
                    continue
    return out


def pr_events(ledger=None, *, at=None):
    """一天内在「PR 待处理」页交给的 PR → prs.feedback 事件，规则「处理 PR 反馈」接。"""
    ledger = load_handoffs(pr_ledger_path()) if ledger is None else ledger
    return ledger_events(ledger, "pr:", PR_EVENT, at)


def issue_feedback_events(ledger=None, *, at=None):
    """一天内在收件箱「待你回」里交给的 issue 留言 → issues.feedback 事件，规则「处理 issue 留言」接。"""
    ledger = load_handoffs(issue_feedback_ledger_path()) if ledger is None else ledger
    return ledger_events(ledger, "issue-feedback:", ISSUE_FEEDBACK_EVENT, at)


def learning_events(ledger=None, *, at=None):
    """一天内在「学习」标签点了「写下一篇」的课题 → learning.next 事件，规则「写学习文章」接。"""
    ledger = load_handoffs(learning_ledger_path()) if ledger is None else ledger
    return ledger_events(ledger, "learning:", LEARNING_EVENT, at)


def recap_events(ledger=None, *, at=None):
    """一天内在「回顾」标签点了「让 Agent 写总结和三问」的那一期 → review.next 事件，规则「写回顾」接。"""
    ledger = load_handoffs(recap_ledger_path()) if ledger is None else ledger
    return ledger_events(ledger, "recap:", RECAP_EVENT, at)


def stale_entry(entry, at, keep=LEDGER_KEEP):
    """账本里格式不对、或交给时间早于 keep 的记录。"""
    try:
        return not isinstance(entry, dict) or at - parse_time(entry.get("handed_at")) > keep
    except (TypeError, ValueError):
        return True


def upstream_events(ledger=None, *, at=None):
    """一天内交给的上游 issue → issues.opened 事件，规则「处理 issue」照常接（detail 第一行写明上游仓库）。
    事件 id 是 upstream:owner/repo#编号@交给时间。账本里有超过 LEDGER_KEEP 的旧记录就顺手删掉。"""
    at = at or now()
    if ledger is None:
        ledger = load_handoffs(upstream_ledger_path())
        if any(stale_entry(v, at) for v in ledger.values()):
            with handoff_ledger(upstream_ledger_path()) as book:
                for key in [k for k, v in book.items() if stale_entry(v, at)]:
                    del book[key]
                ledger = dict(book)
    return ledger_events(ledger, "upstream:", OPENED, at)


def load_held(recorded_at, path=None):
    """已确认「标签早于 baseline」的结论；baseline 重记过就作废。"""
    data = read_json(path or folder() / "held.json", {})
    if not isinstance(data, dict) or data.get("recorded_at") != (recorded_at.isoformat() if recorded_at else None):
        return {}
    return dict(data.get("issues") or {})


def save_held(recorded_at, held, path=None):
    write_json(path or folder() / "held.json",
               {"recorded_at": recorded_at.isoformat() if recorded_at else None, "issues": held})


def event_id(key, handed_at=None):
    if not handed_at:
        return key
    return f"{key}@{parse_time(handed_at).strftime('%Y%m%dT%H%M%S%z')}"


def issue_comments(repo, number):
    """一条 issue 的全部评论，旧的在前：[{author, at, body}]。"""
    item = json.loads(github.run(["gh", "issue", "view", str(int(number)), "--repo", repo, "--json", "comments"],
                                 "issue:comments") or "{}")
    return [{"author": (c.get("author") or {}).get("login") or "?", "at": (c.get("createdAt") or "")[:10],
             "time": c.get("createdAt") or "", "body": (c.get("body") or "").strip()} for c in item.get("comments") or []]


def bot_login(login):
    from .pr_feedback import BOTS
    login = (login or "").lower()
    return login.endswith("[bot]") or login in BOTS


def comment_times(comments):
    """issue_comments 读到的评论 → (别人最后一条的时间, 你最后一条的时间)；机器人不算，读失败（字符串）是两个空串。"""
    if not isinstance(comments, list):
        return "", ""
    theirs = [c.get("time") or "" for c in comments if c.get("author", "").lower() != OWNER.lower() and not bot_login(c.get("author"))]
    mine = [c.get("time") or "" for c in comments if c.get("author", "").lower() == OWNER.lower()]
    return max(theirs, default=""), max(mine, default="")


def clip(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def build_detail(repo, project, item, comments, *, upstream=False):
    """事件 detail：仓库、本地目录、正文和评论，合起来不超过中台上限 DETAIL_LIMIT。
    comments 是列表，或读失败时的错误说明（字符串）。评论放不下时留最近的，并写明怎么读全部。
    upstream 是上游仓库（别人的仓库）的 issue：第一行写明范围，技能按上游仓库的做法处理。"""
    read_all = f"gh issue view {item['number']} --repo {repo} --comments"
    head = "\n".join(([UPSTREAM_SCOPE] if upstream else []) + [
        f"仓库：{repo}", f"项目：{project.get('name', '')}", f"本地目录：{project.get('repo_path') or ''}", f"issue：{item['url']}"])
    body = item.get("body") or ""
    if isinstance(comments, str):
        tail = f"\n评论没读到（{clip(comments, 200)}），动手前运行 {read_all} 读全。"
    elif not comments:
        tail = "\n评论：无。"
    else:
        tail = None
    if tail is not None:
        room = DETAIL_LIMIT - len(head) - len(tail) - len("\n正文：\n")
        return f"{head}\n正文：\n{clip(body, room)}{tail}"[:DETAIL_LIMIT]
    body = clip(body, BODY_SHARE)
    note = f"\n评论共 {len(comments)} 条，下面是最近的 {{shown}} 条（每条最多 {COMMENT_LIMIT} 字）；全部运行 {read_all}。"
    room = DETAIL_LIMIT - len(head) - len("\n正文：\n") - len(body) - len(note) - 8
    picked = []
    for c in reversed(comments):
        line = f"\n- {c['author']} {c['at']}：{clip(c['body'], COMMENT_LIMIT)}"
        if len(line) > room:
            break
        picked.append(line)
        room -= len(line)
    shown = note.format(shown=len(picked)) if len(picked) < len(comments) else f"\n评论共 {len(comments)} 条："
    return f"{head}\n正文：\n{body}{shown}{''.join(reversed(picked))}"[:DETAIL_LIMIT]


def collect(issues, known, baseline, handoffs, when_labeled, verdicts=None, comments=None):
    """挑出要派的 issue。baseline 里的只在标签晚于记录时间或显式交给时放行。
    verdicts 是已确认不派的结论（key → 标签时间），命中就不再读 events；读成功且确认不派时补进去。
    只给放行的 issue 读评论（comments，缺省 issue_comments），读失败照派，在 detail 里注明。"""
    comments = comments or issue_comments
    recorded_at, skip = baseline
    verdicts = {} if verdicts is None else verdicts
    events, held = [], []
    for item in issues:
        repo = item["repository"]["nameWithOwner"]
        key = f"{repo}#{item['number']}"
        if repo not in known:
            continue
        value = handoffs.get(key)
        handed = handed_time(value)
        if key in skip and not handed:
            if key in verdicts:
                held.append(key)
                continue
            try:
                at, known_at = when_labeled(repo, item["number"]), True
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                at, known_at = None, False  # 没读到不记结论，下一轮再核对
            if at is None or recorded_at is None or at <= recorded_at:
                held.append(key)
                if known_at and recorded_at is not None:
                    verdicts[key] = at.isoformat() if at else None
                continue
        try:  # 快照里带着评论（watch_gate.issue_rows）就不再单独读
            thread = item["comments"] if isinstance(item.get("comments"), list) else comments(repo, item["number"])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            thread = str(exc) or type(exc).__name__
        detail = build_detail(repo, known[repo], item, thread)
        their_at, mine_at = comment_times(thread)
        events.append({"id": event_id(key, handed), "type": OPENED, "subject": key,
                       "summary": item["title"][:200], "detail": detail, "link": item["url"],
                       GUARD: {"handed_at": handed or "", "force": forced(value), "their_at": their_at, "mine_at": mine_at}})
    return events, held


PR_FIELDS = ("number title url isDraft isCrossRepository headRefName headRefOid baseRefName author { login } "
             "commits(last: 1) { nodes { commit { oid committedDate statusCheckRollup { state } } } } "
             "comments(last: 100) { nodes { body url createdAt author { login } } }")
MERGED_FIELDS = ("number url headRefName headRefOid isCrossRepository author { login } mergedBy { login } mergedAt "
                 "comments(last: 30) { nodes { body url createdAt author { login } } }")


def reviewable(repo, project):
    """只审本人仓库（owner 是 OWNER）里的 PR；别人的仓库要在 projects.toml 里给这个项目写 review_prs = true 才审。"""
    return repo.split("/", 1)[0].lower() == OWNER.lower() or project.get("review_prs") is True


def own_pr(pr, viewer):
    """本人（gh 当前登录账号）从同一仓库的分支开的 PR；fork 来的、别人开的都不审，字段缺了也不审。"""
    author = ((pr.get("author") or {}).get("login") or "").lower()
    return bool(viewer) and author == viewer.lower() and pr.get("isCrossRepository") is False


def agent_prs(repos):
    """这些仓库里本人从 agent/issue-<编号> 分支开的 PR：开着的（审查用）、近一周合并的（看是谁合并的），
    加上 gh 当前登录的账号（viewer）。返回 {"viewer", "open", "merged"}。和 search 一样先用闸门的快照（personal/watch_gate.py），
    过期才读：一个 GraphQL 请求，按作者和分支搜，不再按仓库逐组读。"""
    from . import watch_gate
    return watch_gate.prs(repos)


def parse_verdict(text, allowed=VERDICTS):
    """审查结论：最后一个非空行是 VERDICT: APPROVE / CHANGES_REQUIRED / WAIT。只按换行切（\\v、\\f、U+2028 不算换行）；
    容忍常见的手误：末尾的代码块围栏（``` / ~~~）、加粗或反引号、句号、全角冒号。allowed 是认的结论，回执另外认 STALE。"""
    lines = [line.strip() for line in (text or "").split("\n") if line.strip()]
    while lines and FENCE.fullmatch(lines[-1]):
        lines.pop()
    found = VERDICT_LINE.fullmatch(lines[-1].strip("*_`。.!！ \t")) if lines else None
    return found[1] if found and found[1] in allowed else None


def reviews_of(pr, viewer):
    """PR 上的审查评论，旧的在前：带 head 标记、最后一行是结论、由本人账号（viewer）留的；别人贴的标记一律不算。"""
    out = []
    for c in (pr.get("comments") or {}).get("nodes") or []:
        if not viewer or ((c.get("author") or {}).get("login") or "").lower() != viewer.lower():
            continue
        mark = REVIEW_MARK.search(c.get("body") or "")
        verdict = parse_verdict(c.get("body"))
        if mark and verdict:
            out.append({"sha": mark[1], "verdict": verdict, "at": parse_time(c["createdAt"]), "url": c.get("url") or "",
                        "body": c["body"]})
    return out


def head_of(pr):
    nodes = (pr.get("commits") or {}).get("nodes") or [{}]
    return nodes[-1].get("commit") or {}


def load_ledger(path=None):
    """读 dispatched.json；坏掉的条目丢掉（时间读不懂的也丢），不让一条坏记录卡住整轮监看。"""
    data = read_json(path or folder() / LEDGER, {})
    data = data if isinstance(data, dict) else {}

    def stamped(value):
        try:
            parse_time(value)
            return isinstance(value, str)
        except (TypeError, ValueError):
            return False
    parts = {part: data.get(part) if isinstance(data.get(part), dict) else {} for part in ("sent", "told", "holds", "redo")}
    return {"sent": {k: v for k, v in parts["sent"].items() if isinstance(v, dict) and stamped(v.get("at"))
                     and isinstance(v.get("pr"), str) and isinstance(v.get("head"), str)},
            "told": {k: v for k, v in parts["told"].items() if stamped(v)},
            "holds": {k: v for k, v in parts["holds"].items() if isinstance(v, dict) and stamped(v.get("since"))},
            "redo": {k: v for k, v in parts["redo"].items() if isinstance(v, int) and not isinstance(v, bool) and v > 0}}


@contextmanager
def dispatch_ledger(path=None):
    """派出记录 dispatched.json（读-改-写加文件锁，和 handoffs.json 一样）：
    sent 是派出过的审查和返工（事件 id → 派出时间、PR、提交、种类），派过的 id 不再放进事件；
    told 是发过的一次性提醒；holds 是每个 PR 为什么暂时不审（给工作台显示）；
    redo 是本人用 rereview 要求重审的次数（PR → 次数），审查 id 带上它，中台就当成新事件。"""
    with locked(path or folder() / LEDGER, load_ledger) as ledger:
        yield ledger


def busy_subjects(read=None):
    """「处理 issue」还在排队或在跑的 issue（subject）。读中台 /v1/state 第一页，它总带着全部没结束的轮次。"""
    state = (read or hub)("/v1/state")
    return {e.get("subject") for r in state.get("runs") or [] if r.get("rule") == HANDLE_RULE and r.get("state") in ACTIVE_RUNS
            for e in r.get("events") or [] if e.get("subject")}


def review_pass(found, known, ledger, busy, at=None, wakes=None):
    """一轮监看里每个 agent PR 的下一步：派审查（pr.review）、派返工（issues.rework）、记一条不审的原因，或什么都不做。
    派过的事件 id 记进 ledger["sent"]，以后不再放进事件（中台按 (来源, id) 永久去重，再放只会被静默丢掉）。
    卡住的情况各发一次提醒（ledger["told"]）。busy 是取「处理 issue」在跑的 issue 的函数，返回 None 表示没读到。
    wakes（列表）收集「GitHub 不变也要再看」的时刻：等 SETTLE、WAIT 重审、「处理 issue」在跑、卡住和 CI 太久的提醒；
    工作台闸门到点触发一轮（personal/watch_gate.py）。
    返回 (事件, [(标题, 正文)] 要发的提醒, 没派审查的说明)。"""
    at = at or now()
    wakes = [] if wakes is None else wakes
    viewer = found.get("viewer") or ""
    sent, told, before = ledger["sent"], ledger["told"], ledger["holds"]
    redos = ledger.setdefault("redo", {})
    events, notices, holds, notes = [], [], {}, []
    stamp = at.isoformat(timespec="seconds")
    running = []

    def tell(key, title, text):
        if key not in told:
            told[key] = stamp
            notices.append((title, text))

    def active():
        if not running:
            try:
                running.append(busy())
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                running.append(None)
                notes.append(f"没读到中台的轮次（{clip(str(exc), 120)}），这一轮不派审查")
        return running[0]

    for pr in found.get("open") or []:
        repo, sha = pr["repo"], pr.get("headRefOid") or ""
        project = known.get(repo)
        if not project or not sha or not reviewable(repo, project) or not own_pr(pr, viewer):
            continue
        issue = int(BRANCH.fullmatch(pr["headRefName"])[1])
        key, subject, number = f"{repo}#{pr['number']}", f"{repo}#{issue}", pr["number"]
        mine = sorted(((i, e) for i, e in sent.items() if e.get("pr") == key), key=lambda pair: pair[1].get("at", ""))
        reworks = [e for _, e in mine if e.get("kind") == "rework"]
        reviews = reviews_of(pr, viewer)
        current = [r for r in reviews if sha.startswith(r["sha"])]
        commit = head_of(pr)

        def hold(why, since=None):
            old = before.get(key) or {}
            keep = old.get("why") == why and old.get("head") == sha
            holds[key] = {"subject": subject, "head": sha, "why": why, "since": old.get("since") if keep else (since or stamp)}
            return parse_time(holds[key]["since"])

        last_sent = next(((i, e) for i, e in reversed(mine) if e.get("kind") == "review"), None)
        if last_sent:
            rid, entry = last_sent
            sent_at = parse_time(entry["at"])
            answered = [r for r in reviews if entry["head"].startswith(r["sha"]) and r["at"] >= sent_at]
            if entry["head"] == sha and not answered and at - sent_at < STUCK_AFTER and f"stuck:{rid}" not in told:
                wakes.append(sent_at + STUCK_AFTER)
            if entry["head"] == sha and not answered and at - sent_at >= STUCK_AFTER:
                tell(f"stuck:{rid}", f"{subject} 的 PR #{number} 审查没有结论",
                     f"审查派出已超过 2 小时，PR 上还没有提交 {sha[:12]} 的审查评论：审查没跑、没发出评论或评论格式不对。"
                     "看一下中台「审查 PR」那一轮；不会自动重派。")
            moved = commit.get("committedDate")
            if entry["head"] != sha and moved and not any(parse_time(e["at"]) > sent_at for e in reworks):
                until = answered[0]["at"] if answered else at
                if sent_at <= parse_time(moved) <= until:
                    tell(f"moved:{rid}", f"{subject} 的 PR #{number} 在审查期间有新提交",
                         f"审查派出时是 {entry['head'][:12]}，审完前 PR 变成了 {sha[:12]}。审查 Agent 不该推送；"
                         "不是你推的就看一下是谁改的。新提交会重新审查。")
        if reworks and reworks[-1].get("head") == sha and at - parse_time(reworks[-1]["at"]) < STUCK_AFTER:
            wakes.append(parse_time(reworks[-1]["at"]) + STUCK_AFTER)
        if reworks and reworks[-1].get("head") == sha and at - parse_time(reworks[-1]["at"]) >= STUCK_AFTER:
            round_ = reworks[-1].get("round") or len(reworks)
            tell(f"stuck:rework:{key}@{round_}", f"{subject} 返工第 {round_} 轮没有新提交",
                 f"返工派出已超过 2 小时，PR #{number} 还停在 {sha[:12]}：返工没跑、卡住了或认为不该改。看一下这条 issue。")

        if pr.get("isDraft"):
            hold("草稿，不审查")
            continue
        last = current[-1] if current else None
        if last and last["verdict"] == "APPROVE":
            continue
        if last and last["verdict"] == "CHANGES_REQUIRED":
            if any(e.get("head") == sha for e in reworks):
                continue  # 这一提交已经派过返工，等新提交
            if len(reworks) >= MAX_REWORK:
                hold(f"返工 {MAX_REWORK} 轮后审查仍要改，不再自动返工，等你决定")
                continue
            event = rework_event(repo, project, pr, issue, len(reworks) + 1, last)
            sent[event["id"]] = {"at": stamp, "kind": "rework", "pr": key, "subject": subject, "head": sha,
                                 "round": len(reworks) + 1}
            events.append(event)
            continue
        waits = sum(r["verdict"] == "WAIT" for r in current)
        if last:  # WAIT：隔 WAIT_RETRY 再审，同一提交最多再审 MAX_WAITS 次
            if waits > MAX_WAITS:
                hold(f"同一提交审查等待了 {waits} 次，不再自动重审")
                tell(f"wait:{key}@{sha[:12]}", f"{subject} 的 PR #{number} 审查一直等待",
                     f"提交 {sha[:12]} 已审出 {waits} 次「等待」，不再自动重审。看一下审查评论里在等什么。")
                continue
            if at - last["at"] < WAIT_RETRY:
                wakes.append(last["at"] + WAIT_RETRY)
                continue
        redo = redos.get(key, 0)
        rid = review_id(repo, number, sha, waits, redo)
        if rid in sent:
            continue  # 派过了，等评论；超时见上面的提醒
        busy_now = active()
        if busy_now is None:
            wakes.append(at + RECHECK)
            continue
        if subject in busy_now:
            hold("「处理 issue」那一轮还没跑完，跑完再审")
            wakes.append(at + RECHECK)
            continue
        state = (commit.get("statusCheckRollup") or {}).get("state")
        if state in CI_FAILED:
            hold("CI 没过，未审查")
            tell(f"ci:{key}@{sha[:12]}", f"{subject} 的 PR #{number} CI 没过，未审查",
                 f"提交 {sha[:12]} 的 CI 没过，审查先不派；修好推了新提交再审。")
            continue
        if state not in CI_DONE:
            since = hold("CI 还在跑，未审查")  # CI 跑完时 GitHub 状态会变，闸门会触发；这里只为「太久」的提醒定时
            if at - since < CI_PATIENCE and f"ci-slow:{key}@{sha[:12]}" not in told:
                wakes.append(since + CI_PATIENCE)
            if at - since >= CI_PATIENCE:
                tell(f"ci-slow:{key}@{sha[:12]}", f"{subject} 的 PR #{number} CI 一直没跑完",
                     f"提交 {sha[:12]} 的 CI 已经 {int((at - since).total_seconds() // 3600)} 小时多没出结果，审查还没派。")
            continue
        if state is None and commit.get("committedDate") and at - parse_time(commit["committedDate"]) < SETTLE:
            wakes.append(parse_time(commit["committedDate"]) + SETTLE)
            continue
        event = review_event(repo, project, pr, issue, sha, reviews, len(reworks), waits, redo)
        sent[event["id"]] = {"at": stamp, "kind": "review", "pr": key, "subject": subject, "head": sha}
        events.append(event)

    for pr in found.get("merged") or []:
        key = f"{pr['repo']}#{pr['number']}"
        mine = sorted((e for e in sent.values() if e.get("pr") == key), key=lambda e: e.get("at", ""))
        if not viewer or not mine:
            continue
        by = (pr.get("mergedBy") or {}).get("login") or ""
        merged_at = parse_time(pr["mergedAt"]) if pr.get("mergedAt") else at
        when = (pr.get("mergedAt") or "")[:16]
        if by.lower() != viewer.lower():  # Agent 用的就是本人账号：这一条只抓得到别的账号合并
            tell(f"merged:{key}", f"{key} 不是你的账号合并的",
                 f"agent PR {key} 由 {by or '未知账号'} 在 {when} 合并，不是你的账号。看一下是怎么合进去的。")
        last = mine[-1]
        since = parse_time(last["at"])
        if since > merged_at:
            continue
        if last.get("kind") == "rework":
            settled = (pr.get("headRefOid") or "") != last.get("head")  # 返工推了新提交
            what = f"返工第 {last.get('round') or '?'} 轮"
        else:
            settled = any(last["head"].startswith(r["sha"]) and since <= r["at"] <= merged_at for r in reviews_of(pr, viewer))
            what = "审查"
        if not settled:
            tell(f"merged-early:{key}", f"{key} 在{what}出结论前就合并了",
                 f"{what}派出后（{last['at'][:16]}）还没出结论，PR 在 {when} 被 {by or '未知账号'} 合并。"
                 "Agent 用的是你的账号，这可能是 Agent 合并的；看一下合进去的是不是你要的。")

    opened = {f"{pr['repo']}#{pr['number']}" for pr in found.get("open") or []}
    old = at - LEDGER_KEEP
    for i in [i for i, e in sent.items() if e.get("pr") not in opened and parse_time(e.get("at") or stamp) < old]:
        sent.pop(i)
    for k in [k for k, v in told.items() if parse_time(v) < old]:
        told.pop(k)
    for k in [k for k in redos if k not in opened]:
        redos.pop(k)
    ledger["holds"] = holds
    return events, notices, "；".join(dict.fromkeys(notes))


def pr_head(repo, project, pr, issue):
    return [f"仓库：{repo}", f"项目：{project['name']}", f"本地目录：{project['repo_path']}",
            f"issue：https://github.com/{repo}/issues/{issue}",
            f"PR：{pr['url']}（#{pr['number']}，分支 {pr['headRefName']} → {pr.get('baseRefName') or '?'}）"]


def review_id(repo, number, sha, waits, redo=0):
    """审查事件 id：绑定 head 提交；WAIT 后重审加 ~<次数>，本人 rereview 重派加 +<次数>。"""
    return f"review:{repo}#{number}@{sha[:12]}" + (f"~{waits}" if waits else "") + (f"+{redo}" if redo else "")


def review_event(repo, project, pr, issue, sha, reviews, reworks, waits, redo=0):
    """link 是 PR 里这个提交的页面（…/pull/<号>/commits/<完整 SHA>）：中台的 /v1/state 不带 detail，工作台从 link 读审的是哪个提交。"""
    lines = pr_head(repo, project, pr, issue) + [
        f"审查的提交：{sha}",
        f"这是第 {len(reviews) + 1} 次审查；已返工 {min(reworks, MAX_REWORK)} 轮（最多 {MAX_REWORK} 轮）。"]
    if reworks >= MAX_REWORK:
        lines.append(f"返工已满 {MAX_REWORK} 轮：这次还要改就停下等本人，不再返工。")
    if reviews:
        lines.append(f"上一次审查：{reviews[-1]['url']}（{reviews[-1]['verdict']}）")
    return {"id": review_id(repo, pr["number"], sha, waits, redo), "type": REVIEW, "subject": f"{repo}#{issue}",
            "summary": f"审查 PR #{pr['number']}：{pr['title']}"[:200], "detail": "\n".join(lines)[:DETAIL_LIMIT],
            "link": f"{pr['url']}/commits/{sha}"}


def rework_event(repo, project, pr, issue, round_, review):
    head = "\n".join(pr_head(repo, project, pr, issue) + [
        f"返工第 {round_} 轮（最多 {MAX_REWORK} 轮）：按审查意见改，推到同一个分支和 PR，不新开 PR。",
        f"审查意见：{review['url']}（审的是 {review['sha'][:12]}）"])
    room = DETAIL_LIMIT - len(head) - 20
    body = clip(review["body"], min(REVIEW_TEXT, room))
    return {"id": f"rework:{repo}#{pr['number']}@{round_}", "type": REWORK, "subject": f"{repo}#{issue}",
            "summary": f"返工第 {round_} 轮：PR #{pr['number']} {pr['title']}"[:200],
            "detail": f"{head}\n审查意见原文：\n{body}"[:DETAIL_LIMIT], "link": review["url"] or pr["url"]}


def type_matches(types, kind):
    """和中台 typeMatches 一样：写 *、写全名，或写 x.* 认 x. 开头的类型。"""
    return any(t == "*" or t == kind or (t.endswith(".*") and kind.startswith(t[:-1])) for t in types or []
               if isinstance(t, str))


def rules_missing(path=None):
    """中台 rules.json 里审查和返工要的几处还缺什么；都在返回空串。中台会拒收规则 emits.types 里没写的事件，
    缺着就派，派出记录却记成派过了，之后再也不会审，所以缺了就整轮不派审查和返工。"""
    data = read_json(path or home() / "rules.json", None)
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return "读不到 rules.json"
    named = {r.get("name"): r for r in rules if isinstance(r, dict)}
    sources = set()
    for name in WATCH_RULES:
        emits = (named.get(name) or {}).get("emits")
        emits = emits if isinstance(emits, dict) else {}
        if not (type_matches(emits.get("types"), REVIEW) and type_matches(emits.get("types"), REWORK)):
            return f"「{name}」不能发 {REVIEW} 和 {REWORK}"
        sources.add(emits.get("source"))
    if len(sources) != 1:
        return "两条监看规则发事件的来源不一样"
    source = sources.pop()

    def takes(rule, kind):
        when = rule.get("when") if isinstance(rule, dict) else None
        return isinstance(when, dict) and when.get("source") == source and type_matches(when.get("types"), kind)
    if not any(takes(r, REVIEW) for r in rules):
        return f"没有规则接 {REVIEW}"
    if not takes(named.get(HANDLE_RULE), REWORK):
        return f"「{HANDLE_RULE}」不接 {REWORK}"
    return ""


def reviews_due(known, prs=None, busy=None, taken=(), at=None, wakes=None):
    """这一轮要派的审查和返工；读 PR 失败不影响派 issue，返回 (事件, 说明)。
    taken 是这一轮刚派出的 issue：它们的「处理 issue」马上要排队，也不派审查。
    中台规则还没补齐（rules_missing）时一个都不派、不记，也不读 PR。"""
    repos = sorted(repo for repo, project in known.items() if reviewable(repo, project))
    if not repos:
        return [], ""
    missing = rules_missing()
    if missing:
        return [], f"中台规则还没补，先运行 install（{missing}），审查和返工这一轮不派"
    try:
        found = (prs or agent_prs)(repos)
    except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        if wakes is not None:
            wakes.append((at or now()) + RECHECK)
        return [], f"没读到 agent PR（{clip(str(exc), 200)}），审查下一轮再看"

    def busy_now():
        return set((busy or busy_subjects)()) | set(taken)
    with dispatch_ledger() as ledger:
        events, notices, note = review_pass(found, known, ledger, busy_now, at, wakes)
    for title, text in notices:
        notify(title, text)
    return events, note


def fenced(data):
    # 反引号转义：issue 正文里的 ``` 不会提前结束回执代码块
    return "```agent-autoloop\n" + json.dumps(data, ensure_ascii=False).replace("`", "\\u0060") + "\n```"


def notify(title, text):
    """点了能打开工作台的提醒（personal/notify_open.py）；它会等人点，所以脱离本进程跑。失败不影响监看结果。"""
    try:
        subprocess.Popen([sys.executable, "-m", "personal.notify_open", "notify", f"--title={title}", f"--text={text[:300]}"],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def record_result(error, *, path=None, alert=None):
    """记连续失败次数，到 FAIL_NOTIFY_AFTER 次时只通知一次；成功就清零。"""
    path = Path(path or folder() / "watch-state.json")
    state = read_json(path, {})
    if not error:
        if state.get("failures"):
            write_json(path, {"failures": 0, "notified": False, "last_ok": now().isoformat(timespec="seconds")})
        return
    failures = int(state.get("failures", 0)) + 1
    notified = bool(state.get("notified"))
    if failures >= FAIL_NOTIFY_AFTER and not notified:
        (alert or notify)("派活：看 issue 连续失败", f"已连续 {failures} 次没跑成：{error}")
        notified = True
    write_json(path, {**state, "failures": failures, "notified": notified, "last_error": str(error)[:500],
                      "last_failed": now().isoformat(timespec="seconds")})


def with_reviews(head, events, known, prs, busy=None, wakes=None):
    """把审查和返工事件接在 issue 事件后面，回执第一行补一句；只写这一轮真的派出的。"""
    extra, note = reviews_due(known, prs, busy, taken={e["subject"] for e in events}, wakes=wakes)
    events = events + extra
    if extra:
        words = "、".join(("审查 " if e["type"] == REVIEW else "返工 ") + e["subject"] for e in extra)
        head = f"派出 {words}" if head.startswith(NOTHING) else f"{head}；{words}"
    if note:
        head += "；" + note
    return head, events


def watch(*, known=None, read=search, when_labeled=labeled_at, fetch=view_one, prs=None, upstream=None,
          pulls=None, busy=None, feedback=None, learning=None, lookup=None, recaps=None):
    """一轮监看：带 agent 标签的 issue、交给的上游 issue、收件箱交给的 PR 和 issue 留言（feedback），
    加上 agent PR 的审查和返工，一起交给中台。
    pulls 是要审的 agent PR（不给就读 GitHub），busy 是「处理 issue」正在跑的 issue。

    交给后一天内每轮都会把同一批事件再报一次，靠中台按 id 去重；第一行只写这一轮第一次报的。
    全都报过的一轮写成「没事可做：」，中台记成 nothing，运行记录里不再每轮列一遍「派出 N 条」。"""
    from . import watch_gate
    known = projects() if known is None else known
    watch_gate.begin()
    wakes = []
    head, events = issue_round(known=known, read=read, when_labeled=when_labeled, fetch=fetch)
    prs = pr_events() if prs is None else prs
    upstream = upstream_events() if upstream is None else upstream
    feedback = issue_feedback_events() if feedback is None else feedback
    learning = learning_events() if learning is None else learning
    recaps = recap_events() if recaps is None else recaps
    dropped, note = repeats(events + upstream + prs + feedback, lookup=lookup)
    if dropped:
        events, upstream, prs, feedback = ([e for e in group if e["id"] not in dropped] for group in (events, upstream, prs, feedback))
    fresh = first_reports(events + upstream + prs + feedback + learning + recaps)
    if head.startswith("派出 "):
        rest = head.split("；", 1)[1] if "；" in head else ""
        new = [e for e in events if e["id"] in fresh]
        head = (f"派出 {len(new)} 条 issue：" + "、".join(e["subject"] for e in new)) if new else NOTHING + IDLE
        head += "；" + rest if rest else ""
    head, events = with_reviews(head, events, known, pulls, busy, wakes)
    if note:
        head += "；" + note
    receipt = with_prs(head, events, prs, fresh, upstream, feedback, learning, recaps)
    watch_gate.mark_seen(wakes)  # 闸门据此判断 GitHub 有没有监看还没看过的变化、什么时候该复查
    return receipt


GUARDED = {OPENED: (HANDLE_RULE, ISSUE_FEEDBACK_RULE), ISSUE_FEEDBACK_EVENT: (HANDLE_RULE, ISSUE_FEEDBACK_RULE), PR_EVENT: (PR_RULE,)}


def latest_runs(wanted, read=None, pages=REPEAT_PAGES):
    """中台里这些 (subject 小写, 规则组) 最近一轮（全部状态）和开着的提问、通知：({(subject, 规则组): slim 后的轮次}, cards)。
    从 /v1/state 第一页往前翻，都找到了或翻满 pages 页就停。"""
    from . import issues
    read = read or hub
    page = read("/v1/state")
    cards = [c for c in page.get("cards") or [] if c.get("state") == "open"]
    found = {}
    for _ in range(pages):
        for r in page.get("runs") or []:
            for e in r.get("events") or []:
                for key in wanted:
                    if (e.get("subject") or "").lower() == key[0] and r.get("rule") in key[1]:
                        if key not in found or r["id"] > found[key]["id"]:
                            found[key] = issues.slim(r)
        if all(k in found for k in wanted) or not (page.get("hasMore") and page.get("nextBefore")):
            break
        page = read(f"/v1/state?before={int(page['nextBefore'])}")
    return found, cards


def run_verdict(run_, cards, read_result=None):
    """一轮的结论（settled.verdict）：结果行按规则读（读不到是结果不明）；提问、通知认这一轮交回执时建的，或标题、正文写着这条的。"""
    from . import conversation, issues, pr_feedback
    parse = {HANDLE_RULE: issues.parse_result, ISSUE_FEEDBACK_RULE: conversation.parse_result, PR_RULE: pr_feedback.parse_result}
    run_ = {**run_, "result": (read_result or issues.outcome)(run_, parse=parse.get(run_["rule"]))}
    subject = next((s for s in run_["subjects"] if s), "")
    mine = [{**c, "tier": issues.tiered(c.get("title") or "", c.get("kind"))[1]} for c in cards
            if c.get("createdAt") == run_.get("finished_at") or (subject and subject in (c.get("title") or "") + (c.get("text") or ""))]
    return settled.verdict(run_, mine)


def repeats(events, *, lookup=None, at=None):
    """不派重复的：返回 (这一轮不报给中台的事件 id, 回执第一行补的一句)。
    已经报过的（emitted.json）、force 的、学习文章不管；repeats.json 里记过的照旧不报；其余第一次要报的读一次中台，
    最近一轮已结束、结论是要你定或不用动、交给时这一轮之后没有新动静的不报，记进 repeats.json。lookup 给测试替换 latest_runs。"""
    path = folder() / REPEATS
    at = at or now()
    with locked(path, lambda p: read_json(p, {})) as store:
        for key in [k for k, v in store.items() if not isinstance(v, str) or at - parse_time(v) > 2 * HANDOFF_FRESH]:
            del store[key]
        known = set(store)
        dropped = {e["id"] for e in events if e["id"] in known}
        emitted = read_json(folder() / "emitted.json", {})
        emitted = emitted if isinstance(emitted, dict) else {}
        todo = [e for e in events if e["type"] in GUARDED and e["id"] not in known and e["id"] not in emitted
                and not (e.get(GUARD) or {}).get("force")]
        if not todo:
            return dropped, ""
        wanted = {(e["subject"].lower(), GUARDED[e["type"]]) for e in todo}
        try:
            runs, cards = (lookup or latest_runs)(wanted)
        except (OSError, ValueError, KeyError) as exc:
            return dropped, f"没读到中台，没核对重复（{str(exc)[:80]}）"
        held, ledgers = [], ledger_guards()
        for e in todo:
            run_ = runs.get((e["subject"].lower(), GUARDED[e["type"]]))
            guard = e.get(GUARD) or ledgers.get(e["id"]) or {}
            if guard.get("force"):
                continue
            if not run_ or (guard.get("handed_at") and not settled.after(guard["handed_at"], run_.get("created_at"))):
                continue  # 没跑过，或这一轮是在这次交给之后建的（已经接上了）
            found = settled.holding(run_verdict(run_, cards), their_at=guard.get("their_at") or "", mine_at=guard.get("mine_at") or "")
            if found:
                store[e["id"]] = at.isoformat(timespec="seconds")
                dropped.add(e["id"])
                held.append(f"{e['subject']}（第 {found['run']} 轮{'要你定' if found['kind'] == settled.DECIDE else '说不用动'}）")
    note = f"上一轮已有结论、之后没有新动静，不再派：{'、'.join(held)}" if held else ""
    return dropped, note


def first_reports(events):
    """这一轮第一次报给中台的事件 id。记在 emitted.json（id → 第一次报的时刻），超过两天的删掉。"""
    path = folder() / "emitted.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            stored = read_json(path, {})
            stored = stored if isinstance(stored, dict) else {}
            at = now()
            seen = {k: v for k, v in stored.items() if isinstance(v, str) and at - parse_time(v) <= 2 * HANDOFF_FRESH}
            fresh = {e["id"] for e in events if e["id"] not in seen}
            if fresh or seen != stored:
                write_json(path, {**seen, **{k: at.isoformat(timespec="seconds") for k in fresh}})
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return fresh


def with_prs(head, events, prs, fresh=None, upstream=(), feedback=(), learning=(), recaps=()):
    """把账本里的事件（上游 issue、PR 反馈、issue 留言、学习文章、回顾）接在第一行前面；fresh 给了就只写这一轮第一次报的。"""
    parts = []
    for name, group in (("上游 issue", upstream), ("PR 反馈", prs), ("issue 留言", feedback), ("学习文章", learning), ("回顾", recaps)):
        shown = group if fresh is None else [e for e in group if e["id"] in fresh]
        if shown:
            parts.append(f"派出 {len(shown)} 条 {name}：" + "、".join(e["subject"] for e in shown))
    if parts:
        head = "；".join(parts) + "；" + (head[len(NOTHING):] if head.startswith(NOTHING) else head)
    shown = [{k: v for k, v in e.items() if k != GUARD} for e in events + list(upstream) + prs + list(feedback) + list(learning) + list(recaps)]
    return head + "\n\n" + fenced({"events": shown})


def issue_round(*, known=None, read=search, when_labeled=labeled_at, fetch=view_one):
    """带 agent 标签的 issue 这一轮派哪些：返回 (第一行结论, 事件)。"""
    known = projects() if known is None else known
    issues = read(sorted(known))
    handoffs = load_handoffs()
    issues += recent_handoffs(issues, known, handoffs, fetch)
    base = folder() / "baseline.json"
    if not base.exists():
        ids = sorted(f"{i['repository']['nameWithOwner']}#{i['number']}" for i in issues)
        write_json(base, {"recorded_at": now().isoformat(timespec="seconds"), "issues": ids})
        # 显式交给的照派：工作台点「交给 Agent」触发的正好是第一次运行时，不能把它也记成不派
        handed = [i for i in issues if f"{i['repository']['nameWithOwner']}#{i['number']}" in handoffs]
        events, _ = collect(handed, known, load_baseline(base), handoffs, when_labeled)
        head = f"第一次运行：记下已有的 {len(ids)} 条带 {LABEL} 标签的 issue，不派"
        if events:
            head += "；显式交给的照派：" + "、".join(e["subject"] for e in events)
        return head, events
    baseline = load_baseline(base)
    saved = load_held(baseline[0])
    current = {f"{i['repository']['nameWithOwner']}#{i['number']}" for i in issues}
    # 掉出搜索结果的（标签被摘掉）删掉结论；不在这轮监看范围的仓库（例如归档了）结论留着，取消归档后照用
    verdicts = {k: v for k, v in saved.items() if k in current or k.rpartition("#")[0] not in known}
    events, held = collect(issues, known, baseline, handoffs, when_labeled, verdicts)
    if verdicts != saved:
        save_held(baseline[0], verdicts)
    head = (f"派出 {len(events)} 条 issue：" + "、".join(e["subject"] for e in events)) if events else NOTHING + IDLE
    if held:
        head += f"；启用前已带标签、没有重新交给的 {len(held)} 条不派"
    return head, events


def add_label(repo, number):
    edit = ["gh", "issue", "edit", str(int(number)), "--repo", repo, "--add-label", LABEL]
    try:
        github.run(edit, "rest:label")
    except github.RateLimited:
        raise
    except RuntimeError as exc:
        if "not found" not in str(exc).lower():
            raise
        github.run(["gh", "label", "create", LABEL, "--repo", repo, "--color", LABEL_COLOR,
                    "--description", "交给 agent-autoloop 处理"], "rest:label")
        github.run(edit, "rest:label")


def emit_check(key, handed_at, title="", link=""):
    """马上触发一次监看：发 local 来源的 issues.check，规则「立即看 agent issue」接它。"""
    args = ["agent-autoloop", "emit", CHECK, "--id", "handoff:" + event_id(key, handed_at), "--subject", key]
    if title:
        args += ["--summary", title[:200]]
    if link.startswith("https://github.com/"):
        args += ["--link", link]
    return run(args, timeout=30).strip()


def handoff(repo, number, title="", link="", *, at=None, label=add_label, trigger=emit_check, force=False):
    """显式交给 Agent：记下交给时间，打 agent 标签，再马上触发一次监看。
    force 是详情里的「仍交给 Agent」：记成 {handed_at, force: true}，监看不按「上一轮已有结论」拦。"""
    key = f"{repo}#{int(number)}"
    at = at or now()
    stamp = at.isoformat(timespec="seconds")
    mark = {"handed_at": stamp, "force": True} if force else stamp
    with handoff_ledger() as ledger:  # 先记账再打标签：定时监看看到标签时一定已经知道是显式交给
        previous = ledger.get(key)
        ledger[key] = mark
    try:
        label(repo, number)
    except Exception:
        with handoff_ledger() as ledger:  # 只撤回自己这次记的；别人已经又交了一次就不动
            if ledger.get(key) == mark:
                if previous is None:
                    ledger.pop(key, None)
                else:
                    ledger[key] = previous
        raise
    try:
        routed, error = trigger(key, at, title, link), ""
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        routed, error = "", f"已打标签，但没能马上触发中台（{exc}），工作台的监看闸门 10 分钟内会再触发一次"
    return {"subject": key, "handed_at": stamp, "routed": routed, "error": error}


def pointer():
    return folder() / "paihuo-root"


def ensure_pointer(root=ROOT):
    """工作台启动时调用：paihuo-root 缺失或指向不存在的目录（例如搬盘后）才改成当前仓库。
    之后按 paihuo-root 认定的仓库修一下技能链接（见 heal_skill）。返回 paihuo-root 是否改了。"""
    path = pointer()
    try:
        current = Path(path.read_text(encoding="utf-8").strip())
    except OSError:
        current = None
    if current and (current / "personal/agent_watch.py").exists():
        heal_skill(current)
        return False
    if not path.parent.exists():
        heal_skill(root)
        return False
    path.write_text(str(root) + "\n", encoding="utf-8")
    heal_skill(root)
    return True


def heal_skill(root):
    """~/.claude/skills/<技能> 已经是符号链接、却悬空或指向别的仓库时，改链到 root 里的技能（SKILLS）。
    真目录、没装过的都不动（那要运行 install）；root 里没有这份技能也不动。返回是否改了。"""
    changed = False
    for name in SKILLS:
        target = skills_home() / name
        source = Path(root) / "personal" / "skills" / name
        if not target.is_symlink() or not (source / "SKILL.md").exists():
            continue
        try:
            changed = link_skill(source, target, skills_home().parent / "skill-backups") or changed
        except OSError:
            pass
    return changed


def backup_path(target, folder=None):
    """备份文件名：<名字>.bak-<时间>，同一秒里已有就加 -2、-3……，不会盖掉前一份备份。"""
    target = Path(target)
    base = Path(folder or target.parent) / f"{target.name}.bak-{now().strftime('%Y%m%d%H%M%S')}"
    found, n = base, 2
    while found.exists():
        found, n = base.with_name(f"{base.name}-{n}"), n + 1
    return found


def place(source, target):
    """把仓库里的文件装到中台目录；原文件内容不同就先备份。返回是否有改动。"""
    content = source.read_text(encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_text(encoding="utf-8") == content:
            return False
        shutil.copy2(target, backup_path(target))
    target.write_text(content, encoding="utf-8")
    return True


def skills_home():
    return Path.home() / ".claude" / "skills"


def link_skill(source, target, backups):
    """把 target 装成指向仓库里 source 目录的符号链接，仓库是唯一来源。原来是真目录或文件就整个挪进 backups 备份，
    不删，也不留在技能目录里（留在那会被当成第二个同名技能）。返回是否有改动。"""
    source = Path(source).resolve()
    if target.is_symlink():
        if target.exists() and target.resolve() == source:
            return False
        target.unlink()
    elif target.exists():
        backups.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup_path(target, backups)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
    return True


def add_types(holder, key, types):
    """holder[key]["types"] 补上缺的事件类型，返回补了哪些。"""
    block = holder.setdefault(key, {})
    have = block.setdefault("types", [])
    missing = [t for t in types if t not in have]
    have.extend(missing)
    return missing


def rules_problem(data):
    """rules.json 的结构和这里要改的地方对不上时，返回哪里不对；对得上返回空串。只查要动的几处，完整校验由中台做。"""
    if not isinstance(data, dict):
        return "顶层不是对象"
    rules, agents, sources = data.get("rules"), data.get("agents"), data.get("sources")
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        return "rules 不是对象的列表"
    if not isinstance(agents, dict) or not all(isinstance(a, dict) for a in agents.values()):
        return "agents 不是对象"
    if not isinstance(sources, dict) or "github" not in sources:
        return "sources 里没有 github 来源"
    named = {r.get("name"): r for r in rules}
    handle = named.get(HANDLE_RULE)
    if not handle:
        return f"没有「{HANDLE_RULE}」规则"
    when = handle.get("when")
    if not isinstance(when, dict) or when.get("source") != "github" or not isinstance(when.get("types"), list) \
            or not all(isinstance(t, str) for t in when["types"]):
        return f"「{HANDLE_RULE}」的 when 不是 github 来源的事件"
    if handle.get("to") not in agents:
        return f"「{HANDLE_RULE}」的 Agent 没登记"
    for name in WATCH_RULES:
        rule = named.get(name)
        if not rule:
            return f"没有「{name}」规则"
        if rule.get("to") not in agents:
            return f"「{name}」的 Agent {rule.get('to')} 没登记"
        emits = rule.get("emits")
        if emits is not None and (not isinstance(emits, dict) or not isinstance(emits.get("types", []), list)):
            return f"「{name}」的 emits 不是对象"
    review = named.get(REVIEW_RULE)
    if REVIEW_RULE in named and not isinstance(review.get("when"), dict):
        return f"已有的「{REVIEW_RULE}」规则没有 when"
    return ""


def feedback_rule(name, kind, skill, template, to=None):
    """「处理 PR 反馈」「处理 issue 留言」规则：目录和来源照抄「处理 issue」，Agent 是 to（缺省照抄），
    按对象并行 FEEDBACK_PARALLEL：同一条 PR 或 issue 一次一轮，不同的同时跑。"""
    return {"name": name, "when": {"source": (template.get("when") or {}).get("source", "github"), "types": [kind]},
            "to": to or template["to"], "cwd": template.get("cwd", "/Volumes/Luochen"), "skill": skill, "parallel": FEEDBACK_PARALLEL}


def pr_rule(template, to=None):
    return feedback_rule(PR_RULE, PR_EVENT, PR_SKILL, template, to)


def issue_feedback_rule(template, to=None):
    return feedback_rule(ISSUE_FEEDBACK_RULE, ISSUE_FEEDBACK_EVENT, ISSUE_FEEDBACK_SKILL, template, to)


def group_of(rule):
    """中台里规则所在的组：没写 group 就是规则名；同一组的规则 parallel 要一致。"""
    return rule.get("group") or rule.get("name")


def feedback_agent(data, template, changed):
    """两条反馈规则用的 Agent：agents 里没有 FEEDBACK_AGENT 就照「处理 issue」的 Agent 抄一份、模型写 FEEDBACK_MODEL；
    已经有的不动（改过的模型照留）。agents 读不懂或模板的 Agent 没登记就不加，返回模板的 Agent。"""
    agents = data.get("agents")
    base = agents.get(template["to"]) if isinstance(agents, dict) else None
    if not isinstance(base, dict):
        return template["to"]
    if not isinstance(agents.get(FEEDBACK_AGENT), dict):
        agents[FEEDBACK_AGENT] = {**base, **({"name": f"{base['name']}（反馈）"} if base.get("name") else {}), "model": FEEDBACK_MODEL}
        changed.append(f"加了 Agent {FEEDBACK_AGENT}（模型 {FEEDBACK_MODEL}）")
    return FEEDBACK_AGENT


def pr_changes(data):
    """在读进来的 rules.json 里接上 PR 反馈和 issue 留言：照「处理 issue」加「处理 PR 反馈」和「处理 issue 留言」，
    两条监看规则的 emits 加上 prs.feedback 和 issues.feedback（缺 issues.opened 也补上，交给的上游 issue 用它）。
    这两条规则交给 FEEDBACK_AGENT（见 feedback_agent；原来和「处理 issue」同一个 Agent 的才改，另外指定过的不动），
    parallel 低于 FEEDBACK_PARALLEL 的调到它（和别的规则同组的不动，同组的 parallel 要一致）。
    没有「处理 issue」可照抄就什么都不改（只让监看发事件、没人接，事件会被中台记成无人处理）。
    别的已经有的不改。返回 (改了哪些, 说明)。"""
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return [], "；rules.json 里没有 rules，PR 反馈规则没加"
    rules_ = [r for r in rules if isinstance(r, dict)]
    changed, note = [], ""
    template = next((r for r in rules_ if r.get("name") == HANDLE_RULE and r.get("to")), None)
    if template is None and not all(any(r.get("name") == name for r in rules_) for name in FEEDBACK_RULES):
        return [], f"；没有「{HANDLE_RULE}」规则可照抄，「{PR_RULE}」「{ISSUE_FEEDBACK_RULE}」和 {PR_EVENT}、{ISSUE_FEEDBACK_EVENT} 都没加"
    agent = feedback_agent(data, template, changed) if template else None
    after = template
    for name, make in ((PR_RULE, pr_rule), (ISSUE_FEEDBACK_RULE, issue_feedback_rule)):
        rule = next((r for r in rules_ if r.get("name") == name), None)
        if rule is None:
            rule = make(template, agent)
            rules.insert(rules.index(after) + 1, rule)
            rules_.append(rule)
            changed.append(f"新增规则「{name}」")
        else:
            if template and agent == FEEDBACK_AGENT and rule.get("to") == template["to"]:
                rule["to"] = agent
                changed.append(f"「{name}」改用 Agent {agent}")
            current = rule.get("parallel")
            current = current if isinstance(current, int) and not isinstance(current, bool) else 0
            if current < FEEDBACK_PARALLEL:
                if any(r is not rule and group_of(r) == group_of(rule) for r in rules_):
                    note += f"；「{name}」和别的规则同组，parallel 没改，要手动改成一致的 {FEEDBACK_PARALLEL}"
                else:
                    rule["parallel"] = FEEDBACK_PARALLEL
                    changed.append(f"「{name}」并行 {current or '未写'} → {FEEDBACK_PARALLEL}")
        after = rule
    handler = next((r for r in rules_ if r.get("name") == HANDLE_RULE), {})
    for rule in rules_:
        if rule.get("name") not in WATCH_RULES:
            continue
        if not isinstance(rule.get("emits"), dict):  # 没有 emits：照「处理 issue」接的来源补一个，免得交给时一直提示 install
            rule["emits"] = {"source": (handler.get("when") or {}).get("source", "github"), "types": []}
        emits = rule["emits"]
        for kind in (OPENED, PR_EVENT, ISSUE_FEEDBACK_EVENT):  # 上游 issue 也是 issues.opened，由「处理 issue」接
            if kind not in (emits.get("types") or []):
                emits["types"] = list(emits.get("types") or []) + [kind]
                changed.append(f"「{rule['name']}」可发 {kind}")
    return changed, note


def worker_capacity(path=None):
    """本机接单程序同时能跑几轮：config.json 的 worker.capacity，没写是 WORKER_CAPACITY；本机没有接单程序或读不到是 None。"""
    config = read_json(path or home() / "config.json", None)
    worker = config.get("worker") if isinstance(config, dict) else None
    if not isinstance(worker, dict):
        return None
    capacity = worker.get("capacity")
    return capacity if isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0 else WORKER_CAPACITY


def capacity_note():
    """接单程序容量低于 FEEDBACK_PARALLEL 时的一句提示；install 不改 config.json（里面是密钥）。"""
    capacity = worker_capacity()
    if capacity is None or capacity >= FEEDBACK_PARALLEL:
        return ""
    return (f"；接单程序容量是 {capacity}（{home() / 'config.json'} 的 worker.capacity，没写按 {WORKER_CAPACITY}），全部规则合起来最多同时跑 {capacity} 轮："
            f"要到 {FEEDBACK_PARALLEL} 个，把 worker.capacity 改成不小于 {FEEDBACK_PARALLEL} 的数，再运行 launchctl kickstart -k gui/$(id -u)/agent-autoloop")


def review_changes(data, path, problem):
    """在读进来的 rules.json 里补上审查和返工要的配置，已有的不动（改过的模型、目录照留）：
    agents 里的 claude-review（审查用的 Agent，模型改它的 model）、「审查 PR」规则、「处理 issue」接 issues.rework、
    两条监看规则能发 pr.review 和 issues.rework。problem 是改动前查出的结构问题（rules_problem），有就一处不改、提示手动加。
    返回 (改了哪些, 说明)。"""
    if problem:
        return [], f"；{path} 的结构和预期不同（{problem}），审查规则没有加，要手动加（见 RUN.md）"
    rules, agents = data["rules"], data["agents"]
    handle = next(r for r in rules if r.get("name") == HANDLE_RULE)
    changes = []
    if REVIEW_AGENT not in agents:
        agent = {"name": "Claude Code（审查 PR）", "use": "claude", "access": "full"}
        worker = agents[handle["to"]].get("worker")
        agents[REVIEW_AGENT] = {**({"worker": worker} if worker else {}), **agent}
        changes.append(f"加了 Agent {REVIEW_AGENT}")
    if add_types(handle, "when", [REWORK]):
        changes.append(f"「{HANDLE_RULE}」接 {REWORK}")
    for rule in rules:
        if rule.get("name") in WATCH_RULES and add_types(rule, "emits", [OPENED, REVIEW, REWORK]):
            rule["emits"].setdefault("source", "github")
            changes.append(f"「{rule['name']}」能发审查和返工")
    if not any(r.get("name") == REVIEW_RULE for r in rules):
        rules.append({"name": REVIEW_RULE, "when": {"source": "github", "types": [REVIEW]}, "to": REVIEW_AGENT,
                      "cwd": handle.get("cwd") or "/Volumes/Luochen", "skill": REVIEW_SKILL, "parallel": 1})
        changes.append(f"加了规则「{REVIEW_RULE}」")
    return changes, ""


def learning_changes(data):
    """接上「写下一篇」：照「处理 issue」加规则「写学习文章」（Agent 用 FEEDBACK_AGENT，没有就照「处理 issue」的；
    目录是学习根目录，技能 dbs-learning，一次一轮），两条监看规则能发 learning.next。已经有的不改。返回 (改了哪些, 说明)。"""
    from . import learning
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return [], "；rules.json 里没有 rules，「写学习文章」没加"
    rules_ = [r for r in rules if isinstance(r, dict)]
    handler = next((r for r in rules_ if r.get("name") == HANDLE_RULE and r.get("to")), None)
    changed = []
    if not any(r.get("name") == LEARNING_RULE for r in rules_):
        if handler is None:
            return [], f"；没有「{HANDLE_RULE}」规则可照抄，「{LEARNING_RULE}」没加"
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        rules.append({"name": LEARNING_RULE,
                      "when": {"source": (handler.get("when") or {}).get("source", "github"), "types": [LEARNING_EVENT]},
                      "to": FEEDBACK_AGENT if isinstance(agents.get(FEEDBACK_AGENT), dict) else handler["to"],
                      "cwd": str(learning.root()), "skill": LEARNING_SKILL, "parallel": 1})
        changed.append(f"新增规则「{LEARNING_RULE}」")
    for rule in rules_:
        emits = rule.get("emits")
        if rule.get("name") in WATCH_RULES and isinstance(emits, dict) and LEARNING_EVENT not in (emits.get("types") or []):
            emits["types"] = list(emits.get("types") or []) + [LEARNING_EVENT]
            changed.append(f"「{rule['name']}」可发 {LEARNING_EVENT}")
    return changed, ""


def recap_changes(data):
    """接上「让 Agent 写总结和三问」：照定时规则「周回顾」（没有就「月回顾」）的 Agent 和目录加规则「写回顾」
    （技能 assistant-review，一次一轮），两条监看规则能发 review.next。已经有的不改，别的规则不动。返回 (改了哪些, 说明)。"""
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return [], "；rules.json 里没有 rules，「写回顾」没加"
    rules_ = [r for r in rules if isinstance(r, dict)]
    changed = []
    if not any(r.get("name") == RECAP_RULE for r in rules_):
        like = next((r for name in RECAP_LIKE for r in rules_ if r.get("name") == name and r.get("to") and r.get("cwd")), None)
        watcher = next((r for r in rules_ if r.get("name") in WATCH_RULES and isinstance(r.get("emits"), dict)), None)
        if like is None or watcher is None:  # 没配回顾的中台不提这件事：回顾标签点交给时会说缺规则
            return [], ""
        rules.append({"name": RECAP_RULE, "when": {"source": watcher["emits"].get("source", "github"), "types": [RECAP_EVENT]},
                      "to": like["to"], "cwd": like["cwd"], "skill": like.get("skill") or RECAP_SKILL, "parallel": 1})
        changed.append(f"新增规则「{RECAP_RULE}」")
    for rule in rules_:
        emits = rule.get("emits")
        if rule.get("name") in WATCH_RULES and isinstance(emits, dict) and RECAP_EVENT not in (emits.get("types") or []):
            emits["types"] = list(emits.get("types") or []) + [RECAP_EVENT]
            changed.append(f"「{rule['name']}」可发 {RECAP_EVENT}")
    return changed, ""


def command_worker(agents):
    """新加 command Agent 用的 worker：照已有的 command Agent，没有就照任一个带 worker 的。"""
    known = [a for a in agents.values() if isinstance(a, dict) and a.get("worker")]
    like = next((a for a in known if a.get("use") == "command"), known[0] if known else {})
    return {"worker": like["worker"]} if like.get("worker") else {}


def recording_changes(data):
    """接上「读新需求」的录音来源：加 command 型 Agent recording-issues（运行助理目录的 scripts/recording_issues.py --no-refresh）
    和规则「录音拟 issue」（本机来源的 recordings.issues.scan，一次一轮，不通知）。已经有的不改。返回 (改了哪些, 说明)。"""
    from . import assistant
    rules, agents = (data.get("rules"), data.get("agents")) if isinstance(data, dict) else (None, None)
    if not isinstance(rules, list) or not isinstance(agents, dict):
        return [], f"；rules.json 里没有 rules 或 agents，「{RECORDING_RULE}」没加"
    changed = []
    if RECORDING_AGENT not in agents:
        agents[RECORDING_AGENT] = {"name": RECORDING_RULE, **command_worker(agents), "use": "command",
                                   "command": ["python3", str(assistant.root() / RECORDING_SCRIPT), "--no-refresh"]}
        changed.append(f"加了 Agent {RECORDING_AGENT}")
    if not any(isinstance(r, dict) and r.get("name") == RECORDING_RULE for r in rules):
        rules.append({"name": RECORDING_RULE, "when": {"source": "local", "types": [RECORDING_EVENT]}, "to": RECORDING_AGENT,
                      "cwd": str(assistant.root()), "tell": "never", "parallel": 1})
        changed.append(f"新增规则「{RECORDING_RULE}」")
    return changed, ""


def source_rule_missing(rule, event, path=None):
    """中台还接不住这个来源的事件时返回原因；接得住返回空字符串。"""
    try:
        data = json.loads(Path(path or home() / "rules.json").read_text(encoding="utf-8"))
        rules = [r for r in data.get("rules", []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return "读不到中台的 rules.json，先运行 python3 -m personal.agent_watch install"
    if not any(r.get("name") == rule and event in ((r.get("when") or {}).get("types") or []) for r in rules):
        return f"中台还没有「{rule}」规则，先运行 python3 -m personal.agent_watch install"
    return ""


def ensure_rules(path=None, *, workbench_data=None):
    """把 PR 反馈和 issue 留言（pr_changes）、审查和返工（review_changes）接进中台的 rules.json，已经有的不改
    （两条反馈规则的 Agent 和 parallel 除外，见 pr_changes）。
    两部分各自判断能不能改，一部分改不了不挡另一部分；有改动先把原文件备份成 rules.json.bak-<时间>，只备份、只写一次。
    返回一句说明（没改动、也没有要提示的就是空字符串）。"""
    path = Path(path or home() / "rules.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return f"；没找到 rules.json，PR 反馈规则没加；没读到 {path}，审查规则要手动加（见 RUN.md）"
    except ValueError:
        return f"；rules.json 读不懂，PR 反馈规则没加；没读到 {path}，审查规则要手动加（见 RUN.md）"
    problem = rules_problem(data)  # 先按原文件查：PR 反馈那部分会把写坏的 emits 补成对象
    changed, note = pr_changes(data)
    more, extra = review_changes(data, path, problem)
    changed, note = changed + more, note + extra
    more, extra = learning_changes(data)
    changed, note = changed + more, note + extra
    more, extra = recap_changes(data)
    changed, note = changed + more, note + extra
    more, extra = recording_changes(data)
    changed, note = changed + more, note + extra
    from . import plugins
    if workbench_data is None:
        from .__main__ import DATA as workbench_data
    for plugin in plugins.KNOWN:  # 已装插件的规则（微信拟 issue）：没有就加，旧命令换成插件里的
        if plugins.installed(workbench_data, plugin):
            more, extra = plugins.autoloop_add(data, plugin)
            changed, note = changed + more, note + extra
    if not changed:
        return note
    backup = backup_path(path)
    shutil.copy2(path, backup)
    write_json(path, data)
    return f"{note}；rules.json：{'、'.join(changed)}（原文件备份在 {backup.name}）"


def rule_missing(name, kind, path=None):
    """中台还接不住 kind 事件时返回原因（交给前先查，免得事件被记成无人处理）；接得住返回空字符串。"""
    hint = "，先运行 python3 -m personal.agent_watch install"
    try:
        data = json.loads(Path(path or home() / "rules.json").read_text(encoding="utf-8"))
        rules = [r for r in data.get("rules", []) if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return "读不到中台的 rules.json" + hint
    handler = next((r for r in rules if r.get("name") == name), None)
    if not handler or kind not in ((handler.get("when") or {}).get("types") or []):
        # 「处理 issue」是照 RUN.md 手工配的，install 不新建它；「处理 PR 反馈」由 install 照它加
        return f"中台还没有「{name}」规则" + ("，照 RUN.md「交给 Agent」一节配好" if name == HANDLE_RULE else hint)
    watchers = [r for r in rules if r.get("name") in WATCH_RULES]
    if not watchers:  # install 不新建监看规则，提示它只会兜圈子
        return f"中台还没有监看规则（{'、'.join(WATCH_RULES)}），照 RUN.md「交给 Agent」一节配好"
    if not any(kind in (((r.get("emits") if isinstance(r.get("emits"), dict) else {}) or {}).get("types") or []) for r in watchers):
        return f"监看规则还不能发 {kind}" + hint
    return ""


def pr_rule_missing(path=None):
    return rule_missing(PR_RULE, PR_EVENT, path)


def issue_feedback_rule_missing(path=None):
    return rule_missing(ISSUE_FEEDBACK_RULE, ISSUE_FEEDBACK_EVENT, path)


def learning_rule_missing(path=None):
    return rule_missing(LEARNING_RULE, LEARNING_EVENT, path)


def recap_rule_missing(path=None):
    return rule_missing(RECAP_RULE, RECAP_EVENT, path)


def issue_rule_missing(path=None):
    """上游 issue 走「处理 issue」规则：中台要有它，监看规则也要能发 issues.opened。"""
    return rule_missing(HANDLE_RULE, OPENED, path)


def install(root=ROOT, *, rules=True):
    """写 paihuo-root，装薄入口 watch.py、点了能打开工作台的桌面通道，把 ~/.claude/skills 下的几个技能（SKILLS）链到仓库，
    再把 PR 反馈、issue 留言、审查和返工的事件与规则接进中台的 rules.json（ensure_rules），容量不够并行时提示一句。"""
    root = Path(root)
    folder().mkdir(parents=True, exist_ok=True)
    pointer().write_text(str(root) + "\n", encoding="utf-8")
    place(ENTRY, folder() / "watch.py")
    channel = place(CHANNEL, home() / "plugins" / "desktop" / "channel.ts")
    note = "；桌面通道有更新，运行 launchctl kickstart -k gui/$(id -u)/agent-autoloop 重启中台后生效" if channel else ""
    for name in SKILLS:
        source = root / "personal" / "skills" / name
        if name != SKILL and not (source / "SKILL.md").exists():
            continue
        skill = skills_home() / name
        if link_skill(source, skill, skills_home().parent / "skill-backups"):
            note += f"；技能 {skill} 已链到仓库"
    if rules:
        note += ensure_rules() + capacity_note()
    return f"已指向 {root}，薄入口在 {folder() / 'watch.py'}{note}"


def rereview(target, *, trigger=None):
    """本人手动重派一次审查：target 是 owner/repo#PR号。中台按 id 永久去重，删派出记录没用；这里给这个 PR 的审查 id
    加一个新后缀（+<次数>），再马上触发一次监看，监看照常过 CI、「处理 issue」在跑等门槛后派出。已有结论（通过、要改）的提交不重审。"""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+#[1-9]\d*", target or ""):
        raise ValueError("要写成 owner/repo#PR号")
    with dispatch_ledger() as ledger:
        ledger["redo"][target] = ledger["redo"].get(target, 0) + 1
        count = ledger["redo"][target]
    try:
        (trigger or emit_check)(target, now(), f"重审 {target}")
        return f"{target} 的审查加了新后缀 +{count}，已触发一次监看"
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return f"{target} 的审查加了新后缀 +{count}；没能马上触发中台（{exc}），下一轮定时监看（每小时）会派"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["install"]:
        print(install())
        return 0
    if argv[:1] == ["rereview"]:
        try:
            print(rereview(argv[1] if len(argv) > 1 else ""))
        except ValueError as exc:
            print(f"用法：python3 -m personal.agent_watch rereview owner/repo#PR号（{exc}）")
            return 2
        return 0
    try:
        receipt = watch()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        record_result(f"{type(exc).__name__}: {exc}")
        prs, ups, notes, lessons, recaps = pr_events(), upstream_events(), issue_feedback_events(), learning_events(), recap_events()
        if prs or ups or notes or lessons or recaps:  # 账本里的上游 issue、PR 反馈、issue 留言和学习文章不读 GitHub：带标签的 issue 没读到也照派，这一轮仍记一次失败
            print(with_prs(f"issue 没读到 GitHub：{exc}", [], prs, first_reports(ups + prs + notes + lessons + recaps), ups, notes, lessons, recaps))
            return 0
        print(f"没读到 GitHub：{exc}")
        return 1
    record_result("")
    print(receipt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
