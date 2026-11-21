"""Agent 上一轮已有结论、之后没有新动静的条目不再派（personal/settled.py，用户 2026-11-19 定）：
结论的读法、新动静的口径、收件箱改判（待你定、Agent 看过）、交给接口和批量交给拒绝、「仍交给 Agent」（force）、
PR 行同样把关、监看不派重复事件（agent_watch.repeats），以及只回复那一轮说要改代码时的「改用处理并回复」。
夹具照运行记录里的真事编：一个客户项目的 #108 一天被派了 3 次，每轮都写「没有要改的代码，剩下要你定」。"""
import json
from datetime import datetime, timezone

import pytest

from personal import agent_watch, conversation, github, issues, next_step, pr_feedback, reply_drafts, settled, upstream

ME = "luochen211"
OWN = {"id": "app", "name": "工具", "group": "个人工具", "github": "luochen211/app"}
TEAM = {"id": "channel", "name": "通道", "group": "合作项目", "github": "acme/channel"}
UP = {"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}
PROJECTS = [OWN, TEAM, UP]
S = "luochen211/app#108"
INSTALLED = {"rules": [
    {"name": "立即看 agent issue", "to": "issue-watch", "emits": {"source": "github", "types": ["issues.opened", "prs.feedback", "issues.feedback"]}},
    {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]}, "to": "claude-issue"},
    {"name": "处理 issue 留言", "when": {"source": "github", "types": ["issues.feedback"]}, "to": "claude-issue"}]}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    monkeypatch.setattr(next_step, "now", lambda: datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc))
    github.reset()
    pr_feedback.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    (tmp_path / "hub/rules.json").write_text(json.dumps(INSTALLED, ensure_ascii=False))
    yield
    github.reset()
    pr_feedback.reset()


def said(login, at, text):
    return {"__typename": "IssueComment", "author": {"__typename": "User", "login": login}, "authorAssociation": "OWNER",
            "bodyText": text, "createdAt": at, "createdViaEmail": False, "url": f"https://github.com/c#{at[8:13]}"}


def list_row(*items, number=108, repo="luochen211/app", author=ME):
    node = {"number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}", "createdAt": "2026-11-01T00:00:00Z",
            "updatedAt": "2026-11-18T00:00:00Z", "bodyText": "数据采购规划", "author": {"__typename": "User", "login": author},
            "labels": {"nodes": []}, "comments": {"totalCount": len(items)}, "timelineItems": {"nodes": list(items)}}
    return upstream.item(node, repo)


# 「处理 issue」第 1969 轮：17:30 派出，没有要改的，留了一张通知卡
RUN = {"id": 1969, "rule": issues.HANDLE_RULE, "state": "done", "created_at": "2026-11-18T09:30:28Z", "claimed_at": "2026-11-18T09:30:30Z",
       "finished_at": "2026-11-18T09:31:03Z", "summary": "没有要改的代码，剩下的事都要人或外部去定。", "error": "", "result": {"kind": "no_change"}}
NOTICE = {"id": 253, "kind": "notice", "title": "示例项目 #108：开发都做完、已上线", "text": "", "tier": "", "options": [], "recommend": None, "created_at": ""}
BEFORE = said(ME, "2026-11-18T06:00:00Z", "进展：六项开发都已合并。")


def fake(monkeypatch, rows, auto=None, feedback=None):
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: rows)
    monkeypatch.setattr(issues, "involved", lambda: {"open": [], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: (auto or {}, ""))
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: (
        {"runs": {}, "history": {}, "waiting": {}, "handed": {}, **(feedback or {})}, ""))


def issue_flow(monkeypatch, *comments, run=RUN, cards=(NOTICE,), handed="2026-11-18T17:30:08+08:00"):
    """#108 交给过「处理 issue」（handoffs.json 记着 17:30:08），第 1969 轮跑完。"""
    agent_watch.write_json(agent_watch.folder() / "handoffs.json", {S: handed})
    fake(monkeypatch, [list_row(*comments)], {"runs": {S: run}, "history": {S: [run]}, "waiting": {S: list(cards)}})


