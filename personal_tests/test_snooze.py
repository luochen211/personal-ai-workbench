"""暂时不处理（personal/snooze.py）：设了落搁置、不计角标、交给接口拒绝；对方有新动静、到点、恢复后回到原来的类别；只写本机文件。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from personal import agent_watch, github, issues, next_step, pr_feedback, snooze, upstream, conversation

ME = "luochen211"
TEAM = {"id": "channel", "name": "通道", "group": "合作项目", "github": "acme/channel"}
OWN = {"id": "app", "name": "工具", "group": "个人工具", "github": "luochen211/app"}
PROJECTS = [TEAM, OWN]
SET_AT = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    monkeypatch.setattr(next_step, "now", lambda: SET_AT)
    monkeypatch.setattr(snooze, "now", lambda: SET_AT + timedelta(hours=1))
    github.reset()
    pr_feedback.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    yield tmp_path / "data"
    github.reset()
    pr_feedback.reset()


def said(login, at, text, typename="User"):
    return {"__typename": "IssueComment", "author": {"__typename": typename, "login": login}, "authorAssociation": "OWNER",
            "bodyText": text, "createdAt": at, "createdViaEmail": False, "url": f"https://github.com/c#{at[8:13]}"}


def issue_row(*items, number=70, repo="acme/channel", author=ME):
    found = {"number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}",
             "createdAt": "2026-11-10T00:00:00Z", "updatedAt": "2026-11-18T00:00:00Z", "bodyText": "正文",
             "author": {"__typename": "User", "login": author}, "labels": {"nodes": []},
             "comments": {"totalCount": len(items)}, "timelineItems": {"nodes": list(items)}}
    return upstream.item(found, repo)


def fake_lists(monkeypatch, own):
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: own)
    monkeypatch.setattr(issues, "involved", lambda: {"open": [], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: ({"runs": {}, "history": {}, "waiting": {}, "handed": {}}, ""))


BOSS = said("boss", "2026-11-17T00:00:00Z", "@luochen211 接口这样改行不行")
TODO = said(ME, "2026-11-17T00:00:00Z", "进展：名单下好了，下一步整理。")


def rows(data):
    github.reset()
    return {r["subject"]: r for r in issues.view(PROJECTS, data=data)["issues"]}


def test_snoozed_reply_and_todo_go_to_hold_until_the_other_side_speaks(fresh, monkeypatch):
    fake_lists(monkeypatch, [issue_row(BOSS), issue_row(TODO, number=3, repo="luochen211/app")])
    before = rows(fresh)
    assert before["acme/channel#70"]["next"]["bucket"] == "reply" and before["acme/channel#70"]["their_at"] == BOSS["createdAt"]
    assert before["luochen211/app#3"]["next"]["bucket"] == "todo"
    snooze.put(fresh, [{"subject": "acme/channel#70", "their_at": before["acme/channel#70"]["their_at"]},
                       {"subject": "luochen211/app#3", "their_at": ""}], at=SET_AT)
    stored = json.loads((fresh / "snoozed.json").read_text())["items"]
    assert sorted(stored) == ["acme/channel#70", "luochen211/app#3"] and stored["acme/channel#70"]["until"] == ""
    held = rows(fresh)
    for subject, was in [("acme/channel#70", "reply"), ("luochen211/app#3", "todo")]:
        row = held[subject]
        assert row["next"]["bucket"] == "hold" and row["snooze"]["from"] == was and row["hint"] is None
        assert row["snooze"]["line"].startswith("你 11-18 设为暂时不处理") and "回来" not in row["snooze"]["line"]
    # 机器人和你自己的新留言不算对方的新动静
    fake_lists(monkeypatch, [issue_row(BOSS, said("github-actions", "2026-11-18T11:00:00Z", "CI 过了", "Bot"),
                                       said(ME, "2026-11-18T11:30:00Z", "我看下")),
                             issue_row(TODO, number=3, repo="luochen211/app")])
    assert rows(fresh)["acme/channel#70"]["next"]["bucket"] == "hold"
    # 对方又说了：回到原来的类别
    fake_lists(monkeypatch, [issue_row(BOSS, said("boss", "2026-11-18T12:00:00Z", "@luochen211 还在吗")),
                             issue_row(TODO, number=3, repo="luochen211/app")])
    back = rows(fresh)
    assert back["acme/channel#70"]["next"]["bucket"] == "reply" and "snooze" not in back["acme/channel#70"]
    assert back["luochen211/app#3"]["next"]["bucket"] == "hold"


def test_timed_snoozes_come_back_when_due_and_restore_is_immediate(fresh, monkeypatch):
    fake_lists(monkeypatch, [issue_row(BOSS), issue_row(TODO, number=3, repo="luochen211/app")])
    out = snooze.put(fresh, ["acme/channel#70"], wait="1d", at=SET_AT)
    assert out["until"] == (SET_AT + timedelta(days=1)).isoformat(timespec="seconds")
    snooze.put(fresh, ["luochen211/app#3"], wait="1w", at=SET_AT)
    held = rows(fresh)
    assert held["acme/channel#70"]["next"]["bucket"] == "hold" and "回来" in held["acme/channel#70"]["snooze"]["line"]
    monkeypatch.setattr(snooze, "now", lambda: SET_AT + timedelta(days=1, minutes=1))
    later = rows(fresh)
    assert later["acme/channel#70"]["next"]["bucket"] == "reply"  # 1 天到了
    assert later["luochen211/app#3"]["next"]["bucket"] == "hold"  # 1 周还没到
    monkeypatch.setattr(snooze, "now", lambda: SET_AT + timedelta(days=7, minutes=1))
    assert rows(fresh)["luochen211/app#3"]["next"]["bucket"] == "todo"
    monkeypatch.setattr(snooze, "now", lambda: SET_AT + timedelta(hours=1))
    assert rows(fresh)["luochen211/app#3"]["next"]["bucket"] == "hold"
    assert snooze.restore(fresh, ["LUOCHEN211/app#3"]) == {"restored": ["luochen211/app#3"]}
    assert rows(fresh)["luochen211/app#3"]["next"]["bucket"] == "todo"
    snooze.put(fresh, ["luochen211/app#3"], at=SET_AT + timedelta(days=3))  # 写入时顺手删掉到点的旧记录
    assert sorted(snooze.load(fresh)) == ["luochen211/app#3"]


def test_put_checks_its_input(fresh):
    for bad, wait in [([], "news"), (["acme/channel"], "news"), (["acme/channel#1"], "2d"), (["x/y#1"] * 2 + ["bad"], "news"),
                      ([f"x/y#{n}" for n in range(1, snooze.MAX_ITEMS + 2)], "news")]:
        with pytest.raises(ValueError):
            snooze.put(fresh, bad, wait=wait)
    snooze.put(fresh, [{"subject": "x/y#1", "their_at": "不是时间"}])
    assert snooze.load(fresh)["x/y#1"]["their_at"] == ""
    assert snooze.load(None) == {}  # 没有运行数据目录（测试、离线）：什么都不算暂时不处理


def test_handoff_endpoints_refuse_snoozed_items_and_point_to_restore(fresh, monkeypatch):
    fake_lists(monkeypatch, [issue_row(BOSS), issue_row(TODO, number=3, repo="luochen211/app")])
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("不该打标签"))
    monkeypatch.setattr(conversation, "handoff", lambda *a, **k: pytest.fail("不该交给 issue 留言流程"))
    snooze.put(fresh, ["acme/channel#70", "luochen211/app#3"], at=SET_AT)
    for repo, number in [("acme/channel", 70), ("luochen211/app", 3)]:
        with pytest.raises(ValueError, match="暂时不处理.*恢复"):
            issues.handoff(PROJECTS, repo, number, data=fresh)
    out = issues.batch_handoff(PROJECTS, ["acme/channel#70", "luochen211/app#3"], data=fresh, trigger=lambda *a: pytest.fail("不该触发"))
    assert out["handed"] == 0 and all("恢复" in r["error"] for r in out["results"])


def test_feedback_handoff_skips_a_snoozed_issue(fresh, monkeypatch):
    found = {**issue_row(BOSS)["thread"], "__typename": "Issue", "state": "OPEN", "number": 70, "title": "issue 70",
             "repository": {"nameWithOwner": "acme/channel"}}
    monkeypatch.setattr(agent_watch, "issue_feedback_rule_missing", lambda: "")
    snooze.put(fresh, [{"subject": "acme/channel#70", "their_at": BOSS["createdAt"]}], at=SET_AT)
    with pytest.raises(ValueError, match="暂时不处理"):
        conversation.handoff(PROJECTS, ["acme/channel#70"], read=lambda keys: {k: found for k in keys}, trigger=lambda *a: "", data=fresh,
                        state={"issue": ({}, "")})
    assert not agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())  # 一条都没记


def pr_node(*items, number=7, repo="up/x"):
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/{repo}/pull/{number}", "state": "OPEN",
            "isDraft": False, "reviewDecision": "", "updatedAt": "2026-11-18T00:00:00Z", "headRefName": "fix/thing", "baseRefName": "main",
            "isCrossRepository": False, "headRepository": {"nameWithOwner": repo}, "repository": {"nameWithOwner": repo},
            "author": {"login": ME}, "timelineItems": {"nodes": list(items)}}


def pr_comment(login, at):
    return {"__typename": "IssueComment", "author": {"__typename": "User", "login": login}, "authorAssociation": "MEMBER",
            "bodyText": "请改一下", "createdAt": at, "url": f"https://github.com/up/x/pull/7#issuecomment-{at[-3:-1]}"}


def pr_commit(login, at):
    return {"__typename": "PullRequestCommit", "commit": {"oid": "abcdef1234", "committedDate": at,
                                                          "author": {"user": {"login": login}}, "committer": {"user": None}}}


def test_snoozed_pr_holds_until_someone_else_comments_or_pushes(fresh, monkeypatch):
    search = [{"type": "PullRequest", "repository": {"nameWithOwner": "up/x"}, "number": 7, "title": "PR 7",
               "url": "https://github.com/up/x/pull/7", "author": {"login": ME}, "updatedAt": "2026-11-18T00:00:00Z",
               "isDraft": False, "reviewDecision": "", "state": "OPEN"}]
    monkeypatch.setattr(issues, "involved", lambda: {"open": search, "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    timeline = {"up/x#7": pr_node(pr_comment("maintainer", "2026-11-17T10:00:00Z"))}

    def view():
        github.reset()
        pr_feedback.reset()
        return pr_feedback.view([], fresh, read=lambda keys: {k: timeline.get(k) for k in keys}, now=SET_AT + timedelta(hours=1))["prs"][0]
    row = view()
    assert row["state"] == "reply" and row["their_last"] == "2026-11-17T10:00:00Z"
    snooze.put(fresh, [{"subject": "up/x#7", "their_at": row["their_last"]}], at=SET_AT)
    held = view()
    assert held["state"] == "hold" and held["snooze"]["from"] == "reply"
    timeline["up/x#7"] = pr_node(pr_comment("maintainer", "2026-11-17T10:00:00Z"), pr_commit(ME, "2026-11-18T10:30:00Z"))
    assert view()["state"] == "hold"  # 你自己的提交不算
    timeline["up/x#7"] = pr_node(pr_comment("maintainer", "2026-11-17T10:00:00Z"), pr_commit("maintainer", "2026-11-18T10:30:00Z"))
    assert view()["state"] == "reply"  # 别人推了新提交：回来
    rules = {"rules": [{"name": pr_feedback.RULE, "parallel": 10}]}
    (agent_watch.home() / "rules.json").write_text(json.dumps(rules, ensure_ascii=False))
    monkeypatch.setattr(agent_watch, "pr_rule_missing", lambda: "")
    snooze.put(fresh, [{"subject": "up/x#7", "their_at": "2026-11-18T10:30:00Z"}], at=SET_AT)
    handed, skipped = pr_feedback.record([], ["up/x#7"], read=lambda keys: {k: timeline.get(k) for k in keys}, state=({}, ""),
                                         at=SET_AT + timedelta(hours=1), data=fresh)
    assert handed == [] and "恢复" in skipped[0]["reason"]
