"""项目详情页的「我的 PR」和「近 30 天已完成的 issue」：分组、缓存、失效、限流。"""
import json
import time
from datetime import datetime, timezone

import pytest

from personal import activity, github, issues
from personal import notifications as inbox

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
PROJECT = {"id": "codewhale", "name": "Codewhale", "github": "up/x", "issue_scope": "mine"}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    github.reset(); activity.reset(); issues._open_subjects = None
    inbox._states.clear(); inbox._states_loaded.clear()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    yield
    github.reset(); activity.reset(); issues._open_subjects = None
    inbox._states.clear(); inbox._states_loaded.clear()


def pr(number, state="OPEN", merged=None, closed=None, draft=False, review=None, checks="SUCCESS", closes=()):
    return {"__typename": "PullRequest", "number": number, "title": f"PR {number}", "url": f"https://github.com/up/x/pull/{number}",
            "state": state, "isDraft": draft, "reviewDecision": review, "createdAt": "2026-11-01T00:00:00Z",
            "updatedAt": "2026-11-12T00:00:00Z", "mergedAt": merged, "closedAt": closed or merged, "author": {"login": "luochen211"},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]},
            "closingIssuesReferences": {"nodes": list(closes)}}


def issue(number, closed="2026-11-12T07:29:00Z", closer=None, reason="COMPLETED", author="luochen211", actor="luochen211"):
    return {"__typename": "Issue", "number": number, "title": f"问题 {number}", "url": f"https://github.com/up/x/issues/{number}",
            "state": "CLOSED", "stateReason": reason, "createdAt": "2026-11-01T00:00:00Z", "closedAt": closed,
            "author": {"login": author},
            "timelineItems": {"nodes": [{"createdAt": closed, "actor": {"login": actor}, "closer": closer}]}}


def ref(number, closed="2026-11-12T07:29:00Z", state="CLOSED", author="someone"):
    return {"number": number, "title": f"问题 {number}", "url": f"https://github.com/up/x/issues/{number}", "state": state,
            "stateReason": "COMPLETED" if state == "CLOSED" else None, "createdAt": "2026-11-01T00:00:00Z",
            "closedAt": closed, "author": {"login": author}}


class FakeGitHub:
    """按查询里的别名给固定结果，记下每次请求。"""
    def __init__(self, open_prs=(), closed_prs=(), closed_issues=()):
        self.open, self.closed, self.issues, self.calls = list(open_prs), list(closed_prs), list(closed_issues), []

    def __call__(self, query, kind="graphql", **_):
        self.calls.append((kind, query))
        if "open: search" in query:
            return {"open": {"nodes": self.open}}
        return {"prs": {"nodes": self.closed}, "issues": {"nodes": self.issues}}

    def kinds(self):
        return [k for k, _ in self.calls]


def view(fake, monkeypatch, data=None, **options):
    monkeypatch.setattr(github, "graphql", fake)
    return activity.view(PROJECT, data, now=NOW, **options)


def test_prs_are_grouped_open_merged_and_closed_within_thirty_days(monkeypatch):
    fake = FakeGitHub(
        open_prs=[pr(7315, checks="FAILURE"), pr(7321, checks="PENDING"), pr(7400, draft=True), pr(7401, review="CHANGES_REQUESTED")],
        closed_prs=[pr(7287, "MERGED", merged="2026-11-12T07:29:05Z"), pr(7310, "MERGED", merged="2026-11-12T07:28:47Z"),
                    pr(7155, "CLOSED", closed="2026-10-31T01:00:00Z"), pr(7000, "MERGED", merged="2026-10-01T00:00:00Z")])
    report = view(fake, monkeypatch)
    groups = {k: [p["number"] for p in v] for k, v in report["prs"].items()}
    assert groups == {"open": [7401, 7400, 7321, 7315], "merged": [7287, 7310], "closed": [7155]}
    assert report["counts"] == {"pending": 2, "open": 4, "merged": 2}  # 草稿和要改的不算待审；30 天前合并的不列
    first = report["prs"]["open"][-1]
    assert first["checks"] == "FAILURE" and first["url"] == "https://github.com/up/x/pull/7315"
    assert report["prs"]["merged"][0]["merged_at"] == "2026-11-12T07:29:05Z"
    assert fake.kinds() == ["graphql:project-prs", "graphql:project-closed"]
    assert "author:luochen211" in fake.calls[0][1] and "repo:up/x" in fake.calls[0][1]
    assert "closed:>=2026-10-13" in fake.calls[1][1]