def row_of(subject=S):
    return next(r for r in issues.view(PROJECTS)["issues"] if r["subject"] == subject)


# ---- 结论和新动静 ----

def test_verdict_reads_decide_and_nothing_only_from_finished_rounds():
    done = lambda result, state="done": {**RUN, "state": state, "result": result}
    assert settled.verdict(done({"kind": "blocked", "reason": "等你定：收不收费"}))["kind"] == settled.DECIDE
    assert settled.verdict(done({"kind": "blocked", "reason": "x"}))["line"] == "x"
    ask = {"kind": "ask", "tier": "ask", "title": "【问你】这一版收不收费"}
    assert settled.verdict(done({"kind": "pr", "pr": 3}), [ask])["line"] == "这一版收不收费"  # 没答的【问你】先于结果
    assert settled.verdict(done({"kind": "no_change"}), [{**ask, "tier": "default"}])["kind"] == settled.NOTHING  # 【默认过】不算等你定
    for quiet in ({"kind": "no_change"}, {"kind": "no_action", "reason": "维护者说他们来改"}):
        assert settled.verdict(done(quiet))["kind"] == settled.NOTHING
    assert settled.verdict(done({"kind": "no_action", "reason": "维护者说他们来改"}))["line"] == "维护者说他们来改"
    assert settled.verdict(done({"kind": "unknown"}, state="nothing"))["kind"] == settled.NOTHING  # 中台记成没事可做
    for moved in ("pr", "pushed", "closed", "replied", "unknown"):
        assert settled.verdict(done({"kind": moved})) is None  # 动了手或结果不明：照原来的流程
    for state in ("claimed", "queued", "cancelled"):
        assert settled.verdict(done({"kind": "blocked", "reason": "x"}, state=state)) is None
    found = settled.verdict(done({"kind": "no_change"}), [NOTICE])
    assert found["since"] == RUN["claimed_at"] and found["line"] == RUN["summary"]


def test_new_activity_is_their_words_after_the_round_started_or_mine_after_it_ended():
    found = settled.verdict(RUN)
    assert not settled.fresh(found, their_at="2026-11-18T09:00:00Z")
    assert settled.fresh(found, their_at="2026-11-18T17:31:00+08:00")  # 两种时间写法都认
    assert not settled.fresh(found, mine_at="2026-11-18T09:30:50Z")  # 这一轮跑着时写的不算
    assert settled.fresh(found, mine_at="2026-11-18T09:40:00Z")
    assert settled.holding(found, their_at="") is found and settled.holding(None) is None


# ---- 收件箱改判和交给把关 ----

def test_nothing_to_do_without_news_moves_to_look_and_refuses_handoff(monkeypatch):
    issue_flow(monkeypatch, BEFORE)
    row = row_of()
    step = row["next"]
    assert (step["key"], step["name"], step["bucket"]) == ("seen", "Agent 看过", "look")  # 原来是 Agent 流程中（等你看），落待你回
    assert step["line"] == "Agent 看过，不用动：没有要改的代码，剩下的事都要人或外部去定。"
    assert step["button"] == "仍交给 Agent" and step["drawer_only"] and step["settled"]["run"] == 1969 and row["hint"] is None
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("不该打标签"))
    with pytest.raises(ValueError, match="第 1969 轮.*不用动.*仍交给 Agent"):
        issues.handoff(PROJECTS, "luochen211/app", 108)
    out = issues.batch_handoff(PROJECTS, [S])
    assert not out["results"][0]["ok"] and "待你看" in out["results"][0]["error"]


