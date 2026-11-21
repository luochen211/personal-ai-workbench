"""监看闸门（personal/watch_gate.py）：一个请求读快照、监看接着用、只在监看没看过的变化或到点时触发中台。"""
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from personal import agent_watch, watch_gate

KNOWN = {"o/r": {"github": "o/r", "name": "演示", "repo_path": "/work/r", "review_prs": True}}
T0 = datetime(2026, 11, 19, 7, 0, tzinfo=timezone.utc)
SHA = "a" * 40


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(agent_watch.github, "run", offline)
    monkeypatch.setattr(agent_watch, "notify", lambda title, text: None)
    monkeypatch.setattr(agent_watch, "busy_subjects", lambda read=None: set())
    monkeypatch.setattr(agent_watch, "rules_missing", lambda path=None: "")
    monkeypatch.setattr(agent_watch, "repeats", lambda events, **kw: (set(), ""))
    agent_watch.github.reset()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    agent_watch.write_json(tmp_path / "hub/issues/baseline.json", {"recorded_at": "2026-11-01T00:00:00+00:00", "issues": []})
    return tmp_path / "hub/issues"


def issue_node(number, repo="o/r", comments=(), total=None):
    return {"number": number, "title": f"需求 {number}", "url": f"https://github.com/{repo}/issues/{number}", "body": "正文",
            "repository": {"nameWithOwner": repo},
            "comments": {"totalCount": len(comments) if total is None else total, "nodes": list(comments)}}


def pr_node(sha=SHA, state="SUCCESS", branch="agent/issue-7", committed="2026-11-19T06:00:00Z", repo="o/r"):
    return {"repository": {"nameWithOwner": repo}, "number": 34, "title": "做需求 7", "url": f"https://github.com/{repo}/pull/34",
            "isDraft": False, "isCrossRepository": False, "headRefName": branch, "headRefOid": sha, "baseRefName": "main",
            "author": {"login": "me"}, "commits": {"nodes": [{"commit": {"oid": sha, "committedDate": committed,
                                                                         "statusCheckRollup": {"state": state} if state else None}}]},
            "comments": {"nodes": []}}


class GitHub:
    """假的 GraphQL：记下每次请求，按当前设定的 issue 和 PR 回答。"""
    def __init__(self, issues=(), open_=(), merged=()):
        self.issues, self.open, self.merged, self.calls = list(issues), list(open_), list(merged), []

    def __call__(self, query, kind="graphql", **variables):
        self.calls.append((kind, sorted(variables)))
        data = {"viewer": {"login": "me"}}
        if "issues" in variables:
            data["issues"] = {"nodes": self.issues}
        if "open" in variables:
            data["open"], data["merged"] = {"nodes": self.open}, {"nodes": self.merged}
        return data


@pytest.fixture
def github(monkeypatch):
    fake = GitHub()
    monkeypatch.setattr(agent_watch.github, "graphql", fake)
    return fake


class Hub:
    def __init__(self):
        self.reasons = []

    def __call__(self, reason, at=None):
        self.reasons.append(reason)
        return "ok"


def tick(at, hub, **kw):
    return watch_gate.tick(known=KNOWN, at=at, trigger=hub, **kw)


def watch(at, monkeypatch):
    monkeypatch.setattr(agent_watch, "now", lambda: at)
    return agent_watch.watch(known=KNOWN)


def test_one_request_reads_issues_with_comments_and_the_owners_agent_prs(github):
    github.issues = [issue_node(1, comments=[{"author": {"login": "bob"}, "createdAt": "2026-11-18T01:00:00Z", "body": " 看看 "}]),
                     issue_node(2, total=150), issue_node(3, repo="x/y")]
    github.open = [pr_node(), pr_node(branch="feat/x"), pr_node(repo="x/y")]
    found = watch_gate.fetch(["o/r"], ["o/r"], at=T0)
    assert github.calls == [("graphql:agent-watch", ["issues", "merged", "open"])]
    assert [i["number"] for i in found["issues"]] == [1, 2]  # 别的仓库的不要
    assert found["issues"][0]["comments"] == [{"author": "bob", "at": "2026-11-18", "time": "2026-11-18T01:00:00Z", "body": "看看"}]
    assert "comments" not in found["issues"][1]  # 评论太多：监看照旧单独读全
    assert [(p["repo"], p["headRefName"]) for p in found["prs"]["open"]] == [("o/r", "agent/issue-7")]
    assert watch_gate.fetch([], []) == {} and len(github.calls) == 1


