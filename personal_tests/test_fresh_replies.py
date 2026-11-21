"""别人在你之后说了话就归待你回（用户 2026-11-20 定）：先于 PR 评审中、待关闭、等对方、已处理完的 Agent 流程；
未读的 GitHub 通知比收件箱数据新时单独重读那一条（personal/fresh.py），重读完成前先凭通知归待你回；通知同步发现新信号就马上重读。
名字和文字都是编的。"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from personal import conversation, fresh, github, issues, next_step, notifications, pr_feedback, settled, upstream

ME = "luochen211"
OWN = {"id": "chan", "name": "通道", "github": "acme/channel"}
UP = {"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}
OLD = {"id": "old", "name": "归档的", "github": "up/old", "issue_scope": "mine", "archived": True}
PROJECTS = [OWN, UP, OLD]


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def refuse(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", refuse)
    github.reset()
    pr_feedback.reset()
    fresh.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    yield
    github.reset()
    pr_feedback.reset()
    fresh.reset()


def iso(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def said(login, at, text, typename="User", association="OWNER", email=False):
    return {"__typename": "IssueComment", "author": {"__typename": typename, "login": login}, "authorAssociation": association,
            "bodyText": text, "createdAt": at, "createdViaEmail": email, "url": f"https://github.com/c#{login}-{at[11:19]}"}


def ref(number, state="OPEN", closes=False):
    return {"__typename": "CrossReferencedEvent", "willCloseTarget": closes,
            "source": {"__typename": "PullRequest", "number": number, "title": f"PR {number}", "url": f"https://github.com/x/pull/{number}",
                       "state": state}}


def issue(*items, number=64, author=ME, repo="acme/channel", updated="2026-11-19T00:00:00Z"):
    node = {"number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}",
            "createdAt": "2026-11-10T00:00:00Z", "updatedAt": updated, "bodyText": "正文", "author": {"__typename": "User", "login": author},
            "labels": {"nodes": []}, "comments": {"totalCount": 0}, "timelineItems": {"nodes": list(items)}}
    return upstream.item(node, repo)


def classify(*items, flow=None, notice=None, repo="acme/channel", author=ME, involved=False):
    row = issue(*items, repo=repo, author=author)
    return next_step.classify(repo=repo, author=author, thread=row["thread"], refs=row["refs"], flow=flow,
                              upstream=repo == "up/x", involved=involved, notice=notice)


MINE = said(ME, "2026-11-19T01:00:00Z", "@boss 改好了，回“1A”或“1B”就行")
THEIRS = said("boss", "2026-11-19T02:00:00Z", "@luochen211 选 1A。另外再看一下导出。")
FLOW_DONE = {"stage": "已处理", "label": "已处理", "kind": "issue", "at": None}


# ---- 规则：别人在你之后说了话 ----

def test_their_word_after_mine_beats_pr_review_close_wait_and_a_finished_flow():
    assert classify(MINE)["key"] == "wait"  # 你在问人：等对方
    assert classify(MINE, ref(80))["key"] == "pr"
    assert classify(MINE, ref(81, "MERGED", closes=True))["key"] == "close"
    for extra, flow, before in [((ref(80),), None, "PR 评审中"), ((ref(81, "MERGED", closes=True),), None, "待关闭"),
                                ((), None, ""), ((), FLOW_DONE, "Agent 流程中")]:
        out = classify(MINE, *extra, THEIRS, flow=flow)
        assert out["key"] == "reply" and out["new"] == 1 and out["button"] == "帮我回复" and out["action"] == "feedback"
        assert out["line"] == f"boss {next_step.when(THEIRS['createdAt'])} @了你：@luochen211 选 1A。"
        assert (f"原来是「{before}」" in out["why"]) == bool(before)
        assert next_step.bucket(out) == "reply" and next_step.allowed(out) == ("feedback", "")
    assert classify(MINE, ref(80), THEIRS)["prs"][0]["number"] == 80  # 引用它的 PR 照样带着


def test_a_running_or_asking_agent_keeps_the_row_and_only_counts_the_new_words():
    for stage, bucket in [("处理中", "agent"), ("已交给 Agent", "agent"), ("审查中", "agent"), ("返工中", "agent"),
                          ("等你决定", "reply"), ("等你看", "reply")]:
        flow = {"stage": stage, "label": stage, "kind": "issue", "at": None}
        out = classify(MINE, THEIRS, said("boss", "2026-11-19T03:00:00Z", "还有一件"), flow=flow)
        assert out["key"] == "flow" and out["new"] == 2
        assert next_step.bucket(out, stage=stage) == bucket


def test_my_account_bots_and_mail_auto_replies_do_not_count():
    sent = said(ME, "2026-11-19T04:00:00Z", "@boss 好的，按 1A 做")  # Agent 写的草稿你确认后用你的账号发出：算你说的
    assert classify(MINE, THEIRS, sent)["key"] == "wait"
    bot = said("ci-helper[bot]", "2026-11-19T05:00:00Z", "@luochen211 stale", typename="Bot")
    assert classify(MINE, bot)["key"] == "look"  # 机器人最后说话：照原来的待你看
    mail = said("someone", "2026-11-19T05:00:00Z", "I am out of office", association="NONE", email=True)
    assert classify(MINE, mail)["key"] not in ("reply", "flow")


def test_the_line_says_who_when_and_the_first_sentence():
    plain = said("boss", "2026-11-19T02:00:00Z", "收到。下周再看")
    out = classify(MINE, plain)
    assert out["line"] == f"boss {next_step.when(plain['createdAt'])} 留言：收到。"
    assert out["quote"] == "收到。" and out["who"] == "boss" and out["url"] == plain["url"]


def test_upstream_words_count_when_it_is_yours_or_you_were_tagged():
    word = said("maint", "2026-11-19T02:00:00Z", "Thanks for the write-up.")
    assert classify(said(ME, "2026-11-19T01:00:00Z", "Opened"), word, ref(9), repo="up/x")["key"] == "reply"  # 你开的上游 issue
    assert classify(word, repo="up/x", author="other")["key"] == "look"  # 别人开的、你没说过话、没 @你
    tagged = said("maint", "2026-11-19T02:00:00Z", "@luochen211 could you check?")
    assert classify(tagged, repo="up/x", author="other")["key"] == "reply"


def test_a_notice_puts_a_stale_row_in_reply_until_the_reread_lands():
    notice = {"key": "acme/channel#64", "reason": "mention", "says": "@了你", "updated_at": "2026-11-19T09:00:00Z",
              "who": "boss", "at": "2026-11-19T08:59:50Z", "text": "看一下这个"}
    out = classify(MINE, notice=notice)  # 数据里你最后说话，通知更新：先归待你回
    assert out["key"] == "reply" and out["notice"] and out["line"].endswith("@了你：看一下这个（正在读最新留言）")
    unread = next_step.classify(repo="acme/channel", author="", thread=None, refs=[], notice={**notice, "who": "", "text": ""})
    assert unread["key"] == "reply" and unread["line"].startswith("GitHub 通知 ")  # 列表里还没有、也没读到谁说的
    busy = classify(MINE, flow={"stage": "处理中", "label": "处理中", "kind": "issue", "at": None}, notice=notice)
    assert busy["key"] == "flow" and busy["new"] == 1


def test_agent_saw_their_word_and_said_nothing_to_do_stays_in_reply_without_a_button():
    step = classify(MINE, THEIRS)
    found = {"kind": settled.NOTHING, "run": 2001, "rule": "处理 issue 留言", "flow": "feedback", "line": "对方只是确认",
             "since": "2026-11-19T03:00:00Z", "finished_at": "2026-11-19T03:10:00Z"}
    out = next_step.settle(step, found)
    assert out["key"] == "seen" and out["new"] == 1 and next_step.bucket(out) == "reply"
    assert out["line"].startswith("boss ") and out["line"].endswith("· Agent 看过，不用动：对方只是确认")
    assert out["drawer_only"] and next_step.allowed(out)[0] == "" and next_step.allowed(out, force=True) == ("feedback", "")
    quiet = next_step.settle(classify(MINE, said("boss", "2026-11-19T00:30:00Z", "早先的话"), said(ME, "2026-11-19T00:40:00Z", "进展")), found)
    assert next_step.bucket(quiet) != "reply"  # 你已经回过：照原来的


# ---- 收件箱：通知比列表新 ----

def write_notifications(data, rows):
    data.mkdir(parents=True, exist_ok=True)
    stored = {}
    for i, (key, kind, reason, at, unread) in enumerate(rows, 1):
        repo, number = key.split("#")
        path = "pulls" if kind == "PullRequest" else "issues"
        stored[str(i)] = {"id": str(i), "repository": {"full_name": repo}, "unread": unread, "reason": reason, "updated_at": at,
                          "last_read_at": None, "subject": {"title": f"通知 {number}", "type": kind,
                                                            "url": f"https://api.github.com/repos/{repo}/{path}/{number}",
                                                            "latest_comment_url": None}}
    (data / notifications.STORE).write_text(json.dumps({"account": ME, "synced_at": "", "ids": list(stored), "rows": stored}))


def fake_lists(monkeypatch, own, involved=()):
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: own)
    monkeypatch.setattr(issues, "involved", lambda: {"open": list(involved), "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: ({"runs": {}, "history": {}, "waiting": {}, "handed": {}}, ""))
    asked = []
    monkeypatch.setattr(fresh, "refresh", lambda wanted, **_: asked.append(dict(wanted)) or sorted(wanted))
    return asked


def rows_of(data):
    return {r["subject"]: r for r in issues.view(PROJECTS, data=data)["issues"]}


def test_an_unread_mention_newer_than_the_list_goes_to_reply_and_is_reread(tmp_path, monkeypatch):
    data = tmp_path / "data"
    asked = fake_lists(monkeypatch, [issue(MINE)])
    later = iso(datetime.now(timezone.utc) + timedelta(minutes=5))  # 列表这次读完之后才来的通知
    write_notifications(data, [("acme/channel#64", "Issue", "mention", later, True)])
    row = rows_of(data)["acme/channel#64"]
    assert row["next"]["bucket"] == "reply" and row["next"]["notice"] and row["next"]["new"] == 1
    assert asked == [{"acme/channel#64": later}]  # 单独重读这一条

    fresh._store["acme/channel#64"] = {"version": later, "at": time.time() + 3600, "error": "",  # 重读到了：对方真的说了
                                       "node": {**issue(MINE, THEIRS), "state": "OPEN"}}
    row = rows_of(data)["acme/channel#64"]
    assert row["next"]["key"] == "reply" and not row["next"].get("notice") and row["next"]["who"] == "boss"
    fresh._store["acme/channel#64"]["node"] = {**issue(MINE), "state": "OPEN"}  # 读到的是你自己的话：回到等对方
    assert rows_of(data)["acme/channel#64"]["next"]["key"] == "wait"
    assert len(asked) == 1  # 已为这条通知读过，不再读


def test_old_read_or_quiet_notifications_are_not_signals(tmp_path, monkeypatch):
    data = tmp_path / "data"
    asked = fake_lists(monkeypatch, [issue(MINE), issue(MINE, number=65)])
    later = iso(datetime.now(timezone.utc) + timedelta(minutes=5))
    write_notifications(data, [("acme/channel#64", "Issue", "mention", "2026-11-01T00:00:00Z", True),  # 比列表旧
                               ("acme/channel#65", "Issue", "mention", later, False),  # 已读
                               ("acme/channel#66", "Issue", "subscribed", later, True),  # 只是订阅
                               ("else/y#1", "Issue", "mention", later, True)])  # 没登记的仓库
    out = rows_of(data)
    assert {k: r["next"]["bucket"] for k, r in out.items()} == {"acme/channel#64": "wait", "acme/channel#65": "wait"}
    assert asked == []


def test_a_mention_missing_from_the_list_still_gets_a_row(tmp_path, monkeypatch):
    data = tmp_path / "data"
    asked = fake_lists(monkeypatch, [])
    later = iso(datetime.now(timezone.utc) + timedelta(minutes=5))
    write_notifications(data, [("up/x#5", "Issue", "mention", later, True), ("up/old#2", "Issue", "mention", later, True)])
    row = rows_of(data)["up/x#5"]  # 只被 @ 了、不在与我有关的搜索里：先列一行（标题取通知的）
    assert row["title"] == "通知 5" and row["next"]["bucket"] == "reply" and row["scope"] == "mine"
    assert asked == [{"up/x#5": later}] and "up/old#2" not in rows_of(data)  # 归档的项目不管

    tagged = said("maint", "2026-11-19T02:00:00Z", "@luochen211 can you confirm the config?")
    fresh._store["up/x#5"] = {"version": later, "at": time.time() + 3600, "error": "",
                              "node": {**issue(tagged, number=5, repo="up/x", author="maint"), "state": "OPEN"}}
    row = rows_of(data)["up/x#5"]
    assert row["title"] == "issue 5" and row["next"]["key"] == "reply" and row["next"]["line"].startswith("maint ")
    fresh._store["up/x#5"]["node"]["state"] = "CLOSED"
    assert "up/x#5" not in rows_of(data)  # 读到已关闭：不列


# ---- fresh：重读 ----

def test_refresh_reads_each_notification_once_and_retries_failures_later():
    reads = []

    def read(keys):
        reads.append(sorted(keys))
        return {k: {"type": "Issue", "state": "OPEN"} for k in keys}
    assert fresh.refresh({"A/b#1": "t1", "a/b#2": "t1"}, read=read, background=False) == ["a/b#1", "a/b#2"]
    assert fresh.refresh({"a/b#1": "t1"}, read=read, background=False) == []  # 这条通知已经读过
    assert fresh.refresh({"a/b#1": "t2"}, read=read, background=False) == ["a/b#1"]  # 通知又更新了
    assert reads == [["a/b#1", "a/b#2"], ["a/b#1"]]
    assert fresh.entry("a/b#1")["version"] == "t2"

    def broken(keys):
        raise RuntimeError("网络断了")
    fresh.refresh({"a/b#3": "t1"}, read=broken, background=False)
    assert fresh.entry("a/b#3")["error"] == "网络断了" and fresh.entry("a/b#3")["at"] == 0
    assert fresh.refresh({"a/b#3": "t1"}, read=read, background=False) == []  # 5 分钟内不重读
    assert fresh.refresh({"a/b#3": "t1"}, read=read, background=False, now=time.time() + fresh.RETRY + 1) == ["a/b#3"]


def test_refresh_reads_subjects_in_one_query_and_warms_pr_timelines(monkeypatch):
    queries = []

    def graphql(query, kind="graphql", partial=False, **_):
        queries.append((kind, partial))
        node = issue(said("boss", "2026-11-19T02:00:00Z", "hi"), number=64)
        raw = {"__typename": "Issue", "state": "OPEN", "number": 64, "title": "issue 64", "url": node["url"], "createdAt": node["createdAt"],
               "updatedAt": node["updatedAt"], "bodyText": "正文", "body": "正文", "author": {"login": ME}, "labels": {"nodes": []},
               "comments": {"totalCount": 1}, "timelineItems": {"nodes": [said("boss", "2026-11-19T02:00:00Z", "hi")]}}
        return {"r0": {"nameWithOwner": "acme/channel", "s64": raw, "s9": {"__typename": "PullRequest", "number": 9, "state": "OPEN",
                                                                           "updatedAt": "2026-11-19T00:00:00Z"}, "s10": None}}
    monkeypatch.setattr(github, "graphql", graphql)
    warmed = []
    fresh.refresh({"acme/channel#64": "t", "acme/channel#9": "t", "acme/channel#10": "t"}, background=False,
                  timelines=lambda wanted: warmed.append(wanted))
    assert queries == [("graphql:fresh-subjects", True)]
    found = fresh.entry("acme/channel#64")["node"]
    assert found["state"] == "OPEN" and found["thread"]["comments"]["nodes"][0]["author"]["login"] == "boss"
    assert conversation.assess(found["thread"])["pending"]  # 和列表同形，归类直接用
    assert fresh.entry("acme/channel#10")["node"] is None
    assert warmed == [{"acme/channel#9": "t"}]


def test_sync_rereads_only_new_or_changed_signals_in_registered_projects():
    def row(number, reason="mention", at="2026-11-19T02:00:00Z", unread=True, repo="acme/channel", kind="Issue"):
        return {"id": str(number), "repository": {"full_name": repo}, "unread": unread, "reason": reason, "updated_at": at,
                "subject": {"type": kind, "url": f"https://api.github.com/repos/{repo}/issues/{number}", "title": "", "latest_comment_url": None}}
    old = {"1": row(1), "2": row(2)}
    rows = {"1": row(1), "2": row(2, at="2026-11-19T03:00:00Z"), "3": row(3, reason="comment"), "4": row(4, reason="subscribed"),
            "5": row(5, unread=False), "6": row(6, repo="else/y"), "7": row(7, kind="CheckSuite")}
    asked = []
    notifications.renew(rows, old, PROJECTS, refresh=lambda wanted: asked.append(wanted) or sorted(wanted))
    assert asked == [{"acme/channel#2": "2026-11-19T03:00:00Z", "acme/channel#3": "2026-11-19T02:00:00Z"}]


# ---- 你开的 PR：通知比列表新 ----

def pr_row(number, updated="2026-11-19T00:00:00Z"):
    return {"type": "PullRequest", "repository": {"nameWithOwner": "up/x"}, "number": number, "title": f"PR {number}",
            "url": f"https://github.com/up/x/pull/{number}", "author": {"login": ME}, "updatedAt": updated,
            "isDraft": False, "reviewDecision": None, "state": "OPEN"}


def pr_node(*items, number=7):
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/up/x/pull/{number}", "state": "OPEN", "isDraft": False,
            "reviewDecision": None, "updatedAt": "2026-11-19T00:00:00Z", "headRefName": "fix", "baseRefName": "main",
            "isCrossRepository": True, "headRepository": {"nameWithOwner": f"{ME}/x"}, "repository": {"nameWithOwner": "up/x"},
            "author": {"login": ME}, "timelineItems": {"nodes": list(items)}}


def test_a_newer_notification_rereads_my_pr_and_lists_one_search_missed(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write_notifications(data, [])
    monkeypatch.setattr(issues, "involved", lambda: {"open": [pr_row(7)], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    mine = said(ME, "2026-11-18T00:00:00Z", "Opened")
    timelines = {"up/x#7": pr_node(mine)}
    reads = []

    def read(keys):
        reads.append(sorted(keys))
        return {k: timelines.get(k) for k in keys}
    out = pr_feedback.view(PROJECTS, data, read=read)
    assert [(r["subject"], r["state"]) for r in out["prs"]] == [("up/x#7", "wait")]
    assert pr_feedback.view(PROJECTS, data, read=read) and len(reads) == 1  # 没有新通知：不重读

    timelines["up/x#7"] = pr_node(mine, said("maint", "2026-11-19T01:00:00Z", "Please split this.", association="MEMBER"))
    timelines["up/x#8"] = pr_node(said("maint", "2026-11-19T01:00:00Z", "Thanks!", association="MEMBER"), number=8)
    write_notifications(data, [("up/x#7", "PullRequest", "author", "2026-11-19T01:00:05Z", True),  # 列表还是旧的
                               ("up/x#8", "PullRequest", "author", "2026-11-19T01:00:05Z", True)])  # 搜索里还没有
    out = {r["subject"]: r for r in pr_feedback.view(PROJECTS, data, read=read)["prs"]}
    assert reads[-1] == ["up/x#7", "up/x#8"]
    assert out["up/x#7"]["state"] == "reply" and out["up/x#7"]["new"] == 1
    assert out["up/x#8"]["state"] == "reply" and out["up/x#8"]["open"]