def test_force_from_the_drawer_still_hands_off_and_marks_the_ledger(monkeypatch):
    issue_flow(monkeypatch, BEFORE)
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: {"title": "数据采购规划", "url": f"https://github.com/{S}"})
    real = agent_watch.handoff
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: real(*a, label=lambda repo, number: None, trigger=lambda *x: "routed", **k))
    out = issues.handoff(PROJECTS, "luochen211/app", 108, force=True)
    value = agent_watch.load_handoffs()[S]
    assert value == {"handed_at": out["handed_at"], "force": True}  # 监看照它放行
    assert agent_watch.handed_time(value) == out["handed_at"] and agent_watch.forced(value)
    assert row_of()["stage"] == "已交给 Agent"  # handoffs.json 的新写法照样认得


def test_their_new_comment_or_my_answer_after_the_round_lifts_the_hold(monkeypatch):
    issue_flow(monkeypatch, BEFORE, said("boss", "2026-11-18T10:00:00Z", "@luochen211 收费，按 A 做"))
    assert row_of()["next"]["key"] == "flow" and row_of()["next"]["bucket"] == "reply"  # 照原来：等你看，能再交
    assert next_step.allowed(row_of()["next"]) == ("issue", "")
    issues_ = [list_row(BEFORE, said(ME, "2026-11-18T09:45:00Z", "定了：收费，按 A 做。"))]
    fake(monkeypatch, issues_, {"runs": {S: RUN}, "history": {S: [RUN]}, "waiting": {S: [NOTICE]}})
    assert not row_of()["next"].get("settled")


def test_a_round_handed_again_and_not_yet_run_is_not_settled(monkeypatch):
    issue_flow(monkeypatch, BEFORE, handed="2026-11-18T18:00:00+08:00")  # 第 1969 轮之后又交了一次
    assert row_of()["stage"] == "已交给 Agent" and not row_of()["next"].get("settled")


def test_blocked_feedback_round_is_decide_under_reply_and_batch_skips_it(monkeypatch):
    key = "acme/channel#1"
    blocked = {"id": 1915, "rule": conversation.RULE, "state": "done", "created_at": "2026-11-18T03:48:03Z", "claimed_at": None,
               "finished_at": "2026-11-18T03:48:28Z", "summary": "四件急事已回过", "error": "", "result": {"kind": "blocked", "reason": "等你定：比邻要不要删"}}
    theirs = said("boss", "2026-11-18T01:49:00Z", "@luochen211 四件急事")
    fake(monkeypatch, [list_row(theirs, number=1, repo="acme/channel", author="boss")],
         feedback={"runs": {key: blocked}, "history": {key: [blocked]},
                   "handed": {key: {"handed_at": "2026-11-18T11:47:00+08:00", "mode": "handle", "their_at": "2026-11-18T01:49:00Z"}}})
    row = row_of(key)
    assert (row["next"]["key"], row["next"]["name"], row["next"]["bucket"]) == ("decide", "待你定", "reply")
    assert row["next"]["line"] == "Agent 要你定：等你定：比邻要不要删" and row["next"]["action"] == "feedback" and row["hint"] is None
    seen = []
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **kw: seen.append((items, mode, kw.get("force"))) or {
        "handed": [{"subject": items[0], "kind": "issue", "handed_at": "2026-11-19T12:00:00+08:00"}], "skipped": [], "routed": "", "error": ""})
    out = issues.batch_handoff(PROJECTS, [key], mode="reply")
    assert not out["results"][0]["ok"] and "要你定" in out["results"][0]["error"] and seen == []
    with pytest.raises(ValueError, match="要你定"):
        issues.handoff(PROJECTS, "acme/channel", 1)
    issues.handoff(PROJECTS, "acme/channel", 1, force=True)
    assert seen == [([key], "handle", True)]


def test_without_the_thread_nothing_is_held_back(monkeypatch):
    agent_watch.write_json(agent_watch.folder() / "handoffs.json", {S: "2026-11-18T17:30:08+08:00"})
    bare = {**list_row(BEFORE), "thread": None}
    fake(monkeypatch, [bare], {"runs": {S: RUN}, "history": {S: [RUN]}, "waiting": {S: [NOTICE]}})
    assert not row_of()["next"].get("settled")  # 判断不了有没有新动静，不拦


# ---- PR 行 ----