def test_search_strings_cover_the_watched_repos_and_the_ledger_window():
    assert watch_gate.issue_search(["a/b", "c/d"]) == "is:issue is:open label:agent repo:a/b repo:c/d"
    opened, merged = watch_gate.pr_searches(T0)
    assert "author:@me" in opened and "head:agent" in opened and merged.endswith("updated:>=2026-11-12")


def test_watcher_reuses_a_fresh_snapshot_and_refreshes_a_stale_one(github, monkeypatch):
    github.issues = [issue_node(1)]
    hub = Hub()
    tick(T0, hub)  # 闸门读一次
    assert len(github.calls) == 1
    monkeypatch.setattr(watch_gate, "now", lambda: T0 + timedelta(minutes=2))
    assert [i["number"] for i in agent_watch.search(sorted(KNOWN))] == [1] and len(github.calls) == 1
    agent_watch.agent_prs(["o/r"])
    assert len(github.calls) == 1
    agent_watch.search(["o/r", "o/s"])
    assert github.calls[-1] == ("graphql:agent-watch", ["issues"]) and len(github.calls) == 2  # 仓库不一样：自己读
    monkeypatch.setattr(watch_gate, "now", lambda: T0 + watch_gate.FRESH + timedelta(minutes=1))
    agent_watch.agent_prs(["o/r"])
    assert github.calls[-1] == ("graphql:agent-watch", ["merged", "open"])  # 过期：只读这一部分


def test_a_triggered_round_needs_no_github_requests_and_dispatches_the_new_issue(github, monkeypatch):
    hub = Hub()
    assert tick(T0, hub)["triggered"]  # 第一次：监看还没看过
    watch(T0 + timedelta(minutes=1), monkeypatch)
    assert not tick(T0 + timedelta(minutes=5), hub)["triggered"] and len(hub.reasons) == 1  # 没变化不触发
    github.issues = [issue_node(9, comments=[{"author": {"login": "bob"}, "createdAt": "2026-11-19T07:00:00Z", "body": "急"}])]
    result = tick(T0 + timedelta(minutes=10), hub)
    assert result["triggered"] and "带标签的 issue" in result["reasons"][0]
    before = len(github.calls)
    monkeypatch.setattr(agent_watch, "labeled_at", lambda repo, n: T0 + timedelta(minutes=8))
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, n: pytest.fail("评论已在快照里"))
    receipt = watch(T0 + timedelta(minutes=11), monkeypatch)
    assert len(github.calls) == before  # 快照还新：监看一个请求都不发
    events = json.loads(receipt.split("```agent-autoloop\n", 1)[1].rsplit("\n```", 1)[0])["events"]
    assert [e["subject"] for e in events] == ["o/r#9"] and "bob 2026-11-19：急" in events[0]["detail"]
    assert not tick(T0 + timedelta(minutes=15), hub)["triggered"]


def test_a_new_ci_result_or_review_triggers_but_an_unrelated_change_does_not(github, monkeypatch):
    hub = Hub()
    github.open = [pr_node(state="PENDING")]
    tick(T0, hub)
    watch(T0 + timedelta(minutes=1), monkeypatch)
    github.open[0]["title"] = "改了标题"
    assert not tick(T0 + timedelta(minutes=5), hub)["triggered"]
    github.open = [pr_node(state="SUCCESS")]
    assert tick(T0 + timedelta(minutes=10), hub)["triggered"] and "agent PR" in hub.reasons[-1]


def test_no_retrigger_while_the_hub_has_not_run_the_watcher_yet(github):
    hub = Hub()
    tick(T0, hub)
    github.issues = [issue_node(3)]
    assert not tick(T0 + timedelta(minutes=5), hub)["triggered"]  # 上一次触发的那轮还没跑完
    assert tick(T0 + watch_gate.RETRIGGER, hub)["triggered"] and len(hub.reasons) == 2  # 太久没跑：再触发一次


def test_a_state_the_watcher_did_not_record_triggers_once_per_retrigger_window(github, monkeypatch):
    hub = Hub()
    github.open = [pr_node()]
    tick(T0, hub)
    monkeypatch.setattr(agent_watch, "rules_missing", lambda path=None: "缺规则")  # 这一轮不读 PR，seen 里没有 PR 部分
    watch(T0 + timedelta(minutes=1), monkeypatch)
    assert "prs" not in watch_gate.load_state()["seen"]
    assert not tick(T0 + timedelta(minutes=5), hub)["triggered"] and not tick(T0 + timedelta(minutes=25), hub)["triggered"]
    assert tick(T0 + watch_gate.RETRIGGER, hub)["triggered"] and len(hub.reasons) == 2