def test_done_issues_say_which_pull_request_closed_them():
    value = {"prs": [activity._pr(pr(7312, "MERGED", merged="2026-11-12T07:28:55Z", closes=[ref(7308, author="luochen211")])),
                     activity._pr(pr(7313, "MERGED", merged="2026-11-12T07:29:01Z", closes=[ref(7500, author="bob")])),
                     activity._pr(pr(7155, "CLOSED", closed="2026-10-31T01:00:00Z", closes=[ref(7154)]))],
             "issues": [activity._issue(issue(7309, closer={"__typename": "PullRequest", "number": 7310, "url": "https://github.com/up/x/pull/7310",
                                                             "title": "PR 7310", "author": {"login": "luochen211"}})),
                        activity._issue(issue(7308, closed="2026-11-12T07:28:57Z",
                                              closer={"__typename": "Commit", "abbreviatedOid": "1af27bb", "url": "https://github.com/up/x/commit/1af27bb"})),
                        activity._issue(issue(7154, closed="2026-10-31T01:24:06Z", reason="NOT_PLANNED")),
                        activity._issue(issue(6000, closed="2026-09-01T00:00:00Z"))]}
    done = {i["number"]: i for i in activity.done_issues(value, "2026-10-13")}
    assert set(done) == {7309, 7308, 7500, 7154}  # 30 天前关的不列
    assert done[7309]["by"] == {"kind": "pr", "number": 7310, "url": "https://github.com/up/x/pull/7310", "title": "PR 7310"}
    assert done[7308]["by"]["number"] == 7312 and done[7308]["by"]["commit"] == "1af27bb"  # 提交关闭的找回本人的 PR
    assert done[7500]["by"]["number"] == 7313 and done[7500]["author"] == "bob"  # 别人创建、本人 PR 关闭的
    assert done[7154]["by"] == {"kind": "manual", "actor": "luochen211"} and done[7154]["reason"] == "不做"
    assert done[7309]["reason"] == "已完成"


def test_open_prs_cache_five_minutes_and_merged_ones_much_longer(monkeypatch):
    fake = FakeGitHub(open_prs=[pr(1)], closed_prs=[pr(2, "MERGED", merged="2026-11-12T00:00:00Z")])
    view(fake, monkeypatch)
    view(fake, monkeypatch)
    assert len(fake.calls) == 2  # 都还新鲜，第二次不发请求
    github._slot(("prs-open", "up/x"))["at"] -= activity.OPEN_TTL + 1
    github._slot(("prs-closed", "up/x"))["at"] -= activity.OPEN_TTL + 1
    report = view(fake, monkeypatch)
    assert report["refreshing"]  # 过期先给旧值、后台刷新
    deadline = time.time() + 2
    while github._slot(("prs-open", "up/x"))["lock"].locked() and time.time() < deadline:
        time.sleep(0.01)
    assert fake.kinds() == ["graphql:project-prs", "graphql:project-closed", "graphql:project-prs"]  # 已合并的不重读


def test_project_page_refreshes_open_prs_older_than_two_minutes(monkeypatch):
    fake = FakeGitHub(open_prs=[pr(1)])
    view(fake, monkeypatch)
    github._slot(("prs-open", "up/x"))["at"] -= activity.FRESH_AGE + 1
    view(fake, monkeypatch)
    assert len(fake.calls) == 2  # 默认 5 分钟内不重读
    report = view(fake, monkeypatch, max_age=activity.FRESH_AGE)
    assert report["refreshing"] and report["fetched_at"]


def test_merged_results_survive_a_restart(monkeypatch, tmp_path):
    fake = FakeGitHub(closed_prs=[pr(2, "MERGED", merged="2026-11-12T00:00:00Z")])
    view(fake, monkeypatch, tmp_path)
    saved = json.loads((tmp_path / activity.STORE).read_text(encoding="utf-8"))
    assert saved["up/x"]["value"]["prs"][0]["number"] == 2
    github.reset(); activity.reset()
    report = view(fake, monkeypatch, tmp_path)
    assert [p["number"] for p in report["prs"]["merged"]] == [2]
    assert fake.kinds() == ["graphql:project-prs", "graphql:project-closed", "graphql:project-prs"]  # 重启后只重读开着的


def test_an_open_pr_that_disappears_rereads_merged_and_issues(monkeypatch):
    fake = FakeGitHub(open_prs=[pr(1), pr(2)])
    view(fake, monkeypatch)
    issues_slot = github._slot(("involved",)); issues_slot.update(value={"open": [], "merged": []}, at=time.time())
    fake.open = [pr(2)]
    fake.closed = [pr(1, "MERGED", merged="2026-11-12T07:00:00Z")]
    github._slot(("prs-open", "up/x"))["at"] = 0
    report = view(fake, monkeypatch, force=True)
    assert [p["number"] for p in report["prs"]["merged"]] == [1]
    assert issues_slot["expired"]  # issue 列表下次请求重读
    calls = len(fake.calls)
    view(fake, monkeypatch)
    assert len(fake.calls) == calls  # 已经读到合并结果，不再反复失效