def pr_node(*items, number=80, repo="up/x"):
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/{repo}/pull/{number}", "state": "OPEN", "isDraft": False,
            "reviewDecision": "", "updatedAt": "2026-11-19T00:00:00Z", "headRefName": "fix/x", "baseRefName": "main", "isCrossRepository": True,
            "headRepository": {"nameWithOwner": f"{ME}/x"}, "repository": {"nameWithOwner": repo}, "author": {"login": ME},
            "timelineItems": {"nodes": list(items)}}


PR_THEIRS = said("maintainer", "2026-11-18T01:00:00Z", "Please rename this.")
PR_RUN = {"id": 2083, "rule": pr_feedback.RULE, "state": "done", "created_at": "2026-11-18T02:00:00Z", "claimed_at": "2026-11-18T02:00:05Z",
          "finished_at": "2026-11-18T02:10:00Z", "summary": "非维护者要求改动", "error": "", "result": {"kind": "blocked", "reason": "要你决定改不改"}}


def test_pr_handoff_refuses_a_settled_pr_unless_forced_or_there_is_news():
    state = ({"runs": {"up/x#80": PR_RUN}, "waiting": {}}, "")
    read = lambda keys: {k: pr_node(PR_THEIRS) for k in keys}
    with pytest.raises(ValueError, match="第 2083 轮.*要你定"):
        pr_feedback.handoff(PROJECTS, ["up/x#80"], read=read, trigger=lambda *a: "", state=state)
    pr_feedback.handoff(PROJECTS, ["up/x#80"], read=read, trigger=lambda *a: "", state=state, force=True)
    entry = agent_watch.load_handoffs(agent_watch.pr_ledger_path())["up/x#80"]
    assert entry["force"] is True and entry["their_last"] == "2026-11-18T01:00:00Z" and "mine_at" in entry
    agent_watch.write_json(agent_watch.pr_ledger_path(), {})
    newer = lambda keys: {k: pr_node(PR_THEIRS, said("maintainer", "2026-11-18T05:00:00Z", "Any update?")) for k in keys}
    out = pr_feedback.handoff(PROJECTS, ["up/x#80"], read=newer, trigger=lambda *a: "", state=state)
    assert [h["subject"] for h in out["handed"]] == ["up/x#80"]
    assert "force" not in agent_watch.load_handoffs(agent_watch.pr_ledger_path())["up/x#80"]


def test_pr_rows_carry_the_settled_verdict_and_the_switch(monkeypatch):
    replied = {**PR_RUN, "id": 2084, "summary": "已写好回复草稿。这条要改代码，要动手请在工作台用「处理并回复」再交一次。", "result": {"kind": "replied"}}
    agent_watch.write_json(agent_watch.pr_ledger_path(), {
        "up/x#80": {"handed_at": "2026-11-18T09:59:00+08:00", "title": "PR 80", "url": "", "their_at": "", "mode": "handle", "detail": ""},
        "up/x#84": {"handed_at": "2026-11-18T09:59:00+08:00", "title": "PR 84", "url": "", "their_at": "", "mode": "reply", "detail": ""}})
    rows = [{"type": "PullRequest", "repository": {"nameWithOwner": "up/x"}, "number": n, "title": f"PR {n}", "url": f"https://github.com/up/x/pull/{n}",
             "author": {"login": ME}, "updatedAt": "2026-11-19T00:00:00Z", "isDraft": False, "reviewDecision": "", "state": "OPEN"} for n in (80, 84)]
    monkeypatch.setattr(issues, "involved", lambda: {"open": rows, "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: (
        {"runs": {"up/x#80": PR_RUN, "up/x#84": replied}, "history": {}, "waiting": {}}, ""))
    read = lambda keys: {k: pr_node(PR_THEIRS, number=int(k.rsplit("#", 1)[1])) for k in keys}
    out = {r["subject"]: r for r in pr_feedback.view(PROJECTS, read=read, now=datetime(2026, 11, 19, tzinfo=timezone.utc))["prs"]}
    assert out["up/x#80"]["state"] == "reply" and out["up/x#80"]["settled"]["kind"] == "decide"
    assert out["up/x#84"]["settled"] is None and out["up/x#84"]["switch"]["run"] == 2084
    assert out["up/x#80"]["switch"] is None  # 处理并回复那一轮不出


