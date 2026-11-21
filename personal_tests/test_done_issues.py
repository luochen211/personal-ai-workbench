"""Issue 列表页的「已完成」：全部项目近 30 天已完成的 issue 汇总、去重、增量读、限流，以及 open 列表剔除已关闭的。"""
import json
import time
from datetime import datetime, timezone

import pytest

from personal import activity, github, issues
from personal import notifications as inbox

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
CODEWHALE = {"id": "codewhale", "name": "Codewhale", "github": "codewhale-hq/Codewhale", "issue_scope": "mine"}
CAREER = {"id": "career-ops", "name": "career-ops", "github": "career-ops-hq/career-ops", "issue_scope": "mine"}
PROJECTS = [CODEWHALE, CAREER]


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    github.reset(); activity.reset(); issues._open_subjects = None
    inbox._states.clear(); inbox._states_loaded.clear()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    yield
    github.reset(); activity.reset(); issues._open_subjects = None
    inbox._states.clear(); inbox._states_loaded.clear()


def url(repo, kind, number):
    return f"https://github.com/{repo}/{kind}/{number}"


def closed_issue(repo, number, closed="2026-11-12T07:29:00Z", by=None, reason="COMPLETED"):
    closer = {"__typename": "PullRequest", "number": by, "url": url(repo, "pull", by), "title": f"PR {by}",
              "author": {"login": "luochen211"}} if by else None
    return {"__typename": "Issue", "repository": {"nameWithOwner": repo}, "number": number, "title": f"问题 {number}",
            "url": url(repo, "issues", number), "state": "CLOSED", "stateReason": reason,
            "createdAt": "2026-11-01T00:00:00Z", "closedAt": closed, "author": {"login": "luochen211"},
            "timelineItems": {"nodes": [{"createdAt": closed, "actor": {"login": "luochen211"}, "closer": closer}]}}


def merged_pr(repo, number, closes=(), merged="2026-11-12T07:29:00Z"):
    return {"__typename": "PullRequest", "repository": {"nameWithOwner": repo}, "number": number, "title": f"PR {number}",
            "url": url(repo, "pull", number), "state": "MERGED", "mergedAt": merged, "closedAt": merged,
            "closingIssuesReferences": {"nodes": [
                {"number": n, "title": f"问题 {n}", "url": url(repo, "issues", n), "state": "CLOSED", "stateReason": "COMPLETED",
                 "createdAt": "2026-11-01T00:00:00Z", "closedAt": merged, "author": {"login": author}}
                for n, author in closes]}}