def test_failed_trigger_is_retried_next_tick(github):
    def down(reason, at=None):
        raise RuntimeError("中台没开")
    assert tick(T0, down) == {"reasons": ["GitHub 有变化（带标签的 issue、agent PR）"], "triggered": False, "error": "中台没开"}
    hub = Hub()
    assert tick(T0 + timedelta(minutes=5), hub)["triggered"]


def test_watcher_asks_to_be_woken_when_a_commit_without_ci_has_settled(github, monkeypatch):
    hub = Hub()
    github.open = [pr_node(state=None, committed="2026-11-19T06:59:00Z")]
    tick(T0, hub)
    receipt = watch(T0 + timedelta(seconds=30), monkeypatch)
    assert "审查" not in receipt.splitlines()[0]
    state = watch_gate.load_state()
    assert state["wake_at"] == "2026-11-19T07:02:00+00:00"
    assert not tick(T0 + timedelta(minutes=1), hub)["triggered"]
    assert tick(T0 + timedelta(minutes=3), hub)["reasons"] == ["到了复查时间"]
    receipt = watch(T0 + timedelta(minutes=3, seconds=10), monkeypatch)
    assert receipt.startswith("派出 审查 o/r#7")
    assert watch_gate.load_state()["wake_at"] == ""


def test_review_pass_collects_wake_times_for_wait_busy_and_stuck():
    ledger = {"sent": {}, "told": {}, "holds": {}, "redo": {}}
    at = T0
    known = KNOWN
    wait = {"body": f"<!-- paihuo-review head={SHA} -->\nVERDICT: WAIT", "url": "u", "createdAt": "2026-11-19T06:30:00Z",
            "author": {"login": "me"}}
    found = {"viewer": "me", "open": [{**pr_node(), "repo": "o/r", "comments": {"nodes": [wait]}}], "merged": []}
    wakes = []
    agent_watch.review_pass(found, known, ledger, lambda: set(), at, wakes)
    assert wakes == [datetime(2026, 11, 19, 7, 30, tzinfo=timezone.utc)]  # WAIT 隔一小时重审
    wakes = []
    found["open"][0]["comments"] = {"nodes": []}
    agent_watch.review_pass(found, known, ledger, lambda: {"o/r#7"}, at, wakes)
    assert wakes == [at + agent_watch.RECHECK]  # 「处理 issue」在跑
    wakes = []
    events, _, _ = agent_watch.review_pass(found, known, ledger, lambda: set(), at, wakes)
    assert len(events) == 1 and wakes == []
    agent_watch.review_pass(found, known, ledger, lambda: set(), at + timedelta(minutes=5), wakes)
    assert wakes == [at + agent_watch.STUCK_AFTER]  # 派出的审查没结论：到点提醒


def test_unreported_handoff_triggers_once_after_the_grace_period(github, home, monkeypatch):
    hub = Hub()
    tick(T0, hub)
    watch(T0 + timedelta(minutes=1), monkeypatch)
    handed = (T0 + timedelta(minutes=2)).isoformat()
    agent_watch.write_json(home / "pr-handoffs.json", {"up/x#7": {"handed_at": handed, "detail": "d"}})
    assert not tick(T0 + timedelta(minutes=5), hub)["triggered"]  # 刚交出：交给时已经触发过
    assert tick(T0 + timedelta(minutes=8), hub)["reasons"] == ["交给记录还没报给中台"]
    assert not tick(T0 + timedelta(minutes=40), hub)["reasons"]  # 同一批只触发一次，监看没跑也不再催
    receipt = watch(T0 + timedelta(minutes=41), monkeypatch)
    assert "派出 1 条 PR 反馈：up/x#7" in receipt and watch_gate.unreported(T0 + timedelta(minutes=42)) == set()


def test_only_one_gate_runs_per_machine(home):
    ticks = []
    stop = threading.Event()
    first = threading.Thread(target=watch_gate.keep_watching, args=(stop,),
                             kwargs={"interval": 0.01, "delay": 0, "once": lambda: ticks.append(1) or {}})
    first.start()
    for _ in range(200):
        if ticks:
            break
        threading.Event().wait(0.01)
    other = []
    watch_gate.keep_watching(threading.Event(), delay=0, once=lambda: other.append(1) or {})  # 拿不到锁，马上退出
    stop.set()
    first.join(2)
    assert ticks and not other


def test_gate_can_be_turned_off(monkeypatch):
    monkeypatch.setenv(watch_gate.SKIP_ENV, "0")
    assert watch_gate.start() == (None, None)