# ---- 只回复那一轮说要改代码 ----

def test_reply_only_round_that_needs_code_offers_handle_even_while_waiting(monkeypatch):
    key = "acme/channel#6"
    replied = {"id": 2071, "rule": conversation.RULE, "state": "done", "created_at": "2026-11-19T01:58:52Z", "claimed_at": None,
               "finished_at": "2026-11-19T01:59:27Z", "error": "", "result": {"kind": "replied"},
               "summary": "someone-dev 回复「删」。这一轮我只写了回复草稿，没改代码。这条要改代码，要动手请在工作台用「处理并回复」再交一次。"}
    theirs = said("boss", "2026-11-19T01:50:00Z", "删")
    entry = {"handed_at": "2026-11-19T09:58:00+08:00", "mode": "reply", "their_at": "2026-11-19T01:50:00Z"}
    sent = said(ME, "2026-11-19T02:30:00Z", "@boss 收到，那两份测试要删，我来改。")  # 你确认发出了那份只回复的草稿
    fake(monkeypatch, [list_row(said(ME, "2026-11-18T00:00:00Z", "@boss 要删吗"), theirs, sent, number=6, repo="acme/channel")],
         feedback={"runs": {key: replied}, "history": {key: [replied]}, "handed": {key: entry}})
    row = row_of(key)
    assert row["next"]["key"] == "wait" and row["switch"]["run"] == 2071  # 已回复、在等对方；但这一轮说要改代码
    seen = []
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **kw: seen.append((items, mode)) or {
        "handed": [{"subject": items[0], "kind": "issue", "handed_at": "2026-11-19T12:00:00+08:00"}], "skipped": [], "routed": "", "error": ""})
    with pytest.raises(ValueError, match="等对方回"):
        issues.handoff(PROJECTS, "acme/channel", 6, mode="reply")  # 只回复照旧不收
    issues.handoff(PROJECTS, "acme/channel", 6, mode="handle")  # 「改用处理并回复」
    assert seen == [([key], "handle")]
    assert settled.needs_handle(replied, {**entry, "mode": "handle"}) is None  # 处理并回复那一轮不算
    assert settled.needs_handle({**replied, "summary": "已写回复草稿。"}, entry) is None


def test_switching_a_draft_regenerates_it_in_handle_mode(tmp_path, monkeypatch):
    reply_drafts.save({"subject": "acme/channel#6", "kind": "issue", "work": "",
                       "replies": [{"id": "r1", "to": "boss", "body": "要删的两份测试在 PR #34 里。"}]},
                      data=tmp_path, read=lambda args: "")
    agent_watch.write_json(agent_watch.issue_feedback_ledger_path(), {"acme/channel#6": {"handed_at": "2026-11-19T09:58:00+08:00", "mode": "reply"}})
    seen = []
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **kw: seen.append(mode) or {
        "handed": [{"subject": items[0]}], "routed": "", "error": ""})
    reply_drafts.regenerate(PROJECTS, "acme/channel#6", data=tmp_path, mode="handle")
    assert seen == ["handle"] and reply_drafts.pending(tmp_path) == set()


# ---- 监看不派重复的 ----

NOW = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
FB_KEY = "acme/channel#1"


def slim_run(rid, subject, rule, state="done", created="2026-11-18T03:48:03Z", finished="2026-11-18T03:48:28Z"):
    return {"id": rid, "rule": rule, "state": state, "created_at": created, "claimed_at": None, "finished_at": finished, "summary": "已回过",
            "error": "", "subjects": [subject], "rework": 0, "pr_link": "", "sha": "", "agent": "", "attempts": 1, "link": ""}


def worker_receipt(home, rid, text):
    folder = home / "worker/runs" / f"{rid}-1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "stdout-1.log").write_text(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text},
                                                    ensure_ascii=False) + "\n", encoding="utf-8")