class FakeSearch:
    """按查询类别（done-issues / done-prs）给固定结果，第一页之后还有一页；记下每次请求的类别和搜索词。"""
    def __init__(self, issue_nodes=(), pr_nodes=(), pages=1):
        self.nodes = {"graphql:done-issues": list(issue_nodes), "graphql:done-prs": list(pr_nodes)}
        self.pages, self.calls = pages, []

    def __call__(self, query, kind="graphql", q="", after=None, **_):
        self.calls.append((kind, q))
        nodes = self.nodes.get(kind, [])
        page = int(after or 0)
        size = max(1, -(-len(nodes) // self.pages))
        chunk = nodes[page * size:(page + 1) * size]
        more = (page + 1) * size < len(nodes)
        return {"search": {"pageInfo": {"hasNextPage": more, "endCursor": str(page + 1)}, "nodes": chunk}}

    def kinds(self):
        return [k for k, _ in self.calls]


def done(fake, monkeypatch, data=None, projects=PROJECTS, **options):
    monkeypatch.setattr(github, "graphql", fake)
    return activity.done_all(projects, data, now=NOW, **options)


def test_done_issues_of_all_projects_come_from_two_searches_not_one_per_project(monkeypatch):
    cw, co = "codewhale-hq/Codewhale", "career-ops-hq/career-ops"
    fake = FakeSearch(
        issue_nodes=[closed_issue(cw, n, f"2026-11-12T07:2{i}:00Z", by=7310 + i) for i, n in enumerate((7299, 7300, 7307, 7308, 7309))]
        + [closed_issue(co, n, f"2026-11-12T05:{20 + i}:00Z", by=6930 + i) for i, n in enumerate((6923, 6924, 6927, 6931))]
        + [closed_issue("someone/else", 1)],  # 不在登记的项目里，不列
        pr_nodes=[merged_pr(co, 6940, closes=[(6950, "other")], merged="2026-11-12T05:00:00Z")],  # 别人创建、被本人 PR 关闭的
        pages=2)
    report = done(fake, monkeypatch)
    assert sorted(fake.kinds()) == ["graphql:done-issues"] * 2 + ["graphql:done-prs"]  # 翻页，不按项目逐个查
    assert "author:luochen211" in fake.calls[0][1] and "closed:>=2026-10-13" in fake.calls[0][1]
    subjects = [r["subject"] for r in report["issues"]]
    assert set(subjects) == {f"{cw}#{n}" for n in (7299, 7300, 7307, 7308, 7309)} | {f"{co}#{n}" for n in (6923, 6924, 6927, 6931, 6950)}
    assert subjects[0] == f"{cw}#7309"  # 新关闭的在前
    first = report["issues"][0]
    assert first["project_name"] == "Codewhale" and first["project_id"] == "codewhale"
    assert first["by"] == {"kind": "pr", "number": 7314, "url": url(cw, "pull", 7314), "title": "PR 7314"}
    other = next(r for r in report["issues"] if r["number"] == 6950)
    assert other["author"] == "other" and other["by"]["number"] == 6940 and other["project_name"] == "career-ops"
    assert report["loaded"] and report["fetched_at"] and report["limited"] == "" and report["error"] == ""


def test_the_same_issue_from_several_sources_is_listed_once(monkeypatch):
    cw = "codewhale-hq/Codewhale"
    # 项目详情页已经读到的结果，和全部项目的搜索都有 #7299；#7300 既是本人创建、又被本人 PR 关闭
    github.seed(("prs-closed", cw.lower()), {"prs": [], "issues": [activity._issue(closed_issue(cw, 7299, by=7310))]}, time.time())
    fake = FakeSearch(issue_nodes=[closed_issue(cw, 7299, by=7310), closed_issue(cw, 7300, by=7311)],
                      pr_nodes=[merged_pr(cw, 7311, closes=[(7300, "luochen211")])])
    rows = done(fake, monkeypatch)["issues"]
    assert sorted(r["number"] for r in rows) == [7299, 7300]


def test_project_page_results_are_merged_in_without_any_request(monkeypatch):
    co = "career-ops-hq/career-ops"
    github.seed(("prs-closed", co.lower()), {"prs": [], "issues": [activity._issue(closed_issue(co, 6923, by=6930))]}, time.time())
    github.seed(activity.ALL_KEY, {"since": "2026-10-13", "full_at": time.time(), "at": time.time(), "repos": {}}, time.time())
    fake = FakeSearch()
    report = done(fake, monkeypatch)
    assert fake.calls == []  # 缓存还新，不发请求
    assert [r["subject"] for r in report["issues"]] == [f"{co}#6923"]


def test_after_the_first_full_read_only_new_closures_are_read(monkeypatch):
    starts = []

    def read(start):
        starts.append(start)
        if len(starts) == 1:
            return {"o/r": {"name": "o/r", "prs": [], "issues": [{"number": 1, "closed_at": "2026-11-01T00:00:00Z"},
                                                                  {"number": 2, "closed_at": "2026-10-01T00:00:00Z"}]}}
        return {"o/r": {"name": "o/r", "prs": [], "issues": [{"number": 3, "closed_at": "2026-11-12T07:59:00Z"}]}}

    first = activity.load_all("2026-10-13", now=1_000_000, read=read)
    assert starts == ["2026-10-13"]
    assert [i["number"] for i in first["repos"]["o/r"]["issues"]] == [1]  # 30 天前关闭的不留
    github.seed(activity.ALL_KEY, first, first["at"])
    second = activity.load_all("2026-10-13", now=1_000_000 + 700, read=read)
    assert starts[1] == activity._utc(1_000_000 - activity.ALL_MARGIN)  # 只读上次以来的
    assert sorted(i["number"] for i in second["repos"]["o/r"]["issues"]) == [1, 3]
    assert second["full_at"] == first["full_at"]
    github.reset(); github.seed(activity.ALL_KEY, second, second["at"])
    activity.load_all("2026-10-13", now=1_000_000 + activity.ALL_FULL + 1, read=read)
    assert starts[2] == "2026-10-13"  # 6 小时整读一次，去掉重新打开的


def test_done_issues_survive_a_restart_without_rereading(monkeypatch, tmp_path):
    cw = "codewhale-hq/Codewhale"
    done(FakeSearch(issue_nodes=[closed_issue(cw, 7299, by=7310)]), monkeypatch, tmp_path)
    saved = json.loads((tmp_path / activity.ALL_STORE).read_text(encoding="utf-8"))
    assert "codewhale-hq/codewhale" in saved["repos"]
    github.reset(); activity.reset()
    fake = FakeSearch()
    report = done(fake, monkeypatch, tmp_path)
    assert fake.calls == [] and [r["number"] for r in report["issues"]] == [7299]


def test_rate_limited_sends_nothing_and_keeps_the_old_list(monkeypatch):
    cw = "codewhale-hq/Codewhale"
    fake = FakeSearch(issue_nodes=[closed_issue(cw, 7299, by=7310)])
    done(fake, monkeypatch)
    calls = len(fake.calls)
    github._slot(activity.ALL_KEY)["at"] = 0
    github.strike("secondary rate limit")
    report = done(fake, monkeypatch)
    assert len(fake.calls) == calls  # 限流中没有再发请求
    assert [r["number"] for r in report["issues"]] == [7299]
    assert "GitHub 限流中" in report["limited"] and report["error"] == ""


def test_rate_limited_without_any_data_says_so(monkeypatch):
    github.strike("secondary rate limit")
    fake = FakeSearch()
    report = done(fake, monkeypatch)
    assert fake.calls == [] and report["issues"] == [] and not report["loaded"]
    assert "还没读到" in report["limited"]


def open_row(repo, number, updated="2026-11-12T07:00:00Z"):
    return {"type": "Issue", "repository": {"nameWithOwner": repo}, "number": number, "title": f"问题 {number}",
            "url": url(repo, "issues", number), "labels": [], "author": {"login": "luochen211"}, "commentsCount": 0,
            "createdAt": "2026-11-01T00:00:00Z", "updatedAt": updated}


def test_issues_known_to_be_closed_leave_the_open_list_before_the_cache_refreshes(monkeypatch, tmp_path):
    cw = "codewhale-hq/Codewhale"
    rows = [open_row(cw, n) for n in (7299, 7300, 7301, 7302)] + [open_row(cw, 7303, updated="2026-11-12T07:50:00Z")]
    monkeypatch.setattr(issues, "involved", lambda: {"open": rows, "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    # 全部项目的「已完成」里有 #7299；项目详情页的缓存里 #7300 被本人 PR 关闭；消息状态里看到 #7301 关闭了；
    # #7303 关闭后又重新打开（更新时间晚于关闭时间），照常列出
    github.seed(activity.ALL_KEY, {"since": "2026-10-13", "full_at": time.time(), "at": time.time(), "repos": {
        cw.lower(): {"name": cw, "prs": [], "issues": [{"number": 7299, "closed_at": "2026-11-12T07:29:00Z"},
                                                      {"number": 7303, "closed_at": "2026-11-12T07:29:00Z"}]}}}, time.time())
    github.seed(("prs-closed", cw.lower()),
                {"prs": [activity._pr(merged_pr(cw, 7311, closes=[(7300, "luochen211")]))], "issues": []}, time.time())
    inbox._states[f"{cw.lower()}#7301"] = {"state": "completed", "at": time.time(), "updated_at": "2026-11-12T07:30:00Z"}
    report = issues.view([CODEWHALE], refresh=True, data=tmp_path)
    assert [r["number"] for r in report["issues"]] == [7303, 7302]