def test_merge_seen_in_github_messages_expires_project_caches(monkeypatch, tmp_path):
    fake = FakeGitHub(open_prs=[pr(7)])
    view(fake, monkeypatch)
    for key in (("issues", ("me/own",)), ("involved",)):
        github._slot(key).update(value=[], at=time.time())
    row = {"repository": {"full_name": "Up/X"}, "updated_at": "2026-11-12T07:30:00Z",
           "subject": {"type": "PullRequest", "url": "https://api.github.com/repos/Up/X/pulls/7"}}
    inbox.subject_states(tmp_path, [row], read=lambda keys: {k: "open" for k in keys})
    assert not github._slot(("prs-open", "up/x"))["expired"]  # 还开着，不失效
    inbox._states["up/x#7"]["at"] = 0
    inbox.subject_states(tmp_path, [row], read=lambda keys: {k: "merged" for k in keys})
    for key in (("prs-open", "up/x"), ("prs-closed", "up/x"), ("issues", ("me/own",)), ("involved",)):
        assert github._slot(key)["expired"], key
    github._slot(("prs-open", "up/x"))["expired"] = False
    inbox._states["up/x#7"]["at"] = 0
    inbox.subject_states(tmp_path, [row], read=lambda keys: {k: "merged" for k in keys})
    assert not github._slot(("prs-open", "up/x"))["expired"]  # 早就合并了的，不再失效


def test_an_issue_leaving_the_open_list_expires_done_issues(monkeypatch):
    rows = [{"type": "Issue", "repository": {"nameWithOwner": "up/x"}, "number": n, "title": f"问题 {n}",
             "url": f"https://github.com/up/x/issues/{n}", "labels": [], "author": {"login": "luochen211"},
             "commentsCount": 0, "createdAt": "2026-11-01T00:00:00Z", "updatedAt": "2026-11-01T00:00:00Z"} for n in (1, 2)]
    involved = {"open": rows, "merged": []}
    monkeypatch.setattr(issues, "involved", lambda: involved)
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    issues.view([PROJECT], refresh=True)
    github._slot(("prs-closed", "up/x")).update(value={"prs": [], "issues": []}, at=time.time())
    involved = {"open": rows[1:], "merged": []}
    report = issues.view([PROJECT], refresh=True, max_age=120, focus="codewhale")
    assert [r["number"] for r in report["issues"]] == [2]
    assert github._slot(("prs-closed", "up/x"))["expired"]


def test_issue_view_uses_the_shorter_age_only_for_the_focused_project(monkeypatch):
    calls = []
    monkeypatch.setattr(issues, "involved", lambda: calls.append("involved") or {"open": [], "merged": []})
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: calls.append("own") or [])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    own = {"id": "mine", "name": "自己的", "github": "me/own"}
    issues.view([PROJECT, own])
    for key in (("involved",), ("issues", ("me/own",))):
        github._slot(key)["at"] -= 130
    issues.view([PROJECT, own], max_age=120, focus="codewhale")
    time.sleep(0.05)
    assert calls == ["own", "involved", "involved"]  # 只重读 codewhale 用的那份


def test_rate_limited_sends_nothing_and_says_so(monkeypatch):
    fake = FakeGitHub(open_prs=[pr(1)], closed_prs=[pr(2, "MERGED", merged="2026-11-12T00:00:00Z")])
    view(fake, monkeypatch)
    github._slot(("prs-open", "up/x"))["at"] = 0
    github.strike("secondary rate limit")
    report = view(fake, monkeypatch, max_age=activity.FRESH_AGE)
    assert len(fake.calls) == 2  # 限流中没有再发请求
    assert [p["number"] for p in report["prs"]["open"]] == [1]  # 给旧值
    assert "GitHub 限流中" in report["limited"] and report["error"] == ""


def test_rate_limited_without_any_data_says_so(monkeypatch):
    github.strike("secondary rate limit")
    fake = FakeGitHub()
    report = view(fake, monkeypatch)
    assert fake.calls == [] and not report["loaded"]
    assert "GitHub 限流中" in report["limited"] and "还没读到" in report["limited"]


def test_projects_without_a_github_repository_have_no_pr_section():
    assert activity.view({"id": "local", "github": ""}) == {"repo": "", "available": False}