def feedback_entry(**fields):
    return {"handed_at": "2026-11-19T11:00:00+08:00", "title": "四件急事", "url": "https://github.com/acme/channel/issues/1",
            "detail": "仓库：acme/channel", "mode": "handle", "their_at": "2026-11-18T01:49:00Z", "mine_at": "", **fields}


@pytest.fixture
def watching(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_watch, "agent_prs", lambda repos: {"viewer": ME, "open": [], "merged": []})
    monkeypatch.setattr(agent_watch, "busy_subjects", lambda read=None: set())
    monkeypatch.setattr(agent_watch, "now", lambda: NOW)
    worker_receipt(tmp_path / "hub", 1915, "四件急事已回过，比邻删不删要你定。\nRESULT: BLOCKED=等你定：比邻要不要删")
    return tmp_path / "hub"


def receipt_events(receipt):
    return json.loads(receipt.split("```agent-autoloop\n", 1)[1].rsplit("\n```", 1)[0])["events"]


def test_watch_does_not_send_a_repeat_after_a_settled_round(watching):
    agent_watch.write_json(agent_watch.issue_feedback_ledger_path(), {FB_KEY: feedback_entry()})
    asked = []

    def lookup(wanted):
        asked.append(wanted)
        return {(FB_KEY.lower(), agent_watch.GUARDED[agent_watch.ISSUE_FEEDBACK_EVENT]): slim_run(1915, FB_KEY, conversation.RULE)}, []
    receipt = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], learning=[], pulls={"viewer": ME, "open": [], "merged": []},
                                lookup=lookup)
    assert receipt_events(receipt) == [] and "不再派：acme/channel#1（第 1915 轮要你定）" in receipt.splitlines()[0]
    assert "_guard" not in receipt
    store = agent_watch.read_json(agent_watch.folder() / agent_watch.REPEATS, {})
    assert list(store) == ["issue-feedback:acme/channel#1@20261119T110000+0800"]
    again = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], learning=[], pulls={"viewer": ME, "open": [], "merged": []},
                              lookup=lambda wanted: pytest.fail("记过的不再读中台"))
    assert receipt_events(again) == [] and len(asked) == 1


@pytest.mark.parametrize("fields, run_fields", [
    ({"force": True}, {}),  # 详情里「仍交给 Agent」
    ({"their_at": "2026-11-18T05:00:00Z"}, {}),  # 这一轮之后对方又说了
    ({"mine_at": "2026-11-18T06:00:00Z"}, {}),  # 你在这一轮之后回答了
    ({}, {"created": "2026-11-19T03:01:00Z", "finished": "2026-11-19T03:05:00Z"}),  # 这一轮是这次交给之后建的
    ({}, {"state": "claimed", "finished": None}),  # 还在跑：中台会接上
])
def test_watch_sends_when_forced_or_there_is_news(watching, fields, run_fields):
    agent_watch.write_json(agent_watch.issue_feedback_ledger_path(), {FB_KEY: feedback_entry(**fields)})
    lookup = lambda wanted: ({(FB_KEY.lower(), agent_watch.GUARDED[agent_watch.ISSUE_FEEDBACK_EVENT]):
                              slim_run(1915, FB_KEY, conversation.RULE, **run_fields)}, [])
    receipt = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], learning=[], pulls={"viewer": ME, "open": [], "merged": []},
                                lookup=lookup)
    assert [e["subject"] for e in receipt_events(receipt)] == [FB_KEY]


def test_watch_checks_labelled_issues_against_the_comments_it_read(watching, monkeypatch):
    key = "o/r#108"
    agent_watch.write_json(agent_watch.folder() / "baseline.json", {"recorded_at": "2026-11-01T00:00:00+00:00", "issues": [key]})
    agent_watch.write_json(agent_watch.folder() / "handoffs.json", {key: "2026-11-19T11:00:00+08:00"})
    item = {"repository": {"nameWithOwner": "o/r"}, "number": 108, "title": "数据采购规划", "url": "https://github.com/o/r/issues/108", "body": "正文"}
    nothing = slim_run(1969, key, issues.HANDLE_RULE, state="nothing")
    lookup = lambda wanted: ({(key.lower(), agent_watch.GUARDED[agent_watch.OPENED]): nothing}, [])
    known = {"o/r": {"github": "o/r", "name": "演示", "repo_path": "/work/r"}}
    pulls = {"viewer": ME, "open": [], "merged": []}
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, number: [
        {"author": ME, "at": "2026-11-18", "time": "2026-11-18T03:00:00Z", "body": "进展"},
        {"author": "github-actions[bot]", "at": "2026-11-18", "time": "2026-11-18T05:00:00Z", "body": "stale"}])  # 机器人不算新动静
    receipt = agent_watch.watch(known=known, read=lambda repos: [item], prs=[], upstream=[], feedback=[], learning=[], pulls=pulls, lookup=lookup)
    assert receipt_events(receipt) == [] and "第 1969 轮说不用动" in receipt.splitlines()[0]

    agent_watch.write_json(agent_watch.folder() / "handoffs.json", {key: {"handed_at": "2026-11-19T11:30:00+08:00", "force": True}})
    receipt = agent_watch.watch(known=known, read=lambda repos: [item], prs=[], upstream=[], feedback=[], learning=[], pulls=pulls,
                                lookup=lambda wanted: pytest.fail("force 的不读中台"))
    assert [e["id"] for e in receipt_events(receipt)] == ["o/r#108@20261119T113000+0800"]  # 新写法的交给时间照样进事件 id

    agent_watch.write_json(agent_watch.folder() / "handoffs.json", {key: "2026-11-19T11:45:00+08:00"})
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, number: [
        {"author": "boss", "at": "2026-11-19", "time": "2026-11-19T01:00:00Z", "body": "收费，按 A 做"}])
    receipt = agent_watch.watch(known=known, read=lambda repos: [item], prs=[], upstream=[], feedback=[], learning=[], pulls=pulls, lookup=lookup)
    assert [e["subject"] for e in receipt_events(receipt)] == [key]


def test_watch_sends_anyway_when_the_hub_cannot_be_read(watching):
    agent_watch.write_json(agent_watch.issue_feedback_ledger_path(), {FB_KEY: feedback_entry()})

    def down(wanted):
        raise OSError("connection refused")
    receipt = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], learning=[], pulls={"viewer": ME, "open": [], "merged": []},
                                lookup=down)
    assert [e["subject"] for e in receipt_events(receipt)] == [FB_KEY] and "没核对重复" in receipt.splitlines()[0]


def test_latest_runs_pages_back_until_every_subject_is_found():
    def run(rid, subject, rule=conversation.RULE):
        return {"id": rid, "rule": rule, "state": "done", "createdAt": "2026-11-18T00:00:00Z", "finishedAt": "2026-11-18T00:01:00Z",
                "events": [{"subject": subject, "type": "issues.feedback"}]}
    pages = {"/v1/state": {"runs": [run(30, "o/r#2"), run(29, "o/r#9", rule="看带 agent 标签的 issue")], "hasMore": True, "nextBefore": 29,
                           "cards": [{"id": 1, "state": "open"}, {"id": 2, "state": "closed"}]},
             "/v1/state?before=29": {"runs": [run(12, "O/R#1"), run(11, "o/r#1")], "hasMore": True, "nextBefore": 11}}
    reads = []
    rules = agent_watch.GUARDED[agent_watch.ISSUE_FEEDBACK_EVENT]
    found, cards = agent_watch.latest_runs({("o/r#1", rules), ("o/r#2", rules)}, read=lambda path: reads.append(path) or pages[path])
    assert found[("o/r#1", rules)]["id"] == 12 and found[("o/r#2", rules)]["id"] == 30
    assert reads == ["/v1/state", "/v1/state?before=29"] and [c["id"] for c in cards] == [1]
